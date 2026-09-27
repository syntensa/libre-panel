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
