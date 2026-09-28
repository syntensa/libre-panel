# Changelog

## Unreleased

First preview of Libre Panel.

**Panels**
- Panel catalog: all Turing/TURZX sizes from 2.1" to 12.3" plus compatible
  brands; choose the panel from a menu, layouts rescale
- Driver for Turing V1.x USB panels (PNG path) with reconnect and clear error
  messages; `libre-panel doctor` checks a panel end to end and writes a report,
  including a ruler card that measures pixels hidden behind the bezel
- The panel catalog knows the strip a bezel hides (9.2": 18 px); the editor
  shows it as a guide
- The main loop waits for a missing panel and survives unplugging; with the
  default driver a panel that appears later is picked up without a restart
- Video mode for TURZX USB panels: frames go to the panel's H.264 decoder at
  up to 50 fps (needs ffmpeg), with flow control, ffmpeg restarts, reconnects
  and hung-decoder detection; it saves nothing on the panel

**Themes and rendering**
- Theme format `libre-panel-theme/1`: text, metric, bar, gauge, graph, clock,
  image, weather, icon and rect widgets; colour rules; safe format strings
- Graphics engine: bundled fonts (Barlow, JetBrains Mono), palettes, glow and
  shadows, gradients, segmented bars, smooth graphs, frosted glass, 21 line
  icons with live weather symbols, equal-width digits, values that glide
- Fast frames: only the parts that change are drawn again (pixel-identical to
  a full redraw), slow pieces are prepared in the background so the frame
  rate holds, gauges are drawn from cached parts
- Built-in themes: SPUR II, Libre Default, Orbit (round), Slate (5"),
  Column (3.5" portrait)

**Editor**
- Visual editor in the browser with the same renderer as the panel: snapping
  and guides, multi-select, align/distribute, undo/redo, copy/paste, layers
  with lock and hide, building blocks, palette and font pickers, zoom,
  import/export, "show on panel"

**Background app**
- `libre-panel tray`: tray icon with status, theme, brightness, pause,
  start with system, settings folder, log and quit; the editor has the same
  controls; runs without an icon where there is no tray
- `libre-panel autostart enable|disable|status`: Windows `Run` key, XDG
  autostart entry, macOS LaunchAgent
- Only one Libre Panel drives the panel; starting it again opens the running
  editor. A broken config or theme no longer stops it: it resumes when the
  file is fixed
- `libre-panel quit`, `libre-panel udev-rules`
- Windows: shutting down or signing out stops Libre Panel cleanly instead of
  cutting it off

**Downloads**
- Windows setup (per user, German/English, start menu, optional start at
  login, clean uninstall) and a portable zip
- macOS disk image with *Libre Panel.app* (menu bar app)
- Linux AppImage and a tarball
- Each one is installed, started and removed in the release workflow
- Without a panel, frames go to the settings folder (an app started at login
  has no usable working directory); Quit reliably ends the app on every
  system (a race with the X11 tray and the macOS menu bar was fixed)

**Languages**
- English and German: editor, tray menu, panel messages, and on the panel
  weekday and month names ("Sonntag, 27. September") and weather
  descriptions; `language = "auto"` follows the system, switch in the editor

**Sensors**
- psutil, LibreHardwareMonitor (checked against real output), demo;
  Open-Meteo weather (off by default)
