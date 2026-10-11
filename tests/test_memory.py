"""The memory: a sample a second into minutes, games and the cooling state in a SQLite file
(a temp file here), the readings it takes and the ones it never does, and the service that
runs it. Time is the samples' own, so a day passes in a moment."""

import json
import sqlite3
import threading
import time

import pytest
from fake_sensors import wait_for

from libre_panel.sensors.base import Reading, Snapshot
from libre_panel.services import memory
from libre_panel.services.memory import Reader, Recorder, Sample, Settings
from libre_panel.services.memory_store import MEANS, Store, read_only

T0 = 1_800_000_000 - 1_800_000_000 % 60  # the start of a minute


def values(**given):
    found = dict.fromkeys(MEANS)
    found.update(given)
    return found


def sample(at, game=None, fps=None, program="spacegame", fans=None, sources=None, **given):
    return Sample(
        at,
        values(**given),
        game=game,
        program=program if game else "",
        fps=fps,
        low=fps * 0.7 if fps else None,
        frametime=1000 / fps if fps else None,
        limiter="gpu" if fps else None,
        fans=fans or {},
        sources=sources or {},
    )


class Rig:
    """A store on a temp file and a recorder on it."""

    def __init__(self, tmp_path, **settings):
        self.path = tmp_path / "memory.db"
        self.store = Store(self.path)
        self.store.start()
        self.reports = []
        self.recorder = Recorder(
            self.store, Settings(**settings), lambda report, stamp: self.reports.append(report)
        )

    def settle(self):
        """Everything queued so far is written."""
        done = threading.Event()
        self.store.run(lambda con: done.set())
        assert done.wait(10)

    def finish(self, at=None):
        if at is not None:
            self.recorder.close(at)
        self.store.stop()
        return sqlite3.connect(self.path)

    def play(self, start, seconds, fps=140, game="Space Game", program="spacegame", temp=70.0):
        """A game for ``seconds``, then a sample at the end with no game: the time after."""
        for i in range(int(seconds)):
            self.recorder.add(
                sample(
                    start + i,
                    game,
                    fps + (i % 20) - 10,
                    program,
                    cpu_temp=temp + (i % 5),
                    gpu_temp=55.0,
                    gpu_hotspot=78.0 + (i % 3),
                    cpu_power=90.0,
                    gpu_power=320.0,
                    vram_used=14.0,
                )
            )
        self.recorder.add(sample(start + seconds, cpu_temp=40.0))
        return start + seconds + 5

    def reported(self, count):
        assert wait_for(lambda: len(self.reports) >= count)
        return self.reports[count - 1]


@pytest.fixture
def rig(tmp_path):
    made = []

    def make(**settings):
        made.append(Rig(tmp_path, **settings))
        return made[-1]

    yield make
    for each in made:
        each.store.stop()


# -- minutes and games ----------------------------------------------------------------------------


def test_a_sample_a_second_makes_a_row_a_minute_and_a_game_a_row(rig):
    r = rig()
    fans = {"Rear": (900.0, None), "Card": (1500.0, None)}
    t = float(T0)
    step = 0.04  # the main loop is faster than the memory samples
    while t < T0 + 200:
        sec = t - T0
        in_game = 30 <= sec < 170
        current = sample(
            t,
            "Space Game" if in_game else None,
            100 + int(sec) % 70 if in_game and sec >= 35 else None,
            cpu_load=20 + sec % 10,
            cpu_temp=50 + sec / 10,
            gpu_temp=45.0,
            gpu_hotspot=60 + (sec > 100) * 20,
            cpu_power=40.0,
            gpu_power=200.0,
            volume=30,
            mem_used=18.0,
            fans=fans,
        )
        current.music = sec < 30
        current.outside = 14.5
        r.recorder.add(current)
        t += step
    t += 600  # the computer slept: ten minutes with nothing
    for _ in range(100):
        r.recorder.add(sample(t, cpu_load=5.0))
        t += step
    db = r.finish(t)
    minutes = db.execute(
        "SELECT ts, samples, mode, game, cpu_temp, cpu_temp_max, gpu_hotspot_max, fps, "
        "music_s, outside_temp, fans FROM minutes ORDER BY ts"
    ).fetchall()
    assert r.store.error is None
    assert len(minutes) == 5  # four minutes, and the one after the gap
    assert sum(row[1] for row in minutes[:4]) == 200  # a sample a second, not one per 0.04 s
    assert minutes[0][8] == 30 and minutes[1][8] == 0  # music only as seconds
    assert [row[2] for row in minutes] == ["game", "game", "game", "normal", "normal"]
    assert minutes[1][3] == "Space Game" and minutes[4][3] is None
    assert minutes[0][9] == 14.5
    assert json.loads(minutes[0][10]) == {"Rear": 900, "Card": 1500}
    assert minutes[0][6] == 60 and minutes[1][6] == 80  # the highest hot spot of the minute
    assert minutes[0][5] > minutes[0][4]  # a peak is above the mean
    game = db.execute(
        "SELECT game, program, started, duration_s, fps_s, fps_mean, fps_p01, levels, "
        "limit_gpu_s, gpu_hotspot_max, outside_temp, closed FROM sessions"
    ).fetchall()
    assert len(game) == 1
    name, program, started, length, fps_s, mean, low, levels, limited, hotspot, outside, closed = (
        game[0]
    )
    assert (name, program, started, length) == ("Space Game", "spacegame", T0 + 30, 140)
    assert fps_s == 135 and limited == 135  # seconds with frame rates; the game service's logic
    levels = json.loads(levels)
    assert levels["edges"] == [60, 90, 120, 144, 165] and sum(levels["seconds"]) == fps_s
    assert 100 <= low <= 102 and 130 <= mean <= 136
    assert (hotspot, outside, closed) == (80, 14.5, 1)


def test_the_memory_keeps_no_title_no_window_and_no_file_name(rig, tmp_path):
    """Whatever a title says never reaches a sample, so it cannot reach the file."""
    snapshot = Snapshot(
        readings={
            "media.title": Reading("media.title", "Secret Song"),
            "media.artist": Reading("media.artist", "Secret Artist"),
            "media.album": Reading("media.album", "Secret Album"),
            "media.state": Reading("media.state", "Playing"),
            "game.name": Reading("game.name", "Space Game"),
            "game.app": Reading("game.app", "SpaceGame.exe"),
            "game.fps": Reading("game.fps", 120.0),
            "proc.cpu.1.name": Reading("proc.cpu.1.name", "secret-program"),
            "cpu.temp": Reading("cpu.temp", 60.0),
        }
    )
    reader = Reader(Settings())
    s = reader.read(snapshot, T0)
    assert s.music is True and s.game == "Space Game" and s.program == "spacegame"
    assert "Secret" not in repr(s) and "secret" not in repr(s)
    r = rig()
    for i in range(130):
        r.recorder.add(reader.read(snapshot, T0 + i))
    db = r.finish(T0 + 130)
    dump = "\n".join(db.iterdump()).lower()
    assert "space game" in dump and "spacegame" in dump  # the game's name and program: kept
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("memory.db*")).lower()
    assert b"secret" not in raw


def test_a_game_in_progress_is_written_every_minute(rig):
    r = rig()
    for i in range(130):
        r.recorder.add(sample(T0 + i, "Space Game", 144.0))
    db = r.finish()  # stopped without closing: as if the program went away
    row = db.execute("SELECT duration_s, closed FROM sessions").fetchone()
    assert row[0] >= 120 and row[1] == 0  # the minute's write, and not yet ended


def test_a_game_that_ends_is_closed_and_another_name_is_another_game(rig):
    r = rig()
    for i in range(10):
        r.recorder.add(sample(T0 + i, "Space Game", 100.0))
    for i in range(10, 20):
        r.recorder.add(sample(T0 + i, "Other Game", 100.0, program="other"))
    db = r.finish(T0 + 20)
    rows = db.execute("SELECT game, program, closed FROM sessions ORDER BY id").fetchall()
    assert rows == [("Space Game", "spacegame", 1), ("Other Game", "other", 1)]


