"""History (editor/history.py): what the memory kept, as a course in steps with gaps where the
PC was off, a summary, the last rounds and the games played most, read only."""

import sqlite3

import pytest

from libre_panel.config import load_config, set_config_value
from libre_panel.editor.history import SPANS, database, history
from libre_panel.services.memory_store import MEANS, PEAKS, SCHEMA

NOW = 1_790_000_000  # a whole multiple of 300, 3600 and 14400 is not needed


def minute(ts, cpu=50.0, gpu=60.0, game=None, peak=None):
    """A row of ``minutes`` with the CPU and the GPU at ``cpu`` and ``gpu`` degrees and load."""
    values = {name: None for name in MEANS}
    values.update(cpu_temp=cpu, gpu_temp=gpu, cpu_load=cpu, gpu_load=gpu, cpu_power=cpu * 2)
    peaks = {name: None for name in PEAKS}
    peaks.update(cpu_temp=peak or cpu, gpu_temp=gpu)
    return [ts, 60, "game" if game else "normal", game, *values.values(), *peaks.values(),
            None, None, 0, None, None]  # fmt: skip


def session(started, game, seconds, fps, low, cpu_max=80.0, gpu_max=70.0):
    return {
        "id": started * 1000, "game": game, "program": f"{game}.exe", "started": started,
        "ended": started + seconds, "duration_s": seconds, "fps_s": seconds, "fps_mean": fps,
        "fps_p01": low - 5, "fps_low_mean": low, "cpu_temp_max": cpu_max,
        "gpu_temp_max": gpu_max, "closed": 1,
    }  # fmt: skip


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "memory.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    marks = ",".join("?" * len(minute(0)))
    rows = [minute(NOW - 3600 + i * 60, cpu=40 + i % 10) for i in range(30)]  # an hour ago
    rows += [minute(NOW - 600 + i * 60, cpu=70, gpu=75, game="Doom", peak=88) for i in range(10)]
    rows.append(minute(NOW - 3 * 86400))  # three days ago: in the week, not in the day
    con.executemany(f"INSERT INTO minutes VALUES ({marks})", rows)
    for row in (
        session(NOW - 600, "Doom", 600, 144.0, 98.0, cpu_max=88.0),
        session(NOW - 7200, "Doom", 1200, 120.0, 90.0),
        session(NOW - 5000, "Hades", 300, 240.0, 200.0),
        session(NOW - 40 * 86400, "Old", 900, 60.0, 50.0),  # older than a month
    ):
        names = ", ".join(row)
        con.execute(f"INSERT INTO sessions ({names}) VALUES ({','.join('?' * len(row))})",
                    list(row.values()))  # fmt: skip
    con.commit()
    con.close()
    return path


def test_a_day_is_a_course_of_five_minute_steps_with_gaps_where_the_pc_was_off(db):
    found = history(db, "day", NOW)
    assert found["found"] and found["step"] == 300 and found["since"] == NOW - 86400
    course = found["course"]
    assert all(t % 300 == 0 for t in course["t"]) and course["t"] == sorted(course["t"])
    # 30 minutes from an hour ago and the last ten, each in its step; nothing in between
    kept = [NOW - 3600 + i * 60 for i in range(30)] + [NOW - 600 + i * 60 for i in range(10)]
    assert course["t"] == sorted({t // 300 * 300 for t in kept})
    assert course["t"][-1] - course["t"][-3] <= 600 < course["t"][-3] - course["t"][5]
    assert course["cpu_temp"][-1] == 70.0 and course["cpu_temp_max"][-1] == 88.0
    assert course["game"][-1] == 1.0 and course["game"][0] == 0.0
    assert set(course) == {"t", "cpu_temp", "gpu_temp", "cpu_load", "gpu_load", "cpu_power",
                           "gpu_power", "cpu_temp_max", "gpu_temp_max", "cpu_power_max",
                           "gpu_power_max", "game"}  # fmt: skip


def test_the_summary_counts_the_minutes_kept_and_those_in_games(db):
    day = history(db, "day", NOW)["summary"]
    assert day["minutes"] == 40 and day["game_minutes"] == 10
    assert day["cpu_temp_max"] == 88.0 and day["gpu_temp_max"] == 75.0
    assert history(db, "week", NOW)["summary"]["minutes"] == 41


def test_the_span_s_energy_and_cost_as_the_power_service_prices_it(db):
    """The minutes' watts add up: the CPU's (twice each minute's number here), the GPU's
    (unknown here) and the rest of the PC at the power service's ``rest_w``, priced per kWh;
    nothing without a price, or without minutes."""
    from libre_panel.editor.history import energy

    day = history(db, "day", NOW)["summary"]
    assert day["cpu_watt_minutes"] == 4070.0 and day["gpu_watt_minutes"] is None
    found = energy(day, {"price": 0.3, "rest_w": 50, "currency": "€"})
    assert found == {"kwh": 0.1, "cost": 0.03, "price": 0.3, "currency": "€", "rest_w": 50.0}
    assert energy(day, {"price": 0}) is None and energy(day, {}) is None
    assert energy({"minutes": 0}, {"price": 0.3}) is None
    assert energy(day, {"price": "no"}) is None


def test_the_last_rounds_and_the_games_played_most(db):
    found = history(db, "month", NOW)
    assert [r["game"] for r in found["rounds"]] == ["Doom", "Hades", "Doom"]  # newest first
    assert found["rounds"][0] == {
        "game": "Doom", "started": NOW - 600, "seconds": 600, "fps": 144.0, "fps_low": 98.0,
        "cpu_temp_max": 88.0, "gpu_temp_max": 70.0, "id": (NOW - 600) * 1000,
    }  # fmt: skip
    doom, hades = found["games"]
    assert doom["game"] == "Doom" and doom["rounds"] == 2 and doom["seconds"] == 1800
    assert doom["fps"] == pytest.approx((144 * 600 + 120 * 1200) / 1800, abs=0.1)
    assert doom["cpu_temp_max"] == 88.0 and hades["rounds"] == 1


def test_no_database_yet_is_said_and_an_unknown_span_refused(tmp_path):
    found = history(tmp_path / "none.db", "week", NOW)
    assert not found["found"] and found["course"] == {"t": []} and found["rounds"] == []
    with pytest.raises(ValueError):
        history(tmp_path / "none.db", "year", NOW)
    assert set(SPANS) == {"day", "week", "month"}


def test_a_file_that_is_no_memory_says_so_instead_of_failing(tmp_path):
    path = tmp_path / "memory.db"
    sqlite3.connect(path).close()  # empty: no tables
    found = history(path, "day", NOW)
    assert found["found"] and "error" in found and found["rounds"] == []


def test_the_database_is_where_the_memory_service_keeps_it(tmp_path, monkeypatch):
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    assert database(load_config(config)) == tmp_path / "plugins-data" / "memory" / "memory.db"
    set_config_value("services.memory.database", str(tmp_path / "x.db"), path=config)
    assert database(load_config(config)) == tmp_path / "x.db"
