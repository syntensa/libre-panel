# From the Libre Panel cloud session

## 4 — Video mode is ready for a device run (2026-09-27)

Thank you for M1–M5 and the hang criteria. With M1 and M4 the core needs no
command that saves anything, which settles most of the policy question.

**Hidden strip:** understood and agreed. It goes into the catalog as data
(`hidden_edges`, 9.2" landscape: top 18, others 0), drawn as a guide in the
editor and respected by the built-in layouts. Nothing in the pipeline offsets,
crops or scales. A code-rendered screen gets the full 1920×480 frame.

### What is on branch `video-layer` (`96f202b`)

It stays off `main` until it has run on your panel. Summary:

- **Start:** exactly your M1: `10 → 110(local_clip) → 111 → 112 → 14 →
  102(transparent) → 15(device_fps) → 17`. No 13, 125, 42 or 11; a test
  checks that none of them is ever sent.
- **Encoder:** your A2 settings, rotation in ffmpeg, `CREATE_NO_WINDOW`.
- **Picture boundary:** cut at NAL units, not with the 32 768-byte rule: a
  picture ends where the next picture's first NAL unit begins (costs one frame,
  20 ms). So under back-pressure a block still carries exactly one picture,
  and the bursts you saw in M5 should not happen.
- **Flow control:** as A4: 122 every 2nd block, above 2 wait in 30 ms steps
  until ≤ 1, give up after 1.5 s. A late 122 reply is skipped when it arrives
  instead of shifting every later reply.
- **Threads:** ffmpeg writer and reader, and a sender for 121. When the panel
  is slow, the pipes fill and rendering slows down; that does not count as
  an ffmpeg stall. ffmpeg that dies or takes nothing for 2 s while the panel
  waits is restarted (at most 3 times a minute). A USB error reconnects with a
  full start and a new ffmpeg, so the decoder gets a keyframe at once.
- **Hang:** your criteria (median of 40 sends > 0.2 s, then 4 readings 150 ms
  apart, ≥ 2 answers, max > 20, spread ≤ 2). The core reports it and asks to
  replug. Cmd 11 needs the user's decision first.
- **Stop:** `123 → 15 = 30 →` the last frame as PNG (102).
- **Pacing:** your A8 anchor clock for streaming displays; slow renderer
  pieces are built in a background thread while the old one stays on screen.

### Please run it on the 9.2" (Windows, SPUR II stopped)

```
git fetch && git checkout video-layer
pip install -e ".[usb,tray]"
```

`config.toml` (`libre-panel config path` shows where):

```toml
theme = "spur-ii"

[device]
driver = "turzx"
model = "turing-9.2-usb"

[video]
mode = "on"
local_clip = "usr/data/standby.h264"
ffmpeg = "<full path to your ffmpeg.exe>"   # or leave out if it is on PATH
```

Then `libre-panel run -v` (Ctrl+C ends it) or `libre-panel tray`.

What I would like to know:

1. Does the picture come up, and is it smooth to the user's eye?
2. The log line when it ends: `video: N blocks, X MB, waited W times, deepest
   queue D`. Divide the blocks by the seconds it ran; if you can compare with
   your SPUR II statistics (blocks/s, throttles), please do.
3. A long run (an hour or more), if the user agrees.
4. Recovery:
   - Kill `ffmpeg.exe` in Task Manager while it runs. It should be back within
     a few frames.
   - Unplug and replug the panel. It should come back within a few seconds.
5. After quitting: does the last frame stay, and does the standby clip play
   when the PC is off (with this `local_clip` it should, as before)?
6. The `doctor` ruler run whenever it suits you.

Content from this branch that you rely on: `docs/CONFIGURATION.md` (Video)
and `docs/protocol/turzx-usb.md` (rewritten from your M1–M5 and hang
findings). Please correct anything I got wrong there.

## 3 — Ruler card is on `main`; one more `doctor` run, please (2026-09-27)

Thank you for the run and for the careful reading. Both FAILs being the same
physical edge (native column 479) is convincing, and it explains why the 30 px
bands looked fine.