def test_the_program_may_come_after_the_name(rig):
    """PresentMon needs two frames: the program is known a moment after the game's name."""
    r = rig()
    r.recorder.add(sample(T0, "Space Game", None, program=""))
    r.recorder.add(sample(T0 + 1, "Space Game", 90.0, program="SpaceGame"))
    db = r.finish(T0 + 2)
    assert db.execute("SELECT program, fps_s FROM sessions").fetchone() == ("SpaceGame", 1)


def test_a_store_that_cannot_open_its_file_says_so_and_tries_again(tmp_path):
    now, asked = [0.0], []

    def clock():
        asked.append(now[0])
        return now[0]

    folder = tmp_path / "no" / "such" / "folder"
    store = Store(folder / "m.db", retry_s=30.0, clock=clock)
    store.start()
    store.put("INSERT INTO meta VALUES ('a', 'b')")
    assert wait_for(lambda: store.error is not None)
    assert "cannot be used" in store.error and store.written == 0
    folder.mkdir(parents=True)
    store.put("INSERT INTO meta VALUES ('a', 'b')")  # too early: the job is dropped
    assert wait_for(lambda: len(asked) >= 3)  # the store has looked at the clock for it
    now[0] = 31.0
    store.put("INSERT INTO meta VALUES ('c', 'd')")
    assert wait_for(lambda: store.written == 1)
    assert store.error is None
    store.stop()
    db = sqlite3.connect(folder / "m.db")
    assert db.execute("SELECT key FROM meta WHERE key IN ('a', 'c')").fetchall() == [("c",)]
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_full_queue_drops_what_does_not_fit_and_says_so(tmp_path):
    store = Store(tmp_path / "m.db", size=3)
    store.start()
    gate, started = threading.Event(), threading.Event()

    def hold(con):
        started.set()
        gate.wait(10)

    store.run(hold)
    assert started.wait(10)  # the writer is busy now: the queue fills
    for n in range(6):
        store.put("INSERT OR REPLACE INTO meta VALUES (?, 'x')", [str(n)])
    assert store.error and "queue is full" in store.error
    gate.set()
    store.stop()
    db = sqlite3.connect(tmp_path / "m.db")
    assert db.execute("SELECT COUNT(*) FROM meta WHERE value = 'x'").fetchone()[0] == 3


def test_a_failing_statement_is_said_and_the_next_one_is_written(tmp_path):
    store = Store(tmp_path / "m.db")
    store.start()
    store.put("INSERT INTO nothing VALUES (1)")
    assert wait_for(lambda: store.error is not None)
    store.put("INSERT INTO meta VALUES ('ok', '1')")
    assert wait_for(lambda: store.written == 1 and store.error is None)
    store.stop()


# -- the cooling state ----------------------------------------------------------------------------


def cooling_rows(db):
    return db.execute(
        "SELECT ts, mode, game, cpu_temp, fans, sources, temps FROM cooling ORDER BY ts"
    ).fetchall()


def test_the_cooling_state_is_written_when_it_moves_not_on_a_clock(rig):
    r = rig()
    t = float(T0)
    for i in range(200):
        r.recorder.add(
            sample(
                t + i,
                cpu_temp=50.0 + (i > 100) * 5,
                fans={"Rear": (900.0, 30.0 + (i > 100) * 10)},
                sources={"Case_Fan": 51.23},
            )
        )
    r.recorder.add(sample(t + 201, "Space Game", 100.0, cpu_temp=70.0))  # a game: at once
    db = r.finish(t + 202)
    rows = cooling_rows(db)
    assert json.loads(rows[0][4]) == {"Rear": [900.0, 30.0]}
    assert json.loads(rows[0][5]) == {"Case_Fan": 51.2}
    assert rows[-1][1:3] == ("game", "Space Game")  # a change of state is written at once
    assert rows[-1][4:] == (None, None, None)  # nothing to say is NULL, not an empty table
    assert 3 <= len(rows) <= 8  # when it moves, not once a second
    assert rows[0][1:3] == ("normal", None)


def test_the_cooling_state_waits_twelve_seconds_five_in_a_game_and_beats_each_minute(rig):
    r = rig()
    t = float(T0)
    for i in range(100):  # a temperature that never stops moving, in the desktop
        r.recorder.add(sample(t + i, cpu_temp=40.0 + 5 * (i % 2)))
    for i in range(100, 130):  # and in a game
        r.recorder.add(sample(t + i, "Space Game", 100.0, cpu_temp=40.0 + 5 * (i % 2)))
    quiet = [sample(t + 200 + i, cpu_temp=40.0) for i in range(190)]  # nothing moves for 190 s
    for each in quiet:
        r.recorder.add(each)
    db = r.finish(t + 400)
    stamps = [row[0] for row in cooling_rows(db)]
    desktop = [s for s in stamps if s < t + 100]
    game = [s for s in stamps if t + 100 <= s < t + 130]
    steady = [s for s in stamps if s >= t + 200]
    assert min(b - a for a, b in zip(desktop, desktop[1:], strict=False)) >= 12
    assert 5 <= min(b - a for a, b in zip(game, game[1:], strict=False)) < 12
    assert max(b - a for a, b in zip(steady, steady[1:], strict=False)) <= 60  # the heartbeat
    assert len(steady) >= 3  # 190 s of nothing still leaves a row a minute


def test_what_is_noise_does_not_count_as_moving(rig):
    r = rig()
    t = float(T0)
    for i in range(120):
        r.recorder.add(
            sample(
                t + i,
                cpu_temp=50.0 + (i % 2) * 1.5,  # below 2 K
                cpu_power=40.0 + (i % 2) * 10,  # below 15 W
                cpu_load=20.0 + (i % 2) * 15,  # below 20 %
                fans={"Rear": (900.0 + (i % 2) * 30, 30.0)},  # below 40 rpm
                sources={"Curve": 50.0 + (i % 2)},
            )
        )
    db = r.finish(t + 130)
    assert len(cooling_rows(db)) <= 3  # the first, and the beats


def test_a_fan_that_appears_or_a_value_that_goes_missing_is_a_move(rig):
    r = rig()
    t = float(T0)
    for i in range(40):
        r.recorder.add(sample(t + i, cpu_temp=50.0))
    for i in range(40, 80):
        r.recorder.add(sample(t + i, cpu_temp=50.0, fans={"New": (500.0, None)}))
    for i in range(80, 120):
        r.recorder.add(sample(t + i, cpu_temp=None, fans={"New": (500.0, None)}))
    rows = cooling_rows(r.finish(t + 130))
    assert [row[0] for row in rows] == [T0, T0 + 40, T0 + 80]
    assert rows[0][4] is None and json.loads(rows[1][4]) == {"New": [500.0, None]}
    assert rows[1][3] == 50.0 and rows[2][3] is None


def test_cooling_can_be_left_out(rig):
    r = rig(cooling=False)
    for i in range(60):
        r.recorder.add(sample(T0 + i, cpu_temp=50.0 + i))
    db = r.finish(T0 + 60)
    assert cooling_rows(db) == [] and db.execute("SELECT COUNT(*) FROM minutes").fetchone()[0] == 1


def feed_days(r, days):
    """A sample (and one a minute later, which ends its minute) on each of these days."""
    for n, day in enumerate(days):
        at = T0 + day * 86400
        r.recorder.add(sample(at, cpu_temp=50.0, fans={"Rear": (900.0, 30.0 + n * 5)}))
        r.recorder.add(sample(at + 61, cpu_temp=50.0))


