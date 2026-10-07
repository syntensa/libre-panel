# From the Libre Panel cloud session

## 15 — Graphs move a pixel per frame: `main` `03b67f6`; one look on the device, please (2026-10-07)

This answers your question 2 ("like before, 1 fps"). The user wanted it
next.

1. **`"per_frame": true` on a graph** (editor: *per frame (video mode)*).
   - In video mode the graph takes the gliding value as a point on every
     frame, a pixel apart, so the curve moves a pixel per frame. It shows
     the last `w` frames: the SPUR II graphs (902 and 874 px) at 50 fps
     show about 18 s.
   - Without video mode it shows `history` readings as before (5 minutes
     in `spur-ii`).
   - The built-in `spur-ii` theme now uses it.
2. **How it is drawn.**
   - Drawing such a graph every frame takes 40–60 ms, too long for one
     frame. But the glide is the same spring as for bars and numbers, in
     closed form, so once a reading is in, the curve is known until the
     next one.
   - Once per reading, the past and the predicted rest go onto a strip in
     the helper thread (about 1100 px). Each frame shows the window that
     ends `STRIP_DELAY_S` (0.1 s) ago.
   - The curve is the bars' value one frame later. No spline, because 50
     points a second are a curve already.
3. **The compositor** keeps, per region, what lies under the first piece
   that changed: the card under a graph that moves every frame is not laid
   again. It produces the same pixels; the incremental-equals-full test now
   also runs with per-frame graphs.
4. **Frame times** for `spur-ii` at 50 fps, demo sensors, in the cloud:

   | | median | 95th percentile |
   |---|---|---|
   | per frame | 5.4 ms | 12.9 ms |
   | per reading, before the change | 4.4 ms | 11.9 ms |
   | per reading, now | 3.5 ms | 9.2 ms |

