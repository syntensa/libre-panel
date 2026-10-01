# Architecture

```
 sensors ─┐                                   ┌─ devices (drivers)
 psutil   │   Snapshot    ┌──────────┐ frame  │  virtual (PNG)
 LHM      ├──────────────▶│ Renderer │───────▶│  turzx → turzx_usb (V1.x USB)
 weather  │  readings +   └──────────┘        │  … serial rev. A–D, WeAct, Lian Li
 plugins ─┘  history           ▲              └─ (plugins)
                               │ theme.json
                        ┌──────┴──────┐
                        │ theme model │◀── editor (local web app, same renderer)
                        └─────────────┘
```

| Package | Responsibility |
|---|---|
| `config` | `config.toml`: panel, sensors, weather, plugins. No defaults that point at a person or place. |
| `theme` | Theme format, validation, safe asset paths, saving; `adapt` rescales a theme to another panel. |
| `render` | Pillow renderer; the same code serves the panel and the editor preview. Safe format strings. |
| `sensors` | `SensorProvider` plugins merged by `SensorHub` (priority, history for graphs). |
| `weather` | Open-Meteo provider, background polling, off by default. |
| `devices` | Panel catalog (`models`), `Display` drivers, USB/serial discovery. |
| `plugins` | Plugin API: finding plugins (entry points, plugin folders), services and the host they talk to the panel through, modes. See [Plugins](PLUGINS.md). |
| `editor` | Local HTTP server (127.0.0.1 only) + vanilla JS editor, no build step. |
| `app` | Main loop: sample → render → diff → send; reloads config/theme on change; reports its state (`RunStatus`). |
| `service` | Background app: the main loop in a thread (pause/resume, restarts after a broken config is fixed), the editor server, and the controls shared by tray and editor. |
| `tray` | Tray icon and menu (pystray); runs without an icon where there is no tray. |
| `autostart` | Login start per system: Windows `Run` key (or, asked for, a Task Scheduler task with the highest rights), XDG autostart entry, macOS LaunchAgent. |
| `instance` | OS file lock so only one process drives the panel. |
| `branding` | The logo, drawn in code (tray icon with status dot, app icons). |
| `cli` | `libre-panel` command. |

## Design rules

- **Nothing personal in code.** Location, sensor names, fan labels and
  thresholds are config or theme data.
- **Drivers only move pixels.** A driver never knows about themes or sensors,
  so a new panel family is one class.
- **Themes are untrusted input.** No code, no paths outside the theme folder,
  locked-down format strings, size limits in the editor server.
- **A broken part never blanks the panel.** A failing sensor or widget is
  logged and skipped.
- **Optional features are plugins.** Anything that serves one setup (special
  fan analysis, game statistics, media players) belongs in a separate package
  registered under the `libre_panel.services`, `libre_panel.sensors` or
  `libre_panel.devices` entry points ([Plugins](PLUGINS.md)).

## Plugins

```toml
# pyproject.toml of a plugin package
[project.entry-points."libre_panel.sensors"]
myfans = "my_plugin:FanProvider"
```

```python
from libre_panel.sensors import Reading, SensorProvider


class FanProvider(SensorProvider):
    name = "myfans"
    api = 1  # the plugin API it is written for (docs/PLUGINS.md)

    def read(self):
        return {"fan.front": Reading("fan.front", 1200.0, "RPM", "Front fan")}
```

Users enable it with `providers = ["myfans", "psutil"]`. A plugin folder can
bring sensor sources too (`[entry-points."libre_panel.sensors"]` in its
`plugin.toml`). A source whose `read` is cheap and whose values must follow
at once (a volume) sets `every_frame = True`: it is then also read on every
frame between the `refresh_ms` snapshots (graphs keep one value per
snapshot).
