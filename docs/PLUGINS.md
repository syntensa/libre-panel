# Plugins

Libre Panel stays small; everything that serves one particular setup comes
as a plugin: a Python package that registers parts of these kinds.

| Kind | Entry point group | What it is |
|---|---|---|
| Service | `libre_panel.services` | a long-running helper: game mode, autopilot, a database |
| Screen | `libre_panel.screens` | draws the whole frame in code; a theme selects it by name |
| Widget type | `libre_panel.widgets` | a widget the editor can place, drawn by plugin code |
| Transition | `libre_panel.transitions` | how the panel goes from one theme (or mode) to the next |
| Toast style | `libre_panel.toasts` | how messages from services look; a theme selects it by name |
| Editor page | `libre_panel.editor_pages` | a page of the plugin's own in the theme editor |
| Themes | `libre_panel.themes` | a package whose folder holds theme folders |
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

## Themes

A plugin brings its themes as a package of theme folders; the entry point
names the package, not a class:

```toml
[entry-points."libre_panel.themes"]
spur = "my_plugin.themes"   # my_plugin/themes/<theme>/theme.json, with its assets
```

They show up in the editor and the tray as "(plugin)" and are read-only
like the built-in ones: saving makes a copy in the user folder, which then
wins over the plugin's theme of that name (a plugin's wins over a built-in
one).

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
| `host.show_theme(name, transition=None, priority=0)` / `host.restore_theme(transition=None)` | show another theme for a while; `config.toml` stays as it is. `transition`: `cut`, `fade`, `slide`, a plugin's, or `(name, {parameters})`; default from `config.toml` |
| `host.set_mode(name, transition=None)` / `host.mode` | switch to a mode from `[modes.<name>]`; `None` ends it. A transition plays also when the theme stays |
| `host.notify(text, icon=None, level="info", seconds=None, kind="", rank=0, payload=None)` | a short message on the panel (see *Toasts*) |
| `host.on(event, callback)` | `panel-connected`, `panel-lost`, `theme-changed` (`theme=`), `mode-changed` (`mode=`), `quit`; callbacks run in an event thread |
| `host.data_dir` | `<settings>/plugins-data/<service>/` for the service's files |
| `host.log` | a logger named after the service |

All calls are thread-safe. Which theme is shown: one a service asked for,
else the active mode's theme, else the one in `config.toml`. When several
services ask, the highest `priority` wins, and among equals the last to ask;
`restore_theme` takes back only the caller's own request, so the one below
shows again (a round report at priority 10 over an autopilot at 0). A theme
that does not exist is refused once with a warning; the panel keeps the
current one.

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
  services) are there; `now` is the frame's time in seconds for animations
  (steady; in video mode the time the frame is planned for, so a frame that
  starts a little late does not make an animation stutter).
- `context` has `size`, `orientation`, `palette`, `model`,
  `hidden_edges()` (pixels the bezel hides, as data: nothing is cropped),
  `font(ref, size)`, `color(value)`, `fps`, `language`, `t(text)`,
  `supersample`/`downsample()` and `assets`, the plugin's own folder for
  pre-rendered files.
- `suppresses = {"music"}` names toast kinds the screen shows anyway: those
  toasts are left out while it is on.
- Do not change an image after returning it; return the same object when
  nothing changed. An RGB image with no widgets on top goes to the panel as
  it is, without compositing (about 4 ms less per frame at 1920×480).
- `context.preview` is true in an editor preview (and `libre-panel
  render`): leave what the panel's drawing keeps between frames (histories,
  smoothing) alone. `context.shown` is the frame the panel showed last,
  after transitions and toasts, e.g. for particles that start from what is
  on it.
- `context.continuous` is true in video mode, where every frame reaches the
  panel; `context.progress(now)` says how far `now` is from the last reading
  to the next (0 to 1). Move a curve by that part of a sample width and it
  scrolls between readings, as the built-in graphs do.
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

## Toasts

`host.notify` shows a short message on the panel. Built in is a card in the
theme's colours with a coloured edge for the level (`info`, `warning`,
`error`, or `payload["color"]`) and one of the built-in icons (`cpu`, `gpu`,
`temperature`, `fan`, …) if named, keeping clear of what the bezel hides.
Toasts are part of the frame, also in video mode (a separate overlay would
make the video judder).

