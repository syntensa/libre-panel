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
  system's. Where a crate in Rust gives the same bytes, the crate is used (zlib: zlib-rs, a
  port of zlib that stops where Pillow's zlib stops).
- **LibreHardwareMonitor** (C#, a library of its own) is replaced by sensors read in Rust, chip
  family by chip family; until a family is read in Rust, the .NET service reads it, then goes.
- **Python plugins end** (there is no Python to run them). Plugins come back as WebAssembly
  modules with an API of their own (stage R10).
- **`pip install libre-panel` ends**: Libre Panel comes as its downloads (Windows installer and
  zip, Linux AppImage).

## How it stays the same

The Python app stays in the repository, runnable and tested, until the Rust app has replaced
all of it. It is the reference every Rust piece is checked against:

- **Pixels.** Each image operation in Rust (rust/pixels) is checked against Pillow 13.0 (the
  Pillow the Python app is built with now; the comparisons install exactly it, so a Pillow
  that draws differently changes nothing unseen) on thousands of random cases by `lp-check`, a small program that runs operations it reads and writes their
  results; pytest compares them with Pillow's, byte for byte (tests/test_rust_*.py). numpy's
  arithmetic the same way where the renderer uses it (np.round rounds half to even, astype
  truncates: the Rust code does what numpy does). Where the Python app itself does not come to
  the same numbers on every machine (numpy's matrix products go through BLAS, whose sums go in
  the processor's own order), the Rust app comes as near as the Python app's own ways come to
  each other, and the tests say how near (a stated part of a level).
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
| py | what Python does that the app's files and messages depend on: its values (ints of any size, dicts in order, ==), their arithmetic and its errors, repr(), json, str's white space, digits, lower() and upper(), re's matching without case, rounding, the division of ints, float() of a str, sum() of floats, splitlines(), the wide characters of East Asian scripts, the zones zoneinfo finds (Python 3.12, Unicode 15.0: the release's) | — (Python's own rules) |
| pixels | Pillow's operations as Libre Panel uses them, to the bit | Pillow, numpy (in render/) |
| decode | pictures from their files as Pillow decodes them (PNG, GIF, JPEG, WebP) | Image.open, Pillow's decoders |
| text | letters and their layout as Pillow draws them (FreeType, shaping as libraqm) | fonts/, ImageFont |
| i18n | the app's languages: its texts and the fields' names in each, the system's language | i18n.py |
| theme | themes: reading, checking, versions per shape, adapting, the modules and cards laid out | theme/ |
| render | the renderer and every widget, effect, motion and transition | render/ |
| devices | TURZX (from the engine), Waveshare, udisp, BeadaPanel, NZXT, virtual, monitor | devices/ |
| sensors | Linux and Windows sensors, history, demo | sensors/, sensorservice/, the C# service |
| services | game mode, notices, AI activity, downloads, memory, timer, cooling, power, autopilot, weather, media, audio | services/, weather/ |
| server | the app's HTTP server and its API | editor/ (server side) |
| ui | the app's pages in Rust (WebAssembly), the same HTML and CSS | editor/app (JavaScript) |
| app | the program: command line, config, log, single instance, tray, window, autostart, light mode | the top-level modules |
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
      the size), in L, RGB, RGBA, RGBa and F, and resize in I;16 (16 bits, its reduce refused)
- [x] thumbnail (of a decoded picture; a JPEG's draft is the decoder's), ImageOps.fit, pad,
      contain, cover, the EXIF orientation's turn; getextrema, linear and radial gradients
- [x] GaussianBlur, BoxBlur
- [x] quantize (MEDIANCUT) and getcolors, as render/cover.py finds a cover's colour: every
      order Pillow's answer depends on as Pillow has it (its hash tables, merge sort, heap of
      boxes), past 65536 colours too
- [x] ImageChops: multiply, screen, add, subtract, lighter, darker, difference, the modulos,
      offset; blend, composite
- [x] ImageColor: getrgb and getcolor in every form Pillow reads (names, hex, rgb() in numbers
      and percent, hsl(), hsv(), hsb(), rgba()), its regular expressions matched as Python
      matches them (any script's digits, Python's white space, a last line end) and its
      messages quoted as Python quotes them (lp-py)
- [x] transpose, rotate, transform (affine, extent, perspective, quad; nearest, bilinear,
      bicubic)
- [x] drawing: line (thin and wide, joints), polygon, rectangle, rounded_rectangle, ellipse,
      circle, arc, pieslice, chord, point, bitmap; the core (Draw.c) and ImageDraw's own
      methods, on pictures of bytes and of floats (F)
