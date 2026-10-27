# Supported panels

`libre-panel models` prints this list from the code
(`src/libre_panel/devices/models.py`), which is the source of truth.

**USB or USB serial?** Every panel here plugs in with a USB cable, often
USB-C. They differ in how they show up on the computer:

- **USB panels** (Turing/TURZX V1.x, USB id `1cbe:…`) are a USB device of their
  own; Libre Panel talks to them directly.
- **USB serial panels** (Turing rev. A–D, UsbPCMonitor, XuanFang, Kipye,
  WeAct) have a USB-to-serial chip inside and show up as a virtual COM port
  (USB CDC): `COM3` on Windows, `/dev/ttyACM0` on Linux. "Serial" means
  this, not an RS-232 cable.

`libre-panel devices` lists both kinds.

**What a USB serial panel can do.** The USB serial panels are the older kind, and slower: they
take their pictures pixel by pixel over a serial link (115200 baud for most), where a USB panel
takes a compressed picture in a few milliseconds. So Libre Panel keeps their motion calm by
itself (`motion = "auto"` in `[device]`): a picture a second, without gliding, moving symbols or
fades, of which it sends only the parts that changed, as up to four boxes, so numbers, bars and
the clock keep up; a whole picture (at the start, for a new theme) takes seconds and builds up
while it goes. `motion = "full"` (*Settings → Panel → Motion on the panel*) gives a serial panel
the motion of a USB panel, if it keeps up; `"calm"` makes any panel calm. They have no smooth
motion (the video mode is for the TURZX USB panels). The panel test's speed check stops after
20 seconds on them.

**Driver status**

- **supported** — `libre-panel doctor` passed on this model.
- **unverified** — the driver exists and its protocol is proven on hardware
  (USB panels: by SPUR II on the 9.2"; USB serial panels: by
  [turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python),
  whose protocol Libre Panel follows), but nobody has confirmed this model with
  Libre Panel yet. Please run `libre-panel doctor` and report the result. For the panels of
  JONSBO, Hongtai, VMAX, Lian Li, Thermalright, BeadaPanel, NZXT and Thermaltake (and the
  Turing 1.6" and 2.1" USB panels) the protocol comes from other projects' notes and captures
  and from the makers' own sources: nobody has run them with Libre Panel yet, but for the
  JONSBO ZC-360's screens as panels of their own, which are supported (#13).
- **planned** — the panel is in the catalog (editor menu, sizes), the driver is
  not written yet. Design themes now; they will work once the driver lands.

## Turing Smart Screen / TURZX

| Model id | Size | Resolution (landscape) | Connection | Driver |
|---|---|---|---|---|
| `turing-1.6-usb` | 1.6" | 400×400 | USB, V1.x | unverified |
| `turing-2.1` | 2.1" round | 480×480 | USB serial, rev. C | unverified |
| `turing-2.1-usb` | 2.1" round | 480×480 | USB, V1.x | unverified |
| `turing-2.8-usb` | 2.8" round | 480×480 | USB, V1.x | unverified |
| `turing-3.5` | 3.5" | 480×320 | USB serial, rev. A | **supported** — runs, as its owners report |
| `turing-4.6-usb` | 4.6" | 960×320 | USB, V1.x | unverified |
| `turing-5` | 5" | 800×480 | USB serial, rev. C | unverified |
| `turing-5.2-usb` | 5.2" | 1280×720 | USB, V1.x | unverified — stills as JPEG: it shows no PNG |
| `turing-8-usb` | 8" | 1280×800 | USB, V1.x | unverified |
| `turing-8.8` | 8.8" | 1920×480 | USB serial, rev. C (V0.x) | **supported** — verified on the hardware: runs, as its owners report, the latest with a doctor report (#20) |
| `turing-8.8-usb` | 8.8" | 1920×480 | USB, V1.x | **supported** — verified on the hardware: runs, as its owners report |
| `turing-9.2-usb` | 9.2" | 1920×480 | USB, V1.x | **supported** — verified on the hardware: stills and video mode at 50 fps, for hours on end; an owner's doctor report, every check passed (#19) |
| `turing-12.3-usb` | 12.3" | 1920×720 | USB, V1.x | **supported** — verified on the hardware: runs from the first start, as stills and as video |

