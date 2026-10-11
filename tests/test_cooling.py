"""The cooling analysis: rows weighted by the time they hold, what a curve really reads (found
from the duty a fan had), the recommendations and what they do to the numbers, the analysis for
a page and its plain-text report in English and German. The database is a temp file with the
memory's schema and made-up data; FanControl's configuration is a made-up file."""

import json
import math
import sqlite3
import time

import pytest
from test_fancontrol import control, curve, write_config

from libre_panel import i18n
from libre_panel.config import Config, ServicesConfig
from libre_panel.plugins import PluginHost, ServiceManager
from libre_panel.services import cooling
from libre_panel.services import cooling_advice as advice
from libre_panel.services import cooling_data as data
from libre_panel.services import cooling_text as words
from libre_panel.services import fancontrol as fc
from libre_panel.services.memory_store import SCHEMA

CPU_ID = "/amdcpu/0/temperature/2"
IGPU_ID = "/gpu-amd/0/temperature/0"  # a graphics chip that is not the card the panel shows
CARD_ID = "/gpu-amd/5/temperature/0"


def make_curve(name, points, source=None, mix=()):
    return fc.Curve(name, tuple(points), source, tuple(mix))


CPU_CURVE = make_curve(
    "Cpu_Curve",
    [(50.0, 22.0), (62.0, 30.0), (72.0, 42.0), (82.0, 62.0), (88.0, 82.0), (93.0, 100.0)],
    CPU_ID,
)
CPU_BASE = make_curve(
    "Cpu_Base", [(55.0, 22.0), (68.0, 30.0), (78.0, 45.0), (86.0, 65.0), (94.0, 100.0)], CPU_ID
)
GPU_CURVE = make_curve(
    "Gpu_Curve",
    [(45.0, 22.0), (52.0, 32.0), (58.0, 50.0), (62.0, 65.0), (70.0, 85.0), (78.0, 100.0)],
    IGPU_ID,
)
MIX = make_curve("Mix_Case", [], None, ["Cpu_Base", "Gpu_Curve"])
CURVES = {c.name: c for c in (CPU_CURVE, CPU_BASE, GPU_CURVE, MIX)}
FANS = {
    "Cpu Fan": fc.Fan("Cpu Fan", "Cpu_Curve", 20.0),
    "Gpu Fan": fc.Fan("Gpu Fan", "Gpu_Curve", 20.0),
    "Case Fan": fc.Fan("Case Fan", "Mix_Case", 20.0),
}
NAMES = {
    "cpu": "8-Core Processor",
    "gpu": "Graphics Card",
    "sensors": [
        {"key": "temp.gpu.gpu_core", "id": CARD_ID, "label": "GPU Core", "panel": "gpu_temp"},
        {"key": "temp.cpu.core", "id": CPU_ID, "label": "Core (Tctl/Tdie)", "panel": "cpu_temp"},
    ],
}


def database(path, names=NAMES):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    if names is not None:
        con.execute("INSERT INTO meta VALUES ('sensors', ?)", (json.dumps(names),))
    con.commit()
    return con


def state(con, ts, mark, mix_function=0, gpu_source=IGPU_ID):
    stored = {
        "curves": [
            {
                "Name": "Gpu_Curve",
                "SelectedTempSource": {"Identifier": gpu_source},
                "Points": ["45,22"],
            },
            {"Name": "Mix_Case", "SelectedMixFunction": mix_function},
        ],
        "controls": [],
    }
    con.execute("INSERT INTO fancontrol VALUES (?, ?, ?)", (ts, mark, json.dumps(stored)))
    con.commit()


def row(con, ts, game, cpu, gpu, duties, load=10.0, outside=12.0, sources=None, temps=None):
    """One cooling row; ``duties`` map a fan to its duty (the speed follows from it)."""
    fans = {name: [round(300 + 12 * d), round(d, 1)] for name, d in duties.items()}
    con.execute(
        "INSERT INTO cooling (ts, mode, game, cpu_temp, cpu_power, cpu_load, gpu_temp, gpu_hotspot,"
        " gpu_power, gpu_load, outside_temp, temps, fans, sources)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            ts, "game" if game else "normal", game, cpu, 80.0, load, gpu, gpu + 8, 250.0, 90.0,
            outside, json.dumps(temps) if temps else None, json.dumps(fans),
            json.dumps(sources) if sources else None,
        ),
    )  # fmt: skip


def run(con, t0, n, game_from, game_to, old=True, cpu_game=72.0, step=30):
    """``n`` rows, ``step`` seconds apart. ``old``: the way things were (the card's curve reads a
    chip that follows the CPU, the mix takes only the CPU's part); else the way they would be
    after the changes the recommendations ask for."""
    new_points = [[35, 22], [55, 54], [62, 65], [70, 85], [78, 100]]
    for i in range(n):
        playing = game_from <= i < game_to
        cpu = cpu_game if playing else 55.0 + 2 * math.sin(i / 7.0)
        gpu = 54.0 if playing else 28.0 + math.sin(i / 5.0)
        base = CPU_BASE.duty_at(cpu)
        if old:
            card = GPU_CURVE.duty_at(cpu - 3.0)  # the chip next to the CPU
            case = base
        else:
            card = fc.Curve("n", tuple(map(tuple, new_points))).duty_at(gpu)
            case = max(base, card)
        duties = {"Cpu Fan": CPU_CURVE.duty_at(cpu), "Gpu Fan": card, "Case Fan": case}
        sources = {"Cpu_Curve": cpu, "Cpu_Base": cpu}
        if not old:
            sources["Gpu_Curve"] = gpu
        row(
            con, t0 + i * step, "Space Game" if playing else None, cpu, gpu, duties,
            load=40.0 if playing else 10.0, sources=sources,
        )  # fmt: skip
    con.commit()


