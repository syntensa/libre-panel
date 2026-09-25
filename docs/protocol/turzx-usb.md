# TURZX / Turing V1.x USB protocol

Applies to the USB panels with VID `0x1CBE`: 2.8" round (`0x0028`), 4.6"
(`0x0046`), 5.2" (`0x0050`), 8" (`0x0080`), 8.8" (`0x0088`), 9.2" (`0x0092`),
12.3" (`0x0123`).

Sources: the GPL-3.0 reference implementation in
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
(`library/lcd/lcd_comm_turing_usb.py`) and measurements on a 9.2" panel made
in the SPUR II project. Statements marked **verified** were observed on that
panel; everything else comes from the reference and still needs confirmation
per model.

## Transport

- One interface (0), class `FF`, bulk **OUT `0x01`** and bulk **IN `0x81`**,
  512-byte packets, USB 2.0 high speed. **verified**
- On Windows the device binds to WinUSB through its MS OS descriptor; libusb can
  open it without Zadig. **verified**
- The vendor app keeps the device open exclusively; quit it first. **verified**

## Command packet

Every command is exactly 512 bytes:

| Bytes (plain text) | Content |
|---|---|
| 0 | command id |
| 1 | 0 |
| 2–3 | magic `1A 6D` |
| 4–7 | milliseconds since local midnight, little endian |
| 8–499 | arguments (command specific) |

The 500 plain-text bytes are zero-padded to 504 and encrypted with **DES-CBC,
key = IV = `slv3tuzx`**. The 504 cipher bytes go into a 512-byte buffer whose
last two bytes are the trailer `A1 1A`. A payload (PNG, H.264) follows the
packet **in the same bulk transfer**. **verified** byte for byte against the
reference and the vendor app.

## Replies

The panel answers every command on bulk IN. Byte 0 echoes the command id,
`0xC8` (in byte 1, or byte 8 for some commands) means *accepted* — not
*displayed*.

**Zero-length packet (important).** Replies are exactly 512 bytes, which equals
`wMaxPacketSize`, so USB terminates them with a zero-length packet. Reading
exactly 512 bytes leaves that ZLP in the pipe; every following reply is then
off by one, the IN pipe fills up and the device stalls bulk OUT. **Read up to
1024 bytes** to consume it (the reference library instead flushes with a 100 ms
timeout per command). Check that byte 0 echoes the id you sent. **verified**

After a crash, stale replies can still be queued: drain IN until timeout, then
send a sync and check the echo (up to three attempts). **verified**

## Commands

| Id | Meaning | Arguments / notes |
|---|---|---|
| 10 | sync / hello | reply `0A C8` + ASCII `turzx_00` **verified** |
| 14 | brightness | `[8]` = 0–102 (percent × 1.02) |
| 15 | frame rate | `[8]` = fps; also sets the panel's own playback rate |
| 17 | H.264 chunk size | reply 0 on the 9.2" → default 202752 **verified** |
| 100 | storage info | all zero: no card in the 9.2" **verified** |
| 101 | JPEG frame | did not display correctly on the 9.2" |
| 102 | PNG frame / overlay | `[8:12]` = size, big endian; payload = PNG |
| 110 | switch to video mode | not in the reference library **verified** |
| 111, 112, 13, 42 | part of the video init | meaning unknown, order matters **verified** |
| 121 | play H.264 chunk | `[8:12]` = size, `[12]` = last flag |
| 122 | stream / queue status | used for flow control |
| 123 | stop stream | |

Never sent by Libre Panel: 11 (restart), 125, and all storage / file-system
commands. Firmware is out of scope.

## Frames

- The framebuffer is **480×1920 portrait** on the 9.2" (the reference library's
  462 is wrong for this panel). A 1920×480 landscape design is rotated with
  **ROTATE_270** before sending. **verified**
- PNG frames must be **colour type 6 (RGBA)**. With RGB the decoder is off by
  one byte per pixel and tiles the image four times. **verified**
- A full PNG frame takes about 87 ms on the device → roughly **9 fps** for full
  frames. **verified**

## Two layers: H.264 video + PNG overlay (smooth path)

The panel composes two layers **verified**:

1. **Background video** — H.264 sent in chunks with command 121.
2. **Overlay** — an RGBA PNG sent with command 102, drawn over the video with
   transparency. The vendor app's "1 fps" is simply how often it updates this
   overlay.

Video init sequence: `10 → 110 → 111 → 112 → 13 → 14 → 42 → 102 (fully
transparent PNG) → 15 → 17`. Command 110 is the video-mode switch; the
reference library sends 41 instead, which the vendor app never uses.

Encoder settings that work: H.264 **Constrained Baseline**, yuv420p, level
3.1, raw Annex-B stream, **one slice per frame** (`sliced-threads=0`; note that
`-tune zerolatency` turns sliced threads on and the panel shows nothing),
`threads=1`. x264 `superfast`, CRF 25 gave the best quality at 50 fps.
Throughput around 170–300 KB/s — flooding the device (MB/s) breaks playback.
One encoded picture per chunk, sent from its own thread so USB never stalls
rendering. Measured: 25 fps and 50 fps stable, about 20–35 ms from render to
USB. **verified**

Pitfalls: a transparent clear overlay must have alpha 0 (an opaque black
overlay hides the video); a hung video stream only recovers by replugging the
USB cable.

On shutdown the SPUR II service stops the stream (123), sets the panel's local
playback rate (15) and sends a standby image (102) so the panel shows something
sensible after the PC is off.

Status in Libre Panel: the PNG path is implemented
(`src/libre_panel/devices/turzx_usb.py`); the video path is next on the
[roadmap](../ROADMAP.md).
