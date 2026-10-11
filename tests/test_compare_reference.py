"""tools/compare_reference.py: a theme against a picture of the same screen."""

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image, ImageFont

from libre_panel.theme.model import THEME_FORMAT

TOOL = Path(__file__).resolve().parent.parent / "tools" / "compare_reference.py"
TRUETYPE = ImageFont.truetype


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("compare_reference", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bar_theme(folder: Path, color: str) -> Path:
    folder.mkdir()
    widgets = [
        {"type": "rect", "id": "back", "x": 0, "y": 0, "w": 1920, "h": 480, "color": "#0A1119"},
        {"type": "bar", "id": "load", "x": 100, "y": 380, "w": 400, "h": 12,
         "sensor": "cpu.load", "color": color, "finish": "lit", "glow": 1.0},
        {"type": "metric", "id": "value", "x": 600, "y": 360, "sensor": "cpu.load",
         "format": "{value:.0f}", "font_size": 34},
    ]  # fmt: skip
    theme = {"format": THEME_FORMAT, "name": "probe", "widgets": widgets,
             "display": {"width": 1920, "height": 480}}  # fmt: skip
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    return folder


def run(tool, monkeypatch, capsys, *args) -> str:
    monkeypatch.setattr(sys, "argv", ["compare_reference.py", *map(str, args)])
    tool.main()
    return capsys.readouterr().out


def test_a_theme_against_its_own_picture_matches_everywhere(tool, tmp_path, monkeypatch, capsys):
    folder = bar_theme(tmp_path / "probe", "#13E5D7")
    values = {"readings": {"cpu.load": [42, "%"]}, "history": {"cpu.load": [10, 20, 30]}}
    (tmp_path / "values.json").write_text(json.dumps(values), encoding="utf-8")
    when = datetime.fromisoformat("2026-09-28 14:29:59")
    tool.render(str(folder), values, when, None).save(tmp_path / "reference.png")
    out = run(tool, monkeypatch, capsys, tmp_path / "reference.png", "--theme", folder,
              "--values", tmp_path / "values.json", "--layout", "raqm", "--out",
              tmp_path / "cmp", "--region", "bar=90,370,510,400")  # fmt: skip
    for region in ("all", "header", "charts", "sections", "bar"):
        assert f"{region:10s} 100.00 % within, mean difference  0.00" in out
    made = {p.name for p in (tmp_path / "cmp").iterdir()}
    assert {"probe-side.png", "probe-diff.png", "probe-bar-x3.png"} <= made
    with Image.open(tmp_path / "cmp" / "probe-side.png") as side:
        assert side.size == (1920, 2 * 480 + 6)  # the reference above the render


def test_differences_are_counted_by_region(tool, tmp_path, monkeypatch, capsys):
    # a lit bar shows its colour only with a value (an empty groove is the same in any colour)
    values = {"readings": {"cpu.load": [42, "%"]}}
    (tmp_path / "values.json").write_text(json.dumps(values), encoding="utf-8")
    teal = bar_theme(tmp_path / "teal", "#13E5D7")
    reference = tool.render(str(teal), values, datetime.now(), None, "basic")
    reference.save(tmp_path / "reference.png")
    other = bar_theme(tmp_path / "purple", "#E0A8FF")
    out = run(tool, monkeypatch, capsys, tmp_path / "reference.png", "--theme", other,
              "--values", tmp_path / "values.json", "--out", tmp_path / "cmp",
              "--zoom", "1")  # fmt: skip
    lines = {line.split()[0]: line for line in out.splitlines() if "% within" in line}
    assert "100.00 %" in lines["header"] and "100.00 %" in lines["charts"]
    assert "100.00 %" not in lines["sections"]  # the bar's colour differs, down there only
    assert not any(p.name.endswith("-x1.png") for p in (tmp_path / "cmp").iterdir())
    assert ImageFont.truetype is TRUETYPE  # the basic layout was for its frame only
