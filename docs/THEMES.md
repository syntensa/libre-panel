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
  "background": { "color": "#0b1016", "image": null },
  "refresh_ms": 1000,
  "widgets": [
    { "type": "clock", "id": "clock", "x": 240, "y": 20, "format": "%H:%M", "font_size": 48, "align": "center" }
  ]
}
```

- `display.model` is an id from `libre-panel models` (or `"custom"` with an
  explicit `width`/`height`). With a model, the size follows from
  `orientation`; a conflicting width/height is an error.
- Colors are `#rgb`, `#rrggbb` or `#rrggbbaa`.
- Widgets are drawn in list order: later ones are on top.
- Unknown fields are ignored with a warning, so newer themes still load.

## Widgets

Every widget has `id` (unique), `type`, `x`, `y` and `visible`. Text-like
widgets share `font` (a `.ttf`/`.otf` inside the theme folder, empty = built-in
font), `font_size`, `color` and `align` (`left`, `center`, `right`; `x` is the
left edge, center or right edge accordingly; `y` is the top).

| Type | Fields | |
|---|---|---|
| `text` | `text` | static label, may contain line breaks |
| `metric` | `sensor`, `format`, `fallback`, `color_rules` | a sensor value as text |
| `bar` | `sensor`, `w`, `h`, `min`, `max`, `color`, `background`, `radius`, `direction`, `color_rules` | progress bar; `direction` `right`/`left`/`up`/`down` |
| `gauge` | `sensor`, `w`, `h`, `min`, `max`, `start_angle`, `end_angle`, `thickness`, `color`, `background`, `color_rules` | ring; angles clockwise from 3 o'clock, default 135→405 |
| `graph` | `sensor`, `w`, `h`, `min`, `max`, `history`, `color`, `fill`, `line_width`, `background` | history line; `min`/`max` `null` = automatic |
| `clock` | `format` | date/time with [strftime codes](https://strftime.org), e.g. `%H:%M:%S`, `%A %d %B` |
| `image` | `src`, `w`, `h` | a picture from the theme folder; `w`/`h` 0 = original size |
| `weather` | `field`, `format`, `fallback` | `temperature`, `apparent_temperature`, `humidity`, `wind_speed`, `description`, `code` |
| `rect` | `w`, `h`, `color`, `radius`, `outline`, `outline_width` | panels, frames, separators |

`color_rules` change the color by value; the highest matching threshold wins:

```json
"color_rules": [{ "above": 70, "color": "#fbbf24" }, { "above": 85, "color": "#f87171" }]
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
| `fan.<chip>.<name>`, `temp.<chip>.<name>` | RPM, °C | psutil (Linux) |
| `battery.load`, `sys.uptime` | %, s | psutil |
| `weather.*` | | Open-Meteo, when enabled |
| `lhm:/<sensor id>` | | any LibreHardwareMonitor sensor |

`gpu.power` is the whole board (TBP), like LibreHardwareMonitor's "GPU Package".

## Sharing themes

Zip the theme folder. Only include fonts and images you may redistribute and
put the license in `theme.json`. Themes cannot run code, and file references
cannot leave the theme folder, so a shared theme is as safe as a picture.