USB (V1.x) panels identify as VID `0x1CBE` with a size-specific PID; see
[protocol notes](protocol/turzx-usb.md). The 9.2" panel is 480×1920, not the
462×1920 listed elsewhere — measured on the device.

Stills go as PNG where that is proven, on the 9.2" and the 12.3" (about 9 a second without
the video mode). The 5.2" shows nothing of a PNG, so Libre Panel sends it JPEG
stills, which its hardware decodes; `still = "jpeg"` in `[device]` (*Settings → Panel →
Advanced*, *Stills*) does that on any TURZX USB panel. A JPEG still on the 9.2" is 464 pixels
wide, not 480: its decoder works in blocks of 16 and only 462 columns show, and a wider JPEG
wraps round. Libre Panel leaves out the 16 columns beyond, all behind the bezel, and never
stretches the picture ([JPEG stills](protocol/turzx-usb.md#jpeg-stills-101)).

Their video mode (up to 50 frames a second) encodes with ffmpeg, which the Windows and Linux
downloads carry; nothing else is to be installed. With a pip install, install ffmpeg yourself
([Video](CONFIGURATION.md#video)).

The video decoders of the 9.2" and the 12.3" show the video's values as full range (0-255)
whatever the stream says: in limited range, as video usually is, black looked dark grey and the
colours pale in video mode (with the processor's conversion and the graphics card's alike),
while their stills were right. So Libre Panel makes their video in full range by itself
(`[device] video_range = "auto"`); the other panels get limited range. *Settings → Panel → Check the colours → Is black
too light on the panel? Compare* shows any panel both, and writes the one that looks right
([Video](CONFIGURATION.md#video)).

Two or more panels can run at once, a USB one and a serial one or two of a kind, each with a
theme of its own ([More panels at once](CONFIGURATION.md#more-panels-at-once)). Of two
panels of the same kind each finds one; which shows which theme goes by the order the system
lists them in, and for serial panels their port settles it.

## Compatible panels from other brands

Same electronics, different sticker:

| Model id | Panel | Resolution | Protocol | Driver |
|---|---|---|---|---|
| `usbpcmonitor-3.5`, `usbpcmonitor-5`, `usbpcmonitor-7` | UsbPCMonitor | 480×320, 800×480, 1024×600 | Turing rev. A (USB id 1a86:5722) | unverified; a UsbPCMonitor 5" with the serial number CT21INCH is a rev. C panel: `turing-5` |
| `xuanfang-3.5` | XuanFang rev. B / flagship | 480×320 | rev. B | unverified |
| `kipye-3.5` | Kipye Qiye | 480×320 | rev. D | unverified |
| `weact-3.5`, `weact-0.96` | WeAct Studio Display FS V1 | 480×320, 160×80 | WeAct | unverified |

**What the newer panels cannot do.** *Settings → Panel* says it under the panel's name, and
the panel lists show it when you point at one.

| Panel | Found by `auto` | Smooth motion | Backlight | Dark on quit (`dark_on_quit`) | Good to know |
|---|---|---|---|---|---|
| JONSBO ZC-360 | yes | no | its own (0–100) | yes | the three screens take their pictures one after the other: some 8 a second when all three change |
| JONSBO TF3-360SC pump, Turing 1.6" and 2.1" USB | yes | not proven | its own | yes | TURZX USB panels inside |
| JONSBO DS916 | yes | no | none: dimming darkens the picture | no: it shows its own animation again | whole JPEG pictures; fine detail may soften |
| JONSBO DS339, VMAX 4.6" | no: choose the model | no | none: dimming darkens the picture | yes: turned off | on Windows the maker's driver, which its app installs |
| Hongtai / Jungle Leopard | yes | no | its own (0–100) | no: it shows its own animation again | whole JPEG pictures of at most 80 KB over a serial port: a few a second, and a busy picture loses detail |
| Lian Li | yes | no | its own (0–100) | not known yet | whole JPEG pictures; close L-Connect while Libre Panel runs |
| Thermalright Trofeo Vision | yes | no | none: dimming darkens the picture | no: it shows its own picture again | whole JPEG pictures; close TRCC while Libre Panel runs |
| BeadaPanel | yes | no | its own | yes: the backlight off | whole pictures in 16-bit colour, a few a second |
| NZXT Kraken (LCD) | yes | no | its own (0–100) | yes: brightness 0 | each picture goes through the cooler's memory: a few a second; close NZXT CAM |
| Thermaltake 6" LCD | yes | no | none known: dimming darkens the picture | no: it shows its own screen again | whole JPEG pictures; close TT RGB Plus |

For all of them: close the maker's own program first. Two programs cannot drive one panel at
once.

## JONSBO

### ZC-360

The ZC-360 (and the fans of the TF3-360) carries three 3.38" screens of 640×180, one under
each of its three 120 mm fans. Each screen is a USB device of its own (`43a8:0e61`,
"TURZX-338inch-r"); Libre Panel drives the three as **one panel of 1920×180**, the screens
side by side (`device.model = "jonsbo-zc360"`, or found by `auto`), and shows Lumen in a
version for it: what plays, the time, CPU and GPU, a part on each screen.

- The screens are written one after the other, as the vendor's protocol is known to stay
  clean that way; a screen whose part did not change is not written. Expect some 8 frames a
  second when all three change, and more when one does.
- Their order from left to right is that of their serial numbers. *Which is which?* (*Settings →
  Panels*) shows each screen its number; where the parts sit in the wrong order, `device.port = "2,1,3"` puts the
  left part on screen 2, the middle one on screen 1 and the right one on screen 3.
- Close the vendor's app first: a screen has one owner at a time. Should a screen show parts of
  the JONSBO logo under the picture after much plugging and unplugging, switch the PC off and
  on again: the controllers keep that state until they lose power.
- On Linux, `libre-panel system-setup --linux` gives your user access to the screens as it does
  to the TURZX panels.

**Each screen a panel of its own.** *Settings → Panels* turns the one picture into three panels
with one click (*Each screen its own panel*), and back (*Back to one picture*): each screen then
shows a theme of its own, the header's panel switch chooses the one that *Live* shows and
*Design* edits, and screens 2 and 3 follow screen 1's brightness. They start with Lumen for
640×180: CPU, GPU and memory, each a lit ring beside its history; the time, what plays, the time
with the CPU, and CPU and GPU together are there too. Behind the click: `device.model =
"jonsbo-zc360-screen"` with `device.port` "1", "2" or "3", the screen by the place it hangs
on in the ZC-360's own USB hub (*Which is which?* shows the numbers). The three still take
their pictures one after the other, also from the panels' own processes; the four panels
Libre Panel runs at most leave room for one more beside them. This way is **supported**: an
owner ran the doctor on each of the three screens (#13, Windows), all checks passed, at 22 to
24 full frames a second each, with 2 pixels hidden at the left and at the right of each screen.

The protocol comes from the notes of the MIT-licensed
[jonsbo-zc360-linux-driver](https://github.com/RimuruNava/jonsbo-zc360-linux-driver).

### JONSBO TF3-360SC

The TF3-360SC (an AIO) has four screens: the ZC-360's three under its fans (above), and a
3.5" screen of 640×480 on the pump, a TURZX USB panel inside (`1cbe:0035`,
`device.model = "jonsbo-tf3-pump"`, or found by `auto`) that Libre Panel drives like the other
TURZX panels. Show something on both: the pump as the first panel and the fans as a panel
beside it (*Settings → Panels*), or the other way round. Both are unverified; that the pump is
a TURZX panel comes from the notes of the MIT-licensed
[jonsbo-tf3-linux-driver](https://github.com/purplebrain23/jonsbo-tf3-linux-driver).

### JONSBO DS916 and DS339

Neither has a TURZX controller inside; their protocols come from the JONSBO drivers of
[InfoPanel](https://github.com/habibrehmansg/infopanel) (GPL-3.0). Both are unverified.

- **DS916** (9.16", 1920×462, `device.model = "jonsbo-ds916"`, or found by `auto`): a USB
  serial port (`33c3:f101`) that takes whole JPEG pictures. It shows Lumen like the 9.2" bars.
  When it gets no picture for a moment it shows its own animation again, so Libre Panel sends
  the last picture again while nothing changes. It has no command for its backlight, so the
  brightness goes into the pixels, and none to turn its screen off: after Libre Panel quits
  it shows its own animation.
- **DS339** (3.39", 960×376, `device.model = "jonsbo-ds339"`): a USB display chip, the
  MacroSilicon MS9132 (`345f:9132`). USB-to-HDMI adapters have the same chip, so `auto` never
  opens it; choose the model in *Settings → Panel*. On Windows its pictures need the driver
  that JONSBO's app installs (install the app once, then close it); on Linux,
  `libre-panel system-setup --linux` gives your user access to it. The brightness goes into
  the pixels; quitting with `dark_on_quit` turns it off.

## Hongtai / Jungle Leopard

Cooler screens on Hongtai's firmware: the Jungle Leopard Chill Arc 360 (480×960) and the
strip of the family (1920×462), and the white-label screens that carry it (MSI EZ Display,
Thermaltake, ZOTAC and others). They are a USB serial port (`33c3:7788`, found by `auto`)
that takes JPEG pictures; the panel says its size, which tells the two models apart. Other
white-label product ids of the family run once `device.model` and `device.port` name them.
The backlight is the panel's own (0–100). The protocol comes from
[InfoPanel](https://github.com/habibrehmansg/infopanel) (GPL-3.0); unverified. Libre Panel
never sends the commands that restart the panel, update its firmware, change its serial number,
region or screen timeouts, or switch the pump's motor.

## VMAX 4.6"

The VMAX 4.6" (960×320) is a MacroSilicon MS9132 USB display chip like the JONSBO DS339's and
runs on the same driver: choose `vmax-4.6` (*Settings → Panel*); `auto` does not open the chip.
Unverified.

## Thermalright Trofeo Vision

| Model id | Size | Resolution (landscape) | Connection | Driver |
|---|---|---|---|---|
| `thermalright-trofeo-6.86` | 6.86" | 1280×480 | USB HID (`0416:5302`) | unverified |
| `thermalright-trofeo-9.16` | 9.16" | 1920×462 | USB (`0416:5408`) | unverified |
| `thermalright-trofeo-11.3` | 11.3" | 1920×440 | USB (`0416:5408`) | unverified |

Found by `auto`; the screen says which one it is when it is opened (the 9.16" and the 11.3"
share their USB id). Libre Panel sends JPEG pictures as Thermalright's own program (TRCC)
does: the 9.16" gets 1920×462 although it calls itself 1920×480, and pictures are turned
the way TRCC turns them for the screen's variant. Neither has a command for its backlight
that is known, so the brightness goes into the pixels; a bar that gets no picture for a few
seconds shows its logo again, so the last picture goes again twice a second. Close TRCC first.
On Linux, `libre-panel system-setup --linux` gives your user access to both. The protocol
comes from the MIT-licensed [turing-smart-screen](https://github.com/usausa/turing-smart-screen)
(.NET, by usausa) and [InfoPanel](https://github.com/habibrehmansg/infopanel) (GPL-3.0); see
the [protocol notes](protocol/thermalright.md). Unverified.

Not yet: the 9.16" with the LY1 controller (`0416:5409`), which only InfoPanel documents, and
Thermalright's other screens (Frozen Warframe, Elite Vision, the AIO pump screens and others on
`0416:5302`, `0416:5406`, `87ad:70db` and as USB mass storage), whose pictures differ. One of
them on `0416:5302` says what it is when asked: Libre Panel names it in the log, leaves it
alone and looks on for other panels.

## BeadaPanel

| Model id | Size | Resolution (landscape) | The maker's models | Driver |
|---|---|---|---|---|
| `beadapanel-2` | 2.1" round | 480×480 | 2 | unverified |
| `beadapanel-2w` | 2.8" round | 480×480 | 2W | unverified |
| `beadapanel-3` | 3.5" | 480×320 | 3, 3C | unverified |
| `beadapanel-4` | 4.3" | 800×480 | 4, 4C | unverified |
| `beadapanel-5` | 5" | 800×480 | 5, 5C, 5T | unverified |
| `beadapanel-5s` | 5" | 854×480 | 5S | unverified |
| `beadapanel-6` | 6.8" | 1280×480 | 6, 6C, 6S | unverified |
| `beadapanel-7` | 7" | 800×480 | 7, 7C | unverified |
| `beadapanel-7s` | 7.8" | 1280×400 | 7S | unverified |
| `beadapanel-8` | 8.8" | 1920×480 | 8, Y | unverified |
| `beadapanel-9` | 9.16" | 1920×462 | 9, Z | unverified |
| `beadapanel-11` | 10.2" | 1920×440 | 11, X | unverified |

Every BeadaPanel is the same USB device (`4e58:1001`, found by `auto`); the panel says which
model it is and the size of its pictures. Libre Panel sends the plain Panel-Link stream that
every model takes: whole pictures in 16-bit colour (RGB565), a few a second, so soft
gradients show steps (the maker's faster "write-through" stream is not used). The backlight
is the panel's own; `dark_on_quit` turns it off. Close the maker's program and AIDA64 first.
On Windows the panel needs no driver of its own (it comes up as a WinUSB device); on Linux,
`libre-panel system-setup --linux` gives your user access to it. The sizes in inches are the
diagonals of the millimetres in the maker's Linux driver (the 3's and the 7's as the maker
announced them). The protocol comes from the maker's own sources (NXElec's demos and Raspberry
Pi daemon, the [beada](https://github.com/JT365/beada) Linux driver) and
[InfoPanel](https://github.com/habibrehmansg/infopanel); see the
[protocol notes](protocol/beadapanel.md). Unverified.

## NZXT Kraken with an LCD

| Model id | Size | Resolution | USB id | Driver |
|---|---|---|---|---|
| `nzxt-kraken-z` | 2.36" round | 320×320 | `1e71:3008` (Kraken Z53, Z63, Z73) | unverified |
| `nzxt-kraken-2023` | 1.54" round | 240×240 | `1e71:300e` (Kraken 2023) | unverified |
| `nzxt-kraken-elite-2024` | 2.72" round | 640×640 | `1e71:3012` (Kraken Elite RGB 2024) | unverified |
| `nzxt-kraken-plus-2024` | 1.54" round | 240×240 | `1e71:3014` (Kraken Plus 2024) | unverified |

The screen on the pump, found by `auto`. Libre Panel writes only the screen's commands, never
the pump's or the fans' (set those with the mainboard, liquidctl or CoolerControl, which can run
beside it as long as only one program drives the screen). Each picture goes into the cooler's
picture memory first (two of its 16 "buckets" in turn, which it forgets when it loses power)
and is then shown: a few pictures a second. The cooler shows uploaded pictures as they are, so
Libre Panel turns them the way the cooler is set to turn its own screens (NZXT CAM sets that).
The brightness is the cooler's own; `dark_on_quit` sets it to 0, and when Libre Panel quits
the cooler shows the liquid's temperature again. Close NZXT CAM first.

**Linux.** The cooler is two USB interfaces: the pictures go to its vendor interface (USB access
as for the other panels), its screen's commands through its HID interface, which stays with the
kernel (the `nzxt-kraken3` hwmon driver reads the liquid's temperature through it, and Libre
Panel can show that sensor). Libre Panel opens that interface's `/dev/hidraw*` node, so your
user needs access to it too: the udev rule of `libre-panel system-setup --linux` gives both
(`SUBSYSTEM=="usb"` and `KERNEL=="hidraw*"` for these ids). Nothing is detached from the kernel.
On Windows liquidctl reaches the vendor interface through WinUSB with no driver to replace (its
list of devices gives the Krakens no Zadig note); unverified with Libre Panel.

The protocol comes from [liquidctl](https://github.com/liquidctl/liquidctl) (GPL-3.0,
`kraken3.py`) and [krakenlcd](https://github.com/ossini/krakenlcd) (GPL-3.0, verified on a
Kraken Z53); see the [protocol notes](protocol/nzxt-kraken.md). Not yet: the Kraken Elite 2023
(`1e71:300c`), whose LCD liquidctl lists as broken; the Kraken X models have no screen.

## Thermaltake

| Model id | Size | Resolution (landscape) | Connection | Driver |
|---|---|---|---|---|
| `thermaltake-lcd-6` | 6" | 1480×720 | USB HID (`264a:2347`) | unverified |

The 6" LCD that InfoPanel names after the TOUGHLIQUID Ultra, found by `auto`: Libre Panel
connects to it with its text commands ("POST conn", "POST realtimeDisplay") and sends it JPEG
pictures in HID reports, as Thermaltake's TT RGB Plus does; when Libre Panel quits, the screen
goes back to the panel. A brightness command is known but not proven (InfoPanel does not send
it either), so the brightness goes into the pixels. Close TT RGB Plus first. On Linux,
`libre-panel system-setup --linux` gives your user access to it. The protocol comes from
[InfoPanel](https://github.com/habibrehmansg/infopanel) (GPL-3.0); see the
[protocol notes](protocol/thermaltake.md). Unverified.

Some of Thermaltake's screens carry Hongtai's firmware (USB serial `33c3:7788`) and run as
[Hongtai screens](#hongtai--jungle-leopard). **Not yet:** the others, such as the LCD Panel Kit
and the pump screens of the Floe Ultra and TOUGHLIQUID Ultra coolers where they are neither the
6" above nor Hongtai screens: no source we could read documents a protocol for them (InfoPanel
has the 6" alone; turing-smart-screen-python and liquidctl have none), and Libre Panel does not
guess one. A `libre-panel devices` listing and the product's name in a
["panel support" issue](https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml)
help.

## Lian Li and others

The **Lian Li Universal Screen** (8.8", 9.2") and the **HydroShift II** LCD (480×480) and OLED
Curve (2288×1080) speak a protocol close to TURZX's (`1cbe:a088`, `a092`, `a034`, `a068`,
found by `auto`): Libre Panel sends them JPEG pictures to their background layer, clears the
layer above it once, and waits when the panel says pictures pile up. As
[InfoPanel](https://github.com/habibrehmansg/infopanel) documents it from the MIT-licensed
[lian-li-linux](https://github.com/sgtaziz/lian-li-linux) driver; unverified.

Lian Li's other LCDs (UNI FAN TL LCD, Galahad II LCD, Lancool 207 Digital, …) speak Lian Li's
own HID and wireless protocols, which lian-li-linux documents; they have no driver here yet.

Not supported and not planned for now: GUITION 3.5", Fuldho 3.5", AX206/AIDA64-style frames.

## Waveshare USB monitors (experimental)

Waveshare's USB monitors (the 2.8" and its family) are small Linux computers of their own: they
draw the themes on their card (PCMonitorLinux) and take from the PC only the values to show,
over a serial port on USB. So Libre Panel does not draw on them; the service *Waveshare USB
monitor* (*Settings → Game mode and more*) sends them its readings once a second, under the
keys Waveshare's themes use (in English and in Chinese), sets the brightness and turns the
picture upside down where you ask, and switches between the themes on the card: one to show, or
several in turn at random. Libre Panel's own themes do not show on them, and nothing is
written on the card. The protocol is the one Derik-for-real24 documented in the wsmonitor
project (#21); with Libre Panel 0.7.1 his 2.8" shows Libre Panel's readings live in its own
themes (Linux, #21).

- The display is looked for by its USB ids (`1d6b:0104` "Virtual Serial Port", or the
  placeholder `1234:5678` some units have) and taken only once it answered the display's own
  handshake. A port can be named instead (`port`).
- Waveshare's own program (or wsmonitor's) and Libre Panel cannot both have the display: quit
  the other one. wsmonitor's service starts at login: `systemctl --user disable --now
  wsmonitor.service` stops it for good. On Linux Libre Panel sees a program that has the port
  open and says so on its card instead of writing over it.
- **Linux:** *Settings → Sensors → Set up sensors and panel access* gives your user the port,
  and on units with `1234:5678` hands the display from the kernel's `usbtouchscreen` driver,
  which takes those ids for a touchscreen, to `cdc_acm`, which makes `/dev/ttyACM*`. Plug the
  display in again afterwards. A real touchscreen with those ids is left alone: the rule only
  moves the display's serial interfaces.

### As a USB display (udisp)

Waveshare's wiki also offers, for each monitor, a card image that makes it a plain USB display:
it no longer shows the themes on its card, it shows what the PC sends, whole pictures over USB.
Libre Panel then drives it as one of its own panels, with its own themes, the way up and the
brightness set in Libre Panel, and a video stream of raw pictures, 30 a second as a TURZX panel
in video mode gets them (`device.model = "waveshare-2.8-udisp"`, or found by `auto`). The service above has nothing to
do with it then.

- It shows up as a USB display, `303a:2986`, whose product string says its size and what it
  takes ("t113udisp1_R480x480_Ejpg5_Ergb16_Ergb32" on the 2.8" round one, #21). Libre Panel
  finds it by that string; a size it does not know yet is not taken.
- Libre Panel streams the pictures as raw pixels (RGB565), every pixel as it was drawn: no
  JPEG blurs fine lines or edges. The stream runs at the frames a second of a TURZX panel in
  video mode (`[video] fps`, 30 by default, up to 60), fewer where the display says so. Libre
  Panel times what the display takes: where raw pixels stay under 25 a second (an ESP32 display
  at USB's full speed), RGB888 and then JPEG are timed too; the first that goes smoothly is
  kept, else the fastest, and the stream runs no faster than it goes. The log says what each
  came to.
- No command for the backlight is known, so the brightness goes into the picture: 100 % is as
  bright as the display's card image sets its backlight, and less is darker.
- Close any other program that drives the display first.
- **Linux:** *Settings → Sensors → Set up sensors and panel access* gives your user the display,
  as it does the other panels. **Windows:** the display brings no driver of its own; it needs
  WinUSB bound to it (Zadig, for example).
- Going back to the themes on the card means writing Waveshare's own card image (or a backup of
  the card) again.
- The protocol: [udisp](protocol/udisp.md).

## Setup per operating system

**All systems:** quit the vendor app (TURZX etc.) — it holds the panel
exclusively. Firmware updates stay the job of the vendor app; Libre Panel never
touches firmware.

**Windows:** USB (V1.x) panels come with the WinUSB driver bound through
their descriptors; no Zadig needed. `pip install "libre-panel[usb]"` also
installs a bundled libusb.

**Linux:** your user needs permission to use the panel. *Start* offers it as one click while the
panel cannot be used (and *Settings → Sensors* always has it): your system asks for the
administrator's password once, and the rule is applied to a panel that is plugged in, so it need
not be replugged. The same in a terminal, with the AppImage too
(`./Libre_Panel-x86_64.AppImage system-setup --linux`):

```bash
libre-panel system-setup --linux
```

That also loads the driver for the mainboard's fan chip; [what it does exactly](CONFIGURATION.md#linux-sensors-and-panel-access).
By hand, only the rule, then replug the panel:

```bash
libre-panel udev-rules | sudo tee /etc/udev/rules.d/60-libre-panel.rules
sudo udevadm control --reload-rules
```

USB serial panels appear as `/dev/ttyACM*`; add yourself to the `dialout` (Debian,
Ubuntu) or `uucp` (Arch) group.

**USB serial panels** (Turing 2.1", 3.5", 5" and 8.8", UsbPCMonitor, XuanFang,
Kipye, WeAct) need no driver on Windows 10/11 or Linux; they show up as a
virtual COM port or `/dev/ttyACM*`. Libre Panel finds the port by the panel's
USB ids (`libre-panel devices` lists what it sees). Only if several devices
share those ids, name the port: `port = "COM5"` under `[device]`. Libre Panel
sends display commands only: no reset, nothing the panel stores
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

## Check your panel

In the app: *Live → Diagnose → Panel test*. Libre Panel gives the panel to the test while it
runs, shows the test pictures, asks what you see with buttons and writes the report; *Report
the panel as working* (or *Report the problem*) then opens the form on GitHub with the report
filled in, and you send it. The same tab has *Check and fix* (it mends what Libre Panel can
mend by itself), *Restart the panel* (it lets the panel go and connects it again; no command
restarts the panel itself) and the log, which it copies and saves without the names of your
folders.

With a download, in a terminal:

```bash
"%LOCALAPPDATA%\Programs\Libre Panel\libre-panel.exe" doctor  # Windows (cmd)
./Libre_Panel-*-x86_64.AppImage doctor                         # Linux
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
off or moved: a background may run under the bezel (with JPEG stills the
9.2" gets only 2 of those 18 rows, see above). With `rotate = 180`
(upside down) the strip is at the opposite edge of the picture; the layouts
do not know that yet.

## What Libre Panel costs your PC

*Live → Diagnose → Load* shows it while the card is on screen, measured every two seconds;
when it is not on screen nothing is measured at all.

- **At the top**: Libre Panel's share of the processor, of the whole PC, and the memory of
  all its processes together: their private memory, and below it their working set.
- **The processes**: the main one (the first panel's loop, the sensors, the app's server, the
  tray, the shortcuts, the weather), one for each further panel, the video encoder (ffmpeg) of
  each panel in the video mode, PresentMon while it reads a game's frame rate, the window and
  its browser engine (WebView2) while it is open, and the sensor service. Each with its share
  of the PC, its share of one core, its private memory and its working set.
- **The threads of the main process**, folded: which part of Libre Panel spends the time
  (`panel` draws the first panel, `panel-video` sends its video, `lhm-service` asks the
  sensor service, `sensors-load` measures the load ten times a second, `editor` takes the
  app's requests). *Other threads* are the ones without a name of Libre Panel's (each request
  of the app, .NET's, a library's) and those that ended meanwhile. The other processes are shown whole: their threads cannot
  be named from outside.
- **LibreHardwareMonitor's rounds**: what the sensor service (or LibreHardwareMonitor inside
  Libre Panel) took in its last round, once a second for the parts the themes on show read,
  and in its last round of all hardware (now and then), with the three slowest parts. A
  sensor service set up from an older version says *Not reported by this service version*:
  set it up again (*Settings → Sensors*) to see them.

How to read it: *of the PC* is what the Task Manager shows, the whole PC being 100 % (on a
PC with 16 logical processors, one core busy all the time is 6.25 %). *Of one core* is the
same time as a share of one core, which is how psutil and `top` count. *Memory* is each
process's private memory, what is its alone: the Task Manager's *Memory* column (on its
*Details* tab *Memory (active private working set)*), measured every six seconds, as it costs
a little more to measure than the rest. The *working set* beside it is larger: it counts what
processes share (the libraries they load, what WebView2's processes share among them) once for
each, so the working sets of all of them add up to more than the PC lends them. The sensor
service runs as the system, and Windows does not let a program without administrator rights
read its private memory: it says – there, and the total is of the others. The Task Manager's
*Processes* tab adjusts the share of the processor to the processor's clock, so it may differ a
little.

Where the memory goes:

- **A panel's loop** keeps what it has drawn, so that a frame on the move costs little: the
  panel's ground (its background picture), the frame before, every widget's picture, and its
  caches: the letters, the numbers a value glides through, the fans at each of their positions,
  the bars of the lists, the lit rings' light, what lies under a graph that moves. Each cache
  holds as many megabytes as the themes need and no more, so a theme that runs for a day keeps
  what it kept after a minute. The lit bodies of the weather symbols (ten megabytes) go a minute
  after a symbol was made from them and are read again when the weather changes. Measured with
  a virtual panel in the video mode at 30 frames a second, Lumen's loop on the 12.3" takes some
  110 MB of private memory, Lumen Companion's on the 9.2" some 75, Arena's 93 and 78, Encore's
  84; some 40 of them are Python, numpy and Pillow, which every process of Libre Panel loads.
- **A panel beside the first** is a process of its own that loads only what it draws with: no
  app server, no sensor source (the first panel's process sends it the readings), no tray, no
  window and no .NET. It takes what its loop takes.
- **The main process** holds the first panel's loop, the sensors and the app's server. The
  pictures the app drew of every theme (Start) go a minute after its window was closed; with
  [light mode](CONFIGURATION.md#light-mode) the caches the app filled while it was open go too,
  and the memory goes back to the system.
- **The window** starts WebView2 with one renderer process, without a spare one and without
  caches on the disk for its pages and the GPU's shaders. While it is minimized or hidden the
  app asks Libre Panel nothing; closed, its processes end. (An app started with the highest
  rights, *autostart --elevated*: Windows ignores those arguments for it, and its window opens
  as it did.)

*Copy as text* copies the same numbers in English, without your names or folders, for an
issue; the report of the panel test carries them too (as they were before the test).
Measuring itself costs a few milliseconds every two seconds; the card says how many.

## Help add your panel

Run `libre-panel doctor` (USB panels) or `libre-panel devices` and open a
["panel support" issue](https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml)
with the output, the product name and a photo of the back label.
