"""FanControl's configuration, read only: where it is, the curves and fans in it, what counts
as a change, and the watch that notices one. The files are made up in a temp dir."""

import builtins
import json
import os

import pytest

from libre_panel.services import fancontrol as fc

CPU_ID = "/amdcpu/0/temperature/2"
GPU_ID = "/gpu-nvidia/0/temperature/0"
BOARD_ID = "/lpc/chip/temperature/3"


def curve(name, points=(), source=None, mix=(), **more):
    raw = {"Name": name, "Points": list(points), **more}
    if source:
        raw["SelectedTempSource"] = {"Identifier": source, "Name": "Sensor"}
    if mix:
        raw["SelectedFanCurves"] = [{"Name": part} for part in mix]
    return raw


def control(nick, identifier, curve_name=None, enable=True, hidden=False, **more):
    raw = {
        "NickName": nick,
        "Identifier": identifier,
        "Enable": enable,
        "IsHidden": hidden,
        "SelectedFanCurve": {"Name": curve_name} if curve_name else None,
        "MinimumPercent": 20,
        "WindowLeft": 120,  # the program's own business, not the cooling's
        **more,
    }
    return raw


def write_config(path, curves, controls, bom=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"FanControl": {"FanCurves": curves, "Controls": controls}})
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")
    return path


CURVES = [
    curve("Case_Fan", ["62,30", "50,22", "72,42", "82,62"], CPU_ID, HysteresisConfig={"Up": 2}),
    curve("Graphics", ["45,22", "70,85"], GPU_ID),
    curve("Both", mix=["Case_Fan", "Graphics"], SelectedMixFunction=0),
    curve("Board", ["30,20", "50,60"], BOARD_ID),
]
CONTROLS = [
    control("Rear_Fan", "/lpc/chip/control/1", "Case_Fan"),
    control("Front", "/lpc/chip/control/2", "Both"),
    control("Card Fan", "/gpu-nvidia/0/control/1", "Graphics"),
    control("Unused", "/lpc/chip/control/3", "Case_Fan", enable=False, hidden=True),
    control("No curve", "/lpc/chip/control/4"),
]


# -- where it is ----------------------------------------------------------------------------------


def test_the_usual_places_come_from_the_environment_not_from_a_drive_letter(tmp_path):
    env = {
        "ProgramFiles(x86)": str(tmp_path / "pf86"),
        "ProgramFiles": str(tmp_path / "pf"),
        "LOCALAPPDATA": str(tmp_path / "local"),
    }
    places = fc.default_paths(env, home=tmp_path / "me")
    assert [p.parts[-4:-2] for p in places] == [
        ("pf86", "FanControl"),
        ("pf", "FanControl"),
        ("local", "FanControl"),
        ("Documents", "FanControl"),
    ]
    assert all(p.name == "userConfig.json" and p.parent.name == "Configurations" for p in places)
    assert len(fc.default_paths({}, home=tmp_path)) == 1  # a system with none of them: Documents


def test_the_first_place_that_has_a_file_is_the_one_and_a_given_path_wins(tmp_path):
    env = {"ProgramFiles": str(tmp_path / "pf"), "LOCALAPPDATA": str(tmp_path / "local")}
    assert fc.find(env=env) is None  # not installed
    later = write_config(
        tmp_path / "local" / "FanControl" / "Configurations" / fc.CONFIG_NAME, [], []
    )
    assert fc.find(env=env) == later
    first = write_config(tmp_path / "pf" / "FanControl" / "Configurations" / fc.CONFIG_NAME, [], [])
    assert fc.find(env=env) == first
    mine = write_config(tmp_path / "mine.json", [], [])
    assert fc.find(str(mine), env=env) == mine
    assert fc.find(str(tmp_path / "nothing.json"), env=env) is None  # no fallback behind a typo


# -- reading --------------------------------------------------------------------------------------


@pytest.mark.parametrize("bom", [True, False])
def test_the_file_is_read_with_or_without_the_marker_windows_writes(tmp_path, bom):
    path = write_config(tmp_path / "c.json", CURVES, CONTROLS, bom=bom)
    section = fc.read_section(path)
    assert [c["Name"] for c in section["FanCurves"]] == ["Case_Fan", "Graphics", "Both", "Board"]


@pytest.mark.parametrize(
    "text",
    ["", "{", "[]", '{"Other": {}}', '{"FanControl": []}', '{"FanControl": 5}'],
)
def test_a_file_that_is_not_what_we_expect_is_no_configuration(tmp_path, text):
    path = tmp_path / "c.json"
    path.write_text(text, encoding="utf-8")
    assert fc.read_section(path) is None
    assert fc.read_section(tmp_path / "gone.json") is None