def ids(found):
    return [e["id"] for e in found.get("list", [])]


def rows_of(path, **kw):
    return data.load_rows(path, **kw)


def advise(path, states=None, curves=None, fans=None, names=NAMES, limits=advice.DEFAULT_LIMITS):
    return advice.advise(
        rows_of(path), states if states is not None else data.fancontrol_states(path),
        curves or CURVES, fans or FANS, names, limits,
    )  # fmt: skip


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "memory.db"
    con = database(path)
    yield path, con
    con.close()


# -- the data -------------------------------------------------------------------------------------


def test_the_time_weighted_quantile_and_the_words_for_a_time():
    assert data.weighted([(1.0, 1.0), (9.0, 99.0)]) == 9.0  # by time, not by count
    assert data.weighted([(1.0, 99.0), (9.0, 1.0)]) == 1.0
    assert data.weighted([]) is None and data.weighted([(None, 5.0), (3.0, 0.0)]) is None
    assert data.weighted([(1.0, 1.0), (2.0, 1.0), (3.0, 1.0), (4.0, 1.0)], 0.95) == 4.0
    assert [words.duration(s) for s in (45, 1800, 6480, 302400)] == [
        "45 s", "30 min", "1.8 h", "3.5 days",
    ]  # fmt: skip
    i18n.set_language("de")
    assert [words.duration(s) for s in (45, 1800, 6480, 302400)] == [
        "45 s", "30 min", "1,8 h", "3,5 Tage",
    ]  # fmt: skip
    assert words.num(26.667, 1) == "26,7" and words.num(3, 0, True) == "+3"


def test_a_row_holds_until_the_next_but_not_longer_than_the_heartbeat(db):
    path, con = db
    t0 = int(time.time()) - 86400
    run(con, t0, 1200, 400, 800, step=30)  # a day of rows, 30 s apart
    all_rows = rows_of(path)
    assert [r.duration for r in all_rows[:2]] == [30.0, 30.0]
    assert all(r.duration <= data.HEARTBEAT_S for r in all_rows)
    assert data.total(all_rows) == 1199 * 30 + data.HEARTBEAT_S  # the last holds one heartbeat
    game, desk = rows_of(path, mode="game"), rows_of(path, mode="desktop")
    assert len(game) == 400 and len(game) + len(desk) == len(all_rows)
    assert {r.game for r in game} == {"Space Game"} and all(r.in_game for r in game)
    assert data.rounds(game) == 1 and data.rounds(all_rows) == 1
    assert len(rows_of(path, days=0.5, now=t0 + 86400)) < len(all_rows)  # only the last half day


def test_a_gap_longer_than_the_heartbeat_ends_a_round(db):
    path, con = db
    t0 = 1_800_000_000
    for i in range(5):
        row(con, t0 + i * 30, "A Game", 70.0, 50.0, {})
    for i in range(5):  # two minutes later: another round of the same game
        row(con, t0 + 300 + i * 30, "A Game", 70.0, 50.0, {})
    con.commit()
    rows = rows_of(path)
    assert data.rounds(rows) == 2
    assert rows[3].duration == 30.0  # the next row is 30 s away
    assert rows[4].duration == data.HEARTBEAT_S  # 180 s before the next, but held 60 s only


def test_a_missing_file_or_table_is_an_error_the_callers_handle(tmp_path):
    with pytest.raises(sqlite3.Error):
        data.load_rows(tmp_path / "none.db")
    assert data.fancontrol_states(tmp_path / "none.db") == []
    assert data.catalogue(tmp_path / "none.db") == {}


def test_junk_in_the_rows_is_left_out(db):
    path, con = db
    con.execute(
        "INSERT INTO cooling (ts, mode, fans, sources, temps)"
        " VALUES (1, 'normal', '[1]', 'oops', ?)",
        (json.dumps({"a": 1.0, "b": "x", "c": None}),),
    )
    con.execute(
        'INSERT INTO cooling (ts, mode, fans) VALUES (2, NULL, \'{"F": [900, 40], "G": 5}\')'
    )
    con.commit()
    first, second = rows_of(path)
    assert first.fans == {} and first.sources == {} and first.temps == {"a": 1.0}
    assert second.mode == "normal" and second.fans == {"F": (900.0, 40.0)}  # G is no pair
    assert first.temp("cpu_temp") is None and first.temp("a") == 1.0


# -- which sensor does a curve read ---------------------------------------------------------------


def source_rows(tmp_path, offset, spread=0.0, strange=False):
    """A day of operation in which the duty follows the curve of the CPU's sensor + offset."""
    path = tmp_path / f"source_{offset}_{strange}.db"
    con = database(path)
    t0 = int(time.time()) - 86400
    for i in range(1200):
        cpu = 45.0 + 30.0 * (0.5 + 0.5 * math.sin(i / 60.0))
        seen = (80.0 if strange else cpu + offset) + spread * math.sin(i / 3.3)
        duty = CPU_CURVE.duty_at(seen)
        game = "Space Game" if 400 <= i < 800 else None
        row(con, t0 + i * 30, game, cpu, 40.0, {"Cpu Fan": duty})
    con.commit()
    con.close()
    return path


