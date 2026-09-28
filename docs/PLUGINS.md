# Plugins

Libre Panel stays small; everything that serves one particular setup comes
as a plugin: a Python package that registers parts of these kinds.

| Kind | Entry point group | What it is |
|---|---|---|
| Service | `libre_panel.services` | a long-running helper: game mode, autopilot, a database |
| Sensor source | `libre_panel.sensors` | readings (see [Architecture](ARCHITECTURE.md#plugins)) |
| Display driver | `libre_panel.devices` | another kind of panel |

`libre-panel plugins` lists what is installed, where it came from, and
whether it loads.

## Installing

**With pip**, into the Python that runs Libre Panel:

```bash
pipx inject libre-panel libre-panel-myplugin     # or: pip install libre-panel-myplugin
```

**As a folder**, for the downloads (Windows setup, macOS app, AppImage),
which have no pip: copy the plugin into the settings folder,

```
<settings>/plugins/myplugin/plugin.toml
<settings>/plugins/myplugin/my_plugin/__init__.py
```

(`libre-panel config path` shows the settings folder.) Libre Panel never
downloads or installs code itself, and the editor cannot either: plugins are
code you chose to install.

## Writing a plugin

A plugin declares the plugin API it was written for. This is version 1.
With pip the entry points go into `pyproject.toml`; a folder plugin has the
same table in `plugin.toml`:

```toml
# pyproject.toml
[project.entry-points."libre_panel.services"]
gamemode = "my_plugin.gamemode:GameMode"
```

```toml
# plugin.toml (folder install)
name = "myplugin"
api = 1

[entry-points."libre_panel.services"]
gamemode = "my_plugin.gamemode:GameMode"
```

Everything a plugin needs is imported from `libre_panel.plugins`. A plugin
that fails to import, raises, or was written for another API version is
reported (log, `libre-panel plugins`) and skipped; the panel keeps running.

## Services

```python
from libre_panel.plugins import Service


class GameMode(Service):
    name = "gamemode"
    api = 1
    options = {"poll_s": ("number", 2.0)}  # types: int, number, bool, string, list

    def start(self):
        # return quickly: start your own thread for the work
        ...

    def stop(self):
        # end your threads within a few seconds
        ...

    def on_game(self, running: bool, fps: float | None):
        self.host.set_mode("game" if running else None)
        self.host.publish("game.fps", fps, "fps", "Game FPS")
```

Turn it on in `config.toml`; the options table overrides the defaults of
`options`:

```toml
[services]
enabled = ["gamemode"]

[services.gamemode]
poll_s = 1.0
```

Services run in the background app (tray, `libre-panel start`) and in
`libre-panel run`. Editing `[services]` starts, stops or restarts them
while Libre Panel runs.

### What a service may do: `self.host`

| Call | |
|---|---|
| `host.publish(key, value, unit="", label="")` | a reading, shown by themes like any sensor (graphs keep its history); `host.unpublish(key)` |
| `host.publish_image(key, image)` | an image (PIL) for screens and widgets, e.g. `media.cover`; `None` removes it |
| `host.snapshot()` | the readings and history the panel was last drawn from |
| `host.show_theme(name)` / `host.restore_theme()` | show another theme for a while; `config.toml` stays as it is |
| `host.set_mode(name)` / `host.mode` | switch to a mode from `[modes.<name>]`; `None` ends it |
| `host.notify(text, icon=None, level="info", seconds=4)` | a short message for the panel |
| `host.on(event, callback)` | `panel-connected`, `panel-lost`, `theme-changed` (`theme=`), `mode-changed` (`mode=`), `quit`; callbacks run in an event thread |
| `host.data_dir` | `<settings>/plugins-data/<service>/` for the service's files |
| `host.log` | a logger named after the service |

All calls are thread-safe. Which theme is shown: one a service asked for,
else the active mode's theme, else the one in `config.toml`. A theme that
does not exist is refused once with a warning; the panel keeps the current
one.

## Modes

A mode changes how the panel runs while it is active, for example fewer
frames while a game needs the machine:

```toml
[modes.game]
fps = 30          # frames per second while the mode is active
theme = "slate"   # optional: another theme
```

Services switch modes with `host.set_mode("game")`; the tray and the editor
show the active mode.
