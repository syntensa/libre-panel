# TURZX / Turing V1.x USB protocol

Applies to the USB panels with VID `0x1CBE`: 2.8" round (`0x0028`), 4.6"
(`0x0046`), 5.2" (`0x0050`), 8" (`0x0080`), 8.8" (`0x0088`), 9.2" (`0x0092`),
12.3" (`0x0123`).

Sources: the GPL-3.0 reference implementation in
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
(`library/lcd/lcd_comm_turing_usb.py`) and measurements on a 9.2" panel made
in the SPUR II project. Statements marked **verified** were observed on that
panel; everything else comes from the reference and still needs confirmation
per model. The JPEG stills follow the notes of two other projects (see
*JPEG stills (101)*).

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
| 10 | sync / hello | reply `0A C8` + ASCII `turzx_00` **verified** (and bytes that change with every connection on the 9.2"; the 5.2" answers a firmware version `turzx_0001_0015` at `[8:]`, as jijiechen/turing-monitor reads it; Libre Panel logs what follows `turzx_` when it connects) |
| 14 | brightness | `[8]` = 0–102 (percent × 1.02) |
| 15 | frame rate | `[8]` = fps; also sets the panel's own playback rate |
| 17 | H.264 block size | reply 0 on the 9.2" → default 202752 **verified** |
| 100 | storage info | all zero: no card in the 9.2" **verified** |
| 101 | JPEG frame | `[8:12]` = size, big endian; payload = baseline JPEG, to the layer under 102. On the 9.2" only 464 columns wide (see *JPEG stills (101)*) |
| 102 | PNG frame / overlay | `[8:12]` = size, big endian; payload = PNG |
| 110 | start local video playback | `[8:12]` = name length, `[16:]` = name. Clears the framebuffer (needed before streaming) and copies the name into the panel's settings **in memory** **verified** |
| 111 | stop local playback | **verified** |
| 112 | local playback running? | **verified** |
| 121 | H.264 block | `[8:12]` = size, `[12]` = 1 on the last block of a clip |
| 122 | queue depth | reply `[8]` = blocks waiting **verified** |
| 123 | stop stream | **verified** |

Commands that change what the panel keeps are **never sent while the panel
shows themes**. A few are sent for the panel's own screens, and only when the
person asks for it in *Settings → Panel → Start screen* (see *The panel's own
screens* below): 40, 42, 98, 99, 113 and 125. Never sent at all: 13 (set
rotation and save the settings), 38, 39, 41, 100, firmware. 11 restarts the
panel (back after about 5 s; the only fix for a hung video decoder besides
replugging); Libre Panel sends it for that and nothing else (see *A hung
decoder*). 12 **halts** the panel until it loses power: never send it.

## Frames

- The framebuffer is **480×1920 portrait** on the 9.2". A 1920×480 landscape
  design is rotated with **ROTATE_270** before sending. **verified**
- The bezel hides the framebuffer's last **18 columns** (its right edge): the
  visible area is 1920×462, the reference library's size. In landscape the
  strip is the frame's top 18 rows; in portrait (ROTATE_180) its left 18
  columns. Measured with a 1 px ruler; the catalog carries it as data
  (`hidden`) for layouts and the editor, and nothing crops a PNG frame (a
  JPEG still is 464 columns wide, 2 into the strip; see below).
  **verified**
- PNG frames must be **colour type 6 (RGBA)**. With RGB the decoder is off by
  one byte per pixel and tiles the image four times. **verified**
- A full PNG frame takes about 87 ms on the device → roughly **9 fps** for full
  frames. **verified**

## JPEG stills (101)

