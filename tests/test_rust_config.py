"""rust/config against config.py (docs/RUST.md, R9): config.toml as parse_config and
load_config make a Config of it, field by field and error by error; each further panel's own
Config (for_panel); the shortcuts as hotkeys.py reads them and the times of day."""

from __future__ import annotations

import dataclasses
import random
import struct
import sys
import tomllib
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from rust_check import run

from libre_panel import config, hotkeys, i18n

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 12), reason="the release's tomllib is Python 3.12's"
)


def _digits(value: int) -> str:
    """An int of any size in decimal (str() refuses the largest)."""
    sign, value = ("-", -value) if value < 0 else ("", value)
    parts = []
    while value >= 10**4000:
        value, low = divmod(value, 10**4000)
        parts.append(f"{low:04000d}")
    return sign + "".join(reversed([*parts, str(value)]))


def _plain(value: object) -> Any:
    """A Config, or anything in it, as lp-check writes its own: nothing lost."""
    if value is None:
        return None
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = dataclasses.fields(value)
        return ["obj", [[field.name, _plain(getattr(value, field.name))] for field in fields]]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", _digits(value)]
    if isinstance(value, float):
        return ["float", struct.pack(">d", value).hex()]
    if isinstance(value, str | Path):
        return ["str", str(value)]
    if isinstance(value, list | tuple):
        return ["list", [_plain(item) for item in value]]
    if isinstance(value, dict):
        return ["dict", [[key, _plain(item)] for key, item in value.items()]]
    if isinstance(value, datetime):
        offset = value.utcoffset()
        minutes = None if offset is None else offset // timedelta(minutes=1)
        return ["datetime", value.year, value.month, value.day, value.hour, value.minute,
                value.second, value.microsecond, minutes]  # fmt: skip
    if isinstance(value, date):
        return ["date", value.year, value.month, value.day]
    assert isinstance(value, time) and value.tzinfo is None, value
    return ["time", value.hour, value.minute, value.second, value.microsecond]


def _outcome(read: Any) -> tuple[dict[str, Any], int]:
    """What ``read()`` makes, as lp-check answers; and how deep a tomllib.loads() called by
    ``read`` itself is."""
    frame, depth = sys._getframe(), 1
    while (frame := frame.f_back) is not None:  # type: ignore[assignment]
        depth += 1
    try:
        made = read()
    except Exception as error:
        return {"error": {"type": type(error).__name__, "msg": str(error)}}, depth + 2
    panels = [_plain(made.for_panel(n)) for n in range(len(made.panels))]
    return {"ok": _plain(made), "panels": panels}, depth + 2


def _parsed(text: str) -> tuple[dict[str, Any], int]:
    return _outcome(lambda: config.parse_config(tomllib.loads(text)))


def _loaded(path: Path) -> tuple[dict[str, Any], int]:
    made, frame = _outcome(lambda: config.load_config(path))
    return made, frame + 1


def _case(platform: str, language: str, **more: Any) -> dict[str, Any]:
    return {"op": "config", "platform": platform, "language": language,
            "locale": str(i18n.LOCALE_DIR), **more}  # fmt: skip