def test_old_rows_are_forgotten_after_the_days_kept(rig):
    r = rig(keep_days=10, cooling_days=2)
    feed_days(r, (0, 5, 11, 13))
    r.recorder.add(sample(T0 + 14 * 86400, "Old Game", 100.0))
    db = r.finish(T0 + 14 * 86400 + 5)
    stamps = [row[0] for row in db.execute("SELECT ts FROM minutes ORDER BY ts")]
    assert T0 not in stamps  # more than ten days old when the third day's minute ended
    assert T0 + 5 * 86400 in stamps and T0 + 11 * 86400 in stamps
    assert all(row[0] > T0 + 5 * 86400 for row in cooling_rows(db))  # cooling: two days only


def test_old_games_are_forgotten_with_the_minutes(rig):
    r = rig(keep_days=10)
    for i in range(5):
        r.recorder.add(sample(T0 + i, "Old Game", 100.0))
    r.recorder.add(sample(T0 + 5))
    feed_days(r, (1, 11, 13))
    db = r.finish(T0 + 13 * 86400 + 100)
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone() == (0,)


def test_zero_days_keep_everything(rig):
    r = rig(keep_days=0, cooling_days=0)
    feed_days(r, (0, 400, 800))
    db = r.finish(T0 + 801 * 86400)
    assert db.execute("SELECT COUNT(*) FROM minutes").fetchone() == (6,)
    assert len(cooling_rows(db)) >= 3


# -- the round report -----------------------------------------------------------------------------


def test_a_round_is_reported_against_the_last_long_rounds_of_the_same_program(rig):
    r = rig(report_min_s=180.0, report_compare=10)
    t = r.play(float(T0), 300, fps=140)
    first = r.reported(1)
    assert first["comparison"]["count"] == 0 and first["comparison"]["levels"] is None
    t = r.play(t, 60, fps=60)  # the menu: too short for a report
    r.settle()
    assert len(r.reports) == 1
    t = r.play(t, 400, fps=150, temp=76.0)
    third = r.reported(2)
    comparison = third["comparison"]
    assert comparison["count"] == 1  # only the long round counts
    session = third["session"]
    assert abs(comparison["mean"]["fps_mean"] - (session["fps_mean"] - 10)) < 1.5
    assert abs(sum(comparison["levels"]) - 1) < 1e-9
    assert session["duration_s"] == 400 and session["game"] == "Space Game"
    assert session["levels"]["edges"] == [60, 90, 120, 144, 165]
    assert session["cpu_temp_max"] == 80.0 and comparison["mean"]["cpu_temp_max"] == 74.0
    t = r.play(t, 300, fps=200, game="Other Game", program="other")
    assert r.reported(3)["comparison"]["count"] == 0  # another program: nothing to compare
    assert r.finish().execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 4


def test_the_last_ten_rounds_and_no_more_are_compared(rig):
    r = rig(report_min_s=60.0, report_compare=3)
    t = float(T0)
    for n in range(6):
        t = r.play(t, 100, fps=100 + 10 * n)
        r.reported(n + 1)
    last = r.reports[-1]["comparison"]
    assert last["count"] == 3
    assert last["mean"]["fps_mean"] == pytest.approx(129.5, abs=0.01)  # rounds 3, 4 and 5


def example_report(count=7):
    """A round against ``count`` earlier ones: the numbers of the plugin guide."""
    mean = {
        "fps_mean": 142.1, "fps_p01": 101.2, "fps_low_mean": 99.0, "frametime_mean": 6.9,
        "cpu_temp_max": 72.4, "gpu_temp_max": 57.1, "gpu_hotspot_max": 78.0,
        "cpu_power_max": 94.0, "gpu_power_max": 318.0, "vram_used_max": 14.5,
    }  # fmt: skip
    return {
        "session": {
            "game": "Space Game", "started": 1760000000, "ended": 1760002700,
            "duration_s": 2700, "outside_temp": 12.6, "fps_mean": 148.3, "fps_low_mean": 101.0,
            "frametime_mean": 6.7, "limit_cpu_s": 160, "limit_gpu_s": 2440, "limit_cap_s": 10,
            "levels": {"edges": [60, 90, 120, 144, 165], "seconds": [26, 52, 235, 1150, 1040, 107]},
            "cpu_temp_max": 74.1, "gpu_temp_max": 56.0, "gpu_hotspot_max": 80.0,
            "cpu_power_max": 96.0, "gpu_power_max": 327.0, "vram_used_max": 14.8,
        },
        "comparison": {
            "count": count, "mean": mean if count else {},
            "levels": [0.02, 0.03, 0.12, 0.40, 0.36, 0.07] if count else None,
        },
    }  # fmt: skip


def test_the_report_reads_out_its_comparison_for_a_theme_made_of_widgets():
    got = {
        key: value
        for key, (value, _unit, _label) in memory.round_report.readings(example_report()).items()
    }
    assert got["report.count"] == 7
    assert got["report.cpu_temp_mean"] == 72.4 and got["report.cpu_temp_delta"] == pytest.approx(
        1.7
    )
    assert got["report.vram_used_delta"] == pytest.approx(0.3)
    assert got["report.frametime_delta"] == pytest.approx(-0.2)
    assert got["report.level.3"] == pytest.approx(100 * 1150 / 2610)
    assert got["report.level.3.mean"] == pytest.approx(40.0)  # the mean share of the earlier rounds
    # who held the rate down, each one's share of that time; one that did for under half a
    # percent (the 10 s of 2610 here) is left out, as in the words of report.limit
    assert got["report.limit.gpu"] == pytest.approx(100 * 2440 / 2610)
    assert got["report.limit.cpu"] == pytest.approx(100 * 160 / 2610)
    assert got["report.limit.cap"] is None and got["report.limit"] == "GPU 93 %  CPU 6 %"
    report = example_report()
    report["session"].update(limit_gpu_s=0, limit_cpu_s=0, limit_cap_s=900)  # a frame limit
    held = {k: v for k, (v, _u, _l) in memory.round_report.readings(report).items()}
    assert held["report.limit.cap"] == 100.0 and held["report.limit.gpu"] is None
    report["session"].update(limit_gpu_s=0, limit_cpu_s=0, limit_cap_s=0)  # nothing was counted
    none = {k: v for k, (v, _u, _l) in memory.round_report.readings(report).items()}
    assert none["report.limit"] is None
    assert [none[f"report.limit.{name}"] for name in ("gpu", "cpu", "cap")] == [None] * 3


def test_the_first_round_of_a_game_has_nothing_to_compare_and_says_so():
    readings = memory.round_report.readings(example_report(count=0))
    got = {key: value for key, (value, _unit, _label) in readings.items()}
    assert got["report.count"] == 0 and got["report.versus"] == "First round of this game"
    for key in ("report.cpu_temp_mean", "report.cpu_temp_delta", "report.level.3.mean",
                "report.frametime_delta", "report.fps_delta"):  # fmt: skip
        assert got[key] is None, key
    assert got["report.cpu_temp"] == 74.1  # the round's own numbers are there
    labels = {key: label for key, (_value, _unit, label) in readings.items()}
    assert labels["report.level.3.mean"] == labels["report.level.3"] == "120–144"
    assert labels["report.limit.gpu"] == "GPU" and labels["report.count"] == "Earlier rounds"


def test_a_round_without_frame_rates_has_no_report(rig):
    r = rig(report_min_s=60.0)
    for i in range(200):
        r.recorder.add(sample(T0 + i, "Space Game", None))  # no PresentMon
    r.recorder.add(sample(T0 + 200))
    r.settle()
    assert r.reports == []
    assert r.finish().execute("SELECT closed, fps_s FROM sessions").fetchone() == (1, 0)


def test_a_round_that_ends_with_the_service_is_closed_but_not_reported(rig):
    r = rig(report_min_s=60.0)
    for i in range(200):
        r.recorder.add(sample(T0 + i, "Space Game", 100.0))
    db = r.finish(T0 + 200)  # the service stops during the game
    assert r.reports == [] and db.execute("SELECT closed FROM sessions").fetchone() == (1,)


