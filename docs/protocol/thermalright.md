# Thermalright Trofeo Vision

Applies to the Trofeo Vision 6.86" (1280×480, USB `0416:5302`, a HID device) and the
Trofeo Vision bars, 9.16" (1920×462) and 11.3" (1920×440) (USB `0416:5408`, bulk transfers).
Thermalright's own program for them is TRCC. Driver: `src/libre_panel/devices/thermalright.py`.

Sources, read in October 2026:

- [usausa/turing-smart-screen](https://github.com/usausa/turing-smart-screen) (MIT, .NET),
  commit `da3063b`: `LcdDriver.TrofeoVision/ScreenDevice.cs` and `DeviceInfo.cs` (the 6.86",
  marked as tested by its author), `LcdDriver.TrofeoVisionLy/ScreenDevice.cs` and
  `DeviceInfo.cs` (the bars; the 9.16" marked as tested), `Example/Commands.cs` (how often a
  picture goes, the JPEG's size).
- [habibrehmansg/infopanel](https://github.com/habibrehmansg/infopanel) (GPL-3.0), commit
  `8a3062d`: `InfoPanel/ThermalrightPanel/HidPanelDevice.cs`,
  `InfoPanel/Services/ThermalrightPanelDeviceTask.cs` (`DoWinUsbTrofeoBulkProtocol`) and
  `ThermalrightPanelModelDatabase.cs`, made from the vendor's application.

Nothing here was seen on a screen by Libre Panel yet. Where the two sources differ, this says
which one the driver follows.

## The 6.86" (HID, `0416:5302`)

Reports of 512 bytes on the HID interface (no report id; HidSharp writes 513 with the id 0
in front). The device has a vendor interface beside it, which TRCC uses for the pictures
(InfoPanel too, where a driver is bound to it); usausa sends them as HID reports, as InfoPanel
does otherwise, and so does Libre Panel.

**The question.** A report with `DA DB DC DD` at bytes 0–3 and `01` at byte 12, zeros
elsewhere. The answer (among the next reports; usausa reads up to eight) begins with `DA DB DC
DD` and has `01` at byte 12:

| Byte | Meaning |
|---|---|
| 4 | the variant (usausa's `Sub`) |
| 5 | the kind (usausa's `PM`): `0x80` is the 6.86" |
| 20–27 | an identifier (`BP21940`, InfoPanel) |

Other Thermalright screens answer on the same id with other kinds (InfoPanel lists over a
dozen, some of them taking RGB565 instead of JPEG); Libre Panel drives the kind `0x80` only
and says so for the others.

**A picture.** A JPEG of 1280×480. The first report:

| Bytes | Content |
|---|---|
| 0–3 | `DA DB DC DD` |
| 4 | `02` (a picture) |
| 8–9 | the width, 1280, little endian |
| 10–11 | the height, 480, little endian |
| 12 | `02` (JPEG) |
| 16–19 | the JPEG's length, little endian |
| 20–511 | the JPEG's first 492 bytes |

Each next report carries the next 512 bytes of the JPEG; the last is filled up with zeros.
The screen does not answer. Where the variant is 2, TRCC turns the picture a quarter
clockwise (a JPEG of 480×1280; the header still says 1280×480).

## The bars (bulk, `0416:5408`)

Interface 0, one bulk OUT and one bulk IN endpoint, packets of 512 bytes. The driver takes the
first OUT and the first IN endpoint of interface 0, as InfoPanel does (usausa names them).

**The question.** 2048 bytes: `02 FF` at bytes 0–1, `01` at byte 8, zeros elsewhere. The answer
is 512 bytes:

| Byte | Meaning |
|---|---|
| 0–1, 8 | `03 FF`, `01` |
| 16–19 | a serial number (not read) |
| 20 | the kind: 3 or less is the 9.16"; 4 a 6.86" (1280×480); 5 the 11.3" |
| 22 | the variant, one less than usausa counts it |
| 24–31 | a width and a height (32 bit each; the 9.16" says 1920×480) |

usausa numbers the kinds 64 + byte 20 (65 where byte 20 is 3 or less), which gives 65 (9.16"),
68 (6.86") and 69 (11.3"); InfoPanel's table of TRCC's models has the same sizes for them.

**A picture.** A JPEG, cut into packets of 512 bytes, each a header of 16 bytes and 496 bytes
of the JPEG:

| Bytes | Content |
|---|---|
| 0–1 | `01 FF` |
| 2–5 | the JPEG's length (32 bit) |
| 6–7 | the length of this packet's part (16 bit; 496 but in the last) |
| 8 | `01` |
| 9–10 | the number of packets (16 bit) |
| 11–12 | this packet's number, from 0 (16 bit) |
| 16–511 | the part, the last one filled up with zeros |

All little endian. Empty packets (zeros) fill the packets up to a multiple of four; they go in
bulk writes of 4096 bytes. Then the bar answers 512 bytes, `03 FF` with `01` at byte 8.
InfoPanel gives up after five pictures in a row without an answer; so does Libre Panel.

usausa counts the packets as length / 496 + 1, InfoPanel as the length divided by 496,
rounded up; they differ only for a JPEG of a whole number of packets, where InfoPanel's way
(which it took from TRCC) is followed.

**The pictures' size and turn.** TRCC sends the 9.16" pictures of 1920×462, though the bar says
1920×480: some bars have 462 rows, and a taller picture runs over them (InfoPanel, "flicker
fix"; usausa's notes give 1920×462 too). The 11.3" gets 1920×440. TRCC turns the bars'
pictures half round unless the variant (as usausa counts it) is 2 or 4, and a 6.86"'s a quarter
clockwise where its variant is 2 (usausa's `DeviceInfo.GetRotateOption`; InfoPanel leaves the
turn to its user). The person's own `rotate = 180` comes on top.

## Both

- JPEG with 4:2:0 chroma: InfoPanel found that some firmware cannot decode 4:4:4 fast
  enough. Libre Panel keeps a picture at 230 KB or less (InfoPanel's cap; TRCC drops pictures
  of 450 KB), lowering the quality from 90 as far as 50.
- No command for the backlight is known in either source: the brightness goes into the
  pixels.
- A bar that gets no picture for a few seconds shows its logo again (usausa); Libre Panel sends
  the last picture again after half a second without one, the 6.86"'s after a second (usausa
  sends it every second).
- No other command is known, and none other is sent.

## Not covered

- The 9.16" with the LY1 controller (`0416:5409`): InfoPanel alone documents it (a question of
  512 bytes, `02` at byte 8 of each packet, no filler packets) and draws it at 1920×480.
- Thermalright's other screens: the HID kinds other than `0x80`, the ALi chipset
  (`0416:5406`), the "ChiZhu" bulk screens (`87ad:70db`) and those that show up as USB mass
  storage, which InfoPanel drives with protocols of their own.
