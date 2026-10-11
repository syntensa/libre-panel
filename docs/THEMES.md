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
  `@cover` is the colour the cover on show carries (a small strong area of it, not
  its average; the palette's `accent` while there is no cover or it is grey): it
  follows the track. Use it for bars, rings and icons; a text keeps the colour it
  was first drawn in.
- `font` is the default for all text; `builtin:Barlow-Regular`, `-Medium`,
  `-SemiBold`, `-Bold`, `builtin:BarlowCondensed-Medium`, `-SemiBold`,
  `builtin:JetBrainsMono-Medium`, `-Bold`, `builtin:LiberationSans-Regular`,
  `-Bold` (the widths of Arial's letters) ship with Libre Panel (SIL OFL), or
  use a `.ttf`/`.otf` inside the theme folder. A character the font has no glyph for
  is drawn with a font of the computer, in the size, weight and baseline of the text,
  wherever text is drawn (widgets, cards, lists, notices, `facet` and `acrylic`
  lettering): a track in Cyrillic, Greek, Chinese, Japanese, Korean, Arabic, Hebrew or
  Thai, a hardware name in any of them, an emoji (monochrome) or a symbol such as ♪ or →
  no longer comes out as boxes. The bundled fonts carry Latin, Liberation Sans and
  JetBrains Mono also Greek and Cyrillic, Liberation Sans also Hebrew; the rest is found
  once among the fonts of the system: on Windows Segoe UI, Segoe UI Symbol, Yu Gothic, Microsoft
  YaHei, Malgun Gothic, Nirmala UI, Ebrima and Segoe UI Emoji, on Linux what fontconfig
  names for the character (`fc-match`), else Noto Sans and DejaVu Sans. A computer with no
  font for a script (a Linux without Noto CJK) shows boxes for it: install one. A theme
  needs no setting for this, and a font of the theme that has the glyph draws it.
- `refresh_ms` is how often sensors are read; `animation.smoothing_ms` is how
  long bars, rings and numbers with `smooth` glide to a new value (0 = jump).
  In video mode values glide until the next reading, so they never stand
  still between readings, and graphs draw the gliding value 25 times a
  second: the curve moves on every frame (in half-pixel steps) and its
  newest point is the value the numbers show now. A graph's `seconds` is the
  time it spans in video mode, the same at any frame rate (`0`: as long as
  `history` readings take); a 900 px graph moves 25 px a second at `36`.
  `"per_frame": true` (older themes) means a point a pixel apart, `w / 25`
  seconds. Without video mode a graph shows `history` readings. While its
  sensor has no value, a graph in video mode moves on with a gap.
- Optional: `"collection"` names a set the theme belongs to, and galleries
  group by it (`"SPUR II"`: the screens of the project Libre Panel grew out
  of, kept as a legacy set). Leave it out for a theme of your own.
- Widgets are drawn in list order: later ones are on top.
- Unknown fields are ignored with a warning, so newer themes still load.
- Optional: `"toast"` sets how messages from services show (in the app: *Look →
  Notices* for the style, *Fine-tuning → Messages* for the rest): `"anchor"`
  places them (`top-right` by default; also
  `top-left`, `bottom-right`, `bottom-left`, `top`, `bottom`; they keep
  clear of the strip a panel's bezel hides), `"seconds"` is how long each
  stays (4), `"queue": false` lets a new message of the same or a higher
  rank take over at once and drops lower ones (one after another by
  default), `"off"` lists kinds the theme does not show (`["music"]`), and
  `"style"` with `"options"` names the toast style: the built-in card
  (the default), `"band"` or one from a plugin.
- The band (`"style": "band"`) is a band across the panel in the look of
  SPUR II: a glowing edge ignites from the centre and the band rolls down
  from it, with a title, a picture or icon, the text and a progress bar
  when the message brings them. It lies at the bottom with an anchor
  starting `bottom`, else at the top, and takes the theme's palette
  (`bg`, `text`, `accent`, `warn`, `crit`, `track`). Its options
  are `roll_in` (0.45 s) and `roll_out` (0.32 s):

  ```json
  "toast": { "style": "band", "anchor": "bottom", "queue": false, "seconds": 2,
             "options": { "roll_in": 0.45, "roll_out": 0.32 } }
  ```
- Optional: `"screen": {"name": "...", "options": {...}}` lets an installed
  plugin draw the whole frame; the widgets are drawn on top. Widget types
  with a dot in the name (`"myplugin.ring"`) also come from plugins. A theme
  that needs a plugin you do not have still loads; those parts are not drawn.
  See [Plugins](PLUGINS.md).
- Optional: `"look"` lays SPUR II's grid over the background, lights the
  panel with one light and lets fine dust fall into the depth; a widget's
  `finish` draws it lit or faceted. See [Look and finish](#look-and-finish).

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

A condition may also compare a reading with a number: `"needs": "media.playing=1"`
shows a widget only while a track plays, `"cpu.temp>80"` only while the processor
is hot (`=`, `==`, `!=`, `>`, `>=`, `<`, `<=`). A reading that is no number
never holds, and `!` in front says the opposite. A state that a service has
taken back (`null`) has no value, so `"needs": "downloads"` is true only while
something downloads. `@` in front of a name asks for a picture a source has:
`"needs": "!@media.app_icon"` is true while the player has no icon.

`=` and `!=` also compare with a word: `"needs": "game.limiter=gpu"` holds while
the reading is that text (in any case), so a word in the colour of whoever holds the
frame rate down shows only while it does. A game's name can stand where its logo is
missing: `"needs": "!@game.logo"`.

Text-like widgets share `font` (empty = the theme font), `font_size`, `color`,
`align` (`left`, `center`, `right`; `x` is the left edge, centre or right edge
accordingly; `y` is the top of the line), `letter_spacing` (pixels, also
fractions of one) and `tabular` (equal-width digits so numbers do not jitter;
on by default for values).
`max_width` (0 = any) keeps text within so many pixels: `fit` `shrink` makes
the font smaller, `ellipsis` cuts the text with "…" (for hardware names).

| Type | Fields | |
|---|---|---|
| `text` | `text`, `glow_area` | static label, may contain line breaks; `glow_area` see [Look and finish](#look-and-finish) |
| `metric` | `sensor`, `format`, `fallback`, `smooth`, `color_rules`, `glow_area` | a sensor value as text; with `smooth` the number glides between readings together with the sensor's bars and rings (give `format` a precision, e.g. `{value:.0f}`) |
| `bar` | `sensor`, `w`, `h`, `min`, `max`, `scale`, `color`, `color2`, `background`, `radius`, `direction`, `segments`, `segment_gap`, `smooth`, `color_rules`, `glow_area`, `mark`, `mark_color`, `mark_width` | progress bar; `color2` makes a gradient along the track, `segments` an LED-style bar; `glow_area` see [Look and finish](#look-and-finish); `mark` names a second reading that is drawn as a thin line across the bar, at its value on the bar's own scale (the average of earlier rounds, a limit), `mark_width` px wide (2) in `mark_color`, reaching a fifth of the bar's thickness beyond it on both sides; it lies over the bar in any finish, and is gone with the bar or while its reading has no number |
| `gauge` | `sensor`, `w`, `h`, `min`, `max`, `scale`, `start_angle`, `end_angle`, `thickness`, `color`, `color2`, `background`, `cap`, `ticks`, `tick_color`, `smooth`, `color_rules` | ring; angles clockwise from 3 o'clock, default 135→405; `cap` round or flat |
| `graph` | `sensor`, `w`, `h`, `min`, `max`, `scale`, `history`, `seconds`, `color`, `fill`, `fill_fade`, `fill_alpha`, `direction`, `inset`, `end_dot`, `smooth`, `per_frame`, `line_width`, `grid`, `grid_color`, `background` | history; `min`/`max` `null` = automatic, `smooth` draws a curve, `fill_alpha` 0 to 1 sets how opaque the fill is (0: the default, a flat fill 0.25, a fade 0.45 at its top), `direction` `down` hangs the curve from the top edge and fills it from there (upload above a line, download below it), `inset` off lets the range span the box (a line at an edge is cut in half there) as SPUR II's charts, `end_dot` (a radius in px, 0: none) ends the line in a dot of its colour at the newest value, with the graph's `glow` around it, as the ends of SPUR II's Horizon curves, `seconds` and `per_frame` see above |
| `clock` | `format`, `timezone`, `uppercase` | date/time with [strftime codes](https://strftime.org), e.g. `%H:%M:%S`, `%A %d %B`; `timezone` e.g. `Asia/Tokyo` (empty: this computer's); `uppercase` sets the weekday and month in capitals, in the language of the panel (MONTAG · 28. SEPTEMBER) |
| `analog` | `w`, `h`, `timezone`, `color`, `color2`, `face`, `marks`, `seconds` | a clock face with hands; `color2` the second hand |
| `countdown` | `target`, `part`, `done` | the time left until `target` (`2026-12-24`, `2026-12-24 18:00`, or `12-24` for every year); `part`: `number` (76, 5:12, 12:33), `unit` (days, hours, minutes), `clock` (05:12:33 left in the day), `text` ("76 days"), `days`, `date`; `done` is shown once it is there ("Today", "Now") |
| `moon` | `size`, `sensor`, `color`, `color2`, `mirror` | the moon as it is lit (`moon.phase`); `mirror` as seen south of the equator |
| `fan` | `sensor`, `w`, `h`, `color`, `max_rpm`, `frame` | a fan that turns with its speed (default `gpu.fan`), see [Fans](#fans) |
| `image` | `src`, `w`, `h`, `fit`, `align`, `radius`, `slides`, `seconds` | a picture from the theme folder, or one a sensor source has (`@media.cover`: the album cover); `w`/`h` 0 = original size; `fit` `stretch`, `contain` or `cover`; `align` `left`, `center` (the default) or `right` says where a `contain`ed picture stands in its box when it does not fill it, as a logo that is wider or narrower than its slot; `slides`: more pictures, one per line (`photos/*.jpg` takes a folder), each shown `seconds` |
| `weather` | `field`, `format`, `fallback` | `temperature`, `apparent_temperature`, `humidity`, `wind_speed`, `description`, `code` |
| `icon` | `icon`, `sensor`, `size`, `color`, `stroke` | line icons: cpu, gpu, ram, disk, network, download, upload, temperature, fan, power, clock, sun, moon, cloud, partly, rain, snow, storm, fog, humidity, wind, battery, plug, list, grid, signal, play, pause, up, down — and `weather`, which follows the live weather, or the code in `sensor` (e.g. `weather.day.1.code`), by day or night |
| `symbol` | `sensor`, `size`, `color`, `moving`, `offset` | a weather symbol that moves (default `weather.code`), see [Weather symbols](#weather-symbols) |
| `downloads` | `state`, `w`, `h`, `font`, `label_font`, `color`, `muted`, `color2`, `done`, `background`, `empty` | a download as the Studio's inset shows it: name, progress, rate, time left and the speed of the last minutes; it reads the state of the `downloads` service, see [Downloads](#downloads) |
| `calendar` | `w`, `h`, `font`, `font_size`, `color`, `color2`, `muted`, `first_day` | the month as a grid of days, today marked in `color2`; weekday names in `muted` and the language; `first_day` `monday` or `sunday` |
| `list` | `w`, `h`, `items`, `style`, `columns`, `max_items`, `sort`, `format`, `detail`, `detail_format`, `min`, `max`, `scale`, `levels`, `font`, `font_size`, `label_font`, `uppercase`, `color`, `muted`, `color2`, `background`, `empty`, `color_rules` | readings found by name or pattern, as many as fit, see [Lists](#lists) |
| `rect` | `w`, `h`, `color`, `color2`, `gradient`, `radius`, `outline`, `outline_width`, `backdrop_blur`, `ridge` | cards, frames, separators; `backdrop_blur` gives frosted glass, `ridge` a ridge (see [Look and finish](#look-and-finish)) |
| `module` | `module`, `col`, `row`, `cols`, `rows`, `source`, `sensor`, `format`, `title`, `text`, `color`, `fallback`, `card`, `forecast`, `step`, `items`, `sort`, `moving`, `hold`, `change`, `slide`, `time` | a building block on the grid, see [Modules](#modules) and [Cards](#cards) |

`scale` is `linear`, `sqrt` or `log`: the latter two keep small values visible
on huge ranges such as network rates.

`bar`, `gauge`, `graph`, `icon`, `symbol`, `downloads`, `text`, `metric`,
`clock`, `weather`, `countdown` and `module` also take `finish`: how their
shape is drawn, see [Look and finish](#look-and-finish).

`color_rules` change the colour by value; the highest matching threshold wins
(and replaces a gradient). Crossing a threshold fades the colour in a fifth
of a second, the same for a bar and a number of one sensor with the same
rules:

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

### Facets

`"finish": "facet"` gives a widget the raised look of SPUR II's lettering: a
flat top in the exact colour and a sharp 45-degree bevel inside the outline,
lit from the top left. Where the bevel faces the light it is lighter, where it
faces away darker. `"flat"` is the default and draws as ever.

- Text (`text`, `metric`, `clock`, `weather`, `countdown`) keeps the outline
  of the flat text pixel for pixel; the bevel lies inside it. It is bright
  metal that mirrors its surroundings, as the "SPUR" of the wordmark: a bevel
  that faces up takes on the sky (the palette's `accent`, a third of the way
  to white), one that faces down the ground (the theme's background colour), so
  the edges look milled instead of shaded grey. The bevel is about 2 px wide at
  a font size of 58 and takes at most a half of a stroke. Small text gets a
  light touch (up to 28 px: a faint highlight and shade, no mirror) and the
  full look comes at 52 px; use a bold font for it. Text under 20 px, with
  line breaks, or in a bitmap font stays flat: a bevel there is only mush.
- `"finish": "acrylic"` is the same lettering lit from within, as the "II" of
  the wordmark: the bevels that face the light lighter still and the others
  hardly darker, nothing mirrored. It is for text only; on anything else the
  theme loader warns and the widget is drawn flat. The glow round the
  wordmark is not part of either finish: put a copy of the text in the accent
  colour below it with `glow` and `glow_radius`.
- A `bar` is raised in a milled track (its `background`), with straight
  bevelled ends (`radius` does not apply), as blocks with `segments`. A `color2`
  gradient runs along the whole track and `color_rules` work as before. A bar
  under 6 px thick stays flat.
- A `gauge`'s arc is raised in a milled track with straight bevelled ends,
  whatever its `cap`; `ticks` stay flat.
- A `graph`'s line becomes a ridge on the plateau of its fill: bright on the
  flank away from the fill, dark on the one towards it, then a band a little
  brighter than the fill, and a soft halo of the line's colour round it all,
  which ends a line and two pixels beyond the curve's highest and lowest
  points, as SPUR II works a curve out. A graph that hangs from its top
  (`direction` `down`) has the ridge mirrored, its light still from above.
  `fill_alpha` (0 to 1) sets how opaque the plateau is: SPUR II's is 0.41 with
  `fill_fade` off. A graph wider than 1200 px (a bar panel) gets a slimmer,
  single-band ridge.

Every shape is built once per font, size and colour and kept, so a gliding
bar or a changing number costs no more than a flat one (`tools/motion_bench.py
--finish facet` compares).

The milled groove a value lies in is there for other finishes to use, in
`libre_panel.render.facets`: `groove_bar(w, h, color, vertical=False)` and
`groove_ring(w, h, thickness, start=0, end=360, color=(22, 35, 50))`. Each
returns an RGBA picture whose floor is exactly `color`, with a sharp bevel of a
sixth of the thickness that is in shade on the upper and left edges and
catches the light on the lower and right ones; a bar's ends are straight and
its corners mitred, an open ring has straight ends too. They are kept by size
and colour and give the same picture on every call: copy it before drawing on
it. `groove_bar` returns `None` for a bar under 6 px or over 400 px thick,
where a flat track is drawn instead.

### Fans

A `fan` draws a rotor of nine lit blades in its `color` that turns with a fan's
speed (`sensor`, in RPM; `gpu.fan` by default). It is not the real speed (1200
RPM would be 20 turns a second, a flicker at 50 frames a second) but a calm one
between a fifth of a turn and a turn a second that rises with the RPM, follows
it with inertia, runs out slowly and stands still at 0 RPM. Fast, the blades
blur towards a disc. `max_rpm` (3000) is the speed the ring is full at.

`frame` is `ring` (the default: a groove with a glowing arc as long as the
fan's share of `max_rpm`), `square` (a lit housing that is the brighter the
faster the fan runs) or `none`. `w` and `h` set the size (48 px; the smaller
counts). A fan without a reading stands dimmed and still, a stopped one dimmer
than a running one. It turns where the panel is driven faster than once a
second (video mode, or `fps` above 1); in the editor's preview and on a
panel that gets one picture a second it stands, its ring and its blur showing
the speed. In the editor, *Fan with speed* adds a fan with its name and RPM.

### Libre Panel's screens

Libre Panel's own themes show what 0.5 can do in its own look: the app's
colours (`#2EE6D6` and its dark greys) and Barlow, flat frames, values as
light. Each is plain JSON; none names a piece of hardware.

| Theme | Panel | Shows |
|---|---|---|
| `lumen` | 8.8" / 9.2" bar | the signature screen: on the left a slot of cards (what plays, else the weather with its moving symbols and the next hours, else a calendar sheet); in the middle the time with its seconds, the load of the CPU and GPU over the last minute as light that scrolls (`seconds` 60), and the fans; on the right lit [instruments](#cards) for the CPU, GPU and RAM with their readings and the names of the hardware (`cpu.name`, `gpu.name`, `mem.name`), over the network card, which shows a download while one runs |
| `studio` | 8.8" / 9.2" bar | a large clock with the date and the network, a slot of cards (what plays, the weather, the day) and lit rings for the CPU, RAM and GPU (the disk on a PC without GPU readings) with the hardware's names |
| `libre-default` | 3.5" | two lit rings, the CPU's temperature, the disk and the network, a history that scrolls through the last 30 seconds, and a small slot in the corner: what plays, the weather or the day |

A `config.toml` without a `theme` shows `lumen` on the 1920×480 bars and `libre-default` on
every other panel ([the theme on the panel](CONFIGURATION.md#the-theme-on-the-panel)).

A few things they do that a theme of your own can do too:

- **What plays, else the weather, else the day.** A `cards` slot with the
  items `playback`, `forecast` and `date`: the day takes the card when there is
  neither a track nor weather (the weather is off until you set a place).
- **A date that steps aside.** Studio's date under the clock is two widgets,
  one with `"needs": "weather.temperature"`, one with `"needs":
  "!weather.temperature, media.title"`: the date shows while the card shows
  something else, and steps aside while the card shows the day.
- **Fans by the names they have.** Lumen turns `gpu.fan` and the board's
  first three fans as LibreHardwareMonitor names them (`fan.lpc.fan_1` ...),
  each with `"needs"` on its own reading, so a fan that is not there leaves
  no gap behind; their names come from the readings (`"format":
  "{label:upper}"`). Where a PC names its fans otherwise (Linux), a
  [list](#lists) of `fan.*` with `"needs": "!gpu.fan, !fan.lpc.fan_1"` shows
  them instead.
- **A backdrop of their own.** Lumen's background is a picture in its folder
  (`assets/backdrop.png`): the dark gradient of the app with a dot every 22 px,
  as the editor's stage has it.
- **The grid as a ruler.** The cards are modules on a fine grid (48 × 48
  cells on Lumen), placed where the plain widgets around them leave room. A
  module that covers fewer than two rows of cells keeps its compact looks:
  Libre Default's corner is one row of a 5 × 4 grid with `"forecast": "off"`,
  so the weather in it is a symbol, a temperature and the sky, and the day a
  calendar sheet without the month.

On a smaller panel (*adapt* in the editor) the modules lay themselves out for
their new size, and the other widgets, and the room their texts may take,
scale with the panel.

### The SPUR II screens

The screens of SPUR II are built-in themes for the 8.8" and 9.2" bars (1920 × 480)
that show the look above in full; each is plain JSON like any other theme, and
none names a piece of hardware (the names of the processor, the graphics card and
the memory come from `cpu.name`, `gpu.name` and `mem.name`). Their frames are flat
bands in the colour of what they frame.

| Theme | Shows |
|---|---|
| `spur-classic` | two load charts, RAM and network in staggered boxes, CPU and GPU sections; for the desk |
| `spur-horizon` | the CPU's load growing up from a midline and the GPU's down from it, 70 seconds side by side (a point a pixel, 25 a second, a mark every 10 s, a dot at each curve's end), tiles below for values, network and music; for the desk |
| `spur-cockpit` | the CPU's and GPU's load as two equal charts with the RAM as a ring beside them, and CPU, GPU, memory and network in four columns of equal width (SPUR II's building blocks on its 40 px grid); for the desk |
| `spur-header` | for a game: the classic frame with the frame rate and the 1% low in the header beside the game's logo (its name where there is none; SPUR II moves the frame rate over for a long name, a theme cannot: a name wider than 72 px is cut with …, a wider logo is made smaller), load charts, who holds the frame rate down and a chart of the longest frame of each moment; needs the `game` service and PresentMon |
| `spur-focus` | for a game: the frame rate large (150 px) with its course over 35 s beside it, the CPU's and GPU's load as small charts, then FRAMES, CPU, GPU and system with bars; the game's logo or name above the number; needs the `game` service and PresentMon |
| `spur-session` | for a game: the game added up as SPUR II's Session screen does: the time played, the average frame rate large (with the frame rate now, the 1% low, who holds it down, and the last ten minutes of the rate under them), the share of the time at each frame rate, and the highest temperatures, power and video memory since it began beside what they are now; the totals are the `session.*` readings of the `memory` service (see [Plugins](PLUGINS.md#the-round-under-way)), the rest needs the `game` service and PresentMon; in the header the game's logo or name has the room SPUR II gives a short name (a name wider than 62 px is cut with …, a wider logo made smaller), as in `spur-header` |
| `spur-studio` | SPUR II's Studio screen, for the desk, made of the [cards](#cards) on one grid: a large clock with the seconds and the date, the network (a download while one runs) and the volume on the left, the playback card in the middle (the weather card when nothing plays, with the line of light between them), and rings for the CPU, GPU and RAM (the RAM's readings: free, temperature, speed); it needs `media` for the playback card, the weather for the forecast, and shows the volume with the `audio` source |
| `round-report` | what the `memory` service shows after a game (`report_theme`): the round's average frame rate with its distance from the average of the earlier rounds, the 1% low and the frame time, who held the rate down as a bar in the colours of the GPU and CPU (and the frame limit's), the share of the round at each frame rate with a tick across each bar where the earlier rounds' average stands, the game's logo at the top right beside its name (`@game.logo`, which the `game` service keeps after the game; its name alone where there is none), and the highest temperatures and power against their average; it reads the `report.*` readings ([Plugins](PLUGINS.md#the-round-report)), so its title, heading hints and note follow the language of the panel, and a first round says so and shows no comparison; needs the `memory` service and PresentMon |

The fans in the header need their sensors (`fan.lpc.fan_1` ... , `gpu.fan`) and show
only the ones that report; the volume needs the `audio` source, what plays the
`media` source, the screens for a game the `game` service and PresentMon
(see [Configuration](CONFIGURATION.md#game-mode)). Where a screen for a game
shows its logo (`@game.logo`, from the `game` service) and the game has none, its
name stands there: `game.name`, or the program PresentMon counts (`game.app`)
where the service has no name.

### Weather symbols

A `symbol` draws the weather of a code sensor (`weather.code` by default, or
`weather.hour.3.code`, `weather.day.1.code` ...) as the weather card of SPUR II
does, and it moves: the sun's rays turn, rain, drizzle and snow fall, clouds
drift and breathe, stars twinkle at night, fog sways and a bolt flashes every few
seconds. It shows a clear sky, mostly clear, partly cloudy, overcast, fog,
drizzle, rain, snow or a thunderstorm, by day or by night: the forecast's `.day`
reading says which for its hours, `weather.is_day` for now, else the clock. A
code it does not know, or no reading at all, is a cloud.

- `size` (64) is its edge; `color` is the colour of the clouds and the moon. The
  sun and bolts take the palette's `warn`, rain its `accent`, snow its `text`.
- `moving` off lets it stand. It moves where the panel is driven faster than once
  a second (video mode, or `fps` above 1); in the editor's preview and on a panel
  that gets one picture a second it stands, in its resting places.
- `offset` shifts its motion by that many seconds: give the symbols of a row
  different ones, or six clouds drift in step.
- `finish` `lit` draws the sun, moon, clouds, drops and bolt as lit bodies (from
  Blender's renders, as the lit weather icon); the default draws them flat.
  `facet` and `acrylic` are not there for a symbol.

A symbol is made of layers drawn once for a kind of weather, a size and the
colours, and kept; a frame only moves, turns and fades them over each other, and
the picture of a step of 1/25 s is kept too, so a panel at 50 frames a second
draws every second frame for free. Small symbols (under 120 px) move a little
quicker than big ones: the paths are the same share of the size, but of a third of
the pixels.

### Downloads

A `downloads` widget draws a download as the inset of SPUR II's Studio screen
does. At the top stand the phase (Download, Paused, Installation, Done), the
source (Steam or Browser) and, to the right, the name, with `+2` where more
downloads run; under them a progress, and under that two readings: the rate and
the time left (or, where the time is not known, what is loaded), and the loaded
amount against the whole (`9.3 / 24.1 GB`, with a `≈` in front where it is
worked out from the rate). A finished download shows its size and how long it
took, one that is being installed what it loaded and how long it took. On the
right the rate of the last three minutes is a curve that grows from its bottom
edge, on a scale of its own highest value.

It reads the state that the built-in `downloads` service publishes
([Plugins](PLUGINS.md#the-downloads-state)); `state` names another state of that
shape. Where nothing downloads it draws nothing, or `empty` (for example "No
download running"). A state that is not what the service sends (a missing or odd
field) is read as far as it goes: the widget never fails.

- The progress is the share of `total` that is loaded, in `color2`; `done`
  colours a finished download and `muted` a paused one. A download of unknown
  size (a browser's) has no share: a stretch of light runs to and fro in the
  groove, and stands while it is paused. `background` is the groove.
- `font` sets the heading and the numbers, `label_font` the names, `color` the
  numbers and `muted` their names.
- The card is laid out in the numbers of the inset (596 × 130 px) and scaled to
  `w` × `h`. A box that is less than 2.4 times as wide as it is high puts the
  graph under the readings and the name on a line of its own, so a tall
  card is as legible as a wide one.
- `finish` `lit` makes the progress light in its groove (`glow` its glow), as
  a lit bar; the default draws a flat bar. Everything but a running stretch is
  drawn once per state (the service publishes one a second) and kept.

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
| `system` | names of processor and graphics card, uptime; the memory's name too when there is room for a fourth row | |
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
| `playback` | the playback card of SPUR II's Studio screen: cover, state, title, artist, app and progress ([Cards](#cards)); the music module where it is small | |
| `forecast` | the weather card of the Studio screen: a moving symbol, the temperature and the day's range, the details, the rain and the sun, the next hours ([Cards](#cards)); the weather module where it is small | |
| `download` | the download card: the Studio's inset for a download ([Cards](#cards)); "No download running" otherwise | |
| `instrument` | a ring with its number inside and the readings of the part beside or below it, or a fan with its speed ([Cards](#cards)) | `cpu`, `gpu`, `mem`, `disk`, `fan` (with `sensor`) |
| `cards` | a slot that shows one of several cards (`items`) and changes with the ignition line ([Cards](#cards)) | `items` |
| `netcard` | a slot of a download card and the network card ([Cards](#cards)) | |

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
  columns (1–6; 0, the default, is 3 for the weather module and 1 for the
  weather card).
- `items` (temps, drives, values): the readings to show, written as for a
  [list](#lists); empty shows the module's own. For `world` the places, for
  `image` the pictures, for `cards` the cards.
- `timezone` (analog), `target` (countdown), `seconds` (image): see the table.
- `time` (playback, music): the time at the right of the progress, `total` (the track's
  length, `3:47`; the default) or `remaining` (what is left, `-2:24`).
- Labels follow the language: "Temp" reads "Temp." in German.

### Cards

The cards of SPUR II's Studio screen are modules (the theme `spur-studio` is made of them). A card is laid out in the numbers
of the card it comes from (376 px tall on the 1920 × 480 bar) and scaled with its
module, so it looks as that card does at its size. Where the module is too small or
too narrow for that, the older module of its kind takes over: the weather card is
the weather module, the playback card the music module, the network card the
network module. Cards are lit by themselves: a module's `finish` is `auto` (its
kind's own: `lit` for a card, flat for the others), and the theme may name one.
Values are light in their grooves, the weather symbols are lit bodies, and frames
are flat thin lines in their colour: a card has no ridge, no bevel and no trough.

- **`playback`** (3 × 2): the cover (`media.cover`; a note where there is none),
  a sign and a word for the state (*Now playing* or *Paused*), the title and the
  artist, the app that plays it with its icon (`media.app_icon`) and name, the
  progress (`media.progress`) with the time played and the length, or with `time`
  `remaining` the time left, counting down with a minus (`-2:24`, from
  `media.remaining`; SPUR Studio does so). Without a track: a note and "Nothing playing". The state's sign and
  the progress are in the colour the cover carries (`@cover`), as in the Studio;
  `color` sets one.
- **`forecast`** (3 × 2): a moving symbol ([Weather symbols](#weather-symbols)),
  the temperature with the day's high and low, the sky, how it feels, the wind and
  the humidity, the rain of the next two hours (`weather.rain.text`, in the `gpu`
  colour where it will rain), the sun (`sun.set` by day, `sun.next_rise` by night),
  and under them the next hours as columns: time, symbol, temperature and the
  chance of rain as a bar. As many hours fit as the width allows, `step` hours apart
  (1); `moving` off lets the symbols stand. Without weather it is a calendar sheet.
- **`instrument`** (1 × 2): a ring with the load of `source` (`cpu`, `gpu`, `mem`,
  `disk`) as light in its groove, the number inside with a small unit, the name
  under it and the part's readings (temperature, power, clock ...) as rows, as many
  as fit; a wide module puts the name and rows beside the ring. The temperature
  goes warm and hot. The RAM's rows are what is free (`mem.free`), the modules'
  temperature (`mem.temp`) and the speed they run at (`mem.speed`, in MT/s); a
  reading the PC cannot give stays on show as `--` with its name, as in the Studio,
  where the other parts leave such a row out. `source` `fan` makes it a fan: the rotor of the
  [`fan` widget](#fans) in its ring, which turns with the speed, with the fan's
  name and its speed in RPM beside it or under it; `sensor` names the fan
  (`fan.cpu`; default `gpu.fan`), `color` sets its colour.
- **`download`** (2 × 1): the [download widget](#downloads) in a card, with the
  `net` colour.
- **`netcard`** (2 × 1): a slot of the download card and **`net`**, the network
  as the Studio's NET section has it: UP and DOWN with the rate beside them and the
  history of both, the upload above a line, the download hanging below it.

**Slots.** `cards` holds the cards its `items` name, one per line (`playback`,
`forecast`, `download`, `instrument`, `net`, ... any module kind but a slot;
default `playback` and `forecast`), and shows the first that has something to
show, else the first one in its empty look:

| card | has something to show |
|---|---|
| `playback`, `music` | a track plays, or was paused less than `hold` minutes ago (2): a paused track stays, you will want it again |
| `forecast`, `weather` | the weather is there (`weather.temperature`) |
| `download` | a download runs (a finished one for 6 s) |
| any other | always |

A line brings its own rule after `if`: `forecast if weather.temperature` or
`playback if media.playing=1, !game.fps`: conditions as `needs` has them, separated
by commas; the rule replaces the card's own. A line that names no card is left
out, `#` starts a comment.

**A change** takes `change` seconds (0.6) and moves as the Studio screen does: a line
of light ignites at the slot's top edge, the card on show rolls up into it, and the
new card rolls down out of it. What the old card shows while it rolls up is what it
showed last: its track is gone from the readings, its words are not. The time is the
frame's planned time, so a change takes as long at 50 frames a second as at 5; on a
panel that gets one picture a second it is a cut. A change that is under way is
finished before the next begins.

**A new track** on the playback card that stays on show does not roll: with `slide` (seconds;
0, none, by default, SPUR Studio sets 0.55) the content of the card moves out to the left and
the new track's moves in from the right, a gap between them (48 px on a panel 480 px tall,
scaled with it) that closes as it arrives, as SPUR II's Studio does it: one movement for a new
track, none while a track plays. What moves out is what the card showed last; the frame stays
and changes its colour with the cover as above. A pause, a cover that comes late and a change
of cards are no new track. It takes the frame's planned time, and is a cut where a change is.

The slot's frame is a thin flat line in the colour of the card on show: the colour
that carries the cover, the weather's (the sun's by day, the clouds' by night, rain's,
snow's, a storm's, grey for overcast), the network's, else `accent`; `color` sets one
for all cards. It changes its colour over half a second, also for a new track. With
`"style": {"card": "flat"}` or `"none"` a slot has no frame line, only the light.

Only the card on show is drawn. Every card of a slot is made once with the first
frame, so the first change does not stall the panel; pieces are kept as for any
widget. Both paths work: video mode plays the change at the panel's frame rate and
the picture-a-second path cuts.

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

## Look and finish

The look of SPUR II in two parts: the theme's `look` block for the whole
panel, and a widget's `finish` for its shape. A theme with neither costs
nothing extra. The built-in *SPUR Classic* (1920 × 480) shows both, with the
grid and the dust; its frames are flat lines, so it has no panel light (the
light needs ridges or a plugin's edges to glint on). The app's
*Look* view sets them: the blocks below, and the finish and glow of each kind of
element.

```json
"look": {
  "grid": { "spacing": 8, "major": 5 },
  "light": { "strength": 1.0, "sweep_seconds": 1.6 }
}
```

A block that is missing (or `false`) is off; `true` or `{}` turns it on with
its defaults. Blocks this version does not know are kept with a warning, so
a theme from a newer version loses nothing when the editor saves it.

**`grid`** lays thin lines every `spacing` px (2–400, default 8) from the top
left, every `major`-th one stronger (0: none; default 5). They lie over the
backdrop (full-panel `rect`s at the start of the widget list) and under every
other widget, and are drawn once. `color` and `major_color` default to the
palette's `grid` and `grid_major`, else to a breath away from the background
colour (the strong lines twice as far); `alpha` (0–1) dims both.

**`light`** is one light for the whole panel. It falls only on *light
edges*: points with a direction that widgets give it. Ridges (below) catch
it; plugins can give their own (see below).

- The sun stands where it stands for the place in `config.toml` (`[weather]`
  latitude and longitude; without a place, or with `"sun": false`, at noon):
  from the left in the morning, steep at noon, from the right in the
  evening, warm near the horizon, a cool weak light at night. A straight
  frame does not shine along its whole length but where the reflection
  fits. Its gloss is worked out again when the sun has moved a step (about
  a minute) and is kept in the frame's background in between.
- A sweep of light runs across the panel in `sweep_seconds` (0.3–10, default
  1.6): when the panel starts (`on_start`), on the full hour (`on_hour`), when
  a new track plays (`on_track`: `media.title` changes) and when a `tube` or
  `entrance` transition ends (`on_transition`); all on by default. `color`
  tints it (default: the palette's accent). A new theme does not count as a
  start, and the editor's previews show no sweeps.
- `strength` (0–2, default 1) scales both.

**Ridges.** A `rect` with a `ridge` name is drawn as a ridge, as SPUR II's
frames: every point of it tilts towards its nearest edge, so it has two
facets, one towards the guide light (top left), which lightens and glows a
little, one away from it in shade. Rects with the same name are one ridge,
so bands that meet join at a mitre (a frame of five bands is one frame). An
outlined rect gives its outline, a filled one its fill. A ridge is a light
edge for `look.light`. Without a name a rect stays flat, with or without a
look.

**`dust`** lets fine dust fall from the lit things on the panel into the
depth behind the glass, as on SPUR II: letters, rails, rings and bars give
off grains of their own colour, which shrink and fade on their way to a
vanishing point a little above the middle, and a few near ones are soft
discs. The theme does not say where the sources are. The dust reads the
frame the panel showed, takes every bright pixel for a source (a new stock
every 4 seconds, and again when a message begins and once it has rolled in)
and so follows any layout.

```json
"look": { "dust": { "count": 1800, "rate": 25, "depth": 1, "strength": 1.5,
                    "colors": ["@accent"],
                    "areas": [{ "x": 0, "y": 0, "w": 480, "h": 120, "dust": false }] } }
```

- `count` (1800, 0–8000) is the number of grains on a 1920 × 480 panel; other
  sizes get as many as their area asks for.
- `rate` (25, 1–60) is how often the grains move in a second, `depth` (1, 0–3)
  how far they fall towards the vanishing point (0: they hang where they are),
  `strength` (1, 0–3) how bright they are.
- `colors`: up to 8 colours (`#rrggbb` or `@name`); empty, each grain takes
  the colour of the thing it comes from.
- `areas`: up to 64 rectangles `{x, y, w, h, dust}` in panel pixels (they move
  with the layout when a theme is adapted to another panel). With an area that
  says `"dust": true` there is dust only inside it, otherwise everywhere;
  `"dust": false` areas are cut out either way.
- A bright element hides the dust over it, as if the dust lay behind it. A dark
  card does not: dust shows over it, so keep dust out of it with an area.
- It is drawn on a panel driven frame by frame (video mode) and in the editor's
  preview. A panel that is sent the changed regions as pictures would get the
  whole frame at every update, so there is none.
- It costs a millisecond or two a frame at 1920 × 480 (the grains move in the
  helper thread, a frame lays them in one pass); `tools/motion_bench.py --dust`
  measures it on your machine.

**`finish`** is `flat` (the default: the widget as it always looked), `lit`,
`facet` or `acrylic`. A widget keeps its place and size in any finish. `facet`
is a flat top in the widget's own colour with a sharp bevel and `acrylic` its
lettering lit from within, see [Facets](#facets).

`lit` draws SPUR II's light, in the widget's colours and at its size. A
value is light, not a body: a core that runs towards white in a soft halo of
its colour, lying in a groove (the widget's `background`), its scale.

- A `bar`'s value is a pill of light with round ends along the middle of its
  groove (0.62 of its thickness); its end moves in fractions of a pixel, and
  `segments` are pills of their own. `color2` and `color_rules` colour it as
  they colour a flat bar. `glow` (with `glow_radius`) is SPUR II's bar glow,
  light added over the bar: dim along the lit part, rising to the head,
  which runs towards white. `glow_area` (`[x, y, w, h]` on the panel) keeps
  it inside that box, worked out over it at a quarter of the size, as SPUR
  II's glow zones; without it, the glow lies around the bar.
- A `gauge`'s value is a thin arc of the same light on the middle of its
  groove (0.6 of `thickness`), from `start_angle`, with square ends; the
  glow is the arc's own halo.
- A `graph` keeps its fill; its line is light (its core runs towards white)
  in a soft halo of its colour (`glow`, `glow_radius`).
- A `text` or `metric` glows as SPUR II's headings and labels do: its
  colours, blurred by `glow_radius` and `glow` of them, are light added over
  the text and around it (flat, the glow lies beneath the text), inside its
  `glow_area` if it has one. `opacity` fades the letters and not their
  light: at `0` only the light is left, so a copy of a word glows over the
  word itself, as SPUR II's wordmark does.
- Weather icons (`icon` `weather` and the symbols `sun`, `moon`, `partly`,
  `cloud`, `fog`, `rain`, `snow`, `storm`) are built of lit bodies, baked
  from Blender renders and coloured: clouds and the moon in the icon's
  `color`, the sun and bolts in the palette's `warn`, rain in `accent`, snow
  in `text`; fog is bands of mist. Other icons stay line icons.
- A `downloads` widget's progress is light in its groove, as a bar's.
- A `module` hands its finish to its parts: a weather module with
  `"finish": "lit"` shows a lit symbol, a ring module a lit ring. The default for
  a module is `auto`: its kind's own, which is `lit` for a [card](#cards) and flat
  for the rest.

Where light does not read, a widget is drawn flat: bars and rings thinner
than 2 px, icons under 16 px. A finish a type does not have (`lit` on a
line icon) draws it flat, and the theme loader warns once per type. The weather
symbols' layers are prepared before the first frame and kept in the config
folder's `cache/light3d`; they are prepared again when the pictures they come
from change.

### Light edges for plugins

The panel light glints on `LightEdge`s (`libre_panel.render.look`):
`LightEdge(normals, coverage, x, y, strength=1.0)` with `normals` (h, w, 3:
x to the right, y down, z towards the viewer; `(0, 0, 1)` faces the viewer
and shines least) and `coverage` (h, w; 0–1 or 0–255) whose top left lies at
(`x`, `y`) on the panel. `LightEdge.ridge(mask, x, y)` makes every line of a
mask a ridge, as a `ridge` rect is.

- A plugin widget type returns its edges from `edges(widget, ctx, snapshot,
  now)`; a screen calls `context.light_edge(edge)` in `render`.
  `context.light` says whether the theme has a panel light at all.
- A finish (code, subclass of `libre_panel.render.finish.Finish`, made
  available with `register`) calls `renderer.add_light_edge(edge)` while it
  draws.
- Give the edges on every frame that shows them, on the render thread, and
  keep the same objects while nothing about them changes: their gloss is
  worked out again only when the light moves. The gloss lies right on the
  piece that gave the edge, under what is drawn after it.

```python
import numpy as np

from libre_panel.plugins import WidgetType
from libre_panel.render.look import LightEdge


class Plate(WidgetType):
    type = "myplugin.plate"
    api = 1
    spec = {"w": ("int", 200), "h": ("int", 60)}
    kept = {}

    def draw(self, widget, ctx, snapshot, now): ...

    def edges(self, widget, ctx, snapshot, now):
        key = (widget["x"], widget["y"], widget["w"], widget["h"])
        if key not in self.kept:  # a rail along the top and along the bottom
            mask = np.zeros((widget["h"], widget["w"]))
            mask[:3] = mask[-3:] = 1
            self.kept[key] = LightEdge.ridge(mask, widget["x"], widget["y"])
        return [self.kept[key]]
```

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
| `{value:gb}` | `13.5` (a size in GB, whatever unit the reading comes in) |
| `{value:rate}` and `{value:rate_unit}` | `519` and `KB/s`, from 1 MB/s `1.2` and `MB/s` (a data rate: the number and its unit apart, so each can have its own font) |
| `{value:upper}` | `ROUND REPORT` (a text in capitals; `{label:upper}` does so for a reading's name) |
| `{label}: {value:.0f}` | `CPU load: 38` |

For safety, attribute or index access (`{value.x}`, `{value[0]}`), other field
names and huge widths are rejected; the widget then shows its `fallback`.

## Sensor keys

`libre-panel sensors` lists every key available on your machine. Common ones:

| Key | Unit | Source |
|---|---|---|
| `cpu.load`, `cpu.freq`, `cpu.temp`, `cpu.power` | %, MHz, °C, W | psutil (temp on Linux), LibreHardwareMonitor; `cpu.load` also ten times a second from `load` |
| `gpu.load`, `gpu.temp`, `gpu.power`, `gpu.fan`, `gpu.mem.load` | %, °C, W, RPM, % | LibreHardwareMonitor; `gpu.temp` also Linux AMD; `gpu.load` also from `load` (Windows, any vendor) |
| `gpu.hotspot` | °C | LibreHardwareMonitor: the hottest spot of the graphics chip ("GPU Hot Spot", as AMD and NVIDIA cards give it; none where the card has no such sensor), the one of the card `gpu.temp` is read from |
| `mem.load`, `mem.used`, `mem.free`, `mem.total`, `swap.load` | %, GiB | psutil; `free` is what programs can still take without the system swapping, so used and free add up to the total |
| `mem.temp`, `mem.speed` | °C, MT/s | the memory: the temperature of its hottest module (LibreHardwareMonitor, from the module's own thermal sensor: DDR4 and DDR5, with administrator rights and the PawnIO driver as for the CPU's temperature) and the speed it runs at (the firmware's SMBIOS table, the slowest module's: see [Configuration](CONFIGURATION.md#hardware-names)) |
| `disk.load`, `disk.used`, `disk.total`, `disk.read`, `disk.write` | %, GiB, B/s | psutil |
| `net.down`, `net.up` | B/s | psutil |
| `fan.<device>.<name>`, `temp.<device>.<name>` | RPM, °C | every fan and temperature: psutil (Linux), LibreHardwareMonitor (e.g. `temp.cpu.package`, `fan.gpu.gpu_fan_1`) |
| `cpu.core.<n>.load` | % | each core (thread), from 1 |
| `disk.<n>.name`, `.load`, `.used`, `.free`, `.total` | text, %, GiB | each drive, the system drive first |
| `proc.cpu.<n>.name`, `.value`; `proc.mem.<n>.name`, `.value` | text, % | the eight busiest programs by processor time (of the whole processor) and by memory; counted only while a theme shows them |
| `net.ip`, `net.ping`, `net.today.down`, `net.today.up` | text, ms, B | the address towards the internet; ping (a connection to `ping`, see the configuration; only while shown); traffic since midnight or since Libre Panel started |
| `battery.load`, `battery.state`, `battery.left`, `battery.plugged`, `sys.uptime` | %, text, s, 1/0, s | psutil; `state` is "On battery", "Charging" or "Charged" in the language |
| `audio.volume`, `audio.muted` | %, 1/0 | the volume of the default output (0 while muted) and whether it is muted; needs `"audio"` in `providers` (the default list has it on Windows and Linux), see [Configuration](CONFIGURATION.md#sensor-sources-and-their-order) |
| `sun.rise`, `sun.set`, `sun.noon`, `sun.next_rise`, `sun.daylight`, `sun.up`, `sun.progress`, `sun.elevation` | "07:42", s, 1/0, 0–1, ° | computed for the place in `[weather]` (no internet needed); `progress` is how much of the day has passed, empty at night |
| `moon.phase`, `moon.illumination`, `moon.name`, `moon.age`, `moon.full_in`, `moon.new_in` | 0–1, %, text, days | computed; `phase` 0 new, 0.5 full; `name` in the language |
| `media.title`, `.artist`, `.album`, `.source`, `.state`, `.playing`, `.position`, `.duration`, `.remaining`, `.progress` | text, 1/0, s, 0–1 | what is playing, while a theme shows it; the cover is the picture `@media.cover`, the icon of the player `@media.app_icon` |
| `calendar.<n>.title`, `.when`, `.day`, `.time`, `.location`, `.calendar`, `.start`, `.minutes`, `.now`; `calendar.events`, `calendar.today` | text, s, min | the next events (1 = the next or the one going on), [calendars](CONFIGURATION.md#more-sensor-sources) |
| `ha.<entity id>`, `ha.<entity id>.<attribute>` | as Home Assistant says | [Home Assistant](CONFIGURATION.md#more-sensor-sources) |
| `mqtt.<topic with dots>` (`.field` for JSON) | | [MQTT](CONFIGURATION.md#more-sensor-sources) |
| `game.fps`, `game.frametime`, `game.low`, `game.app` | fps, ms, fps, text | the game being played, through [PresentMon](CONFIGURATION.md#more-sensor-sources) (Windows) |
| `game.frametime_peak`, `game.limiter` | ms, text | the longest frame time since the last reading (0 while a game runs and no frame came: a graph of it never stops), and who holds the frame rate down: `cpu`, `gpu` or `cap` |
| `game.name` | text | the name of the game while the `game` service has the panel in game mode ([Configuration](CONFIGURATION.md#game-mode)); its logo is the picture `@game.logo`, which stays after the game for the round report, until the next one |
| `session.time`, `session.fps`, `session.level.<n>`, `session.<peak>_max` | s, fps, %, °C / W / GB | the game being played, added up while it is: how long, the mean frame rate, the share of the time at each frame-rate range, the highest temperature, power and video memory since it began; published by the `memory` service ([Plugins](PLUGINS.md#the-round-under-way)) |
| `cpu.name`, `gpu.name`, `mem.name` | text | the names of the processor, graphics card and memory, shortened for a panel ("Ryzen 5 7600", "GeForce RTX 4070", "Kingston DDR5-5600 32 GB"; `mem.name` is the size, "32 GB", where the modules cannot be read): found by Libre Panel itself, or the text you give under `[hardware]`, see [Configuration](CONFIGURATION.md#hardware-names); LibreHardwareMonitor's two fill in where nothing is found |
| `weather.temperature`, `.apparent_temperature`, `.humidity`, `.wind_speed`, `.code`, `.description`, `.is_day` | °C, %, km/h | Open-Meteo, when enabled |
| `weather.hour.<n>.temperature`, `.code`, `.rain`, `.day`, `.time` | °C, %, 1/0, "15:00" | the hour `n` hours ahead (1 = the next full hour, up to 24) |
| `weather.day.<n>.high`, `.low`, `.code`, `.rain`, `.name` | °C, % | day `n` (0 = today, up to 6); `name` is "Today" or the short weekday |
| `weather.rain.minutes`, `weather.rain.text` | min, text | the rain of the next two hours: `minutes` counts to the rain starting (dry now) or stopping (raining now), and is absent when neither happens in those two hours; `text` says it in the language: "Rain in 20 min", "Rain stops in 40 min", "No rain for the next 2 h" |
| `lhm:/<sensor id>` | | any LibreHardwareMonitor sensor |

`gpu.power` is the whole board (TBP), like LibreHardwareMonitor's "GPU Package".

The forecast keys are counted from the moment they are read, so "the next
hour" stays true between two weather updates. `rain` is the chance of
rain in percent, and `weather.rain.minutes` is counted the same way.

## Sharing themes

Zip the theme folder. Only include fonts and images you may redistribute and
put the license in `theme.json`. Themes cannot run code, and file references
cannot leave the theme folder, so a shared theme is as safe as a picture.