def test_levels_of_another_range_are_not_averaged_in(rig):
    r = rig(report_min_s=60.0, fps_levels=(30.0, 60.0))
    t = r.play(float(T0), 100, fps=50)
    first = r.reported(1)
    assert first["session"]["levels"]["edges"] == [30, 60]
    r.settle()
    r.recorder.settings = Settings(report_min_s=60.0, fps_levels=(60.0, 120.0))
    t = r.play(t, 100, fps=50)
    second = r.reported(2)
    assert second["comparison"]["count"] == 1 and second["comparison"]["levels"] is None
    assert second["comparison"]["mean"]["fps_mean"] is not None  # the numbers still count


def test_the_levels_are_the_shares_of_the_seconds_at_each_frame_rate(rig):
    r = rig(report_min_s=10.0, fps_levels=(60.0, 120.0))
    for i, fps in enumerate([30] * 10 + [90] * 30 + [150] * 60):
        r.recorder.add(sample(T0 + i, "Space Game", float(fps)))
    r.recorder.add(sample(T0 + 100))
    report = r.reported(1)
    assert report["session"]["levels"] == {"edges": [60, 120], "seconds": [10, 30, 60]}
    assert [name for name in memory.round_report.level_names([60, 120])] == [
        "< 60",
        "60–120",
        "120+",
    ]
    assert memory.round_report.level_names([]) == []


def test_the_frame_rate_at_the_first_percent_and_the_means_of_a_round(rig):
    r = rig(report_min_s=10.0)
    for i in range(200):
        r.recorder.add(sample(T0 + i, "Space Game", 20.0 if i < 4 else 150.0))
    r.recorder.add(sample(T0 + 200))
    session = r.reported(1)["session"]
    assert session["fps_p01"] == 20.0  # the 1 % quantile of the seconds: the slow ones
    assert session["fps_mean"] == pytest.approx(147.4)
    assert session["fps_low_mean"] == pytest.approx(0.7 * 147.4)
    assert session["frametime_mean"] > 0 and session["limit_gpu_s"] == 200


# -- the readings it takes ------------------------------------------------------------------------


def reading(key, value, unit="", label="", origin=""):
    return Reading(key, value, unit, label, origin)


def snapshot_of(*readings):
    return Snapshot(readings={r.key: r for r in readings})


def test_the_reader_takes_the_core_keys_and_converts_what_needs_it():
    snap = snapshot_of(
        reading("cpu.temp", 61.0, "°C"),
        reading("gpu.temp", 44.0, "°C", origin="lhm:/gpu-nvidia/0/temperature/0"),
        reading("cpu.power", 88.0, "W"),
        reading("gpu.load", 99.0, "%"),
        reading("cpu.freq", 4500.0, "MHz"),
        reading("mem.used", 15.5, "GiB"),
        reading("gpu.mem.used", 2048.0, "MB"),  # LibreHardwareMonitor: megabytes
        reading("net.down", 1.5e6, "B/s"),
        reading("weather.temperature", 59.0, "°F"),  # imperial weather
        reading("audio.volume", 30.0, "%"),
    )
    s = Reader(Settings()).read(snap, T0)
    assert s.values["cpu_temp"] == 61.0 and s.values["cpu_power"] == 88.0
    assert s.values["gpu_load"] == 99.0 and s.values["cpu_clock"] == 4500.0
    assert s.values["vram_used"] == 2.0 and s.values["mem_used"] == 15.5
    assert s.values["net_down"] == 1.5e6 and s.values["volume"] == 30.0
    assert s.values["cpu_load"] is None and s.values["gpu_hotspot"] is None  # not there: none
    assert s.outside == pytest.approx(15.0)
    assert s.game is None and s.fps is None and s.music is False


def test_the_reader_only_takes_numbers_for_numbers():
    snap = snapshot_of(
        reading("cpu.temp", "hot"), reading("gpu.temp", float("nan")), reading("cpu.power", True)
    )
    s = Reader(Settings()).read(snap, T0)
    assert [s.values[k] for k in ("cpu_temp", "gpu_temp", "cpu_power")] == [None] * 3


def test_the_hot_spot_is_that_of_the_card_gpu_temp_comes_from():
    snap = snapshot_of(
        reading("gpu.temp", 50.0, "°C", origin="lhm:/gpu-amd/2/temperature/0"),
        reading(
            "temp.gpu1.gpu_hot_spot", 99.0, "°C", "GPU Hot Spot", "lhm:/gpu-amd/0/temperature/2"
        ),
        reading(
            "temp.gpu2.gpu_hot_spot", 61.0, "°C", "GPU Hot Spot", "lhm:/gpu-amd/2/temperature/2"
        ),
        reading("temp.gpu2.gpu_core", 50.0, "°C", "GPU Core", "lhm:/gpu-amd/2/temperature/0"),
    )
    reader = Reader(Settings())
    assert reader.read(snap, T0).values["gpu_hotspot"] == 61.0
    alone = snapshot_of(reading("temp.gpu.hot_spot", 70.0, "°C", "GPU Hot Spot"))
    assert Reader(Settings()).read(alone, T0).values["gpu_hotspot"] == 70.0
    junction = snapshot_of(reading("temp.gpu.junction", 80.0, "°C", "GPU Junction"))
    assert Reader(Settings()).read(junction, T0).values["gpu_hotspot"] == 80.0
    gone = snapshot_of(reading("cpu.temp", 50.0))
    assert reader.read(gone, T0).values["gpu_hotspot"] is None  # no stale value, no error


def test_a_fan_has_its_speed_its_duty_and_the_name_fancontrol_gave_it():
    snap = snapshot_of(
        reading("fan.lpc.fan_1", 900.0, "RPM", "Fan #1", "lhm:/lpc/chip/fan/1"),
        reading("lhm:/lpc/chip/control/1", 40.0, "%"),
        reading("fan.lpc.fan_2", 700.0, "RPM", "Fan #2", "lhm:/lpc/chip/fan/2"),
        reading("fan.lpc.fan_3", 0.0, "RPM", "Fan #3", "lhm:/lpc/chip/fan/3"),
        reading("fan.gpu.gpu_fan_1", 0.0, "RPM", "GPU Fan 1", "lhm:/gpu-nvidia/0/fan/1"),
        reading("lhm:/gpu-nvidia/0/control/1", 0.0, "%"),
        reading("fan.cpu", 1100.0, "RPM", "CPU fan"),  # no LibreHardwareMonitor behind it
        reading("fan.nothing", None, "RPM", "No value"),
    )
    reader = Reader(Settings())
    fans = reader.read(snap, T0).fans
    assert fans == {
        "Fan #1": (900.0, 40.0),
        "Fan #2": (700.0, None),
        "Fan #3": (0.0, None),  # a stopped fan is a value
        "GPU Fan 1": (0.0, 0.0),
        "CPU fan": (1100.0, None),
    }
    reader.names = {"/lpc/chip/fan/1": "Rear", "/lpc/chip/fan/2": "Rear"}  # one name for two
    reader.hidden = {"/lpc/chip/fan/3"}  # a connector with nothing on it
    fans = reader.read(snap, T0).fans
    assert fans["Rear"] == (900.0, 40.0) and "fan.lpc.fan_2" in fans and "Fan #3" not in fans


def test_more_temperatures_and_what_the_curves_read_are_taken_when_asked_for():
    snap = snapshot_of(
        reading("temp.board.system", 33.0, "°C"),
        reading("temp.nvme.composite", 41.0, "°C"),
        reading("temp.nvme.sensor_2", 44.0, "°C"),
        reading("temp.nvme.broken", None, "°C"),
        reading("lhm:/lpc/chip/temperature/3", 36.0, "°C"),
    )
    reader = Reader(Settings(temperatures=("temp.board.system", "temp.missing", "temp.nvme.*")))
    reader.sources = {"Board_Fan": "/lpc/chip/temperature/3", "Lost": "/nowhere/0"}
    s = reader.read(snap, T0)
    assert s.temps == {
        "temp.board.system": 33.0,
        "temp.nvme.composite": 41.0,
        "temp.nvme.sensor_2": 44.0,
    }  # a name, or a pattern; what has no value is left out
    assert s.sources == {"Board_Fan": 36.0}


