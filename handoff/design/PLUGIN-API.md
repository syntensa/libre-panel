# Libre Panel plugin API — design for part B (draft 1)

Status: **proposal**, for the SPUR II mod to check before anything is built.
Nothing here exists yet except the two plugin kinds Libre Panel already has
(`libre_panel.sensors`, `libre_panel.devices`).

## Principles

1. **Themes stay data.** A theme names a screen, widget type or transition;
   the code comes only from installed plugins. A theme that names a missing
   plugin still loads: the panel shows the rest and a warning (a broken part
   never blanks the panel).
2. **One plugin = one Python package** that can register any of the kinds
   below. The SPUR II mod is one package, e.g. `libre_panel_spur2`.
3. **Two ways to install**, same package:
   - `pip install libre-panel-spur2` into Libre Panel's Python (entry points);
   - a folder `<settings>/plugins/<name>/` holding the package and a
     `plugin.toml` with the same entry point table. This is for the Windows
     setup and the other frozen downloads, which cannot `pip install`. The
     folder is added to `sys.path` at start. Libre Panel never downloads or
     installs code itself, and the editor cannot either.
4. **Isolation by contract, not by sandbox.** Plugins are trusted code the
   user installed. Every call into a plugin is wrapped: an exception is
   logged, the part is skipped or retried, the panel keeps running.
5. **Versioned.** `libre_panel.plugins.API_VERSION = 1`. A plugin declares
   `api = 1`; a mismatch is refused with a clear message instead of
   crashing later.
6. `libre-panel plugins` lists what is installed, from where, and what
   failed to load.

```toml
# plugin.toml (folder install) — same keys as [project.entry-points] in pyproject.toml
name = "spur2"
api = 1

[entry-points."libre_panel.screens"]
spur-studio = "libre_panel_spur2.screens:Studio"

[entry-points."libre_panel.widgets"]
"spur.light-ring" = "libre_panel_spur2.widgets:LightRing"

[entry-points."libre_panel.services"]
spur-gamemode = "libre_panel_spur2.gamemode:GameMode"
```

## B1 — Screens (`libre_panel.screens`)

A screen draws the whole frame in code. A theme selects it by name:

```json
{ "format": "libre-panel-theme/1", "name": "SPUR II Studio",
  "display": { "model": "turing-9.2-usb", "orientation": "landscape" },
  "screen": { "name": "spur-studio", "options": { "seconds": true } },
  "widgets": [] }
```

```python
from libre_panel.plugins import Screen, ScreenContext

class Studio(Screen):
    name = "spur-studio"
    options = {"seconds": ("bool", True), "cover_color": ("bool", True)}  # editor inspector

    def __init__(self, context: ScreenContext, options: dict) -> None: ...
    def render(self, snapshot, now: float) -> Image.Image: ...   # RGB or RGBA, context.size
    @property
    def moving(self) -> bool: ...   # True while something animates (PNG path: full fps)
    def close(self) -> None: ...
```

`ScreenContext` gives: `size`, `orientation`, `model` (with
`hidden_edges(orientation)` as data only, nothing is enforced), `palette`,
`font(name, size)`, `language` / `t()`, `assets` (the plugin's own
directory, for Blender light layers and other pre-rendered files), and
`fps` (the current frame rate, see B5).

