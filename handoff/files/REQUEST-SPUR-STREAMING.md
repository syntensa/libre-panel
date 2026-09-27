# Request to the Libre Panel core: close the "SPUR streaming" gap

From: the SPUR II Mod Edition (local Windows install on the machine with the 9.2"
TURZX panel `1CBE:0092`), session "TURZX-Panel Echtzeit-Renderer".
Date: 2026-09-27.

## Why this request

The user is moving from SPUR II to **Libre Panel + a SPUR II mod**. Everything
personal from SPUR II returns as a mod package. But the proven **SPUR streaming
system** (H.264 video layer at 50 fps) is not personal: it is what Libre Panel is
missing (roadmap 0.2), so it belongs **in the core**. Then core and mod speak one
language and nobody maintains a parallel path.

For us only **Windows** matters. Please keep your cross-platform standards
upstream. We will not test Linux/macOS.

Evidence behind the numbers below: SPUR II has run this pipeline around the
clock since early September. Its last 6 hours: 722 statistics windows, **50.0 fps
median and p5, 0 dropped blocks, 0 USB errors, ~250 KB/s**, render 7.5 ms/frame.
In game mode it holds 30.0 fps.

---

## Part A — H.264 video layer in the core (your roadmap 0.2)

Your `docs/protocol/turzx-usb.md` already has the basics. These are the
verified details it is missing, followed by what we need from the implementation.

### A1. Init sequence and the non-display commands

`10 → 110(path) → 111 → 112 → 13 → 14 → 42 → 102(transparent) → 15 → 17 [→ 125]`

Your CONTRIBUTING asks for a documented, reversible reason and a hardware test
for anything beyond display commands. Here is that record for each command:

| Cmd | Why it is needed | Persistent? | Hardware evidence |
|---|---|---|---|
| 110 `play_local_h264_async(name)` | Without it the screen stays black (sweep of 8 init sequences: only the two with 110 showed a picture). What it really does is `clean_fb1()`, i.e. clear the framebuffer. | **Yes**: `strcpy(cfg+6, name)`, saved by the following 13/125. **Never send an empty name**: that wipes the standby-clip path, and the panel shows nothing at the next power-on (observed 2026-09-03). Send the path of an existing local clip, or read it back first. | verified |
| 111 / 112 | stop / query local playback (stops what 110 started) | no | verified |
| 13 | part of the vendor init (`set_rotation` + `SaveConfig`) | yes (rotation 0) | verified |
| 42 `DeleteFile` | vendor sends it; with an empty name `DeleteFile("")` fails harmlessly | no if the name is empty; **dangerous if filled** | firmware + verified |
| 125 | optional: re-confirm brightness/start mode each start, so a config changed elsewhere cannot stay wrong | yes (`app.cfg`) | verified |
| 11 `system("reboot")` | **recovery**: a hung decoder comes back without replugging. Off the bus after 2.9 s, back after another 1.8 s, then re-init | no | verified 2026-09-09 |
| **12** `system("halt")` | **never**, only a power cycle helps afterwards. In the firmware it sits 8 bytes away from 11 | — | firmware |

Please correct in the protocol doc: *"a hung video stream only recovers by
replugging"* is outdated. Cmd 11 does it.

### A2. Encoder (exact, tuned by measurement)

```
ffmpeg -hide_banner -loglevel error -f rawvideo -pix_fmt rgb24 -s 1920x480
       -framerate 50 -i - -an -vf transpose=1
       -c:v libx264 -profile:v baseline -preset superfast -tune zerolatency -threads 1
       -crf 25 -maxrate 2M -bufsize 2M
       -x264-params sliced-threads=0:bframes=0:scenecut=0:keyint=500:min-keyint=500:ipratio=2.0:repeat-headers=1
       -pix_fmt yuv420p -f h264 -
```

- **Rotation in ffmpeg** (`transpose=1` = PIL `ROTATE_270`) instead of in Python.
  The stream is bit-identical (same MD5 over 60 frames) and it saves ~2 ms per frame.
- **Keyframe interval 3–10 s** (keyint = seconds × fps). At ≤ 1 s every keyframe
  re-quantises static areas, and flat backgrounds visibly pulse (measured
  flicker 0.52 at 0.5 s vs 0.07 at 3 s and 10 s). `repeat-headers=1` lets the
  panel join mid-stream.