def _same(texts: list[str], platform: str = "linux", language: str = "en") -> list[dict[str, Any]]:
    """The texts read by both; what Python made of each."""
    answers = [_parsed(text) for text in texts]
    frames = {frame for _, frame in answers}
    assert len(frames) == 1
    wanted = [answer for answer, _ in answers]
    for start in range(0, len(texts), 500):
        part = texts[start : start + 500]
        got = run([_case(platform, language, texts=part, frame=min(frames))])
        assert len(got[0]["results"]) == len(part)
        for text, mine, theirs in zip(part, got[0]["results"], wanted[start:], strict=False):
            assert mine == theirs, text
    return wanted


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The platform and the language Python reads a config with."""

    def set_up(platform: str, language: str) -> None:
        monkeypatch.setattr(sys, "platform", platform)
        i18n.set_language(language)

    yield set_up
    i18n.set_language("en")


VALUES = [
    "true", "false", "0", "1", "-1", "4", "5", "39", "40", "50", "51", "52", "60", "61", "99",
    "100", "101", "120", "121", "180", "1440", "1441", "5000", "5001", "-5001", "604800",
    "604801", "0.4", "0.5", "1.5", "4.5", "60.0", "60.5", "89.9", "nan", "inf", "-inf", "-0.0",
    "1e300", '""', '"x"', '" fade "', '"Fade"', '"fade"', '"random"', '"auto"', '"off"',
    '"on"', '"de"', '"en"', '"22:00"', '"7:5"', '"7:05"', '"24:00"', '"٢٢:٠٠"', '" Sunset "',
    '"sunrise"', '"#E0457B"', '"#e0457"', '" system "', '"2M"', '"1500k"', '"1.5M"',
    '"2M\\n"', '"1.M"', '"lumen"', '"lumen\\n"', '"metric"', '"imperial"', '"normal"',
    '"slow"', '"turzx"', '"virtual"', '"landscape"', '"portrait"', '"full"', '"calm"',
    '"png"', '"jpeg"', '"limited"', '"4:2:0"', '"4:4:4"', '"noctua-g2"', '" libre "',
    '"own"', '"theme"', '"first"', '"light"', '"classic"', '"fan.lpc.fan_1"',
    '"a\\tb  c\\u0007d "', '"' + "é" * 100 + '"', '"' + "é" * 101 + '"', '"' + "x" * 301 + '"',
    '"' + "x" * 41 + '"', '"' + "x" * 121 + '"', '"Ctrl+Alt+P"', "[]", '[""]', '["a"]',
    '["fade", "wipe"]', '["fade", "random"]', '["playback", "ai 90s", "forecast 1h"]',
    '["playback", "playback"]', '["nope"]', '["ai 2s"]', '["ai 5x"]', '["psutil"]',
    '["load", "audio", "psutil"]', '["load", 1]', "[1, 2]", "[5000, -5000]", "[5001, 0]",
    "[true, 1]", "[1, 2, 3]", "[1.0, 2]", "{}", "{ a = 1 }", "{ a = true }", '{ a = "x" }',
    "{ a = { b = 2 } }", '{ mode = "on", local_clip = "c", clip_asked = true }',
    '{ mode = "nope" }', '{ z = 1, b = 2 }', '[{ theme = "lumen", seconds = 30 }]',
    '[{ name = "A", themes = ["one", " two "] }]', '[{ sensor = "fan.a", name = "N" }]',
    "[{}]", "[{}, {}, {}, {}]", "1979-05-27", "07:32:00", "1979-05-27T07:32:00Z", "9" * 30,
    "-" + "9" * 30, "9" * 400,
]  # fmt: skip

DEVICE = ["model", "driver", "brightness", "rotate", "orientation", "motion", "still",
          "video_range", "jpeg", "jpeg_quality", "dark_on_quit", "dark_on_sleep", "night",
          "dark_when_away", "night_from", "night_to", "night_brightness", "away_brightness",
          "output", "port", "at", "nope"]  # fmt: skip
PANEL = ["theme", "on", "follow_brightness", "game_theme", "media_theme", "alarm_theme",
         "same_look", "follow", "video", "brightness_offset"]  # fmt: skip
KEYS = [
    "theme", "language", "fps", "transition", "symbols", "cards", "card_order",
    "card_transition", "accent", "card_opacity", "light_colours", "graph_steps", "across",
    "refresh_ms", "check_updates", "allow_notify", "tray_icon", "switches", "hotkeys", "device",
    "video",
    "sensors", "hardware", "weather", "services", "modes", "sequence", "panels", "scenes",
    "scene", "transitions", "nope",
    *(f"device.{key}" for key in DEVICE),
    *(f"video.{key}" for key in (
        "mode", "fps", "device_fps", "crf", "preset", "profile", "encoder", "engine_sends",
        "engine_layers", "engine_encoder", "direct", "rest", "pixels", "keyframe_s", "maxrate",
        "ffmpeg", "local_clip", "clip_asked", "nope")),
    *(f"transitions.{key}" for key in (
        "default", "speed", "game", "media", "idle", "sequence", "autopilot", "report",
        "manual", "random", "nope")),
    "sensors.providers", "sensors.linux", "hardware.fans", "hardware.cpu_name",
    "hardware.gpu_name", "hardware.memory_name", "hardware.fan_look", "hardware.fan_paint",
    "weather.enabled", "weather.provider", "weather.latitude", "weather.longitude",
    "weather.place", "weather.units", "weather.update_minutes", "services.enabled",
    "services.media", "modes.game", "modes.game.fps", "modes.game.theme", "modes.game.on",
    "modes.idle.minutes", "sequence.on", "sequence.steps", "hotkeys.enabled", "hotkeys.pause",
    "hotkeys.nope", "switches.frames", "switches.lumen.glow", "switches.Lumen",
    "switches.lumen.Glow",
]  # fmt: skip
IN_LISTS = [
    *(("panels = [{ %s = %s }]", key) for key in [*PANEL, *DEVICE]),
    *(('panels = [{ video = { %s = %s } }]', key) for key in ("mode", "local_clip",
                                                              "clip_asked", "nope")),
    *(('scenes = [{ name = "S", themes = ["a"], %s = %s }]', key) for key in ("name", "themes")),
    *(('hardware.fans = [{ sensor = "fan.a", %s = %s }]', key)
      for key in ("sensor", "name", "look", "paint")),
    *(('sequence.steps = [{ theme = "lumen", %s = %s }]', key)
      for key in ("theme", "seconds", "minutes", "hours", "nope")),
]  # fmt: skip


def _grid() -> list[str]:
    texts = [f"{key} = {value}" for key in KEYS for value in VALUES]
    for shape, key in IN_LISTS:
        clash = key in shape.replace("%s", "")  # (a key twice is no TOML)
        texts += [(shape.replace(f'{key} = "S", ', "").replace(f'{key} = ["a"], ', "")
                   .replace(f'{key} = "fan.a", ', "").replace(f'{key} = "lumen", ', "")
                   if clash else shape) % (key, value) for value in VALUES]  # fmt: skip
    return texts


def test_every_setting_with_every_kind_of_value(on: Any) -> None:
    on("linux", "en")
    texts = _grid()
    wanted = _same(texts)
    good = [text for text, answer in zip(texts, wanted, strict=True) if "ok" in answer]
    assert len(good) > 800 and len(texts) - len(good) > 3000
    kinds = {answer["error"]["type"] for answer in wanted if "error" in answer}
    assert kinds >= {"ConfigError", "OverflowError", "ValueError"}

    # settings that are fine alone, several at once: what one decides for another
    rng = random.Random(8)
    several = ["\n".join(rng.sample(good, rng.randint(2, 9))) for _ in range(1500)]
    wanted = _same(several)
    assert sum("ok" in answer for answer in wanted) > 300


HOTKEYS = [
    "", " ", "Ctrl+Alt+P", "ctrl + alt + p", "alt+ctrl+P", "Shift+A", "A", "Win+1", "Ctrl+é",
    "Ctrl+F1", "Ctrl+F24", "Ctrl+F25", "Ctrl+F0", "F13", "F12", "f24", "Pause", "scrolllock",
    "Ctrl+Space", "Ctrl+pageup", "Ctrl+A+B", "Ctrl++A", "Ctrl+", "+", "Ctrl+Alt", "Ctrl+Ctrl+A",
    "Shift+F13", "Shift+Up", "Ctrl+f²", "Ctrl+F٣", "Ctrl+F" + "1" * 4300,
    "Ctrl+F" + "1" * 4301, "Ctrl+F0" + "0" * 4400 + "5", "Ctrl+nope", "Strg+P", "Ctrl+ß",
    "Ctrl+{0}", "Win+Shift+Alt+Ctrl+Delete", "control+P", "Ctrl+F 1", "Ctrl+f",
]  # fmt: skip
TIMES = [
    "22:00", "7:5", "07:05", "0:00", "23:59", "24:00", "12:60", "٢٢:٠٠", " 22:00 ", "22:00\n",
    "sunset", " SunRise ", "noon", "", "1:2:3", "-1:00", "001:00", "1:000", "²:00", "22.00",
]  # fmt: skip
SPECIAL = [
    "",
    "theme = 'lumen'",
    "weather.enabled = true",
    "weather = { enabled = true, latitude = 52, longitude = 13.4 }",
    "weather = { enabled = true, latitude = " + "9" * 400 + " }",
    "weather = { enabled = true, longitude = 13.4 }",
    "weather.update_minutes = " + "9" * 5000,
    "refresh_ms = " + "9" * 5000,
    "refresh_ms = 99",
    "video.keyframe_s = " + "9" * 400,
    "video = { fps = 30, device_fps = 29 }",
    "video = { fps = 30, device_fps = 30 }",
    "[sensors]\nproviders = ['psutil']\n[sensors.linux]\na = 1\nb = [1979-05-27, 1.5]\n"
    "[sensors.x.y]\nz = 2",
    "sensors.providers = ['load', 'audio', 'psutil']",
    "sensors.providers = ['linux', 'psutil']",
    "sensors.count = 3",
    "[services]\nenabled = ['media', 'agents']\ncount = 2\n[services.agents]\nplan = 'max'",
    "[modes.game]\nfps = 30\ntheme = 'arena'\non = true\n[modes.idle]\nminutes = 5\n"
    "[modes.media]\nminutes = 0.5",
    "modes.idle.minutes = 1440.5",
    "modes.idle.minutes = true",
    "modes.idle = 3",
    "sequence = { on = false, steps = [{ theme = ' a ', minutes = 1.5, seconds = 2 },"
    " { theme = 'b', hours = 1 }] }",
    "sequence.steps = [{ theme = 'a', seconds = nan }]",
    "sequence.steps = [{ theme = 'a', seconds = " + "9" * 400 + " }]",
    "sequence.steps = [{ theme = 'a', seconds = 2.5 }, { theme = 'b', seconds = 4.5 }]",
    "sequence.steps = [{ theme = 'a', days = 1, also = 2 }]",
    "sequence.steps = [{ theme = 'a', hours = 169 }]",
    "sequence.steps = 3",
    "[hotkeys]\nenabled = false\npause = 'ctrl+alt+p'\nopen = 'F13'\ntimer = ''",
    "[hotkeys]\npause = 'Ctrl+P'\nopen = 'p + CTRL'",
    *(f"hotkeys.pause = '{keys}'" for keys in HOTKEYS if "'" not in keys),
    "[switches]\nframes = false\n[switches.lumen]\nglow = true\nrain = false",
    "transitions = { default = 'wipe', speed = 'fast', game = '', idle = 'zoom',"
    " random = ['fade', 'iris'] }\ntransition = 'cut'",
    "transition = 'wipe'\ntransitions.speed = 'slow'",
    "[device]\nmodel = 'auto'\nbrightness = 80\nnight = true\nnight_from = '22:30'\n"
    "night_brightness = 10\naway_brightness = 0\nat = [10, -20]\n"
    "[[panels]]\ntheme = 'terra'\nbrightness_offset = -30\ngame_theme = ' arena '\n"
    "media_theme = 'player'\nalarm_theme = ''\nat = []\nvideo = { mode = 'on' }\n"
    "[[panels]]\nfollow_brightness = false\nbrightness = 55\ndriver = 'virtual'\n"
    "same_look = true\nfollow = false\n[[panels]]\non = false\noutput = 'own.png'\n"
    "[[scenes]]\nname = '  Work   day '\nthemes = ['lumen', '', 'arena']\n"
    "[[scenes]]\nname = 'Play'\nthemes = ['', 'terra']\n",
    "scene = ' Play '\n[[panels]]\n[[panels]]\n[[scenes]]\nname = 'Play'\n"
    "themes = ['arena', '', 'terra', 'more']",
    "scene = 'Gone'\n[[scenes]]\nname = 'Play'\nthemes = ['arena']",
    "[[scenes]]\nname = 'A  B'\nthemes = ['a']\n[[scenes]]\nname = 'A B'\nthemes = ['a']",
    "scenes = [" + ", ".join(f"{{ name = 'S{n}', themes = ['a'] }}" for n in range(13)) + "]",
    "[[panels]]\n[[panels]]\n[[panels]]\n[[panels]]",
    "[[panels]]\nvideo = { local_clip = '" + "é" * 101 + "' }",
    "video.local_clip = '" + "é" * 100 + "'",
    "cards = ['playback 5m', 'ai', 'forecast 24h', 'date 5s']",
    "cards = ['playback 24h', 'ai 86401s', 'date 4.9s']",
    "cards = [' playback   1.5m ', 'AI']",
    "cards = 'playback'",
    "theme = 1\ntheme = 2",
    "a = " + "[" * 30 + "]" * 30,
    "a = " + "[" * 2000 + "]" * 2000,
]  # fmt: skip


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
@pytest.mark.parametrize("language", ["en", "de"])
def test_configs_on_each_platform_and_in_each_language(
    on: Any, tmp_path: Path, platform: str, language: str
) -> None:
    on(platform, language)
    wanted = _same([*SPECIAL, config.default_config_toml(), *_grid()[::37]], platform, language)
    assert {"ConfigError", "OverflowError", "ValueError", "RecursionError", "TOMLDecodeError"} <= {
        answer["error"]["type"] for answer in wanted if "error" in answer
    }

    def shortcut(keys: str) -> dict[str, Any]:
        try:
            combo = hotkeys.parse(keys)
        except ValueError as error:
            return {"error": {"type": type(error).__name__, "msg": str(error)}}
        return {"ok": str(combo), "mods": list(combo.mods), "key": combo.key}

    files = {
        "good.toml": b"theme = 'terra'\n[[panels]]\ntheme = 'lumen'\n",
        "bom.toml": b"\xef\xbb\xbftheme = 'terra'\n",
        "latin.toml": b"theme = 'terra'\nacross = 'caf\xe9'\n",
        "broken.toml": b"theme = \n",
        "wrong.toml": b"fps = 99\n",
        "crlf.toml": b"theme = 'terra'\r\nfps = 5\r\n",
        "empty.toml": b"",
    }
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    (tmp_path / "folder.toml").mkdir()
    paths = [tmp_path / name for name in [*files, "folder.toml", "missing.toml"]]
    loaded = [_loaded(path) for path in paths]
    got = run([_case(platform, language, hotkeys=HOTKEYS, times=TIMES,
                     files=[str(path) for path in paths], file_frame=loaded[0][1])])[0]  # fmt: skip
    assert got["hotkeys"] == [shortcut(keys) for keys in HOTKEYS]
    assert got["times"] == [[config.time_of_day(text), config.night_time(text)] for text in TIMES]
    assert got["providers"] == config.default_providers()
    assert got["defaults"] == _plain(config.Config())
    for path, mine, (theirs, _) in zip(paths, got["files"], loaded, strict=True):
        if "error" in theirs and theirs["error"]["type"] != "ConfigError":
            mine["error"]["msg"] = theirs["error"]["msg"] = ""  # (the system's own words)
        assert mine == theirs, path.name
    assert "ok" in loaded[0][0] and loaded[0][0]["ok"][1][-2][1] is not None  # (its stamp)


EDITED = """\
# my panel
theme = "terra"   # the look
fps = 5
light_mode = true

