# Roadmap

Milestones are ordered by what makes Libre Panel useful to the most people
first. Each hardware step needs a tester with the panel.

## 0.8 — one design for every panel, and lighter

Libre Panel's own themes come in a hand-made copy per size: Lumen alone about twenty times,
Arena, Encore and Almanac eight or nine times each. The four of them are laid out for five of
the 28 sizes of the panels it drives (the 9.2", the 12.3", the 5.2", the 2.1" and bars on
end); the 4.6", the 7", the 8", NZXT's round screens and most of BeadaPanel's, Thermalright's
and JONSBO's sizes get High Contrast or a theme stretched to fit. 0.8 makes each theme one
design that is laid out for every panel, and makes the app ask less of the PC while it does.

**The bar: nothing gets worse.** The layouts made by hand today are what every new one is
measured against. A size whose new layout is not at least as good keeps its hand-made one
(as an override of the family), and every change to a theme or a screen comes with pictures
of it before and after, at every size it is made for. A theme someone chose keeps working
under its id, and themes in the 0.7 format, community themes among them, are read as they are.

### 1. Pictures without bands

- [x] Gradients (the themes' backgrounds, rects and cards) and glows that shine brighter than
      their shape worked out in floating point, and dithered in the panel's video (as the
      start screens are since 0.7.9): black stays black, and the same pattern in every frame,
      so that video frames differ only where something moves
- [x] Before and after for every built-in theme; what it costs measured (frame time, the size
      of PNG and JPEG frames and of the video stream: still frames keep whole levels)
- [x] The light of `lit` rings and bars (its halo is made 1.45 times stronger) worked out in
      floating point, faster than it was in whole levels (its blur a product of matrices);
      every glow and shadow of a text too, with the dither in the panel's video

### 2. Lighter

- [ ] Frames that would show nothing new are not made again or sent (a frame in which nothing
      changed is the one before since 0.8: not copied, nor compared on the still-frame path).
      A video panel rests while nothing on it moves (`[video] rest`, on: Almanac 8 to 14 %
      of a core instead of 72), to be tried on the panels; graphs can step at each reading
      instead of scrolling (`graph_steps`); a graph's strip grows instead of being drawn anew
- [x] What does not move (backgrounds, frames, labels) composed once, not in every frame: a
      region composed anew starts from what lies under its first changed part, kept from the
      frame before while the parts under it stay the same
- [x] The video panel's frames made anew only where they changed: the YUV that ffmpeg encodes
      is converted, block by block, where the frame differs from the one before (12.3": Lumen
      1.0 instead of 1.2 cores, ffmpeg 30 instead of 55 % and 70 instead of 102 MB); a lit
      ring's arc worked out instead of drawn (a new value 1.1 instead of 2.3 ms; Arena 108
      instead of 112 % of a core)
