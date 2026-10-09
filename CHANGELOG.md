# Changelog

## Unreleased

**Fixes**
- A 5" rev. C panel sold as UsbPCMonitor 5" was taken for the round 2.1": its
  USB serial number (CT21INCH asleep, 20080411 awake) fits several sizes. The
  theme's size now decides, failing that the 5", and `libre-panel doctor`
  asks which panel it is (#3)
- `libre-panel doctor` uses the panel and port set in the configuration
- A panel set up as the wrong kind (a rev. A model on a rev. C panel's port)
  no longer stays dark without a word: Libre Panel names the models that fit
- Waking a rev. C panel works with `port` set to its sleeping port
- CI also runs on Python 3.14

## 0.4.0 — 2026-10-08

Fifteen new modules, 25 in all: every temperature, core, drive and the
busiest programs; clocks for any time zone, the sun and the moon,
countdowns; what is playing, your calendar, Home Assistant and MQTT values,
slideshows and the frame rate of your game. Music on Windows and the game
frame rate (PresentMon) have not yet run on a real machine: reports welcome.

**More modules**
- Temperatures and fans: every sensor as a bar, CPU and GPU first, warm and
  hot in the look's warning colours; the fans beside them or as tiles below
- CPU cores: the load of every core as tiles or columns, the history when big
- Drives: every drive, how full and how much is free; reading and writing
- Processes: the busiest programs by processor time or memory
- Network details: IP address, ping, rates and today's traffic
- Battery: charge, charging or not, time left; "No battery" on a desktop
- Dashboard: readings of your choice as tiles
- Widget type `list`: readings by name or pattern (`temp.*`), as rows, bars,
  columns or tiles, as many as fit; a reading under two names is shown once
- Sun & moon: sunrise and sunset under the day's arc, the moon's phase,
  daylight and the next full moon; computed for the place in `[weather]`,
  no internet needed
- Analog clock, world clock (rows, tiles or clock faces) and clocks in any
  time zone (`timezone`)
- Countdown to a day or a moment, every year with "12-24"
- Picture and slideshow: pictures from the theme folder fill the module and
  take turns; the editor adds them
- Widget types `analog`, `countdown` and `moon`; pictures can `fit`
  (contain, cover), round their corners and take turns (`slides`)
- Music: what is playing with its cover and progress (Windows media
  controls, MPRIS players on Linux, Spotify and Music on macOS)
- Agenda: the next events of your calendars
- Game FPS: the frame rate of the game being played, its 1% lows and frame
  time, through Intel's PresentMon on Windows (not yet tried with real games)

**Sensors**
- Every core (`cpu.core.<n>.load`), every drive (`disk.<n>.*`), the busiest
  programs (`proc.cpu.<n>.*`, `proc.mem.<n>.*`), IP address, ping and
  today's traffic, battery state and time left
- LibreHardwareMonitor: every temperature and fan under a friendly key
  (`temp.cpu.package`, `fan.gpu.gpu_fan_1`)
- Costly readings (processes, ping) are only taken while a theme shows them
- `sun.*` and `moon.*`: sunrise, sunset, daylight, the moon's phase
- Calendars (`.ics` addresses and files, with repeating events), Home
  Assistant and MQTT as sources; their readings work in every widget
- Sensor sources can have pictures (`@media.cover` in an image widget)
- `{value:clock}` shows a length as 3:07

## 0.3.0 — 2026-10-08

Modules: building blocks that snap onto a grid and show more the larger they
are (a big weather module shows the days and hours ahead), six looks, and an
editor where a theme takes two minutes.

**Modules**
- Building blocks that fill cells of a grid and lay themselves out for their
  size, like home-screen widgets: clock, calendar, weather, ring, big number,
  history, bars, network, system and title. The larger a module, the more it
  shows: a CPU ring of one cell shows the load, two cells add the
  processor's name, temperature and power, three a history graph
- A theme's `grid` (automatic by default: 8×2 on a 9.2" bar, 3×2 on a 3.5"
  panel, clear of strips the bezel hides) and its `style`: cards (glass,
  flat, outline, none), corners, glow, backdrop and fonts. Six looks set
  palette and style at once: Arctic, Neon, Graphite, Paper, Sunset, Mono
- A module whose readings are missing shows something else: the disk instead
  of a GPU, a calendar sheet instead of the weather
- Text can be held to a width (`max_width`): a smaller font or "…"
- `needs` takes several conditions
- The weather module shows a forecast when there is room: days or hours
  (or both on a big one), as columns beside or under the weather now, or as
  rows in a tall module. The editor sets it: automatic, days, hours, both,
  off, and the hours between two columns
- The calendar module shows the month's days when it is large; a big weather
  module without weather does the same
- The weather now comes with its forecast: `weather.hour.<n>.*` for the next
  24 hours and `weather.day.<n>.*` for seven days, counted from the moment
  they are read, with weekday names in the language
- Widget type `calendar`: the month as a grid, today marked
- A weather icon can follow any code (`sensor`, e.g. tomorrow's), at night
  with the moon where the forecast says so

**Editor**
- A module library with live pictures in the theme's look: drag a module
  onto free cells (they light up) or click it to add it where there is room
- Modules move by cells, swap places when dropped on each other, and grow or
  shrink at their edges and corners, laid out again while dragging; the
  arrow keys move them by a cell
- Module settings as plain choices: what it shows, title, colour, what to
  show when readings are missing, card, size; *Detach* turns a module into
  single widgets
- Six looks in one click; the theme settings get the grid and the module
  style (cards, corners, glow, backdrop, fonts)
- *New* starts from layouts for the chosen panel (overview, performance,
  calm) in a chosen look, or from an empty grid or a free layout
- Switching the panel moves modules onto the new grid without overlaps

## 0.2.0 — 2026-10-07

Studio, a second theme after SPUR II; themes that fit each PC by themselves;
and drivers for the serial panels.

**Panels**
- Drivers for serial panels: Turing Smart Screen 3.5" and UsbPCMonitor 3.5",
  5" and 7" (rev. A; the UsbPCMonitor says its size), XuanFang 3.5" and
  flagship (rev. B), Turing 2.1", 5" and 8.8" (rev. C; a sleeping panel is
  woken), Kipye Qiye 3.5" (rev. D), WeAct Display FS V1 3.5" and 0.96".
  Found by their USB ids and serial numbers, or name the port with
  `device.port`. Only the changed part of a frame is sent. `libre-panel
  doctor` checks serial panels too. They follow the protocols of
  turing-smart-screen-python and have not yet been tried on a panel, so they
  are listed as "unverified"

**Themes and rendering**
- Built-in theme Studio for 8.8" and 9.2" bars, after SPUR II's Studio
  screen: a large clock, a card with the weather of your location (a
  calendar sheet while there is none), rings for CPU, memory and GPU (the
  disk on a PC without GPU readings) and the names of your own hardware
- `needs` on any widget: show it only while a sensor has a value (or, with
  `!`, while it has none), so a card, its labels and icons go with their
  readings. Themes fit each PC without editing: no GPU readings, no GPU
  section
- Built-in theme Pico for tiny panels (160×80, WeAct 0.96"): CPU with
  temperature and memory
- Graphs can move a pixel per frame in video mode (`per_frame`, editor:
  *per frame (video mode)*), as SPUR II draws them: every frame adds the
  gliding value as a point, so the curve moves visibly even when readings
  come once a second. The SPUR II theme uses it; its graphs show the last
  18 s in video mode and 5 minutes otherwise
- Frames where one widget changes every frame cost less: what lies under it
  is kept per region instead of being laid again

## 0.1.0 — 2026-10-01

The first release of Libre Panel.

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
  after three restarts in a row it asks to replug); a slow first start of
  ffmpeg is waited for, and an ffmpeg behind a package manager's shim
  (Chocolatey, scoop) is ended with it. It saves nothing on the panel.
  There, values glide until the next reading, graphs scroll on every frame,
  and every frame is drawn at its planned time, so animations run evenly

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
