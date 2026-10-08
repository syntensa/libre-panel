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
transition = "fade"            # between themes: "cut", "fade", "slide" or from a plugin

[device]
model = "auto"                 # or an id from `libre-panel models`
driver = "auto"                # panel if connected, else PNG; "turzx" or "virtual" to force
brightness = 60                # 0-100
output = "libre-panel-frame.png"   # frames go here when no panel is used;
                                   # a relative path is inside the settings folder
port = ""                      # serial panels: found by their USB ids; only if that
                               # picks the wrong port: "COM5", "/dev/ttyACM0"

[sensors]
providers = ["psutil"]         # order = priority; also "librehardwaremonitor", "demo", plugins

[sensors.librehardwaremonitor]
url = "http://127.0.0.1:8085/data.json"

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

## More sensor sources

Besides the hardware, Libre Panel reads a few things from your life. The sun,
the moon and what is playing come by themselves; the others are listed in
`[sensors] providers` and set up in their own table.

**The sun and the moon** (`sun.*`, `moon.*`) are computed, no internet
needed. Sunrise and sunset need your place: `latitude` and `longitude` in
`[weather]` (also when the weather itself is off).

**What is playing** (`media.*` and the cover) is read only while a theme
shows it:

- Windows: the media controls of Windows, whatever the volume pop-up shows
  (Spotify, browsers, media players). The installer brings what it needs; a
  pip install needs `pip install "libre-panel[media]"`.
- Linux: any MPRIS player through `playerctl` (install it from your
  distribution).
- macOS: Spotify or Music.

**Calendars** (`calendar.*`): the secret address of a calendar (Google:
settings of the calendar → "Secret address in iCal format"; Outlook:
calendar → share → publish → ICS; iCloud and Nextcloud: share as a link)
or an `.ics` file. The address is a secret: it stays in your settings file
and is never written to the log.

```toml
[sensors]
providers = ["psutil", "calendar"]

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
providers = ["psutil", "homeassistant"]

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
providers = ["psutil", "mqtt"]

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
providers = ["librehardwaremonitor", "psutil", "presentmon"]

[sensors.presentmon]
path = "C:/Tools/PresentMon-2.3.0-x64.exe"
```

Show these readings with the Dashboard module (`ha.sensor.living_room_temperature
= Living room`, one per line), a list, or any widget with a `sensor`.
`libre-panel sensors` lists what is there.

## Services and modes

Plugins can add services (a game mode, an autopilot, …). They are off until
you list them; each can have its own options table. A mode changes the frame
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

1. Install [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
   and run it as administrator (CPU temperatures and power need its driver).
2. *Options → Remote Web Server → Run*.
3. `providers = ["librehardwaremonitor", "psutil"]`.

## Weather

Weather is off by default and nothing is sent anywhere until you enable it.
When enabled, only your coordinates go to Open-Meteo every `update_minutes`.
Weather data by [Open-Meteo.com](https://open-meteo.com), CC BY 4.0, free for
non-commercial use.
