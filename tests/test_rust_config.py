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
    "refresh_ms", "check_updates", "allow_notify", "switches", "hotkeys", "device", "video",
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
