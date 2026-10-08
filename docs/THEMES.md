# Themes

A theme is a folder with a `theme.json` and optional files (images, fonts):

```
my-theme/
  theme.json
  assets/
    logo.png
    Inter-Regular.ttf
```

Built-in themes live in `src/libre_panel/themes/`; your own go into the user
themes folder (`libre-panel config path` shows the config folder, themes are in
its `themes/` subfolder). A user theme with the same name as a built-in one
wins. The editor writes this format for you; this page is for people who want
to write or review themes by hand.

## theme.json

```json
{
  "format": "libre-panel-theme/1",
  "name": "My theme",
  "author": "you",
  "license": "CC-BY-4.0",
  "description": "What it shows and for which panel.",
  "display": { "model": "turing-3.5", "orientation": "landscape", "width": 480, "height": 320 },
  "palette": { "bg": "#0b1016", "text": "#f1f5f9", "accent": "#22d3ee" },
  "font": "builtin:Barlow-Medium",
  "background": { "color": "@bg", "image": null },
  "refresh_ms": 1000,
  "animation": { "smoothing_ms": 400 },
  "widgets": [
    { "type": "clock", "id": "clock", "x": 240, "y": 20, "format": "%H:%M", "font_size": 48,
      "align": "center", "color": "@text", "glow": 0.3 }
  ]
}
```

- `display.model` is an id from `libre-panel models` (or `"custom"` with an
  explicit `width`/`height`). With a model, the size follows from
  `orientation`; a conflicting width/height is an error.
- Colours are `#rgb`, `#rrggbb` or `#rrggbbaa` — or `@name` for an entry of the
  theme's `palette`. Change a palette colour and every widget using it follows.
- `font` is the default for all text; `builtin:Barlow-Regular`, `-Medium`,
  `-SemiBold`, `-Bold`, `builtin:BarlowCondensed-Medium`, `-SemiBold`,
  `builtin:JetBrainsMono-Medium`, `-Bold` ship with Libre Panel (SIL OFL), or
  use a `.ttf`/`.otf` inside the theme folder.
- `refresh_ms` is how often sensors are read; `animation.smoothing_ms` is how
  long bars and rings glide to a new value (0 = jump). In video mode values
  glide until the next reading and graphs scroll on every frame (they show
  the readings one interval late, smooth curves two), so the 50 fps show
  even when readings come once a second. A graph with `"per_frame": true`
  goes further, as SPUR II does: in video mode it takes the gliding value as
  a point on every frame, a pixel apart, so the curve moves a pixel per
  frame and shows the last `w` frames (a 900 px graph at 50 fps: 18 s).
  Without video mode it shows `history` readings as usual.
