# Supported panels

`libre-panel models` prints this list from the code
(`src/libre_panel/devices/models.py`), which is the source of truth.

**USB or USB serial?** Every panel here plugs in with a USB cable, often
USB-C. They differ in how they show up on the computer:

- **USB panels** (Turing/TURZX V1.x, USB id `1cbe:…`) are a USB device of their
  own; Libre Panel talks to them directly.
- **USB serial panels** (Turing rev. A–D, UsbPCMonitor, XuanFang, Kipye,
  WeAct) have a USB-to-serial chip inside and show up as a virtual COM port
  (USB CDC): `COM3` on Windows, `/dev/ttyACM0` on Linux,
  `/dev/cu.usbmodem…` on macOS. "Serial" means this, not an RS-232 cable.

`libre-panel devices` lists both kinds.

**Driver status**

- **supported** — `libre-panel doctor` passed on this model.
- **unverified** — the driver exists and its protocol is proven on hardware
  (USB panels: by SPUR II on the 9.2"; USB serial panels: by
  [turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python),
  whose protocol Libre Panel follows), but nobody has confirmed this model with
  Libre Panel yet. Please run `libre-panel doctor` and report the result.
- **planned** — the panel is in the catalog (editor menu, sizes), the driver is
  not written yet. Design themes now; they will work once the driver lands.

## Turing Smart Screen / TURZX

| Model id | Size | Resolution (landscape) | Connection | Driver |
|---|---|---|---|---|
| `turing-2.1` | 2.1" round | 480×480 | USB serial, rev. C | unverified |
| `turing-2.8-usb` | 2.8" round | 480×480 | USB, V1.x | unverified |
| `turing-3.5` | 3.5" | 480×320 | USB serial, rev. A | unverified |
| `turing-4.6-usb` | 4.6" | 960×320 | USB, V1.x | unverified |
| `turing-5` | 5" | 800×480 | USB serial, rev. C | unverified |
| `turing-5.2-usb` | 5.2" | 1280×720 | USB, V1.x | unverified |
| `turing-8-usb` | 8" | 1280×800 | USB, V1.x | unverified |
| `turing-8.8` | 8.8" | 1920×480 | USB serial, rev. C (V0.x) | unverified |
| `turing-8.8-usb` | 8.8" | 1920×480 | USB, V1.x | unverified |
| `turing-9.2-usb` | 9.2" | 1920×480 | USB, V1.x | unverified — protocol proven on this panel by SPUR II |
| `turing-12.3-usb` | 12.3" | 1920×720 | USB, V1.x | unverified |

USB (V1.x) panels identify as VID `0x1CBE` with a size-specific PID; see
[protocol notes](protocol/turzx-usb.md). The 9.2" panel is 480×1920, not the
462×1920 listed elsewhere — measured on the device.

## Compatible panels from other brands

Same electronics, different sticker:

| Model id | Panel | Resolution | Protocol | Driver |
|---|---|---|---|---|
| `usbpcmonitor-3.5`, `usbpcmonitor-5`, `usbpcmonitor-7` | UsbPCMonitor | 480×320, 800×480, 1024×600 | Turing rev. A (USB id 1a86:5722) | unverified; a UsbPCMonitor 5" with the serial number CT21INCH is a rev. C panel: `turing-5` |
| `xuanfang-3.5` | XuanFang rev. B / flagship | 480×320 | rev. B | unverified |
| `kipye-3.5` | Kipye Qiye | 480×320 | rev. D | unverified |
| `weact-3.5`, `weact-0.96` | WeAct Studio Display FS V1 | 480×320, 160×80 | WeAct | unverified |

## Lian Li and others

