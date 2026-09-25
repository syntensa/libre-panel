# What came from SPUR II

SPUR II was a private dashboard for one TURZX 9.2" panel on one PC. Libre Panel
keeps what applies to everyone and leaves out what only made sense there.

## Taken over

| SPUR II | Libre Panel |
|---|---|
| USB transport, DES command packets, ZLP fix, echo check, resync | `devices/turzx_usb.py`, [protocol notes](protocol/turzx-usb.md) |
| 9.2" is 480×1920, landscape = ROTATE_270, PNG must be RGBA | model catalog, driver |
| Two-layer video path (cmd 110/121 + PNG overlay), encoder settings | documented; implementation planned (0.2) |
| LibreHardwareMonitor as the sensor source; GPU power = board power (TBP) | `sensors/librehardwaremonitor.py` |
| Match sensors by type *and* name (two readings called "CPU": a temperature and a fan) | LHM aliases group by sensor type |
| A stopped GPU fan (0 RPM) is a valid value | kept as a reading, not treated as missing |
| Open-Meteo weather, 15-minute interval, retry after 2 minutes | `weather/open_meteo.py`, location from config, **off by default** |
| Theme (look) separate from device settings; brightness belongs to the device | `theme.json` vs. `config.toml` |
| Live reload on save | the main loop reloads config and theme |
| Local web editor with live preview from the real renderer | `libre-panel editor` |
| Classic layout, arctic base, teal signature `#13E5D7`, equal perceived brightness of the channel colors | built-in theme `spur-ii` |
| Measured layout: charts at x 29 / 989, bar columns 276 / 746 / 1236 / 1624, sections from y 298 | `spur-ii` geometry |

## Left out (personal or machine-specific)

| SPUR II feature | Why |
|---|---|
| Weather fixed to one home location | personal; now any location via config |
| Fan names read from FanControl's user config, fan curve recording and recommendations | tied to one PC's cooling setup |
| Game statistics (tracker/game APIs), Steam game detection, "game mode" screens | personal use case; could be a plugin |
| Media "now playing", volume, download and device notifications | Windows-only integrations; candidates for plugins |
| Autopilot switching between screens | depends on the above |
| Blender-rendered lighting, particles, 3D styles | asset pipeline outside the app; effects may return as widget styles |
| HWiNFO shared memory / bundled HWiNFO engine | 12-hour limit in the free version, proprietary engine |
| Tools that decrypt or patch the vendor application | not needed (protocol is public) and not something to redistribute |
| Hard-coded paths, device serial number, PC model names | personal data |

## Still to port

- The H.264 video layer (see roadmap 0.2). Useful from the SPUR II side:
  the exact 121 chunk header and pacing, and the shutdown sequence.
- Animation ideas: easing, sub-pixel scrolling graphs, rolling clock digits,
  soft color transitions at thresholds.
