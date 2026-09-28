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

With the downloads:

- **Windows setup:** the installer offers *Start Libre Panel when I log in*
  and starts it at the end. The start menu entry is *Libre Panel*. The
  command-line tool is `libre-panel.exe` in the installation folder
  (`%LOCALAPPDATA%\Programs\Libre Panel`). Updates and uninstalling quit a
  running Libre Panel first; uninstalling also removes autostart (your
  settings and themes stay).
- **macOS:** open the disk image and drag *Libre Panel* to *Applications*.
  The download is not signed yet, so macOS refuses the first start: open
  *System Settings → Privacy & Security* and choose *Open Anyway* (or run
  `xattr -dr com.apple.quarantine "/Applications/Libre Panel.app"`). The
  command-line tool is `"/Applications/Libre Panel.app/Contents/MacOS/libre-panel"`.
  Autostart points at the app, so keep it in *Applications*.
- **Linux AppImage:** `chmod +x Libre_Panel-*.AppImage`, then double-click
  it or run it without arguments for the tray app; with arguments it is the
  command line (`./Libre_Panel-…AppImage autostart enable`). Autostart
  points at the AppImage file; if you move it, enable autostart again. The
  AppImage needs FUSE, which desktop distributions include.

Installed with pip, add the `tray` extra: `pipx install "libre-panel[usb,tray]"`.

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

## Shutting down

Quit, signing out and shutting the PC down all end Libre Panel cleanly: it
releases the panel and leaves the last frame on it. On Windows, which ends
programs abruptly at shutdown, Libre Panel asks to be told early and answers
only once the panel is left in order (at most a few seconds). Whether the
panel still shows something while the PC is off depends on the mainboard:
only with USB standby power does it stay on at all.

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

### Windows: starting with the highest rights

Some sensors (CPU temperature and power, RAM temperature, mainboard fans)
can be read only with administrator rights. The built-in LibreHardwareMonitor
source asks LibreHardwareMonitor's web server and needs none; a sensor
plugin that reads the hardware itself does. For that, in a terminal opened
with *Run as administrator*:

```
libre-panel autostart enable --elevated
```

This creates the Task Scheduler task `Libre Panel`, which starts Libre Panel
at your login with the highest rights (without a UAC prompt), at normal
priority and without a time limit; the `Run` value is removed, so it starts
once. `libre-panel autostart disable`, again as administrator, removes the
task. Only use it when Libre Panel is installed where only administrators
can change it (under *Program Files*): a program in your user folder could
be replaced by anything you run, which would then get those rights at the
next login. `enable --elevated` warns about that.

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
