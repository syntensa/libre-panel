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
| Linux | `~/.config/libre-panel/config.toml` |

The portable zip keeps everything in the folder `data` beside `libre-panel.exe` instead, as
long as the file `portable.txt` lies there too: the whole folder can move to a USB stick or
another PC. Its first start takes over the settings of a Libre Panel installed for you, once;
delete `portable.txt` to use your own folder as the setup does. Set `LIBRE_PANEL_HOME` to use
any other folder. Changes to
`config.toml` and to the active theme are picked up while running. Choose a folder that
only you can change, such as one in your profile: a Libre Panel that starts with the
highest rights runs the programs and plugins this folder names with those rights
([Running in the background](BACKGROUND.md#windows-starting-with-the-highest-rights)).

## Every setting is in the app

Nothing in this file needs a text editor: *Settings* in the app (the button at the top
right, or the tray's *Settings*) changes each of its settings and writes it with the
rest of the file, comments included, left as it is. A setting that would make the file
invalid is refused with the reason, and nothing is written.

| Section | What it changes in `config.toml` |
|---|---|
| Panel | `accent`, `[device]` (`model`, `brightness`, `orientation`, `rotate`, `motion`, `dark_on_quit`, `dark_on_sleep`, `night`, `night_from`, `night_to`, `night_brightness`, `dark_when_away`, `away_brightness`, `at` (*More panels*: where it stands); under *Advanced* `driver`, `output`, `still` (TURZX USB panels), `port`; `jpeg` is written by the panel test) and `[video]` (`mode`, `fps` (also for a udisp USB display), `local_clip` while smooth motion lacks it; under *Advanced* `ffmpeg`, and `local_clip` once it is set) |
| Sensors | `[sensors] providers` (which sources, and their order; those not in use under *More sources*), the hardware's names, and under *Advanced* `refresh_ms` and `[sensors.librehardwaremonitor]` (`mode`, `url`) |
| Game mode and more | `[services] enabled` and the options of each `[services.<name>]` table: switches, numbers, texts and lists of texts or numbers. The rules of the autopilot have their own page (*Edit*, under the autopilot), the warnings of `notices` and the limits of `cooling` are still written here |
| Weather and place | `[weather]` (`enabled`, `latitude`, `longitude`, `place`, `units`, `update_minutes`) |
| Start and system | `language`, `check_updates`, `light_mode`, `[hotkeys]`, and starting with the system (`libre-panel autostart`) |
| Start (the page) | `cards`: *Cards on the panel*, under the panel while its theme has a card slot that leaves them to you (also in *Look*) |
| About | nothing: the version, the licence, the notices, how to report a panel, and the folders (with *Open folder*) |

The running Libre Panel follows a change at once, services included (one whose options
change starts again by itself). The exceptions are the sensor sources and the weather:
they are made when the panel starts, so the app says that a change takes effect after
a restart, and its *Restart the panel* button does that without quitting Libre Panel.

## Reference

```toml
# theme = "slate"              # built-in or user theme; without one the panel's own
                               # default shows (see "The theme on the panel")
language = "auto"              # "auto" (system language), "en", "de", "fr", "es", "it", "pl", "pt", "ru", "ja", "zh"
# refresh_ms = 1000            # override the theme's refresh interval
# check_updates = true        # look once a day for a newer version (not set: Start asks)
# allow_notify = false        # other programs may show a notice (see below)
fps = 10                       # 1-60; values glide between readings
# [transitions]                # how the panel goes from one screen to the next
# default = "fade"             # (see "Transitions"; Settings → Panel → Transitions)
# accent = "system"           # every theme in one accent colour: "system" (Windows'),
                               # "#E0457B" (your own); empty or left out: each theme's own
# cards = ["playback", "ai", "forecast", "date"]
                               # the cards of the card slots that leave them to you
                               # (Lumen's, in each of its versions), in the order they are
                               # asked: the first that has something to show is on show
                               # (Start: Cards on the panel)

# [[panels]]                   # a panel beside this one (see "More panels at once")
# theme = "lumen"
# model = "turing-3.5"

# [sequence]                   # screens in turn (see "Screens in turn"), instead of one theme
# on = true
# [[sequence.steps]]
# theme = "almanac"
# minutes = 5                  # seconds, minutes and hours, together: at least 5 seconds

[device]
model = "auto"                 # or an id from `libre-panel models`
driver = "auto"                # panel if connected, else PNG; "turzx" or "virtual" to force
brightness = 60                # 0-100
rotate = 0                     # 180: upside down, for a panel mounted the other way
                               # round (also in the tray menu and the app); Design's
                               # preview and the PNG file stay upright
orientation = ""               # "portrait": a bar that stands on end, see A panel on end
motion = "auto"                # calm on the older USB serial panels (a picture a second,
                               # no gliding, moving symbols or fades), full on the others;
                               # "full" or "calm" for every panel
still = "auto"                 # TURZX USB panels without the video mode: each picture as
                               # "png" or as "jpeg" (decoded by the panel's hardware:
                               # faster); "auto" is JPEG on the 5.2", which shows no PNG,
                               # and PNG on the others
video_range = "auto"           # TURZX USB panels in video mode: the range of the video's
                               # values, "limited" (16-235) or "full" (0-255); "auto" is
                               # full on the 9.2" and the 12.3", limited on the others
                               # (see Video)
jpeg = "4:4:4"                 # udisp USB displays that take JPEG (Waveshare's USB display
                               # image): its colours at every pixel, as the protocol's
                               # drivers send them, or "4:2:0", at every second pixel each
                               # way, for a decoder that shows only that; the panel test
                               # tries "4:2:0" where the test card stays black and writes it
dark_on_quit = false           # true: black and its light off when Libre Panel quits
                               # (Windows shutting down too); lit again at the next start;
                               # Turing 3.5" and UsbPCMonitor turn their screen off too
dark_on_sleep = true           # dark while the computer sleeps (Windows; Linux with logind),
                               # lit when it wakes
night = false                  # true: a brightness of its own at night, from night_from to
night_from = "22:00"           # night_to (over midnight too); 0 is dark. brightness holds
night_to = "07:00"             # the rest of the day. "sunset" and "sunrise": the sun's at
                               # the weather's place, day by day (without one 19:00, 7:00)
night_brightness = 10
dark_when_away = true          # the computer locked or its monitor off (Windows; on Linux
                               # the session locked, through logind): the
away_brightness = 0            # panel takes away_brightness (0: dark) until you are back
output = "libre-panel-frame.png"   # frames go here when no panel is used;
                                   # a relative path is inside the settings folder
port = ""                      # serial panels: found by their USB ids; only if that
                               # picks the wrong port: "COM5", "/dev/ttyACM0"
# at = [-275, 0]               # where it stands beside the others (More panels): its
                               # picture's top left corner, in millimetres

[sensors]
# order = priority. Without this line: ["load", "audio", "linux", "psutil"], on Windows
# ["load", "audio", "librehardwaremonitor", "presentmon", "psutil"]. More: "demo", plugins
providers = ["load", "audio", "linux", "psutil"]

[sensors.librehardwaremonitor]
# mode = "auto"                # "auto", "service", "inprocess" (inside Libre Panel), "webserver"
# dll_dir = ""                 # pip install: the folder with LibreHardwareMonitor's libraries
# poll_seconds = 1             # how often it is read
url = "http://127.0.0.1:8085/data.json"   # the web server (mode "webserver", and "auto"
                               # where it cannot be read inside Libre Panel)
# stale_seconds = 5            # no answer for this long: its readings are hidden
                               # ("--" on the panel); 0 keeps the last ones
# gpu = "RTX 4070"             # with several graphics cards: the one whose name has this

[sensors.linux]
# gpu = "RX 7900"              # with several graphics cards: the one whose name has this
                               # gives the gpu.* readings (default: the one gpu.name names)
# nvidia = "auto"              # "off" never loads NVIDIA's library (NVML)

[sensors.psutil]
# disk = "C:\\"                # the drive behind disk.load (default: the system drive)
# ping = "1.1.1.1:443"         # what net.ping connects to (host:port), only while a
                               # theme shows the ping (the Ping module: net.ping.lost, the
                               # share of the last 20 tries without an answer, and its host);
                               # or up to four: ["1.1.1.1:443", "192.168.1.1:80"], the others
                               # as net.ping.2 to net.ping.4 (the module's "Which host")

[hardware]
# cpu_name = ""                # the names themes show (cpu.name, gpu.name, mem.name):
# gpu_name = ""                # found by Libre Panel itself; text here replaces what is
# memory_name = ""             # found, as you write it (empty: as found)
# [[hardware.fans]]            # a fan's own name, by the key of its speed (Settings ->
# sensor = "fan.lpc.fan_1"     # Sensors sets them); without one: FanControl's name for
# name = "Intake"              # it, else the sensor's ("Fan #1")

[weather]
enabled = false                # off unless you turn it on
provider = "open-meteo"
# latitude = 0.0               # your location; find it with:
# longitude = 0.0              #   libre-panel location "Your City" (or in the app)
# place = ""                   # what the app calls it; the coordinates are what counts
units = "metric"               # or "imperial"
update_minutes = 15

[video]
mode = "auto"                  # "auto": smooth video mode by itself where it is proven (the 9.2");
                               # "on": on every TURZX USB panel (needs ffmpeg); "off"
fps = 50                       # frames per second in video mode
local_clip = ""                # required for "auto" and "on", see below
clip_asked = false             # true once the app has asked for local_clip (on Start)
```

## Backup

*Settings → System → Backup* saves config.toml and your own themes in one file
(`libre-panel-backup-<date>.zip`), for another computer or to keep, and puts such a file back:
its config.toml takes the place of the one there (which is kept beside it as
`config.toml.before-restore`), and its themes are added, written over those of the same name.
Nothing is written from a file that is not a sound backup. config.toml holds the keys of
services (Home Assistant's) too: keep a backup to yourself. While Libre Panel runs with
administrator rights, a backup is not restored from the app.

## Light mode

```toml
light_mode = true              # Settings → Start and system → Light mode; off unless set
```

While the app's window is closed (or hidden) for a minute, Libre Panel lets go of what only
the app needs, such as the letters, symbols and pieces of every theme its pictures were drawn
with, and gives the memory back to the system; Windows also takes back the pages the panel
does not touch, which is what Task Manager shows. The panel draws on as before; the app takes a
moment longer to open again. The app's pictures of the themes themselves (and of the
community's) go a minute after its window was closed in any case, light mode or not: nothing
but the app shows them, and it draws them again when it opens.

## New versions

With `check_updates = true` Libre Panel asks GitHub once a day for its latest release
(`api.github.com/repos/syntensa/libre-panel/releases/latest`; a pre-release that runs asks for
the latest releases and hears of the pre-releases of its own minor version too, never of a
draft), and Start shows a newer one with what is new and the download that fits how it was
installed: the setup, the portable .zip, the AppImage or the Linux .tar.gz (installed with pip:
the command that brings it). *Update to …* there and in *Settings → About* fetches that
download, checks it and installs it on a second click ([Updating](BACKGROUND.md#updating)). The
request carries what any does (the computer's address and "libre-panel/<version>"); nothing of
you or the panel. The answer is kept in `updates.json` beside config.toml, with the version whose card
was closed (*Later*): that card does not come again, the next version's does. Without the
setting Start asks once; `false` never looks. *Settings → Start and system → New versions*
switches it and looks at once (*Look now*).

## Keyboard shortcuts

Key combinations for the whole PC, whatever window is in front: pause or resume the panel,
turn its brightness up or down a step (10 %), open Libre Panel, put the next scene on, start
or pause the [timer](#timer) and set it back, or mute the microphone and turn it on again.
Set them in *Settings → Start and system → Keyboard shortcuts* by pressing the keys, or in
config.toml:

```toml
[hotkeys]
# enabled = false             # all of them off at once; the keys below stay for later
pause = "Ctrl+Alt+P"
brightness_up = "Ctrl+Alt+Up"
brightness_down = "Ctrl+Alt+Down"
open = "Ctrl+Alt+L"
timer = "Ctrl+Alt+T"            # start or pause the timer service
timer_reset = "Ctrl+Alt+R"
mic_mute = "Ctrl+Alt+M"         # the default microphone (Windows; Linux with wpctl or pactl)
```

*Use the shortcuts* at the top of the card switches them all off and on (`enabled`): off,
Libre Panel holds no key, so every combination goes to the program in front again, and the
ones you set are kept for when you switch them back on.

A combination needs Ctrl, Alt or Win (Shift alone would take a letter from typing), except
the keys nothing types: F13 to F24, Pause and Scroll Lock. Windows registers them with the
system; on Linux the X server grabs them (X11 only: Wayland lets no program take keys for
itself). One that another program holds already is said so in Settings, not taken from it.
They work while Libre Panel runs in the background.

## Notices from other programs

With `allow_notify = true` (*Settings → Game mode and more → Notices from other programs*) a script, a
Stream Deck button or the command line shows a short notice on the panel:

```sh
libre-panel notify "Render finished" --title Blender --seconds 8
```

or as an address, for a Stream Deck's *Website* action (as a request in the background) or
anything that can open one:

```text
http://127.0.0.1:8765/api/notify?key=<the key>&text=Render%20finished&title=Blender
```

`text` (up to 160 characters) is needed; `title` (up to 40), `seconds` (1 to 60, 5 when not
given), `icon` (one of the icons themes use: `music`, `gamepad`, `temperature`, ...) and
`level` (`info`, `warning`, `error`) may come along, also posted as JSON with the key in the
`X-Libre-Panel-Notify` header. The key is a secret of its own in `notify.json` beside
config.toml, readable by you only; it lets a program show a notice and nothing else. *New key*
in Settings makes another, and the old one stops working. The app answers on this computer
only (port 8765, or the one `libre-panel` printed where that was taken), while it runs in the
background. These notices are of the kind `outside`, which a theme can switch off like any
other (`toast.off`).

## The theme on the panel

Without a `theme` in `config.toml` each panel shows its own default: **Lumen** on the
1920×480 bars (the 8.8" and 9.2"), **Libre Default** on every other panel. A bar is a panel
whose frame is at least 3.5 times as long as it is high; the 12.3" (1920×720) is not one.
`libre-panel config init` and the app's first write leave the line out, and a comment in
the file says so.

```toml
theme = "slate"                # any built-in theme, or a folder in the user themes directory
```

- **A theme you name always wins**, and so does `libre-panel run --theme NAME`. The
  `theme = "libre-default"` that a `config.toml` up to 0.4 carries is such a choice and
  stays as it is; take the line out to get the panel's default.
- **Which panel it is** is the one `device.model` names. With `model = "auto"` Libre Panel
  looks for a TURZX USB panel on the bus (by its ids, without opening it); a panel that is
  known only once it connects (a serial 8.8", or one plugged in later) is taken from the
  connection, and the theme then changes once, with the `transition` of `config.toml`.
  Unplugging the panel changes nothing.
- **In the app**, *Start* asks "How should it look?" on a first start (no `theme` in
  `config.toml` yet) with the panel's default first, marked *Default*. Choosing a theme
  there, in the tray's *Theme* menu or in Design writes `theme`.
- **A theme's switches** (*Look*, *This theme's switches*): Libre Panel's own screens have
  frames around their parts, on to begin with. Switched in Look, a switch holds for every
  screen that has it; a table of a theme's own goes before it, for all its versions:

  ```toml
  [switches]
  frames = false                 # every screen without its frames ...
  [switches.lumen]
  frames = true                  # ... but Lumen, on any panel size and on end
  ```

## A panel on end

`orientation = "portrait"` in `[device]` says the panel stands upright (a bar mounted on
its end), `"landscape"` that it lies on its side; empty, each theme shows as it is made. Each
theme is then shown in its version for the way the panel is mounted where it has one: a theme
named `lumen-vertical` is Lumen's version for a panel on end, so `theme = "lumen"` shows
Lumen Vertical on an upright bar, for the normal theme, the pages and the screens in turn
alike. The app asks on a first start and has the choice on *Start* and in *Settings →
Panel*; its lists show each theme once, by the name it is chosen by. `rotate = 180` turns
either way upside down.

## More panels at once

Up to three panels can run beside the one `[device]` names, each with a theme of its own:
a `[[panels]]` table for each, with the keys of `[device]` and a `theme`. *Settings → Panels*
adds and removes them: *Add a panel* asks, step by step, which panel it is (one plugged in
that shows nothing yet first), how it is mounted and what it shows, each step with a choice
made already. With more than one panel the app's header has *Panel 1 | Panel 2*: the panel
*Live* shows and *Design* edits, in its size and the way it is mounted, with its own pages (its
theme, and those for a game, music and an alarm); *To the panel* puts the theme on that panel.

**A companion beside the first panel.** Lumen, Arena, Encore and Almanac each have a companion
for a second panel (Lumen Companion, Arena Companion, Encore Companion, Almanac Companion), for
the 9.2" either way up and the 12.3". While a companion shows on a panel beside it, the first
panel shows its theme's pair version (in every size, on end too), which leaves out what the
companion shows: Lumen without its fans (the companion has them with every core, the other
temperatures, the drives and the processes), Arena with the game and the two chips large (the
companion has the temperatures, fans, cores, the ping and the processes), Encore without the
cover (the companion shows it large, with the weather and the sun and moon); Almanac has nothing
twice and stays as it is, beside the weather of the next hours and days, the sun and moon and
your appointments. No value shows on both: a companion's cores leave out the processor's load
(the first panel's ring shows it), and its weather card says that no weather is set up instead
of showing the date while the weather is off. A companion
follows the first panel's page: Arena's beside Arena while you play, Encore's while music
plays, Almanac's at rest, unless its `game_theme`, `media_theme` (or a page in Design) names a
theme of its own. A companion that is away a moment (a reconnect) keeps the pair for ten
seconds.

**Each panel at its own pace.** The app runs each panel beside the first in a process of its
own, on the first panel's readings: two panels in the smooth mode do not wait for each other,
and each draws about as fast as it does alone. What such a panel says goes to the app's log
(`panel 2: …`); one whose process cannot start runs inside the app as before. `libre-panel run`
keeps all panels in one process.

```toml
[[panels]]
theme = "lumen"                # empty or left out: the panel's own default
model = "turing-3.5"           # "auto": a panel the others do not use
port = "COM6"                  # serial panels: which of two of the same kind it is
# game_theme = "lumen-vertical"  # its theme while the first panel shows a game's page,
# media_theme = "encore"       # and music's ("" or left out: its own as ever; a companion
                               # shows the companion of the first panel's theme then)
# alarm_theme = "lumen-companion"  # while a sensor is at its crit limit ("": its own)
# same_look = true             # its theme in the look of the one the first panel shows
# video = { mode = "auto", local_clip = "..." }  # smooth motion on it too, its own clip
# follow_brightness = true     # its brightness as the first panel's, at night and while
# brightness_offset = 10       # you are away too, with this added (-50 to 50: some panels
                               # look brighter than others at the same setting); false:
brightness = 40                # its own brightness, night and away settings
# rotate = 180, orientation, motion, still, night, dark_on_quit, dark_on_sleep,
# dark_when_away: as in [device]
# on = false                   # kept, but not shown
```

*Which is which?* there shows each panel's number for a few seconds (the first is 1, those of
`[[panels]]` 2, 3 and 4), *Swap with panel 1* lets two panels' themes change places, and a
panel plugged in that none shows anything on is offered to be set up. *Live* shows the picture
of the panel chosen above it, the *Overview* in Settings says how each one is doing, and the
tray has a *More panels* menu: each panel's state, a switch and its theme. *Picture of all
panels* saves one picture of them where they stand, each with what it shows now, to keep or to
share.
In *Design*, a module's menu has *Copy to panel 2* and *Move to panel 2*: the module goes on
free cells of the theme that panel shows, and a theme that comes with Libre Panel gets a copy
of your own first (`lumen-companion-own`), which the panel shows from then on.

Above them the panels stand in their real sizes (from each model's diagonal and pixels, the
way it is mounted), with what each shows, beside a monitor to place them by (24″ to 34″, or
none; kept in the app, not in config.toml). Drag one, or move it with the arrow keys (Shift:
farther), to where it stands: in the case, under the monitor, beside it, on end. Each place is
written as `at = [x, y]`, millimetres from a point under the monitor's middle, in `[device]`
and in the panel's `[[panels]]` table; *Line them up again* takes them out.

*A picture across them…* puts one picture under the themes of all panels that are placed:
it covers them as one, its middle at theirs, and each shows the part where it stands, instead
of its theme's own background picture. It is kept in the settings folder as
`across/picture.jpg`, at most 4096 pixels on its long side and without what the file said
about itself (where and with what it was taken); *No picture* takes it away.

```toml
across = "across/picture.jpg"  # one picture across the panels that are placed; "" none
```

The sensors are read once, for all panels; each panel's graphs run at its own theme's pace.
Libre Panel never opens a panel twice: a panel that one of them has open is passed by when
another looks for its own, so two panels set to `"auto"` find one each. A panel of
`[[panels]]` that is not plugged in is waited for (its `driver` is `"turzx"` unless it says
otherwise), instead of drawing into a file meanwhile; with `driver = "virtual"` it draws into
`libre-panel-frame-2.png` (the third into `-3`, and so on) or its own `output`.

The pages (game, music, idle clock), the screens in turn, the notices and the tray's brightness
are the first panel's. Smooth motion is each panel's own: off on a panel beside the first
until it is switched on there (`video = { mode = "auto", local_clip = "..." }` in its table),
with the name of its own standby clip, asked for that panel and never taken from another, and
the first panel's rate and encoder settings; each panel in smooth motion is a video of its own,
which costs more of the computer. One given a theme
for a game or for music (`game_theme`, `media_theme`) shows it while the first panel shows
that page. While a sensor is at its critical limit (an [alarm](#notices) of the notices) a
panel shows its `alarm_theme`, Lumen Companion unless you set another (`""`: its own as ever):
the temperatures, the load and the programs that cause it. The panels keep the same beat:
a panel beside the first takes its readings a moment after the first panel's, so a value
glides on both at once, and the same fan stands the same on panels that draw at the same
rate. With `same_look` a panel's theme takes the colours of the theme the first panel
shows (its palette, by name), its background colour, its fonts and how it frames its modules
(`style`, and the module frames switched on or off for it); what is where and the pictures
stay its own, and a change saved in Design shows on both at once. A change in config.toml
reaches every panel, and a panel that is added, switched on or off, or taken out starts or
ends while the others go on.

## Transitions

How the panel goes from one screen to the next. *Settings → Panel → Transitions* sets a
default, a pace, and for each occasion its own; **▶** beside each plays it on the panels, out to
a card with its name and back. A calm panel (`device.motion`, a USB serial one) switches at once.

```toml
[transitions]
default = "fade"      # every switch that has none of its own
speed = "normal"      # "fast", "normal", "slow": shorter or longer, each of them
game = ""             # a game starts or ends ("": the game's entrance, with its logo)
media = "entrance"    # music starts or stops: the cover and the track; "Music ended"
idle = "fade"         # the PC is at rest (Almanac), or in use again
sequence = "light"    # the screens in turn
autopilot = ""        # the autopilot ("": the one its options name, the tube)
report = ""           # the round report ("": the one memory's options name, the tube)
manual = ""           # a theme or scene chosen ("": the default)
random = ["fade", "wipe", "zoom", "iris", "blinds", "dissolve", "light", "tiles"]
```

| Transition | What it does |
|---|---|
| `cut` | at once |
| `fade` | the new screen comes over the old one |
| `slide`, `slide-right`, `slide-up`, `slide-down` | the new screen pushes the old one out, to the left, right, up or down |
| `wipe` | a soft edge goes across |
| `zoom` | the old screen draws back into the dark, the new one comes forward |
| `iris` | a circle opens from the middle |
| `blinds` | upright slats turn one after another |
| `dissolve` | the new screen comes through in small squares |
| `tube` | a line ignites and opens into two edges |
| `entrance` | the screen closes to a line, a band shows a picture or a title with a word typed below, then the new screen opens: a game's logo, music's cover and track, the time when the PC rests, else the new screen's name |
| `light` | a line of light runs across, the new screen behind it |
| `glitch` | a short digital fault: rows slip, the colours split |
| `tiles` | the screen turns over tile by tile |
| `shatter` | the old screen breaks into pieces that fly apart |
| `random` | another of `random` each time |

A plugin's transition goes by its name (`myplugin.curtain`, [Plugins](PLUGINS.md)). The old
setting `transition = "fade"` at the top of `config.toml` still names the default.

## Scenes

A scene is a theme for every panel at once ("Gaming", "Work"), put on with a click on *Start*
(*Keep as a scene* keeps what the panels show now), from the tray, or with a keyboard
shortcut that puts the next one on (`next_scene` in `[hotkeys]`).

```toml
scene = "Gaming"               # the one that is on; left out: none
[[scenes]]
name = "Gaming"
themes = ["arena", "lumen"]    # the first panel's, then those of [[panels]]; "" leaves one as it is
```

A theme chosen for a panel by itself afterwards (on *Start*, in the tray, under *More
panels*) takes the scene off, and a scene stops the screens in turn as a chosen theme does.

Where the panels are placed (*More panels*), a scene that is put on or off goes across them
as they stand: the panel farthest to the left changes first, the one farthest to the right
0.8 seconds after it, each with its own transition.

## Pages of the panel

Besides the normal theme (or the screens in turn) the panel has pages it switches to when
something happens, each with a theme of its own. In the app: *Design → Pages*, a card a page
with a switch, its theme (the screens made for it first) and its frame rate.

| Page | When | Its screen | Switched on with |
|---|---|---|---|
| In game | while a game runs | the normal theme, or one you choose | the [game service](#game-mode) and its `automatic` |
| Music | while a track plays (and for 8 s of a pause, the gap between two tracks) | Encore | `[modes.media] on = true` |
| Idle | when nobody has pressed a key or moved the mouse for `minutes` (5) | Almanac | `[modes.idle] on = true` |
| Round report | after a game | `round-report` | the [memory service](#memory-and-round-report) and its `report` |

A game goes first, then the round report after it, then music, then the idle clock; the
normal theme (or the screens in turn) when none of them is due. Music and the idle clock are
off until they are switched on. On Windows the time without input is the system's own; on
Linux it is the time since the desktop called the session idle (logind's idle hint, which
GNOME, KDE and others set after their own delay), and without one the idle page never shows.

```toml
[modes.media]
on = true
theme = "encore"          # the default

[modes.idle]
on = true
minutes = 5
theme = "almanac-analog"  # Almanac is the default
fps = 25                  # a page may have a frame rate of its own, as a mode
```

## Screens in turn

Instead of one theme the panel can show several in turn: each for its seconds, minutes or
hours, then the next, and from the top again after the last. In the app: *Start → Screens in
turn* (a row a screen: the theme, how long, up, down, remove), and the switch on its card
turns them on and off.

```toml
[sequence]
on = true
[[sequence.steps]]
theme = "almanac"
minutes = 5
[[sequence.steps]]
theme = "nimbus"
minutes = 5
[[sequence.steps]]
theme = "studio"
minutes = 10
```

- **How long:** `seconds`, `minutes` and `hours`, added up (`minutes = 1` and `seconds =
  30` is a minute and a half), at least 5 seconds a step and at most a week. The app writes
  each in the largest unit that takes it whole.
- **What goes first:** a game's theme ([game mode](#game-mode)), an [autopilot](#autopilot)
  rule that holds and any service that shows a theme of its own; the screens in turn go on
  underneath and the panel comes back to the step that is due.
- **When it begins:** with the first step when the panel starts and whenever the steps
  change; anything else changed in `config.toml` leaves it where it is. Each change of screen
  plays the `transition` of `config.toml`, and the next theme is made ready while the one
  before still shows.
- **One theme chosen** on *Start*, in the tray or in Design stops the screens in turn
  (`on = false`); their steps are kept, and the switch on the card starts them again.
- **`theme`** stays what shows when they are off.

## The panel's start screen

A TURZX USB panel has two screens of its own, shown without any program on the PC: a
**boot logo** when it is switched on, and a **standby clip** it plays when the PC starts,
until Libre Panel shows its themes. *Settings → Panel → Start screen* has both in Libre
Panel's look, made for each USB panel's size (the 2.8" round, 4.6", 5.2", 8", 8.8", 9.2" and
12.3"): the mark, the name and the line of light, and in the clip a light that runs out from
the middle. The card shows them as they will look.

- **Put on the panel** writes one, after a question: the panel's themes pause, the panel
  shows what was written (the logo at once, the clip playing), and *Show the themes again*
  brings them back. The boot logo appears at the next power-on; the panel starts with the
  clip from then on.
- **Factory logo again** takes Libre Panel's boot logo away; the factory logo was never
  touched and shows again.
- **The standby clip replaces** the one the panel has (`usr/data/standby.h264`, the vendor
  app's name); that one cannot be read back first. Where `video.local_clip` is empty, the
  video mode is then told this name.
- **With more panels** the card is the one of the panel chosen at the top (*Panel 1 |
  Panel 2*): its own logo and clip, in its size and the way it is mounted, written to that
  panel alone, and a clip there is named in its own `[[panels]]` `video`, never in another's.
  Of two panels alike, the second is the second of that model on the USB bus, as its loop
  found it.
- **What is on the panel** lists its folder and the sizes of both files.
- **JPEG** and **H.264** download the files as the panel takes them.

It works only on a click, never while themes run, and writes only below `usr/data/`, at most
1 MiB a file, checking each file's size afterwards ([protocol](protocol/turzx-usb.md#the-panels-own-screens)).
It was measured on the 9.2"; the other USB panels share its firmware but have not been tried.
A panel kept on power while the PC is off shows neither again after a restart of the PC: it
keeps its last picture.

## Sensor sources and their order

`[sensors] providers` lists the sources in order of priority: when two of them
have a value for the same key, the earlier one wins. A quicker or better source
goes first.

A configuration that names no list (no `config.toml`, or none with `providers`)
starts with `["load", "audio", "linux", "psutil"]` on Linux and
`["load", "audio", "librehardwaremonitor", "presentmon", "psutil"]` on Windows (elsewhere, where the
volume cannot be read, `["load", "psutil"]`); `libre-panel config init` writes
that list into a new file. **A list you wrote is never changed.** The exceptions are
the `["psutil"]` that `config.toml` files up to 0.4 name alone, and on Linux the
`["load", "audio", "psutil"]` that builds of 0.5 wrote before it had a source of its
own: they were the default then and not a choice, and get today's list too. Put the
sources you want in front of psutil in any other list.

| Source | Gives | Where |
|---|---|---|
| `load` | `cpu.load`, each core's `cpu.core.<n>.load` and `gpu.load`, measured ten times a second | CPU everywhere, GPU on Windows (any vendor) and on Linux (AMD, NVIDIA) |
| `psutil` | CPU, memory, drives, network, battery, the busiest programs; temperatures and fans where the system has them (on Linux the `linux` source reads those instead) | everywhere |
| `linux` | temperatures and fans of the kernel's chips (processor, mainboard, drives, memory), the processor's power and the graphics card (`gpu.*`), without root; see [below](#linux-sensors-and-panel-access) | Linux (in its default list) |
| `librehardwaremonitor` | temperatures, power, clocks and fans of CPU, GPU and mainboard | Windows (in its default list); the Windows downloads carry what it needs, see [below](#windows-sensors-gpu-power-fans) |
| `audio` | `audio.volume` (0 while muted) and `audio.muted` (1 or 0) of the default output | Windows, Linux (`wpctl` or `pactl`); in the default list of both |

**Fast load** (`load`): `cpu.load`, each core's load and, on Windows,
`gpu.load` are measured in a background thread ten times a second and read on
every frame, so bars and graphs follow the load as it happens (a core's over
the last 0.3 s: a tenth of a second alone flickers). The other sources give it only once per
reading (`refresh_ms`) or once a second. It needs no other program and no
administrator rights.

- The processor load comes from `GetSystemTimes` on Windows and from psutil
  elsewhere.
- The GPU load is what Task Manager shows on Windows, read from the performance
  counters, for any vendor: the busiest 3D engine of the graphics card that
  has the most memory in use of its own (chosen again every minute). Where
  the counters are missing (some virtual machines), `gpu.load` is simply not
  there and the next source supplies it; the log says so. On Linux it is the
  counter of AMD's driver (`gpu_busy_percent`) or NVIDIA's library, for the card
  `gpu.name` names; Intel's drivers tell no load.
- It runs only while a theme on the panel shows `cpu.load` or `gpu.load`.
- It stands before the sources whose load it replaces, also in the default
  list. In a list of your own put it first, e.g.
  `providers = ["load", "librehardwaremonitor", "psutil"]`; behind
  LibreHardwareMonitor its readings would never be used.

**Volume** (`audio`): the volume and the mute of the default output device,
read on every frame, so a volume bar follows the volume keys at once. It is in
the default list on Windows and Linux; in a list of your own, add it:
`providers = ["load", "audio", "psutil"]`.

- `audio.volume` is 0 while the sound is muted; `audio.muted` is 1 then, else 0.
- Windows: Windows' own sound interface, no program needed. Windows reports
  every change by itself. When the default output device changes (a headset
  plugged in, speakers chosen) the volume follows it within two seconds.
- Linux: `wpctl` (PipeWire), else `pactl` (PulseAudio), asked four times a
  second. Without either tool installed there are no readings, and the log
  says so.
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
- Linux: any MPRIS player (Spotify, browsers, media players) through `playerctl`, asked
  once a second; where it is not installed, straight from the session's D-Bus with systemd's
  `busctl` (the player that plays, else one that is paused).

Two pictures go with it, for an image widget (`"src": "@media.cover"`):
`media.cover`, the album cover, and `media.app_icon`, the icon of the program
that plays. An image widget that shows one is all it takes for the source to read
it. The icon is found once per program, never sent anywhere, and the notices
service puts it on the cover of its "Now playing" message:

- Windows: the logo of a Store app (from its package), else the icon of the
  program that runs under the name Windows gives the player.
- Linux: the PNG that the player's desktop entry names; a player whose icon
  exists only as an SVG has none.

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
frames of the game you play. It listens to what Windows reports about the
frames it shows: nothing is put into the game, nothing hooks it, there is no
overlay. The Windows downloads carry its console version (MIT license) in their
`tools` folder ([below](#programs-that-come-with-the-downloads)), and Libre Panel uses
that one before any other. On Windows the source is on by default (it starts PresentMon
only while a theme shows `game.*`); a list of your own names it:

```toml
[sensors]
providers = ["load", "librehardwaremonitor", "presentmon", "psutil"]
```

PresentMon needs administrator rights or your account in the "Performance Log Users"
group. The Windows setup can add you to the group (sign out and in again afterwards).
Without the setup, with the zip or a pip install, do it once in a PowerShell opened as
administrator; the group is named by its well-known id, so this works in every language of
Windows:

```powershell
Add-LocalGroupMember -SID S-1-5-32-559 -Member "$env:USERDOMAIN\$env:USERNAME"
```

Uninstalling Libre Panel leaves your account in the group. With a pip install there is no
PresentMon: download its console version (`PresentMon-…-x64.exe`, MIT license) from the
[releases page](https://github.com/GameTechDev/PresentMon/releases) and name it, or put it
on the `PATH`:

```toml
[sensors.presentmon]
path = "C:/Tools/PresentMon-2.6.0-x64.exe"
```

Libre Panel starts this program, with the rights it has itself. If it runs as administrator,
keep PresentMon where only administrators can change it (a folder under *Program Files*, not
one you or another user may write to), and name it here in `config.toml`: the app does not
change `path` then. The one in the `tools` folder lies where Libre Panel does, and is as safe
as that installation is
([why](BACKGROUND.md#windows-starting-with-the-highest-rights)).

| Reading | |
|---|---|
| `game.fps` | frames per second over the last second |
| `game.frametime` | the mean frame time over the last second, in ms |
| `game.low` | the 1 % lows: the frame rate of the slowest hundredth of the last 30 s |
| `game.frametime_peak` | the longest frame time since the last reading, in ms. A stutter of 60 ms between two readings shows in the next one even though the readings are a second apart. It is there all the time a game runs (0 when no frame came), so a graph of it never stops |
| `game.limiter` | `cpu`, `gpu` or `cap`: who holds the frame rate down. It is judged from how long each side waits for the other, not from the load: the CPU if the GPU waits longer for it than it waits for the GPU, the GPU in the other case, and `cap` when neither waits much and both work much less than a frame takes (a frame rate limit, or vertical sync). Needs PresentMon 2 |
| `game.app` | the program |
| `game.fps_avg`, `game.fps_1low`, `game.fps_01low` | what every frame of the round has come to so far: the mean frame rate (all the frames over the time they took), and the 1 % and 0.1 % lows as the frame rate at the 99th and the 99.9th percentile of the frame times (the frame time that 99 % and 99.9 % of the frames do not exceed) |
| `game.frametime_avg`, `game.frametime_max` | the same round's mean frame time and its longest frame, in ms |

- **A round** is a program's frames since it began to draw: half a minute without a frame
  begins a new one. The game the `game` service found keeps its round as long as it is the
  game (paused in its menu or minimized, it is the same round), and ends it when the game is
  over. Every frame counts: each one's length is counted in steps of 0.01 ms (of 1 ms above
  100 ms), so a round of any length takes the same little memory, and its lows are exact to
  that step.
- **When it runs.** PresentMon runs while a theme on the panel shows `game.*`
  readings, or the `game` service is on (see [Game mode](#game-mode)), and
  ends a minute after neither is. The recording session has to exist
  *before* the game starts: the anti-cheat of some games refuses a new
  session while the game runs, but leaves one that is already running alone.
  That is why the service keeps it running, and why a theme that shows
  `game.*` only once the game runs may miss those games.
- **Which program counts.** With the `game` service, the program it found;
  otherwise the program with the most frames. The desktop compositor and known
  overlays never count.
- **If it cannot start.** Without administrator rights or the group it ends at
  once, and the log says so once; Libre Panel tries again with growing pauses
  (up to a minute). While a game with an anti-cheat runs, it tries every 15
  seconds, and the frame rates are there again when the game is over. PresentMon
  is stopped with Libre Panel, also if Libre Panel is killed.

On **Linux** the same readings come from [MangoHud](https://github.com/flightlessmango/MangoHud),
the overlay many games run with (`mangohud %command%` in Steam): Libre Panel reads the log
MangoHud writes while it logs, nothing else. MangoHud logs from the start with this in
`~/.config/MangoHud/MangoHud.conf` (Shift+F2 starts and stops it by hand):

```ini
output_folder=/home/you/mangologs
autostart_log=1
log_interval=100
```

Libre Panel looks in the folder MangoHud.conf names (else the home folder), or in the one
given in `[sensors.mangohud] folder = "..."`, for the newest log written in the last ten
seconds. The source needs no entry in `providers`: on Linux it is there by itself and reads
only while a theme shows `game.*`. `game.fps`, `game.frametime`, `game.low`,
`game.frametime_peak`, `game.app` (the program, from the log's name) and the round's figures
(`game.fps_1low` ...) mean what they mean above; the lows are those of MangoHud's samples (one
every `log_interval` ms). There is no `game.limiter`.

**Values from JSON** (`json.*`): what the address of a NAS, a router, a Proxmox host or a
second PC answers, or a file another program writes. Nothing to sign up for: the address is
asked as it is, every `update_s` seconds, in a thread of its own. With the source switched on,
*Settings → Sensors → Values from JSON* sets them up (a name, the address or file, the fields
and their units); headers stay as config.toml has them.

```toml
[sensors]
providers = ["load", "psutil", "json"]

[sensors.json]
update_s = 30

[[sensors.json.sources]]
name = "nas"
url = "http://192.168.1.10:61208/api/4/quicklook"
fields = { cpu = "cpu", temp = "sensors[0].value" }   # empty: every value it has
units = { cpu = "%", temp = "°C" }
# headers = { Authorization = "Bearer ..." }          # where the address wants one

[[sensors.json.sources]]
name = "room"
file = "C:/Data/room.json"
```

Each field is `json.<name>.<field>` (`json.nas.temp`), a number or a text (true and false as
1 and 0). A field's path goes into the answer by names and list places (`a.b[0].c`). Without
`fields` every value down to three levels is read, at most 100, named by its path
(`json.room.kitchen.temp`). Only `http://` and `https://` addresses; an answer is at most
1 MB. An address that does not answer is said once in the log, and its values stay for three
times `update_s`.

**The UPS** (`ups.*`), through [Network UPS Tools](https://networkupstools.org/): NUT's
server `upsd` says how the UPS stands to anyone on its port, on this PC, a NAS (Synology and
QNAP run one), a Raspberry Pi, or WinNUT on Windows. Nothing to sign up for:

```toml
[sensors]
providers = ["load", "psutil", "nut"]

[sensors.nut]
host = "127.0.0.1"     # where upsd runs; a NAS: its address (allow this PC in its settings)
port = 3493
ups = ""               # its name in ups.conf ("": the first upsd has)
update_s = 5
# username = "..."     # only where upsd wants one to read (upsd.users)
# password = "..."
```

The readings are `ups.charge` (%), `ups.runtime` (minutes), `ups.load` (%), `ups.power` (W,
as the UPS says, or its load of the nominal power), `ups.input` (V), `ups.on_battery` (1
while the mains is off) and `ups.status` ("On mains", "On battery", "Battery low"); the UPS
module shows them. A warning with `key = "ups.on_battery"` and `crit = 1` in the
[notices](#notices) turns the mains going off into an alarm on every panel.

Show these readings with the Dashboard module (`ha.sensor.living_room_temperature
= Living room`, one per line), a list, or any widget with a `sensor`.
`libre-panel sensors` lists what is there.

## Hardware names

`cpu.name`, `gpu.name` and `mem.name` are the names a theme can show for the
processor, the graphics card and the memory (the headings of SPUR Classic do).
Libre Panel finds them itself, once, when it starts: with no administrator
rights, no other program and nothing to install. The source is `hardware`, it is
always on and needs no place in `[sensors] providers`. It shortens the names for a
panel: "AMD Ryzen 5 7600 6-Core Processor" is "Ryzen 5 7600", "12th Gen
Intel(R) Core(TM) i7-12700K" is "Core i7-12700K", "NVIDIA GeForce RTX 4070" is
"GeForce RTX 4070". The maker, the marks (R) and (TM), "CPU", the clock, the
number of cores, the generation and "with Radeon Graphics" go.

| | Windows | Linux |
|---|---|---|
| `cpu.name` | the registry, where the processor gives its own name | `model name` in `/proc/cpuinfo`; on ARM the board or the chip it names ("Raspberry Pi 4 Model B") |
| `gpu.name` | the display adapters in the registry, the software and remote ones left out; the one with the most memory of its own: the card, not the chip in the processor | the cards in `/sys/class/drm`, named by the NVIDIA driver, by AMD's `amdgpu.ids` or by the system's `pci.ids`; a card of its own before the chip in the processor |
| `mem.name` | the memory modules in the firmware's SMBIOS table: "Kingston DDR5-5600 32 GB" | the same where `/sys/firmware/dmi/entries` may be read (usually only by root); else the size |

Where the modules cannot be read, `mem.name` is the size, "32 GB" (rounded up to a
size sold, so 15.4 GB that the system leaves visible is "16 GB"). A name that
cannot be found is no reading: the other sources' names (LibreHardwareMonitor
has `cpu.name` and `gpu.name`, tidied the same way) fill in, else a theme shows
its fallback. With several graphics cards, `gpu` under
`[sensors.librehardwaremonitor]` picks the card by its name here too.

The same table gives `mem.speed`, the speed the memory runs at in MT/s (5600 for
DDR5-5600; the slowest module's where they differ). Where the modules cannot be
read it is no reading, and a theme shows "--". The memory's temperature,
`mem.temp`, comes from LibreHardwareMonitor and needs what the processor's does
(see [Windows sensors](#windows-sensors-gpu-power-fans)).

**Your own names.** Text under `[hardware]` is the name, as you write it:

```toml
[hardware]
cpu_name = "Ryzen 5 7600"
memory_name = "Kingston Fury Beast DDR5-5600 2 x 16 GB"
gpu_name = ""                  # empty: the one found
```

A name is one line of at most 100 characters. It wins over every other source
(but for the `demo` source, whose made-up names stay when it is in your list).
A changed `config.toml` is picked up while Libre Panel runs. The editor reads
and writes the names through `GET /api/hardware`, which answers
`{"cpu": {"detected": "Ryzen 5 7600", "custom": ""}, "gpu": {...},
"memory": {...}}`, and `POST /api/hardware` with any of `{"cpu": "text", "gpu":
"", "memory": "text"}` (an empty text gives the found name back). `POST`
answers like `GET`, refuses anything else with 400 and, like every request, needs the
access token in the header `X-Libre-Panel` ([Running in the
background](BACKGROUND.md#who-can-talk-to-the-app)). `GET /api/hardware?refresh=1` asks the computer again
(a card was changed while Libre Panel ran); Libre Panel asks once per start otherwise.

**The fans' names.** A mainboard wires its fan headers its own way, so a theme does
not name them itself: it shows each fan's name (`{label}`), and the name comes from
here. A name of your own (*Settings → Sensors → Fan names*, which shows each fan's
speed now, so one can tell them apart) is kept by the key of the fan's speed:

```toml
[[hardware.fans]]
sensor = "fan.lpc.fan_1"
name = "Intake"
```

Without one a fan goes by the name FanControl gives it (Windows; Libre Panel reads its
`userConfig.json`, where the cooling analysis finds it, or at `fancontrol` under
`[services.cooling]`), else by the sensor's own ("Fan #1"). `GET /api/hardware` lists
the fans as `"fans": [{"key", "detected", "custom", "value"}]` (`value`: the speed now,
or `null`), and `POST /api/hardware` takes `{"fans": {"fan.lpc.fan_1": "Intake"}}` (an
empty text gives the detected name back).

**How the fans turn.** Where a theme leaves a fan's look to you (`"look": "auto"`, as the
built-in themes do), the rotor is the one you choose in *Settings → Sensors → How the fans
turn*, for all fans or for one: `noctua-g2` (Noctua's NF-A12x25 / NF-A14x25 G2, the
default), `noctua-a12x25` (the first NF-A12x25), `noctua-a14` (the first 140 mm, NF-A14) or
`libre` (Libre Panel's own), in the theme's colours or the look's own (`own`: Noctua's
brown and beige):

```toml
[hardware]
fan_look = "noctua-a12x25"
fan_paint = "own"

[[hardware.fans]]
sensor = "fan.lpc.fan_2"
look = "noctua-a14"   # this one is a 140 mm fan
```

`GET /api/hardware` says them as `"fan_look": {"look", "paint", "looks"}` and each fan's
`look` and `paint` ("": as all fans); `POST /api/hardware` takes `fan_look`, `fan_paint`,
`fan_looks` and `fan_paints` (the last two by sensor), and `GET /api/fan-look?look=libre&paint=own`
is a picture of a look.

**Where it can be wrong.** Windows can keep the entry of a graphics card that was
taken out (Device Manager shows it under *Show hidden devices*); if that card had
more memory, it may be the one named. Some firmware tells no speed or no maker for
its memory, and then the name has none. Set the name yourself in these cases.

**The names are yours.** They stay on your computer: Libre Panel does not write
them to the log, and the report of `libre-panel doctor` has none. `libre-panel
sensors` lists every reading, these three too; leave them out when you paste its
output into an issue.

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
and which kinds it shows (`"toast": {"off": ["music"]}`). A screen that shows
the volume or what plays itself (one that reads `audio.volume` or `media.title`)
leaves that band out by itself: it would only cover them.

| Kind | When | The message |
|---|---|---|
| `volume` | the volume has moved by at least 1 since the last message, or is muted or unmuted; the value the service starts with is no change | the level with a bar, or "Muted" |
| `music` | a new track starts playing; not a pause, a resume, or the track that was playing at the start | title and artist, with the cover and the icon of the player when a source brings them |
| `warning` | a sensor reaches a limit you set | "CPU temperature 92 °C", in the theme's warning colour at `warn` and its critical one at `crit` |
| `microphone` | the default microphone is muted, or turned on again; the state the service starts with is no change | "Microphone muted", "Microphone on" |
| `appointment` | an appointment of the [calendars](#more-sensor-sources) begins within `appointments` minutes (once each; not a day-long one) | "In 10 min: Dentist", with its time |
| `panel` | the panel is back from a restart that cleared a hung video decoder (not from a replug) | "Panel restarted 21:05" |

```toml
[services.notices]
volume = true          # the volume; needs "audio" in [sensors] providers (the default has it)
volume_look = ""       # how the volume shows on every screen: "bar", "levels", "ring", "big",
                       # or "" for the theme's notice style
music = true           # a new track
microphone = true      # muted or on again; needs "audio" in [sensors] providers
appointments = 10      # minutes before an appointment begins (0: none); needs "calendar"
restarted = true       # "Panel restarted"
quiet_s = 120          # warnings: the same sensor is not told again within this time
alarm = true           # a crit limit reached: a red edge on every panel, and the panels
                       # beside the first show their alarm_theme

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
- A sensor at its `crit` limit is also an **alarm**: every panel's edge pulses red for three
  seconds (it stays still on a calm panel), and the panels of `[[panels]]` show their
  `alarm_theme` until the value has been below the limit for ten seconds. Themes can read it
  too: `alarm` is 1 while it lasts and 0 else, `alarm.text` says which sensor ("CPU
  temperature 93 °C"), for a widget with `needs = "alarm=1"`. `alarm = false` leaves it out.
- Notices look at every new reading, that is every `refresh_ms` of the theme.
  The volume is looked at on every frame it moves in, too, so its message comes
  within a frame or two of the volume key, whatever `refresh_ms` is.
- While the service runs, the sources it needs run too, although no theme shows
  them: what is playing (`media.*`), the volume (`audio.*`) and the warned
  sensors. They cost what they cost otherwise (a player is asked once a
  second); switch off `volume` or `music` to save that.
- `volume_look` draws the volume in the same look on every screen, whatever the theme's
  notice style (the other notices keep it): `bar`, a slim bar along the bottom edge; `levels`,
  bars that rise as on a mixer; `ring`, a ring that fills with the number in it; `big`, a
  large number in the middle. The app sets it in *Look → Notices → Volume* and on *Start*
  (*Show the volume*), where each is tried on the picture first.
- Texts follow the language setting. Each notice has its own `key`, so the
  same one again (the volume while it turns) changes in place instead of
  coming again.

### Timer

Minutes counted down on the panel, with a notice when they are up. With `break_minutes`
focus and break take turns: when the focus time is up the break begins by itself, and after
the break the next focus waits to be started. A [keyboard shortcut](#keyboard-shortcuts)
starts and pauses it (`timer`) and sets it back (`timer_reset`); so do the buttons on its
card in *Settings → Game mode and more*. The Timer module shows it.

```toml
[services]
enabled = ["notices", "timer"]

[services.timer]
minutes = 25
break_minutes = 5      # 0: no break, a plain timer
notice = true          # "Time is up", "Time for a break", "The break is over"
```

| Reading | |
|---|---|
| `timer.left`, `timer.text` | the time left, in seconds and as `24:13` |
| `timer.running` | 1 while it runs |
| `timer.share` | how much of the time has passed, 0 to 1 (the module's ring) |
| `timer.phase` | "Timer", or "Focus" and "Break" |
| `timer.rounds` | the focus times finished since the service started |

### Power use

The watts the PC draws, and the energy of today and of this month with what it costs. The
watts are those of the sensors (`cpu.power` and `gpu.power`: LibreHardwareMonitor on
Windows, the `linux` source) and `rest_w` for the rest of the PC: the mainboard, drives,
fans and what the power supply loses, some 40 to 80 W. It is an estimate; only a meter at
the socket knows. The Power use module shows it.

```toml
[services.power]
sensors = ["cpu.power", "gpu.power"]
rest_w = 50
price = 0.30           # per kWh; 0: no cost
currency = "€"
```

`power.watts`, `power.today` and `power.month` (kWh), with a price `power.today.cost` and
`power.month.cost`. The totals are kept in the service's folder (`plugins-data/power/`) every
minute and when it stops, so a restart loses nothing; a new day and a new month begin at
zero, and time the PC sleeps is not counted.

### Waveshare USB monitor (experimental)

Sends the readings to a Waveshare USB monitor, which shows them in the themes on its own card
(docs/HARDWARE.md). The display's themes name their values with the keys of Waveshare's
program: `Cpu_Temp` and `Cpu温度` both get `cpu.temp`, `Cpu_Freq` the processor's load (so
Waveshare named it), `Gpu_Usedata` the graphics memory in MB, `Download_Speed` and
`Upload_Speed` KB/s, `Cpu_Fan` and `Pump_Fan` the first fan whose name says `cpu` or
`pump`, `Partition_Usage_0` to `_7` the drives `disk.1` to `disk.8`, `time`, `date` and
`week` the clock; a value Libre Panel does not have is not sent.

```toml
[services.waveshare]
port = ""              # "": the display is looked for; else "COM5", "/dev/ttyACM0"
brightness = 0         # 1-100 sets it; 0 leaves it as it is
flip = ""              # "": as it is; "normal" or "upside-down"
units = false          # the values with their units ("75%", "2934RPM")
theme = ""             # "": the theme the display shows; else one of its card's themes
rotate = []            # themes of the card shown in turn, at random
rotate_min = 0.0       # minutes for each of them; 0: no turns
every_s = 1.0
```

The brightness in percent goes to the display on its own scale of 0 to 255, as Waveshare's
program sends it, so 100 is as bright as there. The weekday goes short (`Mon`, in the app's
language), with `year`, `month` and `day` beside `date`; `units` adds the units as Waveshare's
program writes them, for themes made for that.

`theme` and `rotate` take the themes on the display's card, by their paths there
(`/data/theme/480x480/021-astronaut.Theme`); the service's options list them by name once the
display answered. With `rotate` and `rotate_min`, a theme of the list at random takes the place
of the one shown every `rotate_min` minutes, never the same twice in a row. Each theme brings
its own orientation, which the display takes on as it loads it: `flip` keeps the picture as
set through every switch.

Nothing is written on the display's card: Libre Panel has none of its commands that write
or delete there.

### AI activity

What Claude Code and Codex do on this PC, and Claude Code in the cloud: working, waiting for
you (a question, a permission), idle or not running; the tool at work, the model, and how much
of the plan's limits of five hours and of the week is used. The AI activity module shows it,
with a sign of its own for each state (no maker's logo); where one of them does not run, the
other alone. Lumen's card slot brings it in by itself while one works or waits for you, and two
themes are made for it, each for the 8.8" and 9.2" bars and the 12.3" panel, under *All
themes*: Agents (Claude Code and Codex side by side) and Agents Claude (Claude Code alone,
with how full its context is).

Switch it on in *Settings → Game mode and more → AI activity*, then *Set up* each program there. The app
shows what it adds before it writes it:

- **Claude Code**: hooks in `~/.claude/settings.json` (on Windows in
  `%USERPROFILE%\.claude\settings.json`) for when a session starts and ends, a prompt, a
  tool, a permission, a notice, compacting and the end of a turn; and if you like, and it has
  none of its own, its status line, which brings the plan's usage while a session runs.
- **Codex**: the same hooks in `~/.codex/hooks.json` (or in `CODEX_HOME`). Codex asks you
  once to trust them.

Each hook is a `curl` (`curl.exe` on Windows) that hands the event to Libre Panel on
`127.0.0.1`, with a key of its own kept in `ai.json` beside config.toml. It runs beside the
program and does nothing when Libre Panel is not running. A copy of the file as it was is kept
beside it (`settings.json.libre-panel.bak`, once: the first copy is yours); *Remove* takes out
only Libre Panel's entries, those that call `/api/ai/`. The card says when each program last
reported. These hooks bring the sessions on this PC (in a terminal, an editor or the desktop
app); Claude Code's sessions in the cloud come by a relay, below. Where Libre Panel had to take
another port (its own was taken when it started), the card says so and sets the hooks up again
with a click.

```toml
[services]
enabled = ["agents"]

[services.agents]
claude_usage = true   # ask Claude Code for the plan's usage every three minutes
codex_usage = true    # ask Codex's app-server every minute
task = true           # show what the session is called (ai.<agent>.task)
cloud = ""            # the relay of the cloud sessions (set up in Settings)
cloud_token = ""      # the relay's access token, for a topic not open to everyone
```

While a theme shows `ai.*`, the service asks the programs themselves for the plan's usage, also
without a terminal open: Claude Code (2.1.293 and later) answers a usage request that sends
no message to the model and keeps no session; Codex's `app-server` tells its limits and the
tokens of the day. One program at a time, and after a failure only every 15 minutes. Libre
Panel sends nothing off this PC; the programs ask their own servers with your sign-in.

The readings, for `claude` and `codex`: `ai.<agent>.state` (`working`, `waiting`, `idle`,
`off`), `.busy` (2 waiting for you, 1 working, 0 idle, -1 off: for colours and conditions),
`.tool`, `.task` (the session's name, where it carries one, never the prompt; Claude Code may
name a session after its first prompt, so with `task = false` it reads nothing), `.cloud` (1:
the session that counts runs in the cloud), `.since` (seconds in the state), `.sessions`,
`.model`, `.context` (% of the context window), `.cost` ($ of the session, Claude Code's estimate), `.five_hour` and `.week` (% of the
plan's limits used), each with `_reset` (seconds until the window begins anew; show it with
`{value:duration}`) and `_age` (seconds since it was read), and Codex's `.tokens_today` and
`.tokens_week`. A widget can show a part only in a state:
`"needs": "ai.claude.state=waiting"`.

#### Claude Code in the cloud

Claude Code's cloud sessions (claude.ai/code, `claude --cloud`) run on a machine of
Anthropic's that cannot reach this PC. They come by a relay, an [ntfy](https://ntfy.sh) topic:
*Claude Code in the cloud → Set up* on the same card makes a long random address on ntfy.sh
and shows a setup script for the cloud environment, with what to do on claude.ai/code:

1. Open the settings of the environment your sessions run in.
2. Network access: *Custom*, with the default list kept and `ntfy.sh` added.
3. Setup script: add the script (*Copy the script*).
4. Start a session in it: it reports from then on, and the card says when it last did.

The script writes a hook into the session's own Claude Code settings in the cloud
(`~/.claude/libre-panel-cloud.py`, called on each event; your own hooks stay). The hook works
out the session's state as Libre Panel does and posts it to the relay only when it changes,
and every five minutes while it works: the event's name, a short name for the session (a hash
of its id) and what the session is called where `task` is on. Never a prompt, a tool's input
or a file. Libre Panel listens to the topic and shows such a session as Claude Code's, with
*In the cloud* where the tool or the model stands (`ai.claude.cloud`). It takes from the
topic nothing but such states: whoever knows the address could read along and post, so it is
long and random, and *Remove* stops listening (take the script out of the environment too).

ntfy.sh passes some 250 messages a day for each sender, which is a few a prompt. For more, or
for a relay of your own, put the address of a topic on your own ntfy server in `cloud` (the
host has to be among the environment's allowed domains) and, for a topic that is not open to
everyone, its token in `cloud_token`; the hook in the cloud sends with the token in
`LIBRE_PANEL_RELAY_TOKEN` (an environment variable of the environment), or the environment
adds it itself as a network secret for that host.

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
launchers = true       # and the games of Epic, GOG, Ubisoft Connect, EA app, Battle.net,
                       # Rockstar, Amazon, the Xbox app, Heroic and Lutris
# folders = ["D:\\Games", "C:\\Portable\\Doom"]  # games from no launcher
# ignore = ["Wallpaper Engine"]  # games and tools in a library or folder that never count
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
  Linux (also Flatpak and Snap). Tools in a library, such as Proton,
  the Steam Linux Runtime or SteamVR, are not games; `ignore` adds more.
  Windows programs under Wine and Proton are found by the folder they work in.
- **Other launchers.** With `launchers`, any program of a game that another
  launcher installed counts the same way, read from the launcher's own list
  (only read, every two minutes): Epic's manifests in ProgramData; the list of
  installed programs for GOG (without DLC), the EA app (without the app and its
  anti-cheat), Battle.net and the Rockstar Games Launcher; Ubisoft Connect's key
  in the registry; Amazon Games' database; the folder `XboxGames` the Xbox app
  installs to on a drive; Heroic's lists of its Epic, GOG and Amazon games
  (Linux, also Flatpak, and Windows) and Lutris's database (Linux, also
  Flatpak; its Steam games are Steam's). A game is named as its launcher names
  it. The formats are those Playnite's library extensions, Heroic and Lutris
  read.
- **Game folders.** For games from no launcher (portable ones, a store without
  a launcher), `folders` names their folders: a folder with programs in it
  (`.exe`, `.x86_64`, `.sh`, …) is a game of its own, named after its folder;
  any other holds a game in each of its folders (`D:\Games\Celeste`,
  `D:\Games\Hollow Knight`). A single program is a game through `programs`.
  A folder that would make half the computer a game (a drive, Program Files,
  your home) is never taken.
- **The window in front.** On Windows a game of Steam, another launcher or a
  game folder counts only once its window has been in front, because a library
  also holds programs that run all day (a wallpaper, a launcher's helper). Where
  Libre Panel cannot tell which window is in front (Linux), a running program of
  such a game is enough.
- **The beginning.** The service shows the name (`game.name`) and the logo
  (`game.logo`, an image: `"src": "@game.logo"` in an image widget) to the
  screens, then switches. The logo comes from Steam's picture cache, cut out
  and not scaled, or the game's header picture; a program that is not a Steam
  game shows its name in big letters. The switch waits for the next reading
  (at most a second) so that the first game screen already has the logo.
- **The end.** When the program has been gone for `linger_s` seconds the mode
  ends with the entrance for the end. Games restart while they are set up and
  launchers swap their process, so the mode does not follow every flicker. The
  name goes, the logo stays until the next game (or until the service stops): the
  round report that follows shows it beside the name.
- **Without the entrance** (`entrance = false`) the switch uses the
  `transition` of `config.toml`, and a message says "Game mode" (and "Game
  ended"), so the theme's toast style draws it.
- `force = true` keeps the mode on, which is how to try the game screens;
  `automatic = false` with `force = false` does nothing. A game that runs
  while `force` is on is named.
- The frame rate of the game (`game.fps` and the other `game.*` readings) comes
  from the `presentmon` source, which is a separate setting
  ([above](#more-sensor-sources)). While this service is on it keeps PresentMon
  running, ready before the game starts, and tells it which program is the game.

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
- **In the app.** *Settings → Game mode and more*, *Edit* under the autopilot, opens the
  page that edits these rules and the settings above, switches the service on
  and off, and shows which rule holds and what the
  autopilot sees (music, microphone, the program in front, the loads). Saving
  writes the `[[services.autopilot.rules]]` blocks and the keys that changed into
  `config.toml`; the rest of the file stays as it is, but comments inside the
  rules do not survive a save.

### Devices

Short messages when a drive, a USB device or a game controller comes or goes,
when a controller's battery runs low, when a drive is nearly full and when an
SSD runs hot. Windows only: elsewhere the service starts, says so in the log
and does nothing.

```toml
[services]
enabled = ["devices"]

[services.devices]
drives = true          # removable drives, and drives on a USB, SD or MMC bus
usb = true             # USB devices that plug in directly: phones, adapters, ...
controllers = true     # game controllers (Xbox and compatible), with their battery
storage = true         # free space and SSD temperature of the fixed drives, every minute
# usb_ignore = ["1a86:7523"]  # USB devices to say nothing about, as VID:PID
min_free_gb = 25       # "nearly full" below this much free space, or below...
min_free_percent = 5   # ...this share of the drive, whichever is more
# ssd_warn_c = 70      # "hot" from this temperature (default: the drive's own limit, else 70)
```

| What | When | The message |
|---|---|---|
| a drive | a removable drive, or a drive on a USB, SD or MMC bus, comes or goes | "USB Flash Drive · 20 GB free of 30 GB", titled "USB drive E:", with a bar for how full it is |
| a controller | it connects, disconnects, or its battery runs low | "Connected · Battery full", "Disconnected", or "Battery low" (a warning), titled "Controller · player 1" |
| a USB device | one that is no hub, no storage and no Xbox controller comes or goes | its name; a phone shows a phone and its maker |
| a full drive | free space falls below the limit | "Only 20 GB free" with a bar; an error when less than a third of the limit is left |
| a hot SSD | a fixed drive reaches its temperature limit | "88 °C · limit 70 °C" with a bar; an error from the drive's critical temperature |

Drives, controllers and USB devices are messages of the kind `device`
(`"toast": {"off": ["device"]}` switches them off in a theme), and each has a
drawn icon (`stick`, `gamepad`, `phone`, `usb`). Full drives and hot SSDs are
warnings like those of the notices service (kind `warning`).

- **Only changes.** What is plugged in when the service starts is no news, so a
  controller that has been connected for hours does not pop up at every start.
  Four changes at once (the computer woke up, a hub reset) are taken in
  silently too. A controller is told once it has stood there a moment, and one
  that comes and goes at once is not told at all.
- **One message for a stick.** A USB stick is a drive and a USB device; it is
  told once, as a drive. Hubs, mass storage and Xbox controllers are not told as
  USB devices, and neither is any panel Libre Panel drives: its own USB device
  never makes a message (`usb_ignore` adds more).
- **When it looks.** Windows reports every device that comes or goes and the
  service looks at once; a look every ten seconds catches what was missed, and
  where Windows does not report (before Windows 8) it looks every two seconds.
  Controllers' batteries and the storage (once a minute) are looked at on a
  clock. The first look at the storage can already find a full drive; that is
  told once.
- **Told once.** A full drive is told again only after its free space has risen
  more than 20 % above the limit; a hot SSD only after it has been 5 °C below
  its limit, or when it gets worse (from "hot" to critical). A battery is told
  when it reaches "low" and not again until it was "medium" or "full" in between.
- **It only looks.** A drive is asked with no access rights at all, so nothing is
  read off the disk; USB devices are read from the system's own list and never
  opened; nothing is written, and nothing is sent anywhere.

### Downloads

Notes what is downloading: a game or an update that Steam loads, and a file
that a browser saves in your Downloads folder. It publishes the download that
matters most, with its progress, speed and the time left, for a screen that
draws a download card ([Plugins](PLUGINS.md#the-downloads-state)); a theme's
`downloads` widget and its `download` and `netcard` modules are such cards
([Themes](THEMES.md#downloads)). The service shows nothing by itself. Nothing is written and nothing is sent.

```toml
[services]
enabled = ["downloads"]

[services.downloads]
min_mb = 50          # smaller downloads are not shown
steam = true         # Steam's games and updates
browser = true       # the part files of browsers in the Downloads folder
# steam_path = ""    # Steam's folder, if Libre Panel does not find it
# folder = ""        # the Downloads folder, if it is not the system's
```

- **Steam** is read from its own files, only read: `logs/content_log.txt` (what
  it does with an app: preparing, downloading, installing, and how big it is)
  and the app's `appmanifest_<id>.acf` in the libraries (its name and how much
  is loaded). Steam gives a speed in its log only once a minute, so the speed
  is the network's download (`net.down`, from the `psutil` source; it is all
  that the PC loads, and without it a Steam download has an amount but no
  speed); where the manifest stands
  still although data flows, the amount loaded is worked out from the speed and
  the state says so (`estimated`). Steam is found as the [`game`](#game-mode)
  service finds it, and `steam_path` works as there.
- **Browsers**: the part files `.crdownload` (Chrome, Edge, Brave and the other
  Chromium browsers), `.part` (Firefox) and `.partial` in the Downloads folder:
  on Windows the folder the shell names (it may have been moved), on Linux what
  `xdg-user-dir DOWNLOAD` says, otherwise `Downloads` in your home folder. How a
  file grows gives the amount and the speed. How big the whole will be is
  written only in the browser's history, and **the browser's history is never
  read**, as it holds everything you looked at: a browser download has no time
  left. Libre Panel lists the folder and asks for the size of the part files;
  no file in it is opened.
- **What counts.** Nothing below `min_mb` megabytes; a download of unknown size
  counts once it has run for 5 s at more than 1 MB/s. When it is finished it
  stays for a few seconds as "done", then the panel goes back to what it showed.
- **Data.** The name of the game or of the file is in the state, so that a card
  can show it; the state lives in memory and on the panel only. Nothing is
  written to a file or to the log, and nothing leaves the PC.
- The options are read when the service starts: change `config.toml` and it
  restarts by itself.

### Memory and round report

Keeps what happens at the desk over weeks and months, in a database on this
computer, and shows a round report when a game ends: the round against your
last rounds of the same program. Nothing is sent anywhere.

*Live → History* in the app shows what it kept: the temperature, the load and the power of
the CPU and the GPU over 24 hours, 7 days or 30 days (the time in games behind
them, gaps where the PC was off), what that came to, the games played most and the
last rounds. *Keep as a picture* beside a round with frame rates makes its round report
again, as the panel shows it (the report page's theme, in its version for the panel), in a
picture of the panel, saves it in your Pictures folder (in `Libre Panel` there; where the
system names none, `Pictures` or `Documents` in your home folder) and opens that folder. Where
the memory is off, *History* says so and switches it on.

```toml
[services]
enabled = ["game", "memory"]

[services.memory]
keep_days = 730        # minutes and games are kept this long (0: for ever)
cooling = true         # also the cooling state and FanControl's settings
cooling_days = 180     # the cooling state is kept this long (0: for ever)
music = true           # how long music plays, never what; asks the media source
# temperatures = ["temp.board.system", "temp.nvme.*"]   # more readings for the cooling state
report = true          # a round report when a game ends
report_minutes = 3     # only for rounds at least this long, and with frame rates
report_compare = 10    # against the last this many rounds of the same program
report_seconds = 30    # how long it stays on the panel
report_theme = "round-report"   # "": no theme, only the data for a screen of a plugin
report_transition = "tube"      # "" is the transition of config.toml
fps_levels = [60, 90, 120, 144, 165]   # the frame-rate ranges of the report's chart
# fancontrol = ""      # FanControl's userConfig.json, if Libre Panel does not find it
# database = ""        # where the file lies (default: the service's folder)
```

| Table | What it holds |
|---|---|
| `minutes` | a row a minute: the means, and the highest values of temperature and power, of the load, clocks, memory, network and volume; the frame rate in a game; how long music played; the outside temperature (with the weather on); each fan's speed |
| `sessions` | a row per game, written every minute while it is played and again when it ends: the game's name and its program, how long, the frame rates (mean, the 1 % quantile, the 1 % low), what every frame came to (the mean rate, the 1 % and 0.1 % lows, the mean and the longest frame time), how long at which frame rate, how long the CPU, the GPU or a frame limit held it back, the highest temperatures, power (the PC's too, with the [power service](#power-use)) and video memory, and the course of the CPU's and the GPU's temperature (60 to 120 points, each the mean of as many seconds, however long the round). A file of an older version gets the new columns when the service opens it; its rounds have none of those numbers |
| `cooling` | the cooling state whenever it moves: temperatures, power and load, each fan's speed and duty, and what each FanControl curve reads. At least 12 s apart (5 s in a game) and at least one a minute, so that a longer gap means the service did not run |
| `fancontrol` | every change to FanControl's curves and fans, with the time (FanControl's file is only read, never written) |
| `meta` | the names of the CPU, the GPU and the temperature sensors as the readings give them ("GPU Core"), noted with `cooling` on, so that the [cooling analysis](#cooling-analysis) can speak of them by name |

- **Where, and what stays out.** The file is `memory.db` in
  `plugins-data/memory/` of the settings folder; it is a plain SQLite file that
  you can open, copy or delete (stop the service first to start afresh). No
  title of a track, no window title and no file name is read, so none can be
  kept: music is only how long it played, and of a game only its name and its
  program's file name (`spacegame`). It takes about 0.3 MB a day for the minutes
  and half a megabyte to a megabyte for the cooling state; the defaults keep up
  to a few hundred MB.
- **Names of readings.** Everything comes from the readings of the panel
  (`cpu.temp`, `gpu.power`, `fan.*`, `game.fps` ...), whichever source
  supplies them; what a PC does not have is left empty. The duty of a fan
  (how hard FanControl drives it) and the temperature a curve reads come from
  LibreHardwareMonitor's outputs and FanControl's file, so Windows. On Linux the
  fan speeds are recorded, no duty.
- **Games** are those the [`game` service](#game-mode) finds (`game.name`);
  without it only minutes and the cooling state are kept. Frame rates need the
  `presentmon` source ([above](#more-sensor-sources)): a game without them is
  kept, but has no report. A game ends when its name goes, so a game that
  restarts within the service's `linger_s` is one round.
- **While a game is played** the service publishes what the round has come to so
  far, once a second: `session.time`, `session.fps`, the share of the time at each
  frame rate (`session.level.<n>`) and the highest values since it began
  (`session.cpu_temp_max` ...; the list is in [Plugins](PLUGINS.md#the-round-under-way)).
  The built-in theme `spur-session` shows them, in a game screen.
- **The report.** When a round that lasted `report_minutes` and had frame rates
  ends, the report is built from the file: this round against the mean of the
  last `report_compare` rounds of the same program that were long enough too
  (compared by the game's name where the program is not known). It shows the
  theme for `report_seconds`, ahead of an autopilot's choice and of the music
  and idle pages; a new game ends it at once, and a round that ends because Libre
  Panel quits has none. `round-report` is Libre Panel's report for 8.8" and 9.2" bars
  (with versions for the 12.3", the 5.2" and a bar on end), in four cards: the round with
  what all its frames came to (the 1 % and 0.1 % lows, the mean and the longest frame
  time), the share of each frame rate and who held it down, the CPU's and the GPU's
  temperature over the round, and the highest values with the PC's power where the
  [power service](#power-use) runs; the game's logo (the one the [`game` service](#game-mode)
  keeps, `@game.logo`) is beside its name. A round recorded before these were counted shows
  the means of the seconds' lows instead, and a dash for the temperature. `spur-round-report`
  is SPUR II's report screen. Copy one in the editor to change it, choose another on the round
  report's page in *Design → Pages*, or let a screen of a plugin draw the data
  ([Plugins](PLUGINS.md#the-round-report)).
- **Cost.** One sample a second, whatever the theme's `refresh_ms`. While the
  service runs the sources it needs run too, although no theme shows them: the
  load (`cpu.load`, `gpu.load`), temperatures, power and clocks, the fans, the
  game's frame rates, and with `music` the player's state (not its title). The
  PC's power (`power.watts`) is kept where the power service publishes it.
  A write that cannot be made (the disk is full, the file is locked) is said
  once in the log and never holds the panel back.
- The options are read when the service starts: change `config.toml` and it
  restarts by itself.

### Cooling analysis

Checks the fan curves of FanControl against what your PC really did, and says
what to change so that it runs quieter or cooler. It reads the cooling state that the
[memory](#memory-and-round-report) keeps, and FanControl's own settings:
**read only**. Libre Panel never writes to FanControl; you make the changes
yourself. Nothing is sent anywhere.

```toml
[services]
enabled = ["memory", "cooling"]   # the memory with cooling = true feeds it

[services.cooling]
limits = [
  { key = "cpu.temp", max = 90 },
  { key = "gpu.temp", max = 85, name = "Graphics card" },
]
# fancontrol = ""      # FanControl's userConfig.json, if Libre Panel does not find it
# database = ""        # the memory's file (default: the memory service's folder)
```

- **It answers, it does not show.** The service has no screen of its own: an
  editor page of a plugin, or a script, asks it for the analysis, for the
  recommendations or for a plain-text report ([Plugins](PLUGINS.md#the-cooling-analysis)).
  The work is done when asked and kept for 5 minutes, and until FanControl's
  settings change. The texts are in the language of the panel.
- **Everything is weighed by time**, not by rows: half an hour of the desktop
  standing still counts for half an hour, a minute of changing load for a
  minute. The recommendations look at the last 60 days of the cooling state,
  and only at what happened since FanControl was last changed (the memory
  notes every change); they say what the data before and after the change
  shows. A statement about the desktop needs 30
  minutes of desktop since the change, one about a game 20 minutes of game
  time; oscillation needs 6 hours. Until then it says that it is waiting.
- **What it looks for.** Each finding names the data it rests on ("50 min game,
  2.5 h desktop") and how certain it is.

| Rule | The question |
|---|---|
| `source` | Does a curve read the sensor of the device it should cool for? From the duty a fan really had, it calculates back the temperature its curve must have seen, and compares that with the recorded sensors; where FanControl's own reading of a curve was kept, it compares that directly. |
| `mix` | Does a mix curve pass on the higher value of its parts, as a case fan should, or does one hot part leave the fans cold? |
| `warm` | Does anything stay over its limit for more than 5 % of the time in a game? |
| `oscillation` | Does a duty keep jumping, which one hears as up and down? |
| `quieter` | Does a fan run faster on the desktop than its curve needs, and how many RPM would a gentler start save? |
| `trend` | Does the same light load at the same outside temperature get warmer over weeks (dust, a fan that slows)? It needs 14 days of data and the [weather](#weather) for the outside temperature. |

- **No fixed devices or limits.** The names of the CPU, the GPU and the sensors
  come from the readings the memory noted, the fans from FanControl's file.
  What is too warm depends on the hardware, so `limits` says: each entry names
  a reading (`cpu.temp`, `gpu.temp`, `gpu.hotspot`, or any other the memory
  keeps, see its `temperatures`), the highest temperature wanted and, if you
  like, a name. Without `limits` the starting points are 90 °C for the CPU,
  85 °C for the GPU and 100 °C for its hot spot: change them to what your
  parts are specified for.
- **Without FanControl** (it is a Windows program) or without its file there
  are no curves to check: the service still says what is too warm in a game
  and what the trend is. It finds FanControl's `userConfig.json` as the memory
  does; `fancontrol` names it if it lies elsewhere.
- **What it keeps.** Nothing of its own: it opens the memory's file read only,
  so it can run while the memory writes. Without the memory's file (or with
  `cooling = false`) it says that there is no data yet.
- The options are read when the service starts: change `config.toml` and it
  restarts by itself.

## Language

Libre Panel speaks English, German, French, Spanish, Italian, Polish,
Portuguese (Brazil), Russian, Japanese and Simplified Chinese: `"en"`, `"de"`,
`"fr"`, `"es"`, `"it"`, `"pl"`, `"pt"`, `"ru"`, `"ja"`, `"zh"`.
`language = "auto"`
follows the system language, and English where Libre Panel does not speak it;
*Settings → Start and system → Language* (and Ctrl+K) writes this setting. It
covers the app, the tray menu, messages about the panel, and the panel itself:
weekday and month names (in German with "27.", in Spanish and Portuguese with
"27 de", in Polish and Russian in the genitive, "27 września" and "27 сентября", in Chinese
and Japanese as "9月27日"), and weather descriptions; characters the built-in fonts do not have
(Chinese and Japanese, and Cyrillic in Barlow) are drawn with a font of the system. Texts
that are part of a theme (such as "CPU TEMP")
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
Moving things run smoothly: values glide to each new reading in about half a
second (the theme's `animation.smoothing_ms`) and graphs scroll on every frame
instead of jumping once per reading.

```toml
[video]
local_clip = "usr/data/standby.h264"
```

With `mode = "auto"` (the default) the video mode is on by itself where it is
proven, today the 9.2", as soon as ffmpeg is found and `local_clip` is set.
`libre-panel doctor` and the log say what is missing. `mode = "on"` tries it on
every TURZX USB panel; `"off"` keeps single frames, PNG or JPEG as `[device] still` says
(JPEG by itself on the 5.2", see [the protocol notes](protocol/turzx-usb.md#jpeg-stills-101)).
In the app, *Settings →
Panel* shows the same as *Smooth motion* (on with its frame rate, or what is still missing),
sets the mode and the frame rate (25, 30 or 50), and while the clip's name is missing asks
for it: the field is empty until you type a name, and *Use this name* takes the vendor app's.
Once it is set, the name is under *Advanced*.

Where the clip's name is all that the video mode lacks (a panel it is proven on, ffmpeg
found, `mode = "auto"`), *Start* asks for it once, in a card (on a first start too), and
the tray menu has a line, *Smooth motion: confirm the clip name…*, that opens it. The
field in the card holds the name the vendor app uses, `usr/data/standby.h264`; nothing is
written until you press *Use it* (which writes `local_clip`; a panel that Libre Panel is
running switches to video mode by itself, without a restart) or *Not now* (which writes no
name). Either answer sets `clip_asked = true`, so it is not asked again; put
`clip_asked = false` back to be asked once more. A name typed in *Settings → Panel* is an
answer too.

- **ffmpeg** encodes the video. The Windows and Linux downloads carry one, with libx264, in
  their `tools` folder ([below](#programs-that-come-with-the-downloads)), and Libre Panel uses
  it before any other. With a pip install, install ffmpeg yourself (Windows: `winget install
  ffmpeg`; Linux: the `ffmpeg` package; it must include libx264, which the usual builds do),
  or set `ffmpeg = "C:/path/to/ffmpeg.exe"`, which wins over all of them, in a download too.
  It uses one CPU core, lightly. When Libre Panel runs as administrator, the app does not
  change this path (it starts that program with those rights): set it in `config.toml`.
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
- `video-state.json` beside `config.toml` keeps what `"auto"` found out for the next start:
  the bit rate each panel got to (the next start begins there, within the steps, instead of
  climbing them again), whether ffmpeg can start the graphics card's encoder (asked again
  after a week, as a new driver may change it; with the last line ffmpeg said, which
  *Check the colours* shows for an encoder "auto" passed over), a card's encoder that ended
  before its first picture converting the colours itself (for a week the processor converts
  for it at once; then the card is tried again) and the colour check's verdict on each card's
  encoder in each range (`pixels = "auto"`: whether the card converts; it holds for that
  ffmpeg, its version and the graphics driver's, at most a week); delete the file to have all
  of it found out anew.

| Setting | Default | |
|---|---|---|
| `mode` | `"auto"` | `"auto"`: on where proven (9.2"), with ffmpeg and `local_clip`; `"on"`, `"off"` |
| `fps` | 30 | frames per second (1–60). 30 looks smooth on the panels; 50 is smoother still and costs about two thirds more processor time (measured with two panels: 2.0 instead of 1.2 cores of the processor); 60 is an experiment the panels do not keep up with |
| `device_fps` | 60 | the rate reported to the panel; must be at least `fps`. The panel's player never catches up once behind, so it gets a little more than it needs |
| `crf` | 20 | quality, 0–51; lower is sharper and needs more bits (20 was measured on the 12.3" and the 9.2" at about 3–4 Mbit/s at 50 fps on a screen that moves; 25 was the first measurement's) |
| `preset` | `"superfast"` | x264 speed preset |
| `keyframe_s` | 10 | seconds between keyframes; below 3 flat backgrounds visibly pulse |
| `maxrate` | `"auto"` | highest bit rate: `"auto"` starts at 2 Mbit/s for the 9.2" (1920×480 at 50 fps) and proportionally more for a panel with more pixels or frames (the 12.3": 3 Mbit/s), then follows the panel up the steps 3 · 4 · 6 · 8 · 12 · 16 · 24 · 32 Mbit/s: a step down while the panel takes less than it is sent, never below the start (a panel that cannot keep up there cannot keep up with the pictures: fewer `fps` help), a step up after a calm minute; the *Data rate* tile under Live says the cap in force. Or a fixed rate such as `"1500k"` or `"4M"` |
| `profile` | `"auto"` | (these and the rows below are also on the card *Video experiment* under Settings → Panel, with how the panel is doing underneath; *Run every step by itself* there tries them all in turn and judges each from the panels' numbers) the H.264 profile: `"auto"` is `"high"` on the 9.2" and the 12.3" (measured to show there; it compresses far better) and `"baseline"` elsewhere; `"main"` and `"high"` ask more of a panel's decoder (if the panel shows nothing, go back to `"baseline"`) |
| `encoder` | `"auto"` | the encoder: `"auto"` is the graphics card's where it was measured to show and ffmpeg can start it (AMD's `"h264_amf"`, probed once a week; one that stops before its first picture is left out), else `"libx264"` on the processor; `"h264_nvenc"` (NVIDIA) and `"h264_qsv"` (Intel) are an experiment to set by hand (`crf` and `preset` are x264's, a hardware encoder takes the quality from `maxrate`, NVIDIA's from `crf` too). What ffmpeg says about an encoder it cannot start is in the log |
| `pixels` | `"auto"` | who turns the frames' colours into video (see below): `"auto"` the graphics card where the colour check found it converting exactly as ffmpeg does on the processor, else the processor. The default, because the card's conversion saves the processor a few milliseconds a frame but cannot be trusted unseen: the colour check measured AMD's encoder converting with BT.709 and a full-range tag when left to itself, while the panel's decoder applies BT.601 whatever the stream says. `"card"` the card always (AMD's, NVIDIA's and Intel's encoders; x264 has none); `"rgb"` the processor always, fed three bytes a pixel, the way of before. The log's line `video: encoding with ...` says which it is (`h264_amf, rgb0, card`, `h264_amf, rgb0` or `h264_amf, rgb24`). A card's encoder that ends before its first picture converting the colours itself is started again with the processor's conversion, by itself |
| `ffmpeg` | `""` | path to ffmpeg; empty = the download's own, else look in `PATH` |
| `local_clip` | `""` | see above |
| `clip_asked` | `false` | `true` once the app has asked for `local_clip` (a name written there, or *Not now*); then it does not ask again |

**The colours (`pixels`): the app checks it for you.** Every stream says of itself that it is
BT.601 (its colour matrix, primaries and transfer) in the range the panel gets, on every path
and every encoder, so that what plays it elsewhere shows it as the panel does. With
`pixels = "auto"` the processor converts until a check has compared the graphics card's
conversion with it: the first time the video mode starts one of the card's encoders with a new
ffmpeg, ffmpeg version or graphics driver, a check runs once by itself in the background (five
frames of a test picture each way, a few seconds, never on the panel and never while another
check runs). Where every patch comes out within ±2 code values the card converts from then on
(the panel switches at its next frame); else the processor goes on. The card's conversion is
told to convert as ffmpeg does: AMD's through its own scaler (`vpp_amf`, BT.601 in the panel's
range), Intel's through `vpp_qsv` with the matrix and the range set; NVIDIA's encoder converts
by itself, in limited range only, and on a panel that gets full range the processor converts
for it.

Settings → Panel → *Check the colours* (under the video experiment) says who converts for the
panel chosen at the top and why: *The graphics card converts the colours: checked identical on
...*, or *The processor converts the colours: the card's differ (matrix shift, ...)*, the card
gives no picture converting them itself, it makes limited range only, or the check is still to
come; *Check again* runs the check again, with *Back to the automatic choice* where `pixels`
forces a way. The check sends the test picture through every encoder this PC can start, the
card's way and the processor's, away from the panel, in the range the panel gets, and measures
the colours that come out: per encoder two rows of swatches, the largest difference and the
verdict (identical, a level shift, a matrix shift or other); it also says which encoder the
panel runs, which one `encoder = "auto"` picks and, for a graphics card's encoder it passed
over, the last line ffmpeg said when it was tried. *Show it on the panel* shows the test
picture on that panel for 30 seconds, A (the card's conversion) and B (the processor's) every
3 seconds, then its theme again. How the check measures is in
[the protocol notes](protocol/turzx-usb.md#encoder).

**Black too light (`video_range`).** The panel's decoder shows the video's values either in
limited range (16 is black, 235 white: what video usually is) or as full range (0 to 255). A
panel that shows a limited-range video as full range shows black as dark grey and every colour
paler, in video mode only; its stills are right. `[device] video_range` (and each `[[panels]]`
table's own) says which the panel gets: `"auto"` as its model is known to show video (full on
the 9.2" and the 12.3", whose video looked washed out in limited range; limited on the others),
`"limited"` or `"full"`. Full range is made by ffmpeg on the processor (BT.601 in full
range), or by AMD's and Intel's card where the check found it right; the stream says so of
itself. *Is black too
light on the panel? Compare* under *Check the colours* shows the test picture on that panel in
limited (A) and full range (B) every 3 seconds; *A looks right* or *B looks right* writes that
panel's `video_range`, and the panel follows at once. Where smooth motion runs on a model that
nobody has compared yet and `video_range` is `"auto"`, Start asks once (*Is black black on panel
1?*) and *Compare on the panel* starts this comparison; the panel report (*Settings → About*)
tells the answer, so the model can learn it.

The settings were measured on the 9.2" panel; the details are in
[the protocol notes](protocol/turzx-usb.md#two-layers-h264-video--png-overlay-smooth-path).

## Programs that come with the downloads

The Windows and Linux downloads (setup, zip, AppImage, tarball) carry the programs that
Libre Panel's features run, in the folder `tools` next to the program (in the folder the
setup installed to, the unpacked zip, the AppImage), so that nothing else has to be installed:

| File | For | Licence |
|---|---|---|
| `ffmpeg`, `ffmpeg.exe` | the [video mode](#video) | GPL-3.0-or-later (a GPL build with libx264) |
| `PresentMon.exe` (Windows) | the [game frame rate](#more-sensor-sources) | MIT |
| `PawnIO_setup.exe` (Windows) | the installer of the PawnIO driver, through which LibreHardwareMonitor reads the CPU's temperature and power ([Windows sensors](#windows-sensors-gpu-power-fans)) | the driver: GPL-2.0-or-later with an exception |

`tools\SOURCES.txt` lists the files with their SHA-256 and says where their source code is;
their licence texts are in `tools\licenses`, and [NOTICE.md](../NOTICE.md) says the same.
They are the files their projects publish, unchanged, fetched when a download is built.
Uninstalling Libre Panel removes these files. What PawnIO's installer put on the PC, the
driver, stays, as other programs may use it, and so does your membership in the group
"Performance Log Users" ([above](#more-sensor-sources)).

A setting always wins: `video.ffmpeg` and `[sensors.presentmon] path` name another program.
Only while Libre Panel runs with administrator rights does it pass over one that lies where
programs without those rights may change it (outside Program Files and Windows): it then
takes the one in this folder or on the `PATH` that is safe, and Settings → Overview says so.
Without them Libre Panel looks in this folder first, then on the `PATH`. It never looks in
your Downloads folder. A pip install has no such folder: install ffmpeg yourself, and
PresentMon if you want game frame rates.

## Windows sensors (GPU, power, fans)

On Windows the temperatures and power of the processor and the graphics card,
and the fans, come from
[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor);
psutil has none of them there. New configurations list it
(`providers = ["load", "audio", "librehardwaremonitor", "presentmon", "psutil"]`; a file made
by an earlier version names `psutil` alone and gets the same list). Libre Panel reads it
one of three ways, chosen by `mode` under `[sensors.librehardwaremonitor]`:

| `mode` | |
|---|---|
| `"auto"` (default) | through the sensor service where it is installed; else inside Libre Panel where that is possible, else through the web server of a LibreHardwareMonitor that you run yourself. A service that does not answer for 20 seconds is replaced by the others until it answers again, and a service installed while Libre Panel runs takes over |
| `"service"` | the sensor service only |
| `"inprocess"` | inside Libre Panel only. Where that is not possible, the log says why and those readings show "--" |
| `"webserver"` | the web server only, as Libre Panel 0.4 did |

### Through the sensor service: every sensor, Libre Panel without administrator rights

The Windows setup installs Libre Panel's **sensor service** (a checked box), and the portable
zip offers *Unlock all sensors* on Start and in *Settings → Sensors*, which sets up the same
after one question of Windows. The service runs LibreHardwareMonitor with the rights its
sensors need and reads every kind of hardware it knows: the processor (temperature, power,
clock), the graphics cards, the mainboard's temperatures and fans, the memory modules'
temperature, the drives' temperatures, fan controllers, pumps and power supplies where there
are any. Libre Panel asks it for the readings over a local pipe, only for the parts a theme on
show reads, and itself runs without administrator rights. The same command sets it up from a
terminal opened as administrator:

```
libre-panel system-setup --service --pawnio --perf-log-users   # all three, or any of them
libre-panel system-setup --remove                               # the service again
libre-panel system-setup --status                               # what is in place (no rights)
libre-panel sensors --check-service                             # does it answer, with what
```

`--pawnio` installs the PawnIO driver from the download (unless version 2 or newer is there),
`--perf-log-users` puts you into *Performance Log Users*, which game frame rates
([PresentMon](#more-sensor-sources)) need. [Running in the background](BACKGROUND.md#the-sensor-service)
says what the service may and may not do, and why other users of the PC can only read
readings through it.

### Inside Libre Panel: no extra program

Without the sensor service (not set up yet, a pip install, or `mode = "inprocess"`) Libre
Panel reads LibreHardwareMonitor itself. **The Windows downloads** (setup and zip) carry
LibreHardwareMonitor's library.
Nothing else is to be installed or started, and no web server turned on. Libre
Panel loads the library with [pythonnet](https://pythonnet.github.io/) on the
.NET Framework that Windows has (4.7.2 or later; Windows 10 from version 1803 and
Windows 11 have it) and reads it in the background once a second
(`poll_seconds`). Only the parts of your computer that the theme on the panel
shows are asked, so a theme with just a clock costs next to nothing.

What it needs:

| Reading | Needs |
|---|---|
| everything of the graphics card (temperature, load, power, clock, fan, memory), the loads of CPU and memory, memory use, network | nothing |
| **CPU temperature, power and clock**, RAM temperature (`mem.temp`; DDR4 and DDR5), **the fans on the mainboard** | Libre Panel runs **as administrator**, and the **PawnIO** driver is installed |

PawnIO is the small signed driver that LibreHardwareMonitor uses to talk to the
chips on the mainboard and in the CPU. It is a separate program, not part of
Libre Panel: install it once from [pawnio.eu](https://pawnio.eu) (version 2 or
newer; installing a driver takes administrator rights). Windows starts programs
without those rights, so Libre Panel has to be told to start with them:

```
libre-panel autostart enable --elevated      # in a terminal opened as administrator
```

([Running in the background](BACKGROUND.md#windows-starting-with-the-highest-rights)
says what that does and when it is safe.) Without the rights or the driver these
readings show "--", never 0, and everything else works as it does. In that case
Libre Panel asks a LibreHardwareMonitor web server (below) once at the start: if
yours runs as administrator, its readings fill in what is missing.

Drive temperatures (`temp.nvme.*` and the like) are not read inside Libre Panel;
the sensor service and the web server have them.

`libre-panel sensors` and `libre-panel doctor` say which of the two is missing.
`libre-panel sensors --check-lhm` only tests that the library loads and opens.
The tray (a line under its status line, and its tooltip) and the Control page say
it too, in a sentence in your language, for as long as it is the case.

### With pip

A pip install has no library in it. Install the extra, which brings pythonnet,
and fetch the libraries (they are not part of Libre Panel; the script downloads
them from NuGet and checks them against fixed checksums):

```
pip install "libre-panel[lhm]"
python fetch_lhm.py "C:\Program Files\lhm-libs"      # in a terminal opened as administrator
```

`fetch_lhm.py` is in the repository under
[`packaging/windows`](https://github.com/syntensa/libre-panel/tree/main/packaging/windows);
it needs nothing but Python. Then name the folder:

```toml
[sensors.librehardwaremonitor]
dll_dir = "C:\\Program Files\\lhm-libs"
```

A 32-bit Python needs `--arch x86`. `dll_dir` can also be the folder of an
unpacked [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases)
download (0.9.6 or a 0.9.7 build), which holds the same libraries.

Libre Panel loads these libraries with the rights it has itself, and for the CPU's
temperatures that means administrator rights. So keep them where only administrators can change
them, under *Program Files* as above: every user of the PC may change a folder made directly
under `C:\` unless you take their rights away, and a library of their own in it would run as
administrator. The app does not change `dll_dir` while it runs as administrator.

### Through the web server of LibreHardwareMonitor

Used where Libre Panel cannot read the library itself (pythonnet or the library
is missing, or the .NET Framework would not start, e.g. because another program
already started pythonnet on another runtime), and always with
`mode = "webserver"`:

1. Install LibreHardwareMonitor and run it **as administrator** (CPU
   temperatures and power need its driver).
2. *Options → Remote Web Server → Run*.
3. Check that `providers` in your `config.toml` lists `"librehardwaremonitor"`,
   ahead of `"psutil"`: `providers = ["load", "librehardwaremonitor", "psutil"]`.

Where LibreHardwareMonitor does not run, nothing else is lost: those readings
show "--". Libre Panel gives a request up after a second, asks less and less
often (every half minute at last) and writes one line to the log.

If LibreHardwareMonitor stops answering (closed, crashed, blocked), its
readings stay for `stale_seconds` (5 by default) and then disappear, so the
panel shows "--" instead of numbers that stand still. A line in the log says
so, and another when it answers again. The same limit hides the readings
of the library inside Libre Panel if its thread ever stops.

## Linux sensors and panel access

On Linux Libre Panel reads what the kernel lets everyone read: no administrator rights, no
extra program, no `lm-sensors`. The `linux` source (in the default list, before psutil)
finds the chips once, looks again every few seconds for new ones, and reads a few tiny files
per sample; the keys of temperatures and fans are those psutil gave in 0.4.

| Reading | Comes from | Needs |
|---|---|---|
| `cpu.temp` | the processor's chip (`k10temp`, `coretemp`, `zenpower`): the die's temperature (Tdie, else Tctl, or the package) | nothing |
| `cpu.power` | the energy counters of RAPL (Intel and AMD), else `zenpower`, `fam15h_power` or `amd_energy` | opening the counters, see [below](#one-click-for-what-needs-an-administrator) |
| `fan.lpc.fan_1` ..., `temp.<chip>.<label>` | the chip on the mainboard (Super I/O: `nct6775`, `it87`, ...) named as Windows names its fans, so Lumen and the SPUR II screens show them | the chip's driver, loaded by the setup |
| `fan.<chip>.<label>` | other fans: the board's controller (`asus_ec_sensors`), coolers and pumps on USB | their drivers, which the kernel loads itself |
| `gpu.temp`, `gpu.hotspot`, `gpu.mem.temp`, `gpu.fan`, `gpu.fan.percent`, `gpu.power`, `gpu.freq`, `gpu.mem.freq`, `gpu.mem.used`, `gpu.mem.total`, `gpu.mem.load`, `gpu.load` | **AMD** (amdgpu): edge, junction and memory temperature, the fan in RPM and per cent, power, both clocks, the video memory and the load. **Intel** (i915, xe): temperature and power where the driver tells them, the clock. **NVIDIA**: the driver's own library (`libnvidia-ml.so.1`, NVML), loaded with ctypes: temperature, fan (per cent, and RPM from newer drivers), power, load, video memory, both clocks | nothing; NVIDIA needs the proprietary driver |
| `temp.nvme.*` | every NVMe drive, named by its model | nothing |
| `temp.drivetemp.*` | SATA drives, only while a theme shows them (a drive in standby can wake when it is read) | the `drivetemp` driver, loaded by the setup |
| `mem.temp` | the hottest memory module's own sensor: `spd5118` (DDR5, Linux 6.8 and later) or `jc42` (DDR4) | the driver: DDR5's loads itself, DDR4's by `sudo modprobe jc42` |

`gpu.fan` is in RPM, as on Windows; where a driver tells only the speed in per cent
(NVIDIA's drivers before 555), `gpu.fan.percent` has it, and `gpu.fan` is missing rather
than made up. Several graphics cards: `gpu.*` is the card `gpu.name`
names (an NVIDIA card first, then the one with the most memory of its own); `gpu = "RX 7900"`
under `[sensors.linux]` (or, as on Windows, under `[sensors.librehardwaremonitor]`) picks
another by a part of its name, and the other cards keep the keys of their chips
(`temp.amdgpu.edge_2`). A card that is powered down to save energy, as a laptop's second one,
is never asked: that would switch it on.

### One click for what needs an administrator

Three things cannot be done without root, and the app offers them in one step. *Start* shows
a card while something is missing (the panel may not be used, no fan readings, no processor
power), and *Settings → Sensors* has the same under *Set up sensors and panel access*. The
button asks your system for the administrator's password in its own window (polkit's
`pkexec`), then runs **`libre-panel system-setup --linux`** as root. It is the command that
sets up Windows' [sensor service](#through-the-sensor-service-every-sensor-libre-panel-without-administrator-rights)
too, with the flags of each system; on Linux `--linux` is taken for granted unless a flag of
Windows' is given. The same in a terminal:

```
libre-panel system-setup --linux              # AppImage: ./Libre_Panel-x86_64.AppImage system-setup --linux
libre-panel system-setup --linux --dry-run    # only say what it would do
libre-panel system-setup --linux --status     # what is in place and what it would bring (no rights)
libre-panel system-setup --linux --remove     # take it all out again
```

What it does, and nothing else:

1. **The panel**: writes `/etc/udev/rules.d/60-libre-panel.rules` (what
   `libre-panel udev-rules` prints), makes udev read its rules again and looks at a panel that
   is plugged in again, so it need not be unplugged.
2. **The mainboard's fans and temperatures**: the chip that reads them has a driver in the
   kernel that is not loaded by itself. On a desktop board of a maker whose boards have such a
   chip (ASUS, Gigabyte, MSI, ASRock, Biostar, EVGA, Supermicro, Intel) it loads `nct6775`,
   `it87` or `nct6683`, in the order that fits the maker, and stops at the first that finds its
   chip. What `sensors-detect` has recorded counts more than that guess. The drivers that
   found a chip are written to `/etc/modules-load.d/libre-panel.conf`, so they come back at
   every start. A notebook, a virtual machine, a maker's own PC and a board whose chip is read
   already are left alone. No driver gets a parameter and none is forced: where the firmware
   keeps the chip for itself (the kernel says "resource busy"), Libre Panel says so and does
   not set `acpi_enforce_resources`.
3. **SATA drives' temperatures**: the same file gets `drivetemp`, if there are SATA drives.

**The processor's power is off unless you turn it on.** Since 2020 the kernel keeps the energy
counters of the processor (`/sys/class/powercap/intel-rapl:*/energy_uj`) from everyone but root,
because they can give away what the processor computes (CVE-2020-8694, "PLATYPUS"). The card
has a switch, *Let Libre Panel read the processor's power*, and the command has `--rapl`
(`--no-rapl` closes them again): it writes `/etc/udev/rules.d/60-libre-panel-rapl.rules`, which
makes the counters readable for every program on the PC, and opens them now. Leave it off on a
PC where you do not trust every program you run.

The password window says which program it runs: the AppImage file (root starts that file, as
the AppImage's own mount is closed to other users) or the Python of your installation. Only
that file asks for the rights, and it works out what to do by itself on the PC it runs on;
nothing from the app goes to it but its switches (`--rapl`, `--no-rapl`, `--remove`). Everything it
does is printed (with `--json` as one line of JSON, which is how the app reads it), a second
run changes nothing, and `--remove` takes out the three files and closes the counters again
(drivers stay loaded until the next start, since another program may use them). Where there
is no password window (no polkit agent, a session without a desktop) the card gives the command
for a terminal, with `sudo`.

What the setup cannot do: chips that no kernel driver supports. MSI's NCT6687D and the newer
ITE chips of many Gigabyte boards need a driver from outside the kernel (look for the
`nct6687d` and `it87` projects of your distribution); Libre Panel does not install drivers.
The log of the setup and `libre-panel sensors` say what was tried.

## Weather

Weather is off by default and nothing is sent anywhere until you enable it.
When enabled, only your coordinates go to Open-Meteo every `update_minutes`.
In the app, *Settings → Weather and place* finds the place by its name: the name
you type goes to Open-Meteo's place search, only when you press *Search*, and the
place is kept (and sent) to about a kilometre. The sun and the moon use the place
too, also while the weather is off.
The answer includes the rain in 15-minute steps, for the `weather.rain.*`
readings of the [theme guide](THEMES.md#sensor-keys).
Weather data by [Open-Meteo.com](https://open-meteo.com), CC BY 4.0, free for
non-commercial use.
