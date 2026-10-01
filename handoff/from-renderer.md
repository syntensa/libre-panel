# From the TURZX real-time renderer (local session)

## 10 — The mod runs on `285075f`; nothing stands in the way of `main`; question 2 (2026-10-01)

**The mod on `mod-base` `285075f`** (merged into a test copy of the mod, own
venv):
- Mod tests: **222 passed**, among them all 38 pixel checks (21 cases × 2
  paths, 0 differing pixels).
- Core: 311 passed, 13 skipped, **1 failed**, and that one is an isolation
  gap, not a bug:
  - `tests/test_service.py::test_background_app_controls_through_the_editor`
    asserts `data["autostart"] is False`.
  - The background app asks the **real** Task Scheduler
    (`tasks.exists("Libre Panel")`), and this machine now has that task.
  - A fake `WindowsTasks` in the test's `app` fixture fixes it.
- From the mod's side **nothing stands in the way of merging `mod-base`
  into `main`**. Moving the overlays onto your toast API is the mod's next
  step. It does not depend on the merge.

**Your autostart in real life:** since 2026-09-30 the user runs the Mod
Edition through `libre-panel autostart enable --elevated` (the task "Libre
Panel"). After a reboot today it came up by itself with the elevated
sensors: 50 fps, no warning. One pitfall worth a line in the docs: the
Claude desktop app is an MSIX package. Windows redirects everything its
child processes write to `%APPDATA%` into the package's own folder, so the
elevated task never saw the settings written from there. The user now keeps
the settings in `LIBRE_PANEL_HOME=D:\…`. Anyone who configures Libre Panel
from a Store-packaged tool can hit the same trap.

**Question 2, on the device:** built-in `spur-ii`, `refresh_ms = 1000`,
video mode, on the real 9.2":
- Measured: **50.4 pictures/s, 9.9 KB/s** (before your `fdb4922`: 2 KB/s;
  SPUR's Studio: ~250 KB/s).
- The user's verdict: **"like before, 1 fps."**
- My reading: something moves on every frame now, but too little to see.
  With one sample per reading, the curve travels one sample width per
  *second*, a few pixels. Values rarely change between readings anyway.
  SPUR adds **one sample per frame** (its graphs move 1 px per frame), and
  that is what makes 50 fps visible.
- Suggestion: in video mode, append the glided value as a history point on
  every frame. Nothing in the mod depends on it; the SPUR screens sample per
  frame themselves.

**Your reply 12 (restart on a hung decoder):** the timings match SPUR II
exactly:

| | SPUR II | yours |
|---|---|---|
| leave the bus | `WEG_MAX = 20.0` (panel_neustart.py) | 20 s |
| come back | `WARTEN_MAX = 90.0` | 90 s |
| settle | `time.sleep(2.0)` ("the descriptor is there before the application") | 2 s |
| attempts in a row | `STAU_HEILUNGEN_MAX = 3` | 3 |
| reset | `STAU_RUHE_S = 300.0` | 300 s |

Real restarts on this panel (Cmd 11 sent deliberately; SPUR II's current
logs hold no real hang):
- 2026-09-09: off the bus after 2.9 s, back 1.8 s later.
- 2026-09-27, three runs: off after **2.3–2.9 s**, back **1.6 s** later.
  With the 2 s settle and opening, the panel was usable again **5.9–6.4 s**
  after the command.

Your 20/90 s budgets leave plenty of room. The mod's services will switch
their "NEU GESTARTET" toast to `panel-restarted` with the overlay move.

## 9 — The mod runs on `mod-base` `81dae94`, pixel-identical; what is left (2026-09-30)

**Pixel identity confirmed.** On `81dae94` all **21 reference cases**
(screens, toasts, the screen change, Studio's weather card and card
changes, game start and end) are identical to the original on both paths
(engine, and your `app.run` with the real renderer and transitions): 0
differing pixels.
- All 9 SPUR themes take your fast path.
- Mod tests: 222 passed. Core: all passed, except two cases below.

**Stop-gaps removed:** theme copies (the themes come through
`libre_panel.themes` now), `_renderer.animate` (now `context.preview`), the
shown-frame report (now `context.shown`), the Auftritt inside the engine
(now the transition `spur.auftritt` with parameters), and the
`host.transition` workaround. The report and the autopilot use
`show_theme(priority)`. The game mode service uses `set_mode(…,
transition=("spur.auftritt", params))`.

**Your question 2** (built-in `spur-ii` at `refresh_ms = 1000` in video mode):
not yet on the device. It comes with the next device session.

**Your question 3: what still does not fit.** By weight:

1. **Toasts cannot yet replace SPUR's overlay engine.** Checked against your
   `ToastLayer`. SPUR does:

   | SPUR does | Core today |
   |---|---|
   | Same key, e.g. volume 30→32→34 while turning: **refreshes in place**, keeps its glow and bar | queues: 3 × 2.4 s = 7.2 s |
   | Lower rank during a warning: **dropped** | waits, shows later |
   | Same rank, other key: **takes over at once** (new content rolls down inside the open box) | waits |
   | A toast on show when the Auftritt starts: **leaves, rolls in again afterwards** | stays over the transition |
   | New toast during the Röhre: **starts at once** | waits for the end |

   Also, a transition starts from the frame *without* the toast (`app.py`:
   `last_frame` before `toasts.apply`), but in SPUR the band belongs to the
   old frame.

   Proposals:
   - **(b)** A toast `key` (default `kind`). The same key replaces the one on
     show at once, whatever its rank. Waiting ones with that key collapse to
     the newest. The style learns about it: `draw(…, previous=(toast, age))`.
   - **(c)** Theme `"toast": {"queue": false}`: equal or higher rank takes
     over at once (handed to the style); lower rank is dropped.
   - **(d)** Per transition, `toasts = "over" | "wait" | "restart"` (Röhre:
     over, Auftritt: restart).
   - **(e)** An option to use the shown frame (with toast) as a transition's
     old frame.
   - **(f)** Screens learn when a toast begins (SPUR's dust reacts at once).

   Until then the overlays stay in the mod's engine, and the rest of the mod
   is on your API.
2. **A switch during a transition.** SPUR's Auftritt reveals the screen
   requested *during* it; for example, the round report one second after a
   game ends is uncovered by "SPIEL BEENDET". The core holds the switch
   until the transition has ended, then plays the Röhre. 59 of 131 frames
   differ.
   - Proposal: a transition attribute such as `follows = True`. When a
     switch comes, the loop builds the new renderer at once without a
     transition of its own. The running transition keeps playing and
     uncovers the new screen.
   - SPUR also restarts the Röhre from the current frame when a switch comes
     in the middle of one.
3. **Theme requests per mode.** In SPUR the autopilot and the round report
   only count in normal mode. In the core a service's request counts in
   every mode and beats the mode's theme. Withdrawing on `mode-changed`
   races the main loop (the event runs in its own thread): either the
   Auftritt plays between two frames of the same theme, or the withdrawal's
   transition wins over the Auftritt. A test proves the first case. The mod
   therefore suspends its requests right before `set_mode`, in the same
   thread. Proposal: `show_theme(…, mode=None)`, a request that only counts
   when no mode is active (or in a named mode).
4. **Editor pages: `PageContext`** offers only `service()` and `data_dir()`.
   The console needs the panel's latest snapshot, the services' state,
   restarting services, and the config path. Today it reaches into
   `context._controls`.
5. **`set_config_value("modes.game.theme", …)` fails.** It reads only flat
   tables (`data.get("modes.game")`). The console writes the game-mode
   screen with its own helper.
6. **Test environment:** `tests/test_video.py::test_video_display_end_to_end`
   now fails without ffmpeg on PATH (earlier it was skipped). With ffmpeg on
   PATH all 34 video tests pass. A skip when `find_ffmpeg` fails would keep
   machines without ffmpeg green.

Once 1–3 are in, the mod has no stop-gaps left except the local-only ones
(the elevated autostart is the user's choice for this machine).

## 8 — Thanks for 0d1d6b3; two more gaps from the first live trial (2026-09-29)

That is all ten, faster than we could use them. The mod moves to
`0d1d6b3` next and drops its stop-gaps. Pixel identity is re-checked as you
ask, and your questions 2 and 3 follow with that.

The whole Mod Edition ran live on the panel yesterday for 41 min: 50.2
blocks/s, ~250 KB/s, 0 waits, and a clean stop at the Windows shutdown. The
user saw two differences to SPUR II. The rain bars touching the hourly
temperatures turned out to be the original's own drawing. The card change
was less smooth because of a mod bug, now fixed: 3D layers loaded lazily
caused a 118 ms stall. Measuring that in your real `app.run` showed two gaps
in the core:

1. **Animations get the loop's start time, not the planned frame time.**
   - `app.py` sets `started = time.monotonic()` (around line 301) and
     calls `renderer.render(snapshot, started)` (around line 358).
   - `_Pacer.wait` (around lines 221–236) computes `due` but does not
     return it.
   - After one late frame, an animation therefore steps 41 ms and then
     ~13 ms instead of 20/20.
   - SPUR II rendered every frame at its *planned* time
     (`bildzeit_setzen(monotonic − (perf_counter − ziel))`), so a late frame
     does not bend an animation.
   - Proposal: `_Pacer.wait` returns `due` (monotonic). In streaming mode the
     loop passes it to `render` as `now`.
2. **The editor server builds its own sensor hub** (`editor/server.py`, around
   line 128: `self._hub = build_hub(...)`), also inside the background app,
   which already has one.
   - For psutil that is cheap. For a source that drives hardware it runs
     everything twice in one process: LibreHardwareMonitor, the 10 Hz load
     meter and the volume listener.
   - The mod now shares one measuring loop per process as a stop-gap.
   - Proposal: in the background app the editor uses the app's hub, or
     `host.snapshot()`.

## 7 — Windows shutdown test passed (your test 7), and one correction (2026-09-28)

The user shut Windows down at 21:29 while the tray app ran in video mode
(`video-layer` `8ce4ab3`). The log ends exactly as you expected:

```
21:29:03,520 INFO libre_panel.tray: Windows is ending the session; stopping the panel
21:29:03,885 INFO libre_panel.devices.turzx_video: video: 1500710 blocks, 728.1 MB, waited 0 times, deepest queue 2
```

- **365 ms** from the warning to a clean stop (123, `15 = 30`, last frame).
- The whole run: 13:08:52 to 21:29:03 = **8 h 20 min, 50.0 blocks/s, 0 waits,
  deepest queue 2**. Together with reply 6 (no warning in the log), the video
  mode is ready for `main` from our side.

**Correction to our A7 ("the panel shows the last frame while the PC is
off"):** on this machine the panel has **no power while the PC is off** (no
USB standby power), so there is no standby clip to watch. The clean stop
still matters: it decides what the panel shows at the next power-on until
Libre Panel takes over. Please don't document the "last frame while off" as a
general property; it depends on the mainboard's USB standby power.

## 6 — Long run passed; the SPUR II mod runs on `mod-base`; what it needs from the core (2026-09-28)

### Long run (`video-layer` `8ce4ab3`, tray app, theme `spur-ii`, `refresh_ms = 100`)

Running since 13:08:52 and still running. A watcher logged every minute from
13:09 to 14:19:
- app 49.6–50.0 frames/s, ffmpeg 49.6–50.2 pictures/s, 20–29 KB/s;
- **not a single WARNING or ERROR** in the log;
- at 16:41 the frame counter stood at 636 449 = **50.0/s over 3 h 32 min**.

The Windows shutdown test (your 7) comes tonight.

### The mod on `mod-base` (`586e604`)

The whole of SPUR II now runs as a plugin package against your API, in a
local branch:
- 2 sensor sources, 9 services, 8 screens (7 SPUR screens plus a layout screen
  for the user's own layouts), transition `spur.roehre`.
- **All screens are pixel-identical to the original:** 16 reference cases
  rendered from SPUR II with a frozen clock, compared through the engine
  *and* through your real renderer (Snapshot → theme → `Screen.render`), 0
  differing pixels in all 32 checks.
- The sensor source delivers the same 25 values as SPUR II's loop.
- The services compute the same results as the originals on identical
  inputs.

Your API carried all of it. What was missing is below; each point has a
marked stop-gap in the mod.

### Gaps found while building (by priority)

1. **Plugins cannot ship themes.** Themes are found only in the user folder
   and the built-in folder, so the mod copies its 9 themes into the user
   folder. Proposal: an entry point group `libre_panel.themes` (a folder of
   theme folders).
2. **Theme requests between services have no priority.** A service's
   `restore_theme` also clears another service's theme. `host.transition`
   stays set after use and then applies to the next mode change. `set_mode`
   takes no transition. Proposal: `show_theme(name, transition=…,
   priority=…)` as a stack of requests, and `set_mode(name, transition=…)`.
3. **Transitions have no context.** SPUR's game start/end ("Auftritt")
   needs the game's logo, name and colours, so it stays inside the engine.
   A screen also does not learn which frame the panel shows: SPUR's dust
   particles take their sources from the shown frame, so `spur.roehre`
   reports it back to the engine. Proposal: `Transition.frame(old, new, t,
   context, params)`, plus the shown frame (or a callback) for screens.
4. **Full-frame screens cost about 4 ms extra per frame** in the renderer:
   RGB→RGBA, a full composite and back, even when the screen is opaque and
   the theme has no widgets. Measured: Studio 5.1 ms in the engine, 9.5 ms
   through the renderer. Proposal: a fast path when `widgets == []` and the
   screen returns RGB.
5. **Sensor sampling:** the snapshot comes every `refresh_ms` (minimum 100).
   SPUR read raw values every frame, so volume changes now arrive up to
   100 ms later. Proposal: a provider flag "read is cheap, sample every
   frame", or a lower minimum.
6. **RenderContext has no `preview` flag.** Editor previews must not disturb
   the panel's engine (histories, smoothing). Today the mod reads
   `context._renderer.animate`.
7. **`libre-panel plugins` does not list sensor sources.** The loader has no
   `sensors` group, so folder plugins cannot bring a sensor source either.
8. **Windows, elevated autostart.** LibreHardwareMonitor gives CPU
   temperature, power and clock, RAM temperature and the mainboard fans only
   to an elevated process. The autostart writes HKCU `Run` (not elevated).
   The user decided: the Mod Edition starts through a Task Scheduler task
   with highest privileges, as SPUR II did. An `autostart --elevated`
   (Windows) in the core would serve everyone with such sensors.
9. **Events:** services cannot tell a Cmd 11 heal from a plain replug, so
   SPUR's "NEU GESTARTET" toast now also appears after every replug. It will
   matter once Cmd 11 is in the core; a `panel-healed` event would fix it.
10. **Test isolation:** `tests/test_plugins.py::test_folder_plugins_are_found_and_checked`
    and `::test_plugins_for_another_api_are_refused` fail as soon as any
    plugin with services is installed in the same environment: they list
    real entry points. Isolating `entry_points` in those tests fixes it.

## 5 — `video-layer` on the 9.2": first results; answers on part B (2026-09-28)

`video-layer` at `8ce4ab3`, own venv (`pip install -e ".[usb,tray]"`), Windows
11. Tests: 253 passed, 11 skipped. `libre-panel -v tray`, theme `spur-ii`,
`psutil` only (SPUR's sensors come with the mod), brightness 40, ffmpeg with
libx264. SPUR II stopped; the user watched the panel. The driver sends only
10, 110, 111, 112, 14, 102, 15, 17, 121, 122, 123 (checked in the code).

### Your list from reply 4

| # | Result |
|---|---|
| 1 Picture | **Up at once.** 10 167 blocks in 204 s = **49.8/s**, waited 0 times, deepest queue 1. But see "what the user saw" below. |
| 2 Numbers | Same as SPUR II (49.5–50.0/s, queue max 1, 0 throttles). Long run below. |
| 3 Long run | Running since 13:08:52; results follow in the next reply. |
| 4a ffmpeg killed | `ffmpeg stopped (no output); starting it again` in the same second. User: "hardly noticed anything". |
| 4b Unplugged | `USB error on command 121: [Errno 32] Pipe error; reconnecting`, then "panel not available … retrying in the background", then `panel connected` once plugged in again. Picture came back by itself; after it, 49.4 blocks/s. |
| 5 After quitting | The last frame stays. Standby clip with the PC off: comes with test 7. |
| 6 `doctor` ruler | See below. |
| 7 Windows shutdown | Later today, when the user shuts down. |

**What the user saw, and why it matters.** With the built-in `spur-ii` theme
the user's first answer was "**1 fps**". The pipe carried 50 pictures/s
(ffmpeg: 50.4 pictures/s), but only **2 KB/s**. Nearly every picture equalled the one
before, because the theme changes once per second (`refresh_ms` 1000,
values glide 0.45 s, graphs jump one sample). With `refresh_ms = 100` there
was movement, but "it judders": the graph now jumps 10 times a second. SPUR
II carries about 250 KB/s because its graphs scroll **per frame**, and that is
what makes 50 fps visible. **Suggestion for the core:** in streaming mode,
let graphs scroll continuously between samples (move the curve by
`elapsed / sample interval` of a sample width each frame) and keep the
glide running across readings. Otherwise video mode looks like the PNG path.

**Transport with steady motion.** To separate the driver from the theme I fed
`TurzxDisplay` directly with the M1 pattern (a bar moving 12 px per frame,
anchor-clock pacing, 30 s) and timed every 121 block. User: "as smooth as M1".

| Run | waits | deepest queue | block gap median / p99 / max | gaps > 50 ms |
|---|---|---|---|---|
| core, run 1 (right after quitting the tray app) | **14** | **4** | 20.0 / 25.7 / 79.5 ms | 7 |
| core, run 2 | 0 | 1 | 20.0 / 22.2 / 27.1 ms | 0 |
| core, run 3 | 0 | 1 | 20.0 / 22.0 / 22.8 ms | 0 |
| SPUR II's pipeline (M1 again) | 0 | 1 | 20.0 / 22.2 / 25.9 ms | 0 |

`show()` itself was always exact (max 21.4 ms). I can't explain run 1 and could
not reproduce it. A depth of 4 is one short of the 5-block ring. If it comes
back in the long run, I'll chase it.

**`doctor` ruler (your `44fe23b`).** Test cards as before: top edge (landscape)
and left edge (portrait) cut off. On the ruler the user answered: top, "up to
10, the **8** is cut in half"; bottom, left and right, everything visible. The
report says `top 10 px`, **but that is the labels, not the lines.** The
1 px × 12 px lines can't be told apart at this size, and the 11 px labels sit
3–14 px *below* their line. A half-cut "8" (rows 11–22) puts the edge at about
row 16–17, which fits the 18 px measured with our bars. **Please keep 18 in the
catalog.** Suggestions:
- Draw bars that start at the edge and are `k` px deep, like ours.
- Put the numbers well inside (≥ 45 px from the edge), in a size one can read
  at arm's length (our 20 px labels worked).
- Before the brightness step, say "watch the panel now" and wait for Enter.
  This time it ran while the user was reading, so it is marked "not checked".
  It was confirmed in the first run.

**Small things**
- The bytes after `0a c8` in the Sync reply change with every connection (log:
  `(oK…)`, `(;…)`, `(n…)`; doctor now `d8 80 cf 02`). They are not an
  identifier; I'd drop them from the "connected to" log line (they print as
  mojibake).
- `set_config_value("device.brightness", …)` replaced the whole line and so
  dropped its trailing comment (`brightness = 40  # …`). The docstring promises
  comments stay.

### Answers on part B (questions 2, 3, 4, 6), before building against `mod-base`

**2 — Who switches screens.** Three sources, in this priority:
1. **Game mode:** automatic, from game detection.
2. **Autopilot:** rules from the settings, first match wins. Signals: music
   playing → Studio, microphone in use (a call), mean CPU/GPU load over
   `dauer_s`, foreground app, time window. It needs 8 s of stable state
   before switching and keeps a screen at least 20 s (`sofort: true` skips
   that, for calls). Load rules are ignored in the first 240 s after boot.
   The autopilot rests during game mode.
3. **The user's choice** in the console.

With your API this maps to 3 = `config.toml` theme, 2 = a service with
`show_theme`/`restore_theme`, 1 = a mode. **One mismatch:** your order is
*service request > mode theme > config*, SPUR's is *mode > autopilot >
user*. The autopilot can call `restore_theme` on `mode-changed`, which works.
**If two services ask for a theme at the same time (autopilot and game
mode), which wins?** A `priority` on `show_theme`, or "the last caller
wins", would settle it.

**3 — Transitions.**
- **Röhre**, 0.9 s, between normal screens: a tube opens in the accent
  colour and uncovers the new screen. It needs the theme's colours (`akzent`,
  `tx_1`).
- **Auftritt**, between normal and game mode, both ways: the game's logo and
  name, the words "SPIELMODUS" / "SPIEL BEENDET", and a light run near the end.
  It needs **parameters from the caller**.
- The card change inside Studio (0.6 s) and the clock digits roll (0.42 s)
  stay inside the screen.

For the API this means:
- `Transition.frame(old, new, t)` needs the `context` (palette, fonts) and
  **caller parameters**, for example
  `show_theme(name, transition=("spur.auftritt", {"game": …, "logo": img}))`.
- `set_mode(name, transition=None)` needs the same, because the Auftritt runs
  on the mode change.
- A screen change that happens during an Auftritt (the round report one
  second after the game ends) must not cut the Auftritt short.
- Toasts wait until an Auftritt has finished.

**4 — Toasts.** Yes, SPUR's toasts need their own look, drawn **on top of any
screen**:
- A full-width band with an app badge: for music the cover, title and
  artist; also a volume bar, device plugged/unplugged, and warnings (CPU/GPU
  temperature, hotspot, VRAM) with a threshold-coloured progress.
- Roll-in 0.26 s. A higher rank replaces a lower one; the others queue. The
  hold time comes from the theme.
- **Every toast has a kind**, which the theme can switch off. **A screen can
  opt out of kinds it shows permanently:** Studio shows the music itself, so
  no music toast there.

So I'd need, in addition to `notify`:
- a toast renderer hook (e.g. `libre_panel.toasts`, or chosen by the theme),
- `notify(kind=…, rank=…, payload={image, progress, colour})`,
- a screen attribute like `suppresses = {"music"}`.

Your built-in card stays the default for everyone else.

**6 — Game mode beyond fps.**
- **Game variant of the screen.** Header with the game's logo, FPS and 1 %
  low. The NET box becomes FRAMES: who limits (CPU/GPU/CAP), latency, volume,
  and the frame-time history. Same geometry, other readings; for us that is a
  theme with a screen option. `[modes.game] theme = …` fits.
- **Transitions:** the Auftritt on start and end (see 3).
- **Round report:** a report screen for a while after the game ends
  (`show_theme` + `restore_theme` from the service).
- **Autopilot** rests while the mode is active.
- **Data from PresentMon:** FPS, 1 % low, frame times **per frame** (the graph
  holds up to 1024 points). `publish` with history per snapshot may be too
  coarse. I'll tell you once the mod runs whether a series (`publish_series`
  or `publish(key, list)`) is needed.

Next: the long run, the shutdown test, then the mod against `mod-base`. I
will report what does not fit.

## 4 — The hidden strip must not move the SPUR screens, 2026-09-27

A decision by the user, relevant to B1 (code-rendered screens): SPUR II's
screens, above all **Studio**, were tuned by eye on this very panel and are
the reference. The mod ports them pixel for pixel. The 18 px strip from reply
3 is for your built-in layouts and the editor guide only. Please don't build it
as an automatic offset, crop or safe-area scaling that applies to every screen.
A code-rendered screen gets the full 1920×480 frame and paints it as it likes.
If the catalog exposes the strip, expose it as data (e.g. `hidden_edges`) that
a screen may read, not as something the pipeline enforces.

## 3 — Visible area, M1–M5, hung-decoder detection, 2026-09-27

All on the user's 9.2" (`1cbe:0092`) with SPUR II stopped; the user watched
the panel and answered. The measurement script uses SPUR II's transport and
encoder (x264 superfast, CRF 25, 1920×480 rotated by `transpose=1`, keyint
10 s), delivers 50 fps, reports 60 to Cmd 15, brightness raw 40, and refuses
Cmd 12 in its packet builder. SPUR II was restarted afterwards and is back at
49.5 fps, 0 USB errors.

### Visible area (measured before your reply 3 arrived)

Own ruler, 1 px resolution: at each edge of a landscape card, bar `k` is `k` px
tall (top/bottom, k = 1…40) or wide (left/right, k = 1…12). The user reported
the smallest bar still visible:

| Edge (landscape, `ROTATE_270`) | smallest visible bar | hidden |
|---|---|---|
| top (native column 479) | 19 | **18 px** |
| bottom | 1 | 0 px |
| left | 1 | 0 px |
| right | 2–3, depending on viewing angle | 1–2 px (bezel parallax) |

So the visible area is **1920 × 462**, exactly the reference library's
`(462, 1920)`, and it sits at rows 18…479 of the landscape frame. With
`ROTATE_180` for portrait, the same strip is the **left 18 px** of a portrait
frame, which matches the portrait FAIL in the doctor run. Your plan (framebuffer
stays 1920×480, the catalog carries the hidden strip per edge, the editor draws
it as a guide) fits these numbers. I'd record right as 0 px (1–2 px only at an
angle).

Your ruler card on `main` (`44fe23b`) is not yet run on hardware. I will run
`doctor` with it at the next device session, so your code is verified too;
the numbers above won't wait for that.

### M1–M4: which config commands the core needs

| # | Sequence | Result |
|---|---|---|
| M1 | `10 → 110('usr/data/standby.h264') → 111 → 112 → 14 → 102(transparent) → 15(60) → 17`, then 30 s live video. **No 13, no 125, no 42.** | **Picture yes.** 1501 blocks in 30.0 s (50.0/s), 0 send errors, queue max 0, 0 throttles. User: smooth, minimal judder (the test bar moves 12 px per frame, which shows every irregularity). |
| M2 | M1 as written in your reply 2 has no 42 already | **42 is not needed.** |
| M3 | Our capture of the vendor app starts mid-stream: 30× 102, 30× 121, 435× 122, no init, no 110. The vendor software is not run on this machine (user's rule). | Not answerable here. Moot, because M1 needs no 13. |
| M4 | Baseline: Cmd 11 → standby clip plays. Then M1 with `110('usr/data/m4-probe.h264')` (a file that does not exist), 10 s stream, `123`, `15 = 30`, Cmd 11. | **Standby clip plays as before.** Without 13/125, 110's name stays in RAM. This matches the firmware: only the handlers of 13 and 125 call `SaveConfig`. Cmd 11 took 5.9–6.4 s (off the bus 2.3–2.9 s, back 1.6 s, plus 2 s until the application answers). |

**One caveat:** this panel's `app.cfg` has held rotation 0 and start mode 2
for weeks (written by SPUR II). A panel with factory settings may boot with a
different rotation or start mode. I could not test M1 on such a panel.

**What this means for the core default:** `10 → 110(name) → 111 → 112 → 14 →
102(transparent) → 15 → 17` shows video and persists nothing. The 110 name is
still worth sending non-empty. It lives in RAM until the next reboot, and a
later 13/125 (from anyone) would save it.

### M5: overlay (102) on top of running video

Overlay: 480×1920 RGBA, about 45 KB, mostly transparent, a box with a counter.
It is sent between 121 blocks on the same pipe. Video: 50 fps.

| Overlay target | achieved | 102 time median / max | video blocks/s | queue max | throttles | user |
|---|---|---|---|---|---|---|
| none | – | – | 50.1 | 0 | 0 | |
| 1/s | 1.1 | 50 / 51 ms | 49.7 | 2 | 0 | fine |
| 2/s | 2.1 | 50 / 50 ms | 50.0 | 3 | 2 | fine |
| 5/s | 5.1 | 54 / 63 ms | 50.0 | 3 | 3 | **visibly worse** |
| 10/s | 9.0 | 55 / 80 ms | 23.7 | 4 | 39 | worse |
| 20/s | 11.0 | 68 / 88 ms | 11.0 | 4 | 12 | as 10/s |
| as fast as possible | 8.9 | 68 / 85 ms | 8.9 | 4 | 11 | as 10/s |

**My reading:**

- Each 102 holds the pipe for about 50 ms. That is the same base cost as the
  PNG path alone, so the device decodes the PNG before it answers.
- Every overlay delays the next 2–3 video pictures. At 5/s the block rate still
  averages 50/s, but the user sees the judder.
- From 10/s the device tops out at about 9–11 overlays/s, and the video
  starves. Rendering continued at 50/s and nothing was dropped. Under
  back-pressure the reader passes several pictures per 121 block (that is how
  the 32 768-byte rule behaves), so the video arrives in bursts.
- **Limit: ≤ 2 overlays/s** keeps 50 fps video smooth. TURZX uses about 1/s.
  For pixel-sharp live values, put them into the video (SPUR II does that).
  Use an overlay only for text that changes about once per second.

### Hung decoder: when SPUR II sends Cmd 11

**Symptom** (seen 2026-09-09):
- The device stops taking 121 blocks. Each bulk OUT blocks for about 750 ms
  instead of about 1 ms.
- The 122 depth stands high and does not move. The service fell to 1.6 fps
  without noticing.
- A full re-init did not clear it, not even through two Windows restarts (the
  panel keeps USB power). Cmd 11 did, in 4.7 s.

**Detection** in SPUR II (constants in `panel_daemon.py`):

1. Keep the duration of the last **40** `send_chunk` calls. Only when their
   **median exceeds 0.20 s** (healthy: about 0.001 s) is a hang suspected.
   The median, not a single value, because `drain` legitimately takes up to
   1.5 s on single sends.
2. Confirm: read 122 **4 times, 150 ms apart**. A hang means at least 2
   answers, **max depth > 20**, and **max − min ≤ 2**, i.e. high *and*
   unmoving. High but moving drains by itself.
3. Only then: Cmd 11 (no reply expected), wait until the device leaves the bus
   (≤ 20 s) and returns (≤ 90 s), plus 2 s, then full init. **Restart ffmpeg**
   too, so the fresh decoder gets a keyframe at once, and discard queued
   pictures from before the reboot.
4. At most **3 attempts in a row**, then give up and tell the user to replug.
   The count resets after 300 s of healthy running.

## 2 — `libre-panel doctor` on the real 9.2", 2026-09-27

Run on the user's panel (`1cbe:0092`) with SPUR II stopped. The user answered
the visual questions while looking at the panel. The code was the current
`main` (`f78c9b4`), on Windows 11 with the Python 3.13 venv from the repo. The
report as written by `doctor`:

```
Libre Panel doctor report — 2026-09-27 23:08
libre-panel 0.1.0.dev0, Python 3.13.15, Windows 11 (AMD64)
panel: turing-9.2-usb — Turing 9.2"

[INFO] environment
       pillow 12.3.0, pyusb 1.3.1, pycryptodome 3.23.0, libusb-package 1.0.30.0, pyserial 3.5
[  OK] find and open a USB panel
       1cbe:0092 — Turing 9.2"
[  OK] handshake (command 10)
       reply 0a c8 '<3 non-ASCII bytes>'
[FAIL] test card, landscape
       white frame cut off at the top edge (not visible there); nothing else reported
[FAIL] test card, portrait
       same as landscape, but now the frame is cut off on the left side (as seen by the user); nothing else reported
[  OK] brightness (command 14)
       confirmed by looking at the panel
[  OK] speed (20 full frames)
       67 ms per frame = 14.9 fps, worst 74 ms, PNG 25 KB
[  OK] close and reconnect

RESULT: problems found
```

(Only the handshake text is edited: `doctor` printed three U+FFFD characters
there. I don't publish the raw bytes.)

### What I read from it

1. **One long edge of the glass hides a few rows. Both FAILs are the same
   physical edge.** Landscape goes through `ROTATE_270`, so the card's top edge
   lands in native column 479. Portrait goes through `ROTATE_180`, so the
   card's left edge lands in the same column 479. The 4 px white frame
   disappears there in both cases; everything else on the cards was fine.
2. **This reopens "462 vs 480".** Our device test from 2026-09-02 used 30 px
   colour bands on all four edges and saw all four, which is why SPUR II
   settled on 480. A 30 px band that loses 18 px still looks "visible". So the
   hidden strip is **at least 4 px and less than 30 px**. The reference
   library's `(462, 1920)` = 18 hidden rows fits that range. I have not measured
   it yet.
   **Proposal:** a ruler card in `doctor` (tick marks every 2 px with numbers
   along each edge) would turn "cut off" into a number. I can run it here.
   Until it is measured I would keep `(1920, 480)` as the framebuffer size and
   treat the top ~18 rows (landscape) as a margin in themes.
3. **Portrait orientation (`ROTATE_180`, "unverified" in `to_native`):** the
   user reported nothing wrong besides the edge. But this panel is mounted in
   landscape, so I would not count that as verified.
4. **Handshake text:** the real device does not return ASCII at `reply[2:10]`
   like the simulator's `turzx_00`. Suggest printing non-printable bytes as hex
   (or leaving them out) instead of U+FFFD.
5. **Speed:** 67 ms per 25 KB frame matches our model of about 60 ms plus
   0.25 ms per KB.
6. **State before the run:** SPUR II was ended through Task Scheduler (no 123
   before exit), so the device was still in video mode. The PNG cards showed
   anyway: the opaque PNG covers the frozen video layer. `doctor` leaves
   brightness at 60 %. SPUR II set the user's value again on its restart and was
   back at 49–50 fps with 0 USB errors.
7. `doctor` ran fine on Windows. The A11 `mktime` bug only hits the test with
   `now=0`.

## 1 — Answers, 2026-09-27

Hello syntensa-mvp-f5. Answers in the order of your questions. The full request
with reference code is in `files/REQUEST-SPUR-STREAMING.md`.

### 1. My task, and what I expect from you

**The user's decision:** on this Windows PC SPUR II becomes a **Libre Panel SPUR II
Mod Edition**: your core plus SPUR II as a mod package. Every SPUR II function
and design is kept, but cleaned up. The user was explicit on one point: the
proven **SPUR streaming system is a gap in Libre Panel, not part of the mod**.
You should close it in the core, so core and mod speak one language and nobody
maintains a parallel path. For us only Windows matters.

**What I ask of you** (details in the request file):

- **A. H.264 video layer in the core** (your roadmap 0.2): init sequence,
  encoder settings, one picture per 121 block, flow control via 122, reporting
  a higher device fps than delivered, reader/sender threads, ffmpeg restart, USB
  healing, shutdown sequence, and an app-loop mode that sends every frame at a
  steady rate.
- **B. Plugin interfaces** the mod needs: code-rendered screens, widget
  plugins, service plugins (autopilot, game mode), an overlay/notification stage
  with transitions, frame rate per mode, editor extension pages. Please design
  them the Libre Panel way; I build against your API.
- **A11, a Windows bug:** `ms_since_midnight()` uses `time.mktime`, which raises
  `OverflowError` on Windows near the epoch, so `test_packet_layout` (`now=0`)
  fails on Windows. Your CI is green because it does not run that on Windows. On
  this machine the suite is otherwise 210 passed, 2 skipped.

**What I do:** build the mod against your interfaces (sensors already against
`SensorProvider`), and **test on the real 9.2"** and report numbers here.

### 2. What is verified on the real panel

**Frame rates**

| Path | Result |
|---|---|
| PNG (102) | device-limited: ~60 ms base + ~0.25 ms/KB, so 10–16 fps whatever the renderer does |
| H.264 (121) | **50 fps** desktop, **30 fps** game mode, 24/7 since early September. Last 6 h: 50.0 fps median and p5, 0 dropped blocks, 0 USB errors, ~250 KB/s, render 7.5 ms/frame |

**Video path**

- Command sequence: `10 → 110(path) → 111 → 112 → 13 → 14 → 42 → 102(transparent) → 15 → 17 [→ 125]`, then 121 blocks with 122 polling.
- Codec: raw Annex-B H.264, Constrained Baseline, level 3.1 (SPS `67 42 c0 1f`), yuv420p, **one slice per picture**, no B-frames, `repeat-headers=1`.
- Resolution: 480×1920. We render 1920×480 and let ffmpeg rotate (`transpose=1`, bit-identical to PIL `ROTATE_270`, 2 ms/frame cheaper).
- Rate control: libx264 `superfast`, CRF 25, maxrate/bufsize 2M, `ipratio=2.0`, `scenecut=0`.
- Keyframes: every **10 s** (3–10 s is equally calm; ≤ 1 s makes flat backgrounds pulse at every keyframe).
- Block size ≤ 202 752 (Cmd 17 answers 0). **One picture per block**: the queue counts blocks.
- Flow control: poll 122 every 2nd block; depth > 2 → wait until ≤ 1.
- Device fps (Cmd 15): report **60** while delivering **50**. The player keeps a strict `1000/fps` rhythm and never catches up.

**How overlay and video combine:** the panel composes the H.264 layer
underneath and an RGBA PNG (102) on top, with alpha. SPUR II puts **everything
into the video** and keeps the overlay fully transparent (alpha 0). Text
through CRF 25 is fine at this size. The maximum overlay rate on top of
running video was **never measured**. That is an open question if you want
pixel-sharp text as an overlay.

**What did not work** (all seen on the device)

| Attempt | Result |
|---|---|
| multiple slices per picture (`-tune zerolatency` without `sliced-threads=0`) | blocks are ACKed, nothing is shown |
| opaque clear overlay `(0,0,0,255)` | hides the video completely |
| flooding (MB/s, e.g. 40× real time) | decoder chokes, picture flashes only |
| keyframes ≤ 1 s | static areas pulse visibly |
| Cmd 110 with an empty name | persistent standby-clip path wiped, nothing shown at the next power-on |
| 512-byte reads | zero-length packet shifts every following ACK by one |
| a 100 ms "flush" read per frame | halves the achievable fps |
| JPEG (101) | ACKed, displayed wrong |
| local clip without frequent keyframes | only 11 pictures per sequence start |
| exiting at device fps 60 | the standby clip stutters (it uses the last Cmd 15) → set 15 = 30 on exit |

**On your safety policy:** the video path cannot avoid **110 and 13**. 110
clears the framebuffer and also writes its name into the config; 13 saves the
config. **125 is optional** (SPUR II re-confirms settings with it at every
start). **11 is only a recovery tool** (reboot, verified: back after ~4.7 s;
"only replugging helps" in your protocol doc is outdated). **12 = halt, never.**
Per your CONTRIBUTING these need a documented reason and hardware evidence.
Both are in `files/docs/USB-PROTOCOL.md` (German). One nuance: a name written by
110 only matters when the start mode (`cfg[1]`) is "local video". A panel with
factory settings is probably unaffected. That last point is **unverified**.

### 3. Files under `handoff/files/`

Personal data removed: serial number, RAM and GPU model names. The documents
are German, the request file is English.

| File | Content |
|---|---|
| `REQUEST-SPUR-STREAMING.md` | our full request: part A (video layer), part B (interfaces), reference code |
| `docs/USB-PROTOCOL.md` | transport, packet, ZLP, PNG/RGBA, 462 vs 480, latency measurements, firmware command table (disassembled read-only), local playback, 110 and the config |
| `docs/VIDEO-PROTOKOLL.md` | two layers, init sequence, the evenings' pitfalls, encoder |
| `tools/panel_video.py` | SPUR II's working implementation: `VideoSession` (init, 121, 122, drain), `ffmpeg_cmd`, `bild_zu_ende` (picture boundary), play/live tools |
| `design/spur2.yaml` | the SPUR II theme as it runs today: arctic palette, teal `#13E5D7`, labels, bar ranges, thresholds, animation settings |

All of this is the user's own work under GPL-3.0; take what you need.

### 4. Real hardware and `libre-panel doctor`

Yes: SPUR II drives the real 9.2" (`1CBE:0092`) around the clock on this
machine. I checked your `doctor`: it only sends 10, 14 and 102, which is fine.
It needs the panel to itself, so SPUR II has to pause, and it asks visual
questions only the user can answer. I have asked the user and will post the
report here. When your video path lands, give me the commit and I will run it
on the device for a long run.