def verdict(path):
    rows = rows_of(path)
    pairs = data.data_of(rows, "Cpu Fan")
    return data.find_source(pairs, CPU_CURVE, data.candidates(rows))


def test_the_reverse_question_finds_the_sensor_a_curve_follows(tmp_path):
    exact = verdict(source_rows(tmp_path, 0.0))
    assert exact["verdict"] == "follows" and exact["best"]["sensor"] == "cpu_temp"
    assert abs(exact["best"]["offset"]) <= 2.0
    lines = words.source_lines(exact)
    assert any("follows CPU exactly" in line for line in lines)
    shifted = verdict(source_rows(tmp_path, 3.0))
    assert shifted["verdict"] == "follows" and shifted["best"]["offset"] == pytest.approx(
        3.0, abs=0.3
    )
    assert any("fixed offset of +3.0 K" in line for line in words.source_lines(shifted))
    assert any("another sensor of the same part" in line for line in words.source_lines(shifted))
    strange = verdict(source_rows(tmp_path, 0.0, strange=True))
    assert strange["verdict"] in ("none", "too_quiet")  # a flat input tells nothing
    assert any(
        "NO recorded sensor" in line or "too quiet" in line for line in words.source_lines(strange)
    )


def test_what_is_flat_or_too_short_is_not_judged(db):
    path, con = db
    t0 = int(time.time()) - 3600
    for i in range(10):
        row(con, t0 + i * 30, None, 50.0, 40.0, {"Cpu Fan": 30.0 + i})
    con.commit()
    rows = rows_of(path)
    pairs = data.data_of(rows, "Cpu Fan")
    few = data.find_source(pairs, CPU_CURVE, ["cpu_temp"])
    assert few["verdict"] == "too_few" and few["samples"] == 10
    assert "not enough samples" in words.source_lines(few)[0]
    con.execute("DELETE FROM cooling")
    for i in range(60):  # plenty of rows, but the duty never moves
        row(con, t0 + i * 30, None, 50.0, 40.0, {"Cpu Fan": 30.0})
    con.commit()
    rows = rows_of(path)
    quiet = data.find_source(data.data_of(rows, "Cpu Fan"), CPU_CURVE, ["cpu_temp"])
    assert quiet["verdict"] == "too_quiet"
    assert "too quiet for a verdict" in " ".join(words.source_lines(quiet))


def test_the_deviation_from_the_curve_needs_the_input_measured(db):
    path, con = db
    t0 = int(time.time()) - 7200
    for i in range(100):
        x = 50.0 + i % 40
        duty = CPU_CURVE.duty_at(x) + (3.0 if i % 10 == 0 else 0.0)
        row(con, t0 + i * 30, None, x, 40.0, {"Cpu Fan": duty}, sources={"Cpu_Curve": x})
    con.commit()
    rows = rows_of(path)
    median, high, seconds = data.deviation(data.data_of(rows, "Cpu Fan"), CPU_CURVE)
    assert median == 0.0 and high == pytest.approx(3.0) and seconds > 0
    assert data.deviation(data.data_of(rows[:10], "Cpu Fan"), CPU_CURVE) is None
    assert data.deviation(data.data_of(rows, "Cpu Fan"), GPU_CURVE) is None  # not measured for it


# -- the recommendations --------------------------------------------------------------------------


