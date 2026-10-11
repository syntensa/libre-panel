# Configuration

Everything specific to you or your machine lives in one file, `config.toml`.
Without it Libre Panel runs on defaults. Create a commented one with:

```bash
libre-panel config init
libre-panel config path     # where it is
```

| System | Location |
|---|---|
| Windows | `%APPDATA%\LibrePanel\config.toml` |
| macOS | `~/Library/Application Support/LibrePanel/config.toml` |
| Linux | `~/.config/libre-panel/config.toml` |

Set `LIBRE_PANEL_HOME` to use another folder (portable installs). Changes to
`config.toml` and to the active theme are picked up while running.

## Reference

```toml
theme = "libre-default"        # built-in or user theme
language = "auto"              # "auto" (system language), "en" or "de"
# refresh_ms = 1000            # override the theme's refresh interval
fps = 10                       # 1-60; values glide between readings
transition = "fade"            # between themes: "cut", "fade", "slide", "tube" or from a plugin

[device]
model = "auto"                 # or an id from `libre-panel models`
driver = "auto"                # panel if connected, else PNG; "turzx" or "virtual" to force
brightness = 60                # 0-100
rotate = 0                     # 180: upside down, for a panel mounted the other way
                               # round (also in the tray menu and the editor); the
                               # editor's preview and the PNG file stay upright
output = "libre-panel-frame.png"   # frames go here when no panel is used;
                                   # a relative path is inside the settings folder
port = ""                      # serial panels: found by their USB ids; only if that
                               # picks the wrong port: "COM5", "/dev/ttyACM0"

[sensors]
# order = priority. Without this line: ["load", "psutil"], on Windows
# ["load", "librehardwaremonitor", "psutil"]. More: "audio", "demo", plugins
providers = ["load", "psutil"]

[sensors.librehardwaremonitor]
url = "http://127.0.0.1:8085/data.json"
# stale_seconds = 5            # no answer for this long: its readings are hidden
                               # ("--" on the panel); 0 keeps the last ones

[sensors.psutil]
# disk = "C:\\"                # the drive behind disk.load (default: the system drive)
# ping = "1.1.1.1:443"         # what net.ping connects to (host:port), only while a
                               # theme shows the ping

[weather]
enabled = false                # off unless you turn it on
provider = "open-meteo"
# latitude = 0.0               # your location; find it with:
# longitude = 0.0              #   libre-panel location "Your City"
units = "metric"               # or "imperial"
update_minutes = 15

[video]
mode = "off"                   # "on": smooth video mode (TURZX USB panels, needs ffmpeg)
fps = 50                       # frames per second in video mode
local_clip = ""                # required for "on", see below
```

## Sensor sources and their order

`[sensors] providers` lists the sources in order of priority: when two of them
have a value for the same key, the earlier one wins. A quicker or better source
goes first.

A configuration that names no list (no `config.toml`, or none with `providers`)
starts with `["load", "psutil"]`, and on Windows with
`["load", "librehardwaremonitor", "psutil"]`; `libre-panel config init` writes
that list into a new file. **A list you wrote is never changed.** A
`config.toml` made by an earlier version names `["psutil"]` alone: put the
sources you want in front of it.

