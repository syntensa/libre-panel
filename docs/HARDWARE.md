# Supported panels

`libre-panel models` prints this list from the code
(`src/libre_panel/devices/models.py`), which is the source of truth.

**Driver status**

- **supported** — `libre-panel doctor` passed on this model.
- **unverified** — the driver exists and its protocol is proven on hardware
  (USB panels: by SPUR II on the 9.2"; serial panels: by
  [turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python),
  whose protocol Libre Panel follows), but nobody has confirmed this model with
  Libre Panel yet. Please run `libre-panel doctor` and report the result.
- **planned** — the panel is in the catalog (editor menu, sizes), the driver is
  not written yet. Design themes now; they will work once the driver lands.

## Turing Smart Screen / TURZX

| Model id | Size | Resolution (landscape) | Connection | Driver |
|---|---|---|---|---|
| `turing-2.1` | 2.1" round | 480×480 | serial, rev. C | planned |
| `turing-2.8-usb` | 2.8" round | 480×480 | USB, V1.x | unverified |
| `turing-3.5` | 3.5" | 480×320 | serial, rev. A | unverified |
| `turing-4.6-usb` | 4.6" | 960×320 | USB, V1.x | unverified |
| `turing-5` | 5" | 800×480 | serial, rev. C | planned |
| `turing-5.2-usb` | 5.2" | 1280×720 | USB, V1.x | unverified |
| `turing-8-usb` | 8" | 1280×800 | USB, V1.x | unverified |
| `turing-8.8` | 8.8" | 1920×480 | serial, rev. C (V0.x) | planned |
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
| `usbpcmonitor-3.5`, `usbpcmonitor-5`, `usbpcmonitor-7` | UsbPCMonitor | 480×320, 800×480, 1024×600 | Turing rev. A | unverified |
| `xuanfang-3.5` | XuanFang rev. B / flagship | 480×320 | rev. B | planned |
| `kipye-3.5` | Kipye Qiye | 480×320 | rev. D | planned |
| `weact-3.5`, `weact-0.96` | WeAct Studio Display FS V1 | 480×320, 160×80 | WeAct | planned |

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

Serial panels appear as `/dev/ttyACM*`; add yourself to the `dialout` (Debian,
Ubuntu) or `uucp` (Arch) group.

**Serial panels** (rev. A: Turing 3.5", UsbPCMonitor) need no driver on
Windows 10/11, Linux or macOS; they show up as a COM port, `/dev/ttyACM*` or
`/dev/cu.usbmodem*`. Libre Panel finds the port by the panel's USB ids
(`libre-panel devices` lists what it sees). Only if several devices share
those ids, name the port: `port = "COM5"` under `[device]`. Libre Panel sends
display commands only: no reset, nothing the panel stores.

**macOS:** nothing to install; libusb comes with Libre Panel (the release
download and `pip install "libre-panel[usb]"` alike).

## Check your panel

```bash
pip install "libre-panel[usb]"
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
off or moved: a background may run under the bezel.

## Help add your panel

Run `libre-panel doctor` (USB panels) or `libre-panel devices` and open a
["panel support" issue](https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml)
with the output, the product name and a photo of the back label.