Lian Li LCDs (UNI FAN TL LCD, Galahad II LCD, HydroShift, Universal Screen,
Lancool 207 Digital, …) do **not** use the TURZX protocol. They speak Lian Li's
own HID / wireless / USB protocols, documented by the MIT-licensed
[lian-li-linux](https://github.com/sgtaziz/lian-li-linux) project. Because
Libre Panel separates drivers from themes, a Lian Li driver can be added as its
own package later; the same themes would then work on those screens too.

Not supported and not planned for now: Waveshare USB monitors, GUITION 3.5",
Fuldho 3.5", AX206/AIDA64-style frames.

## Setup per operating system

**All systems:** quit the vendor app (TURZX etc.) — it holds the panel
exclusively. Firmware updates stay the job of the vendor app; Libre Panel never
touches firmware.

**Windows:** USB (V1.x) panels come with the WinUSB driver bound through
their descriptors; no Zadig needed. `pip install "libre-panel[usb]"` also
installs a bundled libusb.

**Linux:** allow your user to access the panel, then replug it:

```bash
libre-panel udev-rules | sudo tee /etc/udev/rules.d/60-libre-panel.rules
sudo udevadm control --reload-rules
```

(With the AppImage: `./Libre_Panel-x86_64.AppImage udev-rules | sudo tee …`.)

USB serial panels appear as `/dev/ttyACM*`; add yourself to the `dialout` (Debian,
Ubuntu) or `uucp` (Arch) group.

**USB serial panels** (Turing 2.1", 3.5", 5" and 8.8", UsbPCMonitor, XuanFang,
Kipye, WeAct) need no driver on Windows 10/11, Linux or macOS; they show up as
a virtual COM port, `/dev/ttyACM*` or `/dev/cu.usbmodem*`. Libre Panel finds the port by the panel's USB ids
(`libre-panel devices` lists what it sees). Only if several devices share
those ids, name the port: `port = "COM5"` under `[device]`. Libre Panel sends
display commands only: no reset, nothing the panel stores
([serial protocols](protocol/serial.md)).

The Turing 2.1", 5" and 8.8" (rev. C) sleep when no program uses them and
then show up with other USB ids; Libre Panel wakes them the way the vendor
app does. Their answer does not say their size reliably. Asleep, a 5" may
call itself `USB7INCH` and an 8.8" `CT88INCH`, which settles it; `CT21INCH`
is the round 2.1" or a 5" sold as UsbPCMonitor 5". Then the theme's size
decides (and failing that, the 5"), and `libre-panel doctor` asks which panel
it is. Set `model = "turing-5"` (or `-2.1`, `-8.8`) to be sure.
Awake, they use generic Linux USB gadget ids, which other devices use too (a
Raspberry Pi as a USB gadget, say): with `model = "auto"` Libre Panel only
talks to such a port when it has the panels' serial number.
A rev. C panel loses an update whose closing bytes fill a 250-byte block or
are split over two (it then answers `needReSend:1` and freezes); Libre Panel
sends such an update in two parts, and a whole frame if the panel asks.
Unlike turing-smart-screen-python, Libre Panel does not send the rev. C
`OPTIONS` command: it also stores the panel's start mode and sleep time. If
a rev. C panel stays dark with Libre Panel, please report it with the
`libre-panel doctor` result.

**macOS:** nothing to install; libusb comes with Libre Panel (the release
download and `pip install "libre-panel[usb]"` alike).

## Check your panel

With a download, in a terminal:

```bash
"%LOCALAPPDATA%\Programs\Libre Panel\libre-panel.exe" doctor     # Windows (cmd)
"/Applications/Libre Panel.app/Contents/MacOS/libre-panel" doctor  # macOS
./Libre_Panel-*-x86_64.AppImage doctor                             # Linux
```

With Python:

```bash
pipx install "libre-panel[usb,serial] @ git+https://github.com/syntensa/libre-panel"
libre-panel doctor
```

Quit the vendor app and a running Libre Panel (tray → Quit) first; `doctor`
needs the panel to itself.

`doctor` uses only the commands normal operation uses (handshake, brightness,
frames). It shows test cards in both orientations and asks what you see,
dims and brightens the panel (then sets your configured brightness again),
measures the frame rate, reconnects once and
writes `libre-panel-doctor-<model>.txt`. The report contains no serial numbers
or personal paths.

Some panels hide a few pixels behind the bezel. `doctor` measures that with a
ruler card: yellow bars along each edge, each starting at the edge and as
many pixels deep as its number (2, 4, … 40). Enter the smallest number whose
bar you can still see; the report lists the hidden pixels per edge (that
number minus 2), so layouts can keep clear of them. Before the brightness
check `doctor` waits for Enter, so you are watching the panel when it dims.

Known so far: the 9.2" hides 18 px at the top in landscape (on the left in
portrait). The editor shows such a strip as a hatched guide and snaps to its
edge; the built-in layouts keep text and values out of it. Nothing is cut
off or moved: a background may run under the bezel. With `rotate = 180`
(upside down) the strip is at the opposite edge of the picture; the layouts
do not know that yet.

## Help add your panel

Run `libre-panel doctor` (USB panels) or `libre-panel devices` and open a
["panel support" issue](https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml)
with the output, the product name and a photo of the back label.
