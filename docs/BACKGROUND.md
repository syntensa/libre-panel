# Running in the background

For everyday use Libre Panel runs as a small background app: it drives the
panel, serves the theme editor and shows an icon in the system tray. It can
start by itself when you log in.

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
- **Linux AppImage:** `chmod +x Libre_Panel-*.AppImage`, then double-click
  it or run it without arguments for the tray app; with arguments it is the
  command line (`./Libre_Panel-…AppImage autostart enable`). Autostart
  points at the AppImage file; if you move it, enable autostart again. The
  AppImage needs FUSE, which desktop distributions include.

Installed with pip, add the `tray` extra: `pipx install "libre-panel[usb,serial,tray]"`.

## Tray menu

| Item | What it does |
|---|---|
| status line | what the panel shows, or why it waits (e.g. "waiting for the panel") |
| **Open theme editor** | also a click on the icon |
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
shows the state and has pause, brightness, upside down, start with system
and quit.

## How the panel is doing

Below the state, the **Panel** menu shows numbers measured over the last few
seconds. They are also in `/api/app` (`panel.stats`) and on the *Control*
page (below).

| Row | What it says |
|---|---|
| **Frame rate** | frames drawn per second, and the rate Libre Panel aims for (the mode's, the display's, else `fps`). In video mode every frame goes out, so a rate below the target means the PC or the panel cannot keep up. Without video mode a frame is drawn only when something moves, so a lower rate is normal there |
| **Drawing** | the median time to draw a frame, and the slowest of the last 120. It has to stay below the time one frame has (20 ms at 50 fps) |
| **Data rate** | what goes to the panel, in KB/s (1 KB = 1000 bytes); not shown for the PNG file |
| **Video** | video mode only: pictures (blocks) sent per second, the deepest queue the panel reported (it holds five, and the driver waits above two), how often the driver waited for room, and how many pictures it threw away unsent when the stream restarted |
| **Faults** | since the panel loop started: errors on the panel's link that made the driver reconnect, restarts of the panel to clear a hung video decoder, and restarts of ffmpeg |

Measuring costs next to nothing: the main loop keeps a ring of drawing times
and counts, and makes the numbers once a second; nothing waits for it.

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

The entry starts `libre-panel tray --background`, which does not open the
editor. If a panel is not ready yet at login (USB still starting), Libre
Panel keeps looking for it and uses it as soon as it appears.

### Windows: starting with the highest rights

Some sensors (CPU temperature and power, RAM temperature, mainboard fans)
can be read only with administrator rights. The built-in LibreHardwareMonitor
source reads them inside Libre Panel, so Libre Panel needs the rights (and the
PawnIO driver, see [Windows sensors](CONFIGURATION.md#windows-sensors-gpu-power-fans));
a sensor plugin that reads the hardware itself needs them too. For that, in a
terminal opened with *Run as administrator*:

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

The task reads the settings from `%APPDATA%\LibrePanel` as usual. A program
from the Microsoft Store or another MSIX package (some desktop apps and
their terminals) does not write there: Windows redirects what it writes to
`%APPDATA%` into the package's own folder, so settings changed through such
a program never reach the task. Then keep the settings in a folder of your
own and set the user environment variable `LIBRE_PANEL_HOME` to it.

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