- Widgets are drawn in list order: later ones are on top.
- Unknown fields are ignored with a warning, so newer themes still load.
- Optional: `"toast"` sets how messages from services show (editor:
  *Theme → Messages*): `"anchor"` places them (`top-right` by default; also
  `top-left`, `bottom-right`, `bottom-left`, `top`, `bottom`; they keep
  clear of the strip a panel's bezel hides), `"seconds"` is how long each
  stays (4), `"queue": false` lets a new message of the same or a higher
  rank take over at once and drops lower ones (one after another by
  default), `"off"` lists kinds the theme does not show (`["music"]`), and
  `"style"` with `"options"` names a toast style from a plugin (the
  built-in card otherwise).
- Optional: `"screen": {"name": "...", "options": {...}}` lets an installed
  plugin draw the whole frame; the widgets are drawn on top. Widget types
  with a dot in the name (`"myplugin.ring"`) also come from plugins. A theme
  that needs a plugin you do not have still loads; those parts are not drawn.
  See [Plugins](PLUGINS.md).

## Widgets

Every widget has `id` (unique), `type`, `x`, `y`, `visible`, `locked` (editor
only), `hide_if_missing` (hide when its sensor has no value, e.g. weather
switched off), `needs` and the effects `opacity` (0–1), `glow` (0–1) with
`glow_radius`, and `shadow` (a colour) with `shadow_offset` and `shadow_blur`.

`needs` shows a widget only while a sensor has a value, even a widget without
a sensor of its own: `"needs": "gpu.load"` on a card, its label and its icon
hides the whole GPU section on a PC that reports no GPU. With `!` in front it
works the other way round: `"needs": "!weather.temperature"` shows something
else while the weather is off. Several conditions, separated by commas, must
all hold: `"needs": "cpu.load, !gpu.load"`. Themes that use it fit each PC
without editing.

Text-like widgets share `font` (empty = the theme font), `font_size`, `color`,
`align` (`left`, `center`, `right`; `x` is the left edge, centre or right edge
accordingly; `y` is the top of the line), `letter_spacing` and `tabular`
(equal-width digits so numbers do not jitter; on by default for values).
`max_width` (0 = any) keeps text within so many pixels: `fit` `shrink` makes
the font smaller, `ellipsis` cuts the text with "…" (for hardware names).

| Type | Fields | |
|---|---|---|
| `text` | `text` | static label, may contain line breaks |
| `metric` | `sensor`, `format`, `fallback`, `color_rules` | a sensor value as text |
| `bar` | `sensor`, `w`, `h`, `min`, `max`, `scale`, `color`, `color2`, `background`, `radius`, `direction`, `segments`, `segment_gap`, `smooth`, `color_rules` | progress bar; `color2` makes a gradient along the track, `segments` an LED-style bar |
| `gauge` | `sensor`, `w`, `h`, `min`, `max`, `scale`, `start_angle`, `end_angle`, `thickness`, `color`, `color2`, `background`, `cap`, `ticks`, `tick_color`, `smooth`, `color_rules` | ring; angles clockwise from 3 o'clock, default 135→405; `cap` round or flat |
| `graph` | `sensor`, `w`, `h`, `min`, `max`, `scale`, `history`, `color`, `fill`, `fill_fade`, `smooth`, `per_frame`, `line_width`, `grid`, `grid_color`, `background` | history; `min`/`max` `null` = automatic, `smooth` draws a curve, `per_frame` see below |
| `clock` | `format`, `timezone` | date/time with [strftime codes](https://strftime.org), e.g. `%H:%M:%S`, `%A %d %B`; `timezone` e.g. `Asia/Tokyo` (empty: this computer's) |
| `analog` | `w`, `h`, `timezone`, `color`, `color2`, `face`, `marks`, `seconds` | a clock face with hands; `color2` the second hand |
| `countdown` | `target`, `part`, `done` | the time left until `target` (`2026-12-24`, `2026-12-24 18:00`, or `12-24` for every year); `part`: `number` (76, 5:12, 12:33), `unit` (days, hours, minutes), `clock` (05:12:33 left in the day), `text` ("76 days"), `days`, `date`; `done` is shown once it is there ("Today", "Now") |
| `moon` | `size`, `sensor`, `color`, `color2`, `mirror` | the moon as it is lit (`moon.phase`); `mirror` as seen south of the equator |
| `image` | `src`, `w`, `h`, `fit`, `radius`, `slides`, `seconds` | a picture from the theme folder, or one a sensor source has (`@media.cover`: the album cover); `w`/`h` 0 = original size; `fit` `stretch`, `contain` or `cover`; `slides`: more pictures, one per line (`photos/*.jpg` takes a folder), each shown `seconds` |
| `weather` | `field`, `format`, `fallback` | `temperature`, `apparent_temperature`, `humidity`, `wind_speed`, `description`, `code` |
| `icon` | `icon`, `sensor`, `size`, `color`, `stroke` | line icons: cpu, gpu, ram, disk, network, download, upload, temperature, fan, power, clock, sun, moon, cloud, partly, rain, snow, storm, fog, humidity, wind, battery, plug, list, grid, signal — and `weather`, which follows the live weather, or the code in `sensor` (e.g. `weather.day.1.code`), by day or night |
| `calendar` | `w`, `h`, `font`, `font_size`, `color`, `color2`, `muted`, `first_day` | the month as a grid of days, today marked in `color2`; weekday names in `muted` and the language; `first_day` `monday` or `sunday` |
| `list` | `w`, `h`, `items`, `style`, `columns`, `max_items`, `sort`, `format`, `detail`, `detail_format`, `min`, `max`, `scale`, `levels`, `font`, `font_size`, `label_font`, `uppercase`, `color`, `muted`, `color2`, `background`, `empty`, `color_rules` | readings found by name or pattern, as many as fit, see [Lists](#lists) |
| `rect` | `w`, `h`, `color`, `color2`, `gradient`, `radius`, `outline`, `outline_width`, `backdrop_blur` | cards, frames, separators; `backdrop_blur` gives frosted glass |
| `module` | `module`, `col`, `row`, `cols`, `rows`, `source`, `sensor`, `format`, `title`, `text`, `color`, `fallback`, `card`, `forecast`, `step`, `items`, `sort` | a building block on the grid, see [Modules](#modules) |

`scale` is `linear`, `sqrt` or `log`: the latter two keep small values visible
on huge ranges such as network rates.

`color_rules` change the colour by value; the highest matching threshold wins
(and replaces a gradient):

```json
"color_rules": [{ "above": 70, "color": "@warn" }, { "above": 85, "color": "@crit" }]
```

### Lists

A `list` shows readings whose number is not known beforehand: every
temperature sensor, every drive, every core, the busiest programs. `items`
holds one entry per line:

```text
cpu.temp = CPU      a reading, with the name to show
temp.*              every reading that matches, in natural order (core 2 before core 10)
!temp.acpitz.*      none of these
```

A reading that repeats another under a friendlier key (`cpu.temp` is one of
the `temp.*` readings) is shown once. Without a name on its line, the name
comes from a reading beside it (`disk.1.name` for `disk.1.load`), else from
the sensor's label.

- `style`: `rows` (name and value), `bars` (with a bar below, or beside the
  name when a row is wide), `columns` (upright bars side by side, the cores
  of a processor) or `cells` (tiles, filled as high as their value)
- `columns` 0 lays rows out side by side when they do not fit one under the
  other; `max_items` 0 shows as many as fit
- `sort`: `none` (as listed), `high`, `low` or `name`
- `format` `auto` shows a value the usual way for its unit (`45%`, `61°C`,
  `2.4 MB/s`, `3h 5m`); `detail` names a reading beside each one to show
  after its name (`free` for `disk.1.free`) with `detail_format`
- `min`/`max` give the bars' range; `max` at or below `min` lets the largest
  value fill its bar. `levels` off shows numbers only
- `empty`: a text while nothing is found

## Modules

A module is a building block that fills cells of the theme's grid and lays
itself out for its size, the way home-screen widgets do: the larger it is,
the more it shows. A CPU ring of 1×1 cells shows the load; 2×1 adds the
processor's name, temperature and power; 3×1 or 2×2 a history graph.

```json
"grid": { "columns": 8, "rows": 2 },
"widgets": [
  { "type": "module", "id": "clock", "module": "clock", "col": 0, "row": 0, "cols": 3 },
  { "type": "module", "id": "cpu", "module": "ring", "source": "cpu", "col": 5, "row": 0, "cols": 3 },
  { "type": "module", "id": "gpu", "module": "ring", "source": "gpu", "col": 5, "row": 1, "cols": 3 }
]
```

| `module` | shows | `source` |
|---|---|---|
| `clock` | time, seconds when wide, date; hours over minutes when tall | |
| `date` | a calendar sheet: day, weekday, month; the month's days beside or under it when large | |
| `weather` | symbol, temperature, sky; feels-like, humidity and wind; a forecast when there is room: as columns when long (3 × 1) or big, as rows when tall; days and hours together on a big wide one (4 × 2); a calendar while the weather is off | |
| `ring` | a ring with the load; details, the hardware's name and a history graph as it grows | `cpu`, `gpu`, `mem`, `disk` |
| `stat` | a large number; a bar, a history graph and details as it grows | as `ring`, `net`, or `sensor` with `sensor` and `format` |
| `graph` | a history graph under its name and value | as `stat` |
| `bars` | rows with bars for CPU, RAM, disk and GPU, as many as fit | |
| `network` | download and upload; graphs when wide or large | |
| `system` | names of processor and graphics card, uptime | |
| `text` | a title (`text`) with an accent line | |
| `temps` | every temperature as a bar, CPU and GPU first, warm and hot in `warn` and `crit`; the fans beside them when long, as tiles below when big | `items` |
| `cores` | the load of every core: tiles when small, columns when wide; the history when big | |
| `drives` | every drive: how full and how much is free; reading and writing when big | `items` |
| `processes` | the programs that use the most processor time, or memory (`sort`: `cpu` or `memory`); a program's processes count once | |
| `netinfo` | IP address, ping, download, upload, today's traffic, as many as fit; the ping's history when big | |
| `battery` | a battery filled to its charge, charging or not, the time left; its history when wide or big; "No battery" on a desktop | |
| `values` | readings of your choice as tiles: a small dashboard (`items`, default CPU, GPU, RAM, temperatures, download) | `items` |
| `sun` | sunrise and sunset under the day's arc, the moon's phase beside or below; daylight and the next full moon when big. Needs the place in `[weather]` (latitude, longitude) for the sun; without it the moon alone | |
| `analog` | a clock face; time and date beside it when wide (`timezone` for another place's time) | |
| `world` | the time in other places: rows, tiles, or clock faces when big (`items`: `Asia/Tokyo = Tokyo`, one per line) | `items` |
| `countdown` | the days, hours or minutes until `target`; the time left in the day for a time, the date when there is room. The `title` names it | |
| `image` | a picture filling the cells, or several in turn (`items`: one per line from the theme folder, `assets/*.jpg` takes a folder; `seconds` each); the `title` as a caption | |
| `music` | what is playing: cover, title, artist, album, progress and times; "Nothing playing" otherwise | |
| `agenda` | the next events of your calendars, the one going on marked; two columns when long | |
| `game` | the frame rate of the game being played, its 1% lows and frame time; the history when long or big; "No game running" otherwise ([PresentMon](CONFIGURATION.md#more-sensor-sources)) | |

- `col`, `row` place the module, `cols`, `rows` give its size in cells. A
  module that would reach past the grid is moved in.
- `title` replaces the module's own name, `color` (a colour or `@palette`
  entry) the look's colour for its source.
- `fallback`: what a module shows when its source has no readings. `auto`
  shows the disk instead of a missing GPU; `none` leaves the cells empty; or
  name a source.
- `card`: `auto` (cards for all but clock and title), `on` or `off`.
- `forecast` (weather): `auto` (days; days and hours on a big wide module),
  `days`, `hours`, `both` or `off`; `step` is the hours between two hourly
  columns (1–6, default 3).
- `items` (temps, drives, values): the readings to show, written as for a
  [list](#lists); empty shows the module's own. For `world` the places, for
  `image` the pictures.
- `timezone` (analog), `target` (countdown), `seconds` (image): see the table.
- Labels follow the language: "Temp" reads "Temp." in German.

**The grid.** `"grid": {"columns": 0, "rows": 0, "gap": 0, "margin": 0}`; 0
means automatic: cells of about half the panel's short side (8×2 on a 9.2"
bar, 3×2 on a 3.5" panel) that keep clear of strips the bezel hides.

**The look.** Modules paint with palette roles: `bg`, `bg2` (the backdrop's
gradient), `surface`, `line`, `track`, `text`, `text2`, `text3`, `accent`,
`cpu`, `gpu`, `mem`, `disk`, `net`, `warn`, `crit`; missing roles come from
the Arctic look. `"style"` sets the rest:

```json
"style": { "card": "glass", "radius": 18, "glow": 0.6, "backdrop": "gradient",
           "display_font": "builtin:Barlow-SemiBold", "text_font": "builtin:Barlow-Medium" }
```

`card` is `glass`, `flat`, `outline` or `none`; `radius` is the corner of a
card 200 px tall (smaller cards get smaller corners); `glow` 0–1 scales the
light on rings, graphs and the clock. The editor's looks (Arctic, Neon,
Graphite, Paper, Sunset, Mono) set palette and style in one go.

Modules and plain widgets mix: plain widgets are drawn in their order, a
module's parts where the module is in the list.

## Format strings

`format` uses Python format syntax with exactly three fields: `{value}`,
`{unit}` and `{label}`.

| Format | Result |
|---|---|
| `{value:.0f}{unit}` | `64°C` |
| `{value:.1f} GiB` | `15.3 GiB` |
| `{value:bytes}/s` | `2.4 MB/s` |
| `{value:duration}` | `3d 5h` |
| `{value:clock}` | `3:07` (a song's length) |
| `{label}: {value:.0f}` | `CPU load: 38` |

For safety, attribute or index access (`{value.x}`, `{value[0]}`), other field
names and huge widths are rejected; the widget then shows its `fallback`.

## Sensor keys

`libre-panel sensors` lists every key available on your machine. Common ones:

| Key | Unit | Source |
|---|---|---|
| `cpu.load`, `cpu.freq`, `cpu.temp`, `cpu.power` | %, MHz, °C, W | psutil (temp on Linux), LibreHardwareMonitor |
| `gpu.load`, `gpu.temp`, `gpu.power`, `gpu.fan`, `gpu.mem.load` | %, °C, W, RPM, % | LibreHardwareMonitor; `gpu.temp` also Linux AMD |
| `mem.load`, `mem.used`, `mem.total`, `swap.load` | %, GiB | psutil |
| `disk.load`, `disk.used`, `disk.total`, `disk.read`, `disk.write` | %, GiB, B/s | psutil |
| `net.down`, `net.up` | B/s | psutil |
| `fan.<device>.<name>`, `temp.<device>.<name>` | RPM, °C | every fan and temperature: psutil (Linux), LibreHardwareMonitor (e.g. `temp.cpu.package`, `fan.gpu.gpu_fan_1`) |
| `cpu.core.<n>.load` | % | each core (thread), from 1 |
| `disk.<n>.name`, `.load`, `.used`, `.free`, `.total` | text, %, GiB | each drive, the system drive first |
| `proc.cpu.<n>.name`, `.value`; `proc.mem.<n>.name`, `.value` | text, % | the eight busiest programs by processor time (of the whole processor) and by memory; counted only while a theme shows them |
| `net.ip`, `net.ping`, `net.today.down`, `net.today.up` | text, ms, B | the address towards the internet; ping (a connection to `ping`, see the configuration; only while shown); traffic since midnight or since Libre Panel started |
| `battery.load`, `battery.state`, `battery.left`, `battery.plugged`, `sys.uptime` | %, text, s, 1/0, s | psutil; `state` is "On battery", "Charging" or "Charged" in the language |
| `sun.rise`, `sun.set`, `sun.noon`, `sun.next_rise`, `sun.daylight`, `sun.up`, `sun.progress`, `sun.elevation` | "07:42", s, 1/0, 0–1, ° | computed for the place in `[weather]` (no internet needed); `progress` is how much of the day has passed, empty at night |
| `moon.phase`, `moon.illumination`, `moon.name`, `moon.age`, `moon.full_in`, `moon.new_in` | 0–1, %, text, days | computed; `phase` 0 new, 0.5 full; `name` in the language |
| `media.title`, `.artist`, `.album`, `.source`, `.state`, `.playing`, `.position`, `.duration`, `.progress` | text, 1/0, s, 0–1 | what is playing, while a theme shows it; the cover is the picture `@media.cover` |
| `calendar.<n>.title`, `.when`, `.day`, `.time`, `.location`, `.calendar`, `.start`, `.minutes`, `.now`; `calendar.events`, `calendar.today` | text, s, min | the next events (1 = the next or the one going on), [calendars](CONFIGURATION.md#more-sensor-sources) |
| `ha.<entity id>`, `ha.<entity id>.<attribute>` | as Home Assistant says | [Home Assistant](CONFIGURATION.md#more-sensor-sources) |
| `mqtt.<topic with dots>` (`.field` for JSON) | | [MQTT](CONFIGURATION.md#more-sensor-sources) |
| `game.fps`, `game.frametime`, `game.low`, `game.app` | fps, ms, fps, text | the game being played, through [PresentMon](CONFIGURATION.md#more-sensor-sources) (Windows) |
| `cpu.name`, `gpu.name` | text | LibreHardwareMonitor |
| `weather.temperature`, `.apparent_temperature`, `.humidity`, `.wind_speed`, `.code`, `.description`, `.is_day` | °C, %, km/h | Open-Meteo, when enabled |
| `weather.hour.<n>.temperature`, `.code`, `.rain`, `.day`, `.time` | °C, %, 1/0, "15:00" | the hour `n` hours ahead (1 = the next full hour, up to 24) |
| `weather.day.<n>.high`, `.low`, `.code`, `.rain`, `.name` | °C, % | day `n` (0 = today, up to 6); `name` is "Today" or the short weekday |
| `lhm:/<sensor id>` | | any LibreHardwareMonitor sensor |

`gpu.power` is the whole board (TBP), like LibreHardwareMonitor's "GPU Package".

The forecast keys are counted from the moment they are read, so "the next
hour" stays true between two weather updates. `rain` is the chance of
rain in percent.

## Sharing themes

Zip the theme folder. Only include fonts and images you may redistribute and
put the license in `theme.json`. Themes cannot run code, and file references
cannot leave the theme folder, so a shared theme is as safe as a picture.