def test_the_names_of_the_hardware_and_the_sensors_are_noted_as_the_readings_give_them():
    snap = snapshot_of(
        reading("cpu.name", "8-Core Processor"),
        reading("gpu.name", "Graphics Card"),
        reading("cpu.temp", 61.0, "°C", "CPU", "lhm:/amdcpu/0/temperature/2"),
        reading("gpu.temp", 44.0, "°C", "GPU", "lhm:/gpu-amd/5/temperature/0"),
        reading("temp.cpu.core", 61.0, "°C", "Core (Tctl/Tdie)", "lhm:/amdcpu/0/temperature/2"),
        reading("temp.gpu.gpu_core", 44.0, "°C", "GPU Core", "lhm:/gpu-amd/5/temperature/0"),
        reading("temp.gpu.hot_spot", 58.0, "°C", "GPU Hot Spot", "lhm:/gpu-amd/5/temperature/2"),
        reading("temp.board.system", 33.0, "°C", "System", "lhm:/lpc/chip/temperature/0"),
        reading("temp.other", 40.0, "°C", "Sensor"),  # not from LibreHardwareMonitor: no id
        reading("temp.nvme.broken", None, "°C", "No value", "lhm:/nvme/0/temperature/0"),
        reading("fan.lpc.fan_1", 900.0, "RPM", "Fan #1", "lhm:/lpc/chip/fan/1"),
    )
    found = Reader(Settings()).sensors(snap.readings)
    assert found["cpu"] == "8-Core Processor" and found["gpu"] == "Graphics Card"
    assert found["sensors"] == [
        {"key": "temp.board.system", "id": "/lpc/chip/temperature/0", "label": "System",
         "panel": None},
        {"key": "temp.cpu.core", "id": "/amdcpu/0/temperature/2", "label": "Core (Tctl/Tdie)",
         "panel": "cpu_temp"},
        {"key": "temp.gpu.gpu_core", "id": "/gpu-amd/5/temperature/0", "label": "GPU Core",
         "panel": "gpu_temp"},
        {"key": "temp.gpu.hot_spot", "id": "/gpu-amd/5/temperature/2", "label": "GPU Hot Spot",
         "panel": "gpu_hotspot"},
        {"key": "temp.other", "id": None, "label": "Sensor", "panel": None},
    ]  # fmt: skip
    nameless = Reader(Settings()).sensors(snapshot_of(reading("cpu.name", 5)).readings)
    assert nameless == {"cpu": None, "gpu": None, "sensors": []}


def test_the_game_comes_from_the_game_service_and_the_rates_from_presentmon():
    snap = snapshot_of(
        reading("game.name", "Space Game"),
        reading("game.app", "SpaceGame.EXE"),
        reading("game.fps", 143.5, "fps"),
        reading("game.low", 101.0, "fps"),
        reading("game.frametime", 6.9, "ms"),
        reading("game.limiter", "gpu"),
    )
    s = Reader(Settings()).read(snap, T0)
    assert (s.game, s.program, s.fps, s.low, s.frametime, s.limiter) == (
        "Space Game", "spacegame", 143.5, 101.0, 6.9, "gpu",
    )  # fmt: skip
    odd = snapshot_of(reading("game.name", ""), reading("game.fps", 100.0))
    assert Reader(Settings()).read(odd, T0).game is None  # no game: rates are none of ours
    other = snapshot_of(reading("game.name", "G"), reading("game.limiter", "mystery"))
    assert Reader(Settings()).read(other, T0).limiter is None


def test_music_is_a_state_and_can_be_left_out():
    playing = snapshot_of(reading("media.state", "Playing"))
    assert Reader(Settings()).read(playing, T0).music is True
    assert Reader(Settings(music=False)).read(playing, T0).music is False
    assert Reader(Settings()).read(snapshot_of(reading("media.playing", 1)), T0).music is True


def test_the_options_are_checked():
    problems = []
    options = {key: default for key, (_kind, default) in memory.Memory.options.items()}
    options.update(fps_levels=[60, "x", -5, 60, 120], temperatures=["temp.a", 5, " "])
    settings = Settings.from_options(options, problems.append)
    assert settings.fps_levels == (60.0, 120.0) and settings.temperatures == ("temp.a",)
    assert len(problems) == 2
    options.update(fps_levels=[], report_minutes=-5, report_seconds=0, report_compare=0)
    settings = Settings.from_options(options, problems.append)
    assert settings.fps_levels == memory.DEFAULT_LEVELS
    assert (settings.report_min_s, settings.report_seconds, settings.report_compare) == (0, 1, 1)


def test_two_samples_at_the_same_second_are_one_and_a_pause_starts_the_grid_afresh(rig):
    r = rig()
    recorder = r.recorder
    for at in (T0, T0 + 0.3, T0 + 0.9, T0 + 1.0, T0 + 1.2, T0 + 2.05):
        recorder.add(sample(at, cpu_temp=50.0))
    assert recorder._minute.n == 3  # the first, and one a second after it
    recorder.add(sample(T0 + 40.0, cpu_temp=50.0))  # after a pause: counts, no catching up
    recorder.add(sample(T0 + 40.5, cpu_temp=50.0))
    assert recorder._minute.n == 4
    assert recorder.due(T0 + 41.0) and not recorder.due(T0 + 40.9)


# -- the service ----------------------------------------------------------------------------------

from libre_panel.config import Config, ServicesConfig  # noqa: E402
from libre_panel.plugins import PluginHost, ServiceManager  # noqa: E402
from libre_panel.sensors import base  # noqa: E402

SESSION_KEYS = {
    "id", "game", "program", "started", "ended", "duration_s", "fps_s", "fps_mean", "fps_p01",
    "fps_low_mean", "frametime_mean", "levels", "limit_cpu_s", "limit_gpu_s", "limit_cap_s",
    "cpu_temp_max", "gpu_temp_max", "gpu_hotspot_max", "cpu_power_max", "gpu_power_max",
    "vram_used_max", "outside_temp", "closed",
}  # fmt: skip


class Clock:
    def __init__(self):
        self.now = float(T0)

    def __call__(self):
        return self.now


def readings_of(**given):
    """Readings by name; a dot in a key is written as a double underscore (cpu__temp)."""
    return {key.replace("__", "."): Reading(key.replace("__", "."), v) for key, v in given.items()}


def in_game(fps=120.0, name="Space Game"):
    return {
        "game.name": Reading("game.name", name),
        "game.app": Reading("game.app", "SpaceGame"),
        "game.fps": Reading("game.fps", fps, "fps"),
        "game.low": Reading("game.low", 0.7 * fps, "fps"),
        "game.frametime": Reading("game.frametime", 1000 / fps, "ms"),
        "game.limiter": Reading("game.limiter", "gpu"),
    }


