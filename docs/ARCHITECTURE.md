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
| `theme` | Theme format, validation, safe asset paths, saving; `adapt` rescales a theme to another panel; `defaults` says which theme a panel shows when `config.toml` names none (Lumen on a bar, Libre Default elsewhere). |
| `render` | Pillow renderer; the same code serves the panel and the editor preview. Safe format strings. `motion` keeps what moves (gliding values, fading colours, graph histories) from frame to frame and theme to theme. |
| `sensors` | `SensorProvider` plugins merged by `SensorHub` (priority, history for graphs). |
| `weather` | Open-Meteo provider, background polling, off by default. |
| `devices` | Panel catalog (`models`), `Display` drivers, USB/serial discovery. |
| `plugins` | Plugin API: finding plugins (entry points, plugin folders), services and the host they talk to the panel through, modes. See [Plugins](PLUGINS.md). |
| `services` | Libre Panel's own services, built in and off until `config.toml` lists them: `notices` (messages for the volume, tracks, limits), `game` (game mode), `autopilot` (theme rules), `devices` (drives, USB devices and controllers that come and go, Windows), `memory` (what happens at the desk in a SQLite file on this PC, and the round report after a game), `cooling` (FanControl's curves checked against what the PC did, read only) and `downloads` (Steam's and the browsers' downloads). They use only the plugin API; Steam, the programs that run and the Windows window in front are read in `steam` and `machine`, FanControl's configuration in `fancontrol` and what Windows says of its devices in `devices_windows`. |
| `editor` | Local HTTP server (127.0.0.1 only, and only for requests with the secret token of the run: `access`) and the app it serves (`editor/app`: ES modules with Preact and htm, no build step). |
| `pages` | The editor's own pages, built in like the services and written with the plugin API: *Control* (live picture, numbers, sources, services, test messages, theme per mode, log) and *Autopilot* (its rules in `config.toml`). Their scripts and styles are in `pages/web`. |
| `app` | Main loop (`MainLoop.step`, one frame at a time): sample → render → diff → send; reloads config/theme on change; works out the panel's own default theme (from `device.model`, the USB bus or the display that connects); reports its state (`RunStatus`). |
| `meter` | How the main loop is doing: frame rate, drawing time, data rate, the video link and faults, made once a second from a ring of timings and the counters the drivers keep (`devices.base.Counters`). Nothing in it holds a lock. |
| `service` | Background app: the main loop in a thread (pause/resume, restarts after a broken config is fixed), the editor server, and the controls shared by tray and editor. |
| `tray` | Tray icon and menu (pystray); runs without an icon where there is no tray. |
| `autostart` | Login start per system: Windows `Run` key (or, asked for, a Task Scheduler task with the highest rights), XDG autostart entry. |
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
- **The editor's server answers only the person who started Libre Panel.** It listens
  on 127.0.0.1, which the programs of every user of the PC and any website can reach.
  Each run makes a secret token that every request but the app's own files must carry
  (`editor/access.py`, [the console API](#the-console-api)). Run with the highest rights, it
  also leaves the settings that name a program or a file to `config.toml`: a request does not
  change them.
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
snapshot). `every_frame = "video"` does so only in video mode, where every
frame reaches the panel anyway (the `load` source).

Where two sources have a reading for a key, the one earlier in the list wins.
A source whose reading must win over those before it (the user said so, or it
is surely right) names the keys in `overrides = ("cpu.name",)`: the built-in
`hardware` source does so for `cpu.name`, `gpu.name` and `mem.name`, which come
from the computer itself or from `[hardware]` in `config.toml`. A source that
reads settings outside its own `[sensors.<name>]` table gets the loaded
`Config` in `follow_config(config)`, once when it is made and again whenever
`config.toml` changes while the panel runs.

## The console API

The editor's own pages (`pages`) answer under `/api/plugins/<page>/` with JSON, one route
for each thing a screen shows or does, so a screen of any design can use them. They are
plain editor-page routes ([Plugins](PLUGINS.md#editor-pages)): the same server, the same
rules.

- **Every request carries the run's access token** in the header `X-Libre-Panel: <token>`,
  `GET` as much as `POST`. The token is new at every start of the server (256 random bits).
  The window, the tray and `libre-panel editor` give it to the page in the address, after a
  `#` (`/#token=…`), which a browser does not send; `libre-panel quit` and a window that
  starts later read it from `private/editor.json` in the settings folder, which only the
  user can open. A request without it or with another is answered **403**, whatever the
  route, a route that does not exist included; only the app's own files (`/`, `/app/…`,
  `/static/…` and the files of plugins' pages under `/plugins/…`) come without it. An
  `<img>` cannot send a header, so the pictures (`/api/themes/<id>/preview`,
  `/api/themes/<id>/assets/<file>` and the `GET` routes of a plugin's page) also take it as
  `?token=<token>`; no other route does.
- **Reading** is `GET`. **Writing** is `POST`, with a JSON object as the body (`{}` when there
  is nothing to say). The server answers only to the host name it listens on
  (`127.0.0.1:<port>`, `localhost:<port>`), and a browser's request only from a page of the
  app itself (`Origin`).
- An answer is an object with named keys. An error is `{"error": "<text in the user's
  language>"}` with the status **400** (the request is wrong; nothing was written),
  **403** (the access token is missing or wrong), **404** (not there: no picture yet, an
  unknown service, no services in this editor) or **409** (config.toml cannot be read or
  written).
- `background` is true when the running app (the tray, `libre-panel start`) serves the
  editor. In a plain `libre-panel editor` there is no panel, no sources and no running
  service: `panel` is `null`, `sources` is empty, a service's `state` is `unknown`. The
  theme per mode, the switches in config.toml and the log work all the same.
- What a screen changes goes into config.toml, so it is kept, and the running app
  follows within a second or two (services too, also while the panel is paused).
- Polling: `panel` once a second (the numbers are made once a second), `services` and
  `sources` every two, `log` while it is open. Ask for the next `live` picture when the
  last one has come (the panel draws `target_fps` at most). Answers are sent with
  `Cache-Control: no-store`; no cache-buster is needed.

`/api/plugins/control/`:

| Route | Asks | Answers |
|---|---|---|
| `GET control/panel` | | `{background, panel}`; `panel` is `{state, detail, hint, target, theme, default_theme, frames, mode, stats}`. `state` is `starting`, `showing`, `waiting`, `paused`, `error` or `stopped`; `hint` is `""` or one sentence in the user's language about readings that show "--" and what to do (Windows: the CPU's temperature and power need administrator rights and the PawnIO driver, LibreHardwareMonitor did not start, its web server is not running); `mode` is `null` or `"game"`; `theme` is the one on show (a service such as the autopilot may choose it); `default_theme` is the one a `config.toml` without a `theme` shows on this panel (Lumen on a bar, Libre Default elsewhere; `""` until the panel loop has worked it out). `stats` is `{}` while the panel does not run, else `fps`, `target_fps`, `streaming`, `render_ms`, `render_peak_ms` (milliseconds to draw a frame), `kbps` (KB sent per second), `blocks_per_s`, `max_depth`, `waits`, `drops` (the video link), `usb_errors`, `panel_restarts`, `ffmpeg_restarts`, `uptime_s`; `null` is "nothing counts this" |
| `GET control/live` | `w` (160 to 1920, default 960), `format` (`jpeg`, the default, or `png`) | the picture on the panel now (after transitions and messages, the right way up) as that file; 404 before the first frame and while the panel is paused |
| `GET control/sources` | | `{background, sources}`: each `{name, readings, status, details, hint, problem}`: the readings taken from it in the last snapshot, what it says about itself (`status`, or `null`), the same in words (`details`: `[label, value]` pairs), a hint when readings are missing (`hint`, or `null`: the `hint` of `panel`, on the LibreHardwareMonitor row) and whether it needs attention. LibreHardwareMonitor's `status` is `{mode, elevated, ring0, pawnio, error, service}`: `"service"` (the sensor service), `"inprocess"` or `"webserver"`; whether it runs with administrator rights (the service always does); whether the CPU's sensors give real values; the version of the PawnIO driver (`null`: none installed, `""`: unknown); why it could not start or does not answer; why the installed sensor service is not what is read now (`""` when it is, or there is none) |
| `GET control/services` | | `{background, services, config_problem}`: each service `{name, state, detail, enabled, installed, restartable}`. `enabled` is what config.toml says, `state` what runs: `running`, `stopped`, `failed` (`detail` says why), `not-installed`, `off`, `pending` (config.toml says one thing and the app has not followed yet) or `unknown` (this editor runs no services). `restartable` is true for `running`, `stopped` and `failed` |
| `GET control/probes` | | `{probes}`: the kinds of test message, each `{kind, label}` |
| `GET control/themes` | | `{available, normal, game, shown, mode, config_problem}`: the installed themes, the theme for the normal mode (config.toml's, else the panel's own default) and in config.toml for the game mode (`""`: the normal one's), the one on show and the mode now |
| `GET control/log` | `lines` (1 to 1000, default 60) | `{lines, path, exists}`: the end of the log the tray app writes |
| `GET control/status` | | the parts above in one answer, for a page that polls once: `{background, panel, config_problem, services, sources, probes, themes, log}` (`log` is `{path, exists}`) |
| `POST control/probe` | `{kind}` | `{kind, name, shown, reason, message}`: a message of that kind goes to the panel the way a service's does, and the answer says whether it will show. `reason` is `""`, `"theme"` (the theme switches that kind off), `"screen"` (the screen shows the kind itself) or `"no-panel"`; `message` says it in words |
| `POST control/services/enable` | `{service, enabled}` | `{service, enabled, state, detail}`: writes `services.enabled`; the state is `pending` until the app has followed (a moment). Asking twice writes once; an unknown service cannot be switched on (404) |
| `POST control/services/restart` | `{service}` | `{service, state, detail}`: stops the service and starts it again (404 when it is not enabled or this editor runs no services) |
| `POST control/theme` | `{mode, theme}` | `{mode, theme}`: `mode` is `"normal"` or `"game"`; for `"game"` an empty `theme` means the normal one's |

`/api/plugins/autopilot/` (the rules are `[[services.autopilot.rules]]` in config.toml,
[Configuration](CONFIGURATION.md)):

| Route | Asks | Answers |
|---|---|---|
| `GET autopilot/settings` | | `{background, enabled, state, detail, status, options, rules, problems, themes, transitions}`: whether the service is switched on, its settings and its rules, what is wrong with each rule (`problems`, `""` when nothing), the installed themes and the transitions it may use |
| `POST autopilot/settings` | `{enabled, options, rules}`, each optional | the same as `GET`. Every rule is checked first; one that is wrong refuses everything (400, `"Rule <name>: ..."`). Changes reach the file in one write, and rules that did not change keep their comments |
| `GET autopilot/status` | | `{background, enabled, state, detail, status}`: `state` as for a service; `status` is what the service does now, `{rule, theme, since_s, candidate, resting, signals}` (`null` while it does not run) |

## The settings routes

The app's *Settings* talk to the editor's server (`editor/settings.py`) through these
routes. The rules of the console API above hold: every request needs the access token in
the header `X-Libre-Panel`; an error is `{"error": "<text>"}` with **400** (the request is
wrong), **403** (Libre Panel runs with administrator rights, and the setting names a program
or a file, see `/api/settings`), **409** (config.toml cannot be read, or the change would
break it), **501** (a folder that cannot be opened here) or **502** (the place search cannot
be reached); nothing is written on an error.

| Route | Methods | What it is |
|---|---|---|
| `/api/settings` | GET, POST | `GET` gives `{config, catalog, restart}`: config.toml's values with the defaults filled in (`device`, `video`, `sensors.providers`, `sensors.librehardwaremonitor`, `refresh_ms`, `weather`, `services.enabled` and `services.options`, `language`); the `catalog` to choose from (the sources, the drivers, the services with the `options` each declares); and `restart`, the written settings that wait for the panel to start again (`sensors.*` and `weather.*`, which are made when it starts; a new start of the panel loop clears them). `POST {"set": {"device.brightness": 70, "refresh_ms": null}}` writes any number of settings in one go, each by its name in config.toml; `null` takes a setting out where that is a thing (`refresh_ms`, `weather.latitude`, `weather.longitude`, `weather.place`, any option of a service). Each setting has a rule (type, range, the choices; `psutil` alone is refused, since config.py reads it as the usual list), and one that fails refuses them all. The answer is that of `GET`. **While Libre Panel runs with administrator rights (root on Linux) a setting that names a program, a library or a file is not changed** (403, "set it in config.toml"): `video.ffmpeg`, `device.output`, and an option of a service whose name says it is a path (`database`, `fancontrol`, `folder`, `steam_path` and the like); saying what it is already is no change. Anything else is written as before |
| `/api/panel` | GET | the panel on the bus, looked for without opening it (`found`), the one the settings go by (`model`: the chosen one, else the found one), `configured` (`"auto"` or a model), `running` (what the app says of the panel) and the video mode `{mode, fps, on, usb, gaps}`, where `gaps` are what it still needs: `clip`, `ffmpeg`, `ffmpeg-path`, or `model` (not proven on this panel) |
| `/api/system` | GET, POST | starting with the system: `{kind, enabled, elevated, can_elevate, location, command, program, risky, language}`. `POST {"autostart": true}` or `{"elevated": true}` do what `libre-panel autostart enable` does; the highest rights only for an administrator (409 and the reason otherwise) |
| `/api/unlock` | GET, POST | the sensor service (Windows): `{platform, available, service, pawnio, frame_rates, job, command}`: whether this download can set it up (`available`), the service as Windows has it (`null`: not installed, else `{state, disabled}`), the PawnIO driver's version (`null`: none), whether this account may record game frame rates (`"yes"`, `"sign-in"`: from the next sign-in, `"no"`), how the last run went (`{action, running, code, cancelled, error}`, or `null`) and the command that does the same in a terminal. `POST {"action": "unlock"}` (or `"remove"`) runs `libre-panel system-setup` with Windows' question for administrator rights and answers at once; 409 where this is no Windows download |
| `/api/location` | POST | `{"query": "name"}` gives up to six places `{name, latitude, longitude}` from Open-Meteo's place search, the coordinates kept to about a kilometre. It is the one route that goes out to the internet, and only when the person asks |
| `/api/about` | GET | `{version, license, notice, links, doctor, folders}`; `notice` is NOTICE.md, of which the package keeps a copy in `assets/` (a test makes sure it is the same) |
| `/api/open` | POST | `{"folder": "config"}` (or `themes`, `logs`) shows that folder in the file manager |