- [x] decoding PNG (every bit depth and colour type, interlaced, its transparency; read
      chunk by chunk as PngImagePlugin reads it, inflated and unfiltered as ZipDecode.c does
      it), the EXIF orientation (EXIF, ImageMagick's raw profile, XMP), opened as the app
      opens pictures (exif_transpose, then convert)
- [x] decoding GIF: the first frame as a still, and every frame as the app reads an
      animation (each laid on the last, every disposal, frames that grow the picture)
- [x] decoding APNG: every frame as the app reads an animation (each disposal and blend, a
      default image, frames in several chunks)
- [x] decoding JPEG: libjpeg-turbo itself, set up as Pillow sets it up (its markers read as
      Pillow reads them, CMYK, a draft's scale and grey, MPO's pictures as frames); loaded
      where the app runs (LP_LIBJPEG: the comparisons use Pillow's own copy, so they run where
      Pillow's wheel has it as a file, Linux and macOS)
- [x] decoding WebP: still and animated, through libwebp's animation decoder as Pillow uses
      it (loaded as libjpeg-turbo is: LP_LIBWEBP, LP_LIBWEBPDEMUX)
- [x] thumbnail on a file just opened, as media.py makes a track's cover small: a JPEG drafted
      first (decoded at 1/2, 1/4 or 1/8, then resized from the box its draft gives), every
      picture resized in its file's own mode (1 and P by NEAREST, LA, CMYK, I;16), a GIF whose
      L is P underneath (its own colour table the grey ramp) resized and converted as Pillow
      does it
- [x] as fast as Pillow or faster where the app reads pictures: libjpeg-turbo's rows straight
      into the picture, resampling a pixel's bands together and summing rows whole (the same
      integer sums), zlib-rs with its SIMD; a cover made small takes 0.82 to 0.99 of the
      instructions Pillow's takes (callgrind: JPEG 600 to 3000 pixels, PNG, WebP)
- [x] the JPEG and WebP comparisons on Windows too: CI builds libjpeg-turbo 3.2.0 and libwebp
      1.6.0 there as Pillow's Windows wheel has them built in (MSVC, the SIMD), from archives
      checked by their SHA-256 (libjpeg's boolean is a byte on Windows, an int elsewhere)
- [ ] to decide: the app reads an animated WebP's durations before each frame is loaded,
      when Pillow has not set them yet, so it plays those at its usual pace (the Rust frames
      read them as the app does for now)
- [x] render/dither.py's numpy: its noise (default_rng's float32s: SeedSequence and PCG64),
      to_u8 and ramp_u8 to the byte, its blur to the bit where it takes its passes, and within
      half a thousandth of a level where it multiplies by their matrices instead
- [ ] numpy's arithmetic in the rest of render/ (lit, facets, fan, moon, sun path, symbols,
      weather art, globes, start screens): widget by widget, with R4

### R2 text
- [x] FreeType glyphs as Pillow renders them (the same FreeType, loaded where the app runs):
      sizes as Pillow asks them (its float times 64), font indexes and collections, TrueType,
      OpenType, Type 1, bitmap fonts in colour (rust/text)