class Desk:
    """A host with the memory service running, and the clocks by hand."""

    def __init__(self, **options):
        self.host = PluginHost()
        self.manager = ServiceManager(self.host)
        self.wall, self.mono = Clock(), Clock()
        options = {"report_minutes": 1, "report_transition": "cut", **options}
        config = Config(services=ServicesConfig(["memory"], {"memory": options}))
        self.manager.apply(config)
        assert self.manager.state == {"memory": "running"}, self.manager.state
        self.service = self.manager.running["memory"][0]
        self.service.wall, self.service.clock = self.wall, self.mono

    def sample(self, seconds=1.0, **found):
        """Time passes and the main loop takes the next snapshot, with these readings."""
        self.wall.now += seconds
        self.mono.now += seconds
        self.host.emit("sample", wait=True, snapshot=Snapshot(readings=found))

    def play(self, seconds, fps=120.0, **found):
        for _ in range(int(seconds)):
            self.sample(**{**readings_of(**found), **in_game(fps)})
        self.sample()  # the game is gone

    def settle(self):
        done = threading.Event()
        self.service.store.run(lambda con: done.set())
        assert done.wait(10)

    def state(self):
        return Snapshot(readings=self.host.published()).state("report")

    def database(self):
        self.manager.stop()  # writes what is left
        return sqlite3.connect(self.service.database)

    def close(self):
        self.manager.stop()
        self.host.close()


@pytest.fixture
def desk():
    made = []

    def make(**options):
        made.append(Desk(**options))
        return made[-1]

    yield make
    for each in made:
        each.close()


def test_a_game_that_ends_brings_the_report_and_its_theme(desk):
    d = desk()
    d.host.request_theme("studio", by="autopilot", priority=0, mode=None)
    assert d.host.theme == "studio" and d.state() is None
    d.play(90, cpu__temp=70.0)
    assert wait_for(lambda: d.host.theme == "round-report")  # ahead of the autopilot's 0
    assert d.host.take_transition() == "cut"
    report = d.state()
    assert set(report) == {"session", "comparison", "seconds", "until"}
    assert set(report["session"]) == SESSION_KEYS
    assert set(report["comparison"]) == {"count", "mean", "levels"}
    assert report["seconds"] == 30 and d.wall.now < report["until"] <= d.wall.now + 30
    session = report["session"]
    assert (session["game"], session["program"], session["duration_s"]) == (
        "Space Game",
        "spacegame",
        90,
    )
    assert session["fps_mean"] == pytest.approx(120.0) and session["cpu_temp_max"] == 70.0
    assert report["comparison"]["count"] == 0
    published = d.host.published()
    assert published["report.game"].value == "Space Game"
    assert published["report.fps"].value == pytest.approx(120.0)
    assert published["report.fps"].unit == "fps" and published["report.fps"].label == "Average FPS"
    assert published["report.versus"].value == "First round of this game"
    assert published["report.fps_delta"].value is None  # nothing to be against
    assert (
        published["report.level.3"].label == "120–144"
        and published["report.level.3"].value == 100.0
    )
    assert published["report.cpu_temp"].value == 70.0 and published["report.cpu_temp"].unit == "°C"
    assert published["report.head.peaks"].value == "Highest values of the round"
    assert published["report.limit"].value == "GPU 100 %"
    assert d.host.published()["report"].label == "Round report"


def test_the_texts_of_the_report_are_in_the_language_of_the_panel(desk):
    from libre_panel import i18n

    i18n.set_language("de")
    d = desk()
    d.play(90)
    assert wait_for(lambda: d.host.theme == "round-report")
    published = d.host.published()
    assert published["report.title"].value == "Rundenbericht"
    assert published["report.fps"].label == "Mittlere FPS"
    assert published["report.versus"].value == "Erste Runde dieses Spiels"
    assert published["report.cpu_temp"].label == "CPU-Temp."


def test_the_next_report_is_against_the_last_rounds(desk):
    d = desk()
    d.play(90, fps=100.0)
    assert wait_for(lambda: d.host.theme == "round-report")
    d.sample(seconds=40)  # the 30 s are over
    assert d.host.theme is None
    d.play(90, fps=130.0)
    assert wait_for(lambda: d.state() is not None and d.state()["comparison"]["count"] == 1)
    published = d.host.published()
    assert published["report.versus"].value == "Compared with the last round"
    assert published["report.fps_delta"].value == pytest.approx(30.0)
    d.sample(seconds=40)
    d.play(90, fps=130.0)
    assert wait_for(lambda: d.state() is not None and d.state()["comparison"]["count"] == 2)
    assert d.host.published()["report.versus"].value == "Compared with the last 2 rounds"


def test_the_report_goes_when_its_seconds_are_over(desk):
    d = desk(report_seconds=20)
    d.host.request_theme("studio", by="autopilot", priority=0, mode=None)
    d.play(90)
    assert wait_for(lambda: d.host.theme == "round-report")
    d.sample(seconds=15)
    assert d.host.theme == "round-report" and d.state() is not None
    d.sample(seconds=6)
    assert d.host.theme == "studio" and d.state() is None  # the state goes with the theme
    assert d.host.published()["report.game"].value == "Space Game"  # the theme is still leaving
    d.sample(seconds=2)
    assert "report.game" in d.host.published()
    d.sample(seconds=2)  # the grace is over
    assert not [key for key in d.host.published() if key.startswith("report.")]


def test_a_new_game_ends_the_report_at_once_and_it_does_not_come_back(desk):
    d = desk()
    d.host.request_theme("studio", by="autopilot", priority=0, mode=None)
    d.play(90)
    assert wait_for(lambda: d.host.theme == "round-report")
    d.host.set_mode("game", by="game")  # the next game: the mode comes before anything else
    assert wait_for(lambda: d.state() is None)
    d.sample(seconds=4)  # the readings stay a moment while the theme leaves
    assert not [key for key in d.host.published() if key.startswith("report.")]
    d.host.set_mode(None, by="game")
    assert d.host.theme == "studio"  # the autopilot's choice, not a report from before


def test_a_report_that_is_built_after_the_next_game_began_is_not_shown(desk):
    d = desk()
    gate, held = threading.Event(), threading.Event()
    d.service.store.run(lambda con: (held.set(), gate.wait(10)))
    assert held.wait(10)  # the writer is busy: the report waits behind it
    d.play(90)
    d.host.set_mode("game", by="game")
    assert wait_for(lambda: d.service._games == 1)
    d.host.set_mode(None, by="game")
    gate.set()
    d.settle()
    assert d.host.theme is None and d.state() is None  # a report of the game before the last


def test_nothing_is_shown_while_a_mode_is_on(desk):
    d = desk()
    d.host.set_mode("game", by="game")
    assert wait_for(lambda: d.service._games == 1)
    d.play(90)
    d.settle()
    assert d.host.theme is None and d.state() is None


def test_a_second_report_only_replaces_the_content_of_the_first(desk):
    d = desk(report_seconds=3600)  # still there when the next game ends
    asked = []
    show = d.service.host.show_theme
    d.service.host.show_theme = lambda *args, **kwargs: (asked.append(args), show(*args, **kwargs))
    d.play(90, fps=100.0)
    assert wait_for(lambda: d.host.theme == "round-report")
    d.play(90, fps=130.0)  # the next game, while the report of the first is still there
    assert wait_for(lambda: d.state() is not None and d.state()["comparison"]["count"] == 1)
    assert len(asked) == 1 and d.state()["session"]["fps_mean"] == pytest.approx(130.0)


def test_the_report_can_be_asked_for_again(desk):
    d = desk()
    assert d.service.show_last_report() is True  # nothing played yet: nothing shown
    d.settle()
    assert d.host.theme is None
    d.play(90, fps=111.0)
    assert wait_for(lambda: d.host.theme == "round-report")
    d.sample(seconds=40)
    assert d.host.theme is None
    assert d.service.show_last_report() is True
    assert wait_for(lambda: d.host.theme == "round-report")
    assert d.state()["session"]["fps_mean"] == pytest.approx(111.0)


def test_the_report_can_be_left_out_or_shown_by_a_screen_of_its_own(desk):
    off = desk(report=False)
    off.play(90)
    off.settle()
    assert off.host.theme is None and off.state() is None
    own = desk(report_theme="")  # a screen that draws the state itself needs no theme
    own.play(90)
    assert wait_for(lambda: own.state() is not None)
    assert own.host.theme is None
    own.sample(seconds=40)
    assert own.state() is None