- The screen gets the **full frame** (1920×480 on the 9.2") and paints it
  as it likes (per your reply 4).
- **Widgets of the theme are drawn on top** of the screen. A screen can be a
  pure backdrop with editor-placed widgets, or everything in code with
  `widgets: []`.
- `render` runs on the render thread at up to 50 fps. The renderer measures
  it; a screen that is slow for long gets a warning in the log. Heavy
  preparation belongs in `__init__` or a thread of the screen's own.
- The editor preview renders screens too, with the same code.

## B2 — Widget types (`libre_panel.widgets`)

```python
from libre_panel.plugins import WidgetType

class LightRing(WidgetType):
    type = "spur.light-ring"          # dotted names: plugin widgets never clash with built-ins
    label = {"en": "Light ring", "de": "Lichtring"}
    spec = {                          # same format as the built-in WIDGET_SPECS
        "sensor": ("sensor", ""),
        "r": ("int", 60),
        "min": ("number", 0), "max": ("number", 100),
        "color": ("color", "accent"),
    }
    presets = [{"label": {"en": "CPU ring"}, "widget": {"sensor": "cpu.load", "r": 80}}]

    def key(self, widget, snapshot, now):            # what the picture depends on
        return round(snapshot.value(widget["sensor"]) or 0, 1)

    def draw(self, widget, ctx, snapshot, now):       # -> Piece | None
        ...
```

- The theme loader validates plugin widgets against `spec` like built-ins
  (types, ranges, no paths outside the theme).
- `key` feeds the renderer's cache: an unchanged key reuses the last piece,
  and a slow build runs in the background in video mode, like built-ins.
- `ctx` offers the helpers built-ins use: `font`, `color`, `supersample`,
  `glow`/`shadow` (the common effects fields apply automatically), `gradient`.
- The editor lists plugin widgets under "Building blocks" with their
  presets, and the inspector shows their fields from `spec`.

## B3 — Services (`libre_panel.services`)

Long-running helpers: autopilot, game mode, memory database, cooling analysis.

```python
from libre_panel.plugins import Service, ServiceHost

class GameMode(Service):
    name = "spur-gamemode"
    options = {"fps": ("int", 30), "screen": ("string", "")}

    def __init__(self, host: ServiceHost, options: dict) -> None: ...
    def start(self) -> None: ...      # returns quickly; start your own threads
    def stop(self) -> None: ...       # within 5 s
```

Enabled in `config.toml`:

```toml
[services]
enabled = ["spur-gamemode", "spur-autopilot"]

[services.spur-gamemode]
fps = 30
```

`ServiceHost`:

| Call | What it does |
|---|---|
| `host.snapshot()` | the latest `Snapshot` (readings, history), read-only |
| `host.publish(key, reading)` | add readings the renderer and widgets can show (e.g. `game.fps`, `media.title`) |
| `host.publish_image(key, image)` | an image for widgets/screens (e.g. `media.cover`) |
| `host.show_theme(name, transition=None)` | switch the theme on the panel (not saved to config) |
| `host.restore_theme(transition=None)` | back to the configured theme |
| `host.set_mode(name)` / `host.mode` | see B5 |
| `host.notify(text, icon=None, level="info", seconds=4)` | a toast, see B4 |
| `host.data_dir` | `<settings>/plugins-data/<service>/`, for the memory database |
| `host.log` | a logger named after the service |
| `host.on(event, callback)` | `panel-connected`, `panel-lost`, `theme-changed`, `quit` |

Services run in the background app (tray / `start`), not in the editor alone.

## B4 — Toasts and transitions

**Toasts:** `host.notify(...)` queues a toast; the main loop draws it on top
of the rendered frame (after screen and widgets), in the theme's palette,
at a position the theme may set (`"toast": {"anchor": "top-right"}`).
A plugin can replace the look with `libre_panel.toasts` (a
`draw(toast, ctx, t) -> Piece`). Toasts go into the video, not into the
PNG overlay (M5: overlays above 2/s make video judder).

**Transitions** (`libre_panel.transitions`): when the theme or screen
changes, the main loop renders both for the transition's duration and
blends them:

```python
class Slide(Transition):
    name = "spur.slide"
    duration = 0.4
    def frame(self, old: Image.Image, new: Image.Image, t: float) -> Image.Image: ...  # t 0..1
```

Built in: `cut`, `fade`. On the PNG path transitions are shortened to
what ~9 fps can show; in video mode they run at full rate.

## B5 — Frame rate per mode

```toml
[video]
fps = 50

[modes.game]
fps = 30
# theme = "spur-game"   # optional: also switch the theme
```

A service calls `host.set_mode("game")` and `host.set_mode(None)`. The main
loop paces at the mode's rate; ffmpeg keeps running (the panel's player is
told 60 and shows whatever arrives, so 30 needs no restart and no new
keyframe). The tray and editor show the active mode.

## B6 — Editor pages (`libre_panel.editor_pages`)

```python
from libre_panel.plugins import EditorPage

class Cooling(EditorPage):
    id = "spur-cooling"
    title = {"en": "Cooling", "de": "Kühlung"}
    icon = "fan"                      # one of the built-in line icons
    static = "editor/cooling"          # folder in the plugin: index.html, *.js, *.css

    def api(self, method: str, path: str, body: dict | None) -> tuple[int, dict]:
        ...
```

- The page appears in the editor's sidebar; its files are served from
  `/plugins/<id>/`, its API at `/api/plugins/<id>/…` (127.0.0.1 only,
  writes need the editor's `X-Libre-Panel` header, like every write).
- `/static/kit.js` exports the editor's components (buttons, fields, colour
  and sensor pickers, `t()`), and pages link `/static/editor.css`, so they
  look like the editor. The CSP stays `script-src 'self'`.

## Questions for the mod

1. **Screens:** does "full frame per call" fit the seven screens? Does any
   of them need more than the snapshot (e.g. the media cover, PresentMon
   frame times)? My answer would be: services publish them (B3).
2. **Screen changes:** who switches between Klassisch/Studio/…: the user, the
   autopilot, a time schedule? That decides whether `show_theme` is enough or
   screens need their own switch.
3. **Transitions:** which ones does SPUR use, and how long?
4. **Toasts:** SPUR's volume/device toasts: are they drawn by the screen
   (then B4 only needs the event) or on top of any screen?
5. **Install:** will the mod run in a venv with `pip install -e`, or should
   it work in the Windows setup (plugins folder)?
6. **Game mode:** anything besides the frame rate and possibly the screen?

Order of work once you agree: B5 and B3 first (small, and the mod's services
can start), then B1 and B2 (the screens need the renderer hooks), then B4,
then B6.
