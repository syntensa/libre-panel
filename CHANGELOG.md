# Changelog

## Unreleased

**New**
- Libre Panel in a window of its own instead of the browser (pywebview:
  WebView2 on Windows, WebKitGTK on Linux), one window at a time that keeps
  its size. A click on the tray icon and *Open Libre Panel* show it on
  *Start*, *Theme editor* and *Settings* on their views; `libre-panel start`,
  `libre-panel editor` and a second start open the same window or bring it to
  the front. Where no window can open (no WebView2 runtime, no GTK with
  WebKit, the Linux AppImage) the browser opens as before, and
  `libre-panel editor --browser` asks for it. With pip on Windows: `pip
  install "libre-panel[window]"`; on Linux see
  [Running in the background](docs/BACKGROUND.md#the-apps-window)
- *Design*, the new theme editor: the panel in the middle; modules, building
  blocks and widgets from a library with pictures in the theme's colours, to
  drag onto it; a toolbar over the selection for what it shows, how it is
  drawn (light, facet, acrylic lettering, flat: those that show on it), its
  colour and size; a short inspector with the rest under *Fine-tuning*, the
  six looks in the theme's settings; corners that resize, snapping guides,
  several widgets at once, layers with lock and eye, the theme's pictures and
  fonts. The panel's pages are tabs over it: the normal theme, game mode's,
  the round report's and those of plugins' modes, each with its frame rate
- *Start*, the first view of the app: the panel at a glance (what it shows,
  its picture, pause, brightness, upside down), the themes made for it to
  switch between with one click, and the way on to Design, Look, Live and
  Settings. *All* is the gallery of every installed theme, built in, from
  plugins and your own, each drawn by the panel's own renderer; Libre
  Panel's own themes and yours come first, and a set of themes that belongs
  together (a theme's `collection`, such as the SPUR II screens: *SPUR II ·
  Legacy*) waits closed at the end; its pencil opens a theme in Design. On a
  first start (no `theme` in `config.toml` yet) Start asks "How should it
  look?" with Libre Panel's own themes for the panel it found, or the one
  you name under *Another panel*; *Start empty* and *Open a theme file* are
  there too. What the panel could not read (the sentence the tray shows) is
  on Start with the way to its details
- *Look*, the theme's look in one place beside the picture of the panel: the
  six colour worlds and the palette, how rings, bars, graphs, lettering,
  icons and modules are drawn (light, facet, acrylic, flat) with their glow,
  the panel light (the sun, its strength, the sweeps and when they run), the
  grid, the depth dust with its colours and areas, and how notices look
  (card, band, a plugin's), which the preview can show as a test notice.
  Every change is one undo step and the preview follows; ridge frames are not
  offered, a theme that has some can make them flat under *Advanced*
- *Live*, what the panel shows and how it runs: its picture, the frame rate
  against the target, the data rate, the drawing time, the video queue and
  the faults, the sensor sources with their state and the hint about missing
  readings, the services with a switch each (and *Restart*), test notices of
  every kind with what became of them, and the end of the log; pause and
  brightness are in the header. It polls the console API while it is shown
- The hardware's names in the app: *Settings → Sensors → Hardware names*
  shows the processor, graphics card and memory names the sensors detect, and
  a text typed there takes their place on the panel (*Automatic* brings the
  detected one back). In *Design* the same field sits beside a widget that
  shows one of them
- *Settings* in the app change `config.toml` for you, so that nothing needs a text
  editor: *Panel* (the panel that is found; the video mode with what it still needs,
  its frame rate, and the name of the standby clip, which is written only when you
  type it; brightness; upside down; under *Advanced* the driver, the picture file,
  the serial port and ffmpeg), *Sensors* (the sources, on and off and in their
  order; how LibreHardwareMonitor is read, with what it says about administrator
  rights and PawnIO; the reading interval), *Services* (each one on or off with its
  state, *Restart*, and the options it declares), *Weather and place* (the weather,
  its units and interval, and a place found by its name: the name goes to
  Open-Meteo's search only when you press *Search*, and the place is kept to about a
  kilometre), *Start and system* (starting with the system; the highest rights
  only when you ask and only for an administrator; the language) and *About* (the
  version, the licence, the notices, how to report a panel, the folders with *Open
  folder*). The comments in `config.toml` stay. What the running app cannot take in
  by itself, the sources and the weather, says so and has a *Restart the panel*
  button; everything else is followed at once. `[weather]` has `place`, the name the
  app shows for the coordinates
- Everything in the app is a command: Ctrl+K finds it by a few letters
  (themes, modules, sensors, panels, the language, the panel's controls),
  with the keys it has
- `load`, a sensor source for the CPU and GPU load ten times a second: bars
  and graphs follow the load as it happens instead of once a second. The GPU
  load (Windows, any vendor, no administrator rights) is the one Task Manager
  shows. It runs only while a theme shows `cpu.load` or `gpu.load`; in an
  existing `config.toml` put it before the other sources in `[sensors] providers`
- `audio`, a sensor source for the volume: `audio.volume` (0 while muted) and
  `audio.muted` of the default output, read on every frame so a volume bar
  follows the volume keys at once. Windows (no extra program; it follows a
  changed default device) and Linux (`wpctl` or `pactl`). It runs only
  while a theme or a service shows `audio.*`, and is in the default
  `[sensors] providers` on both; in a list of your own add `"audio"`
- Messages in the look of SPUR II: the band (`"toast": {"style": "band"}`,
  or *Look → Notices* in the app). A glowing edge ignites from the
  centre and the band rolls down from it, with a title, a cover or icon and
  a progress bar that glides; turning the volume changes it in place
  without blinking, another message rolls down inside the open band, and
  one already leaving rolls back up. It fits every panel from 3.5" to the
  9.2" bar, either way round
- Two transitions after SPUR II, cheap for a panel's video encoder: `tube`
  (a line ignites at mid-height and opens onto the new screen, live) and
  `entrance` for a game or another mode (the screen closes to a line, the
  game's logo or name lights up while "Game mode" or "Game ended" types
  in, then the new screen opens)
- Weather symbols that move: the widget type `symbol` follows a weather code
  (`weather.code`, `weather.hour.3.code` ...) by day or night, and the sun's
  rays turn, rain, drizzle and snow fall, clouds drift and breathe, stars
  twinkle, fog sways and a bolt flashes now and then, as on SPUR II's weather
  card. Flat, or with `"finish": "lit"` from lit bodies; `moving` and `offset`
  keep a row of them out of step. It moves where the panel is driven faster
  than once a second, and stands in the editor's preview
- The cards of SPUR II's Studio screen, as modules that adapt to any panel:
  `playback` (the cover, the state, title and artist, the app with its icon,
  the progress), `forecast` (a moving symbol, the day's range, the details, the
  rain and the sun, the next hours), `instrument` (a ring of light with its
  number inside and the readings of the part beside or below it, or a fan
  with its speed), `download`
  (what loads, its progress, the rate and the time left, the speed of the last
  minutes: the new widget type `downloads`, which reads the state of the
  `downloads` service) and `netcard` (the network, with a download in its place
  while one runs). Cards are lit by themselves (a module's `finish` is `auto`
  now: its kind's own), their frames are flat thin lines, and where a module is
  too small for its card the older module of its kind takes over
- `cards`, a slot that shows one card of several: the first that has something
  to show (a track that plays or was paused a short while, the weather, a
  download that runs), or by a rule of its own on a line (`forecast if
  weather.temperature`). A change is SPUR II's: a line of light ignites at the
  top edge, the card on show rolls up into it and the new one rolls down out of
  it, on the frame's own time, so it takes as long at 50 frames a second as at
  5. The frame of the slot is a thin line in the colour of the card on show: the
  cover's, the weather's, the network's
- A `needs` can compare a reading with a number (`media.playing=1`,
  `cpu.temp>80`) or ask for a picture a source has (`@media.app_icon`), a state
  that a service has taken back counts as no value, the colour `@cover` is the
  colour the cover on show carries (bars, rings and icons follow the track), and
  `play`, `pause`, `up` and `down` are icons
- Plugins: a toast style says how long a toast stays (`lifetime`) and how
  long one without its own time holds (`hold_s`), and keeps what it knows
  across theme switches; a transition can hide toasts while it plays
  (`toasts = "hide"`) and decide case by case what a switch does
  (`on_switch`); `context.image(key)` gives a published picture
- Plugins: services no longer poll. `host.on("sample", callback)` hands them
  every new snapshot of the readings (a slow callback gets the newest one,
  never a backlog), and what a service publishes reaches the next frame
  instead of the next reading, so `set_mode` or `show_theme` right after a
  `publish` is drawn with the value in. `host.publish_state(key, value)`
  keeps a dict or list as JSON with the time it was published, never smoothed
  and never in a graph's history; `snapshot.state(key)` and
  `context.state(key)` read it back. `host.want("media.*")` tells the sources
  that work on demand (what is playing, the volume, the load) that a service
  reads them
- Plugins: `host.on("fresh", callback)` (`snapshot=`, `keys=`) tells a service on the
  frame a reading of a source that follows on every frame (the volume, the fast load, what
  services publish) changed between two samples, and only then. It comes in the thread of
  `sample`, one callback after the other, and a slow one gets the newest with all the keys
  that changed meanwhile
- Services of its own, built into the app: `notices`, `game` and `autopilot`.
  They are switched on with `[services] enabled` like a plugin's, all are off
  until you list them, they work in the downloads without any package, and
  `libre-panel plugins` lists them as "built in". Their settings are in the
  configuration guide
- `notices` puts short messages on the panel: the volume while it turns
  (changing in place, "Muted"), a new track with its cover and the player's
  icon, sensors that cross a limit you set (`[[services.notices.warnings]]`
  with `warn` and `crit`, told once until the value has been below `warn`,
  and never twice within `quiet_s`), and "Panel restarted 21:05" after a
  restart that cleared a hung video decoder. The theme's toast style draws
  them (card or band) and `toast.off` leaves kinds out
- `game` is game mode: it finds the game you play, from a list of programs or
  Steam's library (read from `libraryfolders.vdf` and the `appmanifest` files
  on Windows and Linux; on Windows a game counts once its window has
  been in front), publishes `game.name` and `game.logo` (Steam's logo, cut out
  and not scaled), and switches to `[modes.game]` with the entrance; six
  seconds after the game's program ended it switches back. Without the
  entrance a "Game mode" message says so
- `autopilot` picks the theme by rules in `config.toml`
  (`[[services.autopilot.rules]]`, each naming a theme, the first that holds
  wins): music playing, the microphone in use (Windows), the mean CPU or GPU
  load over a time, a program in front, the time of day. A new choice must
  hold for 8 s and a theme stays for 20 s, the load does not count for the
  first four minutes after the system started, and it rests in game mode
- LibreHardwareMonitor inside Libre Panel (Windows): the downloads carry its
  library and read it themselves, so the GPU, temperatures, power and fans no
  longer need a LibreHardwareMonitor that runs as administrator with its web
  server turned on. `mode` under `[sensors.librehardwaremonitor]` is `"auto"`
  (the default: inside Libre Panel where that is possible, else the web server
  as before), `"inprocess"` or `"webserver"`. With pip: `pip install
  "libre-panel[lhm]"`, `packaging/windows/fetch_lhm.py` and `dll_dir`. Only the
  hardware a theme on the panel shows is read, once a second
- The CPU's temperature, power and clock, the RAM temperature and the
  mainboard's fans need Libre Panel to run as administrator
  (`libre-panel autostart enable --elevated`) and LibreHardwareMonitor's
  driver PawnIO ([pawnio.eu](https://pawnio.eu)). Without them those readings
  show "--", never 0, and a LibreHardwareMonitor web server that does have them
  fills in. `libre-panel sensors` and `libre-panel doctor` say which of the two
  is missing; `libre-panel sensors --check-lhm` tests that the library loads
- The libraries (LibreHardwareMonitor, MPL-2.0, and its companions under
  Apache-2.0 and MIT; see NOTICE.md) are fetched from NuGet when the Windows
  downloads are built, each checked against a fixed SHA-256; they are not in the
  repository
- What moves on the panel goes on from theme to theme: a value gliding when
  the theme switches glides on in the next one instead of starting over
  (the main loop keeps it; the editor's previews have their own)
- Numbers glide too: a value with *smooth* on counts to a new reading with
  the bars and rings of its sensor instead of jumping once per reading
  (`"smooth": true` on a `metric`)
- Threshold colours fade in a fifth of a second instead of jumping, and a
  bar and its number change colour together
- Graphs in video mode move on every frame: they draw the gliding value 25
  times a second and move in half-pixel steps, and their newest point is the
  value the numbers show (they trailed a reading behind and moved a few
  pixels a second). `seconds` sets the time a graph spans in video mode,
  whatever the frame rate; a theme that comes on shows its graphs full
- For plugins: `libre_panel.clock()`, the clock of `now` (`time.perf_counter`)
- A theme coming on is built and drawn once in the background while the
  panel goes on showing the one before, so a switch no longer freezes a
  video for a third of a second. Screens from plugins can load what their
  first frames need meanwhile: `Screen.prepare()`
- `devices`, a built-in service for Windows (off until listed in
  `[services] enabled`): messages when a removable or USB/SD/MMC drive, a USB
  device (a phone with its maker's name) or a game controller comes or goes, a
  controller's battery runs low, a drive is nearly full (`min_free_gb`,
  `min_free_percent`) and an SSD runs hot (`ssd_warn_c`). Only changes are told,
  never what is plugged in at the start; a stick is one message, not two; four
  changes at once (waking from sleep) are taken in silently; Windows' own
  notices bring the look at once, with a slow poll for what was missed. Nothing
  is opened for data. The messages have drawn icons (`usb`, `stick`, `phone`,
  `ssd`, with the existing `gamepad`) that also go in any icon widget
- `media.app_icon`, the icon of the program that plays, next to `media.cover`
  (an image widget: `"src": "@media.app_icon"`), and the small badge on the
  cover of the "Now playing" message. Windows: the logo of a Store app, else
  the icon of the running program; Linux: the PNG of the player's desktop
  entry. Found once per program and nothing is sent anywhere
- `game.frametime_peak`, the longest frame time since the last reading (so a
  stutter between two readings shows, and a graph of it never stops while a
  game runs), and `game.limiter`, `cpu`, `gpu` or `cap`: who holds the frame
  rate down, judged from how long the CPU and the GPU wait for each other
  (PresentMon 2)
- The look of SPUR II for any theme: a widget's `finish` and the theme's
  `look`. `"finish": "lit"` draws bars and rings as tubes in their groove
  with round ends and SPUR II's glow, a graph's line as light in a halo and
  the weather symbols from lit bodies, in the widget's colours and at its
  size (bars, rings, graphs and icons; modules hand it to their parts).
  `look.grid` lays SPUR II's grid over the backdrop; `look.light` is one
  light for the panel: the sun, where it stands for the place in
  `[weather]`, glints on frames (outlines of rects become ridges), and a
  sweep of light runs across at the start, on the hour, on a new track and
  after a tube or entrance transition. The light layers are prepared before
  the first frame and kept in the cache folder. The app sets both (*Grid
  background* and *Panel light* under *Look*, *Finish* for a widget); the
  *Lit demo* theme (1920 × 480) shows them
- For plugins: light edges the panel light glints on
  (`libre_panel.render.look.LightEdge`), from a widget type's `edges()` or a
  screen's `context.light_edge()`; finishes of their own with
  `libre_panel.render.finish.register`
- `tools/motion_bench.py` leaves the warm-up out (`--warmup`) and times the
  frames a sweep of light runs over on their own line
- `weather.rain.minutes` and `weather.rain.text`: the rain of the next two
  hours from Open-Meteo's 15-minute precipitation. The minutes count to the
  rain starting or stopping; the text says so ("Rain in 20 min", "Rain stops
  in 40 min", "No rain for the next 2 h"), in German too. The request still
  carries only your coordinates; the answer is a little larger
- Depth dust, after SPUR II: `"look": {"dust": true}` in a theme lets fine
  grains fall from the letters, rails, rings and bars into the depth behind
  the panel, in the colour of what they come from, a few of them as soft
  discs. Nothing in the theme says where the sources are: the dust reads the
  frame the panel showed. `count`, `rate`, `depth`, `strength`, `colors` and
  `areas` (rectangles with or without dust) set it, *Look → Depth dust* in the
  app has them. It is drawn on a video panel and in the editor's preview,
  never on a panel that is sent changed regions, and costs nothing while the
  theme has none; at 1920 × 480 it adds a millisecond or two to a frame
- A `fan` widget: a rotor of nine lit blades that turns with a fan's RPM (calm
  and with inertia, standing at 0 RPM, dimmed without a reading) in a ring that
  shows its share of `max_rpm`, or in a square frame; *Fan with speed* in the
  editor's presets adds one with its name and value
- `"finish": "facet"` on `text`, `metric`, `clock`, `weather`, `countdown`,
  `bar`, `gauge` and `graph`: a flat top in the exact colour and a sharp
  bevel lit from the top left, like SPUR II's lettering. Lettering is bright
  metal that mirrors the sky above it (the accent) and the ground below (the
  background), with a light touch up to 28 px and the full look from 52 px;
  `"finish": "acrylic"` is the same lettering lit from within, as the "II" of
  the wordmark. Bars and rings are raised in a milled track, a graph's line
  becomes a ridge on the plateau of its fill with a halo of its colour (a
  slimmer ridge on a bar panel). Text keeps the pixels of the flat text;
  shapes are built once per size and colour, so it costs no more than the
  flat look
- `fill_alpha` on a `graph`: how opaque its fill is, 0 to 1 (0: as before, a
  flat fill a quarter, a fade 0.45 at its top)
- `libre_panel.render.facets.groove_bar` and `groove_ring`: the milled groove
  of a bar or a ring (a floor of exactly the colour and a bevel lit from the
  top left), cached, for finishes of their own
- Plugins: an editor page asks `context.background` whether the background app
  serves its editor (the panel and the services run only there),
  `context.shown_frame()` for the picture on the panel now, messages and
  transitions included, and `context.log_tail(lines)` for the end of the log
  the tray app writes
- Plugins: `host.notify` says whether the message will show. It returns a
  `Delivery`, true when it will, else with the `reason`: the theme switches the
  kind off (`"theme"`), the screen shows it itself (`"screen"`) or no panel is
  connected (`"no-panel"`). A page sends one with `context.notify(...)`
- How the panel is doing: the main loop measures its frame rate against the
  target, the time to draw a frame (median and slowest of the last 120), the
  data rate sent to the panel, and in video mode the blocks per second, the
  deepest queue the panel reported, the waits and the pictures thrown away,
  and counts USB errors, restarts of the panel and of ffmpeg. They are in
  `/api/app` (`panel.stats`), on the app's *Live* view, on the new Control page
  and for plugins in `context.status()`;
  `context.sources()` lists the sensor sources and what they say about
  themselves. Measuring costs a few additions per frame and holds no lock
- Plugins: `set_config_value` writes lists too (`"services.enabled"`) and
  replaces a list that runs over several lines, the new `set_config_tables`
  writes an array of tables such as `[[services.autopilot.rules]]`, and
  `edit_config` makes several such changes in one write. They keep the rest of
  `config.toml`, compare the new file with the old one, and refuse what they
  cannot edit safely
- The app has two pages of its own, built in like the services (*Pages* in
  *Design*, or Ctrl+K), written with the plugin API. **Control** shows the live picture
  of the panel, the numbers above as tiles, each sensor source (LibreHardwareMonitor
  says how it is read, whether it has administrator rights, whether PawnIO is there)
  and each service with a *Restart*, sends a test message of each kind and says
  whether it shows or why not, sets the theme of the normal and the game mode in
  `config.toml`, and shows the end of the log. **Autopilot** edits the autopilot's
  rules and settings in `config.toml`, switches it on and off, and shows which
  rule holds and what it sees. Without the background app (`libre-panel editor`)
  they say so. An editor page may answer with a file (`(status, bytes, type)`)
- The two pages are a JSON API that a screen of any design can call
  (`/api/plugins/control/` and `/api/plugins/autopilot/`, listed under "The
  console API" in docs/ARCHITECTURE.md): the live picture as JPEG or PNG, the
  numbers, the sources (LibreHardwareMonitor's mode, rights and PawnIO driver as
  data), the services with `enabled` and a state (`running`, `stopped`, `failed`,
  `not-installed`, `off`, `pending` until the app follows `config.toml`,
  `unknown`) that can be switched on and off and restarted, test messages with
  their verdict, the theme per mode, the log, and the autopilot's rules. Plugin
  pages that write `config.toml` ask the app to follow it with
  `context.follow_config()`
- `memory`, a built-in service (off until listed in `[services] enabled`) that
  keeps what happens at the desk in a SQLite file on this PC: a row a minute
  (means and highest values of load, temperature, power, clocks, memory and
  network; how long music played, never what), a row per game (frame rates,
  how long at which rate, what held it back, the highest temperatures), the
  cooling state whenever it moves (temperatures, power, each fan's speed and
  duty, what each FanControl curve reads) and every change to FanControl's
  curves (read only). One sample a second, one writer thread with a bounded
  queue, WAL mode, 730 and 180 days kept (`keep_days`, `cooling_days`). No
  title, window title or file name is read, and nothing leaves the PC
- A round report when a game that was played for 3 minutes with frame rates
  ends: the round against the last 10 of the same program, published as the
  state `report` and as `report.*` readings, and shown with the theme
  `report_theme` for 30 s (priority 10, normal mode only); a new game ends it
  at once. `round-report` is a plain built-in theme (1920×480) for it; the
  data is described in the plugin guide for screens that draw it better
- The round report in the look of SPUR II: `round-report` is SPUR II's report
  screen as a theme, with the average frame rate large and its distance from
  the earlier rounds, the 1% low and the frame time, who held the rate down as
  a bar in the colours of the GPU, the CPU and the frame limit, the share of
  the round at each frame rate with a line under each bar for the earlier
  rounds, and the highest temperatures and power against their average. The
  first round of a game shows no comparison and says so. For it the report
  reads out more: `report.count`, `report.frametime_delta`,
  `report.limit.gpu`, `.cpu` and `.cap` (shares in percent),
  `report.<peak>_mean` and `_delta` for each highest value and
  `report.level.<n>.mean`; the state `report` and the readings that were
  there are as they were
- The theme *SPUR Studio* (`spur-studio`, 1920 × 480): SPUR II's Studio screen
  made of the cards: the card slot in the middle (what plays, else the weather,
  with the line of light between them), three ring instruments for the CPU, GPU
  and RAM, the network card (a download while one runs) and, as plain widgets,
  the large clock with the seconds, the date and the volume. It sits on a grid
  of 40 × 48 cells that puts the cards within a few pixels of where SPUR II has
  them
- `libre_panel.services.fancontrol`: FanControl's curves, fans and the sensor
  each curve reads, from its configuration file (found from the environment or
  `fancontrol = "..."`; opened for reading only)
- `cooling`, a built-in service that checks FanControl's fan curves against
  what the PC really did (the memory's cooling state, weighed by time) and
  says what to change: a curve that reads the sensor of another device (found
  by calculating back, from the duty a fan had, the temperature its curve must
  have seen), a mix that does not pass on the higher part, a temperature over
  your limit in a game, a fan that oscillates or runs higher than it needs on
  the desktop, cooling that slowly gets worse. Each recommendation names the
  data it rests on, and since FanControl was last changed it says what that
  change did. Read only: FanControl's file and the memory's database are
  opened for reading, nothing is changed or sent; devices come from the
  readings, limits from `limits`. It answers an editor page or a script with
  the analysis as data and a plain-text report in English and German
- `downloads`, a built-in service that notes what is downloading: Steam's
  games and updates (read from its `content_log.txt` and `appmanifest` files,
  with the network's download as the speed) and the part files of browsers
  (`.crdownload`, `.part`, `.partial`) in the Downloads folder (Windows: the
  shell's folder; Linux: `xdg-user-dir DOWNLOAD`). It publishes the download that
  matters most as the state `downloads`: progress, speed, the last three minutes
  of speed and the time left, for a screen that draws a download card. Nothing
  below `min_mb` (50) counts, the browser's history is never read, and nothing is
  written
- The look of SPUR II for any theme: a widget's `finish` and the theme's
  `look`. `"finish": "lit"` draws a bar's or a ring's value as SPUR II's
  light in its milled groove (a core running towards white in a soft halo: a
  pill with round ends on a bar with SPUR II's glow, kept inside a
  `glow_area` if it has one, a thin arc on a ring), a graph's line as light
  in a halo, a text's glow as light added over it as on SPUR II's headings
  (its `opacity` fades the letters, not the light), and the weather symbols
  from lit bodies, in the widget's colours and at its size (bars, rings,
  graphs, texts and icons; modules hand it to their parts).
  `look.grid` lays SPUR II's grid over the backdrop; `look.light` is one
  light for the panel: the sun, where it stands for the place in
  `[weather]`, glints on ridges, and a sweep of light runs across at the
  start, on the hour, on a new track and after a tube or entrance
  transition. The light layers are prepared before the first frame and kept
  in the cache folder. The app sets both (*Grid background* and *Panel
  light* under *Look*, *Finish* for a widget)
- The theme *SPUR Classic* (`spur-classic`, 1920 × 480): SPUR II's classic
  screen as it is now, rebuilt from its geometry with the look and the
  finishes: two load charts with raised curves in flat frames, the CPU and
  GPU sections, RAM and network in staggered boxes, values as light in their
  milled grooves with SPUR II's glow, glowing headings, the faceted wordmark,
  the turning fans and the clock above, the grid, one light and the depth dust
- The theme *SPUR Horizon* (`spur-horizon`, 1920 × 480): SPUR II's Horizon
  screen, the CPU's load growing up from a midline and the GPU's down from it
  across the whole panel, 70 seconds as a landscape with a mark every ten
  seconds, tiles below for CPU, GPU, RAM, network and what plays, the fans and
  the clock above
- The theme *SPUR Header* (`spur-header`, 1920 × 480): SPUR II's game screen
  with the classic frame: the frame rate large and the 1% low in the header
  beside the game's logo (its name where it has none), the CPU's and GPU's
  load as charts, RAM and the GPU's temperatures, and below, where the
  network was, a chart of the longest frame of each moment, who holds the
  frame rate down (in the colour of its chart) and the volume
- The theme *SPUR Focus* (`spur-focus`, 1920 × 480): SPUR II's Focus screen
  for a game, the frame rate in 150 px with the game's logo or name over it
  and its course over 35 seconds beside it, the CPU's and GPU's load as small
  charts, and FRAMES (1% low, frame time, who holds the rate down), CPU, GPU
  and system below
- The theme *SPUR Session* (`spur-session`, 1920 × 480): SPUR II's Session
  screen for a game, as far as the readings go: the frame rate large with its
  last ten minutes, a chart of the longest frame of each moment, and the
  CPU's and GPU's temperatures and power with their course over the same ten
  minutes. SPUR II's sums over a whole session (time played, average frame
  rate, share of time per frame rate, highest values) need readings Libre
  Panel does not have
- The theme *SPUR Cockpit* (`spur-cockpit`, 1920 × 480): SPUR II's Cockpit
  screen, laid out on its 40 px grid: the CPU's and GPU's load as two equal
  charts, the RAM as a ring beside them, and CPU, GPU, memory and network in
  four columns of equal width
- Ridges: a `rect` with a `ridge` name is drawn as SPUR II draws its frames,
  a ridge with two facets under the guide light, the lit one glowing a
  little; rects with the same name are one ridge, joined at mitres. Rects
  without a name stay flat
- `"finish": "facet"` on `text`, `metric`, `clock`, `weather`, `countdown`,
  `bar`, `gauge` and `graph`: a flat top in the exact colour and a sharp
  bevel lit from the top left, like SPUR II's lettering. Lettering is bright
  metal that mirrors the sky above it (the accent) and the ground below (the
  background), with a light touch up to 28 px and the full look from 52 px;
  `"finish": "acrylic"` is the same lettering lit from within, as the "II" of
  the wordmark. Bars and rings are raised in a milled track, a graph's line
  becomes a ridge on the plateau of its fill with a halo of its colour (a
  slimmer ridge on a bar panel; the halo ends where SPUR II's does, and a
  graph that hangs from its top has it mirrored). Text keeps the pixels of
  the flat text; shapes are built once per size and colour, so it costs no
  more than the flat look
- Graphs: `direction` `down`: the curve hangs from the top edge and is
  filled from there, as SPUR II draws the download below the upload; and
  `inset` off: the range spans the whole box, as in SPUR II's charts (the
  line keeps a line's width inside by default)
- `needs` can ask for more than a value: `key=text` holds while a reading is
  that (`game.limiter=gpu`; numbers count by value, texts in any case) and
  `@name` while a source or service has the picture (`@game.logo`), each with
  `!` in front for the opposite. A game's name can stand where its logo is
  missing (the game service's, else the program PresentMon counts), and a word
  in the colour of whoever holds the frame rate down shows only while it does
- Format specs `{value:gb}`, a size in GB whatever unit the reading comes
  in, and `{value:rate}` with `{value:rate_unit}`: a data rate as SPUR II
  sets it, its number and its unit apart (519 and KB/s, from 1 MB/s 1.2 and
  MB/s)
- Format spec `{value:upper}`: a text in capitals (`{label:upper}` a reading's
  name), for headings that follow the language of the panel, as SPUR II sets
  them; a `clock` sets its weekday and month in capitals with `"uppercase":
  true` (MONTAG · 28. SEPTEMBER)
- `tools/compare_reference.py` draws a theme with given readings and
  compares it with a picture of the same screen: the share of pixels that
  match, overall and per region, the pictures side by side, their
  difference and enlarged crops
- Liberation Sans ships with Libre Panel (`builtin:LiberationSans-Regular`,
  `-Bold`): its letters are as wide as Arial's, so a layout made for Arial
  stays in place
- The names of the processor, the graphics card and the memory (`cpu.name`,
  `gpu.name`, `mem.name`) are found by Libre Panel itself, on Windows and
  Linux, once at the start, with no administrator rights and no other
  program: from the registry and the firmware's SMBIOS table on Windows, from
  `/proc`, `/sys` and the system's `pci.ids` on Linux (and the SMBIOS files,
  where the user may read them). They are shortened for a panel, "Ryzen 5
  7600", "Core i7-12700K", "GeForce RTX 4070", "Kingston DDR5-5600 32 GB";
  where the memory modules cannot be read `mem.name` is the size, "32 GB". The
  new `hardware` source is always on, adds nothing to a frame's time and wins
  over LibreHardwareMonitor's names, which fill in where it finds none
- `[hardware]` in `config.toml`: `cpu_name`, `gpu_name` and `memory_name`. Text
  there is the name, as written (one line, 100 characters at most); empty means
  the name found. A changed `config.toml` is taken while the panel runs. The
  names never go to the log or into the report of `libre-panel doctor`
- The editor's server answers `GET /api/hardware` with the found and the
  written name of each part (`{"cpu": {"detected": ..., "custom": ""}, "gpu":
  ..., "memory": ...}`; `?refresh=1` asks the computer again) and
  `POST /api/hardware` sets any of `cpu`, `gpu` and `memory` (an empty text
  gives the found name back) through `config.toml`, comments kept
- Plugins: a sensor source can win over sources before it for some keys
  (`overrides`), and gets the loaded configuration in `follow_config(config)`,
  when it is made and whenever `config.toml` changes while the panel runs
- Lumen, Libre Panel's own signature screen for the 8.8" and 9.2" bars, in
  the app's colours and type on the dot grid of the editor's stage. On the
  left what plays (the weather with its moving symbols and the next hours when
  nothing does, a calendar sheet when there is neither), in the middle the
  time with its seconds, the load of the CPU and GPU over the last minute as
  light that scrolls, and the fans turning with their speed, on the right lit
  rings for the CPU, GPU and RAM named after your hardware, over the network
  (a download in its place while one runs). Frames are flat lines; it keeps
  clear of the 9.2"'s hidden strip and moves to the other panels
- The theme *SPUR Session* (`spur-session`, 1920 × 480): SPUR II's Session
  screen for a game: the time played, the average frame rate large with the
  frame rate now, the 1% low, who holds it down and the last ten minutes of
  the rate, the share of the time at each frame rate, and the highest
  temperatures, power and video memory since the game began beside what they
  are now (the `memory` service's `session.*`, below)
- `media.remaining`, the time left of a track in seconds (its length less
  where it is; none where a player does not tell the one or the other), and
  `time` for the playback card and the music module: `remaining` writes it at
  the right of the progress with a minus (`-2:24`), as SPUR II's Studio does.
  SPUR Studio counts down
- The RAM column of SPUR Studio shows FREE, TEMP and FREQ as SPUR II does, in
  place of USED, TOTAL and SWAP: `mem.free` (psutil, what programs can still
  take; used and free add up to the total), `mem.temp` (LibreHardwareMonitor:
  the hottest memory module's own thermal sensor, with the administrator
  rights and PawnIO the CPU's temperature needs) and `mem.speed` (what the
  memory runs at in MT/s, from the firmware's SMBIOS table, the same one that
  names the memory; Linux where it may be read). What a PC cannot give shows
  as `--` with its name. The RAM instrument card has these rows; SPUR Classic
  and SPUR Header read the memory temperature from `mem.temp`
- The round report shows what SPUR II's does: the game's logo at the top
  right beside its name (`game.logo`, which the `game` service now keeps
  after the game, until the next one; the name has the whole width where
  there is none) and, on each bar of the chart, a white tick where the
  earlier rounds' average stands, in place of the line under it. For that a
  `bar` takes a `mark`, a second reading drawn as a thin line across it at
  its value on the bar's scale (`mark_color`, `mark_width`), in any finish,
  and an `image` takes `align` (`left`, `center`, `right`) for a picture that
  does not fill its box. The demo has a made-up logo, drawn when asked
- The `memory` service adds up the game while it is played and publishes the
  totals once a second as `session.*`: `session.time` (seconds played),
  `session.fps` (the mean frame rate so far), `session.level.<n>` (the share
  of the time at each frame-rate range in percent, as `report.level.<n>`) and
  the highest value since the game began (`session.cpu_temp_max`,
  `gpu_temp_max`, `gpu_hotspot_max`, `cpu_power_max`, `gpu_power_max`,
  `vram_used_max`). They are gone with the game. SPUR Session shows them
  where SPUR II does, and the demo has neutral values for them
- A `graph` takes `end_dot`, a radius in px: the line ends in a dot of its
  colour at the newest value, with the graph's glow around it, drawn in
  quarter pixels so that it glides with the value, in video mode too. SPUR
  Horizon's curves end in a dot as SPUR II's do
- A card slot takes `slide` (seconds, 0: none): a new track on the playback
  card that stays on show moves in from the right while the old one moves out
  to the left, a gap between them that closes, as SPUR II's Studio moves on
  to a new track; the parts are made once and each frame moves and cuts them.
  SPUR Studio slides in 0.55 s

**Fixes**
- LibreHardwareMonitor: when its web server stopped answering, the panel kept
  showing the last numbers for ever. After 5 seconds (`stale_seconds` under
  `[sensors.librehardwaremonitor]`; 0 keeps them) the readings go and the
  panel shows "--"; the log says so, and when they are back
- A mode change that brought a transition of its own (a game that ends
  during its entrance) lost it while a transition that follows switches
  played
- A message on show was gone for good when a screen came that shows its
  kind itself; now it goes on unseen and comes back with the next screen
- A seconds clock ticks on the frame that is due: `snapshot.now` is the time
  a frame is planned for, not the moment its drawing begins
- Video mode: a graph whose sensor goes missing or reads nothing moves on
  with a gap, instead of standing still (or, with `per_frame`, running on as
  a flat line)
- PresentMon began when a theme first showed the frame rate, which is too late
  for a game with an anti-cheat: it refuses a new recording session while the
  game runs. It now runs for as long as the `game` service is on or a theme
  shows `game.*`, ends with Libre Panel however that ends (a Windows job), and
  when it cannot run (no administrator rights) it waits longer and longer
  instead of trying again at every reading
- Video mode: where many widgets move, what lies under a graph was laid anew
  on most frames (the kept layers went oldest first, not least used); the
  ones a frame uses are now kept longest
- The volume message came with the next reading, up to `refresh_ms` late. It now comes
  within a frame or two of the volume key, and a slow turn of the volume that moves less
  than a step between two frames still adds up to one
- An image widget that shows a picture of a source (`"src": "@media.app_icon"`, or such a
  line in `slides`) now counts as reading it. Before, the icon of the player was never
  looked for unless something else asked for it, and the cover of the music module was
  read only while something else asked for `media.*` too, such as the notices service
- The editor's sensor picker did not offer the volume, the rain text and the game's slowest
  frame and what limits it: a source that reads only while a theme shows a key has none of
  them in a running panel until then, and the demo had none. They are listed from a catalog
  now, and the demo values of the previews gained the volume (46 %), the game keys and the
  rain ("Rain in 35 min"). `GET /api/sensors` also lists the pictures a source has
  (`media.cover`, `media.app_icon`, `game.logo`), marked `"picture": true`, for an image's
  `"@key"`
- Windows, when LibreHardwareMonitor gives no temperatures (Libre Panel not run as
  administrator, no PawnIO driver, its reader did not start, its web server not running), the
  panel showed "--" and only `libre-panel sensors`, `doctor` and the Control page said why, in
  technical words. Now one sentence says what to do, in the user's language: a line under the
  status line of the tray menu and in the tooltip of the icon, `hint` in the panel's state
  (`/api/app`, `GET control/panel`) and on the LibreHardwareMonitor row of `GET
  control/sources`. It keeps quiet while LibreHardwareMonitor starts and about what nobody can
  change. A Windows download that cannot use its library (damaged, or deleted by a virus
  scanner) reports that as the source's `error` instead of asking a web server that is not there
- Services follow `config.toml` whenever the panel loop starts. A service switched
  on (or off, or given other options) while the panel was paused, or in a
  `config.toml` that did not load at the start and was fixed later, stayed as it
  was until the file changed once more
- A change of `[services]` saved together with a theme name that does not exist (a typo, a
  theme not installed yet) reached the services only with the next save. They now follow
  `config.toml` whenever it loads, whether or not its theme does; the panel keeps its theme
- A theme's first frame read the pictures of its fans' rotors (about 200 ms) and built the
  bevels of a faceted or acrylic clock (some 30 ms a letter). The warm-up before the first
  frame does both now: SPUR II's screens show their first frame in about half the time
- Moving a theme to a narrower panel (*adapt*) kept the room each text may take
  (`max_width`): a hardware name kept the width it had on a 9.2" bar and ran into its
  neighbours. It narrows with the panel now
- The first frame after the start took 200 to 350 ms: the first theme's
  texts, cards, graph strips and fans were made in it, which held up a video
  panel's first moments. The theme is drawn once while the loop is built, in
  a thread of its own, as the next theme is for a switch, so the first frame
  takes a few milliseconds. The readings of that drawing are the first
  frame's sample, and the start waits for it two seconds at most
- SPUR Horizon, SPUR Header and SPUR Session read the GPU's hot spot by a key
  that no source made (`temp.gpu.hot_spot`): on a PC they showed "--", while
  the demo, which had made the key up, showed a value. LibreHardwareMonitor
  now gives `gpu.hotspot` (the "GPU Hot Spot" sensor of the card `gpu.temp`
  is read from), the themes read it, and the demo has it as it has
  `gpu.temp`. A test keeps every key the SPUR themes read from the hardware
  to what the source makes from real output
- A panel loop that could not start (an unknown `driver` in `config.toml`: a typo,
  a display plugin that was removed) left the sensor sources it had started running,
  and the app tries again every 30 seconds, so their threads piled up. The display is
  made first now, before any source starts
- While the panel was unplugged (or not found), every message of the services, a music
  message with its cover among them, piled up in the main loop for as long as that
  lasted. They wait in the services' queue now, the newest 20, and show when the panel
  is back

**Changes**
- The theme a new install starts with depends on the panel: Lumen on the 1920×480 bars
  (the 8.8" and 9.2"), Libre Default on every other panel. A `config.toml` without a
  `theme` means "the panel's own default", so `libre-panel config init` and the app's
  first write no longer put `theme = "libre-default"` into it; a comment says what the
  default is and how to name another. The panel is the one `device.model` names, else
  (with `model = "auto"`) the TURZX USB panel on the bus, found by its ids and not opened,
  else the panel that connects: the theme then changes once, with the usual transition.
  A theme in `config.toml`, also the `theme = "libre-default"` that 0.4 wrote, and
  `libre-panel run --theme` always win. *Start* asks "How should it look?" with the
  panel's default first, marked *Default*; the app, the tray and the console API report
  the theme the panel really shows (`default_theme` in the panel's state, `default` in
  `GET /api/pages`)
- A setting that `config.toml` gets for the first time, such as the theme chosen in the
  app, is written under the commented line that explains it (`# theme = "slate"`) and no
  longer above the title of the file
- SPUR II's screens are kept as a legacy set: their themes carry
  `"collection": "SPUR II"`, and galleries show Libre Panel's own themes
  first and SPUR II's apart, for those who want to see where Libre Panel
  started
- Studio's card shows what plays, else the weather (the weather card, with its moving
  symbol and the next hours), else a calendar sheet, and changes with a line of light. It
  is wider than the old card, and the rings move 25 px to the right for it; they are light
  in milled grooves now, with the names of the processor, the memory and the graphics card
  under them. The clock, the date and the network keep their places
- Libre Default, the theme a new install starts with on every panel but the bars: lit
  rings and disk segments, a CPU history that scrolls through the last 30 seconds, numbers
  that glide and the app's colours; the corner where the weather was shows what plays,
  else the weather, else the day
- The pictures of the README and the website show Lumen (on top, in the gallery, in the
  social preview), and the README lists Libre Panel's own themes first, SPUR II's apart
- The old browser editor is gone; the app replaces it. A bookmark of `/classic/`
  leads to the app, and plugins' editor pages open inside it (*Pages* in
  *Design*, or Ctrl+K)
- The video mode turns itself on: `video.mode` is `"auto"` by default, which
  streams to a 9.2" (the panel it is proven on) as soon as ffmpeg is found and
  `local_clip` names the panel's standby clip. `libre-panel doctor` and the log
  say what is missing. A `mode = "off"` in `config.toml` stays off
- macOS is no longer supported: no download, no tests, no macOS code. Windows
  and Linux are. Nobody downloaded the macOS builds of any release. Gone with
  it: the disk image and the menu bar app, the LaunchAgent, and reading the
  volume and the player through `osascript`. A system that has no autostart
  (neither Windows nor an XDG desktop) now says "not supported on this system"
  to `libre-panel autostart` and in the editor, instead of getting a file that
  does nothing
- Windows: the GPU, temperatures, power and fans showed nothing unless
  `librehardwaremonitor` was listed in `config.toml`. New configurations now
  start with `providers = ["load", "audio", "librehardwaremonitor", "psutil"]`
  (on Linux `["load", "audio", "psutil"]`, elsewhere `["load", "psutil"]`), and
  an unset list means the same. Where Libre Panel
  cannot read LibreHardwareMonitor itself (see above), that one must run as
  administrator with *Options → Remote Web Server → Run*; where it does not run,
  asking costs nothing (one second at most, waits that grow to half a minute,
  one line in the log) and those readings show "--". A
  `config.toml` from 0.4 or earlier that names psutil alone (what those
  versions wrote) gets the new list too; any other list is left as it is
- `libre-panel sensors` lists the load and the volume too, and on Windows
  `libre-panel sensors` and `libre-panel doctor` say what to do when no
  temperature, power or GPU reading arrives
- Without a queue (`"queue": false`), messages that come at the same moment
  are decided one after the other: a warning that comes with a volume
  change takes over from it at once
- The demo readings of the editor, the previews and the pictures hold 1900
  readings of history instead of 600, so the wide graphs of the SPUR themes
  are full in them, and they carry a made-up round report, so `round-report` is
  complete in them
- Windows downloads: with `mode = "auto"` the library inside Libre Panel is read
  first; a LibreHardwareMonitor web server that happens to run only fills in
  what Libre Panel cannot read (the CPU's readings without administrator
  rights). Drive temperatures (`temp.nvme.*`) come only from the web server for
  now: set `mode = "webserver"` to keep them, or to read only it
- SPUR II and Studio read the sensors twice a second and their numbers
  glide; SPUR II's graphs span 36 seconds in video mode, moving 25 pixels a
  second. The graphs of Slate, Libre Default and Column have a window in
  seconds for video mode too; on the serial panels these themes run on
  nothing changes
- While it drives a panel, Libre Panel keeps its frames on time as SPUR II
  did when the PC is busy: Windows' timer at 1 ms, fewer and shorter pauses
  for garbage collection, and the threads that feed a video panel above
  normal priority (Windows)
- Drawing is faster: cards about five times (only their round corners are
  drawn three times larger), so a theme's first frame takes half as long;
  numbers that change a digit at a time about six times (each digit is drawn
  once and kept); bars and rings are drawn again only when they move by a
  visible amount, and pieces go onto the frame in place
- The main loop draws one frame at a time through `MainLoop.step(now)`, with
  the sensor hub, display, host and clock given: tests and plugins' test
  benches drive the real loop frame by frame instead of copying it
- With the `game` service on, the `game.*` readings count the frames of the
  game it found instead of the program with the most frames
- Windows: what is playing follows Windows' own reports of a new track, a
  pause or another player instead of asking once a second, so a new track
  shows at once. The cover is read when the track changes, not at every look;
  a look every five seconds is the safety net, and the position still moves on
  by itself. Linux asks once a second as before
- `tools/motion_bench.py` takes `--dust`, `--finish facet` and `--fans N`: it
  runs each theme with and without that look, side by side frame by frame, and
  prints what the look adds to a frame
- LibreHardwareMonitor's `cpu.name` and `gpu.name` are shortened like the names
  Libre Panel finds itself ("Ryzen 7 7800X3D" for "AMD Ryzen 7 7800X3D")
- SPUR Classic's RAM heading names the memory (`mem.name`) instead of giving
  its size only. The RAM modules (ring, big number, history) show the name
  under their title as CPU and GPU do, and the System module lists it as a
  fourth row where there is room
- SPUR Header sets the frame rate, the 1% low and the fans where SPUR II has
  them: the game's logo or name has the room of a short name (51 px, up to 72
  for a longer one: a name is cut with an ellipsis, a logo made smaller) in
  place of 120 px, and the fans spread over what was gained as SPUR II
  spreads them
- The demo readings of the editor, the previews and the pictures gained a
  made-up player icon (`media.app_icon`, drawn when asked, a note on a
  rounded square) and a game that downloads (`downloads`, in the state the
  service publishes, 24 GB at about 38 MB/s), so the playback card shows its
  app with an icon and the Studio's NET section shows a download there, as
  they do with a player and a download on a real PC
- SPUR Session sets the rule and the fans of its header where SPUR II has
  them, as SPUR Header does: the game's logo or name has the room of a short
  name (51 px, up to 62 for a longer one) in place of 120 px, and the fans
  spread over what was gained

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