def test_a_game_too_short_or_without_frame_rates_has_no_report(desk):
    d = desk()
    d.play(30)
    d.settle()
    for _ in range(90):  # a game without PresentMon
        d.sample(**{"game.name": Reading("game.name", "Space Game")})
    d.sample()
    d.settle()
    assert d.host.theme is None and d.state() is None


def test_the_service_asks_the_sources_for_what_it_reads_and_only_for_that(desk):
    d = desk()
    for key in ("cpu.temp", "gpu.power", "fan.gpu.gpu_fan_1", "game.fps", "media.state"):
        assert base.wanted(key), key
    assert not base.wanted("media.title") and not base.wanted("media.cover")
    d.manager.stop()
    assert not base.wanted("cpu.temp") and not base.wanted("media.state")  # and no longer
    quiet = desk(music=False)
    assert not base.wanted("media.state") and base.wanted("game.fps")
    quiet.manager.stop()


def test_the_service_writes_its_database_in_its_own_folder_or_where_it_is_told(desk, tmp_path):
    d = desk()
    assert d.service.database == d.service.host.data_dir / "memory.db"
    assert d.service.status()["database"] == str(d.service.database)
    elsewhere = tmp_path / "elsewhere" / "m.db"
    elsewhere.parent.mkdir()
    e = desk(database=str(elsewhere))
    e.sample(**readings_of(cpu__temp=50.0))
    e.manager.stop()
    assert elsewhere.is_file()


def test_the_status_says_what_the_service_is_doing(desk):
    d = desk()
    assert d.service.status() == {
        "recording": True,
        "database": str(d.service.database),
        "written": 0,
        "error": None,
        "game": None,
        "report": False,
    }
    d.sample(**in_game())
    assert d.service.status()["game"] == "Space Game"
    d.play(90)
    assert wait_for(lambda: d.service.status()["report"] is True)
    assert wait_for(lambda: d.service.status()["written"] >= 2)
    d.manager.stop()
    assert d.service.status()["recording"] is False


def test_a_game_that_is_on_when_the_service_stops_is_kept_and_closed(desk):
    d = desk()
    for _ in range(70):
        d.sample(**in_game())
    db = d.database()
    assert db.execute("SELECT game, duration_s, closed FROM sessions").fetchone() == (
        "Space Game",
        69,
        1,
    )
    assert db.execute("SELECT COUNT(*) FROM minutes").fetchone()[0] >= 1
    assert d.host.theme is None  # a game that ends with the program shows no report


def test_a_sample_that_comes_after_the_service_stopped_changes_nothing(desk):
    d = desk()
    d.sample(**readings_of(cpu__temp=50.0))
    service, d.manager.running["memory"] = d.service, d.manager.running["memory"]
    d.manager.stop()
    service.on_sample(Snapshot(readings=readings_of(cpu__temp=60.0)))  # a sample under way
    service.on_mode("game")
    assert service.status()["recording"] is False


def test_what_has_no_readings_is_not_a_failure(desk):
    d = desk()
    for _ in range(5):
        d.sample()  # no sensors at all
    db = d.database()
    assert (
        d.service.store.error is None
        and db.execute("SELECT COUNT(*) FROM minutes").fetchone()[0] == 1
    )


# -- FanControl -----------------------------------------------------------------------------------

from test_fancontrol import CONTROLS, CPU_ID, CURVES, GPU_ID, write_config  # noqa: E402


def fan_readings(duty=40.0, cpu=55.0):
    return readings_of(
        **{
            "fan.lpc.fan_1": 900.0,
            "lhm:/lpc/chip/control/1": duty,
            "fan.lpc.fan_2": 700.0,
            "fan.lpc.fan_3": 300.0,  # a connector FanControl hides
            "fan.gpu.gpu_fan_1": 0.0,
            "lhm:/gpu-nvidia/0/control/1": 0.0,
            "cpu.temp": cpu,
            f"lhm:{CPU_ID}": cpu + 1.5,  # what the curve Case_Fan reads
            f"lhm:{GPU_ID}": 38.0,
        }
    ) | {
        key: Reading(key, v, "RPM", label, origin)
        for key, v, label, origin in (
            ("fan.lpc.fan_1", 900.0, "Fan #1", "lhm:/lpc/chip/fan/1"),
            ("fan.lpc.fan_2", 700.0, "Fan #2", "lhm:/lpc/chip/fan/2"),
            ("fan.lpc.fan_3", 300.0, "Fan #3", "lhm:/lpc/chip/fan/3"),
            ("fan.gpu.gpu_fan_1", 0.0, "GPU Fan 1", "lhm:/gpu-nvidia/0/fan/1"),
        )
    }


def test_fancontrol_names_the_fans_and_says_what_each_curve_reads(desk, tmp_path):
    config = write_config(tmp_path / "userConfig.json", CURVES, CONTROLS)
    d = desk(fancontrol=str(config))
    for _ in range(30):
        d.sample(**fan_readings())
    for key in (f"lhm:{CPU_ID}", f"lhm:{GPU_ID}", "lhm:/lpc/chip/temperature/3"):
        assert base.wanted(key), key  # the sensors the curves read are read for them
    d.sample(seconds=20, **fan_readings(duty=60.0))
    db = d.database()
    rows = db.execute("SELECT fans, sources FROM cooling ORDER BY ts").fetchall()
    fans = json.loads(rows[-1][0])
    assert fans["Rear_Fan"] == [900.0, 60.0] and fans["Card Fan"] == [0.0, 0.0]
    assert fans["Front"] == [700.0, None]  # named, but LibreHardwareMonitor has no duty for it
    assert sorted(fans) == ["Card Fan", "Front", "Rear_Fan"]  # not the one FanControl hides
    assert json.loads(rows[-1][1]) == {"Case_Fan": 56.5, "Graphics": 38.0}  # the Board one: none


def test_a_changed_fancontrol_setting_is_kept_with_the_time_and_the_same_one_is_not(desk, tmp_path):
    config = write_config(tmp_path / "userConfig.json", CURVES, CONTROLS)
    d = desk(fancontrol=str(config))
    for _ in range(10):
        d.sample(**fan_readings())
    for _ in range(40):  # past the look every 30 s: the same file is not a new state
        d.sample(**fan_readings())
    first_at = d.wall.now
    write_config(config, [{**CURVES[0], "Points": ["50,25", "80,70"]}, *CURVES[1:]], CONTROLS)
    for _ in range(40):
        d.sample(**fan_readings())
    db = d.database()
    rows = db.execute("SELECT ts, fingerprint, state FROM fancontrol ORDER BY ts").fetchall()
    assert len(rows) == 2 and rows[0][1] != rows[1][1]
    assert rows[0][0] < first_at < rows[1][0]
    state = json.loads(rows[1][2])
    assert state["curves"][0]["Points"] == ["50,25", "80,70"]
    assert set(state) == {"curves", "controls"}


def test_the_state_stored_before_is_not_stored_again_after_a_restart(desk, tmp_path):
    config = write_config(tmp_path / "userConfig.json", CURVES, CONTROLS)
    first = desk(fancontrol=str(config))
    for _ in range(5):
        first.sample(**fan_readings())
    first.manager.stop()
    again = desk(fancontrol=str(config))  # the same database, the same file
    for _ in range(5):
        again.sample(**fan_readings())
    db = again.database()
    assert db.execute("SELECT COUNT(*) FROM fancontrol").fetchone() == (1,)


def test_without_fancontrol_the_fans_are_named_as_the_sensors_name_them(desk, tmp_path):
    d = desk(fancontrol=str(tmp_path / "typo.json"))
    for _ in range(5):
        d.sample(**fan_readings())
    db = d.database()
    names = set(json.loads(db.execute("SELECT fans FROM cooling").fetchone()[0]))
    assert names == {"Fan #1", "Fan #2", "Fan #3", "GPU Fan 1"}
    assert db.execute("SELECT COUNT(*) FROM fancontrol").fetchone() == (0,)


