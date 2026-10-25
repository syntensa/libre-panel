# BeadaPanel (Panel-Link and Status-Link)

Applies to every BeadaPanel (NXElec), USB `4e58:1001`: the panel says which model it is.
Driver: `src/libre_panel/devices/beadapanel.py`.

Sources, read in October 2026:

- The maker's own: [JT365/beada](https://github.com/JT365/beada) (its Linux DRM driver,
  GPL-2.0+; branch `main` and the newer branch `mint-7.0.0`, commit `76f3a88`:
  `src/statusLinkProtocol.c`, `src/panelLinkProtocol.c`, `src/beada_device.c` with its table of
  models), [NXElec/beadapanelDaemon](https://github.com/NXElec/beadapanelDaemon) (`bpd.c`, the
  Raspberry Pi daemon), [NXElec/panelLink](https://github.com/NXElec/panelLink) and
  [NXElec/BeadaTools](https://github.com/NXElec/BeadaTools) (the Panel-Link and Status-Link
  demos for Windows), all GPL-3.0, and the maker's answers in
  [InfoPanel issue 67](https://github.com/habibrehmansg/infopanel/issues/67) and
  [issue 37](https://github.com/habibrehmansg/infopanel/issues/37).
- [habibrehmansg/infopanel](https://github.com/habibrehmansg/infopanel) (GPL-3.0), commit
  `8a3062d`: `InfoPanel/BeadaPanel/` and `InfoPanel/Services/BeadaPanelDeviceTask.cs`.
- [unawakened1986/beadapanel-python](https://github.com/unawakened1986/beadapanel-python)
  (MIT), read for comparison only (see "Colours" below).

Nothing here was seen on a panel by Libre Panel yet.

## Transport

Interface 0 (class `FF`), two channels:

| Endpoints | Channel | For |
|---|---|---|
| bulk OUT `0x02`, IN `0x82` | Status-Link | the panel's information, the reset, the backlight |
| bulk OUT `0x01` | Panel-Link | the pictures |

On Windows it comes up as a WinUSB device: no driver to install (InfoPanel, beadapanel-python).

## Status-Link

A message is a header of 20 bytes and its payload:

| Bytes | Content |
|---|---|
| 0–10 | `STATUS-LINK` |
| 11 | the version, 1 |
| 12 | the type |
| 13 | 0 |
| 14–15 | a sequence number, 0 |
| 16–17 | the message's length, the header with it (little endian) |
| 18–19 | the checksum of bytes 0–17 |
| 20– | the payload |

The checksum (both channels): the bytes as 16-bit words, little endian, summed; the carries
folded back in twice; the one's complement of the low 16 bits.

| Type | Meaning | Payload |
|---|---|---|
| 1 | the panel's information | none; the answer below |
| 2 | reset the Panel-Link channel | none; the panel wants about a second after it (InfoPanel) |
| 3 | the backlight | one byte, 0 up to the highest the panel said |
| 4–8 | its storage, its clock, resets of its own | not sent |

The answer to type 1 (on `0x82`) is a header of 20 and 80 bytes:

| Bytes (of the 80) | Content |
|---|---|
| 0–1 | the firmware's version |
| 2 | the Panel-Link version |
| 3 | the Status-Link version |
| 4 | the platform (InfoPanel: 1 and 2 are NXP i.MX6UL/ULL) |
| 5 | the model's code (the beada driver calls it `os_version`) |
| 6–69 | the serial number (Libre Panel does not read it) |
| 70–71, 72–73 | the screen's width and height for the plain stream |
| 74–77 | the storage, KB |
| 78, 79 | the highest and the present backlight |

The model codes, from the beada driver's `beada_device.h`: 0 5, 1 7, 2 6, 3 3, 4 4, 10 5C, 11
5T, 12 7C, 13 3C, 14 4C, 15 6C, 16 6S, 17 2, 18 2W, 19 7S, 20 5S, 21 8, 22 11, 23 9, 24 Y, 25 X,
26 Z. A code Libre Panel does not know is taken by the size the panel says.

## Panel-Link

A tag is 270 bytes: `PANEL-LINK`, the version 1, the type, a format string of 256 bytes
(zero-padded) and the checksum of the 268 bytes before it.

The start of a stream carries the format, with the size the panel says (in its information):

```
video/x-raw, format=RGB16, width=W, height=H, framerate=0/1
```

then every picture follows on `0x01` as it is: W × H pixels, 2 bytes each, no header, no
answer. The end of the stream is a tag without a format.

Its types differ between the sources: InfoPanel sends 1 (start) and 2 (end) to a panel whose
Panel-Link version is 1 and 5 and 6 to the others; the Raspberry Pi daemon and the Windows demo
use 5 and 6 (and 3 to reset, 4 to clear); the beada driver uses 1 throughout. Libre Panel does
as InfoPanel does.

**Write-through.** `image/x-raw, format=BGR16` with the size of the model's framebuffer
(standing on end for some) makes the panel copy the pixels straight to its framebuffer, which
is faster on the models whose framebuffer is 32-bit (the maker in issue 37). The beada driver
uses it only on firmware 710 and later and for the models in its table; the maker wrote in
issue 67 that both tags work if the pictures match them, and that write-through is a
performance feature. Libre Panel sends the plain stream only.

**Colours.** The beada driver fills both streams with `drm_fb_xrgb8888_to_rgb565` (red in the
high five bits, little endian), and InfoPanel with Skia's RGB565, the same. beadapanel-python
sends blue in the high bits for the write-through stream; as Libre Panel does not use that
stream, it follows the two others.

## Backlight

Type 3 with a byte from 0 to the highest the panel says (the beada driver's backlight device
has that range; InfoPanel sends 0–100, and 25–100 to a panel of Panel-Link version 1). Libre
Panel scales 0–100 % to the panel's range and sends 0 to turn the screen off.

## Keeping the picture

The sources stream pictures all the time (InfoPanel at its frame rate, the daemon at its own),
so whether a panel keeps a picture without new ones is not known: Libre Panel sends the last
one again after a second without one.