- [x] Measured where a frame's time goes (12.3", video): composing the parts that changed
      (3 ms: the graphs' pieces, pasted through their alpha, the most), the frame for the
      panel (1.4), the rolling seconds' glow, the numbers; frames composed in RGB, the glow's
      blur as matrices: 6 to 16 % less of the processor
- [x] The panel's loop runs lean while the app's window is closed (discussion #4: about
      480 MB on Windows): the caches keep what the panel draws with and let go of the rest,
      light mode is on unless switched off, two heaps on Linux; on Linux with the 12.3" 175 to
      190 MB instead of 330 (to be measured on Windows)
- [ ] Targets, measured on the 12.3" in video mode with every core busy (as in a game): at
      most 8 ms a frame (p95; Arena takes 12 to 18 ms today), and well under 200 MB of
      memory on Windows with the window closed

### 3. Themes that fit every panel

- [x] A theme format with layouts per shape (long bar, very long bar, 16:9, 3:2, square,
      round, on end), from which every size is laid out; a hand-made layout for a size stays
      possible and wins: versions, folders named with the size each is made for; a panel
      without a version of its own shows the version made for the nearest shape, laid out on
      its size; one's own themes too, as soon as they have versions for more than one size
      (Every panel size → *Make a version for this size*)
- [x] One design system for the built-in themes: colours, type sizes, spacing, cards
      (`design.py`: the looks, which one each family wears and what it has of its own, the
      modules' type and room from one measure each; `tools/design.py` writes it into the
      themes, a test checks them); cards that let the background through a little, as they
      do since 0.8 (`card_opacity`, measured per family: the dot grid shows through, the text
      loses at most 3 % of its contrast)
- [x] Lumen first, at all 28 sizes, measured against its hand-made layouts; then Arena,
      Encore, Almanac, Nimbus, Studio and the others: each family laid out for every size but
      the ZC-360's long strip (Lumen has one), looked at size by size; new versions where it
      was scaled or did not read (the 0.96", Nimbus, Studio and Terra on the 12.3" and the
      5.2", Studio round), and a round version no longer pulled onto a 4:3 panel
- [x] A light variant of each family (for white builds): `light_colours`, every theme's
      colours worked out for a light ground by their job (OKLab), measured against the Paper
      look and for contrast over every built-in theme
- [x] The screens in the same design: Arena's game mode, the round report (in Arena's
      colours and cards now, not the app's), alarms, cards, the start screens (the start
      screens, the app's icon and the editor's building blocks from the design's Libre look)
- [x] In the editor: a theme at every size side by side, one's own too (the theme's menu →
      *Every panel size*: each panel's picture, and whether it is made for it, laid out from
      the version for the nearest shape, or scaled into it)

### 4. Finding the sensors

- [x] Every sensor by device, with live values, a search and "put it on the panel"
      (discussion #38: a WireView Pro II's readings were there under names no one would find):
      a source's raw readings under the device they are read from, the library's search finds
      them, a click puts one on the panel
- [x] HWiNFO as a sensor source (its shared memory), its readings under the keys
      LibreHardwareMonitor's have

### For everyone (needs the maintainer)

- [ ] Signed Windows downloads (SignPath, free for open source)
- [ ] winget; PyPI (`release.yml` is ready for trusted publishing); Flatpak

## Next: a native engine, smooth and light

0.8 made the frames lighter piece by piece, and each fix found one more thing that cost too
much in some frame. What makes a panel stutter, glitch and ask much of the PC lies under them:

- the frames are drawn in Python on the processor, every one, and each panel beside the first
  runs in a process of its own (the interpreter's lock): Lumen on the 12.3" at 25 frames a
  second takes 86 % of a core in 0.8.0rc1, a 12.3" and a 9.2" about 500 MB on Windows;
- the video goes through an ffmpeg program fed by a pipe, which is started anew for a change of
  the bit rate or of who converts the frames: the glitches, and 0.8.0rc1's worst bug;
- a frame is sent the moment it is done, so a slow one shows at once;
- many settings meet many paths, in combinations no test covers.

So Libre Panel is split into a brain and hands. The brain stays Python: the sensors, the
services (game mode, weather, the AI agents, notices), the settings and the app; none of it is
pressed for time. The hands are a small native engine, in Rust, one process for all panels: it
draws, encodes and sends the frames, on time. Themes stay as they are (theme.json, community
themes too).

**The bar: nothing gets worse.** Each stage ships behind a switch, beside the way of today,
and becomes the default only once it has proven itself on the panels; the old way goes last.

### Half the memory

A 12.3" and a 9.2" with Lumen and every service, the window closed: about 500 MB on Windows
in 0.8.0. The goal is half of that.

- [x] Panels beside the first in the app's process where the engine sends, no console hosts,
      what the engine makes kept there alone (the fans, the texts' letters, the lit rings and
      bars, the graphs' windows, the globe, the background), the readings' history as plain
      numbers, a hash of each block instead of a second copy of the frame, the heap tidied
      while the app is closed and the engine in small pages (Linux), a sensor service without
      Python's web client: on Linux (both panels with Lumen, every service, the window closed,
      each process's anonymous memory) 161 MB instead of 270
- [ ] Measured on Windows: *Live → Diagnose → Load on this PC* says what the main process holds
      and what the sensor service holds by its own word
- [x] The sensor service without Python: a small .NET program that hosts LibreHardwareMonitor
      and answers on the same pipe (`sensorhost/`, tested on Windows CI against the Python
      service: the same answers, sensors, rights and limits, as a Windows service too; on CI's
      runners 9.1 to 10.4 MB of private memory instead of the Python service's 32.5 to 32.9,
      run from its source, and 0.42 % of a core instead of 0.73), in the Windows downloads
- [ ] The graphics card's encoder where it is as good as x264 (x264's buffers: some 22 MB for
      the 12.3" on Linux)

### Stage 0: measure

- [x] Where a video panel's time goes (the 12.3" at 25 frames a second, Lumen): drawing 10 ms
      a frame in the main thread and 7.5 in helper threads, the YUV 3, ffmpeg 12 (x264 7.5,
      the rest the program itself: reading two megabytes a frame from the pipe, copying,
      packing); 83 % of a core in all. A twentieth of the macroblocks change in a frame
      (Lumen 4.6 %, Arena 5.8 %, Terra 4.8 %), so x264's cost is mostly work it does on
      every frame whatever changed
- [x] x264 told which of its 16x16 blocks stayed the same (`X264_MBINFO_CONSTANT`, which the
      ffmpeg program cannot be told through its pipe), without the deblocking filter and the
      search for motion finer than a pixel: Lumen 3.9 instead of 7.5 ms a frame, Arena 3.4
      instead of 6.3, at the same bit rate and as sharp or sharper (PSNR, the edges, the
      dither in the gradients measured from the decoded stream). The filter and the
      sub-pixel search are off for drawn frames through ffmpeg already (a fifth less);
      adaptive quantization stays (without it the dither goes, and the bands come back).
      OpenH264's mode for screens is no better: as costly, and far blurrier at the rate

### Stage 1: the native output

- [x] The engine takes what changed in each frame, makes the YUV there (byte for byte as
      before) and encodes it with libx264 told which blocks stayed the same; the bit rate
      changes in the running stream (`x264_encoder_reconfig`): no ffmpeg started anew, no
      handover (on the 12.3" at 50 frames a second the encoder 24 % of a core instead of 35,
      33 MB instead of 82). The default since 0.8.0: `[video] encoder = "auto"` takes the
      engine the download carries, and leaves it out after it ended unasked three times in
      ten minutes; `"native"` takes it always
- [ ] The same for the cards' encoders: the engine hands the frame to the card's own encoder
      (Media Foundation's: AMD's, NVIDIA's, Intel's, on Windows), says the colours in every
      SPS and the SPS and PPS before every keyframe, x264 where it cannot start
      (`[video] engine_encoder = "card"`); to be measured on the panels
- [x] Pictures sent on a steady beat: the engine has the panel and sends each picture a fixed
      lead (two frames) after the time its frame was drawn for, from a thread of its own above
      the others in priority (`[video] engine_sends`). Frames that came with up to 15 ms of
      jitter went to the panel 20 ms apart within half a millisecond, against six without
- [x] A frame drawn too late costs no beat: the main loop catches up within the lead, each
      frame drawn for its own time (`Display.slack`), instead of taking a new beat
- [x] On Windows the engine's sender under the multimedia scheduler (MMCSS, "Playback"), as
      video players' threads are; and the 1 ms timer kept where Windows 11 drops it (no window
      of Libre Panel's seen), the last 17 ms before a picture slept on a high-resolution timer:
      a log had 1808 of 3080 pictures late, woken on the 15.6 ms tick. The log says how many
      went late, how many of those were made late, and the latest
- [ ] Frames drawn a few ahead
- [x] The TURZX panels' USB protocol as the Python driver speaks it, in the engine (nusb): the
      same packets, the same flow control and hang check, nothing new sent to a panel (the
      tests compare them packet for packet); where the engine cannot open the panel, Python
      sends as before
- [x] Built in CI for Windows and Linux and in the downloads, the ffmpeg path as the fallback

### Stage 2: the native renderer

- [x] The engine composes the frames from the layers Libre Panel draws, each sent to it once
      (render/scene.py, engine/src/scene.rs): pixel for pixel the renderer's, what lies under
      the layers that change kept block by block; the main loop half as busy on the 12.3"; a
      message in the built-in card or a look of the volume one more layer over the frame
      (SceneFrame.over), not a frame composed here (a frame of the volume 6 ms instead of 17)
- [ ] Layers instead of drawing anew: each element drawn once, and again only when what it
      shows changes; what moves (rolling digits, hands, graphs, transitions) moves, turns and
      fades the layers it has, and a glow goes with its layer
- [ ] Element by element, each against today's renderer as the reference: the same picture, to
      a level, with the same font engine (FreeType). First the lit gauge's ring
      (engine/src/ring.rs): of 50 values and colours, 49 the same pixels, one a level apart at
      two; since, the graphs' windows (cut from their strips, laid straight from them, a
      strip grown from the one before), Terra's turning globe, a card's inside of one colour
      (laid without pixels, its edges as theirs), a fan laid from its ring and its rotor (each
      position sent once, laid as Pillow lays it, to the bit; since, made there of its
      rotor's layers, sent once: its positions, blur and ring as fan.py makes them, to the
      bit), a text of letters laid from its letters (each sent once), a text and a letter
      filled there from their masks (a byte a pixel, Pillow's fill to the bit), a list's
      gliding bars cut from the bar empty, full and the part round its tip, a lit bar at each
      value (its pill's light to a level, its glow as Pillow works it out, to the bit), and a
      theme's grid; a text's glow next

### Stage 3: Python only the brain

- [ ] The engine draws every panel; the Python renderer and the ffmpeg path go
- [ ] Fewer settings: what `[video]` sets today the engine decides by measuring

### Against bugs in the app

- [ ] What the panel shows and what Design opens decided in one place (the server) and shown by
      the app (0.8.0rc1's 3.5" in Design was the same question answered apart in JavaScript
      and in Python)
- [ ] Real configurations, without their secrets, as tests; hours of the app used at random
      against panels in memory, looking for hangs
- [ ] A PC with a panel that runs the tests every night (needs the maintainer)

**Targets**, for stage 1's first version to show before the rest is built: Lumen on the 12.3"
at 25 frames a second under 10 % of a core, about half the memory, no frame late in normal
use, no glitch when the bit rate changes.

## Done

- **0.7** — up to four panels at once, a companion screen beside the first; Claude Code and
  Codex on the panel; High Contrast; drivers for JONSBO, Lian Li, Hongtai, VMAX, BeadaPanel,
  Thermalright, Thermaltake, NZXT, Waveshare and udisp panels; ten languages; community
  themes in the app; the panel as a monitor; the start screen in seven looks
- **0.6** — bars on end; Arena, the game mode's screen; an app that leads with the panel
- **0.5** — video mode up to 50 fps; Lumen; an app of its own that designs the panel; SPUR II's
  messages, transitions, game mode and autopilot (see [SPUR2_MIGRATION.md](SPUR2_MIGRATION.md))
- **0.4** — 25 modules: every temperature, core and drive, clocks for any time zone, the sun
  and the moon, what is playing, the calendar, Home Assistant, MQTT, the game's frame rate
- **0.3** — modules on a grid, six looks, an editor where a theme takes two minutes
- **0.2** — Studio; themes that fit each PC; the serial panels (Turing rev. A to D, WeAct)
- **0.1** — theme format, renderer, visual editor, panel catalogue, sensors, the TURZX V1.x
  USB driver, `doctor`, plugins, tray app, Windows setup and AppImage

## Still open

- [ ] `doctor` passed on each serial family and on more V1.x sizes (issue #1: panel reports
      wanted)
