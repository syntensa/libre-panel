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
| 17 | H.264 block size | reply 0 on the 9.2" → default 202752 **verified** |
| 100 | storage info | all zero: no card in the 9.2" **verified** |
| 101 | JPEG frame | did not display correctly on the 9.2" |
| 102 | PNG frame / overlay | `[8:12]` = size, big endian; payload = PNG |
| 110 | start local video playback | `[8:12]` = name length, `[16:]` = name. Clears the framebuffer (needed before streaming) and copies the name into the panel's settings **in memory** **verified** |
| 111 | stop local playback | **verified** |
| 112 | local playback running? | **verified** |
| 121 | H.264 block | `[8:12]` = size, `[12]` = 1 on the last block of a clip |
| 122 | queue depth | reply `[8]` = blocks waiting **verified** |
| 123 | stop stream | **verified** |

Commands that change what the panel keeps, and are **never sent by Libre
Panel**: 13 (set rotation and save the settings), 125 (save settings: start
mode, brightness), all storage and file commands (38–42, 98–100), firmware.
11 restarts the panel (back after about 5 s; the only fix for a hung video
decoder besides replugging); Libre Panel sends it for that and nothing else
(see *A hung decoder*). 12 **halts** the panel until it loses power: never
send it.

## Frames

- The framebuffer is **480×1920 portrait** on the 9.2". A 1920×480 landscape
  design is rotated with **ROTATE_270** before sending. **verified**
- The bezel hides the framebuffer's last **18 columns** (its right edge): the
  visible area is 1920×462, the reference library's size. In landscape the
  strip is the frame's top 18 rows; in portrait (ROTATE_180) its left 18
  columns. Measured with a 1 px ruler; the catalog carries it as data
  (`hidden`) for layouts and the editor, and nothing crops the frame.
  **verified**
- PNG frames must be **colour type 6 (RGBA)**. With RGB the decoder is off by
  one byte per pixel and tiles the image four times. **verified**
- A full PNG frame takes about 87 ms on the device → roughly **9 fps** for full
  frames. **verified**

## Two layers: H.264 video + PNG overlay (smooth path)

The panel composes two layers **verified**:

1. **Background video** — H.264 sent in blocks with command 121, decoded by
   the panel's hardware decoder.
2. **Overlay** — an RGBA PNG sent with command 102, drawn over the video with
   transparency. The vendor app's "1 fps" is simply how often it updates this
   overlay.

The PNG path alone is limited by the ~60 ms the panel needs per full frame.
The decoder is not: SPUR II has driven the 9.2" at 50 fps around the clock
this way (50.0 fps median, 0 dropped blocks, 0 USB errors, about 250 KB/s).
Libre Panel's video mode ([configuration](../CONFIGURATION.md#video)) puts the
whole frame into the video and keeps the overlay transparent.

### Start

`10 → 110(name) → 111 → 112 → 14 → 102 (fully transparent PNG) → 15 → 17`

Without 110 the screen stays black. 110 is really "play a local clip": it
clears the framebuffer and copies its name into the panel's settings, and 111
stops the playback again at once. **Never send 110 with an empty name**: a
later save of the settings (13 or 125, e.g. by the vendor app) would erase
the panel's standby clip. The vendor app also sends 13 and 42; neither is
needed. With this sequence nothing is saved: after a restart the panel
behaves exactly as before. **verified** (9.2", 1501 blocks in 30 s, then a
restart with the standby clip unchanged)

The overlay must be fully transparent (alpha 0); opaque black hides the video.

### Encoder

x264, Constrained Baseline, yuv420p, Annex B:

```
-c:v libx264 -profile:v baseline -preset superfast -tune zerolatency -threads 1
-crf 25 -maxrate 2M -bufsize 2M
-x264-params sliced-threads=0:bframes=0:scenecut=0:keyint=500:min-keyint=500:ipratio=2.0:repeat-headers=1
```

- **One slice per picture.** `-tune zerolatency` turns slice threads on; the
  panel acknowledges multi-slice pictures but shows nothing.
- **Keyframes every 3–10 s** (keyint = seconds × fps). Each keyframe
  re-quantises static areas; at 1 s and below flat backgrounds visibly pulse.
  `repeat-headers=1` lets the decoder join at any keyframe.
- ffmpeg rotates (`transpose=1` = PIL's `ROTATE_270`); the stream is
  bit-identical to rotating before encoding.
- Throughput about 170–300 KB/s. Flooding the device (MB/s) breaks playback.

**verified**

### Blocks and flow control

- **One picture per 121 block.** The queue counts blocks: a keyframe cut
  into three blocks looks like a queue of three. Libre Panel cuts ffmpeg's
  output at NAL unit boundaries (a picture ends where the next one's first
  NAL unit begins).
- Block size ≤ 202752 (17 answers 0 = this default).
- Ask for the depth (122) every second block. Above 2, wait in 30 ms steps
  until it is at most 1 (give up after 1.5 s). The queue is a ring of five
  blocks that **silently overwrites** unread ones.
- **Report a higher rate than you deliver** (15): the player shows pictures
  strictly every 1000/fps ms and never catches up, so a delay once built up
  stays. 60 reported for 50 delivered keeps the queue at 0–1.

**verified**

### Stop

`123` (stop stream) → `15 = 30` → a last picture as PNG (102). 30 fps is the
panel's rate after power-on, and its own standby clip plays at the last rate
set: after 60 it stutters. With +5 V standby the panel keeps showing the last
picture while the PC is off. **verified**

### Overlays on top of running video

Each 102 holds the pipe for about 50 ms and delays the next 2–3 video
pictures. At most **2 overlays per second** keep 50 fps video smooth; at 5/s
the judder is visible, and from 10/s the video starves. **verified**

### A hung decoder

Rare (seen once in weeks): the panel takes each 121 block only after about
750 ms instead of about 1 ms, and the queue stands high without moving.
Detection, as in SPUR II: when the median time of the last 40 blocks exceeds
0.2 s, read the depth four times 150 ms apart; at least two answers, the
highest above 20 and all within 2 of each other, mean a hang (a high queue
that moves drains by itself). A new connection and a full start do not help,
not even a PC restart; restarting the panel (11) or replugging does.
**verified**

Recovery, as in SPUR II: send 11 (nothing else to the hung decoder, not even
123), wait until the panel has left the bus (up to 20 s) and is back (up to
90 s), give it 2 s more, then connect and start as after power-on. At most
three restarts in a row; the count starts again after 5 minutes without a
hang. When restarts do not help, or the panel does not come back, Libre
Panel asks to replug it. Plugins hear `panel-restarted` after
`panel-connected` when the panel came back from such a restart.
