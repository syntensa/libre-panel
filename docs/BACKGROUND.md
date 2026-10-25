# Running in the background

For everyday use Libre Panel runs as a small background app: it drives the
panel, shows an icon in the system tray and opens its own window (start,
theme editor, settings) when you want it. It can start by itself when you log
in.

```bash
libre-panel tray                 # start it (opens its window the first time)
libre-panel autostart enable     # start it at every login
libre-panel autostart status
libre-panel autostart disable
```

With the downloads:

- **Windows setup:** the installer offers *Start Libre Panel when I log in*
  and starts it at the end. It installs for all users (`%ProgramFiles%\Libre Panel`)
  and sets up the [sensor service](#the-sensor-service), the PawnIO driver and what game
  frame rates need, each a checked box; *Install for me only* puts Libre Panel into
  `%LOCALAPPDATA%\Programs\Libre Panel` without administrator rights and leaves those to
  *Unlock all sensors* (an update keeps the way the version before was installed). The
  start menu entry is *Libre Panel*. The command-line tool is `libre-panel.exe` in the
  installation folder. Updates and uninstalling quit a running Libre Panel first;
  uninstalling also removes autostart and the sensor service (your settings and themes
  stay).
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
| hint line | only on Windows, only while temperatures and power show "--": the one thing to do about it, e.g. "CPU temperature and power need administrator rights: run Libre Panel as administrator." The tooltip of the icon says it too |
| **Open Libre Panel** | the app's window on *Start*; also a click on the icon |
| **Smooth motion: confirm the clip name…** | only until it is answered: on a panel the video mode is proven on (the 9.2"), with ffmpeg found and no `local_clip` yet, *Start* asks for the name of the panel's standby clip, and this line opens it there |
| **Theme** | switch the theme on the panel: the themes made for it by name, the others under *More themes*, a collection (SPUR II) in its own |
| **Brightness** | Off, 10–100 %; written to `config.toml` |
| **Pause panel** | stops sending frames and releases the USB device, e.g. for the vendor app |
| **Design** | the window on *Design* |
| **Settings** | the window on *Settings*: every setting of `config.toml` ([Configuration](CONFIGURATION.md#every-setting-is-in-the-app)) |
| **Upside down** | for a panel mounted the other way round |
| **Start with system** | the same as `libre-panel autostart enable/disable` |
| **Open settings folder** / **Open log** | `config.toml`, user themes, `logs/libre-panel.log` (in the file manager) |
| **Quit Libre Panel** | |

The icon's dot shows the state: green = showing, amber = waiting for the
panel, grey = paused, red = the config or theme needs attention (the log and
the editor say why; Libre Panel starts again by itself as soon as the file is
fixed).

The same controls are in the app: the panel button at the top right of
*Design* shows the state and has pause, brightness, upside down, start with
system and quit. *Start* shows the picture on the panel with pause, brightness
and upside down, and *Live* says in a sentence how it runs ("Runs smoothly at 50
fps", slower than planned and what helps, waiting for the panel and why), with its numbers
under it, the sensor sources, the services and test messages (the *Control* page, below, in
the app's look).

## The app's window

Libre Panel shows its app (*Start*, *Design*, *Look*, *Live*, *Settings*) in a
window of its own, not in the browser. There is one window at a time: whatever
asks for the app again (the tray, `libre-panel start`, `libre-panel editor`, a
second start) brings that window to the front, on the view it asks for. The
window keeps its size for the next time; with unsaved changes, closing it asks
first; quitting Libre Panel closes it.

- **Windows:** WebView2 draws the window. Windows 11 has it, Windows 10 gets
  it with Microsoft Edge; the downloads bring everything else. Installed with
  pip: `pipx install "libre-panel[usb,serial,tray,window]"`.
- **Linux:** WebKitGTK draws the window, through pywebview and PyGObject.
  WebKitGTK and PyGObject come from your distribution:

  | Distribution | Packages |
  |---|---|
  | Debian, Ubuntu | `sudo apt install python3-gi gir1.2-webkit2-4.1` |
  | Fedora | `sudo dnf install python3-gobject webkit2gtk4.1` |
  | Arch | `sudo pacman -S python-gobject webkit2gtk-4.1` |
  | openSUSE | `sudo zypper install python3-gobject typelib-1_0-WebKit2-4_1` |

  pywebview goes into the environment Libre Panel runs in, which must see the
  system's PyGObject: `pipx install --system-site-packages
  "libre-panel[usb,serial,tray]"`, then `pipx inject libre-panel pywebview`.
  The AppImage and the Linux archive have no window yet: they open the browser.
- **Without a window** (no WebView2 runtime, no WebKitGTK, no desktop, an SSH
  session) the app opens in the browser as it always did. `libre-panel editor
  --browser` asks for the browser, `libre-panel editor --no-browser` only
  serves the app. `libre-panel window --check` says whether a window can open
  here, and why not; with `--open` it opens one for a moment, minimized, as the
  app does. A window that ends with an error right after its start leaves the
  app in the browser instead, and `logs/window.log` (next to the tray's log)
  says why.

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

## The Control and Autopilot pages

The **Control** page is the console of the background app. It is under *Pages →
From plugins* in *Design* and found by Ctrl+K; *Live* shows the same numbers,
sources, services and test messages in the app's own look. In a plain
`libre-panel editor` there is no panel, so the page says so and keeps what works
without one: the theme per mode and the log.

| Part | What it does |
|---|---|
| **Live picture** | the frame on the panel now, with transitions and messages, about five times a second; it is the theme's way up, not turned for an upside-down panel. Paused, it says so |
| **How the panel is doing** | the numbers above as tiles, with the panel's state and how long the loop has run |
| **Sensor sources** | each source in the order it is asked, with the readings it gave last time. LibreHardwareMonitor says how it is read (inside Libre Panel or through its web server), whether Libre Panel has administrator rights, whether the CPU's sensors are within reach and which PawnIO driver is there, and on Windows what to do when the CPU's temperature and power or all of them do not arrive: the same sentence as the tray's hint line |
| **Services** | each service Libre Panel knows, with its state (running, failed with the reason, stopped, off, or changing while the app follows a change of `config.toml`) and **Restart**, which stops it and starts it again with its settings. A service that is not in `[services] enabled` is off |
| **Test messages** | sends a test message of each kind (volume, music, warning, panel, device, game) and says whether it shows, or why not: the theme switches the kind off (`toast.off`), the screen shows it itself, or no panel is connected |
| **Theme per mode** | the theme of the normal mode and of game mode, written to `config.toml` (`theme`, and `theme` under `[modes.game]`); "same as the normal mode" leaves the game mode without a theme of its own |
| **Log** | the last lines of `logs/libre-panel.log`, which the tray app writes (`libre-panel start` logs to its terminal instead) |

The **Autopilot** page (found the same way, or by *Edit* under the autopilot in
*Settings → Game mode and more*) edits the rules of the `autopilot` service, switches it
on and off, and shows which rule holds and what it sees; see
[the autopilot](CONFIGURATION.md#autopilot).

Both pages are a skin over a JSON API (the live picture, the numbers, the
sources, switching and restarting services, test messages, the theme per mode,
the log, the autopilot's rules), so a screen of another design can show and do
the same: [The console API](ARCHITECTURE.md#the-console-api).

## Shutting down

Quit, signing out and shutting the PC down all end Libre Panel cleanly: it
releases the panel and leaves the last frame on it. On Windows, which ends
programs abruptly at shutdown, Libre Panel asks to be told early and answers
only once the panel is left in order (at most a few seconds). Whether the
panel still shows something while the PC is off depends on the mainboard:
only with USB standby power does it stay on at all.

## Only one at a time

Only one Libre Panel drives the panel. Starting it a second time (a second
double-click, `libre-panel tray`, `libre-panel editor`) brings the window of
the one that is already running to the front. `libre-panel run` and `libre-panel doctor`
refuse to start while it runs — quit it from the tray first.

## Who can talk to the app

The app is served on `127.0.0.1`, an address that every program on the PC can reach, those
of other users too. So the server answers only a request that carries a secret *token*, a
new one at every start of Libre Panel, in the header `X-Libre-Panel`. Only the app's own
files (its page, scripts, styles and fonts) come without it; nothing that reads or changes
anything does. A website cannot guess the token, and no other user of the PC has it.

- The window, the tray, `libre-panel start` and `libre-panel editor` give the token to the
  page in the address, after a `#` (`http://127.0.0.1:8765/#token=…`). A browser never sends
  that part to a server, so it is in no log; the page keeps it for its tab and takes it out
  of the address.
- A program of your own account that starts later, such as the window the tray opens or
  `libre-panel quit`, reads the token from `private/editor.json` in the settings folder. Only
  you can open it: on Linux it has mode 600 in a folder of mode 700, on Windows it is in your
  profile, whose rights keep other users out. The window process is told the token in its
  environment, not on its command line, which other users can read on Linux.
- A page opened without the token, such as a bookmark of an earlier run's address, finds every
  request refused ("Access token missing or wrong"). Open Libre Panel from the tray, or with
  the address that `libre-panel editor` prints (a running Libre Panel prints its own). So does
  a browser tab that was open when Libre Panel started anew: it has the token of the run
  before. Open Libre Panel again; the window of the tray takes the new token by itself.
- The browser, where no window can open, gets the address with the token as an argument. On
  Linux other users can read the arguments of your programs while they run, so on a PC that
  several people use, prefer the window (pywebview, above) to the browser.

Anything that runs as you can read the token and change `config.toml`; the token keeps out the
programs of *other* users and of websites. That is why a Libre Panel that runs with the highest
rights (below) trusts your settings folder and nothing else.

## The sensor service

On Windows the processor's temperature, power and clock, the mainboard's temperatures and
fans and the temperatures of the memory modules and the drives can be read only with
administrator rights, through the PawnIO driver. Libre Panel itself never runs with those
rights unless you set that up ([below](#windows-starting-with-the-highest-rights)). A small
Windows service reads them for it and hands over the readings; this is how it is built and
why that is safe.

**What it is.** The service `LibrePanelSensors` (*Libre Panel sensors*), the program
`%ProgramFiles%\Libre Panel\sensors\libre-panel-sensors.exe`, running as LocalSystem. Windows
starts it a while after it starts itself (automatic, delayed) and starts it again after a
failure (after 5 s, 10 s, a minute). The setup installs it, a checked box; the portable zip
has *Unlock all sensors* on Start and in *Settings → Sensors*, which runs the same command,
`libre-panel system-setup`, after Windows asks once for administrator rights. *Settings →
Sensors* shows it and removes it; the uninstaller removes it too. In the default `mode` of
`[sensors.librehardwaremonitor]` Libre Panel reads LibreHardwareMonitor through it wherever
it is installed, and reads it inside Libre Panel meanwhile if it does not answer.

**What runs as SYSTEM.** The service's program and nothing else: Python, pythonnet,
LibreHardwareMonitor's .NET libraries (the files NuGet publishes, pinned by SHA-256 like the
rest of the download) and Libre Panel's code that reads them and answers on the pipe. It is
a program of its own, built without plugins, renderer, web server or window. It takes no
arguments and reads no settings, no `config.toml`, nothing of any user's folder: what it does
is fixed in its program. It loads from its own folder only, and writes only its log, in
`sensors\logs` next to its program.

**It only reads.** It opens LibreHardwareMonitor on every kind of hardware it knows (CPU,
graphics cards, memory, mainboard and its chips, drives, network, fan controllers and pumps,
power supplies, batteries) and asks its sensors for their values. It never sets a control
(no fan speed, no pump, no light), changes no setting of the hardware and starts no program.
Reading some devices means sending them the request they answer (a USB fan controller, a
power supply), as LibreHardwareMonitor itself does.

**What crosses the pipe.** The service answers on the local named pipe
`\\.\pipe\LibrePanelSensors`, and understands two requests: *hello* (its version) and *read*
(the readings, and which pieces of hardware the client's themes show, which it then reads for
the next ten seconds). The answer to *read* also says how long each piece's update took in the
last round and in the last round of all hardware (*Live → Diagnose → Load*); a service older
than that leaves it out, and the protocol is the same either way. Anything else gets an error
answer. A message is a JSON object after
its length, at most 4 KiB in and 4 MiB out; a length out of bounds ends the connection, as
does a client that says nothing for 30 seconds or takes longer than 5 seconds with a message
it began; a connection may ask four times a second (after a first eight); four clients are
served at once. A request never touches the hardware: it is answered from what the service
read last, so no client can make it read more often than once a second, and while nobody asks
it reads nothing at all.

**Who may talk to it.** The pipe's security descriptor (a protected DACL, set when the
service makes it):

- SYSTEM and Administrators: everything.
- People signed in at this PC (INTERACTIVE: at the screen or over Remote Desktop): read and
  write its data and read its attributes (`0x120083`). Not `FILE_CREATE_PIPE_INSTANCE` (no
  instance of their own under its name), not `WRITE_DAC` or `WRITE_OWNER`.
- Network logons: refused, by the first entry, whatever else they are; clients on other
  computers are turned away by Windows (`PIPE_REJECT_REMOTE_CLIENTS`).

Programs with a low integrity (a browser's sandbox) or in an app container cannot open it
either. So another person who uses this PC can read the same temperatures and fan speeds that
Libre Panel shows you, and that is all: there is nothing to change through the pipe.

**Nobody can pose as it.** The service makes its first instance with
`FILE_FLAG_FIRST_PIPE_INSTANCE`: if a program took the name before it started, the service
does not join that program's pipe and tries again every few seconds. Libre Panel asks Windows
which process serves the pipe and talks only to the one that the Service Control Manager names
for `LibrePanelSensors`. It opens the pipe with `SECURITY_IDENTIFICATION`: the server can tell
who is asking, not act as them. It checks every answer, as the service checks every request.

**Why a user cannot replace what runs as SYSTEM.** The service's program lies in
`%ProgramFiles%\Libre Panel\sensors`. `system-setup` gives that folder rights of its own,
inherited by nothing above it: SYSTEM and Administrators may change it, users may read it and
run what is in it. The folder above it, `%ProgramFiles%\Libre Panel`, is in Program Files,
where only administrators create, rename or delete anything, so the folder cannot be swapped
either. The service is registered with its path in quotes (no `C:\Program.exe`), and its own
security leaves stopping and reconfiguring it to administrators; signed-in people may only
start it (Libre Panel does that when its delayed start has not come yet).

**What `system-setup` does with administrator rights.** As administrator, `libre-panel
system-setup --service --pawnio --perf-log-users` copies the service's folder to Program Files
(not when the setup has put it there already), sets the folder's rights, registers and starts
the service with `sc.exe`, installs PawnIO silently from the official installer that the
download carries (`tools\PawnIO_setup.exe`; not when PawnIO 2 or newer is there) and adds the
person signed in to *Performance Log Users* (S-1-5-32-559), which game frame rates need
(PresentMon), from their next sign-in. It finds `sc.exe` and `icacls.exe` in Windows' own
folder and Program Files in the registry, not in the environment of whoever started it. Each
step can run again without harm. `--status` says what is in place and needs no rights.

**What is still trusted.** The portable zip's *Unlock all sensors* copies the service from the
zip's folder, which you (and so any program you run) can change: whatever is there when you
answer Windows' question runs as SYSTEM afterwards, as with any program you start as
administrator. The setup copies nothing from a folder of yours. An administrator can always
change the service, as anything else on the PC.

**Taking it away.** *Settings → Sensors → Remove*, the uninstaller, or `libre-panel
system-setup --remove` as administrator: the service is stopped and deleted, its folder
removed (the uninstaller removes the setup's own), and the memberships of *Performance Log
Users* that `system-setup` gave are taken back (it keeps a list of them in
`HKLM\SOFTWARE\Libre Panel`; whoever was in the group before stays). PawnIO stays: other
programs use it too, and it has its own entry under *Settings → Apps*.

## Autostart, per system

Autostart is per user and needs no administrator rights. Turning it off
removes exactly what turning it on created.

| System | Entry |
|---|---|
| Windows | registry value `Libre Panel` under `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run` |
| Linux (and other XDG desktops) | `~/.config/autostart/libre-panel.desktop` |

The entry starts `libre-panel tray --background`, which does not open the
editor. If a panel is not ready yet at login (USB still starting), Libre
Panel keeps looking for it and uses it as soon as it appears. The same switch
is in the app: *Settings → Start and system*.

### Windows: starting with the highest rights

Some sensors (CPU temperature and power, RAM temperature, mainboard fans)
can be read only with administrator rights. The [sensor service](#the-sensor-service)
reads them without giving Libre Panel those rights; where it is not set up, the built-in
LibreHardwareMonitor source reads them inside Libre Panel, so Libre Panel needs the rights
(and the PawnIO driver, see [Windows sensors](CONFIGURATION.md#windows-sensors-gpu-power-fans));
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

In the app this is *Settings → Start and system → With administrator rights*. It is
off until you turn it on, and Libre Panel can set it up only while it runs as an
administrator itself; otherwise the app shows the command to run in a terminal
opened as one. It warns, as the command does, when Libre Panel lies in your user
folder.

**What Libre Panel trusts while it has the highest rights.** It reads `config.toml` and your
plugins from the settings folder and starts or loads what they name with those rights: ffmpeg
(`video.ffmpeg`), PresentMon (`[sensors.presentmon] path`), the libraries of LibreHardwareMonitor
(`dll_dir`) and the plugins in `plugins\`. The programs the download carries itself (ffmpeg and
PresentMon in its `tools` folder, used when the settings name none) lie where Libre Panel does:
they are as safe as its installation. The settings folder is yours: `%APPDATA%\LibrePanel`
can be written by you and by administrators, no other user, so no other user can make Libre Panel
run anything. The programs and libraries it names are better where only administrators can write,
under *Program Files*. Take care with a folder made directly under `C:\`, as `C:\LibrePanelData`
or `C:\Tools` would be: every user of the PC can change what is in it unless you take their
rights away, and that would let another user run a program with administrator rights, whether it
holds the settings, a program or a library. While Libre Panel runs with the highest rights the
app does not change a setting that names a program, a library or a file (`video.ffmpeg`, the
picture file `device.output`, a path under `[sensors.*]`, the files and folders a service's
options name): it says to set it in `config.toml`, which Libre Panel reads from your own folder.

The task reads the settings from `%APPDATA%\LibrePanel` as usual. **A program
started from a Microsoft Store app (an MSIX package) writes `%APPDATA%` into
the package's private folder instead.** Some desktop apps, terminals and
assistants are packaged that way, and so is everything they start: a setting
changed, a theme saved or a plugin installed from there never reaches the
task, which runs outside the package and sees the real `%APPDATA%`. To avoid
that, keep the settings in a folder outside `%APPDATA%` and point Libre Panel
at it with the user environment variable `LIBRE_PANEL_HOME`, for example in a
terminal:

```
setx LIBRE_PANEL_HOME "%USERPROFILE%\LibrePanelData"
```

Libre Panel then keeps `config.toml`, the themes, the plugins and the log
there, however it is started. Move what is in `%APPDATA%\LibrePanel` over
first if you want to keep it; programs that are already running (and the
task, until the next login) still use the old place. Choose a folder in your
profile, as above, not one directly under `C:\` (every user may change those
by default): with the highest rights Libre Panel runs what is in this folder.

### Linux desktops

- **KDE Plasma, Xfce, Cinnamon, MATE, LXQt, Budgie:** the icon appears in
  the tray.
- **GNOME:** GNOME has no tray of its own. Ubuntu includes the *AppIndicator*
  extension, so the icon appears there; on other distributions install the
  "AppIndicator and KStatusNotifierItem Support" extension.
- Without PyGObject (e.g. the release download), Linux trays show a plain
  icon without a menu: a click opens the app's window, which has the same
  controls.
  With PyGObject and AppIndicator installed you get the full menu.
- No tray at all (a server, a minimal window manager): Libre Panel runs
  without an icon; open the app with `libre-panel editor`.

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
through an SSH tunnel: `ssh -L 8765:127.0.0.1:8765 host`). It answers only with its
token ([above](#who-can-talk-to-the-app)): `libre-panel editor --no-browser` on that machine prints
the running Libre Panel's address with it (`http://127.0.0.1:8765/#token=…`), which is what you
open in the browser once the tunnel stands.
