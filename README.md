<h1 align="center">Libre Panel</h1>

<p align="center">
  <b>Free, open-source software for USB system-monitor displays</b><br>
  for the small "smart screens" sold as TURZX, Turing Smart Screen, WeAct,
  XuanFang, Kipye and under many other names
</p>

<p align="center">
  <a href="https://github.com/syntensa/libre-panel/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/syntensa/libre-panel?color=13E5D7&labelColor=0A1119"></a>
  <a href="https://github.com/syntensa/libre-panel/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/syntensa/libre-panel/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: GPL-3.0" src="https://img.shields.io/badge/license-GPL--3.0-A3AEFF?labelColor=0A1119"></a>
  <img alt="Windows, Linux, macOS" src="https://img.shields.io/badge/runs_on-Windows%20%C2%B7%20Linux%20%C2%B7%20macOS-E0A8FF?labelColor=0A1119">
</p>

<p align="center"><img src="docs/images/hero.png" alt="The Studio theme on a 9.2 inch bar: a large clock, the local weather in a card, and rings for CPU, RAM and GPU" width="100%"></p>

<p align="center">
  <a href="https://github.com/syntensa/libre-panel/releases/latest"><b>Download</b></a> ·
  <a href="#themes">Themes</a> ·
  <a href="#the-theme-editor">Theme editor</a> ·
  <a href="docs/HARDWARE.md">Supported panels</a> ·
  <a href="docs/THEMES.md">Make your own theme</a>
</p>

Plug in your panel, start Libre Panel, and it shows your PC's load,
temperatures, memory, network and the weather outside, in a design you pick
or build yourself. It runs quietly in the tray on Windows, Linux and macOS.
No account, no cloud, no vendor app.

- **Looks good out of the box.** Seven built-in themes from the 0.96" stick
  to the 9.2" bar, with glow, soft gradients, smooth graphs and values that
  glide instead of jumping.
- **Fits your machine without editing.** The names your own CPU and GPU
  report, the weather for the place in *your* config, dates in English or
  German, and sections that step aside where a sensor is missing.
- **Design anything.** A visual editor in your browser, rendered by the
  same engine that drives the panel: drag, snap, align, undo, done.
- **Smooth.** On TURZX USB panels, video mode plays up to 50 frames per
  second through the panel's own decoder, so graphs scroll and numbers glide.
- **Yours.** GPL-3.0, plain JSON themes that cannot run code, everything
  configurable in one file.

## Fits your machine

The same theme on different PCs: Studio shows a calendar sheet where the
weather would be when the weather is off, and the disk where the GPU would be
on a PC that reports no GPU readings. Any widget can do this with one field,
`needs` (see [Theme format](docs/THEMES.md#widgets)).

<p align="center"><img src="docs/images/studio-adapts.png" alt="Studio without weather (a calendar sheet in the card) and without GPU readings (a disk ring in the third slot)" width="100%"></p>

## Themes

<p align="center"><img src="docs/images/gallery.png" alt="Built-in themes: SPUR II (1920x480), Slate (5 inch), Libre Default (3.5 inch), Orbit (round), Column (3.5 inch portrait) and Pico (0.96 inch)" width="100%"></p>

| Theme | Panel | |
|---|---|---|
| `studio` | 8.8" / 9.2" bar (1920×480) | after SPUR II's Studio screen: large clock, weather for your location, CPU, RAM and GPU rings named after your hardware |
| `spur-ii` | 8.8" / 9.2" bar (1920×480) | the SPUR II layout: load charts, four sections, light on every value |
| `slate` | 5" (800×480) | four cards: CPU, GPU, memory, network |
| `libre-default` | 3.5" (480×320) | two rings, temperature, disk, network and history |
| `orbit` | round 2.1" / 2.8" (480×480) | three concentric rings around a large clock |
| `column` | 3.5" portrait (320×480) | clock, resource rows and network |
| `pico` | 0.96" (160×80) | CPU with temperature and memory, large numbers and slim bars |

Every theme moves to any other panel from the editor's panel menu. Themes
are plain JSON plus images and fonts — see [docs/THEMES.md](docs/THEMES.md).
Made one you like? Share it in
[Discussions](https://github.com/syntensa/libre-panel/discussions).

## The theme editor

<p align="center"><img src="docs/images/editor.png" alt="The theme editor with the Studio theme on a 9.2 inch panel, the GPU ring selected" width="100%"></p>

- Drag and drop with snapping and alignment guides, multi-select, align and
  distribute, undo/redo, copy/paste, layers with lock and hide, zoom.
- Ready-made building blocks: CPU card, ring, bar rows, clock, weather,
  network and more; palette and font pickers.
- **Every panel size from a menu**: 0.96" up to 12.3", round ones too,
  portrait or landscape. Switching the panel rescales your layout.
- What you see is what the panel shows: the editor renders with the panel's
  own engine, with demo values or your live sensors. *Show on panel* sends
  it there.

## Features

- **Widgets**: text, sensor values, bars (also segmented), ring gauges,
  smooth history graphs, clock and date, images, weather, 21 line icons (with
  live weather symbols), cards with frosted glass; glow, shadows, gradients,
  colour rules (e.g. turn red above 85 °C) and values that glide.
- **Video mode** for TURZX USB panels: up to 50 frames per second through
  the panel's own video decoder (needs ffmpeg).
- **Sensors**: CPU, RAM, disk, network, temperatures and fans via `psutil`;
  on Windows also GPU, power and more through
  [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor).
- **Weather** from [Open-Meteo](https://open-meteo.com) for your location:
  off by default, no API key.
- **Nothing hard-coded**: location, units, sensors and panel live in one
  config file.
- **English and German** in the editor, the tray and on the panel (dates,
  weather); follows the system language.
- **Safe to share themes**: themes are plain JSON plus images/fonts and
  cannot run code or read files outside their folder.
- **Runs in the background**: tray icon with theme, brightness, pause and
  quit; starts with the system if you want; picks up the panel whenever it
  is plugged in.
- **Plugins** ([plugin API](docs/PLUGINS.md)): services that publish
  readings and switch themes or modes, short messages on the panel,
  transitions, screens drawn in code, widget types, sensor sources, display
  drivers, themes and editor pages; installed with pip or as a folder.

## Panels

The driver for Turing/TURZX USB panels (4.6"–12.3", 2.8" round) has run for
hours on the 9.2", as still pictures and as 50 fps video; the other sizes
speak the same protocol. The serial panels have drivers too (Turing 2.1",
3.5", 5" and 8.8", UsbPCMonitor, XuanFang, Kipye, WeAct). A model is listed as
"supported" once `libre-panel doctor` has passed on it — see
[supported panels](docs/HARDWARE.md).

**Own one of these panels?** `libre-panel doctor` checks it end to end and
writes a report. Attach it to a
[panel report](https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml)
and your model moves from *unverified* to *supported* for everyone.

Without a panel you can still design themes and preview them.

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
pipx install "libre-panel[usb,serial,tray] @ git+https://github.com/syntensa/libre-panel@v0.2.0"
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

## Documentation

- [Supported panels](docs/HARDWARE.md)
- [Running in the background: tray, autostart](docs/BACKGROUND.md)
- [Configuration](docs/CONFIGURATION.md)
- [Theme format and sensor keys](docs/THEMES.md)
- [Plugins](docs/PLUGINS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [TURZX USB protocol notes](docs/protocol/turzx-usb.md)
- [Serial panel protocols](docs/protocol/serial.md)
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
