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
| Editor page | `libre_panel.editor_pages` | a page of the plugin's own in the app |
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

**As a folder**, for the downloads (Windows setup, AppImage),
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
| `host.publish(key, value, unit="", label="")` | a reading, shown by themes like any sensor (graphs keep its history); `host.unpublish(key)`. It reaches the next frame |
| `host.publish_state(key, value, label="")` | a dict or a list (`None`: none) for screens and services: kept as JSON text with a time stamp, never smoothed, never in a graph's history; read with `snapshot.state(key, default)` (see *States*) |
| `host.publish_image(key, image)` | an image (PIL) for screens and widgets, e.g. `media.cover`; `None` removes it |
| `host.snapshot()` | the readings and history the panel was last drawn from |
| `host.want(*keys)` | the readings the service reads (`"media.*"`, `"audio.volume"`): sources that work on demand run for it although no theme shows them |
| `host.show_theme(name, transition=None, priority=0, mode=ANY_MODE)` / `host.restore_theme(transition=None)` | show another theme for a while; `config.toml` stays as it is. `transition`: `cut`, `fade`, `slide`, a plugin's, or `(name, {parameters})`; default from `config.toml`. `mode=None`: only while no mode is on; `mode="game"`: only in that mode |
| `host.set_mode(name, transition=None)` / `host.mode` | switch to a mode from `[modes.<name>]`; `None` ends it. A transition plays also when the theme stays |
| `host.notify(text, icon=None, level="info", seconds=None, kind="", rank=0, payload=None, key="")` | a short message on the panel (see *Toasts*). It returns a `Delivery`: true when the message will show, else `reason` says why (`"theme"`, `"screen"`, `"no-panel"`) |
| `host.on(event, callback)` | `panel-connected`, `panel-lost`, `panel-restarted` (after `panel-connected`, when the panel came back from a restart that cleared a hung video decoder, not from a replug), `theme-changed` (`theme=`), `mode-changed` (`mode=`), `sample` (`snapshot=`) and `fresh` (`snapshot=`, `keys=`), see below, `quit`; callbacks run in an event thread |
| `host.data_dir` | `<settings>/plugins-data/<service>/` for the service's files |
| `host.log` | a logger named after the service |