| Source | Gives | Where |
|---|---|---|
| `load` | `cpu.load` and `gpu.load`, measured ten times a second | CPU everywhere, GPU on Windows (any vendor) |
| `psutil` | CPU, memory, drives, network, battery, the busiest programs; temperatures and fans where the system has them | everywhere |
| `librehardwaremonitor` | temperatures, power, clocks and fans of CPU, GPU and mainboard | Windows (in its default list), needs LibreHardwareMonitor running, see [below](#windows-sensors-gpu-power-fans) |
| `audio` | `audio.volume` (0 while muted) and `audio.muted` (1 or 0) of the default output | Windows, Linux (`wpctl` or `pactl`), macOS |

**Fast load** (`load`): `cpu.load` and, on Windows, `gpu.load` are measured in
a background thread ten times a second and read on every frame, so bars and
graphs follow the load as it happens. The other sources give it only once per
reading (`refresh_ms`) or once a second. It needs no other program and no
administrator rights.

- The processor load comes from `GetSystemTimes` on Windows and from psutil
  elsewhere.
- The GPU load is what Task Manager shows, read from Windows' performance
  counters, for any vendor: the busiest 3D engine of the graphics card that
  has the most memory in use of its own (chosen again every minute). Where
  the counters are missing (some virtual machines), `gpu.load` is simply not
  there and the next source supplies it; the log says so.
- It runs only while a theme on the panel shows `cpu.load` or `gpu.load`.
- It stands before the sources whose load it replaces, also in the default
  list. In a list of your own put it first, e.g.
  `providers = ["load", "librehardwaremonitor", "psutil"]`; behind
  LibreHardwareMonitor its readings would never be used.

**Volume** (`audio`): the volume and the mute of the default output device,
read on every frame, so a volume bar follows the volume keys at once. It is not
in the default list; add it: `providers = ["load", "psutil", "audio"]`.

- `audio.volume` is 0 while the sound is muted; `audio.muted` is 1 then, else 0.
- Windows: Windows' own sound interface, no program needed. Windows reports
  every change by itself. When the default output device changes (a headset
  plugged in, speakers chosen) the volume follows it within two seconds.
- Linux: `wpctl` (PipeWire), else `pactl` (PulseAudio), asked four times a
  second. Without either tool installed there are no readings, and the log
  says so.
- macOS: `osascript`, asked twice a second.
- It runs only while a theme on the panel shows `audio.volume` or
  `audio.muted`.

## More sensor sources

Besides the hardware, Libre Panel reads a few things from your life. The sun,
the moon and what is playing come by themselves; the others are listed in
`[sensors] providers` and set up in their own table.

**The sun and the moon** (`sun.*`, `moon.*`) are computed, no internet
needed. Sunrise and sunset need your place: `latitude` and `longitude` in
`[weather]` (also when the weather itself is off).

**What is playing** (`media.*`, the cover and the icon of the player) is read
only while a theme or a service reads it:

- Windows: the media controls of Windows, whatever the volume pop-up shows
  (Spotify, browsers, media players). Windows reports a new track, a pause or
  another player at once, so the panel follows without delay; a look every
  five seconds is only the safety net, and the position moves on by itself in
  between. The cover is read when the track changes. The installer brings what
  it needs; a pip install needs `pip install "libre-panel[media]"`.
- Linux: any MPRIS player through `playerctl` (install it from your
  distribution), asked once a second.
- macOS: Spotify or Music, asked once a second.

Two pictures go with it, for an image widget (`"src": "@media.cover"`):
`media.cover`, the album cover, and `media.app_icon`, the icon of the program
that plays. The icon is found once per program, never sent anywhere, and the
notices service puts it on the cover of its "Now playing" message:

- Windows: the logo of a Store app (from its package), else the icon of the
  program that runs under the name Windows gives the player.
- Linux: the PNG that the player's desktop entry names; a player whose icon
  exists only as an SVG has none.
- macOS: the `.icns` file of the app.

**Calendars** (`calendar.*`): the secret address of a calendar (Google:
settings of the calendar → "Secret address in iCal format"; Outlook:
calendar → share → publish → ICS; iCloud and Nextcloud: share as a link)
or an `.ics` file. The address is a secret: it stays in your settings file
and is never written to the log.

```toml
[sensors]
providers = ["load", "psutil", "calendar"]

[sensors.calendar]
sources = ["https://calendar.google.com/calendar/ical/.../basic.ics"]
days = 14                      # how far ahead
update_minutes = 15
```

**Home Assistant** (`ha.*`): every entity that matches `entities`, with its
unit and name. Make a token in Home Assistant under your profile →
Security → Long-lived access tokens.

```toml
[sensors]
providers = ["load", "psutil", "homeassistant"]

[sensors.homeassistant]
url = "http://homeassistant.local:8123"
token = "eyJ..."
entities = ["sensor.*", "binary_sensor.front_door", "climate.*"]
poll_seconds = 10
```

**MQTT** (`mqtt.*`; `pip install "libre-panel[mqtt]"`, included in the
installers): what your devices publish. A JSON object is taken apart into one
reading per field.

```toml
[sensors]
providers = ["load", "psutil", "mqtt"]

[sensors.mqtt]
host = "192.168.1.10"
port = 1883
username = ""
password = ""
topics = ["home/+/temperature", "zigbee2mqtt/#"]
units = { "home/+/temperature" = "°C" }
```

**Game frame rate** (`game.*`, Windows): Intel's
[PresentMon](https://github.com/GameTechDev/PresentMon/releases) counts the
frames of the game you play. Download its console version (MIT license) and
name it; Libre Panel starts it only while a theme shows the frame rate and
stops it a minute after. PresentMon needs administrator rights or your
account in the "Performance Log Users" group (Computer Management → Local
Users and Groups → Groups; sign out and in again).

```toml
[sensors]
providers = ["load", "librehardwaremonitor", "psutil", "presentmon"]

[sensors.presentmon]
path = "C:/Tools/PresentMon-2.3.0-x64.exe"
```

Show these readings with the Dashboard module (`ha.sensor.living_room_temperature
= Living room`, one per line), a list, or any widget with a `sensor`.
`libre-panel sensors` lists what is there.

## Services and modes

Services are helpers that run beside the panel: Libre Panel's own
([below](#built-in-services)) and those of plugins. They are off until you
list them; each can have its own options table. A mode changes the frame
rate and optionally the theme while a service keeps it switched on. Details
are in [Plugins](PLUGINS.md).

```toml
[services]
enabled = ["gamemode"]

[services.gamemode]
poll_s = 1.0

[modes.game]
fps = 30
theme = "slate"
```

## Built-in services

Libre Panel brings a few services of its own (no plugin needed). Each is off
until `[services] enabled` lists it, and takes its settings from a table with
its name. `libre-panel plugins` lists them as "built in".

```toml
[services]
enabled = ["notices"]
```

### Notices

Short messages on the panel for what happens around it. The theme decides how
they look (the card, or the band after SPUR II: `"toast": {"style": "band"}`)
and which kinds it shows (`"toast": {"off": ["music"]}`); a screen that shows
something itself leaves its kind out.

| Kind | When | The message |
|---|---|---|
| `volume` | the volume changes by at least 1, or is muted or unmuted; the value the service starts with is no change | the level with a bar, or "Muted" |
| `music` | a new track starts playing; not a pause, a resume, or the track that was playing at the start | title and artist, with the cover and the icon of the player when a source brings them |
| `warning` | a sensor reaches a limit you set | "CPU temperature 92 °C", in the theme's warning colour at `warn` and its critical one at `crit` |
| `panel` | the panel is back from a restart that cleared a hung video decoder (not from a replug) | "Panel restarted 21:05" |

```toml
[services.notices]
volume = true          # the volume; needs "audio" in [sensors] providers
music = true           # a new track
restarted = true       # "Panel restarted"
quiet_s = 120          # warnings: the same sensor is not told again within this time

[[services.notices.warnings]]
key = "cpu.temp"       # any reading, see: libre-panel sensors
warn = 80              # a message at this value (optional if crit is set)
crit = 90              # a stronger one at this value
# name = "CPU"         # the text before the value (default: the reading's name)
# max = 100            # the value a full bar stands for (percentages need none)
# icon = "cpu"         # one of the built-in icons (default: from the key)

[[services.notices.warnings]]
key = "gpu.temp"
crit = 85
```

- A warning is told when the value reaches a limit and not again until it has
  been below `warn` (below `crit` when there is no `warn`) in between, and
  never twice within `quiet_s`. A worse level (`crit` after `warn`) is told
  at once, also in the quiet time. A sensor that is past its limit when the
  service starts is told too.
- Notices look at every new reading, that is every `refresh_ms` of the theme.
  For a volume bar that follows the volume keys closely use a theme with a short
  `refresh_ms` (100 to 250).
- While the service runs, the sources it needs run too, although no theme shows
  them: what is playing (`media.*`), the volume (`audio.*`) and the warned
  sensors. They cost what they cost otherwise (a player is asked once a
  second); switch off `volume` or `music` to save that.
- Texts follow the language setting. Each notice has its own `key`, so the
  same one again (the volume while it turns) changes in place instead of
  coming again.

### Game mode

The panel changes by itself when you play: it switches to the mode `game`
(its own frame rate and theme, `[modes.game]`) with an entrance that shows the
game's logo, and back a few seconds after you quit.

```toml
[services]
enabled = ["game"]

[services.game]
programs = ["emulator.exe = Retro Games", "othergame.exe"]  # programs that are games
steam = true           # and any game installed through Steam
# steam_path = ""      # where Steam is, if Libre Panel does not find it
# ignore = ["Wallpaper Engine"]  # games and tools in Steam's library that never count
automatic = true       # switch by itself when a game runs
force = false          # game mode on, game or not (to try the screens)
entrance = true        # the entrance; false: a "Game mode" message and the theme's transition
logo = true            # the game's logo (Steam's cache) in the entrance and on screens
linger_s = 6           # the mode stays this long after the game's program ended
poll_s = 2             # how often to look for a game (0.25 to 60)

[modes.game]
fps = 30               # frames per second while the mode is on
theme = "slate"        # optional: another theme
```

- **What is a game.** A program in `programs` (its file name, case does not
  matter; `name = A Better Name` sets the name to show), and with `steam` any
  program of a game installed through Steam. Steam's libraries are found
  from its own files (`libraryfolders.vdf`, `appmanifest_*.acf`), on Windows,
  Linux (also Flatpak and Snap) and macOS. Tools in a library, such as Proton,
  the Steam Linux Runtime or SteamVR, are not games; `ignore` adds more.
  Windows programs under Wine and Proton are found by the folder they work in.
- **The window in front.** On Windows a Steam game counts only once its window
  has been in front, because a library also holds programs that run all day (a
  wallpaper). Where Libre Panel cannot tell which window is in front (Linux,
  macOS), a running program of a Steam game is enough.
- **The beginning.** The service shows the name (`game.name`) and the logo
  (`game.logo`, an image: `"src": "@game.logo"` in an image widget) to the
  screens, then switches. The logo comes from Steam's picture cache, cut out
  and not scaled, or the game's header picture; a program that is not a Steam
  game shows its name in big letters. The switch waits for the next reading
  (at most a second) so that the first game screen already has the logo.
- **The end.** When the program has been gone for `linger_s` seconds the mode
  ends with the entrance for the end. Games restart while they are set up and
  launchers swap their process, so the mode does not follow every flicker.
- **Without the entrance** (`entrance = false`) the switch uses the
  `transition` of `config.toml`, and a message says "Game mode" (and "Game
  ended"), so the theme's toast style draws it.
- `force = true` keeps the mode on, which is how to try the game screens;
  `automatic = false` with `force = false` does nothing. A game that runs
  while `force` is on is named.
- The frame rate of the game (`game.fps`) comes from the `presentmon` source,
  which is a separate setting.

### Autopilot

Picks the theme that fits what you are doing: the album theme while music
plays, a big clock at night, a load display while the machine is busy. It
follows rules, and when none holds, the theme of `config.toml` shows. The
autopilot never writes your configuration.

```toml
[services]
enabled = ["autopilot"]

[services.autopilot]
hold_s = 8             # a new choice must hold this long before the panel follows
min_s = 20             # a theme then stays at least this long
quiet_after_boot_s = 240   # right after the system started the load counts for nothing
# transition = "tube"  # how the theme changes ("" is the transition of config.toml)

[[services.autopilot.rules]]     # checked from the top: the first that holds wins
name = "Music"
theme = "studio"
when = { music = true }

[[services.autopilot.rules]]
name = "Call"
theme = "slate"
when = { microphone = true }
immediate = true       # no minimum time for this one
hold_s = 2             # and its own hold time

[[services.autopilot.rules]]
name = "Busy"
theme = "column"
when = { cpu_over = 80, for_s = 30 }   # the mean load over 30 s

[[services.autopilot.rules]]
name = "Editor"
theme = "orbit"
when = { apps = ["code.exe", "devenv.exe"] }

[[services.autopilot.rules]]
name = "Night"
theme = "pico"
when = { time = "22:00-06:30" }        # may cross midnight
```

- **Conditions** (all of a rule's must hold, a rule needs at least one):

  | Condition | Holds when |
  |---|---|
  | `music = true` | something is playing (`media.state` is "Playing") |
  | `microphone = true` | a program is recording (Windows; elsewhere never) |
  | `cpu_over = 80`, `gpu_over = 80` | the mean load over `for_s` seconds (20 by default) is at least that; it counts once four fifths of the window has been seen, so a peak at the start is no load |
  | `apps = ["a.exe"]` | one of these programs is in front (Windows; where Libre Panel cannot tell, one of them runs). Case does not matter |
  | `time = "22:00-06:30"` | the local time is in the window, start included, end not |

  `music = false` and `microphone = false` hold when there is none. A rule
  with no condition, or with one that is not on this list, never holds; it is
  reported in the log and left out.
- **A theme that is not installed** makes its rule be skipped, with one line in
  the log; it is used as soon as it is there.
- **Game mode.** While a mode such as `game` is on, the autopilot rests: its
  choice stays and shows again right after the game, and a change in the
  meantime must hold afresh. A service that asks for a theme with a higher
  priority (a report) goes first.
- **Cost.** The load rules keep the CPU and GPU load sources running (`cpu.load`,
  `gpu.load`; the `load` source is the one that reads them ten times a second);
  a music rule keeps the media source running; `microphone` and `apps` look at
  the system once a second (the list of programs every two seconds).
- Rules are read when the service starts: change `config.toml` and it restarts
  by itself.

## Language

Libre Panel speaks English and German. `language = "auto"` follows the
system language; the editor has a switch at the bottom right, which writes
this setting. It covers the editor, the tray menu, messages about the panel,
and the panel itself: weekday and month names (in German with "27."), and
weather descriptions. Texts that are part of a theme (such as "CPU TEMP")
stay as the theme's author wrote them. Command-line output for diagnostics
(`models`, `devices`, `sensors`, `doctor`) is in English.

Translations live in `src/libre_panel/locale/<language>.json`, keyed by the
English text; a test makes sure every text has one. To add a language, copy
`de.json`, translate the values and add the language to `LANGUAGES` in
`src/libre_panel/i18n.py`.

## Video

TURZX USB panels (VID 1CBE) have a hardware video decoder. In video mode
Libre Panel sends every frame as H.264 video at up to 50 fps instead of one
PNG image per frame, which the panel accepts only about 9 times a second.
Moving things run smoothly: values glide until the next reading and graphs
scroll on every frame instead of jumping once per reading.

```toml
[video]
mode = "on"
local_clip = "usr/data/standby.h264"
```

- **ffmpeg** encodes the video. Install it (Windows: `winget install ffmpeg`;
  macOS: `brew install ffmpeg`; Linux: the `ffmpeg` package), or set
  `ffmpeg = "C:/path/to/ffmpeg.exe"`. It must include libx264, which the
  usual builds do. It uses one CPU core, lightly.
- **`local_clip`** is the name the panel's video start command carries: the
  clip the panel plays by itself while the PC is off, if it has one (vendor
  app: standby video) and the mainboard keeps USB powered then. The command
  copies this name into the panel's memory, and the panel plays whatever
  name is there. Nothing is saved on the panel; after it loses power it is
  back to its own setting.
- When Libre Panel stops, the panel returns to its power-on frame rate and
  keeps showing the last frame for as long as it has power.
- Rarely the panel's video decoder hangs (the picture freezes). Libre Panel
  then restarts the panel, which takes about 10 seconds, and carries on. If
  that does not help three times in a row, it asks you to replug the panel.

| Setting | Default | |
|---|---|---|
| `mode` | `"off"` | `"on"` turns video mode on |
| `fps` | 50 | frames per second (1–60) |
| `device_fps` | 60 | the rate reported to the panel; must be at least `fps`. The panel's player never catches up once behind, so it gets a little more than it needs |
| `crf` | 25 | quality, 0–51; lower is sharper and needs more USB bandwidth |
| `preset` | `"superfast"` | x264 speed preset |
| `keyframe_s` | 10 | seconds between keyframes; below 3 flat backgrounds visibly pulse |
| `maxrate` | `"2M"` | highest bit rate, e.g. `"1500k"` |
| `ffmpeg` | `""` | path to ffmpeg; empty = look in `PATH` |
| `local_clip` | `""` | see above |

The settings were measured on the 9.2" panel; the details are in
[the protocol notes](protocol/turzx-usb.md#two-layers-h264-video--png-overlay-smooth-path).

## Windows sensors (GPU, power, fans)

On Windows the temperatures and power of the processor and the graphics card,
and the fans, come from
[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor);
psutil has none of them there. New configurations list it, and Libre Panel
finds it by itself, at the latest half a minute after it is running:

1. Install LibreHardwareMonitor and run it **as administrator** (CPU
   temperatures and power need its driver).
2. *Options → Remote Web Server → Run*.
3. Check that `providers` in your `config.toml` lists `"librehardwaremonitor"`,
   ahead of `"psutil"`: `providers = ["load", "librehardwaremonitor", "psutil"]`.
   A file made by an earlier version names `psutil` alone.

`libre-panel sensors` and `libre-panel doctor` say so when no CPU or GPU
temperature or power reading arrives.

Where LibreHardwareMonitor does not run, nothing else is lost: those readings
show "--". Libre Panel gives a request up after a second, asks less and less
often (every half minute at last) and writes one line to the log.

If LibreHardwareMonitor stops answering (closed, crashed, blocked), its
readings stay for `stale_seconds` (5 by default) and then disappear, so the
panel shows "--" instead of numbers that stand still. A line in the log says
so, and another when it answers again.

## Weather

Weather is off by default and nothing is sent anywhere until you enable it.
When enabled, only your coordinates go to Open-Meteo every `update_minutes`.
Weather data by [Open-Meteo.com](https://open-meteo.com), CC BY 4.0, free for
non-commercial use.