- `ipratio=2.0`: +0.26 dB at the same rate. `scenecut=0` keeps the rhythm fixed.

### A3. One picture per 121 block, and how to find the picture boundary

The device queue counts **blocks**, not bytes. When a keyframe went out as 3
blocks, the queue sat at 3 and flow control throttled every 3 s. With one
picture per block the queue stays at 0–1.

Picture boundary without parsing: ffmpeg writes to the pipe in **32 768-byte**
pieces. A read whose accumulated length is **not a multiple of 32 768** ends a
picture. If a picture happens to be an exact multiple, it goes out with the
next one: one picture late, not lost. Parsing AUD/NAL units is fine too, if you
prefer that.

### A4. Flow control

Poll 122 (queue depth in `resp[8]`) every **2nd** block. If depth **> 2**, wait
in 30 ms steps until depth **≤ 1** (give up after 1.5 s). Count these waits; in
steady state there should be none. The ring holds 5 blocks of 202 756 B.
Flooding the device breaks playback.

### A5. Report a higher rate than you deliver (Cmd 15)

The firmware player shows each picture strictly every `1000/fps` ms and **never
catches up**, so any lag once accumulated stays forever. Report a rate above the
delivered one. SPUR II reports 60 and delivers 50: queue max 1, 0 throttles in
steady state.

### A6. Threads and recovery

- The render loop never waits for USB: a **reader thread** reads ffmpeg stdout
  and cuts pictures, a **sender thread** sends 121 blocks. If the send queue is
  full, back-pressure through the pipe slows rendering. Fewer frames beat torn frames.
- **ffmpeg dies or stalls** → start a new ffmpeg and keep the service running.
  Drop bytes buffered from the old process.
- **USB error** → reopen, full init, continue ("healing").
- **Decoder hang** (blocks accepted but picture frozen, or queue stuck) → Cmd 11,
  wait, re-init.

### A7. Shutdown (your roadmap: "standby image when the PC shuts down")

1. `123` stop stream.
2. **`15` = 30**. Local standby playback uses the *last* Cmd 15 rate. After a
   service that sent 60, the standby clip played 11 frames in 190 ms and
   stuttered.
3. Leave a deliberate last picture. With +5 V standby the panel shows the last
   frame while the PC is off.
4. Windows kills processes hard on shutdown. SPUR II gets warned in time via
   `SetProcessShutdownParameters(0x3FF)`, a hidden window handling
   `WM_QUERYENDSESSION`, and System event 1074 as a safety net.
5. Local clips (if you ever upload one with Cmd 40): the decoder shows at most
   **11 pictures per sequence start**, so a standby clip needs a keyframe
   (+SPS/PPS) at least every 11 frames.

### A8. App loop for streaming drivers

A video driver needs **every frame at a steady rate**. Skipping unchanged frames
(`changed_region`) is wrong here. Pacing that does not drift:

```python
soll = fps_for_current_mode
if soll != takt:                       # rate changed (e.g. game mode 30 fps)
    takt = soll
    anker_t, anker_n = time.perf_counter(), frames
ziel = anker_t + (frames - anker_n) / takt
jetzt = time.perf_counter()
if jetzt < ziel:
    time.sleep(ziel - jetzt)
elif jetzt - ziel > 1.0 / takt:        # fell behind by a frame: re-anchor, don't sprint
    anker_t, anker_n = jetzt, frames
```

Suggestion: a capability on `Display`, e.g. `streaming = True`, that makes the
app render and hand over every frame at `fps`.

### A9. Config

`video` (auto when ffmpeg is found / on / off), `fps` (50), `device_fps` (60),
`crf` (25), `preset` (superfast), `keyframe_s` (10), `maxrate` (2M), `ffmpeg`
path. On Windows: search the configured path first, then PATH.

### A10. Tests without hardware

Extend `tests/fake_panel.py` to accept 110/111/112/13/42/121/122/123/125 and
simulate queue depth. Test picture framing, flow control, pacing, ffmpeg restart
and USB healing.

### A11. A Windows bug we hit on the first test run

