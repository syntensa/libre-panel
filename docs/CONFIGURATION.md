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
# refresh_ms = 1000            # override the theme's refresh interval

[device]
model = "auto"                 # or an id from `libre-panel models`
driver = "auto"                # panel if connected, else PNG; "turzx" or "virtual" to force
brightness = 60                # 0-100
output = "libre-panel-frame.png"   # frames go here when no panel is used

[sensors]
providers = ["psutil"]         # order = priority; also "librehardwaremonitor", "demo", plugins

[sensors.librehardwaremonitor]
url = "http://127.0.0.1:8085/data.json"

[weather]
enabled = false                # off unless you turn it on
provider = "open-meteo"
# latitude = 0.0               # your location; find it with:
# longitude = 0.0              #   libre-panel location "Your City"
units = "metric"               # or "imperial"
update_minutes = 15
```

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