def test_the_file_is_only_ever_opened_for_reading(tmp_path, monkeypatch):
    path = write_config(tmp_path / "c.json", CURVES, CONTROLS)
    before = (path.read_bytes(), os.stat(path).st_mtime_ns)
    opened = []
    real = builtins.open

    def spy(file, mode="r", *args, **kwargs):
        opened.append((str(file), mode))
        return real(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    watch = fc.Watch(str(path))
    assert watch.poll(0.0) is not None
    assert opened == [(str(path), "r")]  # and nothing else of the file, ever
    monkeypatch.undo()
    assert (path.read_bytes(), os.stat(path).st_mtime_ns) == before


# -- the state ------------------------------------------------------------------------------------


def test_the_state_holds_what_cools_and_a_moved_window_is_no_change():
    section = {"FanCurves": CURVES, "Controls": CONTROLS, "WindowSize": [1, 2]}
    state = fc.state_of(section)
    assert set(state) == {"curves", "controls"} and len(state["curves"]) == 4
    assert "WindowLeft" not in state["controls"][0] and "IsHidden" not in state["controls"][0]
    moved = [{**c, "WindowLeft": 999, "IsHidden": not c["IsHidden"]} for c in CONTROLS]
    assert fc.fingerprint(fc.state_of({"FanCurves": CURVES, "Controls": moved})) == fc.fingerprint(
        state
    )
    other = [{**CURVES[0], "Points": ["62,31"]}, *CURVES[1:]]
    assert fc.fingerprint(
        fc.state_of({"FanCurves": other, "Controls": CONTROLS})
    ) != fc.fingerprint(state)
    assert len(fc.fingerprint(state)) == 16


def test_junk_in_the_lists_is_left_out():
    state = fc.state_of({"FanCurves": [5, {"Name": "A"}], "Controls": "nope"})
    assert state == {"curves": [{"Name": "A"}], "controls": []}
    assert fc.curves_and_fans({}) == ({}, {})


def test_curves_know_their_points_sensor_mix_and_hysteresis():
    curves, _ = fc.curves_and_fans(fc.state_of({"FanCurves": CURVES, "Controls": []}))
    case = curves["Case_Fan"]
    assert case.points == ((50.0, 22.0), (62.0, 30.0), (72.0, 42.0), (82.0, 62.0))  # by temperature
    assert case.source == CPU_ID and case.mix == () and case.hysteresis == {"Up": 2}
    assert curves["Both"].mix == ("Case_Fan", "Graphics") and curves["Both"].source is None
    assert [curves[n].device for n in ("Case_Fan", "Graphics", "Both", "Board")] == [
        "cpu",
        "gpu",
        "mix",
        "other",
    ]
    assert fc.Curve("x", source="/intelcpu/0/temperature/0").device == "cpu"
    assert fc.Curve("x").device == "other"


def test_a_point_that_is_no_pair_is_left_out_and_the_others_still_count():
    curves, _ = fc.curves_and_fans(
        {"curves": [curve("A", ["40,20", "oops", "60", "x,y", "80,90"])], "controls": []}
    )
    assert curves["A"].points == ((40.0, 20.0), (80.0, 90.0))


def test_fans_are_the_enabled_outputs_with_a_curve_and_know_their_speed_sensor():
    _, fans = fc.curves_and_fans(fc.state_of({"FanCurves": CURVES, "Controls": CONTROLS}))
    assert sorted(fans) == [
        "Card Fan",
        "Front",
        "Rear_Fan",
    ]  # not the disabled, not the one without
    assert fans["Rear_Fan"] == fc.Fan("Rear_Fan", "Case_Fan", 20.0, "/lpc/chip/fan/1")
    assert fans["Card Fan"].sensor == "/gpu-nvidia/0/fan/1"
    assert fc.fan_sensor("/x/y/other/1") is None and fc.fan_sensor(None) is None


def test_the_names_the_user_gave_and_the_fans_hidden_in_fancontrol():
    names, hidden = fc.names_and_hidden({"Controls": CONTROLS})
    assert names["/lpc/chip/fan/1"] == "Rear_Fan" and names["/gpu-nvidia/0/fan/1"] == "Card Fan"
    assert hidden == {"/lpc/chip/fan/3"}
    nameless = {"Controls": [control("", "/lpc/chip/control/1"), control("A", "oddly named")]}
    assert fc.names_and_hidden(nameless) == ({}, set())


# -- the curve ------------------------------------------------------------------------------------

LINE = fc.Curve("c", ((50.0, 22.0), (62.0, 30.0), (72.0, 42.0), (82.0, 62.0)))


def test_a_curve_gives_its_duty_and_the_temperature_that_made_it():
    assert LINE.duty_at(57.0) == pytest.approx(26.667, abs=0.001)
    assert LINE.duty_at(20.0) == 22.0 and LINE.duty_at(99.0) == 62.0  # flat before and after
    assert LINE.temp_at(26.667) == pytest.approx(57.0, abs=0.02)
    assert LINE.temp_at(22.0) is None and LINE.temp_at(62.0) is None  # the flat ends tell nothing
    assert (
        fc.Curve("empty").duty_at(50.0) == 0.0
        and fc.Curve("one", ((50.0, 20.0),)).temp_at(30.0) is None
    )
    steep = fc.Curve("s", ((50.0, 20.0), (50.0, 80.0)))  # two points at one temperature
    assert steep.duty_at(50.0) == 20.0 and steep.duty_at(51.0) == 80.0


# -- what changed ---------------------------------------------------------------------------------


def test_changes_say_what_differs_between_two_states():
    old = fc.state_of({"FanCurves": CURVES, "Controls": CONTROLS})
    assert fc.changes(old, old) == []
    new_curves = [
        # points and sensor
        curve(
            "Case_Fan", ["62,30", "50,22", "72,42", "90,100"], GPU_ID, HysteresisConfig={"Up": 2}
        ),
        CURVES[1],
        {**CURVES[2], "SelectedMixFunction": 1},
        {**CURVES[3], "HysteresisConfig": {"Up": 5}},
        curve("New", ["30,30"]),
    ]
    new_controls = [{**CONTROLS[0], "MinimumPercent": 30}, *CONTROLS[1:]]
    new = fc.state_of({"FanCurves": new_curves, "Controls": new_controls})
    found = fc.changes(old, new)
    assert [(c["kind"], c.get("curve") or c.get("fan")) for c in found] == [
        ("hysteresis", "Board"),  # by name, a curve at a time
        ("mix-function", "Both"),
        ("source", "Case_Fan"),
        ("points", "Case_Fan"),
        ("curve-added", "New"),
        ("fan", "Rear_Fan"),
    ]
    source = next(c for c in found if c["kind"] == "source")
    assert (source["old"], source["new"]) == (CPU_ID, GPU_ID)
    assert next(c for c in found if c["kind"] == "fan")["fields"] == ["MinimumPercent"]
    gone = fc.state_of({"FanCurves": CURVES[:2], "Controls": CONTROLS})
    assert [c["curve"] for c in fc.changes(old, gone) if c["kind"] == "curve-removed"] == [
        "Board",
        "Both",
    ]


# -- the watch ------------------------------------------------------------------------------------


def test_the_watch_looks_now_and_then_and_reads_only_what_changed(tmp_path):
    path = write_config(tmp_path / "c.json", CURVES, CONTROLS)
    reads = []

    def reader(found):
        reads.append(found)
        return fc.read_section(found)

    watch = fc.Watch(str(path), every_s=30.0, reader=reader)
    assert watch.poll(0.0)["FanCurves"][0]["Name"] == "Case_Fan"  # the first look finds it
    assert watch.poll(10.0) is None and watch.poll(31.0) is None  # nothing changed: not read again
    assert len(reads) == 1
    write_config(path, CURVES[:2], CONTROLS)
    assert watch.poll(40.0) is None  # not yet: 30 s since the last look
    section = watch.poll(62.0)
    assert [c["Name"] for c in section["FanCurves"]] == ["Case_Fan", "Graphics"]
    assert len(reads) == 2


def test_a_file_that_cannot_be_read_just_then_is_tried_again(tmp_path):
    path = write_config(tmp_path / "c.json", CURVES, CONTROLS)
    good = path.read_text(encoding="utf-8-sig")
    path.write_text('{"FanControl": {"FanCur', encoding="utf-8")  # FanControl is writing it
    watch = fc.Watch(str(path), every_s=10.0)
    assert watch.poll(0.0) is None
    path.write_text(good, encoding="utf-8")
    assert watch.poll(11.0) is not None  # the same stamp, but it was never read


def test_without_the_program_the_watch_has_nothing_and_a_typo_is_said_once(tmp_path, caplog):
    assert fc.Watch("", finder=lambda given: None).poll(0.0) is None
    watch = fc.Watch(str(tmp_path / "typo.json"), every_s=1.0)
    with caplog.at_level("WARNING", logger="libre_panel.services.fancontrol"):
        for t in range(5):
            assert watch.poll(float(t) * 2) is None
    assert caplog.text.count("typo.json") == 1
    assert watch.stamp() is None