Libre Panel sends stills as JPEG where `[device] still` says so: `"jpeg"` on
any TURZX USB panel, and `"auto"` on the 5.2". The facts come from
[sjcsoftware/turzx-linux](https://github.com/sjcsoftware/turzx-linux/blob/main/docs/protocol.md)
(verified there on a 9.2", firmware `turzx_0001_0015`) and
[jijiechen/turing-monitor](https://github.com/jijiechen/turing-monitor/blob/main/docs/PROTOCOL.md)
(5.2"); Libre Panel has not been seen sending them on hardware yet.

- **The packet** is built like 102: `[8:12]` = the payload's size, big
  endian; a baseline JPEG follows the packet in the same transfer, below
  1 MiB like every payload. The panel decodes it in hardware (a PNG in
  software: about 9 full frames a second).
- **The width is whole blocks of 16.** On the 9.2" the layers are **464**
  pixels wide (29 × 16, the alignment of the hardware JPEG decoder), not
  480. A 480-wide JPEG is decoded with the wrong stride: the picture is
  roughly right, but its last rows wrap onto the first ~70 (garbage with
  diagonal streaks at one end) and the other end is never updated. That is
  why 101 "did not display correctly" on the 9.2" in SPUR II. About 462 of
  the 464 columns show behind the bezel, which fits the 18 columns measured
  here: 480 − 18 = 462, rounded up to whole blocks = 464. So Libre Panel
  cuts the native frame to its first 464 columns (464–479 lie behind the
  bezel) and never stretches it. Where nothing is hidden the width stays
  the framebuffer's: 720 on the 5.2" and the 12.3", whole blocks already
  (turzx-linux suggests a width rounded up to a multiple of 16 for the
  other models, which this gives).
- **Subsampling:** 4:4:4 and 4:2:0 both decode once the width is right
  (4:2:2 untested). Libre Panel uses 4:4:4 at quality 90 for crisp
  coloured text, as turzx-linux does; a frame that would be larger than
  1 MiB takes quality 75, 60 or 45.
- **Two layers.** A JPEG goes to the base layer, where the video plays; the
  PNG of 102 is an overlay above it with per-pixel alpha, and it **keeps its
  last content, even across reconnects**: an old opaque PNG hides every
  later JPEG. Before its first JPEG on each connection Libre Panel sends a
  fully transparent PNG of the framebuffer's size, as the video mode does
  (and as the vendor app does before streaming).
- **Speed:** on the 9.2" turzx-linux measured about 20 fps for 800 KB
  frames of noise and more than 45 fps for simple ones (12–16 MB/s), where
  full PNG frames give about 9 fps. turing-monitor measured about 8 fps
  with 30 KB frames on the 5.2".
- **The 5.2" shows no PNG.** It acknowledges 102 and shows nothing
  (turing-monitor); 101 is its only still path, so `"auto"` takes JPEG
  there. PNG stays the default on the others, where it is proven (9.2",
  12.3").

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
- **How the frames reach ffmpeg.** Libre Panel writes each frame into ffmpeg's
  stdin as raw video of the theme's size, packed into the pipe by Pillow from
  ffmpeg's writer thread (no copy of the frame as bytes, the render loop free
  meanwhile). By default (`[video] pixels = "auto"`) as `-pix_fmt rgb0`: the
  four bytes a pixel Pillow keeps, the fourth (the canvas's alpha) left aside,
  as RGB never showed it. AMD's and NVIDIA's encoders take rgb0 themselves
  (FFmpeg's `libavcodec/amfenc.c` lists `rgb0`/`rgba`/`bgr0`/`bgra` and maps them
  to `AMF_SURFACE_RGBA`/`BGRA`; `nvenc.c` takes them as `NV_ENC_BUFFER_FORMAT_ABGR`/
  `ARGB` and converts to 4:2:0, `rgb_mode` yuv420), so ffmpeg runs `transpose`
  and nothing else for them: no `-pix_fmt nv12`, no scaler in the filter graph
  (checked with `-loglevel debug` against the FFmpeg 8.1 build the downloads
  carry, whose Linux build lists both encoders: no `auto_scale` for either;
  FFmpeg 6.1 the same for h264_nvenc). The card converts RGB to YUV; it is told
  the colours swscale used before, `-colorspace smpte170m -color_range tv`
  (BT.601, limited range): AMF takes that as its output colour profile
  (`AMF_VIDEO_CONVERTER_COLOR_PROFILE_601`; left unset, AMF picks the profile by
  the picture's size), and FFmpeg marks NVENC's own conversion of packed RGB as
  BT.601 in limited range (matrix 5, `videoFullRangeFlag` 0 in the stream)
  whatever it is told. Whether a panel's picture then looks exactly as before
  only a panel shows (**unverified** on AMD and NVIDIA). For x264 and Intel's QSV,
  which take YUV only, ffmpeg converts rgb0 on the processor: the YUV is the
  same, byte for byte, as from rgb24 (checked with FFmpeg 6.1 and 8.1), so
  x264's stream is the same as before. `pixels = "rgb"` is the feed of before
  (`rgb24`, NV12 made by ffmpeg for every card); a card's encoder that ends
  before its first picture fed rgb0 is started again so by itself, before
  "auto" leaves it out for x264; `video-state.json` keeps that for a week per
  encoder and ffmpeg, so the next connections start it fed rgb24 at once.
- **Checking the colours** (Settings → Panel → *Check the colours*,
  `devices/colour_check.py`). The panel's decoder applies one fixed matrix to
  every stream, BT.601 in limited range (the one swscale converted for), whatever
  the stream says of its colours: equal YUV in the stream is equal colours on the
  panel. So the check compares YUV, measured on the PC and away from the panel:
  a test picture at the size of the panel's frames, of large flat patches (black,
  five greys, white, pure red, green and blue, the themes' accent `#2EE6D6` and
  violet `#A78BFA`, an orange, a skin tone), goes ten times through the very
  command the display builds (its encoder settings, its `transpose`), once fed
  `pixels = "auto"` and once `"rgb"`, for every encoder of the card ffmpeg can
  start here (the probe "auto" goes by: the video mode's own command, whose last
  line from ffmpeg the card shows for an encoder passed over) and for x264, one
  run after the other, each with a time limit. ffmpeg
  decodes each stream to raw video in the decoder's own pixel format, converting
  nothing (the result names it: yuv420p, or yuvj420p for a stream that says it is
  full range, read the same); the turn is undone and the mean Y, U and V of the
  middle of every patch (half its width and height, at least 8 pixels from its
  edges, where chroma and blocks blur) are taken over all ten pictures. The two
  feeds are **identical** when every patch is within ±2 code values; a **level
  shift** when the greys stay grey (U and V at 128) in both but black and white
  differ in Y (full against limited range: 0/255 against 16/235); a **matrix
  shift** when black, the greys and white agree and the saturated colours differ
  (BT.709 against BT.601); else **other**. The swatches the app shows are the
  measured YUV turned into RGB with BT.601 in limited range, as the panel shows
  it. Measured here with x264, both feeds give the same YUV to the code value;
  ffmpeg's `scale=out_range=full` gives a level shift (black 0 against 16) and
  `scale=out_color_matrix=bt709` a matrix shift (green's Y 173 against 145).
  Whether AMD's and NVIDIA's conversion on the card agrees is what the check is
  for, on the PC that has the card (**unverified** so far). With a shift,
  `pixels = "rgb"` (the card's button *Use the processor's conversion*) gives the
  colours of before. *Show it on the panel* shows the test picture on a panel for
  30 s, fed every 3 s the other way (a new ffmpeg each time, the panel stays open;
  a large A or B in a corner), written nowhere: the panel's loop asks for it on
  each frame, so its theme and feed come back when it ends, when it is stopped, or
  with the app (a marker beside `config.toml` tells the next start that the app
  ended during one; nothing else is left to undo).
- Measured with Arena on the 12.3" (1920×720, 50 fps) and FFmpeg 8.1 on a
  4-core PC, the CPU time of Libre Panel and ffmpeg together per frame, before
  and after, in paired runs on a busy machine (its absolute numbers are about
  twice a quiet one's): with a card's encoder (the part up to the encoder)
  29.1 → 20.5 ms; with x264 40.9 → 35.5 ms; Intel's (up to its encoder) about
  2 ms less. ffmpeg's `transpose` turns four bytes a pixel for less than Pillow
  does (three for more), so ffmpeg turns the frames for every encoder.
- The stream above took about 170–300 KB/s: that is its own rate at the 2M
  cap, not a limit of the link. The link carries far more: 12–16 MB/s of JPEG
  stills were measured on the 9.2" (20 fps of 800 KB frames; sjcsoftware/
  turzx-linux, docs/protocol.md), and jijiechen/turing-monitor streams H.264
  to the 5.2" at 30 fps with 16 Mbit/s by default and 24 Mbit/s "for sharper
  output". What breaks playback is flooding the *queue* (sending faster than
  the player drains), which the flow control below prevents.
- Libre Panel scales `-maxrate` with the pixels and the frame rate: 2M is for
  1920×480 at 50 fps; the 12.3" (1920×720) starts at 3M. From there "auto"
  follows the panel up the steps 4 · 6 · 8 · 12 · 16 · 24 · 32 Mbit/s (never
  below its start: a panel that cannot keep up there cannot keep up with the
  pictures, as the 9.2" at 60 fps showed): a step down when,
  within 10 s, the sender waited for room in the queue five times (the decoder
  lags) or half the blocks (25 at least) took longer than 20 ms to send (the
  link lags; a healthy block takes about 1 ms), a step up after 60 s without
  that (a failed step is held off for 10 min, twice as long each time it fails
  again). Each step starts ffmpeg anew: x264 cannot change its rate on the way.
- A step does not freeze the picture: the new ffmpeg starts beside the old one, whose
  pictures go on to the panel until the new one's first (SPS, PPS, keyframe); one without a
  picture within 3 s is closed and the old one goes on (tried again after a minute).
- Measured on the 12.3" with a 9.2" beside it (2026-10, Settings → Panel →
  Video experiment, during a game): the 12.3" took every step without the
  sender waiting for room, nothing dropped, no faults, with caps up to 32M
  and streams up to 447 KB/s (AMD's encoder); crf 18 cost 190 KB/s at 21
  fps there and 244 KB/s at 48 fps on the 9.2"; the profiles **main and
  high show**, and so does **h264_amf** (AMD), alone and with high. So the
  defaults are crf 20, profile high on these two, and AMD's encoder where
  ffmpeg can start it. The 9.2" at 60 fps (70 told) **cannot**: 55 delivered,
  the sender waited 65 times. The hardware encoders h264_nvenc and h264_qsv
  are set to the same (no B-frames, one slice, the keyframe spacing, the
  cap) and remain an experiment until someone sees them show.
- Inside the 12.3" (board `QS400_123inch_V1.1`, 2025-12) sits an Allwinner
  T113-S3 with a custom top mark (eLQFP128, dual Cortex-A7, 128 MB DDR3 in the
  package, SPI flash, USB 2.0 OTG). Its decoder takes H.264 up to 1080p@60, so
  the bit rate a panel takes is bounded by its firmware's USB-to-decoder path,
  not by the decoder: what "auto" measures.

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

## The panel's own screens

The panel has two screens of its own that need no program on the PC: a JPEG at
power-on (`usr/data/boot.jpg`, else the factory logo `/media/logo.jpg` on another
partition, never written) and an H.264 clip it plays in a loop when the PC's USB
comes up, until a program streams (the name kept in its settings, `cfg+6`; the
vendor app's `usr/data/standby.h264`). SPUR II read this from the 8.8"'s firmware
and measured it on the 9.2". Libre Panel draws both in its own look for the
panel's size (`render/bootscreen.py`) and writes them on a click
(`devices/panel_files.py`), with the panel's themes paused first.

File commands share one layout: `[8:12]` the name's length (big endian),
`[12:16]` the file's length (40 only), `[16:]` the name, relative to the
application's folder (`usr/data/...`; `/usr/data` fails).

| Cmd | | |
|---|---|---|
| 40 | write a file | the file follows the packet in the same transfer, at most 1 MiB; written as named, so only names below `usr/data/` are sent |
| 42 | delete a file | taking `usr/data/boot.jpg` away brings the factory logo back |
| 98 | a file's size | little endian at `[8:12]`; checked after every write |
| 99 | list a folder | 20 pieces of 512 bytes from a 0x2800-byte buffer; it reads the name only at the first, so all 20 are fetched every time; it creates a missing folder |
| 113 | show a JPEG of the panel's | the boot logo, at once |
| 110 | play a clip | clears the framebuffer and keeps the name in the settings **in memory**; never with an empty name |
| 125 | save the settings | `[8:14]` brightness (0–102), start mode (2: the clip `cfg+6` names), 0, rotation, 0, offline (0: dim and stop 10 s after the PC is gone); saved with the clip's name |

- **The boot logo** is drawn in the framebuffer's orientation (480×1920 on the
  9.2", turned as the frames are), JPEG quality 88. The JPEG path draws into a
  smaller area that leaves about 64 of 1920 px at both short ends as they were and
  does not clear the framebuffer, so nothing is near the ends; they fade to black.
- **The standby clip** is H.264 baseline with one slice a picture
  (`sliced-threads=0`), no B pictures, and a sequence (SPS, PPS and a keyframe)
  every 11 pictures (`keyint=11:repeat-headers=1`): the decoder takes at most 11
  pictures per sequence. 44 pictures at the 30 fps of a cold start loop in about
  1.5 s. A sequence start costs the panel about 30 ms and it refills its queue
  about once a second, so the clip moves only in brightness at fixed places.
- **Writing it:** 40 `usr/data/standby.h264`, 98 to check, 15 = 30, 110 with the
  name (it plays at once), 125 `[brightness, 2, 0, 0, 0, 0]`. The clip that was
  there cannot be read back first: the protocol has no file read.

