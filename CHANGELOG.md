# Changelog

## 0.5.6 — 2026-10-12

Three new screens for the bars: one for music, one for the weather and one clock with the
month, in digits or on a face.

**New**
- Encore: what plays with its cover large and the cover's colours blurred behind the whole
  screen, the state in the panel's language, the time with rolling digits, its seconds and a
  line through the minute, and the volume as levels that rise as on a mixer (a crossed
  speaker while muted)
- Nimbus: the weather now and the next hours, the next days with their moving symbols, rain,
  low and high, the moon as it is lit tonight with the days until the next full and new moon,
  and a small clock. Without a place, a calendar sheet with the month and a line that says
  where the weather is set
- Almanac: the time large beside the month with today marked and the week; Almanac Analog has
  a clock face whose second hand glides to each second instead
- Modules: *Volume* (a speaker, the level and levels that rise; upright in a tall module),
  *Moon* (large, with its phase and the days until the next full and new moon) and *Next
  days* (side by side when long, one under the other when tall or big)
- Themes: a clock face's second hand may glide to the second (`"hand": "glide"`, as a clock's
  rolling digits) or go round (`"sweep"`), for a millisecond or two a frame; the clock faces of
  the analog and world clock modules glide. Bars may rise as levels (`rising`), pictures be
  blurred into a backdrop of their colours (`blur`), and a text may be one of Libre Panel's own
  words in the panel's language (`translate`) and in capitals (`uppercase`); icons `volume` and
  `mute`

## 0.5.5 — 2026-10-12

Fans in the look of your own fans, graphs as fluid as SPUR II's everywhere, and modules that
are easier to place.

**New**
- Fans: the rotor a fan widget turns may now look like Noctua's first NF-A12x25 (nine broad
  blades swept forward), the first 140 mm NF-A14 (seven blades) or Libre Panel's own fan (six
  round-tipped blades with a lit ridge round a hub with a ring of light), besides the nine
  blades of Noctua's G2 it always had. Each in the theme's colour or its own (Noctua's brown
  rotor in a beige frame with brown pads). *Settings → Sensors → How the fans turn* chooses
  the look and the colours for all fans and for each, with a picture of every look; a theme
  may set `look` and `paint` on a fan of its own
- Design: the *module grid* (View and snapping) moves and grows modules by whole cells
  (coarse, as the pictures of the modules show them), half ones (medium, the default) or each
  line of the theme's grid (fine, as before); Alt while dragging goes line by line

**Fixes**
- Graphs: every graph now moves 25 pixels a second, as SPUR II's (a step of half a pixel in
  every frame at 50 fps), in every theme and as a module, unless it sets its own `seconds`.
  Lumen's moved 8 px a second, Studio's network graphs 2, and they stood between their steps
  or blurred between them; Lumen's now show the last 19 seconds instead of a minute
- Design: a module from the library on a theme with a fine grid (Lumen's) was one sliver of
  it, hard to see or click. It now comes in whole cells, chosen, with its settings at once
- Design: the "Fan with speed" block read the graphics card's fan in three parts and named it
  with a fixed text. Several parts selected together that read one sensor now have one picker
  for all of them, and the block shows the fan's own name. The ring instrument's fan shows
  its sensor in the inspector, and its title is the fan's name too
- Windows: a setting changed just as the panel read `config.toml` (a service switched on or off
  right after another change) could be refused with "access denied": Windows cannot replace a
  file another reader holds open. The write now tries again a moment later

## 0.5.4 — 2026-10-12

The motion of the SPUR II mod: numbers that count, values in the next frame, graphs without
steps, and a clock whose digits roll.

**New**
- Clocks: a clock's `digits` may `roll` on the second (the old digit moves up and out, the new
  one comes in from below, soft at the edges; all that change roll together) or `fade`, for
  `digits_seconds` (0.42) after it, as the SPUR II mod's clocks do. The rest of the second the
  clock is its plain text, the same pixels as before. Finished lettering (facet, acrylic) rolls
  as it is lettered. The SPUR II themes, Studio and Lumen roll their clocks; others jump as before
- SPUR Studio: the seconds run under the clock fills its groove once a minute, without steps,
  with a soft head. `time.second` is the seconds of the minute at each frame's time, and a bar's
  `end_dot` gives a bar such a head

**Fixes**
- The processor's, graphics card's, memory's and network's numbers and the fans'
  speeds jumped once a second while the rings glided. Every number that shows an amount now
  counts to a new reading with its bars and rings, in the modules and in the built-in themes
  (`smooth`)
- LibreHardwareMonitor's readings came up to three seconds late and now and then one was
  skipped: the sensor service, its client and the panel's snapshot each kept their own second.
  A new reading now shows in the next frame, and the service is asked twice in each of its rounds
- Music: a new song, play and pause showed up to a second late; they now show in the next frame,
  and the position moves on at each frame's time
