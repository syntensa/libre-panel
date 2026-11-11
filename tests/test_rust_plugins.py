"""Plugins in the Rust app (rust/app's plugins, docs/RUST.md R10): the Python plugins run in a
process of their own, the plugin host (src/libre_panel/plugin_host.py), which the Rust app
starts where plugin folders are installed. What the app says of them and does with them,
beside what the Python app says and does."""

from __future__ import annotations

import json
import sys

from test_plugins import SENSOR, SENSOR_PARTS, theme_data
from test_rust_app import _run

from libre_panel import cli

HOST = {"LIBRE_PANEL_PLUGIN_HOST": sys.executable,
        "LIBRE_PANEL_PLUGIN_HOST_ARGS": "-m libre_panel.plugin_host"}  # fmt: skip


def test_the_plugins_are_listed_as_cli_py_lists_them(plugin_folder, isolated_home, capsys):
    data = theme_data()
    data["name"] = "Demo dash"
    files = {"themes/__init__.py": "", "themes/demo-dash/theme.json": json.dumps(data)}
    plugin_folder(SENSOR, SENSOR_PARTS, files=files)
    plugin_folder("x = 1\n", {"libre_panel.services": {"broken": "Missing"}})
    assert cli.main(["plugins"]) == 1
    want = capsys.readouterr().out
    done = _run(isolated_home, "plugins", **HOST)
    assert (done.returncode, done.stdout) == (1, want), done.stderr
    # the theme a plugin brings is one of the app's
    themes = _run(isolated_home, "themes", **HOST)
    assert "demo-dash" in themes.stdout.split()
    out = isolated_home / "frame.png"
    drawn = _run(isolated_home, "render", "--demo", "--theme", "demo-dash", "--output", str(out),
                 **HOST)  # fmt: skip
    assert drawn.returncode == 0 and "theme demo-dash" in drawn.stderr, drawn.stderr


def test_without_plugins_no_host_is_started(isolated_home, capsys):
    assert cli.main(["plugins"]) == 0
    want = capsys.readouterr().out
    # (a host that cannot start would be said)
    done = _run(isolated_home, "plugins", LIBRE_PANEL_PLUGIN_HOST="/nonexistent")
    assert (done.returncode, done.stdout) == (0, want), done.stderr


def test_plugins_without_a_host_are_said(plugin_folder, isolated_home):
    plugin_folder(SENSOR, SENSOR_PARTS)
    done = _run(isolated_home, "plugins", LIBRE_PANEL_PLUGIN_HOST="/nonexistent")
    assert done.returncode == 1
    assert "error: the plugin host /nonexistent did not start" in done.stdout