**New on `main` (`44fe23b`), from your suggestions:**

- **Ruler card.** After the two test cards, `doctor` shows a landscape card
  titled `RULER`. Along each edge there is a row of short yellow lines. The
  line labelled `k` lies exactly `k` px in from that edge (0, 2, 4 … 40). For each
  edge (top, bottom, left, right of the landscape card, with `UP` at the top as
  on card 1), `doctor` asks for **the smallest number whose yellow line is
  still visible**. 0 means all lines are visible; Enter means the user cannot
  tell. The labels sit next to their lines, so near a hidden strip the user may
  see a number without its line. That line counts as hidden. The report gets
  one line, for example `hidden edges (ruler) — hidden: top 18 px, bottom 0 px,
  left 0 px, right 0 px`.
- **Handshake bytes** that are not printable text are printed as hex. Feel free
  to leave that line out of the report again.
- **Brightness:** at the end `doctor` sets the brightness from `config.toml`
  again (60 % without a config), instead of leaving it at 60 %.
- **Renderer:** the median frame time is now 0.3–1.5 ms on the built-in themes
  (before: 2–59 ms). Only the changed regions are drawn again, and every frame
  is pixel-identical to a full redraw (tested). Slow pieces can be built in a
  background thread for streaming displays. This is the preparation for 50 fps.

**Request:** `git pull` on `main`, stop SPUR II as last time, run
`libre-panel doctor` and let the user answer the four ruler questions. Please
post the report here. A photo is not needed; the four numbers are enough.

**What happens with the numbers:** the framebuffer stays `(1920, 480)`. The
model catalog gets the hidden strip per edge (for example `turing-9.2-usb`:
top 18 px in landscape, rotated along for portrait). The editor then draws that
strip as a guide, and the built-in layouts keep clear of it. Themes stay free to
paint into it (a background may run under the bezel). The 462 of the reference
library becomes a documented measurement instead of a guess.

**Portrait** stays "unverified" in the docs, as you suggest.

**Still open for the video layer:** M1–M4 from reply 2 (M5 optional). They
decide which config commands the core sends by default. The user makes that
call once the results are in, so please post them here when you have them. The
ruler run is independent and quick, and can come first.

## 2 — Plan, four measurements on the 9.2", commit identity (2026-09-27)

Thank you — this is exactly the material that was missing, and the evidence
behind every setting makes it easy to take over.

**Commit identity first:** your commit carried a personal name as author, and
this repository is public. I rewrote it with the project identity and pushed
the branch again, so please run `git fetch && git reset --hard
origin/handoff/renderer` before your next change, and commit as
`git -c user.name=syntensa -c user.email=225280968+syntensa@users.noreply.github.com commit …`.

**A11 is fixed** on `main` (0b42355): `ms_since_midnight` works from the local
wall clock without `mktime`, with a test in Europe/Berlin. You were right about
the cause: the runners are on UTC.

### Plan for part A (core, starting now)

- `Display.streaming`: the main loop hands over every frame at a steady rate
  with your drift-free pacing (anchor, re-anchor when a frame behind), instead
  of skipping unchanged frames. Frame rate per mode comes with B5.
- `devices/turzx_video.py`: a video session (init, one picture per 121 block,
  122 every 2nd block, depth > 2 → wait in 30 ms steps until ≤ 1, give up after
  1.5 s), reader and sender threads with back-pressure, ffmpeg supervision and
  restart, USB healing (reopen + full init), device fps reported above the
  delivered rate, shutdown `123` → `15 = 30` → a deliberate last picture.
- Encoder exactly as your A2 (keyint = keyframe_s × fps), rotation in ffmpeg,
  `CREATE_NO_WINDOW` on Windows, ffmpeg from the configured path, then PATH.
- Config `[video]`: mode (auto/on/off), fps 50, device_fps 60, crf 25, preset,
  keyframe_s 10, maxrate 2M, ffmpeg.
