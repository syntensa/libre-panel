# Supported panels

`libre-panel models` prints this list from the code
(`src/libre_panel/devices/models.py`), which is the source of truth.

**Driver status**

- **experimental** — driver exists; the protocol was verified on at least one
  panel of the family, other sizes still need a tester.
- **planned** — the panel is in the catalog (editor menu, sizes), the driver is
  not written yet. Design themes now; they will work once the driver lands.

## Turing Smart Screen / TURZX

| Model id | Size | Resolution (landscape) | Connection | Driver |
|---|---|---|---|---|
| `turing-2.1` | 2.1" round | 480×480 | serial, rev. C | planned |
| `turing-2.8-usb` | 2.8" round | 480×480 | USB, V1.x | experimental |
| `turing-3.5` | 3.5" | 480×320 | serial, rev. A | planned |
| `turing-4.6-usb` | 4.6" | 960×320 | USB, V1.x | experimental |
| `turing-5` | 5" | 800×480 | serial, rev. C | planned |
| `turing-5.2-usb` | 5.2" | 1280×720 | USB, V1.x | experimental |
| `turing-8-usb` | 8" | 1280×800 | USB, V1.x | experimental |
| `turing-8.8` | 8.8" | 1920×480 | serial, rev. C (V0.x) | planned |
| `turing-8.8-usb` | 8.8" | 1920×480 | USB, V1.x | experimental |
| `turing-9.2-usb` | 9.2" | 1920×480 | USB, V1.x | experimental, **verified** |
| `turing-12.3-usb` | 12.3" | 1920×720 | USB, V1.x | experimental |

USB (V1.x) panels identify as VID `0x1CBE` with a size-specific PID; see
[protocol notes](protocol/turzx-usb.md). The 9.2" panel is 480×1920, not the
462×1920 listed elsewhere — measured on the device.

## Compatible panels from other brands

Same electronics, different sticker:

| Model id | Panel | Resolution | Protocol | Driver |
|---|---|---|---|---|
| `usbpcmonitor-3.5`, `usbpcmonitor-5` | UsbPCMonitor | 480×320, 800×480 | Turing rev. A | planned |
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
sudo cp packaging/linux/60-libre-panel.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
```

Serial panels appear as `/dev/ttyACM*`; add yourself to the `dialout` (Debian,
Ubuntu) or `uucp` (Arch) group.

**macOS:** `brew install libusb` for USB panels.

## Help add your panel

Run `libre-panel devices` and open a
["panel support" issue](https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml)
with the output, the product name and a photo of the back label.