[device]
driver = "virtual"
  brightness = 80 # indented
output = "panel.png"

[weather]
# units = "metric" or "imperial"
location = "Berlin # not a comment"
update_minutes = 15

[services]
enabled = [
  "game",   # in the array ] [
  "timer",
]

[services.game]
theme = "arena"

[services.agents]
cloud = "x"

[[scenes]]
name = "Work"
themes = ["terra", ""]

# about Film
# (two lines)
[[scenes]]
name = "Film"
themes = ["lumen"]

[hotkeys]
pause = "Ctrl+Alt+P"
"""
EDITED_OTHERS = [
    "",
    "theme = 'terra'",
    'theme = """\nterra"""\nfps = 5\n',
    "device.brightness = 50\n",
    '"theme" = "terra"\n',
    'scenes = [{ name = "A", themes = ["terra"] }]\nscene = "A"\n',
    "[device]\nbrightness = 50\n[weather]\n",
    "[ device ] # the panel\nbrightness = 50\n\n\n[[panels]]\ntheme = 'lumen'\n# last\n",
    "theme = 'ü'\n# ö\n[[scenes]]\nname = 'ä'\nthemes = []\n[weather]\nunits = 'metric'\n",
    "[[scenes]]\nname = 'A'\nthemes = ['terra']\n[sequence]\non = true\n"
    "[[sequence.steps]]\ntheme = 'terra'\nseconds = 30\n",
    "fps = \n",
    "device.brightness = 50\ntheme = 'terra'\n",
    '"fps" = 5\n"theme" = "terra"\n[weather]\n"units" = "metric"\n',
    "services = { enabled = ['game'] }\n",
    "theme = 'terra'\n[[services.autopilot.rules]]\nwhen = 'game'\ntheme = 'arena'\n"
    "[services.autopilot]\nevery_s = 2\n",
]


def _edit_step(rng: random.Random) -> list[Any]:
    scenes = rng.choice([
        [], [{"name": "A", "themes": ["terra"]}],
        [{"name": "A", "themes": ["terra", ""]}, {"name": "B c", "themes": []}],
        [{"name": "A", "themes": ["terra"]}, {"name": "A", "themes": []}],
    ])  # fmt: skip
    rules = rng.choice([[], [{"when": "game", "theme": "arena", "n": 2, "on": True,
                              "for": {"a b": [1.5, "x"]}}]])  # fmt: skip
    edits = [
        [["value", "theme", rng.choice(["terra", "lumen", "a # b", 'q"uote\\', "ü\x7f", 5])]],
        [["value", "fps", rng.choice([1, 5, 30, 99, 2.5, True])]],
        [["value", "device.brightness", rng.choice([0, 55, 100, 101])]],
        [["value", "services.enabled", rng.choice([[], ["game"], ["timer", "game"], [1]])]],
        [["value", "services.game.theme", "arena"], ["value", "theme", "terra"]],
        [["value", "weather.units", rng.choice(["metric", "imperial", "x"])]],
        [["value", "weather.update_minutes", 30], ["unset", "weather.location"]],
        [["unset", rng.choice(["theme", "fps", "device.brightness", "weather.units", "scene",
                               "services.enabled", "nothing.here", "hotkeys.pause"])]],
        [["tables", "scenes", scenes]],
        [["tables", "scenes", scenes], ["value", "scene", "A"]],
        [["tables", "services.autopilot.rules", rules]],
        [["tables", "panels", rng.choice([[], [{"theme": "lumen"}]])], ["value", "fps", 7]],
        [["value", "theme.x", 1]],
        [["value", "hotkeys.pause", rng.choice(["Ctrl+Alt+P", "p", "Ctrl+Alt+O"])]],
        [["value", "device.x", {"a": 1, "b c": {}}]],
    ]  # fmt: skip
    others = [
        ["theme", rng.choice(["lumen", "terra", ""])],
        ["brightness", rng.choice([0, 40, 100, 300])],
        ["state"], ["put_on", rng.choice(["A", "Work", "Film", "", "nope"])], ["next"],
        ["save", rng.choice(["New  one", "A", "", " x" * 30, "Work"])],
        ["delete", rng.choice(["A", "Work", "nope"])],
    ]  # fmt: skip
    return ["edit", rng.choice(edits)] if rng.random() < 0.6 else rng.choice(others)


def _edit_text(rng: random.Random) -> str | None:
    roll = rng.random()
    if roll < 0.08:
        return None  # (no file yet: the default one is written first)
    if roll < 0.25:
        return config.default_config_toml()
    if roll < 0.5:
        return rng.choice(EDITED_OTHERS)
    lines = EDITED.split("\n")
    for _ in range(rng.randrange(4)):
        at = rng.randrange(len(lines))
        kind = rng.randrange(5)
        if kind == 0:
            del lines[at]
        elif kind == 1:
            lines.insert(at, rng.choice(["", "# fps = 30", "   ", "#theme = 'x'", "[colors]"]))
        elif kind == 2:
            lines[at] = "\t" + lines[at]
        elif kind == 3:
            lines[at] = lines[at] + rng.choice(["  # why", " ", "\t"])
        else:
            lines[at], lines[at - 1] = lines[at - 1], lines[at]
    text = "\n".join(lines)
    return text.rstrip("\n") if rng.random() < 0.1 else text


def test_config_toml_is_changed_as_config_py_changes_it(tmp_path: Path) -> None:
    """config.py's edit_config, set_active_theme and set_brightness and scenes.py: a setting
    set or taken out, arrays of tables replaced, scenes put on, kept and taken out, in files
    of many layouts (the default one, indented keys, comments, values over several lines,
    settings that are no more, layouts that are refused): what each step gives or its error,
    and the file's bytes after it."""
    from libre_panel import scenes

    rng = random.Random(0xED17)

    def taken(step: list[Any], path: Path) -> Any:
        kind = step[0]
        if kind == "edit":
            changes = [(c[0], c[1], c[2] if len(c) > 2 else None) for c in step[1]]
            config.edit_config(*changes, path=path)
        elif kind == "theme":
            config.set_active_theme(step[1], path)
        elif kind == "brightness":
            config.set_brightness(step[1], path)
        elif kind == "next":
            return scenes.next_one(path)
        else:
            how = {"state": scenes.state, "put_on": scenes.put_on, "save": scenes.save,
                   "delete": scenes.delete}[kind]  # fmt: skip
            state = how(*step[1:], path)
            return [[[list(pair) for pair in scene.items()] for scene in state["scenes"]],
                    state["on"]]  # fmt: skip
        return None

    files, theirs = [], []
    for number in range(800):
        text = _edit_text(rng)
        steps = [_edit_step(rng) for _ in range(rng.randrange(1, 6))]
        paths = []
        for side in ("py", "rs"):
            path = tmp_path / side / str(number) / "config.toml"
            path.parent.parent.mkdir(exist_ok=True)
            if text is not None or rng.random() < 0.5:
                path.parent.mkdir(exist_ok=True)
            if text is not None:
                path.write_bytes(text.encode("utf-8"))
            paths.append(path)
        rng.random()  # (the folder's draw, once for each side: keep the sides alike)
        if text is None and paths[0].parent.exists() != paths[1].parent.exists():
            for path in paths:
                path.parent.mkdir(exist_ok=True)
        done = []
        for step in steps:
            try:
                answer: Any = {"ok": taken(step, paths[0])}
            except Exception as error:
                answer = {"error": {"type": type(error).__name__, "msg": str(error)}}
            after = paths[0].read_bytes().hex() if paths[0].exists() else None
            done.append([answer, after])
        theirs.append(done)
        files.append({"path": str(paths[1]), "steps": steps})
    platforms = ["linux", "win32", "darwin"]
    case = {"op": "config_edit", "platform": sys.platform, "language": "en", "files": files,
            "locale": str(i18n.LOCALE_DIR), "defaults": platforms}  # fmt: skip
    [ours] = run([case])
    assert "files" in ours, ours
    assert ours["defaults"] == [config.default_config_toml(name) for name in platforms]
    seen = dict.fromkeys(("ok", "by hand", "ConfigError", "state", "changed"), 0)
    for number, (want, got) in enumerate(zip(theirs, ours["files"], strict=True)):
        py, rs = str(tmp_path / "py" / str(number)), str(tmp_path / "rs" / str(number))
        before = None
        for step, a, b in zip(files[number]["steps"], want, got, strict=True):
            for answer, folder in ((a[0], py), (b[0], rs)):
                if "error" in answer:
                    answer["error"]["msg"] = answer["error"]["msg"].replace(folder, "<folder>")
                    if answer["error"]["type"] != "ConfigError":
                        answer["error"]["msg"] = ""  # (the system's own words)
            assert b == a, (number, step, files[number]["path"])
            seen["ok"] += "ok" in a[0]
            seen["state"] += isinstance(a[0].get("ok"), list)
            seen["by hand"] += "by hand" in a[0].get("error", {}).get("msg", "")
            seen["ConfigError"] += a[0].get("error", {}).get("type") == "ConfigError"
            seen["changed"] += a[1] != before
            before = a[1]
    assert all(count >= 10 for count in seen.values()), seen