def test_the_recommendations_follow_the_settings_and_show_what_the_change_did(db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    run(con, t0, 400, 200, 300)  # 100 min desktop, 50 min game, 50 min desktop
    found = advise(path)
    assert ids(found)[:2] == ["source:Gpu_Curve", "mix:Mix_Case"]
    source = found["list"][0]
    steps = " ".join(source["do"])
    assert (
        "GPU Core" in steps and "Graphics Card" in steps and CARD_ID in steps
    )  # from the readings
    points = source["proposal"]
    assert 30 <= points[0][0] <= 36 and points[0][1] == 22
    assert any(abs(temp - 54) <= 2 for temp, _ in points)  # the card's temperature in a game
    assert all(a[0] < b[0] and a[1] <= b[1] for a, b in zip(points, points[1:], strict=False))
    mix = found["list"][1]
    assert mix["after_number"] == 1 and any("Max" in step for step in mix["do"])
    assert mix["after"] == "source:Gpu_Curve"
    assert "preliminary" in source["confidence"] and "certain" in source["confidence"]
    assert not found["changed"] and found["effect"] == [] and found["change"] is None
    assert found["basis"] == source["basis"] == "50 min game (1 round), 2.5 h desktop"
    assert found["proposals"]["Gpu_Curve"] == points

    # the user follows the advice: FanControl changes, the new data follows the proposals
    changed_at = t0 + 400 * 30 + 60
    state(con, changed_at, "b", gpu_source=CARD_ID)
    run(con, changed_at + 30, 400, 200, 300, old=False)
    curves = {
        **CURVES,
        "Gpu_Curve": make_curve(
            "Gpu_Curve", [(35, 22), (55, 54), (62, 65), (70, 85), (78, 100)], CARD_ID
        ),
    }
    after = advise(path, curves=curves)
    assert after["changed"] and after["since"] == changed_at
    assert "source:Gpu_Curve" not in ids(after) and "mix:Mix_Case" not in ids(after)
    assert any("sensor" in line for line in after["change"]["what"])
    game = next(e for e in after["effect"] if e["mode"] == "game")
    case = next(r for r in game["rows"] if r["quantity"] == "Case Fan")
    assert case["after"] > case["before"] + 10
    desk = next(e for e in after["effect"] if e["mode"] == "desktop")
    card = next(r for r in desk["rows"] if r["quantity"] == "Gpu Fan")
    assert card["after"] < card["before"] - 5
    assert after["changes"] == [changed_at]


def test_a_curve_whose_sensor_is_measured_is_compared_with_the_device_directly(db):
    """Where the input of the curve was kept (LibreHardwareMonitor could read it), the advice
    does not need to work it out: the sensor the curve reads is compared with the card's."""
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    for i in range(400):
        playing = 200 <= i < 300
        cpu = 72.0 if playing else 55.0
        gpu = 54.0 if playing else 28.0
        chip = cpu - 3.0  # what the curve really reads
        duties = {
            "Cpu Fan": CPU_CURVE.duty_at(cpu),
            "Gpu Fan": GPU_CURVE.duty_at(chip),
            "Case Fan": CPU_BASE.duty_at(cpu),
        }
        row(
            con, t0 + i * 30, "Space Game" if playing else None, cpu, gpu, duties,
            sources={"Cpu_Curve": cpu, "Cpu_Base": cpu, "Gpu_Curve": chip},
        )  # fmt: skip
    con.commit()
    found = advise(path)
    assert "source:Gpu_Curve" in ids(found)
    why = next(e for e in found["list"] if e["id"] == "source:Gpu_Curve")["why"]
    assert "On the desktop it sees 52 °C, the graphics card is at 28 °C" in why  # as measured


def test_a_curve_on_the_right_device_is_noted_as_checked(db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    for i in range(400):
        cpu = 55.0 + 10 * math.sin(i / 20)
        row(con, t0 + i * 30, None, cpu, 30.0, {"Cpu Fan": CPU_CURVE.duty_at(cpu)})
    con.commit()
    found = advise(path)
    assert not found["list"]
    assert any("Cpu_Curve follows the right device" in note for note in found["checked"])


def test_right_after_a_change_the_advice_waits_for_data(db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    run(con, t0, 400, 200, 300)
    state(con, t0 + 400 * 30 + 60, "b")
    run(con, t0 + 400 * 30 + 90, 5, 100, 100, old=False, step=60)  # five minutes of desktop
    found = advise(path)
    assert found["waiting"] and not found["list"]
    assert found["waiting"].startswith("Since the change at ")
    assert "only 5 min of data" in found["waiting"] and "30 min of desktop" in found["waiting"]


def test_what_is_too_warm_in_a_game_comes_from_the_limits_the_options_name(db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    run(con, t0, 400, 200, 300, cpu_game=92.0)
    assert "warm:cpu_temp" in ids(advise(path))  # the default limit is 90
    warm = next(e for e in advise(path)["list"] if e["id"] == "warm:cpu_temp")
    assert warm["title"] == "CPU gets too warm in a game"
    assert "limit 90 °C" in warm["why"] and "Raise the curve Cpu_Curve around 92 °C." in warm["do"]
    lenient = advice.parse_limits([{"key": "cpu.temp", "max": 100}], print)
    found = advise(path, limits=lenient)
    assert "warm:cpu_temp" not in ids(found)
    assert any(
        "Temperatures in a game are fine" in note and "CPU 92 °C" in note
        for note in found["checked"]
    )
    strict = advice.parse_limits([{"key": "cpu.temp", "max": 60, "name": "Processor"}], print)
    named = next(e for e in advise(path, limits=strict)["list"] if e["id"] == "warm:cpu_temp")
    assert named["title"] == "Processor gets too warm in a game" and "limit 60 °C" in named["why"]


def test_any_recorded_temperature_can_have_a_limit_and_is_named_by_its_reading(db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    for i in range(400):
        playing = 200 <= i < 300
        row(
            con, t0 + i * 30, "Space Game" if playing else None, 60.0, 40.0, {},
            temps={"temp.board.system": 48.0 if playing else 35.0, "temp.nvme.x": 50.0},
        )  # fmt: skip
    con.commit()
    names = {
        **NAMES,
        "sensors": [*NAMES["sensors"], {"key": "temp.board.system", "label": "Motherboard"}],
    }
    limits = advice.parse_limits(
        [{"key": "temp.board.system", "max": 45}, {"key": "temp.nvme.x", "max": 70, "name": "SSD"}],
        print,
    )
    found = advise(path, names=names, limits=limits)
    assert ids(found) == ["warm:temp.board.system"]
    board = found["list"][0]
    assert board["title"] == "Motherboard gets too warm in a game"  # named by the catalogue
    assert board["do"] == ["Let the fans cool Motherboard more in a game."]
    assert any("SSD 50 °C" in note for note in found["checked"])


def test_limits_that_are_no_limits_are_reported_and_the_defaults_stay():
    problems = []
    assert advice.parse_limits([], problems.append) == advice.DEFAULT_LIMITS
    got = advice.parse_limits(
        [
            "cpu",
            {"key": "cpu.temp"},
            {"max": 90},
            {"key": "x", "max": True},
            {"key": "gpu.hotspot", "max": 95},
        ],
        problems.append,
    )
    assert got == (advice.Limit("gpu_hotspot", 95.0, ""),) and len(problems) == 4
    assert advice.parse_limits(["junk"], problems.append) == advice.DEFAULT_LIMITS


def test_a_fan_that_keeps_jumping_oscillates_and_a_calm_one_does_not(db):
    path, con = db
    t0 = int(time.time()) - 8 * 3600
    state(con, t0 - 60, "a")
    for i in range(7 * 120):  # seven hours of 30 s rows
        busy = {"Cpu Fan": 30.0 + (i % 2) * 10, "Gpu Fan": 25.0}
        row(con, t0 + i * 30, None, 50.0, 30.0, busy)
    con.commit()
    curves = {
        **CURVES,
        "Cpu_Curve": fc.Curve(
            "Cpu_Curve",
            CPU_CURVE.points,
            CPU_ID,
            (),
            {"HysteresisValueUp": 2, "HysteresisValueDown": 3, "ResponseTimeDown": 5},
        ),
    }
    found = advise(path, curves=curves)
    assert "oscillation:Cpu_Curve" in ids(found) and "oscillation:Gpu_Curve" not in ids(found)
    rec = next(e for e in found["list"] if e["id"] == "oscillation:Cpu_Curve")
    assert rec["title"] == "Cpu Fan oscillates"
    assert (
        "Raise the hysteresis from 2/3 K to 4/5 K" in rec["do"][1]
        and "from 5 s to 13 s" in rec["do"][1]
    )
    assert any("No oscillation" in note and "Gpu Fan" in note for note in found["checked"])


def test_oscillation_is_judged_only_from_six_hours(db):
    path, con = db
    t0 = int(time.time()) - 3 * 3600
    for i in range(100):
        row(con, t0 + i * 30, None, 50.0, 30.0, {"Cpu Fan": 30.0 + (i % 2) * 10})
    con.commit()
    found = advise(path)
    assert "oscillation:Cpu_Curve" not in ids(found)
    assert any("Oscillation: judged from 6 h of recording" in note for note in found["checked"])


def test_a_fan_that_runs_higher_than_it_must_on_the_desktop_can_be_quieter(db):
    path, con = db
    t0 = int(time.time()) - 3 * 3600
    for i in range(300):
        duty = 22.0 if i % 10 < 3 else 30.0  # mostly 30 %: the curve starts rising too early
        row(con, t0 + i * 30, None, 50.0, 30.0, {"Cpu Fan": duty}, sources={"Cpu_Curve": 45.0})
    con.commit()
    found = advise(path, curves={"Cpu_Curve": CPU_CURVE}, fans={"Cpu Fan": FANS["Cpu Fan"]})
    rec = next(e for e in found["list"] if e["id"] == "quieter:Cpu_Curve")
    assert rec["title"] == "Cpu Fan quieter on the desktop"
    assert rec["proposal"][0] == [47, 22.0] and rec["proposal"][1][0] > 49
    assert rec["effect"] == ["Desktop: 22 % (≈ 564 RPM) instead of 30 % (≈ 660 RPM)"]
    assert (
        "preliminary" in rec["confidence"]
        and rec["note"] == "Nothing changes in a game: the points above stay."
    )
    assert found["proposals"]["Cpu_Curve"] == rec["proposal"]


def test_a_fan_at_its_base_value_or_saving_nothing_is_not_made_quieter(db):
    path, con = db
    t0 = int(time.time()) - 3 * 3600
    for i in range(300):
        row(con, t0 + i * 30, None, 50.0, 30.0, {"Cpu Fan": 22.0}, sources={"Cpu_Curve": 45.0})
    con.commit()
    found = advise(path, curves={"Cpu_Curve": CPU_CURVE}, fans={"Cpu Fan": FANS["Cpu Fan"]})
    assert not found["list"]
    assert any("Cpu Fan runs at its base value on the desktop" in n for n in found["checked"])


def test_cooling_that_gets_worse_at_the_same_load_and_outside_air_is_found(db):
    path, con = db
    t0 = int(time.time()) - 17 * 86400
    for i in range(17 * 288):  # 17 days, a row every 5 minutes
        day = i / 288
        warmer = 5.0 if day > 10 else 0.0
        row(con, t0 + i * 300, None, 40.0 + warmer, 30.0, {}, load=10.0, outside=10.0)
    con.commit()
    found = advise(path)
    rec = next(e for e in found["list"] if e["id"] == "trend")
    assert rec["title"] == "The cooling is getting worse"
    assert "5.0 K warmer than in the first seven days" in rec["why"]
    assert rec["do"] == ["Clean the dust filters and the cooler's fins."]


def test_cooling_that_stays_as_it_was_is_noted_and_a_short_record_says_so(db, tmp_path):
    path, con = db
    t0 = int(time.time()) - 17 * 86400
    for i in range(17 * 288):
        row(con, t0 + i * 300, None, 40.0, 30.0, {}, load=10.0, outside=10.0)
    con.commit()
    found = advise(path)
    assert not found["list"]
    assert any("Cooling is stable" in note and "+0.0 K" in note for note in found["checked"])
    short = tmp_path / "short.db"
    other = database(short)
    for i in range(50):
        row(other, t0 + i * 300, None, 40.0, 30.0, {}, load=10.0, outside=10.0)
    other.commit()
    assert any("compared from 14 days" in n for n in advise(short)["checked"])


def test_a_mix_that_takes_the_higher_part_is_checked_and_one_that_does_not_is_found(db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    run(con, t0, 400, 200, 300, old=False)  # the mix is max(cpu part, card)
    found = advise(path)
    assert "mix:Mix_Case" not in ids(found)
    assert any("Mix_Case takes the higher value of Gpu_Curve" in note for note in found["checked"])


def test_without_fancontrol_the_advice_has_the_data_but_no_curves(db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    run(con, t0, 400, 200, 300, cpu_game=92.0)
    found = advice.advise(rows_of(path), [], {}, {}, NAMES)
    assert ids(found) == ["warm:cpu_temp"]  # what needs no curve still works
    assert found["list"][0]["do"] == [
        "Let the fans cool the CPU more in a game: raise its fan curve around 92 °C."
    ]
    assert found["since"] == rows_of(path)[0].ts and not found["changed"]


def test_the_names_of_the_devices_come_from_the_readings_not_from_the_code(db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    run(con, t0, 400, 200, 300)
    other = {**NAMES, "gpu": "Another Card", "sensors": [{**NAMES["sensors"][0], "label": "Edge"}]}
    steps = next(e for e in advise(path, names=other)["list"] if e["id"] == "source:Gpu_Curve")[
        "do"
    ]
    assert "“Edge” of “Another Card”" in " ".join(steps) and "Graphics Card" not in " ".join(steps)
    unnamed = next(e for e in advise(path, names={})["list"] if e["id"] == "source:Gpu_Curve")["do"]
    assert "the sensor of the device" in " ".join(unnamed)
    assert words.device_name("gpu", {"gpu": "Another Card"}) == "GPU temperature (Another Card)"
    assert words.device_name("cpu", {}) == "CPU temperature"
    assert words.sensor_name("temp.board.system", {}) == "temp.board.system"


# -- the analysis for a page ----------------------------------------------------------------------


def analyst_for(path, clock=None, stamp=lambda: None, limits=advice.DEFAULT_LIMITS):
    kw = {"clock": clock} if clock else {}
    return cooling.Analyst(lambda: path, lambda: (CURVES, FANS), limits, stamp, **kw)


def test_the_analysis_has_what_a_page_draws_and_the_text_report(db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    run(con, t0, 400, 200, 300)
    found = analyst_for(path).analysis(days=None, points=120)
    assert not found["empty"] and found["rows"] == 400 and len(found["history"]["t"]) == 120
    assert [s["curve"] for s in found["series"]] == ["Cpu_Curve", "Gpu_Curve", "Mix_Case"]
    assert [s["device"] for s in found["series"]] == ["cpu", "gpu", "mix"]
    assert found["sessions"][0]["name"] == "Space Game"
    assert {s["name"] for s in found["states"]} == {"Desktop", "Space Game"}
    assert found["states"][0]["name"] == "Desktop"
    assert set(found["history"]) == {"t", "temps", "duty"}
    assert set(found["history"]["temps"]) == {"cpu_temp", "gpu_temp", "gpu_hotspot"}
    assert set(found["history"]["duty"]) == {"Cpu_Curve", "Gpu_Curve", "Mix_Case"}
    assert sum(v is not None for v in found["history"]["duty"]["Cpu_Curve"]) > 100
    gpu = next(p for p in found["plots"] if p["curve"] == "Gpu_Curve")
    assert (
        gpu["proposal"]
        and not gpu["measured"]
        and gpu["x_name"] == "GPU temperature (Graphics Card)"
    )
    assert gpu["target"] == [list(p) for p in GPU_CURVE.points] and gpu["cloud"][0][2] > 0
    cpu = next(p for p in found["plots"] if p["curve"] == "Cpu_Curve")
    assert cpu["measured"] and cpu["x_name"] == "Curve input (measured)" and cpu["proposal"] is None
    mix = next(p for p in found["plots"] if p["curve"] == "Mix_Case")
    assert "Mix curve of Cpu_Base and Gpu_Curve" in mix["note"] and mix["target"] == [
        list(p) for p in CPU_BASE.points
    ]
    # findings that a recommendation already covers are not said twice
    assert not any(f.get("id") in ("source:Gpu_Curve", "mix:Mix_Case") for f in found["findings"])
    assert any("follows the curve Cpu_Curve" in f["text"] for f in found["findings"])
    table = {r["fan"]: r for r in found["table"]}
    assert table["Cpu Fan"]["curve"] == "Cpu_Curve" and table["Cpu Fan"]["p50"] is not None
    assert table["Cpu Fan"]["characteristic"] and table["Case Fan"]["rpm_max"] > 0
    assert found["advice"]["list"] and found["text"] == analyst_for(path).report(days=None)
    json.dumps(found)  # all of it is data a page can take as it is


def test_the_findings_say_how_long_the_recording_must_be_for_the_rest(db):
    path, con = db
    t0 = int(time.time()) - 8 * 3600
    run(con, t0, 400, 200, 300)  # 3.3 h
    info = lambda found: [f["text"] for f in found["findings"] if f["level"] == "info"]  # noqa: E731
    assert info(analyst_for(path).analysis(days=None)) == [
        "Unused curve points and oscillation are judged from 6 h of recording: so far 3.3 h."
    ]
    run(con, t0 + 400 * 30, 400, 100, 200)  # 3.3 h more: enough
    assert info(analyst_for(path).analysis(days=None)) == []


def test_an_empty_database_and_a_period_without_rows_are_said_not_raised(tmp_path, db):
    nothing = analyst_for(tmp_path / "nothing.db")
    assert nothing.analysis() == {"empty": True, "reason": "no database"}
    assert nothing.recommendations() == {"empty": True, "reason": "no database"}
    assert nothing.report() == [f"No database: {tmp_path / 'nothing.db'}"]
    path, _con = db
    assert analyst_for(path).analysis()["reason"] == "no cooling data in the chosen period yet"
    assert analyst_for(path).recommendations()["list"] == []
    assert "No cooling data yet" in analyst_for(path).report()[0]
    broken = tmp_path / "broken.db"
    sqlite3.connect(broken).close()  # a file with no tables
    assert analyst_for(broken).recommendations()["reason"] == "no cooling data yet"
    assert analyst_for(broken).analysis()["empty"] is True


def test_only_the_game_or_only_the_desktop_can_be_asked_for(db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    run(con, t0, 400, 200, 300)
    game = analyst_for(path).analysis(days=None, mode="game")
    assert game["rows"] == 100 and {s["name"] for s in game["states"]} == {"Space Game"}
    desktop = analyst_for(path).analysis(days=None, mode="desktop")
    assert desktop["rows"] == 300 and [s["name"] for s in desktop["states"]] == ["Desktop"]
    recent = analyst_for(path).analysis(days=0.1)  # the last 2.4 h: the recording ended 1.7 h ago
    assert 0 < recent["rows"] < 400


# -- cache and read-only --------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_work_is_kept_for_five_minutes_and_until_fancontrol_changes(db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    run(con, t0, 400, 200, 300)
    clock, mark = Clock(), ["x"]
    analyst = analyst_for(path, clock, stamp=lambda: mark[0])
    first = analyst.recommendations()
    assert analyst.recommendations() is first  # kept
    clock.now += 299
    assert analyst.recommendations() is first
    clock.now += 2  # five minutes
    second = analyst.recommendations()
    assert second is not first and second == first
    mark[0] = "y"  # FanControl's configuration changed
    third = analyst.recommendations()
    assert third is not second
    state(con, t0 + 400 * 30, "b")  # a new state in the file
    fourth = analyst.recommendations()
    assert fourth is not third and fourth["changed"]
    assert analyst.recommendations() is fourth
    assert analyst.analysis(days=None)["advice"] is fourth  # the analysis uses the kept one
    i18n.set_language("de")
    assert analyst.recommendations() is not fourth  # the texts are in another language


def test_a_page_asking_again_gets_the_kept_analysis_per_period_and_mode(db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    run(con, t0, 400, 200, 300)
    clock = Clock()
    analyst = analyst_for(path, clock)
    page = analyst.analysis(days=None)
    assert analyst.analysis(days=None) is page
    game = analyst.analysis(days=None, mode="game")
    assert game is not page and game["rows"] == 100
    assert analyst.analysis(days=None) is page  # each question has its own answer
    lines = analyst.report()
    assert analyst.report() == lines
    clock.now += 301
    fresh = analyst.analysis(days=None)
    assert fresh is not page and fresh["rows"] == page["rows"]  # five minutes are over
    for days in range(1, 12):  # many periods: only the last few answers are kept
        analyst.analysis(days=days)
    assert len(analyst._kept) <= cooling.CACHE_ENTRIES


def test_the_analysis_only_reads_the_database_and_never_writes_it(db, monkeypatch):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    run(con, t0, 400, 200, 300)
    con.close()
    before = path.read_bytes()
    opened = []
    real = sqlite3.connect

    def spy(database, *args, **kwargs):
        opened.append((str(database), kwargs.get("uri")))
        return real(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    analyst = analyst_for(path)
    analyst.analysis(days=None)
    analyst.report()
    assert opened and all(uri is True and name.endswith("?mode=ro") for name, uri in opened)
    assert path.read_bytes() == before
    with pytest.raises(sqlite3.OperationalError):  # what it opens cannot be written
        from libre_panel.services.memory_store import read_only

        read_only(path).execute("INSERT INTO meta VALUES ('x', 'y')")


# -- the words ------------------------------------------------------------------------------------


def test_the_text_report_in_english_and_in_german(db):
    path, con = db
    t0 = int(time.time()) - 8 * 3600
    state(con, t0 - 60, "a")
    run(con, t0, 400, 200, 300)
    analyst = analyst_for(path)
    english = analyst.report()
    top = english[0]
    assert top.startswith("RECORDED  3.3 h in 400 rows")
    joined = "\n".join(english)
    assert "CPU FAN" in joined and "Curve         Cpu_Curve on" in joined
    assert "Duty          median" in joined and "RECOMMENDATIONS" in joined
    assert "1. Tie Gpu Fan to the graphics card" in joined
    assert "NO recorded sensor" not in joined
    assert "Basis: 50 min game (1 round), 2.5 h desktop" in joined
    assert "follows CPU with a fixed offset of -3.0 K" in joined  # named as a person reads it
    i18n.set_language("de")
    german = "\n".join(analyst.report())
    assert german.startswith("ERFASST") and "Kurve" in german and "Stellwert" in german
    assert "Gpu Fan an die Grafikkarte hängen" in german
    assert "Grundlage: 50 min Spiel (1 Runde), 2,5 h Desktop" in german
    assert "In FanControl die Kurve „Gpu_Curve“ öffnen." in german
    assert "Recorded" not in german and "RECOMMENDATIONS" not in german


def test_the_changes_in_fancontrol_are_worded_in_both_languages():
    changes = [
        {"kind": "curve-added", "curve": "A"},
        {"kind": "curve-removed", "curve": "B"},
        {"kind": "source", "curve": "C", "old": "/x", "new": "/y"},
        {"kind": "points", "curve": "D", "old": ["1,2"], "new": ["3,4", "5,6"]},
        {"kind": "mix-function", "curve": "E", "old": 0, "new": 1},
        {"kind": "hysteresis", "curve": "F"},
        {"kind": "fan", "fan": "G", "fields": ["MinimumPercent", "Enable"]},
    ]
    assert words.change_lines(changes) == [
        "Curve A created", "Curve B deleted", "C: sensor /x → /y", "D: points 1,2 → 3,4 5,6",
        "E: mix function 0 → 1", "F: hysteresis changed", "Fan G: MinimumPercent, Enable",
    ]  # fmt: skip
    assert words.change_lines([]) == ["only small things that do not touch the curves"]
    i18n.set_language("de")
    assert words.change_lines(changes[:2]) == ["Kurve A neu angelegt", "Kurve B gelöscht"]


def test_the_words_for_a_duty_and_a_speed_say_when_it_was_never_measured():
    measured = [(21.0, 500.0), (31.0, 640.0), (41.0, 800.0)]
    assert words.percent_rpm(26.0, measured) == "26 % (≈ 570 RPM)"
    assert "below everything measured" in words.percent_rpm(10.0, measured)
    assert "above everything measured" in words.percent_rpm(90.0, measured)
    assert words.percent_rpm(30.0, []) == "30 %"
    assert words.rpm_at(measured, None) is None and words.rpm_at([], 30.0) is None
    assert (
        words.compare("Game", 52.0, 52.5, measured) == "Game: practically unchanged (52 % → 52 %)"
    )
    assert words.compare("Game", 41.0, 21.0, measured) == (
        "Game: 41 % (≈ 800 RPM) instead of 21 % (≈ 500 RPM)"
    )
    assert words.points_text([[32, 22], [54.0, 82.4]]) == "32 °C → 22 %, 54 °C → 82 %"


# -- the service ----------------------------------------------------------------------------------


@pytest.fixture
def made(tmp_path):
    made = []

    def make(**options):
        host = PluginHost()
        manager = ServiceManager(host)
        config = Config(services=ServicesConfig(["cooling"], {"cooling": options}))
        manager.apply(config)
        assert manager.state == {"cooling": "running"}, manager.state
        made.append((manager, host))
        return manager.running["cooling"][0]

    yield make
    for manager, host in made:
        manager.stop()
        host.close()


def fancontrol_file(tmp_path):
    curves = [
        curve("Cpu_Curve", [f"{t:g},{d:g}" for t, d in CPU_CURVE.points], CPU_ID),
        curve("Cpu_Base", [f"{t:g},{d:g}" for t, d in CPU_BASE.points], CPU_ID),
        curve("Gpu_Curve", [f"{t:g},{d:g}" for t, d in GPU_CURVE.points], IGPU_ID),
        curve("Mix_Case", [], mix=["Cpu_Base", "Gpu_Curve"]),
    ]
    controls = [
        control("Cpu Fan", "/lpc/chip/control/1", "Cpu_Curve"),
        control("Gpu Fan", "/lpc/chip/control/2", "Gpu_Curve"),
        control("Case Fan", "/lpc/chip/control/3", "Mix_Case"),
    ]
    return write_config(tmp_path / "userConfig.json", curves, controls)


def test_the_service_works_from_the_memorys_file_and_fancontrols_configuration(made, tmp_path, db):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    state(con, t0 - 60, "a")
    run(con, t0, 400, 200, 300)
    con.commit()
    config = fancontrol_file(tmp_path)
    service = made(database=str(path), fancontrol=str(config))
    found = service.recommendations()
    assert ids(found)[:2] == ["source:Gpu_Curve", "mix:Mix_Case"]
    assert service.recommendations() is found  # kept
    result = service.analysis(days=None, points=60)
    assert result["advice"] is found and len(result["history"]["t"]) == 60
    assert result["text"] and service.report(days=None)[0].startswith("RECORDED")
    assert service.status() == {
        "database": str(path), "database_found": True, "fancontrol": str(config),
        "curves": 4, "fans": 3,
    }  # fmt: skip
    changed = fancontrol_file(tmp_path)  # FanControl's file is written again: read again
    text = json.loads(changed.read_text(encoding="utf-8-sig"))
    text["FanControl"]["FanCurves"][0]["Points"] = ["50,25", "80,70"]
    changed.write_text(json.dumps(text), encoding="utf-8")
    assert service.recommendations() is not found


def test_the_service_without_fancontrol_or_data_still_answers(made, tmp_path):
    service = made(database=str(tmp_path / "nothing.db"), fancontrol=str(tmp_path / "typo.json"))
    assert service.recommendations() == {"empty": True, "reason": "no database"}
    assert service.analysis()["empty"] is True and "No database" in service.report()[0]
    assert service.status()["database_found"] is False and service.status()["curves"] == 0


def test_the_service_looks_where_the_memory_writes_unless_told_otherwise(made, isolated_home):
    service = made()
    assert service.database == isolated_home / "plugins-data" / "memory" / "memory.db"
    assert cooling.default_database() == service.database


def test_the_limits_option_reaches_the_rules(made, tmp_path, db, caplog):
    path, con = db
    t0 = int(time.time()) - 5 * 3600
    run(con, t0, 400, 200, 300, cpu_game=72.0)
    con.commit()
    with caplog.at_level("WARNING"):
        strict = made(
            database=str(path),
            fancontrol=str(tmp_path / "none.json"),
            limits=[{"key": "cpu.temp", "max": 60, "name": "Processor"}, {"key": "nonsense"}],
        )
    assert "limits[2]" in caplog.text
    found = strict.recommendations()
    assert found["list"][0]["title"] == "Processor gets too warm in a game"