- Video mode: a slow graph (a minute across, as Lumen's) stood still for two or three frames and
  then stepped half a pixel. Its steps are now cut into parts, drawn ahead in the helper thread,
  so it moves on in every frame. Graphs that move a step a frame (SPUR II's) stay as they were

## 0.5.3 — 2026-10-12

Fans by their own names.

**New**
- Fan names: a mainboard wires its fan headers its own way, so a theme cannot know which fan is
  the intake. Each fan now goes by its name: one of your own (*Settings → Sensors → Fan names*,
  which shows each fan's speed now so that you can tell them apart; `[[hardware.fans]]` in
  `config.toml`), else the name FanControl gives it, else the sensor's own ("Fan #1"). Lumen and
  the SPUR II themes show it; the SPUR II themes no longer write "Intake", "CPU" and "Exhaust"
  beside the first four headers of every board. `GET /api/hardware` lists the fans,
  `POST /api/hardware` takes `{"fans": {"fan.lpc.fan_1": "Intake"}}`

## 0.5.2 — 2026-10-12

The mainboard's fans on Windows, and values that follow as fast as in the SPUR II mod.

**Fixes**
- Windows: the mainboard's fans, and the temperatures and voltages of its Super I/O chip, did not
  appear (only the GPU's fan did). LibreHardwareMonitor lists them only once an update has read
  them, and Libre Panel took the list before the first update and never again. Each part is now
  updated first, and sensors that appear later (a fan that starts turning, a graphics card's fan
  after its zero-RPM mode) are found within a minute, through the sensor service and inside
  Libre Panel alike
- Video mode: values reach a new reading in about half a second and then hold, as in the SPUR II
  mod, instead of gliding until the next reading (about a second): numbers, bars and rings follow
  a change twice as fast. A theme's `animation.smoothing_ms` sets it, as it does on still frames
- The app: a sensor this PC does not have no longer shows a made-up value in the sensor picker and
  the inspector. Sample values are greyed, marked, and `/api/readings` says `sample` for each
- The app: a module's resize handles stand at its new edges at once after a resize (they followed
  a preview later, so a quick second drag could start at the old edge), and keys typed right after
  Ctrl+K go into the palette, which gives the focus back where it was

## 0.5.1 — 2026-10-12

Fixes from the first installs of 0.5.0 on Windows, above all the app's window.

**Fixes**
- Windows: the app's window did not open. It died at once, before it showed, on every PC: it was
  given a PNG as its icon, which .NET cannot use as one. It now takes the program's own icon. A
  window that still cannot start (a broken WebView2) opens the app in the browser instead of
  nothing, and `logs/window.log` says why. The release build opens a real window on Windows
  before it is published (`libre-panel window --check --open`)
- A theme that `config.toml` names but that is not installed any more (removed, or one of a plugin
  that is gone) no longer leaves the panel dark until the file is changed: the panel shows its
  own theme (Lumen on the 8.8" and 9.2", Libre Default elsewhere), and the tray menu, its tooltip
  and *Start* name the missing theme and say how to choose another. `config.toml` stays as it is
- Windows autostart: a task named "Libre Panel" in Task Scheduler that is switched off, or that
  starts another program (a leftover of an older copy or of a mod), showed *Start with Windows* as
  on and kept the normal start from being written. Only Libre Panel's own task, switched on,
  counts now; another one is left alone, and *Settings → System* and `libre-panel autostart
  status` say how to remove it

## 0.5.0 — 2026-10-12

Libre Panel's biggest release: the panel moves, and an app of its own designs it.

![Lumen, Libre Panel's own theme, in video mode on a 9.2" panel](https://raw.githubusercontent.com/syntensa/libre-panel/v0.5.0/docs/media/video-mode.gif)

**Highlights**
- **Video mode up to 50 frames a second**, on by itself on the 9.2" once the panel's standby clip
  is named (the downloads carry the ffmpeg it needs): numbers count, rings and bars glide, graphs
  scroll on every frame, fans turn at their speed.
- **Lumen**, Libre Panel's own theme, is the default on the 8.8" and 9.2" bars, and Libre
  Default (every other panel) and Studio are renewed in its look: values as light in milled
  grooves, faceted lettering, depth dust.
- **Cards** that change with a line of light: what plays (with the time left), else the weather
  with moving symbols, the next hours and the rain, else the day; the network card shows a
  download while one runs.
- **Notices and game mode**: the volume, a new track, warnings and devices as a band or a card;
  the tube between themes; a game with an entrance, PresentMon's frame rates and a round report.
- **The app**, in a window of its own: Start, Design with drag-and-drop building blocks, Look,
  Live, Settings for all of `config.toml`, Ctrl+K for everything; every theme moves to every
  panel size. The old browser editor is gone.
- **Out of the box.** The downloads carry what the features run, in a `tools` folder: ffmpeg
  (Windows and Linux), PresentMon and PawnIO's installer (Windows). The Windows setup installs
  the sensor service, the PawnIO driver and the frame-rate rights in three checked boxes, after
  one question for administrator rights; in the portable zip *Unlock all sensors* does the same;
  on Linux one click and one password (*Set up sensors and panel access*) lets your user use the
  panel and loads the driver of the mainboard's fans. Libre Panel itself runs without
  administrator rights.
- **Hardware read by Libre Panel itself.** Windows: LibreHardwareMonitor is built in, read inside
  Libre Panel or through the sensor service. Linux: the new `linux` source reads the kernel's
  chips, AMD and Intel graphics from sysfs and NVIDIA through NVML, with no root. The hardware's
  names are found by Libre Panel and can be typed over.
- **Optional services**, all off until you switch them on: notices, game mode, autopilot,
  devices, memory and round report, cooling analysis (read from FanControl), downloads.
- **Windows and Linux.** macOS is no longer supported.

<p>
  <img src="https://raw.githubusercontent.com/syntensa/libre-panel/v0.5.0/docs/media/card-ignition.gif" width="49%" alt="Lumen's card slot: the playing track gives way to the weather, with a line of light between the two">
  <img src="https://raw.githubusercontent.com/syntensa/libre-panel/v0.5.0/docs/media/game-entrance.gif" width="49%" alt="Game mode: the screen closes to a line, the game's name lights up, the game screen opens">
  <img src="https://raw.githubusercontent.com/syntensa/libre-panel/v0.5.0/docs/media/app-drag-module.gif" width="49%" alt="The app's Design view: a module dragged from the library onto the panel, showing more as it grows">
  <img src="https://raw.githubusercontent.com/syntensa/libre-panel/v0.5.0/docs/media/app-look.gif" width="49%" alt="The app's Look view: a colour world for the whole theme, and a test notice">
</p>

**New: motion**
- What moves goes on from theme to theme: a value that glides when the theme switches glides on in
  the next one (the main loop keeps it; the app's previews have their own)
- Numbers glide too: a `metric` with `"smooth": true` counts to a new reading together with the bars
  and rings of its sensor
- Threshold colours fade in over a fifth of a second, and a bar and its number change colour
  together
- Graphs in video mode move on every frame: they draw the gliding value 25 times a second and move
  in half-pixel steps, and their newest point is the value the numbers show. `seconds` sets the time
  a graph spans in video mode, whatever the frame rate; a theme that comes on shows its graphs full
- A theme coming on is built and drawn once in the background while the panel goes on showing the
  one before, so a switch does not freeze a video. The first theme is drawn that way too, while the
  loop is built (the start waits for it two seconds at most), with the pictures of the fans' rotors
  and the bevels of faceted lettering made before the first frame, so the first frame takes a few
  milliseconds. Screens from plugins can load what their first frames need meanwhile:
  `Screen.prepare()`
- Frames stay on time while the PC is busy, as SPUR II keeps them: while it drives a panel, Libre
  Panel pauses less for garbage collection and, on Windows, asks for a 1 ms timer and runs the
  threads that feed a video panel above normal priority
- Drawing is faster: the rounded cards behind modules about five times (only their round corners are
  drawn three times larger), so a theme's first frame takes half as long; numbers that change a
  digit at a time about six times (each digit is drawn once and kept); bars and rings are drawn
  again only when they move by a visible amount, and pieces go onto the frame in place

**New: Libre Panel's themes**
- Lumen, Libre Panel's own signature screen for the 8.8" and 9.2" bars, in the app's colours and
  type on the dot grid of the app's stage. On the left what plays (the weather with its moving
  symbols and the next hours when nothing does, a calendar sheet when there is neither), in the
  middle the time with its seconds, the load of the CPU and GPU over the last minute as light that
  scrolls, and the fans turning with their speed, on the right lit rings for the CPU, GPU and RAM
  named after your hardware, over the network (a download in its place while one runs). Frames are
  flat lines; it keeps clear of the 9.2"'s hidden strip and moves to the other panels
- Studio, renewed: its card shows what plays, else the weather (the weather card, with its moving
  symbol and the next hours), else a calendar sheet, and changes with a line of light. The card is
  wider than 0.4's, and the rings sit 25 px further right; they are light in milled grooves, with
  the names of the processor, the memory and the graphics card under them. The clock, the date and
  the network keep their places
- Libre Default, renewed, the theme a new install starts with on every panel but the bars: lit rings
  and disk segments, a CPU history that scrolls through the last 30 seconds, numbers that glide and
  the app's colours; the corner that showed the weather in 0.4 shows what plays, else the weather,
  else the day

**New: the look**
- Values as light, for any theme, after SPUR II's look: a widget's `finish` and the theme's
  `look`. `"finish": "lit"` draws a bar's or a ring's value as light in its milled groove (a core
  running towards white in a soft halo: a pill with round ends on a bar with SPUR II's glow,
  kept inside a `glow_area` if it has one, a thin arc on a ring), a graph's line as light in a
  halo, a text's glow as light added over it as on SPUR II's headings (its `opacity` fades the
  letters, not the light), and the weather symbols from lit bodies, in the widget's colours
  and at its size (bars, rings, graphs, texts and icons; modules hand it to their parts)
- `look.grid` lays SPUR II's grid over the backdrop; `look.light` is one light for the panel:
  the sun, where it stands for the place in `[weather]`, glints on ridges, and a sweep of
  light runs across at the start, on the hour, on a new track and after a tube or entrance
  transition. The light layers are prepared before the first frame and kept in the cache
  folder. In the app: *Grid background* and *Panel light* under *Look*, *Finish* for a widget
- `"finish": "facet"` on `text`, `metric`, `clock`, `weather`, `countdown`, `bar`, `gauge` and
  `graph`: a flat top in the exact colour and a sharp bevel lit from the top left, like SPUR
  II's lettering. Lettering is bright metal that mirrors the sky above it (the accent) and the
  ground below (the background), with a light touch up to 28 px and the full look from 52 px;
  `"finish": "acrylic"` is the same lettering lit from within, as the "II" of the wordmark.
  Bars and rings are raised in a milled track; a graph's line becomes a ridge on the plateau
  of its fill with a halo of its colour (a slimmer ridge on a bar panel; the halo ends where
  SPUR II's does, and a graph that hangs from its top has it mirrored). Text keeps the pixels
  of the flat text; shapes are built once per size and colour, so it costs no more than flat
- Depth dust: `"look": {"dust": true}` lets fine grains fall from the letters, rails, rings and bars
  into the depth behind the panel, in the colour of what they come from, a few of them as soft
  discs. Nothing in the theme says where the sources are: the dust reads the frame the panel showed.
  `count`, `rate`, `depth`, `strength`, `colors` and `areas` (rectangles with or without dust) set
  it; *Look → Depth dust* has them. It is drawn on a video panel and in the app's preview, never on
  a panel that is sent changed regions, costs nothing while the theme has none, and adds a
  millisecond or two to a frame at 1920 × 480
- A `fan` widget: a rotor of nine lit blades that turns with a fan's RPM (calm and with inertia,
  standing at 0 RPM, dimmed without a reading) in a ring that shows its share of `max_rpm`, or in a
  square frame; *Fan with speed* in Design's library adds one with its name and value
- Weather symbols that move: the widget type `symbol` follows a weather code (`weather.code`,
  `weather.hour.3.code` ...) by day or night: the sun's rays turn, rain, drizzle and snow fall,
  clouds drift and breathe, stars twinkle, fog sways and a bolt flashes now and then, as on SPUR
  II's weather card. Flat, or with `"finish": "lit"` from lit bodies; `moving` and `offset` keep a
  row of them out of step. It moves where the panel is driven faster than once a second, and stands
  still in the app's preview
- Ridges: a `rect` with a `ridge` name is drawn as SPUR II draws its frames, a ridge with two
  facets under the guide light, the lit one glowing a little; rects with the same name are one
  ridge, joined at mitres. Rects without a name stay flat

**New: cards, notices and transitions**
- Cards, after SPUR II's Studio screen, as modules that adapt to any panel: `playback` (the cover,
  the state, title and artist, the app with its icon, the progress), `forecast` (a moving symbol,
  the day's range, the details, the rain and the sun, the next hours), `instrument` (a ring of light
  with its number inside and the readings of the part beside or below it, or a fan with its speed),
  `download` (what loads, its progress, the rate and the time left, the speed of the last minutes:
  the new widget type `downloads`, which reads the state of the `downloads` service) and `netcard`
  (the network, with a download in its place while one runs). Cards are lit by themselves (a
  module's `finish` is `auto`: its kind's own), their frames are flat thin lines, and where a module
  is too small for its card, the older module of its kind takes over (the weather module for the
  forecast card, the music module for the playback card). `time` on the playback card and the music
  module: `remaining` writes the time left at the right of the progress with a minus (`-2:24`), as
  SPUR II's Studio does
- `cards`, a slot that shows one card of several: the first that has something to show (a
  track that plays or was paused a short while, the weather, a download that runs), or by a
  rule of its own on a line (`forecast if weather.temperature`). A change is SPUR II's: a line
  of light ignites at the top edge, the card on show rolls up into it and the new one rolls
  down out of it, on the frame's own time, so it takes as long at 50 frames a second as at 5.
  The slot's frame is a thin line in the colour of the card on show: the cover's, the
  weather's, the network's. `slide` (seconds, 0: none): a new track on a playback card that
  stays on show moves in from the right while the old one moves out to the left, a gap
  between them that closes; the parts are made once and each frame moves and cuts them
- The band, a style for messages after SPUR II (`"toast": {"style": "band"}`, or *Look →
  Notices* in the app): a glowing edge ignites from the centre and the band rolls down from
  it, with a title, a cover or icon and a progress bar that glides; turning the volume changes
  it in place without blinking, another message rolls down inside the open band, and one
  already leaving rolls back up. It fits every panel from 3.5" to the 9.2" bar, either way
  round
- Two transitions after SPUR II, cheap for a panel's video encoder: `tube` (a line ignites at
  mid-height and opens onto the new screen, live) and `entrance` for a game or another mode (the
  screen closes to a line, the game's logo or name lights up while "Game mode" or "Game ended" types
  in, then the new screen opens). `transition` in `config.toml` takes both

**New: the app**
- A window of its own instead of the browser (pywebview: WebView2 on Windows, WebKitGTK on Linux),
  one at a time, keeping its size. A click on the tray icon and *Open Libre Panel* show it on
  *Start*, the tray's *Theme editor* and *Settings* on their views; `libre-panel start`,
  `libre-panel editor` and a second start open the same window or bring it to the front. Where no
  window can open (no WebView2 runtime, no GTK with WebKit, the Linux AppImage) the browser opens
  instead, and `libre-panel editor --browser` asks for it. With pip on Windows:
  `pip install "libre-panel[window]"`; on Linux see [Running in the
  background](docs/BACKGROUND.md#the-apps-window)
- *Start*: the panel at a glance (what it shows, its picture, pause, brightness, upside down), the
  themes made for it, one click each, the way on to Design, Look, Live and Settings, and what the
  panel could not read (the tray's sentence) with the way to its details. *All* is the gallery of
  every installed theme (built in, from plugins, your own), each drawn by the panel's renderer:
  Libre Panel's own and yours first, a set that belongs together (a theme's `collection`, such as
  *SPUR II · Legacy*) closed at the end; a pencil opens a theme in Design. On a first start (no
  `theme` in `config.toml`) it asks "How should it look?", without the tabs in the header (the views
  behind them have nothing to show before a theme is on the panel), with Libre Panel's own themes
  for the panel it found, or the one named under *Another panel*, the panel's default first and
  marked *Default*; *Start empty* and *Open a theme file* are there too
- *Design*, the theme editor: the panel in the middle; modules, building blocks and widgets from a
  library with pictures in the theme's colours, to drag onto it; a toolbar over the selection for
  what it shows, how it is drawn (light, facet, acrylic lettering, flat: those that show on it), its
  colour and size; a short inspector with the rest under *Fine-tuning*, the six looks in the theme's
  settings; corners that resize, snapping guides and steps (1, 4, 8 or 16 px), several widgets at
  once, layers with lock and eye, the theme's pictures and fonts. The panel's pages are tabs over
  it: the normal theme, game mode's, the round report's and those of plugins' modes, each with its
  frame rate. The sensor picker lists every key, also those a source reads only while a theme shows
  them (the volume, the rain text, the game's frame rate and what holds it down). The preview shows
  demo values unless *Live values* is on, with a round report, a game logo, a player icon and a
  download that runs, so that the widgets which show those are complete, and 1900 readings of
  history, so that the wide graphs are full. A theme that comes with Libre Panel is saved as a theme
  of your own, under a name you give it
- *Look*, the theme's look beside the picture of the panel: the six colour worlds and the
  palette; how rings, bars, graphs, lettering, icons and modules are drawn (light, facet,
  acrylic, flat) with their glow; the panel light (the sun, its strength, the sweeps and when
  they run); the grid; the depth dust with its colours and areas; how notices look (card,
  band, a plugin's), shown in the preview as a test notice. Every change is one undo step and
  the preview follows. Ridge frames are not offered; a theme that has some can make them flat
  under *Advanced*
- *Live*, what the panel shows and how it runs: its picture, the frame rate against the
  target, the data rate, the drawing time, the video queue and the faults; the sensor sources
  with their state and the hint about missing readings; the services with a switch each and
  *Restart*; test notices of every kind and what became of them; the end of the log. Pause and
  brightness are in the header. It polls the console API while it is shown
- *Settings* change `config.toml` for you, comments kept (a setting that is new to the file is
  written under the comment that explains it), so that nothing needs a text editor: *Panel* (the
  panel that is found; the video mode with what it still needs, its frame rate and the name of the
  standby clip, written only when you type it; brightness; upside down; under *Advanced* the driver,
  the picture file, the serial port and ffmpeg), *Sensors* (the sources, on and off and in their
  order; how LibreHardwareMonitor is read, with what it says about administrator rights and PawnIO;
  the reading interval), *Services* (each one on or off with its state, *Restart*, and the options
  it declares), *Weather and place* (the weather, its units and interval, and a place found by its
  name: the name goes to Open-Meteo's search only when you press *Search*, and the place is kept to
  about a kilometre; `[weather]` has `place`, the name the app shows for the coordinates), *Start
  and system* (starting with the system; the highest rights only when you ask and only for an
  administrator; the language) and *About* (the version, the licence, the notices, how to report a
  panel, the folders with *Open folder*). What the running app cannot take in by itself, the sources
  and the weather, says so and has a *Restart the panel* button; everything else is followed at once
- The video mode starts without editing `config.toml`: where the name of the panel's standby clip is
  all it lacks (a panel it is proven on, the 9.2", and ffmpeg found), *Start* asks for it once, on a
  first start and afterwards as a card, with the name the vendor app uses, `usr/data/standby.h264`, in
  the field. *Use it* writes `video.local_clip`, and the running panel switches to video mode by
  itself, without a restart; *Not now* writes no name. Nothing is written before the click, and
  either answer sets `clip_asked = true` under `[video]`, so it is not asked again. While it waits,
  the tray menu has a line, *Smooth motion: confirm the clip name…*, that opens Start at the card.
  *Live* says whether the video mode is on (with its frame rate) or what it lacks, as *Settings →
  Panel* does. `GET /api/clip` gives `{ask, name}`
- A Libre Panel that runs with the highest rights (as administrator or root) obeys what
  `config.toml` names, so there Settings do not change a setting that names a program, a library or
  a file (`video.ffmpeg`, the picture file `device.output`, a path under `[sensors.*]`, the files
  and folders a service's options name): the app says to set it in `config.toml`, which Libre Panel
  reads from your own folder
- The hardware's names: *Settings → Sensors → Hardware names* shows the processor, graphics
  card and memory names the sensors detect, and a text typed there takes their place on the
  panel (*Automatic* brings the detected one back). In *Design* the same field sits beside a
  widget that shows one of them. The app's server answers `GET /api/hardware` with the found
  and the written name of each part (`{"cpu": {"detected": ..., "custom": ""}, "gpu": ...,
  "memory": ...}`; `?refresh=1` asks the computer again), and `POST /api/hardware` sets any of
  `cpu`, `gpu` and `memory` (an empty text gives the found name back) through `config.toml`,
  comments kept
- Everything in the app is a command: Ctrl+K finds it by a few letters (themes, modules,
  sensors, panels, the language, the panel's controls), with the keys it has
- Two pages of the app's own, built in like the services and written with the plugin API
  (*Pages* in *Design*, or Ctrl+K). **Control**: the live picture of the panel, its numbers as
  tiles, each sensor source (LibreHardwareMonitor says how it is read, whether it has
  administrator rights, whether PawnIO is there), each service with a *Restart*, a test
  message of each kind with whether it shows or why not, the theme of the normal and the game
  mode in `config.toml`, the end of the log. **Autopilot**: the autopilot's rules and settings
  in `config.toml`, on and off, and which rule holds and what it sees. Without the background
  app (`libre-panel editor`) they say so
- The console API: the two pages are a JSON API that a screen of any design can call
  (`/api/plugins/control/` and `/api/plugins/autopilot/`, see [The console
  API](docs/ARCHITECTURE.md#the-console-api)): the live picture as JPEG or PNG, the numbers, the
  sources (LibreHardwareMonitor's mode, rights and PawnIO driver as data), the services with
  `enabled` and a state (`running`, `stopped`, `failed`, `not-installed`, `off`, `pending` until the
  app follows `config.toml`, `unknown`) to switch on and off and restart, test messages with their
  verdict, the theme per mode, the log, and the autopilot's rules
- How the panel is doing: the main loop measures its frame rate against the target, the time to draw
  a frame (median and slowest of the last 120), the data rate sent to the panel, and in video mode
  the blocks per second, the deepest queue the panel reported, the waits and the pictures thrown
  away, and counts USB errors, restarts of the panel and of ffmpeg. It is in `/api/app`
  (`panel.stats`), on *Live*, on the Control page and for plugins in `context.status()`;
  `context.sources()` lists the sensor sources and what they say about themselves. Measuring costs a
  few additions per frame and holds no lock. The panel chip in the header of *Design* and *Look*
  shows the frame rate against its target, the data rate and the drawing time (a number nothing
  counts, such as the data rate of a PNG file, is left out), marked when the panel cannot keep up;
  *Live* beside them opens the Live view

**New: services** (built in, each off until listed in `[services] enabled`)
- Services of the app's own: `notices`, `game`, `autopilot`, `devices`, `memory`, `cooling` and
  `downloads`. They are switched on with `[services] enabled` like a plugin's, work in the downloads
  without any package, and `libre-panel plugins` lists them as "built in"; their settings are in
  [the configuration guide](docs/CONFIGURATION.md#built-in-services)
- `notices` puts short messages on the panel: the volume while it turns (changing in place,
  "Muted"), a new track with its cover and the player's icon, sensors that cross a limit you
  set (`[[services.notices.warnings]]` with `warn` and `crit`, told once until the value has
  been below `warn`, and never twice within `quiet_s`), and "Panel restarted 21:05" after a
  restart that cleared a hung video decoder. The theme's toast style draws them (card or band)
  and `toast.off` leaves kinds out
- `game` is game mode: it finds the game you play, from a list of programs or Steam's library (read
  from `libraryfolders.vdf` and the `appmanifest` files on Windows and Linux; on Windows a game
  counts once its window has been in front), publishes `game.name` and `game.logo` (Steam's logo,
  cut out and not scaled, kept after the game until the next one), and switches to `[modes.game]`
  with the entrance; six seconds after the game's program ended it switches back. Without the
  entrance (`entrance = false`) a "Game mode" message says so
- `autopilot` picks the theme by rules in `config.toml` (`[[services.autopilot.rules]]`, each
  naming a theme, the first that holds wins): music playing, the microphone in use (Windows),
  the mean CPU or GPU load over a time, a program in front, the time of day. A new choice must
  hold for 8 s and a theme stays for 20 s, the load does not count for the first four minutes
  after the system started, and it rests in game mode
- `devices` (Windows): messages when a removable or USB/SD/MMC drive, a USB device (a phone
  with its maker's name) or a game controller comes or goes, a controller's battery runs low,
  a drive is nearly full (`min_free_gb`, `min_free_percent`) and an SSD runs hot
  (`ssd_warn_c`). Only changes are told, never what is plugged in at the start; a stick is one
  message, not two; four changes at once (waking from sleep) are taken in silently; Windows'
  own notices bring the look at once, with a slow poll for what was missed. Nothing is opened
  for data. The messages have drawn icons (`usb`, `stick`, `phone`, `ssd`, with the existing
  `gamepad`) that also go in any icon widget
- `memory` keeps what happens at the desk in a SQLite file on this PC: a row a minute (means
  and highest values of load, temperature, power, clocks, memory and network; how long music
  played, never what), a row per game (frame rates, how long at which rate, what held it back,
  the highest temperatures), the cooling state whenever it moves (temperatures, power, each
  fan's speed and duty, what each FanControl curve reads) and every change to FanControl's
  curves (read only). One sample a second, one writer thread with a bounded queue, WAL mode,
  730 and 180 days kept (`keep_days`, `cooling_days`). No title, window title or file name is
  read, and nothing leaves the PC. While a game is played it publishes the totals once a
  second as `session.*`: `session.time` (seconds played), `session.fps` (the mean frame rate so
  far), `session.level.<n>` (the share of the time at each frame-rate range in percent, as
  `report.level.<n>`) and the highest value since the game began (`session.cpu_temp_max`,
  `gpu_temp_max`, `gpu_hotspot_max`, `cpu_power_max`, `gpu_power_max`, `vram_used_max`), gone
  with the game
- A round report when a game that was played for 3 minutes with frame rates ends: the round
  against the last 10 of the same program, published as the state `report` and as `report.*`
  readings (with `report.count`, `report.frametime_delta`, `report.limit.gpu`, `.cpu` and
  `.cap` as shares in percent, `report.<peak>_mean` and `_delta` for each highest value, and
  `report.level.<n>.mean`), and shown with the theme `report_theme` for 30 s (priority 10,
  normal mode only); a new game ends it at once. The data is described in the plugin guide for
  screens that draw it otherwise
- `round-report`, the plain built-in theme (1920×480) the round report shows, in the look of
  SPUR II's report screen: the average frame rate large and its distance from the earlier
  rounds, the 1% low and the frame time, who held the rate down as a bar in the colours of the
  GPU, the CPU and the frame limit, the share of the round at each frame rate with a white tick
  on each bar where the earlier rounds' average stands, the game's logo at the top right beside
  its name (the name has the whole width where there is none), and the highest temperatures
  and power against their average. The first round of a game shows no comparison and says so
- `cooling` checks FanControl's fan curves against what the PC really did (the memory's cooling
  state, weighed by time) and says what to change: a curve that reads the sensor of another
  device (found by calculating back, from the duty a fan had, the temperature its curve must
  have seen), a mix that does not pass on the higher part, a temperature over your limit in a
  game, a fan that oscillates or runs higher than it needs on the desktop, cooling that slowly
  gets worse. Each recommendation names the data it rests on, and since FanControl was last
  changed it says what that change did. Read only: FanControl's file and the memory's database
  are opened for reading, nothing is changed or sent; devices come from the readings, limits
  from `limits`. It answers an editor page or a script with the analysis as data and a
  plain-text report in English and German
- `downloads` notes what is downloading: Steam's games and updates (read from its
  `content_log.txt` and `appmanifest` files, with the network's download as the speed) and the
  part files of browsers (`.crdownload`, `.part`, `.partial`) in the Downloads folder
  (Windows: the shell's folder; Linux: `xdg-user-dir DOWNLOAD`). It publishes the download
  that matters most as the state `downloads`: progress, speed, the last three minutes of speed
  and the time left, for a screen that draws a download card. Nothing below `min_mb` (50)
  counts, the browser's history is never read, and nothing is written

**New: sensors**
- `load`, a sensor source for the CPU and GPU load ten times a second: bars and graphs follow
  the load as it happens instead of once a second. The GPU load (Windows, any vendor, no
  administrator rights) is the one Task Manager shows. It runs only while a theme shows
  `cpu.load` or `gpu.load`; in an existing `config.toml` put it before the other sources in
  `[sensors] providers`
- `audio`, a sensor source for the volume: `audio.volume` (0 while muted) and `audio.muted` of
  the default output, read on every frame so a volume bar follows the volume keys at once.
  Windows (no extra program; it follows a changed default device) and Linux (`wpctl` or
  `pactl`). It runs only while a theme or a service shows `audio.*`, and is in the default
  `[sensors] providers` on both; in a list of your own add `"audio"`
- LibreHardwareMonitor inside Libre Panel (Windows): the downloads carry its library and read it
  themselves, so the GPU, temperatures, power and fans need no LibreHardwareMonitor that runs as
  administrator with its web server turned on. `mode` under `[sensors.librehardwaremonitor]` is
  `"auto"` (the default: inside Libre Panel where that is possible, else its web server),
  `"inprocess"` or `"webserver"`. With pip: `pip install "libre-panel[lhm]"`,
  `packaging/windows/fetch_lhm.py` and `dll_dir`. Only the hardware a theme on the panel shows is
  read, once a second. The libraries (LibreHardwareMonitor, MPL-2.0, and its companions under
  Apache-2.0 and MIT; see [NOTICE.md](NOTICE.md)) are fetched from NuGet when the Windows downloads
  are built, each checked against a fixed SHA-256; they are not in the repository. A download that
  cannot use its library (damaged, or deleted by a virus scanner) reports that as the source's
  `error` and does not ask a web server that is not there. More: [Windows
  sensors](docs/CONFIGURATION.md#windows-sensors-gpu-power-fans)
- The CPU's temperature, power and clock, the RAM temperature and the mainboard's fans need
  administrator rights and LibreHardwareMonitor's driver PawnIO ([pawnio.eu](https://pawnio.eu)).
  The sensor service (below) has both, so that Libre Panel itself needs neither; read inside
  Libre Panel they need it to run as administrator (`libre-panel autostart enable --elevated`).
  Without them those readings show "--", never 0, and a LibreHardwareMonitor web server that does
  have them fills in. `libre-panel sensors` and `libre-panel doctor` say which of the two is
  missing, and so does one plain sentence in the user's language when a Windows PC gives no
  temperatures (no administrator rights, no PawnIO driver, the reader did not start, its web
  server not running): a line under the status line of the tray menu and in the tooltip of the
  icon, `hint` in the panel's state (`/api/app`, `GET control/panel`) and on the
  LibreHardwareMonitor row of `GET control/sources`. It keeps quiet while LibreHardwareMonitor
  starts and about what nobody can change.
  `libre-panel sensors --check-lhm` tests that the library loads
- The sensor service (Windows): `LibrePanelSensors`, a small Windows service that runs
  LibreHardwareMonitor as LocalSystem on every kind of hardware it knows (the CPU's temperature,
  power and clock, the mainboard's temperatures and fans, the memory's and the drives'
  temperatures, fan controllers, pumps, power supplies) and hands the readings to Libre Panel over
  a local pipe, so Libre Panel itself needs no administrator rights for them. It only reads: two
  requests (hello, read) as length-prefixed JSON with bounded sizes and a rate limit; people
  signed in at the PC may ask for readings, nobody can change anything through it, network logons
  and other computers are refused, and Libre Panel talks only to the service's own process. It
  reads only the hardware the themes show, and nothing while nobody asks. `mode = "service"`
  under `[sensors.librehardwaremonitor]` asks it alone; `"auto"` prefers it wherever it is
  installed, reads the other ways while it does not answer and lets a service installed later
  take over. What it may do and why it is safe: [the sensor
  service](docs/BACKGROUND.md#the-sensor-service)
- The Windows setup installs for all users and sets up what the sensors need, in three checked
  boxes that say what each is (English and German): the sensor service, the PawnIO driver (from
  the download) and *Performance Log Users*, which game frame rates need. *Install for me only*
  installs without administrator rights as before, and an update keeps the way it was installed.
  The uninstaller stops and removes the service and takes back the group membership it gave
- `libre-panel system-setup --service --pawnio --perf-log-users` (as administrator; `--remove`
  takes it back, `--status` needs no rights): the one way the setup and the portable zip set these
  up, which may run again without harm
- *Unlock all sensors*: where a Windows download runs without the sensor service (the portable
  zip, an install for one user), a card on Start and *Settings → Sensors* run `system-setup` after
  one question of Windows (UAC). *Settings → Sensors* shows the service, the PawnIO driver and
  whether game frame rates may be recorded, and sets the service up again or removes it.
  `libre-panel sensors --check-service` asks it for readings
- Drive temperatures (`temp.nvme.*` and the like) through the sensor service: the Windows
  downloads carry LibreHardwareMonitor's DiskInfoToolkit 1.1.2 (MPL-2.0) and BlackSharp.Core 1.0.7
- The names of the processor, the graphics card and the memory (`cpu.name`, `gpu.name`, `mem.name`)
  are found by Libre Panel itself, on Windows and Linux, once at the start, with no administrator
  rights and no other program: from the registry and the firmware's SMBIOS table on Windows, from
  `/proc`, `/sys` and the system's `pci.ids` on Linux (and the SMBIOS files, where the user may read
  them). They are shortened for a panel, "Ryzen 5 7600", "Core i7-12700K", "GeForce RTX 4070",
  "Kingston DDR5-5600 32 GB"; where the memory modules cannot be read `mem.name` is the size, "32
  GB". The new `hardware` source is always on, adds nothing to a frame's time and wins over
  LibreHardwareMonitor's names, which are shortened the same way and fill in where it finds none
- `[hardware]` in `config.toml`: `cpu_name`, `gpu_name` and `memory_name`. Text there is the
  name, as written (one line, 100 characters at most); empty means the name found. A changed
  `config.toml` is taken while the panel runs. The names never go to the log or into the
  report of `libre-panel doctor`
- `media.app_icon`, the icon of the program that plays, next to `media.cover` (an image widget:
  `"src": "@media.app_icon"`), and the small badge on the cover of the "Now playing" message.
  Windows: the logo of a Store app, else the icon of the running program; Linux: the PNG of
  the player's desktop entry. Found once per program and nothing is sent anywhere
- `media.remaining`, the time left of a track in seconds (its length less where it is; none
  where a player does not tell the one or the other)
- What is playing (`media.*`) follows Windows' own reports of a new track, a pause or another
  player, so a new track shows at once; the cover is read when the track changes, a look every five
  seconds is the safety net, and the position moves on by itself. Linux asks once a second
- `mem.free` (psutil, what programs can still take; used and free add up to the total),
  `mem.temp` (LibreHardwareMonitor: the hottest memory module's own thermal sensor, with the
  administrator rights and PawnIO the CPU's temperature needs) and `mem.speed` (what the memory
  runs at in MT/s, from the firmware's SMBIOS table, the same one that names the memory; Linux
  where it may be read). What a PC cannot give shows as `--` with its name; the RAM's
  instrument card has these rows
- `gpu.hotspot` (LibreHardwareMonitor: the "GPU Hot Spot" sensor of the card `gpu.temp` is read
  from)
- `game.frametime_peak`, the longest frame time since the last reading (so a stutter between two
  readings shows, and a graph of it never stops while a game runs), and `game.limiter`, `cpu`, `gpu`
  or `cap`: who holds the frame rate down, judged from how long the CPU and the GPU wait for each
  other (PresentMon 2). With the `game` service on, the `game.*` readings count the frames of the
  game it found, not of the program with the most frames
- `weather.rain.minutes` and `weather.rain.text`: the rain of the next two hours from
  Open-Meteo's 15-minute precipitation. The minutes count to the rain starting or stopping;
  the text says so ("Rain in 20 min", "Rain stops in 40 min", "No rain for the next 2 h"), in
  German too. The request still carries only your coordinates; the answer is a little larger
- **Linux reads its sensors without root.** The new `linux` source (in the default list, ahead of
  psutil, which leaves the kernel's chips to it) reads the hwmon chips from sysfs: the processor
  (`cpu.temp` from k10temp, coretemp or zenpower, Tdie before Tctl), the mainboard's Super I/O chip
  with its fans named as Windows names them (`fan.lpc.fan_1` ..., so Lumen and the SPUR II screens
  show them), NVMe and SATA drives, and the memory modules (`mem.temp`, DDR5's `spd5118`, DDR4's
  `jc42`). The keys of temperatures and fans stay those psutil gave. Files are found once and a
  rescan every ten seconds finds chips that appear; drives are read by a thread of their own,
  SATA drives (`drivetemp`) only while a theme shows them, as a drive in standby can wake when it
  is asked. `[sensors.linux]` has `gpu` (pick the card by a part of its name) and `nvidia = "off"`
- `cpu.power` on Linux, from the energy counters of RAPL (Intel and AMD) where the user may read
  them, else from zenpower, fam15h_power or amd_energy. Since 2020 the kernel keeps the counters
  from everyone but root (the PLATYPUS side channel); opening them is off unless you say so
- The graphics card on Linux, all of `gpu.*`: **AMD** through amdgpu (edge, junction as
  `gpu.hotspot` and memory temperature, fan in RPM and per cent, power, both clocks, video memory,
  the load), **Intel** (i915, xe) through hwmon and sysfs (power from the energy counter, the
  temperatures a driver gives, the clock), **NVIDIA** through NVML, loaded from the driver's
  `libnvidia-ml.so.1` with ctypes (no extra package): temperature, fan, power, load, video memory,
  clocks. New keys: `gpu.fan.percent` (where a driver tells only per cent, NVIDIA's before 555;
  `gpu.fan` stays RPM and is missing then, not made up), `gpu.mem.total`, `gpu.mem.freq`,
  `gpu.mem.temp`. A card that is powered down to save energy, as a laptop's second one, is never
  asked; `gpu.load` is measured ten times a second by `load` here too (AMD, NVIDIA)
- **`libre-panel system-setup --linux`**: one command, one password, for what needs root. It is
  the command of Windows' sensor service too, with the flags of its own system (`--linux` is taken
  for granted on Linux unless a flag of Windows' is given; `--remove` and `--status` mean the same
  on both). It lets your user use the TURZX USB panels (the udev rule, applied to a panel that is
  plugged in, so nobody replugs), loads the Super I/O driver of the mainboard (`nct6775`, `it87`
  or `nct6683`, tried in the order that fits the maker, only on a desktop board, the first that
  finds its chip stays) and `drivetemp` for SATA drives, and keeps them in
  `/etc/modules-load.d/libre-panel.conf`. A driver is never given a parameter or forced (a
  firmware that keeps its chip, "resource busy", is said, not overridden); what `sensors-detect`
  recorded counts more than the guess. The processor's energy counters open only with `--rapl`.
  `--remove` takes it all out, `--dry-run` says what it would do, `--json` reports every step; a
  second run changes nothing. It asks with `pkexec` (polkit's own password window) or, in a
  terminal, `sudo`; the root side works out its plan itself and takes nothing but its switches
- The app offers it where it offers *Unlock all sensors* on Windows, through the same route
  (`/api/unlock`): *Start* shows a card while the panel may not be used, there are no fan
  readings or the processor's power is not readable (with *Not now*), and *Settings → Sensors*
  has *Set up sensors and panel access*, always (also *Remove again* and Ctrl+K). Both say
  exactly what the button does, in your language, and give the command for a terminal where
  there is no password window. The AppImage asks for rights as the AppImage file itself (its
  mount is closed to root), with the environment of its bundle left out

**New: themes and widgets**
- `needs` can compare a reading with a number (`media.playing=1`, `cpu.temp>80`) or a text
  (`key=text` holds while a reading is that: `game.limiter=gpu`; numbers count by value, texts
  in any case), ask for a picture a source or service has (`@media.app_icon`, `@game.logo`;
  `@name`), each with `!` in front for the opposite; a state that a service has taken back
  counts as no value. A game's name can stand where its logo is missing (the game service's,
  else the program PresentMon counts), and a word in the colour of whoever holds the frame rate
  down shows only while it does
- The colour `@cover` is the colour the cover on show carries (bars, rings and icons follow the
  track), and `play`, `pause`, `up` and `down` are icons
- Graphs: `fill_alpha`, how opaque the fill is, 0 to 1 (0: the default, a flat fill a quarter, a
  fade 0.45 at its top); `direction` `down`: the curve hangs from the top edge and is filled from
  there, as SPUR II draws the download below the upload; `inset` off: the range spans the whole box,
  as in SPUR II's charts (the line keeps a line's width inside by default); `end_dot`, a radius in
  px: the line ends in a dot of its colour at the newest value, with the graph's glow around it,
  drawn in quarter pixels so that it glides with the value, in video mode too
- A `bar` takes a `mark`, a second reading drawn as a thin line across it at its value on the
  bar's scale (`mark_color`, `mark_width`), in any finish, and an `image` takes `align`
  (`left`, `center`, `right`) for a picture that does not fill its box
- Format specs `{value:gb}`, a size in GB whatever unit the reading comes in;
  `{value:rate}` with `{value:rate_unit}`, a data rate as SPUR II sets it, its number and its
  unit apart (519 and KB/s, from 1 MB/s 1.2 and MB/s); `{value:upper}`, a text in capitals
  (`{label:upper}` a reading's name) for headings that follow the language of the panel. A
  `clock` sets its weekday and month in capitals with `"uppercase": true` (MONTAG · 28.
  SEPTEMBER)
- Liberation Sans ships with Libre Panel (`builtin:LiberationSans-Regular`, `-Bold`): its
  letters are as wide as Arial's, so a layout made for Arial stays in place
- The RAM modules (ring, big number, history) show the memory's name (`mem.name`) under their title
  as the CPU and GPU modules show theirs, and the System module lists it as a fourth row where there
  is room

**New: the SPUR II collection** (`"collection": "SPUR II"`, the screens of the private project
Libre Panel grew out of, for the 1920 × 480 bars, rebuilt as plain themes)
- *SPUR Classic* (`spur-classic`): SPUR II's classic screen, rebuilt from its geometry with the look
  and the finishes: two load charts with raised curves in flat frames, the CPU and GPU sections, RAM
  (its heading names the memory, its temperature is `mem.temp`) and network in staggered boxes,
  values as light in their milled grooves with SPUR II's glow, glowing headings, the faceted
  wordmark, the turning fans and the clock above, the grid and the depth dust
- *SPUR Horizon* (`spur-horizon`): the CPU's load growing up from a midline and the GPU's down
  from it across the whole panel, 70 seconds as a landscape with a mark every ten seconds and
  a dot at each curve's end, tiles below for CPU, GPU, RAM, network and what plays, the fans
  and the clock above
- *SPUR Header* (`spur-header`): SPUR II's game screen with the classic frame: the frame rate large
  and the 1% low in the header beside the game's logo (its name where it has none), the CPU's and
  GPU's load as charts, RAM and the GPU's temperatures (the memory's from `mem.temp`), and below,
  where the network was, a chart of the longest frame of each moment, who holds the frame rate down
  (in the colour of its chart) and the volume
- *SPUR Focus* (`spur-focus`): SPUR II's Focus screen for a game, the frame rate in 150 px with
  the game's logo or name over it and its course over 35 seconds beside it, the CPU's and
  GPU's load as small charts, and FRAMES (1% low, frame time, who holds the rate down), CPU,
  GPU and system below
- *SPUR Session* (`spur-session`): SPUR II's Session screen for a game: the time played, the
  average frame rate large with the frame rate now, the 1% low, who holds it down and the last
  ten minutes of the rate, the share of the time at each frame rate, and the highest
  temperatures, power and video memory since the game began beside what they are now (the
  `memory` service's `session.*`)
- *SPUR Cockpit* (`spur-cockpit`): SPUR II's Cockpit screen on its 40 px grid: the CPU's and
  GPU's load as two equal charts, the RAM as a ring beside them, and CPU, GPU, memory and
  network in four columns of equal width
- *SPUR Studio* (`spur-studio`): SPUR II's Studio screen made of the cards on a grid of 40 × 48
  cells that puts them within a few pixels of where SPUR II has them: the card slot in the
  middle (what plays, else the weather, with the line of light between them and a new track
  sliding in in 0.55 s), three ring instruments for the CPU, GPU and RAM (the RAM's FREE, TEMP
  and FREQ as SPUR II shows them, in place of USED, TOTAL and SWAP), the network card (a
  download while one runs) and, as plain widgets, the large clock with the seconds, the date
  and the volume. Its playback card counts down the time left

**New: plugins**
- Services do not poll: `host.on("sample", callback)` hands them every new snapshot of the readings
  (a slow callback gets the newest one, never a backlog), and what a service publishes reaches the
  next frame instead of the next reading, so `set_mode` or `show_theme` right after a `publish` is
  drawn with the value in. `host.on("fresh", callback)` (`snapshot=`, `keys=`) tells a service on
  the frame a reading of a source that follows on every frame (the volume, the fast load, what
  services publish) changed between two samples, and only then; it comes in the thread of `sample`,
  one callback after the other, and a slow one gets the newest with all the keys that changed
  meanwhile
- `host.publish_state(key, value)` keeps a dict or list as JSON with the time it was published,
  never smoothed and never in a graph's history; `snapshot.state(key)` and `context.state(key)`
  read it back. `host.want("media.*")` tells the sources that work on demand (what is playing,
  the volume, the load) that a service reads them
- `host.notify` says whether the message will show: it returns a `Delivery`, true when it will,
  else with the `reason`: the theme switches the kind off (`"theme"`), the screen shows it
  itself (`"screen"`) or no panel is connected (`"no-panel"`). A page sends one with
  `context.notify(...)`
- A toast style says how long a toast stays (`lifetime`) and how long one without its own time holds
  (`hold_s`), and keeps what it knows across theme switches; a transition can hide toasts while it
  plays (`toasts = "hide"`) and decide case by case what a switch does (`on_switch`), and a mode
  change that brings a transition of its own keeps it while a transition that follows switches
  plays; `context.image(key)` gives a published picture. A message on show when a screen comes that
  shows its kind itself goes on unseen and comes back with the next screen; without a queue
  (`"queue": false`), messages that come at the same moment are decided one after the other (a
  warning that comes with a volume change takes over from it at once)
- Editor pages: `context.background` says whether the background app serves the editor (the panel
  and the services run only there), `context.shown_frame()` gives the picture on the panel now,
  messages and transitions included, `context.log_tail(lines)` the end of the log the tray app
  writes, and `context.elevated` whether Libre Panel runs with the highest rights (a page then
  leaves what names a program, a library or a file to `config.toml`, as the app's Settings do). A
  page may answer with a file (`(status, bytes, type)`), and pages that write `config.toml` ask the
  app to follow it with `context.follow_config()`. Pages get the app's colours (its teal and amber,
  its grey) and its typeface, Barlow, so that a page inside the app looks like part of it;
  `/static/editor.css` keeps its names. `GET /api/sensors` also lists the pictures a source has
  (`media.cover`, `media.app_icon`, `game.logo`), marked `"picture": true`, for an image's `"@key"`
- `set_config_value` writes lists too (`"services.enabled"`) and replaces a list that runs over
  several lines, the new `set_config_tables` writes an array of tables such as
  `[[services.autopilot.rules]]`, and `edit_config` makes several such changes in one write.
  They keep the rest of `config.toml`, compare the new file with the old one, and refuse what
  they cannot edit safely
- A sensor source can win over sources before it for some keys (`overrides`), and gets the
  loaded configuration in `follow_config(config)`, when it is made and whenever `config.toml`
  changes while the panel runs
- Light and finishes: light edges the panel light glints on
  (`libre_panel.render.look.LightEdge`), from a widget type's `edges()` or a screen's
  `context.light_edge()`; finishes of their own with `libre_panel.render.finish.register`;
  `libre_panel.render.facets.groove_bar` and `groove_ring`, the milled groove of a bar or a
  ring (a floor of exactly the colour and a bevel lit from the top left), cached
- `libre_panel.clock()`, the clock of `now` (`time.perf_counter`); `snapshot.now` is the wall time
  of the frame that is planned, not of the moment its drawing begins, so a seconds clock ticks on
  the frame that is due
- The main loop draws one frame at a time through `MainLoop.step(now)` (in `libre_panel.app`), with
  the sensor hub, display, host and clock given: tests and plugins' test benches drive the real loop
  frame by frame instead of copying it
- `libre_panel.services.fancontrol`: FanControl's curves, fans and the sensor each curve reads,
  from its configuration file (found from the environment or `fancontrol = "..."`; opened for
  reading only)

**New: tools**
- `tools/compare_reference.py` draws a theme with given readings and compares it with a picture
  of the same screen: the share of pixels that match, overall and per region, the pictures
  side by side, their difference and enlarged crops
- `tools/motion_bench.py` leaves the warm-up out (`--warmup`), times the frames a sweep of light
  runs over on their own line, and takes `--dust`, `--finish facet` and `--fans N`: it runs
  each theme with and without that look, side by side frame by frame, and prints what the look
  adds to a frame
- `tools/readme_media.py` makes the GIFs, videos and posters of the README, the website and
  forum posts from recipes, with demo values only, and `tools/check_pages.py` checks that the
  README, the website and the release notes show only what a recipe makes and puts the website
  together

**Fixes**
- The app's local server (127.0.0.1) answered every program of every user of the PC: a `GET` needed
  nothing, a `POST` only the header `X-Libre-Panel: 1`, which any program can send. Every request
  now needs a secret token that is new at each start of Libre Panel, in the header
  `X-Libre-Panel: <token>`, `GET` as well; only the app's own files come without it. It reaches the
  app in the address (`#token=…`, which a browser never sends to a server), and the programs of your
  own account (`libre-panel quit`, the window the tray opens) in `private/editor.json` in the
  settings folder, which only you can open. A bookmark of an earlier run's address says the token is
  missing and what to do, and a request that a browser sends from a page of another origin is
  refused. See [Running in the background](docs/BACKGROUND.md#who-can-talk-to-the-app)
- On Windows another program could bind the app's local port too and receive its requests; the port
  is Libre Panel's alone now
- A Libre Panel that starts with the highest rights (`libre-panel autostart enable --elevated`) runs
  what `config.toml` and the plugins in its settings folder name: `autostart enable --elevated` and
  the log now warn when other users can change that folder (one made directly under `C:\` can be
  changed by every user of the PC; see [Windows: starting with the highest
  rights](docs/BACKGROUND.md#windows-starting-with-the-highest-rights))
- PresentMon is no longer looked for in the downloads folder: Libre Panel may run as administrator,
  and it started any `PresentMon*.exe` found there. Name it in `[sensors.presentmon] path`, put it
  in `PATH`, or install it
- PresentMon began when a theme first showed the frame rate, too late for a game with an anti-cheat,
  which refuses a new recording session while the game runs. It now runs for as long as the `game`
  service is on or a theme shows `game.*`, ends with Libre Panel however that ends (a Windows job),
  and waits longer and longer after a start that fails (no administrator rights)
- LibreHardwareMonitor's web server on this PC is asked directly, past a proxy set for the system,
  which could not reach it. When the web server stops answering, its readings go after 5 seconds
  (`stale_seconds` under `[sensors.librehardwaremonitor]`; 0 keeps them) and the panel shows "--",
  not the last numbers for ever; the log says so, and when they are back
- The music module's cover (an image widget with `"src": "@media.cover"`) was looked for only while
  something else asked for it, and an old cover could stay on show with the next track. An image
  widget that shows a picture of a source (in `src` or on a line of `slides`) counts as reading it
  now
- A NaN or infinite reading (a payload "nan" from MQTT or Home Assistant) showed as "nan" and left
  the bars and rings of its sensor empty for good, and an integer too large for a float (400 digits)
  made its widgets fail with a warning at every frame. Both are no reading now: the numbers show
  "--", what needs the reading hides, a graph has a gap (and a weather code that is no number is a
  cloud)
- A `config.toml` with a table or a list of the wrong kind (`device = "turing-5"` where `[device]`
  belongs, `weather = true`) or not saved as UTF-8 ended the app at its start. It is an error of the
  config now: the app starts, says what is wrong, and goes on once the file is fixed
- A theme file of the wrong shape (a string where an object belongs, a list as a widget's `type`,
  `1e999` for a whole number) stopped the panel loop instead of being refused like any other mistake
  in a theme; named by a service or a mode, it took the panel down every few seconds. Now the panel
  keeps its theme and the log says why
- Saving a theme that could not be written (text with half an emoji in it, a full disk) emptied its
  `theme.json` first. Such text is refused with a message now, and a theme is written whole or not
  at all
- Two changes to `config.toml` at the same moment (the tray's brightness while the app saves a
  setting) undid each other; a change now waits for the one under way
- Services follow `config.toml` whenever the panel loop starts and whenever the file loads, also
  when its theme does not: a service switched on, off or given other options while the panel was
  paused, in a `config.toml` that did not load at the start, or saved together with a theme name
  that does not exist, stayed as it was until the file changed once more
- A panel loop that could not start (an unknown `driver` in `config.toml`) left the sensor sources
  it had started running, and the app tries again every 30 seconds, so their threads piled up. The
  display is made first now
- While the panel was unplugged, every message of the services (a music message with its cover among
  them) piled up in the main loop for as long as that lasted. The newest 20 wait in the services'
  queue now and show when the panel is back
- A notice on the card (the default look) with a `payload["color"]` that is a palette name
  (`"@net"`, as the plugin docs have it) or a colour nobody can read was not shown at all. The card
  takes the accent as the band does now (`#rrggbb`, `@name` or a tuple, else the level's)
- A theme with a slideshow of a folder kept every picture it had shown, each as fitted to its box:
  2000 photos of a bar's size took 7 GB. A renderer keeps up to 64 MB of them now and reads the
  others again when their turn comes
- Video mode: a graph whose sensor goes missing or reads nothing moves on with a gap (it repeated
  the same window, and with `per_frame` ran on as a flat line), and where many widgets move, what
  lies under a graph is no longer laid anew on most frames
- Moving a theme to another panel: Pico no longer fails on the 5.2", 8" and 12.3" panels
  (`glow_radius` must be 0 to 64: the glow, shadow and backdrop blur stop at 64 now), a `moon`
  widget and a text's `max_width` scale with the panel, and a rule in the last column of a much
  smaller panel (the 0.96") stays on its frame
- Modules keep the alpha of a palette colour (a translucent `track`, `surface` or `line` was made
  opaque)
- A page that stayed open while Libre Panel was started again (a browser tab, which has the
  token of the run before) said "Libre Panel does not answer … this page tries again by
  itself", which it never would: the server refuses it, and the bar now shows the server's
  sentence about how to open Libre Panel again
- A character that the bundled fonts have no glyph for came out as a box: a track, an artist,
  a hardware or place name or a notice in Cyrillic, Greek, Chinese, Japanese, Korean, Arabic,
  Hebrew or Thai, an emoji, ♪ or →. It is drawn with a font of the computer now, found once, in
  the size and weight of the text and on its baseline (Segoe UI, Yu Gothic, Microsoft YaHei,
  Malgun Gothic and the like on Windows; what fontconfig names, else Noto Sans or DejaVu Sans,
  on Linux), in widgets, cards, lists, notices and the lettering of `facet` and `acrylic`. Text
  the bundled fonts have is drawn exactly as before. A computer with no font for a script still
  shows boxes for it: install one (docs/THEMES.md)
- The card toast cut a long notice (a track and its artist, a device's name) off at the edge of
  its card in the middle of a letter; it ends with "…" now, the width counted in the font that
  draws each letter
- Arabic is laid out whole: letter spacing, equal-width digits and the lettering of `facet` and
  `acrylic` took its letters one by one, which cannot join them. Where Pillow has libraqm (the
  Linux builds; the Windows ones have not) Arabic joins and Hebrew reads from the right; without
  it the letters of both stand in the order they are written, from the left
- A whole number of 400 digits in an MQTT message, or in a state or an attribute of Home Assistant,
  was too large for a float and raised in the thread that reads the broker; it is read as infinity
  now, as `1e400` always was, and shows as no value
- The pictures of the library's modules in the app made a renderer each and did not close it, as
  the other previews do (a renderer that is not closed keeps what its plugin parts hold until the
  collector runs, which the background app keeps from running for hours); every preview of the
  editor, plugin screens and widgets among them, closes its renderer and frees it at once
- Design, in German: *Wettersymbol* and *Monatskalender* broke in the middle of a syllable in the
  library's three columns of single widgets (*Wettersymb-ol*); the catalog marks where a compound
  may break now (*Wetter-symbol*, *Monats-kalender*), and a typed word finds the name as before

**Changes**
- Windows: game frame rates are on by default (`presentmon` among the default
  sources); PresentMon runs only while a theme shows `game.*`
- macOS is no longer supported: no download, no tests, no macOS code. Windows and Linux are. Nobody
  downloaded the macOS builds of any release. Gone with it: the disk image and the menu bar app, the
  LaunchAgent, and reading the volume and the player through `osascript`. A system that has no
  autostart (neither Windows nor an XDG desktop) now says "not supported on this system" to
  `libre-panel autostart` and in the app, instead of getting a file that does nothing
- The old browser editor is gone; the app replaces it. A bookmark of `/classic/` leads to the
  app, and plugins' editor pages open inside it (*Pages* in *Design*, or Ctrl+K)
- For editor pages and scripts that call the app's API: every request carries the token of the run
  in `X-Libre-Panel`, `GET` as well as `POST` (`kit.js`'s `api()` does, `withToken()` puts it in the
  address of a picture, `static/token.js` has `authHeaders()` for any other `fetch`); a script
  outside the browser reads it from `private/editor.json` in the settings folder (see Fixes).
  Without it the answer is 403, also for a route that does not exist. `make_server()` has a `token`
  of its own, and `libre_panel.editor.access` the rest. The routes of the server are one list,
  `editor.server.ROUTES`
- The theme a new install starts with depends on the panel: Lumen on the 1920×480 bars (the 8.8" and
  9.2"), Libre Default on every other panel. A `config.toml` without a `theme` means "the panel's
  own default", so `libre-panel config init` and the app's first write no longer put
  `theme = "libre-default"` into it; a comment says what the default is and how to name another. The
  panel is the one `device.model` names, else (with `model = "auto"`) the TURZX USB panel on the
  bus, found by its ids and not opened, else the panel that connects: the theme then changes once,
  with the usual transition. A theme in `config.toml`, also the `theme = "libre-default"` that 0.4
  wrote, and `libre-panel run --theme` always win. The app, the tray and the console API report the
  theme the panel really shows (`default_theme` in the panel's state, `default` in `GET /api/pages`)
- The video mode turns itself on: `video.mode` is `"auto"` by default, which streams to a 9.2"
  (the panel it is proven on) as soon as ffmpeg is found (the downloads carry one) and
  `local_clip` names the panel's standby clip. `libre-panel doctor` and the log say what is
  missing. A `mode = "off"` in `config.toml` stays off
- The downloads carry the programs their features run, in a `tools` folder next to the program,
  so that nothing else has to be installed: **ffmpeg** (a GPL build with libx264) in all of them
  for the video mode, **PresentMon** 2.6 for the game's frame rate and the installer of the
  **PawnIO** driver for the CPU's sensors in the Windows ones. The programs are not in the
  repository: `packaging/fetch_tools.py` fetches them when a download is built and checks every
  file against a SHA-256 written into the script (ffmpeg also for being the GPL build with libx264
  and without non-free parts, with its licence text). The release's CI then proves, for the
  program folder, the installed setup and the AppImage, that ffmpeg lists libx264 and encodes the
  video mode's stream, that PresentMon starts and that the app finds both before anything on the
  PATH. The licences: ffmpeg GPL-3.0-or-later, PresentMon MIT, PawnIO's driver GPL-2.0-or-later
  with an exception (its installer says it may be passed on unmodified, which the build checks).
  Their texts are in `tools/licenses`, the exact sources (FFmpeg's commit and the build recipe's)
  in `tools/SOURCES.txt` and [NOTICE.md](NOTICE.md). A pip install has none of it:
  install ffmpeg yourself (and PresentMon for game frame rates), or name them in `video.ffmpeg` and
  `[sensors.presentmon] path`, which win over the download's own too. More:
  [docs/CONFIGURATION.md](docs/CONFIGURATION.md#programs-that-come-with-the-downloads)
- PresentMon 2.6 names the per-frame waits `MsCPUBusy` and so on by default: both that and the
  names of 2.3 and `--v2_metrics` are read, so `game.limiter` works with the PresentMon that comes
  with the download and with the one you name yourself
- Windows: the GPU, temperatures, power and fans showed nothing unless `librehardwaremonitor` was
  listed in `config.toml`. New configurations now start with
  `providers = ["load", "audio", "librehardwaremonitor", "psutil"]` (on Linux
  `["load", "audio", "linux", "psutil"]`, elsewhere `["load", "psutil"]`), and an unset list means
  the same.
  Where Libre Panel cannot read LibreHardwareMonitor itself, that one must run as administrator with
  *Options → Remote Web Server → Run*; where it does not run, asking costs nothing (one second at
  most, waits that grow to half a minute, one line in the log) and those readings show "--". A
  `config.toml` from 0.4 or earlier that names psutil alone (what those versions wrote) gets the new
  list too, and so does, on Linux, the `["load", "audio", "psutil"]` that builds of 0.5 wrote
  before the Linux source; any other list is left as it is
- Windows downloads: with `mode = "auto"` the library inside Libre Panel is read first; a
  LibreHardwareMonitor web server that happens to run only fills in what Libre Panel cannot
  read (the CPU's readings without administrator rights). Drive temperatures (`temp.nvme.*`)
  come only from the web server for now: set `mode = "webserver"` to keep them, or to read only
  it
- `numpy` is a new requirement (light, dust and facets draw whole layers at a time). Python 3.11 to
  3.14 stay supported
- SPUR II's screens are kept as a legacy set: the SPUR II theme of 0.4 and the screens of the
  project it came from carry `"collection": "SPUR II"`, and galleries show Libre Panel's own themes
  first and SPUR II's apart (*SPUR II · Legacy*), for those who want to see where Libre Panel
  started. SPUR II and Studio read the sensors twice a second and their numbers glide; SPUR II's
  graphs span 36 seconds in video mode, moving 25 pixels a second. The graphs of Slate, Libre
  Default and Column have a window in seconds for video mode too; on the serial panels these themes
  run on, nothing changes
- The 9.2" (`turing-9.2-usb`) is listed as supported: stills and the video
  mode at 50 fps have run on it for hours on end

## 0.4.2 — 2026-10-10

A fix for video mode (the 9.2" panel with `[video] mode = "on"`): graphs no
longer stop moving a few seconds after the start.

**Fixes**
- Video mode: graphs that move a pixel per frame (`per_frame`) stood still
  about three seconds after the start and after every theme switch, while
  numbers and bars went on; scrolling graphs were drawn again on every frame
  instead of once per reading, which kept the helper thread busy

## 0.4.1 — 2026-10-10

Upside down for panels mounted the other way round, and fixes from the first
reports: Turing rev. C panels (2.1", 5", 8.8", and the 5" sold as UsbPCMonitor)
are no longer taken for another size and no longer freeze after a while; a
panel whose size is not the theme's no longer crashes Libre Panel; Bazzite
shows the real disk; and the fixes of a review of the whole code. The rev. C
fixes follow one user's logs: reports from more rev. C owners are welcome.

**New**
- Upside down, for a panel mounted the other way round: `rotate = 180` under
  `[device]`, or "Upside down" in the tray menu and in the editor's panel menu.
  Off unless chosen; works with every panel. Only the panel's picture is
  turned: themes, the editor's preview and the PNG file stay the right way up

**Fixes**
- A 5" rev. C panel sold as UsbPCMonitor 5" was taken for the round 2.1": its
  USB serial number (CT21INCH asleep, 20080411 awake) fits several sizes. The
  theme's size now decides, failing that the 5", and `libre-panel doctor`
  asks which panel it is (#3)
- `libre-panel doctor` uses the panel and port set in the configuration
- A panel set up as the wrong kind (a rev. A model on a rev. C panel's port)
  no longer stays dark without a word: Libre Panel names the models that fit
- Waking a rev. C panel works with `port` set to its sleeping port
- A rev. C panel froze after 20 seconds to a few minutes: it loses an update
  whose closing bytes fill a 250-byte block or are split over two, answers
  "needReSend:1" and shows nothing new (found by gwendal-h for
  turing-smart-screen-python). Such updates now go out in two parts, and should
  the panel still ask, the whole frame follows (#3)
- An awake rev. C panel is used as it is: Libre Panel no longer opens the port
  of its sleeping side, which may stay on the bus, or another device's (#3)
- A panel set in the configuration whose size is not the theme's crashed
  Libre Panel (`unknown color specifier: '@bg'`) with every built-in theme;
  the frame is scaled to fit again (#3)
- UsbPCMonitor 5" and 7" set up in the configuration are found, although they
  report the serial number of the Turing 3.5"
- A misspelt or misconfigured sensor source no longer stops the panel: it is
  left out, with an error in the log
- Two NVMe drives (or GPUs) whose sensors Linux names alike: the temperatures
  and fans of both show (`temp.nvme.composite`, `temp.nvme.composite_2`)
- Bazzite, Silverblue and other Fedora Atomic systems: the disk shows its real
  use, not that of the small read-only image at `/` (always 100 %)
- Modules take every colour a theme may hold: a short one (`#fff`), a name or
  `rgb()` in the palette stopped the whole theme
- Calendar: events that repeat since many years (daily since 2010) show again
- CI also runs on Python 3.14

**Changes**
- To find out why a panel stops changing: with `-v` the log shows what a
  rev. C panel answers (when that changes) and a summary of its updates every
  minute; `kill -USR1 <pid>` prints where every thread is (Linux, macOS)
- Serial panels are now called "USB serial panels (virtual COM port)" in the
  docs, `libre-panel models` and `doctor`: they plug in with USB(-C) like the
  others and only show up as a COM port

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
