# Libre Panel

**Free, open-source software for USB system-monitor displays** — the small
"smart screens" sold as TURZX / Turing Smart Screen and under many other names.
Design your own dashboard in a visual editor, pick your panel from a menu, and
show CPU, GPU, memory, network, weather and more. No account, no cloud, no
vendor app.

<p align="center"><img src="docs/images/gallery.png" alt="Built-in themes: SPUR II (1920x480), Libre Default (3.5 inch), Orbit (round), Slate (5 inch) and Column (3.5 inch portrait)" width="100%"></p>

> **Panels:** the driver for Turing/TURZX USB panels (4.6"–12.3", 2.8" round)
> has run for hours on the 9.2", as still pictures and as 50 fps video; the
> other sizes speak the same protocol. The serial panels have drivers too
> (Turing 3.5", UsbPCMonitor, XuanFang, Kipye, WeAct); the serial Turing
> 2.1", 5" and 8.8" follow. A model is listed as "supported" once
> `libre-panel doctor` has passed on it — see
> [supported panels](docs/HARDWARE.md). Without a panel you can still design
> themes and preview them.

## Features

<p align="center"><img src="docs/images/editor.png" alt="The theme editor with the SPUR II theme on a 9.2 inch panel" width="100%"></p>

- **Visual theme editor** in your browser, rendered by the same engine that
  drives the panel: drag and drop with snapping and alignment guides,
  multi-select, align and distribute, undo/redo, copy/paste, layers with lock
  and hide, ready-made building blocks (CPU card, ring, bar rows, clock,
  weather, network …), palette and font pickers, zoom.
- **Every panel size from a menu**: 2.1" round up to 12.3", portrait or
  landscape. Switching the panel rescales your layout.
- **Widgets**: text, sensor values, bars (also segmented), ring gauges, smooth
  history graphs, clock / date, images, weather, 21 line icons (with live
  weather symbols), cards with frosted glass — with glow, shadows, gradients,
  colour rules (e.g. turn red above 85 °C) and values that glide smoothly.
- **Video mode** for TURZX USB panels: up to 50 frames per second through the
  panel's own video decoder (needs ffmpeg), so values glide and graphs
  scroll instead of jumping once a second.
- **Sensors**: CPU, RAM, disk, network, temperatures and fans via `psutil`;
  on Windows also GPU, power and more through [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor).
- **Weather** from [Open-Meteo](https://open-meteo.com) for *your* location —
  off by default, no API key.
- **Nothing hard-coded**: location, units, sensors and panel all live in one
  config file.
- **English and German** in the editor, the tray and on the panel (dates,
  weather); follows the system language.
- **Safe to share themes**: themes are plain JSON plus images/fonts, and cannot
  run code or read files outside their folder.
- **Runs quietly in the background**: tray icon with theme, brightness, pause
  and quit; starts with the system if you want (Windows, Linux, macOS); picks
  up the panel whenever it is plugged in.
- **Plugins** ([plugin API](docs/PLUGINS.md)): services that publish readings
  and switch themes or modes, short messages on the panel, transitions,
  screens drawn in code, widget types, sensor sources, display drivers,
  themes and editor pages; installed with pip or as a folder, so optional
  extras never bloat the core.
- Windows, Linux and macOS. GPL-3.0.

## Install

Download from the [releases](https://github.com/syntensa/libre-panel/releases)
(no Python needed):

| System | Download | |
|---|---|---|
| Windows 10/11 | `libre-panel-…-windows-x64-setup.exe` | installs for your user (no admin rights), start menu entry, optional start at login; uninstall in *Settings → Apps* |
| macOS 11+ (Apple silicon) | `libre-panel-…-macos-arm64.dmg` | drag *Libre Panel* to *Applications*; lives in the menu bar |
| Linux (x86-64) | `Libre_Panel-…-x86_64.AppImage` | make it executable and double-click; no installation |

A portable Windows zip and a Linux tarball are there too. The downloads are
not code-signed yet: Windows SmartScreen asks once (*More info → Run
anyway*), and on macOS you open the app the first time with right-click →
*Open* (on macOS 15: *System Settings → Privacy & Security → Open Anyway*).
After the first start, the icon in the tray (menu bar) opens the theme editor; *Start with
system* makes it start at every login. More in
[Running in the background](docs/BACKGROUND.md).

With Python instead:

```bash
pipx install "libre-panel[usb,tray] @ git+https://github.com/syntensa/libre-panel@v0.1.0"
libre-panel tray                    # background app with tray icon; opens the editor
libre-panel autostart enable        # start it whenever you log in
```

Other commands:

```bash
libre-panel                         # panel + editor in a terminal (Ctrl+C stops)
libre-panel editor                  # only the theme editor
libre-panel run                     # only drive the panel
libre-panel preview spur-ii -o spur.png   # render a theme to an image
libre-panel models                  # all known panels and their resolutions
libre-panel devices                 # which panel is connected?
libre-panel doctor                  # check the panel end to end, write a report
libre-panel quit                    # quit the background app
libre-panel udev-rules              # Linux: the rule that gives your user USB access
libre-panel sensors                 # every sensor key with its current value
libre-panel config init             # write a commented config.toml
libre-panel location "Berlin"       # coordinates for the weather config
```

Close the vendor app first; it keeps the panel to itself.

## Themes

| Theme | Panel | |
|---|---|---|
| `spur-ii` | 8.8" / 9.2" bar (1920×480) | the SPUR II layout: load charts, four sections, light on every value |
| `libre-default` | 3.5" (480×320) | two rings, temperature, disk, network and history |
| `orbit` | round 2.1" / 2.8" (480×480) | three concentric rings around a large clock |
| `slate` | 5" (800×480) | four cards: CPU, GPU, memory, network |
| `column` | 3.5" portrait (320×480) | clock, resource rows and network |

Every theme adapts to any other panel from the editor's panel menu. The engine
behind them: bundled fonts (Barlow, JetBrains Mono), colour palettes, glow and
shadows, gradients, segmented bars, smooth history graphs, 21 line icons
including live weather symbols, and values that glide instead of jumping.
Themes are plain JSON plus images/fonts — see [docs/THEMES.md](docs/THEMES.md).

## Documentation

- [Supported panels](docs/HARDWARE.md)
- [Running in the background: tray, autostart](docs/BACKGROUND.md)
- [Configuration](docs/CONFIGURATION.md)
- [Theme format and sensor keys](docs/THEMES.md)
- [Plugins](docs/PLUGINS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [TURZX USB protocol notes](docs/protocol/turzx-usb.md)
- [Roadmap](docs/ROADMAP.md)
- [Releasing](docs/RELEASING.md)
- [Contributing](CONTRIBUTING.md)

## Credits

Libre Panel stands on the shoulders of
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
(GPL-3.0), whose reverse-engineering documents these panels, and of the SPUR II
project, which verified the USB protocol on real hardware. See [NOTICE.md](NOTICE.md).

"TURZX", "Turing Smart Screen" and other product names are trademarks of their
owners. Libre Panel is an independent project and not affiliated with them.

## License

[GPL-3.0-or-later](LICENSE). Bundled themes carry their own license, stated in
each `theme.json`.
