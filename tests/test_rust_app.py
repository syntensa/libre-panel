"""rust/app (docs/RUST.md, R5 and R9): `libre-panel`, the program of Rust, run as a person
runs it. It finds the app's files, reads config.toml, draws a theme's frame of readings and
writes it where it is told; its frames are the Rust renderer's, which test_rust_render.py
holds to the Python renderer's pixels."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from rust_check import (
    find,
    pillow_libfreetype,
    pillow_libharfbuzz,
    pillow_libjpeg,
    pillow_libwebp,
    python_tzdata,
)

import libre_panel

DATA = Path(libre_panel.__file__).resolve().parent
BUILTIN_THEMES_DIR = DATA / "themes"


def _program() -> Path:
    check = find()
    if check is None:
        pytest.skip("rust/ is not built")
    program = check.with_name("libre-panel.exe" if os.name == "nt" else "libre-panel")
    if not program.is_file():
        pytest.skip("libre-panel is not built")
    return program


def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """The program run with its settings in ``home``, the app's files the Python app's, and
    the libraries of C the comparisons use (rust_check.run)."""
    env = {**os.environ, "LIBRE_PANEL_HOME": str(home), "LIBRE_PANEL_DATA": str(DATA),
           **pillow_libwebp()}  # fmt: skip
    libraries = {"LP_LIBJPEG": pillow_libjpeg(), "LP_LIBFREETYPE": pillow_libfreetype(),
                 "LP_LIBHARFBUZZ": pillow_libharfbuzz(), "LP_TZDATA": python_tzdata()}  # fmt: skip
    env.update({name: file for name, file in libraries.items() if file is not None})
    return subprocess.run([str(_program()), *args], env=env, capture_output=True,
                          encoding="utf-8", timeout=120, check=False)  # fmt: skip


# (letters want FreeType, loaded where the program runs: Pillow's own, which its Windows
# wheels have built in; docs/RUST.md, R2)
draws = pytest.mark.skipif(pillow_libfreetype() is None, reason="needs Pillow's FreeType")


@draws
def test_it_draws_a_theme_into_a_png_file(tmp_path: Path) -> None:
    out = tmp_path / "frames" / "frame.png"
    done = _run(tmp_path, "render", "--demo", "--theme", "lumen", "--output", str(out))
    assert done.returncode == 0, done.stderr
    assert "theme lumen" in done.stderr
    with Image.open(out) as frame:
        assert frame.size == (1920, 480) and frame.mode == "RGB"
        assert len(frame.getcolors(1 << 24)) > 500  # (a picture, not one colour)
    assert not out.with_name("frame.png.tmp").exists()


@draws
def test_it_follows_config_toml(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        'theme = "terra"\nfps = 5\n[device]\ndriver = "virtual"\noutput = "panel.png"\n'
        '[sensors]\nproviders = ["psutil", "nope"]\n',
        encoding="utf-8",
    )
    said = _run(tmp_path, "config")
    assert said.returncode == 0, said.stderr
    assert "theme: terra" in said.stdout and "sensors: psutil, nope" in said.stdout
    done = _run(tmp_path, "run", "--frames", "3")
    assert done.returncode == 0, done.stderr
    assert "theme terra" in done.stderr
    assert "not in this build yet: nope" in done.stderr
    with Image.open(tmp_path / "panel.png") as frame:  # (relative: in the settings folder)
        assert frame.mode == "RGB" and frame.width > frame.height


def test_a_config_that_is_wrong_is_said_in_config_py_s_words(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("fps = 99\n", encoding="utf-8")
    done = _run(tmp_path, "run", "--frames", "1")
    assert done.returncode == 1
    assert "fps: must be between 1 and 60" in done.stderr


def test_it_lists_what_it_knows(tmp_path: Path) -> None:
    themes = _run(tmp_path, "themes").stdout.split()
    assert themes == sorted(p.name for p in BUILTIN_THEMES_DIR.iterdir()
                            if (p / "theme.json").is_file())  # fmt: skip
    models = _run(tmp_path, "models").stdout
    assert "majesty-9.16" in models
    assert _run(tmp_path, "nope").returncode == 1
    assert "libre-panel run" in _run(tmp_path).stdout