All calls are thread-safe. Which theme is shown: one a service asked for,
else the active mode's theme, else the one in `config.toml`. When several
services ask, the highest `priority` wins, and among equals the last to ask;
`restore_theme` takes back only the caller's own request, so the one below
shows again (a round report at priority 10 over an autopilot at 0). A
request made for one mode (`mode=None` for normal mode, as an autopilot's)
rests while another mode is on and counts again when that mode comes back,
without the service doing anything: nothing races the switch. A theme that
does not exist is refused once with a warning; the panel keeps the current
one.

When a service stops (Quit, or it was removed from `[services]`), what it
left goes too: its readings and images disappear, a theme or mode it set
ends, what it asked for with `host.want` is no longer asked for, and its
listeners hear nothing more. `quit` arrives before the services stop.

### Reacting to readings: `host.on("sample", callback)`

Whatever reacts to readings (a notice for the volume, a rule that picks a
screen) should not poll. The main loop emits `sample` after every new
snapshot, about every `refresh_ms` of the theme, with the snapshot:

```python
def start(self):
    self.host.on("sample", self.on_sample)


def on_sample(self, snapshot):
    temperature = snapshot.value("cpu.temp")
    if temperature is not None and temperature > 90:
        self.host.notify("CPU is hot", level="warning", kind="warning")
```

- The callback runs in a thread for samples alone, so a slow one holds up no
  other event. If it takes longer than a sample interval it is handed the
  newest snapshot when it is free again, not every one in between. Keep it
  short; do slow work (a process list, a file) in a thread of your own.
- A callback that raises is logged, once for every new error text, and does
  not stop the others.
- Readings from sources that read on every frame (the volume) are brought up
  to date between samples; a `sample` callback sees them as of the sample.
  To hear of a change when it happens, listen to `fresh` as well (below).
- Readings you publish reach the next frame, not only the next sample. A
  `set_mode` or `show_theme` right after a `publish` is therefore drawn with
  the value already in; nothing needs to wait for a sample. (Images reach the
  snapshot with the next sample; pass an image a transition needs to it as a
  parameter instead of its key.)

A source that follows on every frame (`every_frame`: the volume, the fast load,
what services publish) can change between two samples. The main loop then emits
`fresh` on the frame the change shows in, with the snapshot (its readings
already up to date) and `keys`, the readings that changed:

```python
def start(self):
    self.host.on("sample", self.on_sample)
    self.host.on("fresh", self.on_fresh)


def on_fresh(self, snapshot, keys):
    if "audio.volume" in keys:  # the volume moved, say so within a frame or two
        self.on_volume(snapshot.value("audio.volume"))
```

- Nothing is emitted while the readings stand still, so listening costs nothing
  between changes. A `fresh` callback only hears readings that follow on every
  frame; a limit on a temperature is for `sample`.
- It runs in the thread of `sample`, one callback after the other, never two
  at once. A slow callback is handed the newest snapshot when it is free again,
  with the keys that changed meanwhile.

### States: `host.publish_state(key, value)`

A reading is a number or a text. Structured data (a list of fans, a report)
goes as a state: a dict or a list, kept as JSON text together with the time it
was published. It is not smoothed and not part of a graph's history; screens,
widget types, transitions, toast styles and other services read it back:

```python
# in the service
self.host.publish_state("myplugin.round", {"name": "Space Game", "minutes": 42})

# in a screen or widget type: render(snapshot, now) / draw(widget, ctx, snapshot, now)
round_ = snapshot.state("myplugin.round")  # a new dict; None if there is none
age = now - snapshot.state_stamp("myplugin.round")  # seconds since it was published
# in a toast style or transition: self.context.state("myplugin.round", default)
```

- `snapshot.state(key, default=None)` gives the default when nothing was
  published, the state is `null` (`publish_state(key, None)`) or the text is
  no JSON. Every call parses the text again, so what you get is yours to
  change; read it once per frame.
- The time stamp is on the main loop's clock, `libre_panel.clock()`
  (`time.perf_counter()`), the clock of the `now` a screen is drawn with.
  `PluginHost(clock=...)` sets the clock for tests.
- Only a dict, a list or `None` is a state; a number or a text goes through
  `publish`. A value that is not JSON raises `TypeError` in the service.
- A state reaches the next frame, like a reading, and goes when the service
  stops.

### Sources that work on demand: `host.want`

Sources that cost something read only what a theme on the panel shows: what
is playing (`media.*`), the volume (`audio.*`), the CPU and GPU load, game
frame rates (`game.*`). A service that reads such a key itself says so, once,
with `host.want("media.*", "audio.volume")`; the source then runs for as long
as the service does. A key a source does not know is harmless.

### Built-in services

Libre Panel brings services of its own. They are not plugins and need no
package: they are in the app, listed by `libre-panel plugins` as "built in", and
switched on like any service with `[services] enabled`. All are off until you
do. Their names cannot be used by a plugin.

| Service | What it does |
|---|---|
| `notices` | messages for the volume, a new track, sensors that cross a limit and a panel that restarted |
| `game` | game mode: finds the game you play (a list of programs, Steam's library) and switches the mode with an entrance, and back |
| `autopilot` | picks the theme by rules: music, microphone, load, program, time of day |
| `devices` | messages for drives, USB devices and controllers that come and go, a full drive and a hot SSD (Windows) |
| `memory` | keeps minutes, games and the cooling state in a database on this PC, and shows a round report after a game |
| `cooling` | checks the fan curves of FanControl against what the PC did and says what to change; answers an editor page or a script, reads only |
| `downloads` | notes what is downloading (Steam's games and updates, a browser's part files) with progress, speed and time left, as a state for a download card |

Their options are in the [configuration guide](CONFIGURATION.md#built-in-services).
They talk to the panel only through the host described here, so they are also
examples for your own.

### The round report

When a game that was played long enough ends, the `memory` service publishes the
round against the last ones of the same program: as the state `report` (a
screen reads it with `snapshot.state("report")`, `None` when none is on show),
and as plain readings for a theme made of the usual widgets (the built-in theme
`round-report` is one). Both stay for `report_seconds`, or until a new game
begins; the readings go 3 s after the state, so that the theme still has its
numbers while it leaves with a transition.

```json
{
  "session": {
    "id": 1760000000123, "game": "Space Game", "program": "spacegame",
    "started": 1760000000, "ended": 1760002700, "duration_s": 2700,
    "fps_s": 2610, "fps_mean": 148.3, "fps_p01": 104.0, "fps_low_mean": 101.0,
    "frametime_mean": 6.7,
    "levels": {"edges": [60, 90, 120, 144, 165], "seconds": [26, 52, 235, 1150, 1040, 107]},
    "limit_cpu_s": 160, "limit_gpu_s": 2440, "limit_cap_s": 10,
    "cpu_temp_max": 74.1, "gpu_temp_max": 56.0, "gpu_hotspot_max": 80.0,
    "cpu_power_max": 96.0, "gpu_power_max": 327.0, "vram_used_max": 14.8,
    "outside_temp": 12.6, "closed": 1
  },
  "comparison": {
    "count": 7,
    "mean": {"fps_mean": 142.1, "fps_p01": 101.2, "fps_low_mean": 99.0, "frametime_mean": 6.9,
             "cpu_temp_max": 72.4, "gpu_temp_max": 57.1, "gpu_hotspot_max": 78.0,
             "cpu_power_max": 94.0, "gpu_power_max": 318.0, "vram_used_max": 14.5},
    "levels": [0.02, 0.03, 0.12, 0.40, 0.36, 0.07]
  },
  "seconds": 30,
  "until": 1760002730.4
}
```

| Field | |
|---|---|
| `session.game`, `program` | the game's name (`game.name`), and the program PresentMon counted, in lower case without `.exe` (`""` if it was never seen) |
| `started`, `ended`, `duration_s` | Unix seconds; how long the game ran |
| `fps_s` | the seconds that have a frame rate |
| `fps_mean`, `fps_p01`, `fps_low_mean`, `frametime_mean` | the mean of the per-second frame rates; the rate that 1 % of the seconds fell below; the mean of PresentMon's 1 % low; the mean frame time in ms |
| `levels` | `seconds` in each range of frame rates: below `edges[0]`, between two edges, above the last (one more than there are edges; the `fps_levels` option) |
| `limit_cpu_s`, `limit_gpu_s`, `limit_cap_s` | the seconds `game.limiter` said the CPU, the GPU or a frame limit held the rate down |
| `*_max` | the highest value of the round: °C, W, GB (`gpu_hotspot_max` where the card has a hot spot); `null` where there is no such reading |
| `outside_temp` | °C, from the weather, `null` without |
| `comparison.count` | how many earlier rounds are in it (0: the first round) |
| `comparison.mean` | the same numbers, averaged over those rounds (`null` where none had it) |
| `comparison.levels` | the mean share of the time in each range (they add up to 1); `null` without earlier rounds, or when the ranges were another ones |
| `seconds`, `until` | how long the report is shown, and when it ends (Unix time) |

The readings (a value may be `None`; labels and texts are in the language of
the panel):

| Key | Value |
|---|---|
| `report.title`, `report.game`, `report.when`, `report.versus` | texts: "Round report", the game, "21:03 – 21:48", "Compared with the last 7 rounds" |
| `report.length`, `report.outside` | s, °C |
| `report.fps`, `report.low`, `report.frametime` | the means: fps, fps, ms |
| `report.fps_delta`, `report.low_delta`, `report.frametime_delta` | this round minus the mean of the earlier ones |
| `report.count` | how many earlier rounds the comparison is made of (0: the first round) |
| `report.limit` | text: who held the rate down, by share, "GPU 94 %  CPU 6 %" |
| `report.limit.gpu`, `report.limit.cpu`, `report.limit.cap` | the same shares as numbers in percent, for a bar in their colours (no value for one that held the rate down for under half a percent of that time, as in the text) |
| `report.cpu_temp`, `report.gpu_temp`, `report.gpu_hotspot`, `report.cpu_power`, `report.gpu_power`, `report.vram_used` | the highest values: °C, W, GB |
| `report.cpu_temp_mean`, ..., `report.cpu_temp_delta`, ... | for each of those: the mean of the earlier rounds, and this round minus it |
| `report.level.0` ... | the share of the round in each range of frame rates in percent, the slowest range first; the label is the range ("< 60", "60–90", "165+") |
| `report.level.0.mean` ... | the mean share of the earlier rounds in each range (no value for the first round, or when the ranges were other ones) |
| `report.head.levels`, `report.head.peaks` | headings for a chart and a list; with earlier rounds the first says what the tick on each bar of the chart is ("Share of the round · tick: average") |

Where there is nothing to be against (the first round of a game) the mean and
delta readings have no value: a theme shows "--" or hides them with `needs`.

The game's logo is the picture `game.logo` (an image widget's `"src": "@game.logo"`):
the `game` service keeps it after the game ends, until the next game, so the report
can show it beside the name. A `bar` can draw the mean of a range as a tick across it
with `mark` (`"mark": "report.level.3.mean"`).

The service also has `show_last_report()` (builds and shows the latest round
that was long enough again; for a console page) and `status()` (is it
recording, the file, how many rows were written, the last error, the game now
played).

### The round under way

While a game is played the `memory` service also publishes what the round has come to so
far, once a second, as readings (a screen made of the usual widgets, such as the built-in
`spur-session`, shows them; they are gone when the game's name goes):

| Key | Value |
|---|---|
| `session.time` | s: how long the round has been played |
| `session.fps` | fps: the mean of the per-second frame rates so far (no value before the first) |
| `session.level.0` ... | the share of the round in each range of frame rates in percent, the slowest range first; the label is the range ("< 60", "60–90", "165+"), the ranges are the `fps_levels` option |
| `session.cpu_temp_max`, `session.gpu_temp_max`, `session.gpu_hotspot_max`, `session.cpu_power_max`, `session.gpu_power_max`, `session.vram_used_max` | the highest value since the round began: °C, W, GB (no value for one that nothing has told) |

They are what the round's row in the database will say when it ends, and what the
`report.*` readings call the round's own numbers (`report.cpu_temp`, `report.level.0` ...).

### The cooling analysis

The `cooling` service shows nothing by itself; an editor page (or a script)
asks it. A page gets the running service with `context.service("cooling")`
(`None` where it does not run), then calls, from any thread:

| Call | Answers |
|---|---|
| `analysis(days=1.0, mode=None, points=480)` | everything a page draws, as plain data (below), and the report's lines in `text`. `days`: the last this many days (`None`: all); `mode`: `"game"`, `"desktop"` or `None` for both; `points`: how many points the course (`history`) has |
| `recommendations()` | what to change in FanControl, from the data since it was last changed (below) |
| `report(days=None, mode=None)` | the plain-text report as a list of lines: what was recorded, each fan, what is notable, the trend and the recommendations |
| `status()` | `{"database", "database_found", "fancontrol", "curves", "fans"}`: what it reads and what it found |

All of it is JSON-able. The texts (`title`, `why`, `findings`, the report) are
in the language of the panel, English or German. An answer is kept for 5
minutes, and until FanControl's settings or the language change (the same
object comes back: do not change it), so a page may ask often. The first answer
for a long period of a busy PC takes some seconds (60 days of games, 150 000
rows: about 5 s for `recommendations()`, twice that for `analysis(days=60)`);
the default day takes a fraction of a second. Nothing is
written, neither to the memory's file nor to FanControl. Without data
`analysis()` and `recommendations()` answer `{"empty": true, "reason": "..."}`
and `report()` with one line that says so.

`recommendations()`:

```json
{
  "empty": false,
  "since": 1759990000, "changed": true,
  "basis": "50 min game (1 round), 2.5 h desktop",
  "list": [
    {
      "id": "source:Gpu_Curve", "number": 1, "curve": "Gpu_Curve",
      "title": "Tie Gpu Fan to the graphics card",
      "why": "The curve Gpu_Curve reads /gpu-amd/0/temperature/0. On the desktop it sees 52 °C, the graphics card is at 28 °C: it follows another device. ...",
      "do": ["Open the curve “Gpu_Curve” in FanControl.", "Choose “GPU Core” of “Graphics Card” as the temperature source: ...", "Set the curve points: 32 °C → 22 %, 54 °C → 82 %, ..."],
      "effect": ["Desktop: 22 % instead of 33 % (≈ 692 RPM)", "Game: practically unchanged (82 % → 82 %)"],
      "note": "The points leave the fan at its base value on the desktop ...",
      "confidence": "certain (source) · preliminary (points)",
      "basis": "50 min game (1 round), 2.5 h desktop",
      "proposal": [[32, 22], [54, 82], [70, 85], [78, 100]]
    },
    {
      "id": "mix:Mix_Case", "number": 2, "curve": "Mix_Case", "proposal": null,
      "after": "source:Gpu_Curve", "after_number": 1,
      "title": "...", "why": "...", "do": ["..."], "effect": ["..."], "note": "...",
      "confidence": "preliminary", "basis": "..."
    }
  ],
  "checked": ["Cpu_Curve follows the right device (+0 K)."],
  "proposals": {"Gpu_Curve": [[32, 22], [54, 82], [70, 85], [78, 100]]},
  "change": {"ts": 1759990000, "what": ["Gpu_Curve: sensor /gpu-amd/0/temperature/0 → /gpu-amd/5/temperature/0"]},
  "effect": [
    {"mode": "game", "before_s": 3000, "after_s": 3600,
     "rows": [{"quantity": "Case Fan", "unit": "%", "before": 36.0, "after": 52.4}]}
  ],
  "computed": 1760086400,
  "changes": [1759990000],
  "waiting": "Since the change at 2025-10-09 07:26 only 6 min of data: ..."
}
```

| Field | |
|---|---|
| `list[].id` | the rule and what it is about: `source:<curve>` (the curve reads the sensor of another device), `mix:<curve>` (a mix does not pass on the higher part), `warm:<reading>` (over its limit in a game), `oscillation:<curve>`, `quieter:<curve>` (a fan runs higher on the desktop than it needs to) and `trend` (cooling gets worse over weeks) |
| `list[].number`, `after`, `after_number` | the order to work in; a recommendation with `after` makes sense only once the one it names is done |
| `list[].why`, `do`, `effect`, `note` | the reason; the steps, in FanControl; what changes (duty, and speed where it was measured); a remark |
| `list[].confidence`, `basis` | how certain it is, in words ("reliable", "preliminary", "certain (source)" where the sensor is known), and which data it rests on |
| `list[].proposal` | curve points `[[°C, %], ...]` for the curve, or `null` |
| `checked` | what was looked at and found fine, or not yet judged, one line each |
| `since` | the data is from here on: the last change in FanControl, else the first row |
| `change` | `ts` and `what`: the last change in FanControl, in words (`null` if it was never changed) |
| `effect` | before and after that change, per `mode` (`"game"`, `"desktop"`): medians of each fan's duty and of the temperatures, over `before_s` and `after_s` seconds; only where both sides have enough data |
| `waiting` | present right after a change, while there is not yet enough data after it |
| `changes` | the times of all changes to FanControl the memory has seen |

`analysis()` has, beside `advice` (the `recommendations()` above) and `text`
(the report's lines):

```json
{
  "empty": false,
  "from": 1759990000, "to": 1760086400, "seconds": 86400, "rows": 2880,
  "states": [{"name": "Desktop", "seconds": 61200}, {"name": "Space Game", "seconds": 25200}],
  "sessions": [{"name": "Space Game", "from": 1760040000, "to": 1760065200}],
  "series": [{"curve": "Cpu_Curve", "name": "Cpu Fan", "fans": ["Cpu Fan"], "device": "cpu"}],
  "history": {
    "t": [1759990900, 1759992700],
    "temps": {"cpu_temp": [55.1, 56.0], "gpu_temp": [28.1, null], "gpu_hotspot": [36.1, null]},
    "duty": {"Cpu_Curve": [25.4, 26.1]}
  },
  "plots": [
    {"curve": "Cpu_Curve", "name": "Cpu Fan", "device": "cpu",
     "target": [[50, 22], [62, 30], [72, 42]], "x_name": "Curve input (measured)", "measured": true,
     "cloud": [[72.0, 42.0, 1800], [55.0, 26.0, 900]], "note": null, "proposal": null}
  ],
  "table": [
    {"fan": "Cpu Fan", "curve": "Cpu_Curve", "p50": 26.0, "p95": 42.0, "max": 42.0,
     "minimum_share": 0.0, "rpm50": 613.0, "rpm_max": 804.0, "characteristic": [[20, 606], [40, 804]]}
  ],
  "findings": [
    {"level": "ok", "text": "Cpu Fan follows the curve Cpu_Curve: 0.0 points off on average, p95 0.0: ..."},
    {"level": "info", "text": "Unused curve points and oscillation are judged from 6 h of recording: so far 3.3 h."}
  ],
  "text": ["RECORDED  3.3 h in 400 rows, from ...", "..."]
}
```

| Field | |
|---|---|
| `from`, `to`, `seconds`, `rows` | the period of the rows used (Unix seconds), the time they hold, and how many there are |
| `states`, `sessions` | where the time went (`Desktop`, each game by name; the desktop first), and each game round with its `from` and `to` |
| `series` | the fans per curve that drives them; `device`: `"cpu"`, `"gpu"`, `"mix"` or `"other"` (by the sensor the curve reads) |
| `history` | the course in `points` steps (`t`: the middle of each, Unix seconds): the temperatures (`cpu_temp`, `gpu_temp`, `gpu_hotspot`, and the other readings the memory keeps, by their keys) and the duty of each series by its curve; `null` where there is no data |
| `plots[]` | a curve against what the fans did. `target`: the curve's points `[[°C, %], ...]` (for a mix, those of its CPU or GPU part, the `note` says so); `cloud`: `[input, duty, seconds]`, the most time first, at most 800; `x_name`: what the input is; `measured`: whether it is the temperature the curve really read (kept by the memory) or the sensor of the device; `proposal`: the points a recommendation would set, where they fit this input |
| `table[]` | per fan: duty median, 95th percentile and highest (`p50`, `p95`, `max`, %), the share of the time at the minimum, speed median and highest (RPM), and the speed at each duty `characteristic: [[duty %, RPM], ...]` |
| `findings[]` | what the numbers say by themselves: `level` (`"ok"`, `"warn"` or `"info"`), `text` and sometimes the `id` of the recommendation it belongs to; one that a recommendation already covers is left out |

How a curve's real input is found when FanControl's own reading was not kept:
the duty a fan had is put back through the curve (the rising part, where it
can be turned around) and gives the temperature the curve must have seen; the
recorded sensor that lies on top of it, or follows it with a fixed offset, is
what the curve reads. A course that moved less than 5 K proves nothing, and the
answer says so (`too quiet`) instead of guessing.

### The downloads state

While something downloads, the `downloads` service publishes the download that
matters most as the state `downloads`, once a second (a screen reads it with
`snapshot.state("downloads")`; it is `None` when nothing downloads, and a
download that is finished shows for 6 s with the phase `done`, if no other is
going):

```json
{
  "id": "steam:777", "source": "steam", "name": "Space Game", "phase": "downloading",
  "bytes": 1041943000, "total": 2000000000,
  "rate": 41943040, "remaining_s": 22.8, "elapsed_s": 80.0,
  "estimated": false,
  "history": [0, 20971520, 31457280, 36700160, 39321600],
  "more": 1
}
```

| Field | |
|---|---|
| `id` | `steam:<app number>` or `browser:<number>` (counted from the start of the service); it stays the same while the download goes on, also when a browser renames its part file |
| `source`, `name` | `"steam"` or `"browser"`; the game's name, or the file's name |
| `phase` | `downloading`, `preparing` (Steam makes room), `paused`, `installing` (Steam checks and puts together what it loaded) or `done` (for 6 s) |
| `bytes`, `total` | loaded so far, and the whole; `total` is `null` for a browser download, which does not tell |
| `rate` | bytes per second, smoothed over about two seconds: the network's download for Steam, the growth of the file for a browser; 0 when it is not downloading |
| `remaining_s` | seconds left; only for Steam, while it downloads with a known size at more than 0.2 MB/s, else `null` |
| `elapsed_s` | seconds since it began |
| `estimated` | `true` when `bytes` is worked out from the speed because Steam's manifest has not moved for 10 s |
| `history` | `rate` of the last (at most 180) seconds, the oldest first, for a graph |
| `more` | how many other downloads run beside this one |

The one that matters most is the one still going that is fastest. Nothing below
`min_mb` is in it; the browser's history is never read; see the
[configuration guide](CONFIGURATION.md#downloads).

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
  services) are there, and `snapshot.state(key)` gives a dict or list a
  service published (see *States*); `now` is the frame's time in seconds for animations
  (steady; in video mode the time the frame is planned for, so a frame that
  starts a little late does not make an animation stutter). It is a time on
  `libre_panel.clock()` (`time.perf_counter`): compare with that clock, not
  with `time.monotonic`. `snapshot.now` is the same moment as a wall-clock
  `datetime`, so a seconds clock ticks on the frame that is due.
- `context` has `size`, `orientation`, `palette`, `model`,
  `hidden_edges()` (pixels the bezel hides, as data: nothing is cropped),
  `font(ref, size)` (a Pillow font that takes a character it lacks from a font of the
  computer, see the fonts in [THEMES.md](THEMES.md#themejson)), `color(value)`,
  `fps`, `language`, `t(text)`,
  `supersample`/`downsample()`, `image(key)` (a picture a service
  published, e.g. `media.cover`, or None), `state(key, default)` (a dict or
  list a service published) and `assets`, the plugin's own folder for
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
  on it. `context.toast` is the toast on the panel and its age, or None: a
  screen can react the moment one begins.
- `context.continuous` is true in video mode, where every frame reaches the
  panel; `context.progress(now)` says how far `now` is from the last reading
  to the next (0 to 1). Move a curve by that part of a sample width and it
  scrolls between readings.
- `__init__` also runs for every preview in the editor, which then calls
  `close()`: keep expensive loading in a module-level cache.
- `prepare()` (optional) runs once in a thread of its own before the
  screen's first frame on the panel: at start, and when its theme is about
  to come on, while the panel still shows the theme before (the switch
  waits for it, up to two seconds). Load what the first frames need there
  (pre-rendered layers, caches), so the first frame does not stall a video.
  `render` must work without it too: previews do not call it.
- Design offers installed screens in the theme's *Fine-tuning* (select nothing,
  then *Fine-tuning → screen*), with their options.

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

`host.notify` shows a short message on the panel. Two looks are built in:
the card, in the theme's colours with a coloured edge for the level
(`info`, `warning`, `error`, or `payload["color"]`) and one of the built-in
icons (`cpu`, `gpu`, `temperature`, `fan`, …) if named; and the band
(`"toast": {"style": "band"}`), a band across the panel after SPUR II: a
glowing edge ignites from the centre and the band rolls down from it. Both
keep clear of what the bezel hides. Toasts are part of the frame, also in
video mode (a separate overlay would make the video judder).

- Messages show one after the other, each for its `seconds` (None: the
  theme's hold time). A higher `rank` replaces the one on show at once; the
  others wait, by rank and then in order. With the theme's `"queue": false`
  they are decided one by one as they come: the same or a higher rank
  takes over at once, a lower one is dropped (a warning and the volume in
  the same moment: the warning takes over).
- A message with the `key` of the one on show (default: its `kind`)
  replaces it in place, whatever its rank: turning the volume from 30 to 34
  refreshes one toast. Waiting ones with that key collapse to the newest.
- `kind` says what a message is about (`"music"`, `"volume"`, `"device"`,
  …). A theme switches kinds off with `"toast": {"off": ["music"]}`, and a
  screen leaves out those it shows anyway with `suppresses = {"music"}`. A
  message of such a kind that comes is dropped; one already on show when
  such a screen comes goes on unseen (it still holds back lower ranks) and
  shows again when the screen goes, on its own clock.
- `payload` carries what a style draws besides the text, for example
  `{"image": cover, "progress": 0.4, "color": "#EF4444"}`.
- While a transition plays, new toasts wait, unless the transition says
  otherwise (see *Transitions*).
- `host.notify` answers with a `Delivery`, so a service (or the editor's
  *Control* page, which sends a test message of each kind) can tell whether the
  message will show. `delivery.shown` (also its truth value) is True when
  nothing stands in the way; otherwise `delivery.reason` is `"theme"` (the
  theme switches the kind off), `"screen"` (the screen shows it itself) or
  `"no-panel"` (the panel is not connected or paused: the message waits for
  it). It is the answer of the moment: a theme or screen that comes before the
  message is due changes it, and a message of a higher rank can still take its
  place. A message that will not show is handed over all the same.

What the band draws besides `text` (other styles may read the same keys):

| | |
|---|---|
| `payload["label"]` | a title above the text: `"Volume"`, `"Now playing"` |
| `payload["progress"]` | 0 to 1: a bar that glides to its value. Rising levels for `kind="volume"`, a line under the text for `kind="music"` and on narrow panels, segments otherwise |
| `payload["color"]` | the accent instead of the level's: `"#EF4444"` or `"@net"` |
| `payload["image"]` | a picture in a tile on the left, e.g. a cover: a PIL image, or the key of a published one (`"media.cover"`) |
| `payload["badge"]` | a small picture on the tile's corner, e.g. the icon of the app that plays |
| `icon` | one of the built-in icons, in the tile when there is no picture |

The theme's options for the band are `roll_in` (0.45 s) and `roll_out`
(0.32 s); the hold time is the theme's `seconds`.

The theme decides the rest (`"toast": {"anchor": "bottom-right",
"seconds": 5, "style": "myplugin.band", "options": {...}}`; the app has the
style under *Look → Notices* and the rest under *Fine-tuning → Messages*). A toast
style draws them its own way:

```python
from libre_panel.plugins import ToastStyle


class Band(ToastStyle):
    name = "myplugin.band"
    api = 1
    label = {"en": "Band", "de": "Band"}
    options = {"height": ("int", 96)}  # field kinds as for screens
    leave_s = 0.26  # seconds to leave after the hold time

    def draw(self, frame, toast, age, previous=None):
        # age: 0 when it arrives ... lifetime() (by default toast.seconds +
        # leave_s) when it is gone; previous: (the toast this one replaced
        # at once, its age), or None
        out = frame.copy()
        ...  # roll in (or change the content in place), draw toast.text, ...
        return out
```

`self.context` is the theme's render context (palette, fonts, size,
`hidden_edges()`, `image(key)`), `self.anchor` the theme's toast position.
`draw` runs at up to 50 fps: keep what does not change. A style that fails
leaves the frame as it is; one that is not installed falls back to the
card.

Two more methods let a style decide its timing:

- `lifetime(toast, previous)`: the seconds from its arrival until the toast
  is gone, asked once when it starts (default `toast.seconds + leave_s`).
  A band that changes in place instead of rolling in again ends sooner
  than one that comes afresh, and says so here.
- `hold_s(toast, theme_seconds)`: how long a toast without its own
  `seconds` holds, asked before anything else sees it (default: the
  theme's hold time), e.g. a hold time from the style's own design.

The style instance stays across theme switches while the theme names the
same style with the same options at the same size, so what it keeps per
toast goes on (a band does not roll in again on the next screen);
`self.context` then becomes the new theme's.

## Transitions

When the theme changes (a service's `show_theme`, a mode, the editor, the
tray), the panel blends from the old frame to the new one. `transition`
in `config.toml` chooses how (`fade` by default; `cut`, `slide`, `tube`),
and a service can choose per change, also for a mode change that keeps the
theme (`set_mode("game", transition=...)`).

Two are after SPUR II, drawn with light in the theme's accent; from one
frame to the next only the rows under a moving edge and those just
revealed change, which keeps them cheap for a panel's video encoder:

- `tube` (0.9 s): a line ignites at mid-height and opens into two edges;
  between them the new screen is already live.
- `entrance` (3.2 s), for a mode such as a game: the old frame closes to a
  line, a band opens with a logo or a title while a text types in below
  it, then the band closes and opens onto the new screen. Parameters:
  `title`, `text`, `logo` (a PIL image, or the key of one the service
  published) and `kind`: `"start"` or `"end"` for a text of its own ("Game
  mode", "Game ended") when there is no `text`. Toasts are not drawn over
  it; the one on show rolls in afresh as it ends. A screen asked for
  meanwhile (a round report) is revealed by it; when the mode turns back
  (the game ends during its entrance) the next one starts from the frame
  on show.

```python
# in a game-mode service:
self.host.publish_image("game.logo", logo)
entrance = {"title": game, "logo": "game.logo", "kind": "start"}
self.host.set_mode("game", transition=("entrance", entrance))
...
self.host.set_mode(None, transition=("entrance", {"title": game, "kind": "end"}))
```

A plugin can add its own:

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

A transition says how it gets along with what happens while it plays:

| Attribute | Values |
|---|---|
| `switch` | `"wait"` (default): a switch waits until it is over, so a report after a game's end does not cut the entrance short. `"follow"`: the new theme shows at once and the transition keeps playing, uncovering it. `"restart"`: the switch's transition starts again from the frame on the panel. A mode change that brings a transition of its own never waits behind a following one: that transition starts from the frame on the panel. |
| `on_switch(theme, mode, mode_changed)` | decides case by case instead of `switch` (its default), asked on every frame it plays with the theme the panel should show and the mode on now; `mode_changed` is True when that is not the mode it began in. The entrance follows a report in its own mode and starts again when the mode turns back. |
| `toasts` | `"wait"` (default): the toast on show stays, new ones wait. `"over"`: toasts go on over the transition. `"restart"`: the toast on show leaves and comes again afterwards. `"hide"`: toasts are decided as usual (screens see them in `context.toast`) but none is drawn; the one on show rolls in afresh on the frame the transition ends. |
| `from_shown` | `True`: `old` is the last frame as it showed, with its toast (the band belongs to the old screen). |

Between themes of different sizes (landscape to portrait) the panel switches
without a transition.

## Editor pages

A plugin can bring its own page into the app, for settings or an analysis. It is
listed under *Pages → From plugins* in *Design* and found by Ctrl+K, and takes the
place of the view until *Back* returns to it.

Libre Panel brings two pages of its own, built in like its services (so they
work in the downloads, and no plugin can use their names): **Control** (`control`)
and **Autopilot** (`autopilot`), described in
[Running in the background](BACKGROUND.md#the-control-and-autopilot-pages). They
are written with the same API, from `libre_panel/pages/`, and a good model for
a page of your own. Their routes are a JSON API that a screen of any design can
call: [The console API](ARCHITECTURE.md#the-console-api).

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
// ... build the page with el(), field() and button()
await api("curve", { method: "POST", body: { points } });
```

- Files come from `/plugins/<id>/` (`.html`, `.js`, `.css`, images, fonts,
  `.json`; nothing outside the folder). They are given to anyone who can reach
  the editor, so keep nothing in them that is private. Scripts must be files:
  the editor's security policy allows no inline scripts.
- `handle(method, path, query, body)` answers `/api/plugins/<id>/<path>` and
  returns `(status, data)`; `data` is sent as JSON. `(status, bytes,
  "image/jpeg")` sends a file instead, e.g. the picture on the panel for an
  `<img>`. GET must not change anything: changes go through POST. The editor
  listens on 127.0.0.1 only, and every request to `/api/plugins/<id>/…`, GET
  and POST, must carry the run's secret token, which no other user of the PC
  has (`kit.js`'s `api()` sends it; the server answers 403 without it).
  `handle` runs for requests that have it, and with the rights of Libre Panel:
  a page that changes what a program runs or loads (a path to a program, a
  library, a file it writes) should leave that to `config.toml` when Libre
  Panel runs with administrator rights (`self.context.elevated`, below), as the
  app's own settings do.
- `kit.js` exports `api`, `loadTexts`, `t`, `language`, `el`, `field`,
  `button`, `setStatus` and `withToken`. An `<img>` cannot send the token in a
  header; `img.src = withToken("/api/plugins/<id>/picture")` puts it in the
  address, which the server accepts for the `GET` routes of a page (and for
  nothing else). A page that does not use `kit.js` imports `authHeaders()` from
  `/static/token.js` for its own `fetch` calls. `editor.css` gives the page
  Libre Panel's dark colours and controls.

`self.context` gives what the page may need from the running app. Only the
background app (the tray, `libre-panel start`) runs a panel and services; in a
plain `libre-panel editor` `background` is False and the rest is empty or
None, so a page says what is missing instead of failing.

| | |
|---|---|
| `background` | True when the background app serves this editor |
| `elevated` | True when Libre Panel runs with administrator rights (root on Linux). A page then leaves a setting that names a program, a library or a file to `config.toml`: a request cannot be told from the person's, and what it asks for would be done with those rights |
| `service(name)` / `services()` | the running service or None / each enabled service and its state (`running`, `failed: …`, `not installed`, `stopped`) |
| `restart_service(name)` | stop and start an enabled service again; its new state |
| `follow_config()` | after a page wrote `config.toml` (`services.enabled`, a service's options): have the running app bring its services in line with it now, instead of when the panel loop next sees the file, which it does not while the panel is paused. Nothing happens in a plain editor |
| `snapshot()` | the readings and history the panel was last drawn from, or None |
| `status()` | what the panel is doing, as `/api/app` shows it: `state`, `detail`, `hint` (what to do when temperatures and power show "--", else `""`), `target`, `theme`, `frames`, `mode` and `stats` (frame rate, drawing time, data rate, the video link, faults; see [Running in the background](BACKGROUND.md#how-the-panel-is-doing)); None without the background app |
| `sources()` | the sensor sources the panel reads, the first with a reading wins: `[{"name", "readings", "status"}]`, with the number of readings the last snapshot took from the source and, if it has a `status()` (LibreHardwareMonitor does), what it says about itself as a dict |
| `shown_frame()` | the frame on the panel now, with transitions and messages, as a PIL image in the theme's orientation (not turned for an upside-down panel); None before the first frame and while the panel is paused. Read it only |
| `notify(text, **options)` | a message on the panel as a service sends it (`ServiceHost.notify`'s arguments), e.g. a test or the answer to an action; returns the `Delivery` (see *Toasts*) |
| `log_tail(lines=40)` | the last lines of `logs/libre-panel.log`, the log the tray app writes (nothing for `libre-panel start`, which logs to its terminal) |
| `config_path` | config.toml; change one setting with `libre_panel.config.set_config_value("modes.game.theme", ...)` (a boolean, number, string or list), a list of tables such as the autopilot's rules with `set_config_tables("services.autopilot.rules", [...])`, or several of either in one write with `edit_config(("value", name, value), ("tables", name, tables))`. They keep the rest of the file, comments included, check the result before writing it and refuse a layout they cannot edit |
| `data_dir(name)` | a service's data folder |

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
