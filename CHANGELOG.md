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
  and recovery from a hung decoder (the panel is restarted, as SPUR II does;
  after three restarts in a row it asks to replug); it saves nothing on the
  panel. There, values
  glide until the next reading, graphs scroll on every frame, and every
  frame is drawn at its planned time, so animations run evenly

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

**Plugins**
- Plugin API 1 ([docs/PLUGINS.md](docs/PLUGINS.md)): plugins install with pip
  or as a folder in the settings folder (for the downloads); `libre-panel
  plugins` shows what loads
- Services: long-running plugins that publish readings and images, show
  another theme for a while (with a priority when several ask), switch
  modes, post messages and hear about events; `[services]` in config.toml,
  applied while running
- Modes (`[modes.<name>]`) with their own frame rate and theme, e.g. fewer
  frames while a game runs; tray and editor show the active mode
- Screens: a theme can name a plugin screen that draws the whole frame in
  code, with the theme's widgets on top; chosen in the editor with its options
- Widget types from plugins: placed and edited in the editor like built-in
  widgets (fields, building blocks, effects), checked when a theme loads
- Toasts: short messages from services on the panel, in the theme's colours,
  one after the other or taking over at once; the same key refreshes one in
  place, a higher rank takes over, and themes and screens leave out kinds
  they do not want. The theme picks the corner, the hold time, the queueing
  and the style (the built-in card or a plugin's); the editor has it under
  *Theme → Messages*
- Transitions between themes and modes: fade (default), slide, cut, or from
  a plugin, which can take parameters from the service and the theme's
  colours; a transition decides whether a switch meanwhile waits, follows
  or restarts it, and whether toasts wait, go on or come again afterwards
- Theme requests can be limited to one mode (an autopilot only in normal
  mode); editor pages see the panel's readings, the services and the config
- Editor pages: a plugin's own page in the theme editor (under *Pages*),
  with its API and the editor's look (`/static/kit.js`)
- Plugins can bring themes (read-only, like the built-in ones) and sensor
  sources, also as a folder; sources that are cheap to read can follow on
  every frame. Opaque plugin screens without widgets go to the panel
  without compositing
- Windows: `libre-panel autostart enable --elevated` starts Libre Panel at
  login with the highest rights (Task Scheduler), for sensor plugins that
  read the hardware themselves; set up and removed only as administrator

**Sensors**
- psutil, LibreHardwareMonitor (checked against real output), demo;
  Open-Meteo weather (off by default)