def test_the_fancontrol_states_are_not_kept_when_cooling_is_left_out(desk, tmp_path):
    config = write_config(tmp_path / "userConfig.json", CURVES, CONTROLS)
    d = desk(fancontrol=str(config), cooling=False)
    for _ in range(5):
        d.sample(**fan_readings())
    db = d.database()
    assert db.execute("SELECT COUNT(*) FROM fancontrol").fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM cooling").fetchone() == (0,)


def stored_names(d):
    """The names of the sensors in the file now (it is opened the way the cooling analysis does)."""
    d.settle()
    con = read_only(d.service.database)
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'sensors'").fetchone()
    finally:
        con.close()
    return json.loads(row[0]) if row else None


def named_readings(gpu="Graphics Card"):
    return {
        "cpu.name": Reading("cpu.name", "8-Core Processor"),
        "gpu.name": Reading("gpu.name", gpu),
        "cpu.temp": Reading("cpu.temp", 55.0, "°C", "CPU", "lhm:/amdcpu/0/temperature/2"),
        "temp.cpu.core": Reading(
            "temp.cpu.core", 55.0, "°C", "Core (Tctl/Tdie)", "lhm:/amdcpu/0/temperature/2"
        ),
    }


def test_the_names_of_the_sensors_are_kept_for_the_cooling_analysis_and_looked_at_each_minute(desk):
    d = desk()
    d.sample(**named_readings())
    assert stored_names(d) == {
        "cpu": "8-Core Processor",
        "gpu": "Graphics Card",
        "sensors": [
            {
                "key": "temp.cpu.core",
                "id": "/amdcpu/0/temperature/2",
                "label": "Core (Tctl/Tdie)",
                "panel": "cpu_temp",
            }
        ],
    }
    d.sample(**named_readings(gpu="Another Card"))  # looked at once a minute, not at every second
    assert stored_names(d)["gpu"] == "Graphics Card"
    d.sample(seconds=70, **named_readings(gpu="Another Card"))
    assert stored_names(d)["gpu"] == "Another Card"


def test_the_names_of_the_sensors_are_not_kept_when_cooling_is_left_out(desk):
    d = desk(cooling=False)
    d.sample(**named_readings())
    assert stored_names(d) is None


# -- the real main loop ---------------------------------------------------------------------------


class Ticks:
    """The service's wall clock: a second more at every sample it takes."""

    def __init__(self):
        self.now = float(T0)

    def __call__(self):
        self.now += 1.0
        return self.now


def test_a_game_is_played_and_its_report_comes_up_on_the_panel(monkeypatch, isolated_home):
    """The game service finds the game, the memory records it; when it ends the report builds and
    the main loop shows its theme with the numbers in it; the next game takes it away again."""
    from fake_system import FakeMachine, game_path, make_steam
    from test_toast_scenarios import NoWarmup, Panel, Script, write_theme

    from libre_panel import app
    from libre_panel.config import ModeConfig, SensorsConfig
    from libre_panel.render.renderer import Renderer
    from libre_panel.services import machine as computer

    for name, color in (("classic", "#102030"), ("focus", "#302010")):
        write_theme(isolated_home, name, color)
    steam = make_steam(isolated_home.parent / "Steam", ("10", "Space Game", "SpaceGame"))
    machine = FakeMachine()
    seen = []  # (theme of the frame, the game the report names, the frame rate it shows, its state)
    render = Renderer.render

    def rendered(self, snapshot, now=None):
        report = snapshot.state("report")
        seen.append(
            (
                self.theme.name,
                snapshot.value("report.game"),
                snapshot.value("report.fps"),
                report["session"]["game"] if report else None,
            )
        )
        return render(self, snapshot, now)

    monkeypatch.setattr(computer, "current", lambda: machine)
    monkeypatch.setattr(Renderer, "render", rendered)
    monkeypatch.setattr(app, "create_display", Panel)
    monkeypatch.setattr(app, "_Warmup", NoWarmup)  # frames counted: switch at once
    host = PluginHost()
    manager = ServiceManager(host)
    config = Config(
        theme="classic",
        fps=50,
        refresh_ms=100,
        transition="cut",
        sensors=SensorsConfig(providers=["demo"]),
        services=ServicesConfig(
            ["game", "memory"],
            {
                "game": {
                    "steam_path": str(steam),
                    "poll_s": 3600,
                    "linger_s": 0.0,
                    "entrance": False,
                    "logo": False,
                },
                "memory": {
                    "report_minutes": 0.05,
                    "report_seconds": 3600,
                    "report_transition": "cut",
                },
            },
        ),  # fmt: skip
        modes={"game": ModeConfig(theme="focus")},
    )
    manager.apply(config)
    assert manager.state == {"game": "running", "memory": "running"}, manager.state
    game, service = manager.running["game"][0], manager.running["memory"][0]
    ticks = Ticks()
    service.wall = ticks
    seen_at: dict = {}

    def play(pid):
        machine.start("game.exe", pid, game_path(steam, "SpaceGame"))
        machine.front = pid
        game.detect()

    def stop(pid):
        machine.end(pid)
        game.detect()

    def watch():
        """The player: a game for a few seconds of the memory's time, its report on show for a
        moment, then another game, which does not last long enough for a report."""
        if "first" not in seen_at and script.n == 20:
            seen_at["first"] = None
            play(100)
        elif seen_at.get("first") is None and service.status()["game"]:
            seen_at["first"] = ticks.now  # the memory has seen the game
        elif seen_at.get("first") and "ended" not in seen_at and ticks.now >= seen_at["first"] + 6:
            seen_at["ended"] = script.n
            seen_at["frames"] = len(seen)  # what was drawn while the first game was on
            stop(100)
        elif "ended" in seen_at and "second" not in seen_at and any(s[1] for s in seen[-3:]):
            seen_at["second"] = ticks.now  # the report has been on the panel
            play(101)
        elif "second" in seen_at and "over" not in seen_at and ticks.now >= seen_at["second"] + 2:
            seen_at["over"] = script.n
            stop(101)
        elif (
            "over" in seen_at and "done" not in seen_at and host.theme is None and host.mode is None
        ):
            seen_at["done"] = script.n
            script.length = script.n + 40  # a moment more, then the loop ends

    class Paced(Script):
        """Frames take a moment each: the samples come in time, however fast the machine is."""

        def wait(self, fps, stop):
            time.sleep(0.002)
            return super().wait(fps, stop)

    script = Paced(4000, {n: [watch] for n in range(1, 4000)})
    monkeypatch.setattr(app, "_Pacer", lambda: script)
    try:
        app.run(config, host=host)
    finally:
        manager.stop()
        host.close()

    themes = [s[0] for s in seen]
    assert {"first", "ended", "second", "over", "done"} <= set(seen_at), seen_at
    order = [name for i, name in enumerate(themes) if i == 0 or name != themes[i - 1]]
    # the game, the user's theme for a moment until the report is built (it comes with the
    # next sample), the report, then the next game and the end of it
    assert order[:2] == ["classic", "focus"] and order.count("Round report") == 1, order
    assert order[order.index("Round report") + 1 :] == ["focus", "classic"], order
    shown = [s for s in seen if s[0] == "Round report"]
    assert shown and all(s[1] == "Space Game" for s in shown)  # the theme never lacks its numbers
    assert any(s[3] == "Space Game" for s in shown)  # and the state is there while it lasts
    assert all(s[2] and s[2] > 50 for s in shown)  # the frame rate of the game, in the readings
    # none of it before the first game was over, and its state is gone at the end (the readings
    # stay a moment after the report, and come a moment before its theme: the calls are two)
    assert all(s[3] is None and s[1] is None for s in seen[: seen_at["frames"]])
    assert seen[-1][3] is None
    assert host.theme is None and host.mode is None