- [x] the basic layout: FreeType's advances and Pillow's kerning (a 64th of the font's), its
      boxes and anchors, getlength, getbbox, getmask2 in L, 1 and RGBA, the stroke (FreeType's
      stroker, filled as ImageDraw has it), colour glyphs unpremultiplied
- [x] Pillow's text drawing on top: ImageText's lines, anchors, alignment (justify too) and
      spacing, textbbox, textlength, the stroke in its own ink, embedded colours
- [x] the layout shaped and reordered as libraqm 0.11 does it, Pillow's on Linux where
      FriBiDi is (Windows' Pillow finds none and lays out as above): FriBiDi's levels and
      raqm's reordering of the runs, its scripts (common and inherited characters, paired
      brackets), HarfBuzz's shaping of each run with the direction, features and language;
      HarfBuzz and FriBiDi stay libraries (Pillow's HarfBuzz, made for its FreeType). The
      language of a text without one is HarfBuzz's default, the locale's: the Rust app sets
      it from the environment when it starts, as Python does
- [x] fonts/fallback: what a font covers, read from its cmap as the app reads it (broken
      tables too), the system's chains (Windows' table, the folders and fontconfig elsewhere,
      the East Asian fonts of the app's language first), the faces that draw what their file
      lacks in runs on its baseline; Pillow's truetype with its search of the font folders.
      Python's Unicode database is the release's (3.12: Unicode 15.0), the crates pinned to it
- [ ] the comparisons on Windows: a FreeType 2.14.3 built as Pillow builds its own (zlib,
      libpng, HarfBuzz and Brotli in it), for lp-check to load

### R3 theme
- [x] Python's json as the app reads and writes its files (lp-py): NaN and Infinity, ints of any
      size, a key given twice, every refusal in Python 3.12's words at its place, dumps' indent,
      separators, ensure_ascii and sort_keys, floats as repr() writes them (of the fewest
      digits, the nearest, an even last digit between two); a half of a surrogate pair, which
      Python keeps in its str (and cannot save), is refused here, as Rust's str cannot hold it
- [x] theme.json read and checked as theme/model.py reads it (rust/theme): every field of
      every widget type completed and checked, the palette, fonts, background, toast, look,
      switches and the detached modules, every refusal in its words and every warning; what
      Python itself raises on the way (an int() of NaN, a font that is no str, an int too
      large for a float) raised of the same kind; assets kept in the theme's folder as
      Path.resolve() keeps them (links, "..", a loop of links), the file read as read_text
      reads it (line ends, UTF-8's refusals); its tables (the widgets' fields, the panel
      models, icons, finishes) the Python app's own, compared
- [x] versions per shape (theme_for: a size's own, vertical, the pairs and companions, the
      agents' screens), the search of the folders (the user's over a plugin's over the app's),
      list_themes, save_theme (a copy takes its theme's files along, written whole or not at
      all), a person's accent, card opacity, the look of the first panel, the switches; light
      colours as theme/light_colours.py works them out in OKLab (the C library's pow, cos,
      sin and atan2 that Python calls, and Python's own hypot)
- [x] a theme adapted to another panel (theme/adapt.py: scaled or kept, its modules moved
      onto the new grid as Python's set orders their sizes) and laid out on a panel it has no
      version for (the version of the nearest shape, its grid made of what its file holds);
      Python's arithmetic on what files hold as it is (lp-py: + - * / // % of bools, floats
      and ints of any size, int / int rounded once, min, max, round, log, every error in its
      words), the versions in the order the folder lists them, matched without case on
      Windows as Python's re matches (its own tables, compared over every code point)
- [x] the panel's default theme (theme/defaults.py): Lumen on a bar and on a round panel it
      has a version for, Libre Default elsewhere (the panel a config is for, and the theme of
      its normal page, come with the config in R9; community themes with the editor's import
      and export in R9; the cards and modules' layout in R4)
- [ ] paths whose names are no Unicode (Linux: bytes that are no UTF-8, kept by Python as
      surrogates; Windows: a half of a surrogate pair): read as Python reads them, all through
      rust/theme (today a theme in such a folder finds no versions)

### R4 render
- [x] modules on their grid as theme/modules.py and theme/cards.py lay them out (expand),
      every kind of both (ring, stat, huge, graph, bars, network, netinfo, system, clock,
      date, analog, world, countdown, weather, outlook, sun, moon, earth, text, temps, cores,
      drives, processes, values, ping, timer, power, ups, mic, battery, ai, image, music,
      volume, agenda, game; the cards playback, forecast, download, instrument and netcard,
      and the slot of cards with the cards its lines name): their parts as widgets, field for
      field in Python's order and of its types, with every look, size and option, in every
      language, and Python's error where it has one (a card no higher than its padding); the
      app's texts in each language and the system's language as i18n.py finds them
      (lp-i18n); Python's sum() of floats, splitlines(), East Asian widths and the zones
      zoneinfo finds (lp-py)
- [x] what the editor asks of the modules: module_info (the kinds, their names in the
      language, sources and sizes, the looks), the starting layouts of a grid (packed where
      there is none, the sizes in the order Python's set has them), a module detached into
      its parts, the person's cards in each slot open to them (with_cards, follows_cards)
      and the cards a slot's lines name with their turns (card_time, time_text); Python's
      float() of a str (lp-py)
- [ ] the zones on Windows (Python finds them in the tzdata package there, which the Rust
      app does not bring yet: none is found there for now)
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
- [ ] config.toml and its folders; the panel a config is for (theme/defaults.py's
      panel_model: the configured model, the one a display reports, a TURZX panel on the USB
      bus) and the theme of its normal page
- [ ] community themes as one file (theme/share.py): the zip read as Python's zipfile reads it
      (every entry, name, method and damage) and written as it writes it, every check of an
      import in its words, the files a theme names, what is scrubbed from it

### R10 plugins
- [ ] WebAssembly plugins: services, sensors and pages, an API of their own

### R11 packaging
- [ ] Windows: the installer and the zip, signing; Linux: the AppImage; the tools (ffmpeg)

### R12 the switch
- [ ] fixtures recorded from the Python app; the Rust app on main; the Python app goes
