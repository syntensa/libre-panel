# Libre Panel in Rust

The whole of Libre Panel becomes Rust: the panel's drawing, the panels' loop and devices, the
sensors, the services, the app's server, its window, its tray icon and its pages. No release
waits for a date: the next one comes when the Rust app does all the Python app does, and looks
the same, pixel for pixel.

This file is the plan and its state. It is kept up to date with every step (the checklists
below), so that the work can be picked up where it stands.

## What it is made of today

| Part | Language | Lines |
|---|---|---|
| The app (render, devices, sensors, services, editor, theme, plugins, …) | Python | 92,000 |
| Its tests | Python (pytest) | 86,000 |
| The app's pages | JavaScript, CSS | 22,000, 9,700 |
| The sensor service on Windows (hosts LibreHardwareMonitor) | C# | 2,600 |
| The engine (compositing, YUV, H.264, USB) | Rust | 11,400 |

## What "all of it in Rust" means

- **Every line Libre Panel itself is written in is Rust**: the app, its tests, its tools, and
  its pages (Rust compiled to WebAssembly, in the system's web view as now).
- **The look stays.** The pages keep their HTML structure and their CSS (styles, not a program):
  what they show and how they show it does not change. The panel's pictures are the same to the
  bit (below).
- **The files stay.** config.toml, theme.json (every version), the locale files, the history
  and the logs keep their formats: a Rust Libre Panel takes over a Python one's settings and
  themes as they are.
- **Libraries in C that Pillow itself uses stay libraries**: FreeType draws the letters
  (only it draws them to the bit as Pillow does), libjpeg-turbo decodes JPEGs as Pillow does,
  x264 encodes as now; ffmpeg stays a program of its own; WebView2 and WebKitGTK are the
  system's. Where a crate in Rust gives the same bytes (PNG, zlib), the crate is used.
- **LibreHardwareMonitor** (C#, a library of its own) is replaced by sensors read in Rust, chip
  family by chip family; until a family is read in Rust, the .NET service reads it, then goes.
- **Python plugins end** (there is no Python to run them). Plugins come back as WebAssembly
  modules with an API of their own (stage R10).
- **`pip install libre-panel` ends**: Libre Panel comes as its downloads (Windows installer and
  zip, Linux AppImage).

## How it stays the same

The Python app stays in the repository, runnable and tested, until the Rust app has replaced
all of it. It is the reference every Rust piece is checked against:

- **Pixels.** Each image operation in Rust (rust/pixels) is checked against Pillow on thousands
  of random cases by `lp-check`, a small program that runs operations it reads and writes their
  results; pytest compares them with Pillow's, byte for byte (tests/test_rust_*.py). numpy's
  arithmetic the same way where the renderer uses it (np.round rounds half to even, astype
  truncates: the Rust code does what numpy does).
- **Frames.** Every built-in theme at every panel size with recorded readings and times: the
  Rust renderer's frames against the Python renderer's, to the bit, frame by frame, animations
  and transitions included.
- **Behaviour.** The server's answers (the same routes, the same JSON), the files written, the
  log's lines: recorded from the Python app, replayed against the Rust one.
- Before the Python app goes, its answers are recorded once more as fixtures, and the Rust
  tests keep checking against those.

## The crates (rust/, a Cargo workspace)

| Crate | What it is | Python it replaces |
|---|---|---|
| pixels | Pillow's operations as Libre Panel uses them, to the bit | Pillow, numpy (in render/) |
| text | letters and their layout as Pillow draws them (FreeType, shaping as libraqm) | fonts/, ImageFont |
| theme | themes: reading, checking, versions per shape, adapting | theme/ |
| render | the renderer and every widget, effect, motion and transition | render/ |
| devices | TURZX (from the engine), Waveshare, udisp, BeadaPanel, NZXT, virtual, monitor | devices/ |
| sensors | Linux and Windows sensors, history, demo | sensors/, sensorservice/, the C# service |
| services | game mode, notices, AI activity, downloads, memory, timer, cooling, power, autopilot, weather, media, audio | services/, weather/ |
| server | the app's HTTP server and its API | editor/ (server side) |
| ui | the app's pages in Rust (WebAssembly), the same HTML and CSS | editor/app (JavaScript) |
| app | the program: command line, config, languages, log, single instance, tray, window, autostart, light mode | the top-level modules |
| check | `lp-check`, the comparison program for the tests | — |

The engine (engine/) moves into the workspace as the panels' loop takes it over: one process
for all panels, no pipe between Python and the engine.

## Stages

Each stage ends with its pieces checked against the Python app as above, pushed to
`feature/rust`.

### R0 The workspace and the check
- [x] rust/ workspace, `lp-check`, tests/test_rust_pixels.py comparing with Pillow
- [x] a CI job: build rust/, run its tests and the comparisons (ci.yml's `rust`)

### R1 pixels: Pillow's operations
- [x] images in L, RGB, RGBA (and RGBa, premultiplied, as Pillow converts), new, crop, paste
      (with a mask), alpha_composite, convert, point, putalpha, split/merge/getchannel, getbbox
- [x] reduce, resize (every filter, a box of floats, reducing_gap, the crop where the box is
      the size), in L, RGB, RGBA, RGBa and F
- [ ] thumbnail, ImageOps.fit/pad/contain
- [x] GaussianBlur, BoxBlur
- [x] ImageChops: multiply, screen, add, subtract, lighter, darker, difference, the modulos,
      offset; blend, composite
- [ ] transpose, rotate, transform
- [x] drawing: line (thin and wide, joints), polygon, rectangle, rounded_rectangle, ellipse,
      circle, arc, pieslice, chord, point, bitmap; the core (Draw.c) and ImageDraw's own
      methods, on pictures of bytes and of floats (F)
- [ ] decoding: PNG, JPEG (as libjpeg-turbo), GIF (frames), WebP; EXIF orientation
- [ ] numpy's arithmetic where the renderer uses it (dither, gradients, light)

### R2 text
- [ ] FreeType glyphs as Pillow renders them (the same FreeType), sizes, font indexes
- [ ] layout: basic, and shaped and reordered as libraqm does (HarfBuzz's shaping, bidi)
- [ ] fonts/fallback: the fallback chains, emoji and symbols
- [ ] Pillow's text drawing: anchors, stroke, textbbox, textlength, multiline

### R3 theme
- [ ] theme.json in every version, checks and messages, versions per shape, adapting
      (theme.adapt.laid_out), the built-in themes and their files

### R4 render
- [ ] the renderer's frame, widget by widget (texts, clocks, gauges, rings, bars, lists, graphs
      and their strips, fans, images, videos, icons, weather, globes, moon, sun path, slots,
      cards, notices), effects (glow, lit finish, dither), motion, transitions, toasts, start
      screens; each theme's frames to the bit

### R5 the panels' loop
- [ ] the main loop (frames driven by change, pacing), the engine in the same process, panels
      beside each other, game mode and the alarm's frames, videos (ffmpeg)

### R6 devices
- [ ] TURZX (the engine's), Waveshare, udisp, BeadaPanel, NZXT Kraken, virtual, the panel as a
      monitor (Windows' virtual display driver, X11, Wayland)

### R7 sensors
- [ ] Linux: hwmon, /proc, the cards' (amdgpu, NVML), storage, network, fans
- [ ] Windows: processor (with PawnIO for its registers), graphics cards (NVAPI/NVML, ADLX,
      Intel), memory, storage (SMART), network, mainboard chips and fans; HWiNFO, RTSS,
      PresentMon, MangoHud
- [ ] the history (rings of numbers), demo values; the .NET service goes

### R8 services
- [ ] game mode, notices, AI activity (Claude Code, Codex), downloads, memory and its store,
      timer, cooling, power, autopilot, devices, weather, media (Windows' media session, MPRIS),
      audio

### R9 the app
- [ ] the server and every route of its API, the same JSON
- [ ] the pages in Rust (WebAssembly), view by view, with the same HTML and CSS; the browser
      tests (Playwright) against the Rust app
- [ ] the window (wry, tao), the tray icon, the command line, autostart, single instance,
      updates, languages, the log, light mode

### R10 plugins
- [ ] WebAssembly plugins: services, sensors and pages, an API of their own

### R11 packaging
- [ ] Windows: the installer and the zip, signing; Linux: the AppImage; the tools (ffmpeg)

### R12 the switch
- [ ] fixtures recorded from the Python app; the Rust app on main; the Python app goes