- Messages show one after the other, each for its `seconds` (None: the
  theme's hold time). A higher `rank` replaces the one on show at once; the
  others wait, by rank and then in order.
- `kind` says what a message is about (`"music"`, `"volume"`, `"device"`,
  …). A theme switches kinds off with `"toast": {"off": ["music"]}`, and a
  screen leaves out those it shows anyway with `suppresses = {"music"}`.
- `payload` carries what a style draws besides the text, for example
  `{"image": cover, "progress": 0.4, "color": "#EF4444"}`.
- While a transition plays, new toasts wait.

The theme decides the rest (`"toast": {"anchor": "bottom-right",
"seconds": 5, "style": "myplugin.band", "options": {...}}`; the editor has
it under *Theme → Messages*). A toast style draws them its own way:

```python
from libre_panel.plugins import ToastStyle


class Band(ToastStyle):
    name = "myplugin.band"
    api = 1
    label = {"en": "Band", "de": "Band"}
    options = {"height": ("int", 96)}  # field kinds as for screens
    leave_s = 0.26  # seconds to leave after the hold time

    def draw(self, frame, toast, age):
        # age: 0 when it arrives ... toast.seconds + leave_s when it is gone
        out = frame.copy()
        ...  # roll in, draw toast.text, toast.payload.get("image"), ...
        return out
```

`self.context` is the theme's render context (palette, fonts, size,
`hidden_edges()`), `self.anchor` the theme's toast position. `draw` runs at
up to 50 fps: keep what does not change. A style that fails leaves the
frame as it is; one that is not installed falls back to the card.

## Transitions

When the theme changes (a service's `show_theme`, a mode, the editor, the
tray), the panel blends from the old frame to the new one. `transition`
in `config.toml` chooses how (`fade` by default; `cut`, `slide`), and a
service can choose per change, also for a mode change that keeps the theme
(`set_mode("game", transition=...)`). A plugin can add its own:

```python
from libre_panel.plugins import Transition


class Wipe(Transition):
    name = "wipe"
    api = 1
    duration = 0.3  # seconds

    def frame(self, old, new, t):  # t from 0 to 1; old and new are the same size
        out = old.copy()
        width = round(new.width * t)
        out.paste(new.crop((0, 0, width, new.height)), (0, 0))
        return out
```

A transition can take parameters from the service and draw with the new
theme's colours and fonts:

```python
class Entrance(Transition):
    name = "myplugin.entrance"
    api = 1

    def __init__(self, context=None, params=None):
        super().__init__(context, params)  # self.context, self.params
        self.duration = 2.4 if self.params.get("logo") else 0.9

    def frame(self, old, new, t):
        accent = self.context.color("@accent")
        ...  # self.params["game"], self.params["logo"] (a PIL image), ...


# in a service:
self.host.set_mode("game", transition=("myplugin.entrance", {"game": name, "logo": logo}))
```

While a transition plays, further switches wait until it is over (a report
screen that follows a game's end does not cut the entrance short), and so
do new toasts. Between themes of different sizes (landscape to portrait)
the panel switches without a transition.

## Editor pages

A plugin can bring its own page into the theme editor, for settings or an
analysis. It appears under *Pages* in the editor's top bar and replaces the
editing area until *Theme editor* goes back.

```python
from libre_panel.plugins import EditorPage


class Cooling(EditorPage):
    api = 1
    title = {"en": "Cooling", "de": "Kühlung"}
    static = "page"  # folder next to this module, with index.html

    def handle(self, method, path, query, body):
        if path == "curve" and method == "GET":
            return 200, {"points": self.load_curve()}
        if path == "curve" and method == "POST":
            self.save_curve(body["points"])
            return 200, {"saved": True}
        return 404, {"error": "not found"}
```

```html
<!-- page/index.html -->
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="/static/editor.css">
  <script type="module" src="app.js"></script>
</head>
<body><h1>Cooling</h1><div id="curve"></div></body>
</html>
```

```js
// page/app.js
import { api, loadTexts, el, field, button } from "/static/kit.js";

await loadTexts();
const { points } = await api("curve");
// ... build the page with el(), field(), button() like the editor does
await api("curve", { method: "POST", body: { points } });
```

- Files come from `/plugins/<id>/` (`.html`, `.js`, `.css`, images, fonts,
  `.json`; nothing outside the folder). Scripts must be files: the editor's
  security policy allows no inline scripts.
- `handle(method, path, query, body)` answers `/api/plugins/<id>/<path>` and
  returns `(status, data)`; `data` is sent as JSON. GET must not change
  anything: changes go through POST, which needs the editor's header
  (`kit.js`'s `api()` sends it). The editor listens on 127.0.0.1 only.
- `self.context.service(name)` is the running service of that name when the
  editor belongs to the background app (tray), else `None`;
  `self.context.data_dir(name)` is that service's data folder.
- `kit.js` exports `api`, `loadTexts`, `t`, `language`, `el`, `field`,
  `button` and `setStatus`; with `editor.css` the page looks like the editor.

## Modes

A mode changes how the panel runs while it is active, for example fewer
frames while a game needs the machine:

```toml
[modes.game]
fps = 30          # frames per second while the mode is active
theme = "slate"   # optional: another theme
```

Services switch modes with `host.set_mode("game")`, optionally with a
transition; the tray and the editor show the active mode.
