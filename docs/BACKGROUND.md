# Running in the background

For everyday use Libre Panel runs as a small background app: it drives the
panel, serves the theme editor and shows an icon in the system tray (menu bar
on macOS). It can start by itself when you log in.

```bash
libre-panel tray                 # start it (opens the editor the first time)
libre-panel autostart enable     # start it at every login
libre-panel autostart status
libre-panel autostart disable
```

With the release downloads on Windows, double-click **LibrePanel.exe** (the
program without a console window); `libre-panel.exe` is the command-line tool.
Installed with pip, add the `tray` extra: `pipx install "libre-panel[usb,tray]"`.
The macOS download is not signed yet, so Gatekeeper blocks it; run
`xattr -dr com.apple.quarantine .` once in the unpacked folder (or install
with pipx).

## Tray menu

| Item | What it does |
|---|---|
| status line | what the panel shows, or why it waits (e.g. "waiting for the panel") |
| **Open theme editor** | on Windows and Linux also a click on the icon |
| **Theme** | switch the theme on the panel |
| **Brightness** | Off, 10–100 %; written to `config.toml` |
| **Pause panel** | stops sending frames and releases the USB device, e.g. for the vendor app |
| **Start with system** | the same as `libre-panel autostart enable/disable` |
| **Open settings folder** / **Open log** | `config.toml`, user themes, `logs/libre-panel.log` |
| **Quit Libre Panel** | |

The icon's dot shows the state: green = showing, amber = waiting for the
panel, grey = paused, red = the config or theme needs attention (the log and
the editor say why; Libre Panel starts again by itself as soon as the file is
fixed).

The same controls are in the editor: the **Panel** button at the top right
shows the state and has pause, brightness, start with system and quit.

## Only one at a time

Only one Libre Panel drives the panel. Starting it a second time (a second
double-click, `libre-panel tray`, `libre-panel editor`) opens the editor of
the one that is already running. `libre-panel run` and `libre-panel doctor`
refuse to start while it runs — quit it from the tray first.

## Autostart, per system

Autostart is per user and needs no administrator rights. Turning it off
removes exactly what turning it on created.

| System | Entry |
|---|---|
| Windows | registry value `Libre Panel` under `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run` |
| Linux (and other XDG desktops) | `~/.config/autostart/libre-panel.desktop` |
| macOS | `~/Library/LaunchAgents/io.github.syntensa.libre-panel.plist` (restarted after a crash, not after Quit) |

The entry starts `libre-panel tray --background`, which does not open the
editor. If a panel is not ready yet at login (USB still starting), Libre
Panel keeps looking for it and uses it as soon as it appears.

### Linux desktops

- **KDE Plasma, Xfce, Cinnamon, MATE, LXQt, Budgie:** the icon appears in
  the tray.
- **GNOME:** GNOME has no tray of its own. Ubuntu includes the *AppIndicator*
  extension, so the icon appears there; on other distributions install the
  "AppIndicator and KStatusNotifierItem Support" extension.
- Without PyGObject (e.g. the release download), Linux trays show a plain
  icon without a menu: a click opens the editor, which has the same controls.
  With PyGObject and AppIndicator installed you get the full menu.
- No tray at all (a server, a minimal window manager): Libre Panel runs
  without an icon; open the editor with `libre-panel editor`.

### Headless (no desktop session)

On a machine without a desktop, run the panel as a systemd user service
instead:

```ini
# ~/.config/systemd/user/libre-panel.service
[Unit]
Description=Libre Panel

[Service]
ExecStart=%h/.local/bin/libre-panel tray --background --no-icon
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now libre-panel
loginctl enable-linger "$USER"      # keep it running without a login
```

The editor is then at <http://127.0.0.1:8765/> on that machine (reach it
through an SSH tunnel: `ssh -L 8765:127.0.0.1:8765 host`).