`ms_since_midnight()` uses `time.mktime`, which raises
`OverflowError: mktime argument out of range` on Windows for timestamps near
the epoch. `test_packet_layout` calls it with `now=0`, so it fails on Windows;
the Linux CI does not see it. Computing from `datetime.fromtimestamp(now)`
(hour/min/sec/microsecond) avoids `mktime` entirely.

### A12. Hardware verification

We have the real 9.2" panel. As soon as the video path is in, send us the commit.
We stop SPUR II (with the user's consent) and run `libre-panel doctor` plus a
long run, then report back with numbers. That also moves the 9.2" from
"unverified" to "supported".

---

## Part B — interfaces the SPUR II mod needs ("one language")

Everything that SPUR2_MIGRATION.md lists under "left out" comes back as the mod.
Please design these the Libre Panel way; we build against whatever API you choose.

| # | What | Why the mod needs it |
|---|---|---|
| B1 | **Code-rendered screens** from installed plugins (e.g. entry point `libre_panel.screens`); a theme (data only) selects one by name | SPUR's seven screens (Klassisch, Studio, Horizont, Kopfzeile, Fokus, Sitzung, Bericht) are procedural, with Blender light layers. Themes stay code-free, and the code comes only from installed packages |
| B2 | **Widget plugins** (`libre_panel.widgets`) with a property schema for the editor inspector | SPUR building blocks (light rings, light bars, facet curves, fan rotors) placeable in the editor |
| B3 | **Service plugins** (`libre_panel.services`): lifecycle, read snapshot/history, request a theme/screen switch, post notifications | autopilot, game mode (PresentMon + Steam), memory database, cooling analysis |
| B4 | **Overlay / notification stage** after rendering, plus **screen transitions** | volume/warning/device toasts, SPUR's transitions |
| B5 | **Frame rate per mode** (or settable by a service) | 50 fps desktop, 30 fps while a game runs |
| B6 | **Editor extension pages** (`libre_panel.editor_pages`) that reuse the editor's CSS and components | cooling analysis, autopilot rules, screen picker, in the editor's look |

---

## Appendix — reference code from SPUR II (GPL-3.0, may be taken over)

**Picture boundary** (`tools/panel_video.py`):

```python
FFMPEG_PUFFER = 32768

def bild_zu_ende(laenge: int) -> bool:
    """Complete picture? The panel counts its queue in BLOCKS."""
    return laenge > 0 and laenge % FFMPEG_PUFFER != 0
```

**Reader thread** (`tools/panel_daemon.py`, simplified):

```python
while True:
    b = proc.stdout.read(65536)
    if not b:
        break
    puffer += b
    if not bild_zu_ende(len(puffer)):
        continue                    # more pieces of the same picture follow
    try:
        sendeschlange.put((proc, bytes(puffer)), timeout=0.5)   # back-pressure, not drop
    except queue.Full:
        verworfen += 1
    puffer.clear()
```

**Sending a block and flow control** (`tools/panel_video.py`, `VideoSession`):

```python
hoch, tief, poll_every = 2, 1, 2

def send_chunk(self, data: bytes, last: bool = False) -> bool:
    pkt = build_packet(121)
    put_size_be(pkt, len(data))       # [8..11] big-endian
    if last:
        pkt[12] = 1                   # only at the end of a clip, never in live streaming
    r = self.p.xfer(seal(pkt) + data)
    self.sent_chunks += 1
    if r is None:
        return False
    if self.sent_chunks % poll_every:
        return True
    d = self.queue_depth()            # Cmd 122, resp[8]; short timeout, None if busy
    if d is not None and d > hoch:
        self.drain(tief)              # 30 ms steps until <= tief, max 1.5 s
    return True
```

**Init** (`VideoSession.start`, condensed): 10 (sleep 50 ms) → 110 with
`[8..11]` = BE length of the path and the path at `[16..]` → 111, 112, 13 → 14
(`[8]` = brightness 0–102) → 42 → 102 with a fully transparent 480×1920 RGBA PNG →
15 (`[8]` = device fps) → 17 (0 means keep 202 752) → 125 (`[8]` brightness,
`[9]` start mode 2 = local video, `[10..13]` 0).
