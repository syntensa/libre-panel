# What came from SPUR II

SPUR II was a private dashboard for one TURZX 9.2" panel on one PC. Libre Panel
keeps what applies to everyone and leaves out what only made sense there.

## Taken over

| SPUR II | Libre Panel |
|---|---|
| USB transport, DES command packets, ZLP fix, echo check, resync | `devices/turzx_usb.py`, [protocol notes](protocol/turzx-usb.md) |
| 9.2" is 480×1920, landscape = ROTATE_270, PNG must be RGBA | model catalog, driver |
| Two-layer video path (cmd 110/121 + PNG overlay), encoder settings, flow control, the shutdown sequence, the restart of a hung decoder | `devices/turzx_video.py` and `h264.py` (ffmpeg), `[video]`; `mode = "auto"` turns it on by itself on the 9.2" once ffmpeg is found and `local_clip` is set |
| What keeps a frame on time at 50 fps: the timer at 1 ms, the garbage collector kept off what lives as long as the app, the panel's threads above normal priority | `timing.py`, active while a panel is driven |
| Motion: values that glide, graphs drawn from a history of 25 points a second, threshold colours that fade, one engine across screen changes | `render/motion.py` (one `Motion` for the panel, handed to every renderer); the next theme is built in the background before it comes on |
| LibreHardwareMonitor as the sensor source; GPU power = board power (TBP) | `sensors/librehardwaremonitor.py` |
| LibreHardwareMonitor inside the app, not a second program with its web server; a CPU sensor that reads zero without rights shows "--" | `sensors/lhm_inprocess.py` (pythonnet); the Windows downloads carry the libraries, fetched from NuGet when they are built. The web server stays the way where inside is not possible |
| Match sensors by type *and* name (two readings called "CPU": a temperature and a fan) | LHM aliases group by sensor type |
| A stopped GPU fan (0 RPM) is a valid value | kept as a reading, not treated as missing |
| CPU and GPU load ten times a second (the GPU as Task Manager shows it) | `sensors/load.py`, the `load` source: read only while a theme shows it, on every frame in video mode |
| The volume, by Windows' own change events | `sensors/audio.py`, the `audio` source (`audio.volume`, `audio.muted`): Core Audio on Windows, `wpctl` or `pactl` on Linux; in the default list |
| What is playing, with its cover and the player's icon, by the system's events | `sensors/media.py` and `appicon.py` (`media.*`, the pictures `media.cover` and `media.app_icon`) |
| The game's frame rate with its slowest frame and who holds it down | `sensors/presentmon.py` (`game.*`) |
| Open-Meteo weather, 15-minute interval, retry after 2 minutes | `weather/open_meteo.py`, location from config, **off by default** |
| Messages: how long one stays, the hold time of one without its own, hidden or kept while a transition plays, a switch while one plays, a screen that shows a kind itself, several at once (T1 to T6) | `render/overlays.py` and the toast and transition parts of the [plugin API](PLUGINS.md); the scenarios run through the real main loop in `tests/test_toast_scenarios.py` |
| The band: a glowing edge ignites, the band rolls down, the volume changes it in place | `render/band.py`, `"toast": {"style": "band"}` |
| The tube (a line ignites and opens onto the new screen) and the entrance (the screen closes to a line, the game's logo lights up, "Game mode" types in) | `render/transitions.py`: the transitions `tube` and `entrance` |
| The look: light baked once in Blender (rings, bars, fan rotors, weather symbols), the grid, the panel's light, depth dust, the lettering of the wordmark | `render/lit.py`, `light3d.py`, `look.py`, `dust.py`, `fan.py`, `facet.py`: a widget's `finish`, the theme's `look`. The colours come from the theme |
| Messages for the volume, a new track, a sensor past a limit and a restarted panel | the `notices` service, `services/notices.py` |
| Game mode: the game found from a list or Steam's library, its logo, the entrance, six seconds after the game ended | the `game` service, `services/game.py` (with `steam.py` and `machine.py`) |
| Autopilot switching between screens | the `autopilot` service, `services/autopilot.py` |
| Messages for drives, USB devices and controllers that come and go, a full drive and a hot SSD | the `devices` service (Windows), `services/devices.py` and `devices_windows.py` |
| The memory of weeks and months (minutes, games, the cooling state) and the report after a game | the `memory` service, `services/memory.py`, `memory_store.py` and `round_report.py`, with the theme `spur-round-report` (Libre Panel's own is `round-report`) |
| Fan curves checked against what the PC did; the curves and fan names of FanControl; recording the cooling state | the `cooling` service, `services/cooling.py` and `fancontrol.py`, fed by the memory: FanControl's file is only read, device names come from the readings |
| Downloads of Steam and the browsers | the `downloads` service, `services/downloads.py` |
| Classic layout, arctic base, teal signature `#13E5D7`, equal perceived brightness of the channel colors | built-in theme `spur-ii` |
| Measured layout: charts at x 29 / 989, bar columns 276 / 746 / 1236 / 1624, sections from y 298 | `spur-ii` geometry |
| Theme (look) separate from device settings; brightness belongs to the device | `theme.json` vs. `config.toml` |
| Live reload on save | the main loop reloads config and theme |
| Local web editor with live preview from the real renderer | `libre-panel editor` |

The services are written with the plugin API only, are off until
`[services] enabled` lists them, and `libre-panel plugins` shows them as "built in"
([configuration guide](CONFIGURATION.md#built-in-services)).

## Left out (personal or machine-specific)

| SPUR II feature | Why |
|---|---|
| Weather fixed to one home location | personal; now any location via config |
| Game statistics from online trackers and game APIs | personal use case, and a network service; Libre Panel keeps its own frame-rate statistics on this PC (the `memory` service) |
| HWiNFO shared memory / bundled HWiNFO engine | 12-hour limit in the free version, proprietary engine |
| Tools that decrypt or patch the vendor application | not needed (protocol is public) and not something to redistribute |
| Hard-coded paths, device serial number, PC model names | personal data |

## Still to port

- Rolling clock digits (roadmap 0.2).
