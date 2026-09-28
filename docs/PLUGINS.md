# Plugins

Libre Panel stays small; everything that serves one particular setup comes
as a plugin: a Python package that registers parts of these kinds.

| Kind | Entry point group | What it is |
|---|---|---|
| Service | `libre_panel.services` | a long-running helper: game mode, autopilot, a database |
| Screen | `libre_panel.screens` | draws the whole frame in code; a theme selects it by name |
| Widget type | `libre_panel.widgets` | a widget the editor can place, drawn by plugin code |
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

When a service stops (Quit, or it was removed from `[services]`), what it
left goes too: its readings and images disappear, a theme or mode it set
ends, and its listeners hear nothing more. `quit` arrives before the
services stop.

## Screens

A screen draws the whole frame in code. Themes stay data: a theme names the
screen, and its widgets are drawn on top (a screen can be a backdrop for
editor-placed widgets, or everything with `"widgets": []`).

```json
{
  "format": "libre-panel-theme/1",
  "name": "Studio",
  "display": {"model": "turing-9.2-usb", "orientation": "landscape"},
  "screen": {"name": "studio", "options": {"seconds": true}},
  "widgets": []
}
```

```python
from PIL import Image

from libre_panel.plugins import Screen


class Studio(Screen):
    name = "studio"
    api = 1
    label = {"en": "Studio", "de": "Studio"}
    options = {"seconds": ("bool", True), "accent": ("color", "#13E5D7")}

    def __init__(self, context, options):
        super().__init__(context, options)
        self.moving = True  # animates: the PNG path draws at full rate too

    def render(self, snapshot, now):
        frame = Image.new("RGB", self.context.size)
        ...
        return frame
```

- `render(snapshot, now)` returns an RGB or RGBA image of `context.size`
  (the full frame, 1920×480 on the 9.2"). It runs on the render thread at
  up to 50 fps; returning the same image object again costs nothing.
- `snapshot.readings`, `snapshot.history` and `snapshot.images` (from
  services) are there; `now` is a monotonic time for animations.
- `context` has `size`, `orientation`, `palette`, `model`,
  `hidden_edges()` (pixels the bezel hides, as data: nothing is cropped),
  `font(ref, size)`, `color(value)`, `fps`, `language`, `t(text)`,
  `supersample`/`downsample()` and `assets`, the plugin's own folder for
  pre-rendered files.
- `__init__` also runs for every preview in the editor, which then calls
  `close()`: keep expensive loading in a module-level cache.
- The theme editor offers installed screens under *Theme → Look*, with their
  options.

## Widget types

```python
from PIL import Image, ImageDraw

from libre_panel.plugins import WidgetType


class LightRing(WidgetType):
    type = "myplugin.light-ring"  # with a dot: never clashes with a built-in
    api = 1
    label = {"en": "Light ring", "de": "Lichtring"}
    spec = {
        "w": ("int", 120),
        "h": ("int", 120),
        "sensor": ("sensor", "cpu.load"),
        "color": ("color", "#13E5D7"),
    }
    presets = [{"label": {"en": "CPU ring"}, "widget": {"sensor": "cpu.load"}}]

    def key(self, widget, snapshot, now):
        return round(snapshot.value(widget["sensor"]) or 0, 1)

    def draw(self, widget, ctx, snapshot, now):
        image = Image.new("RGBA", (widget["w"], widget["h"]))
        ...
        return image  # placed at x, y; or return (image, (x, y))
```

- `spec` declares the widget's fields with the kinds built-in widgets use:
  `int`, `number`, `bool`, `string`, `text`, `color` (`color?` may be
  empty), `font`, `asset`, `sensor`, `format`, `icon`, `enum:a|b`. Themes are
  checked against it like built-in widgets (fonts and assets must stay in the
  theme folder), and the editor's inspector edits these fields. `x`, `y`,
  `id`, `opacity`, `glow`, `shadow` and the other common fields come
  automatically, and their effects are applied to what `draw` returns.
- `key` says what the picture depends on; while it stays the same, the last
  picture is reused. The default is the value of the `sensor` field.
- In video mode `draw` may run in a helper thread while the previous picture
  stays on the panel.
- `presets` appear under *Building blocks*. When a theme moves to another
  panel, `x`, `y`, `w`, `h` and the usual size fields (`font_size`,
  `thickness`, `radius`, `line_width`) are scaled.
- A theme that uses a widget type or screen that is not installed still
  loads: those parts are not drawn, the editor warns, and saving keeps them.

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