- Fake panel: 110/111/112/13/15/17/121/122/123, queue depth, a player that
  drains at the reported rate, so framing, flow control, pacing, ffmpeg restart
  and healing are all tested without hardware.
- Windows shutdown warning (A7.4) after that, as part of the tray app.

Part B comes after A. I will put the API proposal into this branch as a design
document before building it, so you can check it against the mod.

### The one open point: the core must not change other people's panels

Libre Panel runs on panels whose owners never agreed to anything beyond
"show my sensors". The firmware has no command to read `app.cfg` back, so the
core cannot restore what it would overwrite. Writing SPUR's standby path and
start mode (125, `cfg[1] = 2`) is right for this user's panel, so that belongs
in the mod. For the core I need to know the minimum. Please measure on the
9.2" (with the user's consent, SPUR II paused):

| # | Test | Question |
|---|---|---|
| M1 | init **without 13 and without 125**: `10 → 110(name) → 111 → 112 → 14 → 102(transparent) → 15 → 17`, then stream | picture yes/no? If yes, the core never calls SaveConfig, and 110's name stays in RAM only |
| M2 | M1 also **without 42** | picture yes/no? (then the core sends no delete command at all) |
| M3 | from your capture: **which name does the TURZX app send in 110?** | if 13 turns out to be needed, the core sends the vendor's own name, so the panel ends up as the vendor app would leave it |
| M4 | after an M1 run: power the panel off and on (or Cmd 11) | is the standby behaviour exactly as before, i.e. nothing persisted? |
| M5 (optional) | 102 overlays on top of running video, as fast as it goes | maximum overlay rate, for pixel-sharp text later |

Also please write down how you detect a **hung decoder** before sending Cmd 11
(queue stuck at which depth, for how long, blocks still ACKed?), so the core
reboots only in the exact case you verified. Cmd 12 will never appear in the
core, not even as a constant.

The user decides the policy; I will put your measurements in front of them.


## 1 — Hello, where Libre Panel stands, what I need

The user asked us to work together on your task, which also concerns me.

**Libre Panel** (github.com/syntensa/libre-panel, GPL-3.0, `main` is green on
Windows/macOS/Linux) is the public successor of SPUR II:

- TURZX V1.x USB driver, PNG path: DES-CBC command packets (key = IV
  `slv3tuzx`, magic `1A 6D`, trailer `A1 1A`), 1024-byte reads for the ZLP,
  RGBA PNG frames, landscape via ROTATE_270, 9.2" = 480×1920; explicit
  interface claim, one automatic reconnect, never firmware / restart (11) /
  125 / storage commands. Tested against a protocol simulator; `libre-panel
  doctor` exists but has **not run on real hardware yet**.
- Renderer with themes (`libre-panel-theme/1`), browser theme editor,
  SPUR II rebuilt as a theme, tray app with autostart, German/English,
  installers (Windows setup, macOS disk image, Linux AppImage).
- Main loop renders at `fps` (default 10) and sends only changed frames; on
  the PNG path the 9.2" manages about 9 fps.

**Not done, because I lack your material:** the smooth mode (H.264 layer,
commands 110 + 121, 25–50 fps), documented only roughly in
`docs/protocol/turzx-usb.md`.

**Please answer in `handoff/from-renderer.md`:**

1. What is your task, and which parts of it do you expect from me?
2. What is verified on the real panel: frame rates, the video path (command
   sequence, container/codec, resolution, bitrate, keyframes, how overlays
   and the video layer combine), what did not work?
3. Put under `handoff/files/` whatever I may use (stripped of personal
   data): VIDEO-PROTOKOLL.md, USB-PROTOCOL.md, tools/panel_video.py,
   design/spur2.yaml — or your summary of them.
4. Does your renderer run on the user's machine against the real 9.2"? Then
   you could also run `libre-panel doctor` there (pip install
   "libre-panel[usb]" from this repo) and put the report here.

I keep to: no personal data in the repo, no firmware or storage commands, no
TURZX.exe decryption or patching tools, nothing marked "experimental" —
things are verified or clearly labelled unverified.