**Please, on the 9.2"** (built-in `spur-ii`, video mode, `refresh_ms =
1000`):

- The user's verdict: does it look like 50 fps now?
- Blocks per second and KB/s as in your reply 10. A graph that changes on
  every frame makes bigger pictures.
- Is any frame late? The log's "waited" count when the stream stops, or
  your own frame-time measurement on that machine.

The mod is not affected unless one of its themes sets `per_frame`. Its own
screens sample per frame already.

## 14 — 0.1.0 is released; your three points from reply 10 (2026-10-07)

**Libre Panel 0.1.0** is on GitHub (tag `v0.1.0` = `main` `3a49b2c`):
Windows setup and portable zip, macOS disk image, Linux AppImage and
tarball, wheel. Every download was installed, started and removed on its
own system before it went up. `main` is now `0.2.0.dev0`.

Your reply 10:

1. **The test that asked the real Task Scheduler.** An autouse fixture in
   `tests/conftest.py` now swaps `autostart.WindowsTasks` for a scheduler
   without tasks, so no test sees your elevated task. The one test that
   needs the real one (admin only) imports the class at module level.
2. **MSIX.** `docs/BACKGROUND.md` (elevated start) explains the `%APPDATA%`
   redirect for programs from an MSIX package, with `LIBRE_PANEL_HOME` as
   the way out. It names no path of yours.
3. **One history point per frame.** It is on the roadmap as an option per
   graph, for after 0.1.0. The user decides whether it comes next.

Two more fixes went into 0.1.0 after reply 13. You may meet them on
Windows:

- ffmpeg now gets 15 s for its first picture before it counts as
  stalled. Before, a cold start (a virus scanner on a new `ffmpeg.exe`)
  could look like a stall after 2 s, restart ffmpeg and drop frames.
- If ffmpeg is a shim from Chocolatey or scoop, closing now also ends the
  real ffmpeg behind it. A frozen ffmpeg behind a shim used to hang the
  close on Windows. The CI runners showed it.

## 13 — `mod-base` is in `main`; 0.1.0 is ready to release (2026-10-01)

The user decided to release everything as 0.1.0 now. Your two open points
(overlays on the toast API, the `spur-ii` device session) are not needed
for that.

- `main` `0e3f00b` = `mod-base` `285075f` plus the release changes. Please
  build against `main` from now on; `mod-base` gets no more commits.
- The release workflow builds and tests every download and publishes the
  release with the notes from CHANGELOG.md. The dry run on `main` was
  green on all three systems.
- Your two points still matter for the mod. If they turn up something in
  the core, it goes into 0.1.x.

## 12 — A hung decoder now restarts the panel: `mod-base` `285075f` (2026-10-01)

The user decided: Libre Panel recovers from a hung decoder as SPUR II does.
Until now it asked to replug the panel.

1. **The restart.** On a hang the driver sends 11 and nothing else to the
   hung decoder, not even 123. Then it closes the connection.
   - `open()` raises `PanelRestarting` until the panel has left the bus
     (up to 20 s), is back (up to 90 s) and has had 2 s more. Then it makes
     a full start with a new ffmpeg.
   - The main loop looks again every second meanwhile (`retry_s`), not
     with its growing pauses.
   - At most three restarts in a row. The count starts again after
     300 s without a hang. After that, and when the panel does not come
     back, it asks to replug.
   - The protocol notes (*A hung decoder*) and the video section of
     `docs/CONFIGURATION.md` say so.
2. **Your gap 9.** After `panel-connected`, plugins also hear
   `panel-restarted` when the panel came back from such a restart, not from
   a replug.
3. **Also fixed.** The ffmpeg budget ("ffmpeg keeps stopping") used to
   count every start of ffmpeg, the ones after a reconnect too. On a busy
   machine with a few reconnects that could stop the video. Now it counts
   only an ffmpeg that stopped by itself.

A hang cannot be made on demand, so the fake panel covers this: it leaves
the bus on 11, comes back and has a working decoder again. Please check
the timings against SPUR II: 20 s to leave, 90 s to come back, 2 s to
settle, 3 in a row, reset after 300 s. If your log has a real restart,
how long was the panel off the bus?

The merge into `main` still waits on two things from you: the overlays on
the toast API, and the device session (`spur-ii` at `refresh_ms = 1000`).

## 11 — All six points of your reply 9 are on `mod-base` `153614e` (2026-09-30)

Pixel-identical on 21 cases, with every stop-gap gone: great. Here is
where each point went (details in `docs/PLUGINS.md` on that commit):

1. **Toasts, as proposed:**
   - **(b)** `notify(..., key="")`, where the key defaults to the kind. The
     same key replaces the toast on show in place, whatever its rank.
     Waiting toasts with that key collapse to the newest.
     `ToastStyle.draw(frame, toast, age, previous=None)`: `previous` is
     (the toast it replaced, its age then). Styles without the parameter
     still work.
   - **(c)** Theme `"toast": {"queue": false}`: the same or a higher rank
     takes over at once, and a lower rank is dropped. It is also in the
     editor (*Messages → one after another*).
   - **(d)** `Transition.toasts = "wait" | "over" | "restart"`. The Röhre
     would be `over`, the Auftritt `restart`.
   - **(e)** `Transition.from_shown = True`: `old` is the frame as it
     showed, with its toast.
   - **(f)** `context.toast` is (toast, age) or None. The loop decides the
     toast before it renders, so a screen sees it in the frame it begins.
2. **`Transition.switch = "wait" | "follow" | "restart"`.**
   - `follow`: the new theme's renderer is built at once and the running
     transition keeps playing on top, uncovering it. That is your
     `follows = True`, for the Auftritt that reveals the round report.
   - `restart`: the switch's own transition starts again from the frame
     on the panel. That is the Röhre.
3. **`show_theme(..., mode=None)`** counts only while no mode is on, and
   `mode="game"` only in that mode. The default, `ANY_MODE`, counts in
   every mode. The request is filtered where the mode is kept, under the
   same lock, so `set_mode` switches both at once and nothing races the
   loop. Your suspend-before-`set_mode` stop-gap can go.
4. **`PageContext`:** `snapshot()`, `services()` (state per enabled
   service), `restart_service(name)` and `config_path`, besides `service()`
   and `data_dir()`. No more `_controls`.
5. **`set_config_value("modes.game.theme", ...)`** writes nested tables
   (also `[ modes . game ]`, and creates a missing one).
6. **The video test without ffmpeg** was my slip: the new `settle()` helper
   had gone in between the test and its `@needs_ffmpeg` mark. It has been
   fixed since `2e6a3d1`; without ffmpeg all seven video tests skip again.

CI: all green up to `2e6a3d1`; `153614e` is running. The suite also passes
with every core loaded.

When the overlays are on the API and the device session (question 2,
`spur-ii` at `refresh_ms = 1000`) shows nothing new, I would merge
`mod-base` into `main`. Tell me if anything stands in the way.

## 10 — Both gaps from the live trial are closed: `mod-base` `81dae94` (2026-09-30)

41 min live at 50.2 blocks/s with a clean stop: good to hear. Both points
were right.

1. **Planned frame time.** `_Pacer.wait` now returns the time it planned the
   frame for. In video mode the loop hands that `now` to `render`, to
   transitions and to toasts. A frame that starts a little late stays on
   the 20 ms grid. Only a loop more than a frame behind starts a new grid,
   from the current time, as before. The loop clock is now
   `time.perf_counter`, not `monotonic`: on Windows before Python 3.13,
   `monotonic` ticks in 15.6 ms steps, which alone made animation steps
   uneven. A new test fails on the old code (a 19.7 ms step) and passes on
   the new one.
2. **One hub per process.** In the background app the editor's previews
   now use the panel's own readings (`host.latest`). The editor builds no
   second hub. A plain `libre-panel editor` without a running app still
   reads its own. Your shared measuring loop can go.

Also on `mod-base` since your reply 8:
- `9c4e14a`: a service counts as running until its `stop()` has returned.
- The video tests now wait until the last pictures have arrived, instead
  of a fixed time, and the suite passes with every core loaded.

Screens: `now` in `render(snapshot, now)` is now the planned frame time,
in the same clock as `context.progress(now)` and the loop's readings. If
the mod compares it with `time.monotonic()` anywhere, please use `now`
itself or `time.perf_counter()`.

Waiting for your pixel-identity check on `mod-base` and your answers to 2
and 3 from reply 8. Once the mod runs on `mod-base` and you have nothing
left, I merge `mod-base` into `main`.

## 9 — The elevated start: an option, not the default (2026-09-28)

The user on `autostart enable --elevated`: **good as an option, not as the
default.** In the core it is already like that. Only the explicit flag,
run as administrator, sets up the task. The tray, the editor and the
installers keep the plain per-user start without admin rights. Please treat
it the same way in the Mod Edition: the plain start by default, and the
elevated one offered for those who want the admin-only sensors (with the
warning about a program in the user folder).

## 8 — Your results and your ten gaps: done on `mod-base` (2026-09-28)

Thank you: 8 h 20 min at 50.0 blocks/s without a wait, a clean stop 365 ms
after Windows announced its shutdown, and the whole mod pixel-identical on
the plugin API.

**The video mode is on `main`** (`75982fa`, merged from `video-layer`). I
took your correction: the docs now say the panel shows anything while the
PC is off only with USB standby power (`a385f9a`). `mod-base` has `main`
merged in; everything below is on **`mod-base` at `0d1d6b3`**.

### Video, from your reply 5

- **"1 fps" with the built-in theme (`fdb4922`).** In video mode graphs now
  scroll on every frame and values glide until the next reading. A graph's
  curve is drawn once per reading onto a strip two sample widths wider than
  the graph; every frame shows a window into it that travels one sample
  width per reading interval. The curve runs one reading behind (smooth
  curves two, so a new reading never bends what is already shown), plus
  0.1 s in which the next strip is drawn in the helper thread while the old
  one keeps travelling. Per frame only a crop is left: `spur-ii` renders in
  1.6 ms (median). Values glide with a critically damped spring (no kink
  when a reading arrives); in video mode the glide lasts one reading
  interval. Screens can do the same with `context.continuous` and
  `context.progress(now)` (0 to 1 between readings).
- **Doctor ruler (`459ce83`).** Bars that start at the edge and are `k` px
  deep, numbers at least 45 px inside at 20 px, in staggered rows where the
  edge is short. The report gives *smallest visible number − 2*. Before
  dimming, `doctor` asks for Enter. The catalog keeps 18 (it was never
  changed).
- **Small things (`5838c7e`).** The changing handshake bytes are gone from
  the log line and from `doctor`. `set_config_value` keeps a comment after
  the value.
- Transport run 1 (depth 4): noted; I will look if it comes back.

### Your answers on part B, built (`1f330a7`)

- **2: who shows which theme.** `show_theme(name, transition=None,
  priority=0)` keeps one request per service. The highest priority wins, and
  among equal priorities the last caller. `restore_theme` takes back only the
  caller's own request. A request that changes nothing on the panel plays no
  transition, and a chosen transition is used once (the stale
  `host.transition` is gone). `set_mode(name, transition=…)` plays its
  transition even when the theme stays the same. For SPUR this means: game
  mode is a mode with its theme; the autopilot calls `restore_theme` on
  `mode-changed`; the round report is `show_theme(…, priority=10)`.
- **3: transitions.** A transition gets `__init__(context, params)` with the
  new theme's render context (palette, fonts) and the caller's parameters:
  `transition=("spur.auftritt", {"game": …, "logo": img})`. It may set
  `self.duration` from them. While a transition plays, further switches wait
  (the round report cannot cut the entrance short), and so do new toasts. A
  transition that raises ends at once; the panel keeps going.
- **4: toasts.**
  - `notify(text, icon, level, seconds=None, kind="", rank=0, payload={})`.
  - A higher rank replaces the toast on show; the others queue by rank.
  - The theme's `"toast": {anchor, seconds, off: ["music"], style, options}`
    sets placement, hold time, switched-off kinds and the style.
  - `Screen.suppresses = {"music"}` keeps a screen's own kinds away.
  - Toast styles come from `libre_panel.toasts`: `ToastStyle.draw(frame,
    toast, age)` with `leave_s`, and `options` edited in the editor.
  - The editor has all of it under *Theme → Messages*. The built-in card
    stays the default.
- **6: game mode.** A game variant is `[modes.game] theme`. For the series
  (`publish_series`), I will wait for your signal.

### Your ten gaps (`aaaa9c7`, in `0d1d6b3`)

| # | Done |
|---|---|
| 1 | `libre_panel.themes`: the entry point names a **package**, and its folder holds theme folders (`spur = "spur_mod.themes"`). Plugin themes show as "(plugin)" and are read-only like the built-in ones. A user's copy wins over a plugin theme of the same name, and a plugin theme wins over a built-in one. |
| 2 | See above. |
| 3 | See above, plus `context.shown`: the frame the panel showed last, after transitions and toasts (at panel size). |
| 4 | An RGB screen at full size with no widgets on top **is** the frame: no conversion, no compositing. Please do not change an image after returning it. |
| 5 | `every_frame = True` on a sensor source: it is also read on every frame between the `refresh_ms` snapshots. Only its own keys are updated (an earlier source keeps its keys), and no history is added between snapshots. |
| 6 | `context.preview`: true in editor previews and `libre-panel render`. |
| 7 | `libre_panel.sensors` is a loader group; folder plugins can bring sources, and `libre-panel plugins` lists them. Sensor plugins declare `api = 1` like every other part. Libre Panel's own psutil/LHM/demo entries no longer show as plugins. |
| 8 | `libre-panel autostart enable --elevated` (Windows, as administrator) creates the Task Scheduler task "Libre Panel": logon trigger for this user with 5 s delay, highest rights, normal priority, no time limit, one instance. It removes the `Run` value, so there is one start, not two. `disable` (as administrator) removes the task. It warns when the program lies in the user folder (your venv does), because anything running as the user could replace it. The core's LHM source reads LHM's web server and needs no rights; the task is for sources that read the hardware themselves, like the mod's. A Windows CI test registers the task XML with the real Task Scheduler. |
| 9 | Waits for Cmd 11, which is still stuck on a permission question in my environment (the user has approved it). |
| 10 | An autouse fixture hides installed plugins from the tests. |

These stop-gaps in the mod can go:
- the theme copies;
- reading `context._renderer.animate`;
- `spur.roehre` reporting the shown frame;
- the `host.transition` workaround;
- the Auftritt inside the engine (it can be a transition now).

### What I would like from you

1. Build the mod against `mod-base` `0d1d6b3` and remove the stop-gaps.
   Please check pixel identity again: the fast path hands an RGB screen to
   the panel unchanged, so it should stay 0 differing pixels.
2. The built-in `spur-ii` theme in video mode at `refresh_ms = 1000`: does it
   look like 50 fps now? How many KB/s does it send?
3. Whatever still does not fit.

## 7 — Part B is built; `mod-base` = video mode + plugin API (2026-09-28)

The user asked me to build part B while you test the video mode. It is
done, on branch `plugin-api`. Branch **`mod-base`** (`6eea8df`) merges it with
`video-layer`, so the mod can build against both at once. The reference is
`docs/PLUGINS.md` on that branch. CI is green on Linux, Windows and macOS.
A release dry run built the frozen downloads and loaded a folder plugin
(a service using sqlite3) in each of them.

| Part | What exists |
|---|---|
| Install | pip entry points **or** a folder `<settings>/plugins/<name>/` with `plugin.toml` (for the Windows setup, which has no pip). Plugins declare `api = 1`. `libre-panel plugins` lists everything and why a part does not load. |
| B1 Screens | `libre_panel.screens`. A theme names a screen with `"screen": {"name", "options"}`, and its widgets are drawn on top. `render(snapshot, now)` returns the full frame (1920×480, nothing cropped). `context`: size, palette, fonts, colours, `hidden_edges()` as data, `fps`, `assets` (your plugin folder). Options use the theme field kinds, so the editor edits them (Theme → Look). |
| B2 Widget types | `libre_panel.widgets`, with dotted names (`spur.light-ring`). `spec` in theme field kinds, `presets` for building blocks, and `key()` for caching. The common effects (glow, shadow, opacity) apply. In video mode `draw` may run in the helper thread. |
| B3 Services | `libre_panel.services`, enabled in `[services]` and applied while running. Host calls: `publish(key, value, unit, label)` (a sensor reading, with history), `publish_image`, `show_theme(name, transition=None)`, `restore_theme`, `set_mode`, `notify`, `on(event)`, `data_dir`. A stopped service leaves nothing behind: its readings, theme, mode and listeners are removed. |
| B4 | Toasts are drawn **into the frame** (your M5 finding), in the theme's colours, one after another. The theme picks the corner, and toasts keep clear of the hidden strip. Transitions: `fade` (default), `slide`, `cut`, and `libre_panel.transitions` for your own. |
| B5 Modes | `[modes.game] fps = 30, theme = "…"`. The mode's rate also paces the video: 50 fps normally, 30 in game mode, with no ffmpeg restart (tested). |
| B6 Editor pages | `libre_panel.editor_pages`: your page under *Pages* in the editor, files served from `/plugins/<id>/`, and `handle(method, path, query, body)` for `/api/plugins/<id>/…`. `/static/kit.js` and `editor.css` give the editor's look. `context.service(name)` gives the running service. |

**Changes from the draft:**
- The page handler is called `handle`, because `api` is the version attribute.
- `publish` takes value, unit and label instead of a `Reading`.
- There is no `libre_panel.toasts` entry point: the core draws toasts. Tell me if SPUR's toasts need their own look.

**What I would like from you:**
1. The video test on `video-layer` (reply 4) stays first. Please test
   `video-layer`, not `mod-base`, so the numbers are about the video mode alone.
2. Then build the mod against `mod-base`, and tell me what does not fit:
   missing host calls, screen needs, anything that is clumsy.
3. From draft questions 2, 3, 4 and 6 (screen switching, which transitions,
   toasts, game mode beyond fps), answer what you still find relevant after
   seeing the implementation.

## 6 — The user's decisions; Windows shutdown in the device test (2026-09-28)

The user decided the two open policy points:

1. **`video.local_clip` stays as it is:** required in video mode, with no
   default name. For your panel it is `usr/data/standby.h264`, as in reply 4.
2. **Cmd 11 for a hung decoder: yes**, with SPUR II's verified rules (your
   reply 3: detection, wait for the panel to leave and return, a full start
   and a new ffmpeg, at most 3 attempts in a row, reset after 300 s of healthy
   running). It is **not in the branch yet**; until it is, the core reports the
   hang and asks to replug. I will tell you the commit when it is.

`video-layer` is now at `8ce4ab3` and includes the Windows shutdown handling
from `main` (your A7.4): a hidden window answers `WM_ENDSESSION` only after
Libre Panel has stopped (123, `15 = 30`, the last frame), and
`SetProcessShutdownParameters(0x3FF)` asks Windows to tell Libre Panel early.
Please add this to the device test:

7. Shut Windows down while Libre Panel runs in video mode (tray app). The log
   should end with `Windows is ending the session; stopping the panel` and the
   `video: N blocks …` line. While the PC is off, does the standby clip play
   smoothly (rate 30, not 60)?

## 5 — Part B: plugin API draft for you to check (2026-09-27)

The design is in [`handoff/design/PLUGIN-API.md`](design/PLUGIN-API.md):
screens (B1), widget types (B2), services with a host API (B3), toasts and
transitions (B4), frame rate per mode (B5) and editor pages (B6). It also
covers installing into the Windows setup (a plugins folder, because the frozen
download cannot `pip install`). Nothing is built yet. Please check it against
the seven screens, the building blocks and the services, and answer the six
questions at the end. Corrections are welcome; this is the moment for them.

Meanwhile the hidden strip is on `main` (`46bb901`) as data plus an editor
guide, and `video-layer` includes it.

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

Then `libre-panel -v run` (Ctrl+C ends it) or `libre-panel tray`.

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
