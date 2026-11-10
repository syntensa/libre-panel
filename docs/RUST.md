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
| py | what Python does that the app's files and messages depend on: its values (ints of any size, dicts in order, ==), their arithmetic and its errors, repr(), json, str's white space, digits, lower() and upper(), re's matching without case, rounding, the division of ints, float() of a str, sum() of floats, splitlines(), casefold(), format() and str.format, the wide characters of East Asian scripts, the zones zoneinfo finds and reads, the computer's own as glibc finds it, what datetime asks of them, TOML as tomllib reads it and a file's text as open() reads it (Python 3.12, Unicode 15.0: the release's) | — (Python's own rules) |
| pixels | Pillow's operations as Libre Panel uses them, to the bit | Pillow, numpy (in render/) |
| decode | pictures from their files as Pillow decodes them (PNG, GIF, JPEG, WebP) | Image.open, Pillow's decoders |
| text | letters and their layout as Pillow draws them (FreeType, shaping as libraqm) | fonts/, ImageFont |
| i18n | the app's languages: its texts and the fields' names in each, the system's language | i18n.py |
| config | config.toml as the app reads it: every setting, its defaults and its errors | config.py, sequence.py, hotkeys.py |
| theme | themes: reading, checking, versions per shape, adapting, the modules and cards laid out | theme/ |
| render | the renderer and every widget, effect, motion and transition | render/ |
| devices | TURZX (from the engine), Hongtai and the MAJESTY, Waveshare, udisp, BeadaPanel, NZXT, virtual, monitor | devices/ |
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
- [x] saving JPEG, for the panels that take JPEGs: libjpeg-turbo set up as JpegEncode.c sets
      it up (its tables for the quality forced to baseline, none for Pillow's default, the
      chroma halved both ways, no smoothing or optimising); L and RGB, every quality, the bytes
      Pillow's
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
- [ ] numpy's arithmetic in the rest of render/ (lit, facets, fan, sun path, symbols,
      weather art, globes): widget by widget, with R4

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
      zoneinfo finds (lp-py): in its search path (PYTHONTZPATH's, else the one it is built
      with, none on Windows), else in the tzdata package, the folders of a key packages as
      the import system finds them, a key checked by the system's own paths' rules
- [x] what the editor asks of the modules: module_info (the kinds, their names in the
      language, sources and sizes, the looks), the starting layouts of a grid (packed where
      there is none, the sizes in the order Python's set has them), a module detached into
      its parts, the person's cards in each slot open to them (with_cards, follows_cards)
      and the cards a slot's lines name with their turns (card_time, time_text); Python's
      float() of a str (lp-py)
- [x] the texts readings become (render/formatting.py, lp-render): str.format locked down to
      value, unit and label, the format string read piece by piece as Python's
      formatter_parser reads it (a field's error before what is wrong further on; its
      conversions, specs nested two deep, the numbering of fields); Python's format() of ints,
      floats, strs, bools and the rest in every spec (fill, alignment, sign, z, #, 0 and the
      width, grouping with its zeros, the precision, every type, 'n' in the C locale, the
      digits of floats as dtoa makes them) in lp-py; the app's own specs (bytes, duration,
      clock, gb, rate, rate_unit, upper), a reading's usual text (auto_format), and what Python
      refuses or raises, of the same kind. {value:c} of a half of a surrogate pair, which
      Python makes, is refused here, as Rust's str cannot hold it (the widget shows its
      fallback)
- [x] the frame's ground and its cards (lp-render's Renderer): the background's colour, its
      picture fitted, its gradient in numpy's float32 (bit for bit) made 8 bits with the
      dither where the panel's video has it; the pieces laid as _compose lays them (pasted
      through their alpha on an opaque frame in RGB, composited on one that is not, a frosted
      backdrop blurring what lies beneath its box, light added); rects as cards with their
      corners alone drawn three times larger and as shapes drawn three times larger and
      reduced (colours, gradients, outlines, the surface as see-through as the style asks,
      the cover's colour as render/cover.py finds it); a widget's opacity, the conditions it
      is drawn on (needs: readings, pictures, comparisons with numbers and words in any case,
      the theme's switches), hide_if_missing, and Python's refusals, where a widget is left
      out. Kept as Python keeps it: a card made once. The frames of random themes of cards
      the same to the bit; with a glow or a shadow a pixel now and then a level apart, as
      their soft light is blurred in floating point: numpy multiplies by the blur's matrices
      in float32 (BLAS), Rust takes the passes, and Python's own answer differs as much from
      one processor to another
- [x] the theme's texts (text widgets): the font the widget names or the theme's (a built-in
      one or a file of the theme's; the default where it cannot be read, said once), its size
      in Python's range, the text in the panel's language and in capitals where it asks
      (Python's upper()); made to fit its max_width as _fit does (a smaller size, 6 at the
      least, or cut short with an ellipsis), the widths summed as Python's sum() sums floats;
      laid out whole where it can be (anchored la, ma or ra) and letter by letter where it has
      letter spacing or equal-width digits: each letter on a picture of its own, with room for
      what reaches beyond it (an accent on its own), put on a clear layer as it is where its
      ink meets none put before, laid over it where it does; the fallback fonts for the
      letters a font does not have. The frames of random themes of texts the same to the bit,
      texts exactly as wide as their max_width among them. A frame with a screen, the panel's
      look or a finish other than flat is not drawn here yet: Rust says so, rather than draw
      one without them
- [x] readings shown as texts (metric widgets) in a still frame: the reading's value in the
      widget's format, the fallback where there is none and where the format refuses a number,
      the text itself where it refuses a text, Python's error where it raises one (the widget
      left out); the sensor texts in the panel's language; the colour of the highest threshold
      the value is above (sorted as sorted() sorts them); the volume's "MUTE" in the palette's
      crit while it is muted. The frames of random themes of them the same to the bit. A number
      gliding to a new reading and a colour fading to a threshold's are motion (below); where a
      threshold is NaN, Rust says it does not draw it yet
- [x] icons (icons.py, icon widgets): every one of the line icons drawn as icons.py draws it
      (four times larger on the grid of 24, lines with curved joints and round caps, rings,
      arcs, the outlines of shapes eroded by a blur, the gap a shape clears, then reduced with
      Lanczos), in every size from 8 to 512 and every stroke; the weather's icon for a WMO code
      (Python's int() of it) by day or night as the reading or the clock says, the network
      connection's for its kind and signal. Each icon to the bit, and the frames of random
      themes of them; left out where what it follows has no value (hide_if_missing: a weather
      icon's code, a weather widget's field), as render/renderer.py leaves them out
- [x] the weather's readings and countdowns as texts (weather and countdown widgets): the
      weather's field in the widget's format, as a metric's reading (its description in the
      panel's language); counting down to a day or a moment as render/countdown.py counts:
      the target as its regular expression reads it (any decimal digit, Python's white
      space), the 28th for a day the month lacks, the year 0 as this one, every year's next
      one, to the microsecond; its number, its unit, the time left in the day, the whole in
      words or the days, the date it counts to (i18n.format_date), in the panel's language.
      Every part's text to the letter, and the frames of random themes of them the same to the
      bit, in English and German
- [x] clocks (clock widgets) in a still frame, and dates as i18n.format_date writes them:
      strftime's codes one at a time, with the days' and the months' names of the language, a
      day's number before a month's name joined as the language joins them ("7. November",
      "7 de noviembre", "7 listopada", "11月7日"); each code as Python's datetime.strftime writes
      it in the C locale (lp_py::strftime): %f, %z and %Z its own, the rest the C library's,
      glibc's on Linux (the flags -, _, 0, ^ and #, a code it does not know as it stands) and
      the Universal CRT's on Windows (# alone; for anything else Python's ValueError, which a
      clock shows as "--:--"); a hundred characters at most, in capitals where the clock asks.
      Every code on dates from the year 1 to 9999, random formats in all ten languages, and the
      frames of random themes of clocks the same to the bit, in English and German; a clock
      whose digits roll or fade laid out letter by letter in a still frame too, as Python lays
      it out (another place's time: with the earth widget, below; digits that roll or fade as
      they move: with motion, below). Not here yet, and Rust says so: the years before 1000
      (Python's own answer for them differs between its releases), %s (the seconds since 1970
      in the computer's zone) and, on Windows, %#z and %#Z
- [x] bars (bar widgets) in a still frame: where the reading stands between min and max on
      a linear, square-root or logarithmic scale, in steps of the supersampled grid; drawn
      three times larger and reduced: the track, the filled part in one colour, a gradient
      across the whole track (the other way round for left and up) or the colour of the highest
      threshold the reading is above; segments, the last lit one partly, rising as levels on a
      mixer; a thin mark where a second reading stands, a dot at the bar's head placed in
      quarter pixels. The frames of random themes of bars the same to the bit; with a glow
      round the bar and its dot within a level, as a card's. A bar gliding to a new reading and
      a colour fading to a threshold's are motion (below)
- [x] ring gauges (gauge widgets) in a still frame: where the reading stands, in steps of the
      supersampled grid along the arc; drawn three times larger and reduced: the track with
      round caps or flat and the ticks across it; the value's arc a mask over the gauge's
      colour, a gradient along the scale (thin wedges round the centre) or a threshold's
      colour. A gauge Python cannot draw (no size, a box the wrong way round) is left out as
      there. The frames of random themes of gauges the same to the bit; with a glow round the
      ring within a level. A ring gliding and a colour fading are motion (below); a bar's or a
      ring's numbers past 53 bits (Python counts with them exactly, a float rounds them): Rust
      says it does not draw them yet
- [x] calendars (calendar widgets): the month as a grid of days, the week from Monday or
      Sunday, the days' names of the panel's language cut to two letters over the columns,
      the days in their cells, today on a dot drawn four times larger and reduced (its number
      dark on a light dot); in the last week of 9999 left out as Python leaves it (its Sunday
      lies past the dates it knows). The frames of random themes of calendars the same to the
      bit, in six languages
- [x] an AI agent's sign (agent widgets) in a still frame: three lights on a faint ring at
      work, the ring and a light in its middle in the waiting colour, a light resting at its
      top while idle, the ring faint while off (any other reading), drawn four times larger
      and reduced. The frames the same to the bit; a sign at work or waiting moves in a moving
      frame: below
- [x] the sun's way (sunpath widgets): the horizon fading at its ends and the day's way
      dotted; by day the part of the day gone from the day's start's colour to the sun's, in
      its own soft light (a blur), the sun glowing where it stands, worked out in numpy's
      float32 as Python's is; by night the moon's crescent; the midnight sun at its height;
      nothing where the sun's times are not known. The frames the same to the bit
- [x] finishes (render/finish.py), as a widget's finish is found and whether it takes the
      widget: the lit finish's texts and readings (lit.py's LitText): the text as the renderer
      draws it without its glow, its letters faded by its opacity and not its light, its glow
      as light (the text in its colours on black, blurred, in whole levels, the light's alpha
      its brightest band, only inside its glow area if it has one, of ints of any size),
      added over it; clocks, the weather, countdowns and line icons the lit finish leaves flat,
      as Python leaves them. The frames the same to the bit; with a shadow, or the renderer's
      own glow round what the finish leaves flat, a pixel now and then a level apart, as a
      card's. The facets' rings and graphs: Rust says it does not draw them yet (the lit
      finish's weather icons, symbols, graphs and download cards: below)
- [x] the facet and acrylic finishes' letters (facet.py, facets.py): each letter of a text of
      one line, from 20 px on, the finish's own: of bright metal that mirrors the sky (the
      palette's accent, else its primary or cpu colour, a third of the way to white) above it
      and the ground (the background) below, or of acrylic lit from within; its bevel worked
      out of its coverage drawn four times larger (the distance to its edge, exact, its slope
      by a Sobel filter, the normals averaged down), lit from the top left, in numpy's float32
      and in numpy's order of sums; laid where the renderer lays its letters, at the pen's
      quarter pixel. The letters and the frames the same to the bit. (numpy takes float32
      powers from SVML on a processor with AVX-512, now and then a unit in the last place
      apart from the C library's; the terms, made 8 bits, came out the same in every test)
- [x] the lit finish's bars (lit.py's LitBar): the groove milled into the panel as the facets
      mill it (facets.py's groove_bar: a floor of the track's colour and a 45-degree bevel all
      round, worked out four times larger at its ends and in one column between, in numpy's
      float32 and order), plain where a bevel does not read; over it the light of each lit
      span, a pill centred in the groove (drawn twice as large with round ends, reduced), its
      core towards white and a soft halo in its colour (the coverage blurred in floating
      point, as a card's glow); its glow light added over it, brightest at the head, worked
      out over its glow area (ints of any size, as Python's) at a fraction of the size, as
      SPUR II's glow zones are; flat where it is too thin for light. The facets' raised bars
      (facet.py's FacetBar): a body in its colour, a gradient along the whole track or a
      threshold's colour, filled from either end, or segments in their colours, in the milled
      track; flat where a bevel does not read. The raised bars, alone and in frames, the same
      to the bit; the lit bars' frames the same to the bit here (240 of them), a pixel now and
      then a level apart allowed, as numpy multiplies by the blur's matrices in float32 as
      another processor's library adds them up
- [x] the lit finish's ring gauges (lit.py's LitGauge): the groove milled into the panel as
      facets.py's groove_ring mills it (the band's samples, two by two to a pixel, their edges
      and cuts, the shading in doubles as numpy's normals are), the gauge's ticks flat on it;
      on the middle of the groove the value's arc of light: a round ring's worked out from each
      pixel's distance and angle (numpy's float32 arctan2), any other's drawn twice as large
      and made smaller; the whole sweep's light up to the arc's tip, and around the tip (and
      the start of a closed ring) the light made anew of the coverage in a window round it,
      cut as numpy slices, where the window lies off the canvas too; of a gradient the colour
      where the arc ends; flat under 8 px across or with a groove under 2. The frames the same
      to the bit where numpy takes float32 arctan2 from the C library, as Rust does (240 of
      them); on a processor with AVX-512 numpy takes it from SVML, a unit in the last place
      apart in two of five angles, and there a pixel now and then comes out a level apart
      (3 in 4 million)
- [x] image widgets in a still frame (renderer.py's _draw_image and _live_image): a picture a
      sensor source or a service has (an album cover, a game's logo, the playing app's icon),
      fitted into the widget's box with LANCZOS (stretched, contained at the left, in the
      middle or at the right, or covering it), blurred into a backdrop of its colours (fitted
      and blurred premultiplied at a fraction of the size, then drawn up), its corners rounded
      (their mask drawn three times larger and reduced); nothing for a picture the snapshot
      lacks, a clear piece for a widget with none. The frames of random themes of them the
      same to the bit. The theme's own pictures, animated ones and slideshows: below, after
      the earth. In the two encore themes of 1920 x 720, drawn by Rust
      since, one pixel of 1.4 million is a level apart here, where the volume's bar glow is
      blurred (numpy multiplies by the blur's matrices in float32)
- [x] weather symbols in a still frame (symbol widgets, and the lit finish's weather icons
      from 16 px: symbols.py, weather_art.py): Meteocons' outlines (their paths read as
      weather_art reads them, the curves cut into twenty in doubles), the sun, its rays and
      corona, the moon's crescent, the stars, drops, snow crystals, fog's bands and the bolt as
      weather_art draws them, flat or as frosted glass (lighter at the top, a breath of shade
      in the floor, an edge of light), worked out in float32 as numpy works it out, with
      numpy's own float32 exp (its ratio of polynomials, not the C library's: the same to the
      bit as numpy's on 3.7 million numbers here); each kind of weather's layers, by day and
      by night, in both looks, laid in their resting places as symbols.compose lays them (the
      sun's or the moon's light through the cloud in front, its shade on the one behind, the
      crystals turned); the kind of a weather code as kind_of takes it (int() of it) and day
      or night as the weather icon decides; clouds and the moon in the widget's colour, the
      rest in the palette's (light.role_color). The look the person chose: vivid until the app
      sets it, as in Python's renderer. Still symbols of each kind at 30 sizes from 8 to 300 px
      (1,440 of them), and the frames of random themes of them the same to the bit. A symbol
      that moves: below. 43 of the app's 99 themes are drawn by Rust, 41 of them the same to
      the bit
- [x] graphs in a still frame (renderer.py's _draw_graph, _curve, _draw_end_dot): the curve
      of a sensor's history, or of a course a service publishes whole as text (course_of:
      JSON as Python's json reads it, a gap where a point is no number to draw), across the
      widget, drawn three times larger and reduced: its background, its grid, its fill fading
      from its highest point (or a stepped range's top) or flat, the line through its points
      (Catmull-Rom in Python's order of sums), a gap where a value is none, hanging from the
      top where it says; its range the widget's, else the values' with a tenth of their span
      of room (in whole range_steps, from 0 up where none is below it), kept as Python keeps
      it in a still frame, and the dot at the line's end in it; a graph left out where it has
      neither a course nor two values of history; the lit finish's line as light (the glow
      its halo, worked out at a quarter of the size, the line towards white). The range is
      counted as Python counts it: each end the int or the float it is, the multiples of a
      whole step exact ints however large, so a range of ints a float does not hold is drawn
      too; the ranges of 20,000 random graphs the same to the bit, their types and Python's
      errors too. The frames of 200 random themes of graphs (990 graphs: 584 curves of a
      history, 241 of them lit, 209 in a range of their own, 55 of a course, 130 end dots)
      the same to the bit. Values near a float's largest make a span of no finite size: on a
      log scale every point is NaN. An end dot there fails Python's frame, and Rust makes
      none; a fill's fade fails the widget, as in Python; a line or a fill drawn through the
      NaN points gets them from Pillow's C as ints the processor makes of them (the least int
      on x86, 0 on ARM), so Rust leaves those out. A graph whose own range glides from frame
      to frame: below. 56 of the app's 99 themes are drawn by Rust, 54 of them the same to the
      bit
- [x] lists in a still frame (lists.py's pick, the renderer's _draw_list and its rows, bars,
      columns and cells): the readings a list's lines pick, by key or by pattern as fnmatch
      matches it (translate's sets and ranges, a range running backwards left out; filter
      taking names and pattern as normcase has them, on Windows the system's own small
      letters), in natural order (re's \d, int() of each run: a run of digits int()
      refuses fails the list, as Python's), a reading once (not again under the key it
      repeats, nor one a line leaves out), named by the line, the reading beside the key or
      the sensor's label, sorted by number or by name (casefolded), as many as fit (64 at
      most); each as its text and its bar's share, the largest value filling the bar where
      the range is none; as rows with their bars beside or below the names, as upright bars
      side by side or as tiles filled as high as their values, with a detail beside a name,
      colour rules, names in capitals. fnmatch on 600 random patterns, each against 178
      names, and on 2,000 sets of ranges both ways, hyphens alone and doubled, a first ! or ]
      and escapes (1,418 different), each against every printable letter: 296,800 pairs as
      Python matches them, and filter on both systems. The frames of 200 random themes of
      lists the same to the bit (and 1,200 more); ten lists (rows with a detail and a
      comment, columns of cores and of long names, bars beside their names and below them,
      one low list showing three of its cores, names in capitals as Python's Unicode makes
      them, tiles with and without their levels, more of them than a list takes, a reading
      under no key) at every third width from 8 to 400 px, low and tall, and of no width or
      height, to the bit; three lists casting a glow or a shadow, small type among them,
      within a level (their light blurred in floating point, as a card's: 2 pixels of 17.2
      million a level apart). Lists whose bars glide in a moving frame: below
- [x] fans in a still frame (lists of fans: the renderer's _list_fan_grid, _list_fan_places,
      _list_fan_texts and _list_turning; render/fan.py's Rotor.settle, rotor_layers,
      _coloured, disc_image, blades_image and ring_image): each fan beside its name and
      number, as many as fit side by side and in lines (as even as they go), standing as a
      still frame shows it: its blades where its key puts them, blurred into their disc by
      its speed and dimmed by its light (turning, standing, no reading), in its ring (a groove
      all round, an arc glowing as far round as its share of the most) or in none. The rotor
      is the default look's (noctua-g2): its 144 pictures read as numpy reads them (a mean of
      three channels in single precision, from the first), averaged down to each size as
      Pillow resizes floats, coloured in the theme's colour and a lighter one. A list that
      leaves out the fans never run keeps those seen running since the process began, as
      lists._RAN does, and the graphics card's always. The ring's core (m ** 0.7 in single
      precision) comes out the same whichever powf a system has: numpy's on this processor
      (SVML) differs from glibc's in 43 of its 256 levels, and in none of the 8-bit colours
      or opacities made of them. The rotor's layers at 43 sizes the same to the bit as
      fan.rotor_layers', 20,000 fans settled to the bit (NaN, infinities and no reading among
      them), every length of a ring's arc at three sizes and 400 fans at random, laid as the
      renderer lays them. The frames of 200 random themes of fan lists the same to the bit
      (and 1,200 more), five lists at every ninth width from 8 to 640 px, low and tall, and
      of no width or height, to the bit; two with a glow or a shadow within a level; a fan
      seen running kept from one frame to the next. Where the fans lie apart from each other
      and on none of the names and numbers, they go over the list as pieces of their own, cut
      as they lie on it, as Python lays them (the same pixels; turned, they turn apart: turned
      widgets, below). Fans in a moving frame turn: below. The
      looks and colours one may choose for the fans in the app's settings: below (fans of
      every look). 66 of the app's 99 themes are drawn by Rust, 61 of them the same to the bit
- [x] card slots in a still frame (render/slot.py's Slot: begin, wants, _turn, _has, _target,
      _weather, _line_width, _outline, _cut, _frame and overlay; the renderer's _make_slots and
      its frame loop): which card a slot shows, the first whose rule holds or that has
      something to show by its own (a track that plays, or is paused for less than the slot's
      `hold`; the weather; Claude Code or Codex at work or waiting, or so less than 45 s ago; a
      download whose state json.loads reads as a dict), else the first; in turns the one on
      show while its turn lasts (its line's time, else the slot's, held between 5 s and a
      day), then the next that has. Only the parts of the card on show are drawn, and none of
      the marks or dots that lie over a bar or a graph. The slot's frame is a thin line in the
      card's colour (the module's own, the cover's, the weather's by day and night, the
      network's, else the accent, mixed over the ground), drawn three times its size and
      averaged down, laid in strips along its edges right after the slot's last part. A
      renderer drawing one frame after another keeps what the rules remember, as Python's
      does. lp-check tells what each slot shows (its card, its frame's colour and thickness),
      also where a card holds a widget Rust does not draw yet. The frames of 240 random themes
      of slots, in every look, style, corner and size, beside and over other modules: 137 the
      same to the bit, and in 205 what each slot shows as Python has it; 100 slots drawn frame
      after frame by one renderer (445 frames, 324 to the bit, 60 changes of card); 26 slots
      a hair before, at and a hair after each time a rule keeps (turns and holds no float
      holds among them, and every kind of number a track plays by); the frame's colour of
      every card, the weather's of each kind by day and night (171 frames); and a slot
      whose numbers are past a float's range, which Python's renderer cannot be made for,
      is not made by Rust either. The counter-checks put in 80 faults, and the tests catch
      72; the other 8 change nothing a picture can show (the frame laid whole or in strips,
      which hold every pixel of its line; its layer's opacity, which putalpha replaces; a
      frame of no thickness, which draws nothing; a hold below none, which keeps no track
      on a clock that only goes forward). Slots in a moving frame (changes that roll, fade or
      move, a new track sliding in, the line of light): below.
      73 of the app's 99 themes are drawn by Rust, 68 of them the same to the bit
- [x] moon widgets in a still frame (render/moon.py: face, disc, halo, picture, halo_level and
      lit_share; the renderer's _draw_moon, _moon_classic and _moon_piece), in both looks. The
      classic one: a disc in its night colour and its lit side bounded by the terminator, an
      ellipse, drawn three times its size and averaged down. The vivid one as the moon looks:
      its face made once as moon.py makes it, from numpy's default_rng(1969) (SeedSequence,
      PCG64, the ziggurat's normals and exponentials, powers and uniforms: the same doubles to
      the bit), its noise of grids enlarged bicubic, its seas, craters and rays in float32, or
      in float64 where moon.py's numbers are numpy's doubles, with the C library's exp, pow,
      hypot and atan2 where numpy calls them and numpy's own float32 exp; its relief blurred
      with np.convolve's sums as numpy adds them (the ends by OpenBLAS's sdot: float products
      summed in a double), and np.gradient. The whole face is the same to the bit as numpy's on
      a processor without AVX-512 (and with OpenBLAS's Haswell, Zen or Sandy Bridge kernels
      alike). Lit at its phase, north or south (Lommel-Seeliger's light, the relief along the
      terminator by numpy's own float32 tanh), with its halo as bright as it is full. The tests'
      face is made by numpy in a process told to leave AVX-512 unused: where it takes Intel's
      SVML instead (Linux with AVX-512), 5904 of the face's 262144 albedos are a unit or two in
      the last place apart, and of 1560 moons drawn here 4 come out a level apart in a pixel.
      numpy's float32 tanh, and its exp, are the same to the bit on every one of the 2^32
      float32s, with AVX-512 and without (the tests check 400,000 of them and the edges of the
      tanh's intervals); the Generator's draws of eleven seeds, the moons of every size from a
      pixel, phase, colour and night (234 pictures), the frames of 160 random themes of moons in
      their own look or the one the person chose, and of 60 of moon and sun modules: the same to
      the bit. A moon of more than 4096 px a side Rust says it does not draw yet (where the
      panel moves, its halo breathes: below). The counter-checks put in 120 faults and the tests
      catch 110; the other 10 change nothing in any picture the tests make (two in numpy's tanh,
      its first step unfused and 1 from 2^127 on, which still give numpy's tanh on every
      float32; the full moon drawn waning, the same polygon; the halo's level among what keeps a
      still moon, which its phase holds already; a crater's place compared with the sea in
      doubles, which makes the same face; three that change a draw only at a layer's edge, the
      tail's edge or where log and log1p of one number round apart, none of which the tests'
      draws meet; and the C library's float32 tanh in the terminator and its exp in the halo in
      place of numpy's, 3 and 2 units in the last place apart at most, which no byte of the
      tests' moons shows). 83 of the app's 99 themes are drawn by Rust, 77 of them the same to
      the bit (nimbus in its four shapes and the companion screens of almanac and encore are
      new; nimbus at 1920 x 720 has a pixel a level apart in its moon's light, which was
      counted at first with those the same)
- [x] download cards in a still frame (render/download.py: read, amount, time_left, of_total,
      layout, rail, _tracked, _cut, _graph, _bar_flat, _base, _empty and draw; the lit finish's
      LitDownloads), flat and lit: what loads (its phase, source and name, cut to its room), the
      two readings (the speed or the time left, the amount loaded or the duration) and the
      amount against the whole where it keeps off the second reading's unit, laid out in the
      inset's numbers and scaled with its box, wide or stacked; the speed of the last minutes as
      a curve on its own scale, with its halo and its fill fading below it (np.convolve's sums
      and np.linspace's float32s as numpy makes them); the progress a pill in its groove, or
      with the lit finish light in its milled groove, and for a download of unknown size a
      stretch of light that runs to and fro, where it is at the frame's time, kept frame after
      frame by its place of 64 as Python keeps it. A state is read as Snapshot.state reads it
      (json.loads anew; the card slots' rule now reads it the same way), its numbers as _number
      takes them (an int past a float's range fails the card, as Python's OverflowError does).
      The tests: 2500 random states in five languages read as Python reads them (2085, the
      others failing as Python fails); the frames of 300 random themes of cards in every size,
      colour and finish, at times all along the light's way (284 drawn, all the same to the
      bit); 2304 cards of every width and height; 569 where the card's rules decide by a pixel
      or less (a source beside the heading in the smallest fonts, a long amount beside the unit
      or meeting it to the bit, a name as long as its room to the 64th of a pixel); 80 themes
      drawn frame after frame (415 frames); 60 themes of download modules in every look (59 to
      the bit, one a level apart where the look's glow is blurred in floating point). The
      counter-checks put in 147 faults, and the tests catch 136; the other 11 change nothing a
      picture can show: two graphs' thresholds no box of whole pixels meets; the smoothing
      summed from its other end, a unit in the last place in a quarter of the sums, which moves
      the curve's points by 10^-13 px at most; the area's floor a row higher and the fade's last
      row worked out, which make the same bytes; the halo laid under the fill, in the same
      colour (Pillow's alpha_composite gives the same bytes in either order, all colours and
      alphas tried); a curve kept from its top, which it never reaches; a flat pill's corners a
      pixel smaller and one of a pixel, which Pillow joins the same or never draws; the
      stretch's place kept where no light runs, where it is 0; and the bar's glow kept before
      the stretch's, where the bar has none. A download whose light runs in a moving frame:
      below. 87 of the app's 99 themes are drawn by Rust, 79 of them the same to the bit
      (lumen-pair in its four shapes is new)
- [x] fan widgets in a still frame (the renderer's _draw_fan and _laid_fan; fan.Choices'
      for_widget, with none of the person's choices; render/fan.py's geometry, fan_parts and
      Rotor.settle): a rotor of the default look in its ring or in none, in the theme's colour
      or one of its own, standing as its reading shows against the widget's most (blurred by
      its speed, dimmed by its light, its ring's arc as far round as its share), its blades
      where its id puts them; the square of fan and glow in the middle of its box, the room on
      either side floored as Python's // floors it (below nought too), the box the editor hits
      the widget's own, with the widget's effects. A most past a float's range fails the fan
      where there is a reading, as Python's sums with it fail. The frames of 300 random themes
      of fans of every size, square or not, at every speed a reading gives (216 drawn, all the
      same to the bit); 60 themes drawn frame after frame as their readings change (202
      frames, to the bit); fans with a glow or a shadow at ten sizes within a level (2 pixels
      of 1.5 million a level apart, their light blurred in floating point). The counter-checks
      put in 19 faults, and the tests catch 17; the other two change nothing a picture can
      show: a most past a float's range taken as 0 where there is no reading, which nothing
      then looks at, and another most where the widget gives none, which never happens (the
      theme's table gives every fan its own). The other looks, their own colours, the square
      frame and what the person chose for the fans: below, as fans that turn in a moving
      frame.
      91 of the app's 99 themes are drawn by Rust, 81 of them the same to the bit (Lumen in its
      four shapes is new; its two of a level apart in the glow of their rings and a frame, none
      in their fans)
- [x] analog clocks in a still frame (the renderer's _draw_analog and _analog_piece): the face
      and its faint rim, its marks (the quarters', the hours', and from 110 px those between
      them), the hour and the minute hands with round ends, the second hand (or none) and the
      dot in the middle, in the theme's colours or their own, drawn three times as large and
      reduced as _canvas and _down make them, the hands' angles in Python's sums (math.radians
      is the same times pi/180 as Rust's to_radians); kept by the hands they show. A second
      hand that ticks, glides or goes round stands alike in a still frame. The frames of 300
      random themes of clocks of every size, square or not, at moments of the years round now
      and at the hands' edges (288 drawn, all the same to the bit, another place's time among
      them since the earth widget's work, below); every side from 1 to 140 px, square and not,
      to the bit; 40 themes drawn frame after frame a part of a second to half a day apart
      (181 frames, to the bit); clocks with a glow or a shadow at six sizes within a level
      (none apart here). The counter-checks put in 27 faults, and the tests catch them all. A
      second hand that glides or goes round in a moving frame: below. 93 of the app's 99 themes
      are drawn by Rust, 83 of them the same to the bit (almanac with its analog face, in both
      its shapes, is new, the same to the bit; terra's six, the earth, are left)
- [x] the earth's picture (render/earth.py's picture, subsolar, _basis, _lookup, _lit, _air,
      _air_pixels and _spot; sensors/sky.py's _sun_path): a globe lit by the sun where the
      sunrise equation puts it at a moment, seen from above a place: the day's land and sea,
      the clouds over them, the night's ground and its cities' lights, the dusk along the line
      between them, the glint of the sun on the water, the air glowing at the rim of the day
      side, your place marked; made at twice its size and reduced. The maps read as
      earth._read reads them, by libjpeg, drafted to the scale that covers the round's width
      (an eighth, a quarter, a half or whole). In earth.py's own numbers: float32 where its
      arrays are, the Python floats doubles made float32 as numpy takes them beside an array
      (and the one product of the clouds' bytes and a Python float, float64 as numpy makes
      it), numpy's own float32 exp, np.dot's sums as OpenBLAS's sdot makes them, the C
      library's asinf, atan2f, powf and hypotf. numpy on Linux with AVX-512 takes its float32
      arcsin, arctan2 and power from SVML (of 20,000 numbers each, 30 % of the arcsines and
      39 % of the arctangents up to 3 units in the last place apart from the C library's, a
      tenth to a fifth of the powers 1 unit), and Python's own globe is not the same there as
      elsewhere: the test asks Python for its globes in a process with numpy's AVX-512 paths
      off, and as numpy runs here. 88 globes of every size (those where libjpeg reads the maps
      at another scale among them), at moments of the years round now, seen from anywhere
      (the poles among them), marked or not (near the rim, where the mark fades, among them),
      in any colours, with and without the clouds and the lights: the same to the bit as
      Python's with the C library's float32 functions; as numpy runs on this processor,
      Python's own globes come out 84 pixels apart from those in the first 80, 12 levels at
      most, and Rust's the same. The counter-checks put in 51 faults, and the tests catch 48
      (the places near the rim came of one they missed at first); the other three change
      nothing the tests' globes show, nor 160 more looked for them, the sun all round: the
      glint looked for in a band the sun does not reach, and bands of 64 rows (where the sun
      is below the horizon its glint is far below a level), and the clouds' veil over the
      glint in float32 (a unit in the last place of the glint at most). The earth widget:
      below, after the zones
- [x] time zones as Python takes them (lp_py::zones): a zone's file read as zoneinfo reads it
      (TZif of every version: its transitions and types, past the last transition its
      footer's POSIX rule; before the first and after the last as CPython's C zoneinfo takes
      them), the computer's own zone as glibc's tzset finds it (TZ unset: /etc/localtime;
      empty: UTC; the name of a file, after a colon or not, in TZDIR or as a path; else a TZ
      string; UTC where glibc reads none); each rule's days as each counts them (zoneinfo's
      zero-based day a day early and its J59 a day late in a leap year, glibc's years before
      1970 counted from 1970's); and on them what datetime asks: a naive time's timestamp
      (local_to_seconds, in either fold), astimezone() and its offset (both folds asked, as
      local_timezone_from_local asks them: in the gap where the clocks go on, the offset of
      before it), astimezone(zone), fromtimestamp and its fold, and none where Python raises
      (at the edges of the years 1 and 9999, an offset of a day). 69 TZ values (the system's
      zones by name, path and colon, TZ strings of every kind of day and time, north and
      south, offsets up to a day, and what glibc reads as UTC): 190,436 naive times about the
      moments their clocks change (13,961 in a gap, 13,533 where the fold picks the other
      moment), at new year's and at random, and 86,985 timestamps (18,378 of them the second of
      two moments of one local time); 16 named zones about the moments they change: all as
      Python's. 22 zone files made for the test, where zoneinfo and glibc part, the same as
      both. Where glibc would read a TZ value by rules or leniencies of its own (summer time
      without its rules, which it takes from posixrules; a space or a second sign before an
      offset; minutes past 59, more digits than POSIX's, more after the rules; leap seconds; a
      file it reads otherwise than zoneinfo), Rust says it does not know the zone (12 TZ values
      and 6 files in the tests). The counter-checks put in 42 faults, and the tests catch them
      all (5 of them once cases were added: J59 in a leap year for both, a version 1 file whose
      two blocks part, a rule that changes at new year's seen from 14 hours east, and a fault
      written anew where it changed nothing)
- [x] the earth widget, standing (the renderer's _draw_earth; earth.view_of, zone_longitude and
      redraw_s), and clocks and clock faces in another time zone (_time): the globe lit for
      the frame's moment on the wall clock (its timestamp in the computer's zone, in steps of
      redraw_s), seen from above your place, else your time zone's longitude a little north of
      the equator (the computer's offset now, as datetime.now() finds it when asked, 15 degrees
      an hour), your place marked where the widget asks, in its colours, with its clouds and
      lights; drawn again where its moment, its view or its look moves, kept where none does.
      A clock's time in another zone is the frame's time put into it as astimezone puts it
      (through the computer's zone, its fold where the clocks go back and the gap where they
      go on, then zoneinfo's fromutc); where no zone has the name, the computer's own time,
      warned as Python warns. The frame's time carries its fold. In the tests Python's globes
      are those it makes with the C library's float32 functions (where numpy takes SVML's
      here, the globes a frame asks for are made so in a process of their own, and the frames
      drawn again with them; the globe itself is the earth's picture's, above): the frames of
      60 random themes of globes (57 drawn, all the same to the bit), 24 themes drawn frame
      after frame a part of a step to a day apart (78 frames, to the bit), globes with a glow
      or a shadow at four sizes (within a level); with the computer's zone made five others
      (summer time north and south, half an hour's, 14 hours east, a TZ string), 57 frames of
      globes and 192 of clocks in 14 zones, at moments about those when the clocks change in
      either fold and at random, all to the bit, and the warnings as Python's. The
      counter-checks put in 23 faults (in the zone a clock asks for, its warning, the fold, the
      globe's turning, size, mark, colours, clouds and lights, its step, its view, the zone's
      longitude and its bounds, its box and place, the moment it is kept by, lp-check's fold
      and place), and the tests catch them all. A globe that turns in a moving frame:
      below. On Windows the earth and another place's time (Rust does not know the computer's
      zone there): Rust says it does not draw them yet. All 99 of the app's themes are drawn by
      Rust on Linux, 89 of them the same to the bit (Terra's six are new, all to the bit); 93
      on Windows
- [x] the theme's own pictures in image widgets (the renderer's _draw_image, _asset_image,
      _read_picture, _slides, _slide, _moving and _animation; moving.py's read_animation): a
      picture of the theme's folder, upright as its EXIF says, in RGBA, fitted (or as it is),
      blurred and rounded as a live one is; a clear piece of the widget's size for one that is
      not there, a folder or outside the theme (warned as Python warns); kept fitted within
      64 MB (render/kept.py's Kept: the least recently used go, the newest stays). A
      slideshow's pictures as _slides finds them: the widget's, then one a line (a name, a
      source's, a pattern a folder's pictures and videos as pathlib's glob finds them in
      Python 3.12: hidden names too, a part before others folders only, "**" the folders
      below without following links, twice each path once, ".." the folder above; a root or
      "**" in a part refused as pathlib refuses them, before any folder is read), sorted as
      paths are, none outside the theme, read again after half a minute. Still pictures take
      turns by the clock (the frame's time as a timestamp of the computer's zone,
      floor-divided as floats are); one alone shows, and where its timestamp fails (at the
      edges of the years) so does the widget, as in Python. An animated picture (GIF, WebP,
      APNG) shows the frame of the moment from when it first showed, at its speed, round and
      round: each frame finished as a picture is, every second (third ...) kept where all
      would take more than 96 MB, each as long as those it stands for (lp-decode gives them
      one after another: one is held at a time). The tests: 160 random themes of the theme's
      pictures (124 drawn, all the same to the bit, 15 left out for a file of a kind Rust
      does not know), 160 of slideshows at random moments and at their turns (96 drawn, to the
      bit), 40 of animated pictures frame after frame, a part of a frame to a minute later and
      back (122 frames, to the bit), an animation of frames of its own lengths at each speed a widget may
      ask, at each frame's end, just before and after it and before it first showed, an
      animation whose frames hold more than 96 MB, the pictures of 312 slideshows' lines
      (patterns of every kind of part, links, ".." out of the theme, the errors) as _slides
      finds them, a picture alone at the edges of the years in four zones (92 frames to the
      bit). The counter-checks put in 45 faults, and the tests catch them all (3 of them once
      cases were added: two "**" apart, a moment before an animation first showed, a speed
      under the least). Rust says it does not draw these yet: videos where it is not told of
      ffmpeg (a slideshow's turn of one too), a file of a kind lp-decode does not know (also as
      the background: Pillow knows forty more), folders of names that are no Unicode; where it does not know the computer's zone (a TZ glibc reads by rules of its
      own), slideshows by the clock and a picture alone at the edges of the years; on Windows,
      slideshow patterns of other than ASCII (pathlib matches there without case). Pictures that move in a moving
      frame, and slideshows with them: below
- [x] motion (render/motion.py and the renderer's _glide_s, _spring, _lead, _eased, _rule_step,
      _rule_mix, _rule_paint, _metric and _settled): in a moving frame (the panels' loop) a
      smooth value glides to each new reading on its sensor's spring, critically damped (95 % of
      the way after the theme's smoothing_ms, a widget's own glide_ms, the volume's bars and
      rings 320 ms behind its number, which follows at once), from the frame
      before as the frame rate says, the numbers, bars and rings of a sensor together, also
      into the next theme; one that was off the panel a while (not over a pause between frames)
      starts where it is. Crossing a threshold fades the colour a step per 0.18 s, the same for
      every widget of the sensor with the same rules (50 as 50.0), mixed in eighths along a
      smooth step: a number's text colour, a bar's or ring's colours (a gradient's ends too), a
      bar's dot at its gliding head. A gliding number's text is made at the histories' points
      (25 a second) and stands once it reads the same; a bar or ring is there once within a
      five-hundredth of its span. Whether a frame moves on is told as Python's renderer tells
      it (the metric that settles told so, as _settled tells it). The tests: one renderer
      drawing random themes frame after frame at a frame rate or none, the frames at once to
      two seconds apart and back in time, the readings new between them now and then: 60
      themes of metrics (all to the bit), 60 of bars, facet bars and rings (to the bit), 50 of
      lit bars and rings (within a level, their light blurred in floating point); a reading
      that jumps there and back for two seconds of frames a 50th apart, its numbers, bar, dot
      and rings at the theme's glide, their own and at once, one away a while that comes back
      on the way; values off the panel a moment (back after exactly a pause each glides on,
      after a little more each starts where it is, and so after a frame at the time of the
      one before); a muted volume whose number glides past its value; the app's themes in
      moving frames: drawn by Rust all along to the bit or (their light) within a level,
      whether each frame moves on as in Python. The counter-checks put in 44 faults
      (the spring's curve, speed, start, pause and idle second, freshness, forgetting; the
      fades' pace, way, freshness and keys; the mix; the metrics' text points, moving and
      settling; the bars' and rings' glide, their colours on the way, the dot; the glides'
      lengths, keys and the frame rate's lead; lp-check's frame rate and moving), and the
      tests catch 41 of them, 5 once cases were added (a pause, a frame at the time of the one
      before, numbers of the same rules back on a fade's way, the muted volume); the other
      three change nothing that is drawn (one old piece of a glide more kept, which only a
      graph's history would look at; a gradient's end where a threshold's colour is never
      empty; a threshold's colour as a gradient of itself). A glide_ms past 53 bits (Python
      divides the whole number, Rust would round it first): Rust says it does not draw it yet
- [x] clocks whose digits roll or fade (the renderer's _draw_clock in a moving frame, with
      _changing, _digit_band, _moved and _ramp): for digits_seconds after the second turns
      (0.42 s unless the clock says, up to a second), each letter that changes from the text
      of the second before rolls (the old one up and out, the new one in from below, by 0.92
      of the band the digits live in, soft over its edges) or fades where it stands, on its way
      as 1 - (1 - p)^3; the renderer's own letters moved as their opacity in their colour, a
      finish's with their colours, a fraction of a row bilinear; the letters that stay where
      they stand; a text laid out otherwise the second before (another fitted size, width or
      place) as it stands, and so is it at any other time. In the first second of the year 1
      Python cannot take a second off: the clock is left out, warned, as there. The tests: 80
      random themes of clocks (formats of seconds, spacing, digits of one width, a width they
      fit, any alignment, a finish's letters) frame after frame across their seconds, 495
      frames to the bit, 240 of them on the way; the edges of the digits' time (0.42 s, half a
      second, a second and a little less, at the microseconds round their end); texts fitted
      in other sizes yet laid out alike, of other lengths the ellipsis makes alike, the
      microseconds of the second before over midnight; the first second of the years; the
      app's themes in moving frames: 49 of the 99 drawn by Rust all along (32 before). The
      counter-checks put in 31 faults (the roll's travel and way, the band and its edges, the
      ramp, the moving of a fraction of a row and whose colours, the fade, the letters that
      stay, the layouts compared, the clock's way, its digits' time and its edges, the texts'
      lengths, moving, effects, the year 1, the second before), and the tests catch 28 of
      them, 3 once cases were added (the fitted sizes, the lengths, the microseconds over
      midnight); the other three change nothing that is drawn (a ramp's one column resized
      nearest, which is as it is; a digits' time Rust takes where a clock has none, which the
      theme always gives; the layouts' places compared beside their sizes, which on a whole
      pixel are the same where the sizes are)
- [x] lists whose bars glide (the renderer's _draw_list in a moving frame, with _list_piece's
      parts and _joined): each bar glides to its reading on its item's own spring, with the
      bars and rings of its sensor, and stands once within a five-hundredth of its span (of a
      list without a range of its own, the span its largest value gives); the colour rules go
      by the reading itself. The names and numbers are made once and kept while they read the
      same, the bars and tiles anew while they move, the text laid over them. A list of smooth
      false, without levels or of fans does not glide. The tests: 60 random themes of lists
      (rows, bars, columns, tiles; the volume and cores among their items; smooth or not, with
      levels or not) frame after frame at a frame rate or none, 344 frames to the bit, 66 of
      them on the way; lists whose largest value fills their bars and one whose range is
      upside down, frames a 50th apart as the readings jump there and back, 240 frames to the
      bit. The counter-checks put in 13 faults (the bars' glide, its spring and its span,
      gliding without levels, the rules by the gliding number, the text and the levels apart,
      kept apart, joined, in a still frame too), and the tests catch 12 of them, 1 once a case
      was added (the span of a list whose largest value fills its bars); the other changes
      nothing that is drawn (the levels left out where the two parts are not laid alike, which
      they always are: both the list's box with its margin, under the same effects). Where a
      list's text lies on none of its bars (where they lie is noted as they are drawn, as
      _Levels notes it), the two are laid apart on the frame, as Python lays them, which gives
      the pixels the join gives; turned, they turn apart (turned widgets: below). Lists whose
      fans turn in a moving frame: below
- [x] fans that turn (render/fan.py's Rotor.advance and Beats, the renderer's _draw_fan and
      _list_turning in a moving frame): a fan widget's rotor and each fan of a list turn with
      their speed, which follows the reading with inertia (0.45 s, from the frame before, a
      fifth of a second at most), runs out by friction and stands at 0 RPM; the ring and the
      light follow alike; the blades stand where the beat of the sensor has them (the same for
      every fan of the sensor at the panel's frame rate: a fan widget and a list's fan of one
      sensor in step; a frame a moment back in time taken back by the speed, one further back
      on another clock), never more than 0.3 of a blade's pitch a frame. The frame moves on
      while a fan turns. The tests: 60 random themes of fan widgets and lists of fans (of the
      same sensors now and then, some of them off the panel a while) frame after frame at a
      frame rate or none, 344 frames to the bit, 267 of them on the way; fans of one sensor at
      other speeds on its beat, through a pause, frames back in time and further back, a fan
      that stops and runs out, a reading that goes and comes back, at three frame rates: 180
      frames to the bit; the app's themes in moving frames: 57 of the 99 drawn by Rust all
      along (49 before). The counter-checks put in 27 faults (the rotor's time constant, its
      following, its time between frames and back in time, friction and where it stops, the
      ring and the light, its first frame, its time kept; the beat's rate, its most step and
      time, back in time, another clock, its time kept, where it begins, whose it is; moving;
      the widget and the list standing in a moving frame, a list's rotors by the item alone),
      and the tests catch all of them
- [x] card slots in a moving frame (render/slot.py's Slot.begin with its changes, _move,
      _slide, live, leave, _changing, _accent, _ignition and overlay; window, slid, moved,
      faded and _shade; render/light.py's add_line and its curves; the renderer's frame loop
      with the parts of the card that goes): a change from one card to the next takes the
      slot's `change` seconds of the frames' planned time, from the frame it begins in (not the
      slot's first, nor with `cut`): the light rolls the card on show up into a line of light
      at the top edge and the new one down out of it, each row it cuts showing its share; a
      fade takes the old one out as the new one comes in; a slide or a rise has the new one
      push the old one out to the left or upwards, a gap between them; a wipe sweeps a beam of
      light across, the new card behind it. The card that goes shows what it showed last, as
      its parts were then. A new track on the playback card on show slides in from the right
      as the old one moves out (`slide` seconds). The frame's colour reaches the card's over
      0.55 s. The frame moves on while any of that is on its way. Changes chosen at random
      (Python's random module picks them): Rust says it does not draw them yet. A panel's
      first moving frame, which draws every card once first (not in a preview): below. The
      tests: a
      slot of a track's card and the weather's changing each way at a frame rate and none,
      frames a 25th of a second apart through the change and back in time within it, the
      track back and another sliding in: 768 frames to the bit, 594 of them on the way; 80
      random themes of slots (every way of changing, of a moment to seconds, tracks sliding
      in) frame after frame: 792 frames to the bit, 311 on the way; the edges: a change that
      ends exactly at a frame, a fade a moment after it began and before it ends, a track
      sliding in with less to show and then rolling up, a change into the track's card that
      gets another track on the way, a cut from one track's card to another's, a small slot
      on a large panel, a slot on a clear ground under a see-through bar, frames 8192ths of a
      second apart where the rolling edges meet in one row: 516 frames to the bit; a lit slot
      whose track's progress glows, changing each way: 240 frames within a level; the app's
      themes in moving frames: 72 of the 99 drawn by Rust all along (57 before; 71 on
      Windows). The counter-checks put in 52 faults (the light's shares and strength, the
      fade's, the slide's gap, the colour's time; when a change begins, ends and moves on, what
      the old card shows and the new one keeps; the curves of each way; the track's slide,
      when, how and with what; the rows cut and their shares, light cut and faded; the
      frame's colour on its way; the line of light's strip, colour, alpha, place and ends; the
      old track shown; the frozen parts' needs; moving), and the tests catch 48 of them, 8 once
      cases were added (the edges above); the other four change nothing a frame shows: a glow
      faded in its alpha alone, which a frame adds without it (on a clear ground onto a card
      that already covers it); light cut or faded in its colour, which no part of a card is; a
      line's strip two rows narrower, whose rows beyond its halo hold no light
- [x] moons whose halo breathes (the renderer's _draw_moon and _moon_piece in a moving frame,
      render/moon.py's halo_level and picture at a time): the halo of the moon as it looks
      swells and settles over seven seconds, its strength at the frame's time in steps of a
      tenth of a second (Python's round: at a step's very half the even one), the picture made
      anew only where that strength changed, a few times a breath; the classic moon stands. The
      frame moves on while a moon breathes. The tests: 70 random themes of moons in either look
      and of moon modules frame after frame at a frame rate or none, the frames at any
      distance, back in time too, over a breath and more, the phase new now and then: 598
      frames to the bit, 440 of them on the way; moons full, at 0.6 and at 0.3 and a classic
      one in frames at a step's very half, where the strengths on either side of it are apart,
      back in time and long after, at two frame rates: 20 frames to the bit; the app's themes
      in moving frames: 78 of the 99 drawn by Rust all along (72 before; 77 on Windows). The
      counter-checks put in 17 faults (the steps a second, how the time is rounded to them,
      steps by the second; moving; the halo's time the step itself, half a step on, the
      frame's own; what keeps the picture: the phase alone, the still halo's strength; the
      picture's halo still; a moon that breathes in a preview only, or on a panel only; the
      breath's start and length), and the tests catch all of them (the one of a preview by the
      still frames', whose moons would breathe)
- [x] clocks whose second hand glides or goes round (the renderer's _draw_analog in a moving
      frame, _analog_part, _second_hand and _hand_effects): a second hand that glides moves to
      each second in the clock's digits_seconds (0.42 s), as rolling digits do, and rests for
      the rest of the second, kept; one that goes round never stops. The face with its hour
      and minute hands (the minute hand moving on every five seconds) is kept by the clock, the
      widget and those hands, the newest eight (as Python's dict keeps them: a face in the
      cover's colour stays as it was made while it is kept); the second hand and the dot are
      drawn in their own small box and laid onto it. On a face that is not see-through the
      shadow and glow are the face's alone, kept alike; a see-through face's are made whole
      each frame. The frame moves on while a hand moves. The tests: 60 random themes of clocks
      that glide (in their own time, the digits', or one that is none), go round, tick or show
      no second hand, frame after frame at a frame rate or none, the frames at any distance,
      back in time too: 484 frames to the bit, 374 of them on the way; 40 of clocks with a glow
      or a shadow, their faces solid, see-through or none, the whole see-through now and then:
      303 frames within a level, 277 on the way; the edges: a second's very start, a moment
      before and at the end of a glide, a second's last microsecond, five seconds on, a minute
      and noon, back in time to a face kept, faces and a resting hand in the cover's colour as
      the cover changes (kept as they were, made anew once eight others came after them), a
      clock going round alone on the second, at two frame rates: 62 frames to the bit; the
      app's themes in moving frames: 80 of the 99 drawn by Rust all along (78 before; 79 on
      Windows). The counter-checks put in 39 faults (a sweep's and a glide's time, the glide's
      curve and how long it may take; the minute hand's steps; what is kept at rest, and when;
      moving; the faces kept, which go, by what; the face without its second hand; the hand's
      length, tail, width, angle, place, round ends and box, the dot; the effects of a solid
      and of a see-through face, the halo kept and by what, see-through faces laid on, moved
      by the halo; a glide taken as a tick, a clock without a second hand moving; the shadow's
      colour), and the tests catch 36 of them, 3 once the edge cases were added (a resting hand
      kept, a lone sweep on the second, a face let go); the other three change nothing a frame
      shows: the faces kept by the id and the hands alone (a theme's ids are its own, and a
      widget stays the same while a renderer draws it), and the hand's box a pixel narrower
      round it or its left edge rounded rather than floored (the box holds all of the hand's
      pixels either way)
- [x] graphs whose own range glides (the renderer's _glided_bounds, _range_glide and _range_now
      with its _Range, in a moving frame): a graph without min or max takes its range from
      the values it shows, with room, and glides there from the range it drew last at a time
      constant of 0.6 s (in whole range_steps 0.12 s, and there at once within a hundredth of
      its span); wider at once where the values leave it, narrower to theirs once they have
      used less than 0.6 of it for three seconds, the values always inside; anew where the
      time went back; its end dot in the range of the frame. Counted as Python counts it:
      each end the int or the float it is. The frame moves on while a range glides. The tests:
      the ranges of 3000 random graphs frame after frame (in moving frames or still ones, the
      numbers the same, larger or smaller, a peak come in, half of them gone or others, the
      frames at any distance, back in time too), with the range a moment later: 20,819 steps
      the same to the bit, 3,370 of them gliding, and Python's errors in 1,635; 60 random
      themes of graphs of their own range frame after frame, their histories growing, at a
      frame rate or none: 432 frames to the bit, 179 of them on the way; 50 with their glow,
      flat or lit: 379 frames within a level, 118 on the way; a peak that comes in, stays and
      leaves the window, in whole steps from 0 up and up to 100, through the glides, a pause
      and back in time, at two frame rates: 96 frames to the bit, 78 on the way; the app's
      themes in moving frames: 84 of the 99 drawn by Rust all along (80 before; 83 on
      Windows). The counter-checks put in 29 faults (the paces, nearness, the time to narrow
      and the share used; back in time and still frames; the glide's curve and direction; the
      values inside below and above; wider downwards, upwards and to what; the times of use,
      kept and when; the time, values and range kept; the snap in whole steps, the gliding
      ends; the range without room; the range a moment later at its pace and with the values
      inside; moving), and the tests catch 28 of them; the other changes nothing a frame
      shows: the end dot laid in the range drawn last rather than in the range now (it is
      drawn right after its graph, in the same frame, where the two are one)
- [x] agents' signs that move (render/agent.py's step and picture at a step, the renderer's
      _draw_agent in a moving frame): a sign at work has its three lights go round once in
      1.6 s, a waiting one breathes once in 2.4 s, each in 48 pictures, the step the frame's
      time gives (int() of the steps gone, counted on from the last before nought); idle and
      off signs stand. The frame moves on while a sign moves. The tests: 60 random themes of
      signs frame after frame, their states new now and then, at a frame rate or none, the
      frames at any distance, back in time too: 415 frames to the bit, 255 of them on the way;
      a sign at work and one waiting in frames a float either side of the times their steps
      change at, at a round's and a breath's end, before nought, far on and back in time, at
      two frame rates: 134 frames to the bit; the app's themes in moving frames: 88 of the 99
      drawn by Rust all along (84 before; 87 on Windows). The counter-checks put in 12 faults
      (a round's and a breath's time, the steps, floored, a remainder below nought, counted in
      another order; a breath as long as a round, an idle sign going round; the picture where
      its motion starts; moving; what keeps the picture; the step's time), and the tests catch
      all of them
- [x] weather symbols that move (symbols.py's compose at a time and its draw in a moving
      frame): a symbol widget that moves is drawn at the frame's planned time, its offset
      added, in 25ths of a second (Python's round: at a step's very half the even one), the
      picture made anew only where the step changed. The corona breathes over 4.3 s, the rays
      turn 7 degrees a second (turned in quarter degrees, bicubic, as Pillow turns them) and
      on a big sun pulse, the stars twinkle each at its own pace, now and then a shooting star
      crosses a big clear night behind the moon, fog's bands pass each other and thicken a
      little, rain falls a second a drop and faster as it falls (on a big symbol farther drops
      behind it, and a ring opening where the last drop landed), drizzle two drops a lane
      (three on a big one), crystals fall, sway and turn (in steps of three degrees in the
      second look; farther ones behind them on a big symbol), the clouds drift, rise and sink
      and breathe, each motion at its own period, and a bolt flashes twice and rests, its
      cloud lit from within as it fades (lit in float32 as numpy lights it); a big symbol is
      one of the second look from 120 px, and below 120 px the fog and the clouds move 1.8
      times as fast. The frame moves on while a symbol moves. The tests: each kind of
      weather, by day and by night, at eight sizes from 8 to 262 px, in both looks, flat and
      lit, at four of 28 moments (before nought and past a thousand seconds, through a bolt's
      flashes a 25th of a second apart): 1,536 pictures to the bit; 60 random themes of
      symbols frame after frame, the weather new now and then, at a frame rate or none, the
      frames at any distance, back in time too: 397 frames to the bit, 343 of them on the
      way; the app's themes in moving frames: 94 of the 99 drawn by Rust all along (88
      before; 93 on Windows). The counter-checks put in 43 faults (the steps a second, the
      offset, what keeps the picture, moving; the tempo of a small symbol and where small
      ends; the corona's breath; the rays' pace, their turn, its way and steps, the pulse;
      the stars' twinkle; the shooting star's way, time, fade and width; the fog's drift and
      thickening; the rain's fall, the far drops' size, the splashes' time, ring, opening and
      size; drizzle's fall and its third drop; the crystals' fall, way, sway, turn and the far
      ones' pace; the clouds' breath, drift and stretch; the bolt's beat and flashes, the
      cloud's light and its fade), and the tests catch 42 of them; the other changes nothing
      a frame shows: a crystal's turn counted as the floor of its angle over three rather than
      by Python's floor division (for a divisor of three and an angle from nought the two
      never differ: the quotient, rounded, does not reach the next whole number; 2.1 million
      angles, the neighbours of every multiple of three among them, the same)
- [x] numpy's own float32 exp as each of its builds has it (np.rs): the whole number of the
      exponent, its product and its sum two steps where GCC builds numpy for Linux (its C as
      ISO C11, which contracts nothing) and one fused step where MSVC builds it for Windows
      (/fp:contract), as the builds' machine code shows: 84 float32s come out apart, each as
      its build makes it. Rust's exp the same to the bit as numpy's on all 4,294,967,296
      float32s on Linux, with and without its AVX-512 paths; the tests ask numpy on either
      system for a dozen of the 84
- [x] globes that turn (the renderer's _draw_earth with spin in a moving frame; earth.Turning:
      tables, lit, use, air_at, mark_at, picture_of, frame, longitude, step_at and step_of;
      turn_latitude): a globe seen from above your latitude (within 30 degrees of the equator,
      20 north without a place), beginning with your place in the middle (else your time
      zone's longitude), turning a pixel at its middle a step, once in spin seconds (one at
      least). Where each pixel of its disc lies on the map is worked out once, in float32 as
      numpy works it out (the C library's asinf and atan2f); the map is lit whole for each
      moment on the wall clock (the maps drafted by libjpeg and boxed to the round and half of
      it, each pixel's sun from its latitude's and longitude's sines and cosines); each frame
      looks the disc up in it at its step, lays the air over it anew every two degrees and
      keeps it between (as laid from the step it was laid at), and marks your place. A frame
      is made for each step only, and it is due again when the next step is: the renderer's
      due, which lp-check gives now and the tests compare for every moving frame (a globe
      that turns does not move the frame on). The map is lit here and now, as a still frame
      lights it: the panels' loop lights it aside, in a thread of its own, with the loop.
      numpy's own float32 sin and cos (np.rs: Cody-Waite's reduction to a quarter turn and its
      polynomials, the whole number of quarter turns one fused step as both GCC and MSVC
      build it, the C library's past 117,435.992 and 71,476.0625) the same to the bit as
      numpy's on all 4,294,967,296 float32s, with and without its AVX-512 paths. The tests:
      69 turning globes of every size up to 1000 px, seen from any latitude (beyond 60
      degrees too), lit for moments round the year, their frames at steps a pixel to half a
      turn on, past the round and before nought, marked or not, lit anew now and then, their
      whole maps, where their pixels lie, their steps at times (before nought too) and turns
      of every length, with a longitude in the middle (halfway between two steps too), and
      Python's errors (a frame before the light, a turn of no time, a time that is no
      number): 165 frames, 58 maps, 68 tables and 455 steps the same to the bit as Python's
      with the C library's float32 functions; 30 random themes of globes that turn or stand
      frame after frame, your place anywhere or none, at a frame rate or none, the frames at
      any distance, back in time too, the moment on the wall clock moving with them or by a
      redraw_s and more: 146 frames to the bit, 136 of them due at a next step; the app's
      themes in moving frames: all 99 drawn by Rust all along (94 before; 93 on Windows, where
      Rust did not know the computer's zone then: below). The counter-checks put in 41 faults (the
      air's steps, its key, how it is rounded, where it is laid from, laid every frame; the
      round's width, at least and rounded; the rim and its alpha; the rows and columns by a
      reciprocal; the latitude held and the latitude seen from, with and without a place; the
      longitude's way, the ground's way, the first step's way and halves; the steps
      truncated, a turn under a second, when the next is due, where the turn begins and which
      way; the soonest due, kept from the frame before; lit once or every frame, the piece
      kept through a light, the mark; the sines and cosines of the longitudes and latitudes,
      the sun's sum, the maps resized, the rows' latitudes; the rim's alpha laid; turning
      with no spin; numpy's sine: the quarter unfused, the cosine's own reach, its last
      product, its quarter), and the tests catch 39 of them, 5 once the whole maps, the
      tables and the halfway longitudes were added; the other two change nothing: the air
      laid anew with each light is seen to twice in Python (its key holds the light, and a
      new light lets the key go), and either alone does it
- [x] card slots warmed up for a panel (the renderer's _warm_slots, Slot.pinned and settle): a
      panel's renderer (not a preview's) draws every card of every slot once before its first
      moving frame, slot after slot, each card pinned on show without a change; each slot then
      forgets what it showed, and the frame is drawn from there (a slot warmed before another
      is begun again by the frames of those after it, as in Python). Those frames go; what
      they made stays: the cards' pieces, and the springs of what they show, so that a card
      that first shows the frame after glides on from there. lp-check draws a panel's frames
      where asked. The tests: 50 random themes of slots of every kind of card, a panel's frames
      one after another, at a frame rate or none, the frames a moment to a second apart, and
      back in time: 334 frames to the bit, 177 of them on the way; a slot showing a track, its
      ring card drawn only in the warm-up and shown the frame after it as the track stops and
      the load leaps, alone or beside another slot, its change of each way, at a frame rate and
      none: 72 frames to the bit. The counter-checks put in 11 faults (no warm-up, one in a
      preview too, no settling, the first card alone, the slots the other way; a pinned slot's
      colour faded, its track kept; settling that keeps the slot begun, its card, what it
      showed or its colour), and the tests catch 5 of them, one once the ring card's edge case
      was added; the other six change nothing a frame shows: settling forgets a pinned slot's
      colour and its track anyway; the first frame chooses each slot's card anew and shows
      that card's parts; and with two slots or more the first frame moves on whichever was
      warmed last (every slot but that one has been begun again by the warm-up of those after
      it)
- [x] turned widgets (the renderer's _turn and _turn_pieces; Turn, _turned_point,
      _turned_box, _turned_image and _turned): a widget turned by its `rotate` (degrees
      clockwise, as float(rotate) % 360 makes it: -1e-20 is a whole turn) about the middle of
      its box, a text about the point it is aligned at halfway down its line (its font's
      ascent and descent), a module's parts about the middle of the module; a quarter turn and
      its multiples pixel for pixel, any other angle smoothly (Pillow's bicubic rotate, all of
      the layer kept), each piece where its middle turns to; its box in the editor the box
      round it turned (the corners rounded to six places: a quarter turn's sines are not quite
      1 and 0), a module's round all of it; a frosted piece's mask turned with it and its box
      where that now lies; its light and a slot's line of light turned too. What lies over a
      widget turns with it: a bar's mark and dot, a graph's end dot, a slot's frame, its line
      of light and the card that goes, and what Python lays apart over a list (its fans where
      they lie apart from each other and on none of its names and numbers, its names and
      numbers over its gliding bars where they lie on none of them), which Rust now lays
      apart as Python does: turned apart and turned joined are not the same pixels. A piece
      turned as it was the frame before is that frame's again. The tests: 20,000 points and
      20,000 boxes turned to the bit, and the angles of 2,000 rotates; 22 kinds of widget
      (texts, metrics, icons, weather, clocks, analog clocks, bars, gauges, calendars, agents,
      the sun's way, the lit finish, lettering, live and theme pictures, symbols, graphs,
      lists, fan lists, fans, moons, downloads), 40 random themes of each, most of their
      widgets turned a quarter, a half or a whole turn either way, by other angles and by a
      hair off none or off a whole turn: 809 frames to the bit; 120 themes of modules and card
      slots turned: 120 frames to the bit; 90 themes frame after frame (gliding lists, turning
      fans, slots changing cards): 435 frames to the bit, 213 of them on the way; 100 fan
      lists whose fans touch edge to edge, turned: to the bit; 120 themes of lists whose
      letters crowd their bars, gliding and turned, now and then faded: 345 frames to the bit,
      196 of them on the way. Python turned 1,682 pieces a quarter turn in them and 3,668
      smoothly, 33 frosted, 57 with their light, 7 lines of light, 267 fans and 50 lists'
      texts laid apart. The counter-checks put in 37 faults (the point turned the other way,
      its radians rounded otherwise, the box's corners unrounded or its edge floored, a
      quarter turned the other way, smooth turns bilinear or cut to their size, the middle and
      the place rounded otherwise, a frosted piece's box, its light and a line of light, the
      angle by Rust's remainder, a module's parts about their own middles, a text about the
      foot of its line or a larger line, the boxes and a module's box as they were, what lies
      over a widget or a module's frame not turned with it, the turned pieces kept for others;
      a list's fans laid apart on their text, in a card or touching, cut from their corners,
      its text laid apart where its bars are not known, under an opacity, on its bars, with
      the bars' boxes off by the margin or not cut to the room, cells said to have bars), and
      the tests catch 32 of them, 4 once the turns were compared to the bit and touching fans
      and crowded lists were added; the other five change nothing a frame shows: no kind of
      widget has a font size, an alignment and a width together; the turn of a piece never
      changes (the same piece, the same box); Pillow turns a half turn by transposing too; a
      list's text is drawn within its room, so beyond it the text is clear; and Python lays no
      letter of a list on a bar (none of 1,500 crowded lists did)
- [x] pictures that move in a moving frame, and slideshows with them (the renderer's
      _draw_image, _slide, _slide_seconds and _moving; moving.Animation.length): an animated
      picture shows the frame of the moment from its first at its speed, round and round,
      and a moving frame moves on with it; a slideshow with a picture that moves shows its
      pictures in turn by the frame's time, each that moves for whole rounds at its speed, as
      many as take its `seconds` at least (seconds / length - 1e-6, rounded up: 7 s of rounds
      of 0.056 s are 125 of them, not 126), each still one for its seconds, and each from its
      start; a frame back in time goes on to the next. The tests: 50 random themes of animated
      pictures and slideshows with them (stills, animations, patterns of their folder) frame
      after frame, a moment to a minute apart and back in time, in moving frames (241 frames
      to the bit, 184 of them on the way) and in still ones (288 frames to the bit); in them a
      slideshow showed its first picture 279 times, the next 419 times, the next after a
      frame back in time 231 times; four slideshows whose rounds come a hair over a whole
      number of them, at four speeds (16 frames to the bit). The counter-checks put in 10
      faults (the frame not moving on with an animation; a slideshow's turn ending a moment
      later, held back in time, the same picture again, an animation not from its first
      frame, its rounds counted without the tolerance, the speed the other way, its seconds
      alone, the length of a first frame alone; one past the last of fewer pictures), and the
      tests catch 9 of them, 1 once the rounds that come a hair over were added; the other
      only shows where a slideshow's folder is read again (each half minute of the clock) with
      fewer pictures in it than the turn had come to, which these tests do not do (without it
      Rust would index past the end). A video in a slideshow: Rust does not draw videos yet
- [x] downloads whose light runs in a moving frame (download.draw's renderer.moving): a
      download of a size not known, its stretch of light running to and fro in its groove,
      moves the frame on while it runs (not while it stands: a share shown, or paused), each
      frame showing it where it is at the frame's time, as a still frame does. The tests: 80
      random themes of download cards, flat and lit, frame after frame at a frame rate or none,
      the download going on in another state now and then, a part of a second to a minute
      apart and back in time: 424 frames to the bit, 250 of them on the way. The counter-checks
      put in 3 faults (the frame not moving on, moving on while the light stands, the light
      running while paused), and the tests catch them all
- [x] fans of every look, in their own colours, in the square frame and in what the person
      chose for them (render/fan_looks.py's Look, LOOKS, look, angles, rotor and frame;
      render/fan.py's Choices.for_widget, colours, rotor_layers of the drawn looks,
      frame_layers, frame_image and fan_parts' square): the rotors of noctua-a12x25,
      noctua-a14 and libre drawn from their shape at each of the 48 positions over a blade's
      pitch as numpy draws them, every pixel worked out from 3 x 3, each step in single
      precision where numpy works in it and in double where its types take it there (a
      float64 scalar or array among them: np.radians(1), the light), numpy's own exp for
      float32 (np.rs) and the C library's atan2f, powf and pow, as numpy takes them on a
      processor without AVX-512; the square frame of the default look from its Blender
      pictures, averaged down as the rotor's, and a look's own drawn one (Noctua's pads round
      their screw holes, Libre Panel's lit ring) for a drawn look or one in its own colours,
      brighter the faster the fan runs (0.58 to 1 in 8 steps of its ring, its gloss and its
      colours with it, rounded as Python rounds); a look's own colours (Noctua's brown blades
      and beige sticker in a beige frame with brown pads; Libre Panel's white blades with a
      cyan hub ring), else the theme's and a lighter one; each look turning on the beat of its
      sensor for its own blades (9, 7 or 6). A widget's look and paint come first; "auto", and
      the fans of a list, take what the person chose for the sensor, else for all, else
      Noctua's G2 in the theme's colour. The tests: every layer of every look at 14 sizes from
      1 px up, at its positions and at angles of every kind (none, a hair over, a million
      round), every float32 the same to the bit as numpy's (drawn in a process of its own
      that leaves AVX-512 unused, as the moon's face is; with numpy's AVX-512 paths 281 of 375
      cases tried came out apart); the rotor's layers of the drawn looks at 10 sizes; 672 fans of
      every look, paint and frame, every brightness of the square, laid to the bit; the
      frames of 300 random themes of fan widgets of every look, paint and frame (289 drawn,
      all the same to the bit; 216 before); 60 drawn frame after frame as their readings
      change; 60 turning in moving frames (448 frames to the bit, 330 of them on the way; 344
      and 267 before); 200 themes of fan widgets and lists in what a person chose for them
      (186 drawn to the bit), and 40 of them frame after frame in moving frames (223 frames
      to the bit, 169 on the way). Rust draws a look's 48 positions side by side on the
      processor's cores (each alone: the same numbers), and keeps the rotors' layers and the
      frames' as fan.py does, 24 MB of each at most, the least recently used going first. The
      counter-checks put in 36 faults (each look's own colours swapped or one for both, the
      frame's brightness, its gloss not brighter, its colours rounded otherwise, a Blender frame
      for a look's own, the frames, the rotors and the discs kept without their look, the
      square a pixel off or lit by the ring, the drawn looks as the Blender rotor, every own
      frame Libre Panel's, the square drawn as none, nine blades or one beat for every look,
      every widget in the default look or the theme's colours, numbers of the looks a hair off
      (the gloss, the sticker, the round tips, the grooves, the ridge, the pads, the lit ring,
      the corners), the edges of the hub's ring and of the screw holes, the person's choice for
      all or by sensor left out or put first, a widget's "theme" taken for "auto", the lists'
      fans or a widget without the person's choice), and the tests catch them all, the last
      two edges once the least sizes where a pixel lies on them were added: a rotor of 339 px
      and a frame of 510 px (none smaller has one, of 1,024 sizes searched)
- [x] toasts (render/overlays.py's ToastLayer and its built-in card, CardStyle): the messages
      of services as Python decides which shows (one with the key of the one on show replaces
      it in place, the waiting ones of a key collapse to the newest, ranks with the theme's
      queue and without it, 20 waiting at most, the kinds the theme switches off and those a
      theme shows itself: SHOWN_BY, of what its widgets read; while a transition plays they
      wait, one is set aside or hidden by it and starts again after it; the theme's hold time
      and the card's lifetime), and the card as Python draws it over a frame (RGB or RGBA) or
      lays it over a frame the engine composes: its colours of the theme's roles as the palette
      writes them (the card's to its seventh letter, so a colour Pillow then cannot read leaves
      the toast out, as in Python), a payload's colour (a palette name, the cover's, Pillow's
      names, three numbers as int() takes them, else the level's), the icon, the text cut to
      120 letters and to its room with "…" (a text that FreeType cannot lay out leaves the
      toast out), the strips the panel's bezel hides, the anchor, the fade and the slide. A
      plugin's style comes with the plugins (R11): until then a theme that names one gets the
      card, as Python's does where the plugin is not installed. The tests: 160
      layers through scripts of up to 34 steps and two scenarios the scripts seldom meet by
      chance (a text of 121 narrow letters on the 9.2" bar, the card at the same size in a theme
      of other colours): 332 frames with a toast on them compared to the bit, and every step's
      queue, times and state alike. The counter-checks put in 57 faults
      (the supersampling, the fade's and the leave's times, a toast replaced in place rolling in
      anew, the card kept for another theme, the card's unit, pads, stripe, gap, room, edge
      and accent, a payload's colour read otherwise (a bool, a float rounded, a text, two
      numbers), a level's accent, the icon's stroke and place, the text's start, anchor and
      reduction, the margin, the bezel's strips turned or of a square taken for a landscape,
      the slide, the queue's order, its 20, its ranks with and without the queue, the end a
      moment late, no time to leave, a toast set aside to the end, one unseen shown, a hidden
      one keeping what it replaced, the kinds left out, the keys, the roles' names and the
      Libre look's colours, two letters left of a cut text, a picture's need) and the tests
      catch 56, the text's start and the queue's 20 once letters whose ink begins left of them
      and bursts of 25 waiting toasts were added, the 120 letters and the card kept for another
      theme for good once the two scenarios were. The 57th, waiting toasts of a key collapsed without the others dropped, changes nothing: no
      two waiting toasts share a key (one with the key of the one on show replaces it in
      place, and one set aside was alone with its key)
- [x] the toasts' band (render/band.py's BandStyle, after SPUR II's notices): the edge of light
      that ignites from the centre, the band rolling down from it and up into it, the edge
      glowing on, each toast's run as Python keeps it (where in its life it begins of the one
      it replaced: rolling on, standing, rolling back up from the height it reached; the same
      key going on with its bar, another rolling down inside the open band), the theme's
      roll_in and roll_out (0 to 10 s; one float() cannot take: the card, as Python's band
      fails to start), the layout by the frame's shorter side and the bezel, SPUR's raster and
      lit frame (bloom: a quarter's Gaussian blur), the title letter-spaced and haloed, the
      tile with the picture a source published, its badge or the icon, the text in the largest
      size that fits, the bar beside it (rising levels for the volume, segments) or a line
      under it with its knob, gliding to its value on time; Python's caches by bytes where
      what they keep decides what shows (a toast's band kept while its cover changes), and
      Python's errors (round() and floor() of NaN) where they leave the toast out. Over a frame
      the engine composes it draws the frame (it lays no layer: `layered` says so). The tests:
      the toasts' test with the band in four themes of ten, and 120 layers all in the band with
      four scenarios of their own: 797 frames with it to the bit, laid over a frame of layers
      too (its rolling in and out, 51 bands rolling over the one before, 201 bars of the three
      forms, the volume turning in place with its bar gliding and drawn out of turn, titles,
      pictures with the badge on their corner, badges and icons; the scenarios: a band in a
      theme that puts it elsewhere, a change drawn at the very end of its 0.26 s, a band gone at
      its very end as the next arrives, bars on panels just too narrow for them beside the text,
      864 px without a tile and 1000 with one, and on one so flat that its width cuts their
      steps). The counter-checks put in 96 faults (the glow and the times, the shares of the
      rolling, the layout's parts, the bar beside the text or under it and its steps, a run's
      start, end and change, the glide, the edge's light, the line's look, the title, the tile
      and its badge, the bars' forms, the bloom, the tracking, the text cut to fit, the anchor
      kept, the lifetime) and the tests catch 92, the glide with the clock gone back, the
      badge's pad, the bar beside the text and its steps cut by the width once the volume
      turning, pictures with badges and those panels were added, the end of a change, a band
      gone at its end and the anchor kept once the scenarios were. The other 4 change nothing:
      where the band has no height its edge's light is 1 or 0.85, never near the 0.01 that
      leaves the frame as it is; a bar beside the text is 560u wide at least, so its steps are
      cut by its width only where 8u is below 3 px, and then 7u is too; no face of the band is
      smaller than 7 px; and the bloom's strengths are 0.6 and 0.54 alone
- [x] the volume's looks (render/volume_looks.py), the look the notices service chose for
      every screen and named in the message: a bar along the bottom edge, levels as on a mixer
      (the loudest in the warning colour), a ring, and a large number in the middle; the
      speaker or its muted sign, the number, the groove filled to the level with its glow,
      drawn three times larger and made smaller as Pillow up to 12 does it (premultiplied,
      across, then down); fading and sliding in, by the theme's anchor or a look's own place,
      clear of the bezel; changed in place while the volume turns, its level gliding on the
      panel's clock (a preview shows where it ends), and the picture made again only where what
      it shows changes (its number, muted, the level to three places or the levels lit, the
      size, the theme), as Python keeps it; a look's lifetime and its leaving, the looks kept
      through themes of the same style (their glide goes on) and forgotten with their toast.
      Over a frame the engine composes, a look is one more layer. Python makes a picture anew
      only where it changed since the one before, and its tests hold that to the picture made
      whole: here each is made whole, the same pixels. The tests: 100 layers of the volume in
      its looks, half of them on the panel, its level gliding on a clock both sides read the
      same, and seven scenarios (each look by a bezel, coming, turning and muted, and on a
      square frame by it, which Python takes as standing; a look named by a message of another
      kind, which the card draws; numbers too long for their cards; the volume turning in each
      look on the panel, drawn out of turn and late in its glide, then again once gone, and of
      a progress that is no number; a look as the theme changes to one of the same style in
      other colours and another place, and to one of another style while it glides; the same
      volume again once its toast is gone, in a colour of its own, which the key of the
      picture leaves out): 943 frames to the bit, and the looks in the toasts' other tests
      too. The counter-checks put 81 faults into looks.rs and the toast layer's part of it,
      and the tests catch 78 of them (a progress of no number, a picture kept for another
      theme, the level to three places, a new volume's level at once and not gliding on from
      the last one's, squares, other kinds and the forgetting once their scenarios were added);
      the other three change nothing: the levels also ask for a level above nothing, which the
      lowest of them, at 1/32, asks for anyway; a groove's corners a pixel rounder are the
      same, Pillow taking no more of a radius than half of the box's height, the groove's; and
      a glow's reach of a pixel less for each of the blur's three passes is still all of it,
      Pillow's box blur of radius r reaching ceil(r) a pass at most
- [x] the transitions from one screen to the next (render/overlays.py's fade and slide,
      render/transitions.py's tube and entrance, render/handovers.py's wipe, iris, blinds,
      dissolve, the slides from each side, zoom, the line of light, glitch, tiles and shatter;
      a cut is none): each made by its name for a switch, with the parameters a service passed
      (an entrance's title, text, logo and kind; one whose kind is no key does not start), the
      new theme's colours and the app's language, its duration and what a switch and toasts
      do while it plays; each of its frames of the old screen, the new one and how far it has
      gone to the bit, what it keeps kept as Python keeps it (a map or a strip of light for
      the frame's size, the logo or the title in big letters with its halo, the text typed in
      below it, the pieces of each old frame), and where Python's raises (a fade of frames of
      other modes) Rust's fails too. Python's random.Random in lp-py for the squares, the
      faults and the pieces (the Mersenne Twister seeded with an int as CPython seeds it, and
      random(), uniform(), randrange() and getrandbits(), the same numbers). The main loop
      plays them with R5. The tests: 120 themes of other colours, sizes (1x1 to 1920x480,
      either way round) and languages, with up to three switches each (their old frame now
      and then another), at times on their way, at their ends and past them; and six
      scenarios for what those seldom meet: the entrance at each turn of its timeline in every
      kind, on another size and on a panel narrower than its band; every transition at its
      start, its end and where the line of light is almost at its brightest, a shatter whose
      old frame changes; the maps and the strip of light kept for a size: 1375 frames to the
      bit; and random's draws of 132 seeds. The counter-checks put 110 faults into
      transitions.rs and 10 into random.rs: the tests catch 107 and 8 (squares, the early
      light of an entrance's edges, the music's track joined to its text, the room a narrow
      panel leaves and the line almost at its brightest once their scenarios were added; the
      old and new frames of the tests had been one and the same picture, seeds a few apart
      making the same noise, until the seeds were spread). The other five change nothing: a
      map's value at the very edge of its soft band is 255 whether it is taken as inside or
      worked out (the ramp gives 255 there); the text that types in from 1.25 s is none until
      1.3 s; a halo's margin of 50u instead of 60u loses nothing, its light being none that far
      from the picture even of an opaque logo, at every size (measured); the lowest bit of the
      Mersenne Twister's first tempering mask meets the zeros its shift brings in; and of the
      first word of its seeded state only the top bit is ever used (as its authors' note says)
- [x] the alarm's edge on every panel (render/alarm.py): red round the picture for 3 s from
      the moment the notices service's alarm turns on, pulsing (strong at once, softer and
      strong again once a second) or still on a calm panel, in the theme's crit colour or the
      alarm's own, in 12 strengths each made once for a size and forgotten when it is over; the
      frame as it was while it does not show, and Python's error for a colour Pillow cannot
      read. The main loop draws it over each frame with R5. The tests: 150 scripts of the
      alarm going on and off, frames drawn while it shows and after (of sizes from 1x1 to
      1920x480 in RGB, RGBA and L, another size now and then, the clock put back) and whether
      it shows; and a whole pulse drawn every 1/20 s, then the alarm on again while it shows,
      off, and on once it ended: 930 frames with the edge to the bit, 1159 left as they were
      and 90 refused alike, and after every step as many masks kept as Python keeps (forgotten
      63 times). The counter-checks put 25 faults into alarm.rs, and the tests catch all of
      them (the masks kept once the edge is over, which no picture shows, once the test
      counted the masks)
- [x] the panel's own screens (render/bootscreen.py, render/bootlooks.py and the mark of
      branding.py): the boot logo and the standby clip of any panel of the catalog, in
      landscape or upright, at its size and smaller for the app (without the dither there), in
      each of the seven looks: Libre Panel's own (the dark gradient with Lumen's dots, the name
      in two colours, the mark with its glow breathing in the clip, the line of light or its
      dashes with a wave of light running along them; laid out in the room the bezel and the
      quiet ends leave: beside each other on a bar, the name under the mark on a broad panel or
      a bar on end, smaller in a round one's circle), Aurora's curtains with their shimmering
      rays and faint stars, Pulse's heartbeat on a monitor's grid with the light sweeping along
      it, Circuit's traces running into a chip with the mark, Night's stars twinkling over the
      horizon's glow and the milky way, with a crescent moon, Levels' equaliser and Dark's
      faint mark. In bootscreen.py's and bootlooks.py's own numbers: float32 where their arrays
      are, the Python floats made float32 where numpy takes them as weak scalars and the sums
      in doubles where a numpy float64 meets them (np.add.at's and += too); the box blurs'
      running sums in doubles as np.cumsum makes them, a wide blur done at a fraction of the
      size by Pillow's resampling of float pictures, the shapes drawn three times larger and
      reduced, and the pictures of mode F the traces and the cells are told apart by; numpy's
      own float32 exp, sin and cos, the C library's powf, hypotf and the doubles' sin, cos, exp
      and pow; numpy's default_rng for each room's chances (uniforms, integers by Lemire's
      method, pareto, choice) and the dither, once for a size as Python keeps it; Python 3.12's
      compensated sum of floats where it adds up a trace's length (lp-py's). The tests: every
      panel of the catalog in both orientations, its logo in Libre Panel's own look at its size
      and as the app draws it 960 px wide, a picture of its clip at a point of the loop (its
      ends and past them among them), four pictures of the clip of a panel of each size and
      shape, two whole clips, far smaller logos and clips (rooms of 8 px a side among them) and
      a look there is not; each look for every room of the catalog, at the panel's size and as
      the app draws it 960 px wide (a look's choices change from one room to the next: Pulse's
      beats on the bars of 11" and 11.3", the blur of Aurora's stars on the 5.2", 8" and
      12.3", two of its stars on one pixel on the 6.86" on end), and for a bar, a round panel,
      a bar on end and rooms chosen by chance at other scales, its still and pictures of its
      clip, its logo and a clip as the app asks for them; and panels as though their bezels hid
      other strips, of each side another: 753 pictures of Libre Panel's own look and 1200 of
      the others to the bit, and 1340 canvases of the looks to the bit of their floats before
      they are made pictures, as numpy draws them with its AVX-512 paths left unused (with
      them, 2 of 212 logos and pictures of the catalog in Libre Panel's own look come out a
      pixel apart here, by a level); the draws of numpy the looks take besides those of the
      renderer (Pareto's, float32s, integers by Lemire's method: wide ranges, whose draws it
      throws away again, ranges of a power of two and of all 32 bits) against numpy's own. The
      tests deal their cases out to as many processes of Python and of lp-check as there are
      cores. lp-pixels' reduce now adds up a picture of one band a row at a time and works out
      the multiplier once for each count of a block: the same bytes, and a 1920 x 480 panel's
      whole clip of 44 pictures in 1.6 s instead of Python's 3.6 s here; each look on such a
      bar (made, its logo and a picture of its clip) 1.2 to 2.1 times as fast as Python's
      (Night 1.24 s instead of 1.71, Aurora 0.26 instead of 0.56). The counter-checks put 225
      faults into boot.rs, bootlooks.rs, branding.rs and the new parts of random.rs and
      reduce.rs (the looks' colours, sizes, places, times and chances, the blurs and glows,
      the dither, the ends, the layout), and the tests catch all of them but six that change
      no picture: a gradient's step as i times 1/(h - 1) or as i/(h - 1), the same float32 at
      every height to 4096; a bound of the line under the name on a broad panel that the
      name, at least 4.54 times as wide as its size, always passes; Pulse's grid 0.8 or 1.2
      times as wide as its scale, drawn at twice the size, where both are 2 px; the floor of
      Levels' bars in the logo (0.08 or 0.1), which no bar comes down to (0.24 at the
      lowest); the box round what glows from a thousandth up or from a hundredth, the same
      696 canvases of the catalog either way (what lies between is at the edges of what is
      lit, and the box reaches beyond the blur); and a trace's length added up without
      Python's compensation, a unit in the last place apart at most, which the picture of the
      places, of float32s, does not keep. They found four gaps the tests have closed since
      (the rooms each look is drawn for, numpy's integers in a range of a power of two)
- [x] videos (render/moving.py's half for them and the renderer's _draw_video, in
      render/video.rs): a video in an image widget, round and round at its own pace or
      faster or slower, as ffmpeg decodes it into frames of the widget's size, fitted as the
      widget says. ffmpeg's command as Python's to the letter (the fit's filter, the frames a
      second, its speed written as %g writes it, its start to the thousandth, round and round
      or to its end, its first frame alone, its keyframes alone); what `ffmpeg -i` says of a
      video read as moving.video_info's patterns read it (the first of each from the left, \d
      and \s of bytes, its size turned upright where a turn says so, a turn too large to round
      failing as Python's does); its first frame in a preview and until the clip's first
      comes, kept with the theme's pictures; a feed that plays it in a thread of its own, three
      frames ahead, and gives the frame of the moment each frame of the panel is drawn for
      (none twice, none passed over at the video's own rate; on from the frame it has after a
      pause), stops ffmpeg after five seconds unasked, starts it again a while after it failed,
      plays a slideshow's video from its start and keeps the last frame of one played to its
      end; a video's frames laid as they are where it has no transparency (the same pixels);
      no more frames a second than the video's own; a video of no size of its own at its own,
      within the theme's; and the frame that is one video alone, which the panel then takes
      from its file (full_video). Rust draws videos where the app tells its renderer where
      ffmpeg is (Python's finds it as devices/h264.find_ffmpeg does: R6); without one, a frame
      with a video stays Python's. The tests: 600 commands; 30,000 texts like ffmpeg's (its
      own cut and mended here and there, lines of them, texts of the patterns' pieces, turns
      of half a degree); videos of other sizes, shapes, rates and kinds (29.97 and 25.02
      frames a second, turned, with their sound first, of keyframes a second apart, a file of
      no video, one not there) and 54 of their first frames; 36 feeds asked at their moments,
      between them, late, after a pause, back in time, closed and asked again, from their
      start, to their end; 40 themes of video widgets frame after frame as the loop draws them
      and as a preview shows them (fitted, blurred, rounded, of no size of their own, faster
      and slower, at the frames a second the loop asks for, in slideshows that come back to
      their videos, one alone across the theme, see-through, with a glow or a shadow,
      contained over the theme's picture or taking turns with a still picture): to the bit,
      both waiting between frames for each video's frames ahead, so that what a frame shows
      does not depend on how fast ffmpeg is. The counter-checks put 49 faults into video.rs and
      the renderer's videos, and the tests catch all of them but three that change no frame: a
      first frame or a last one cut short (ffmpeg writes its frames whole or none at all), and
      a preview's frames a second held to the video's own (a preview shows the first frame
      whatever the rate). They found eleven gaps the tests have closed since (turns of half a
      degree, a video of 25.02 frames a second, sizes rounded up, the loop's rate 0, blurs
      drawn at a fraction, slideshows that come back to their videos, a video alone
      see-through, with a glow, contained over the theme's picture or taking turns with one)
- [x] pictures as Pillow fails at them (lp-decode's errors as the exceptions Pillow 13 raises,
      the renderer's catches as Python's): each error lp-decode gives carries the class Python
      would raise and its text, as Image.open, load, ImageOps.exif_transpose, n_frames and
      moving.read_animation raise them. What a plugin raises while it opens a file Image.open
      takes as "not this kind" (a SyntaxError, IndexError, TypeError, EOFError, struct.error, a
      size of nothing) and the file is not identified, its name as repr writes it; then the
      decompression bomb's limit, from the header's size where Pillow checks it there (a GIF's
      screen grown to a frame beyond it, a WebP's header); a file ending in the data as
      ImageFile.load says it ("image file is truncated", with the bytes not processed), the
      decoders' own errors after load_end as their status says them (and only then: a GIF's
      palette missing is raised first); PNG's chunks (their type, length and checksum, a tRNS
      of one 0 among 255s and a newline after them), a JPEG's markers and its MP index as
      _getmp reads it (in the byte order of the directory's header, the entries big-endian only
      under "MM\0*"; a NumberOfImages of no whole number, an MPEntry of no bytes, entries cut
      short or of another format read as a plain JPEG), WebP's decoder; EXIF of no TIFF header,
      BigTIFF's, text where Pillow wants bytes (an empty one too), a raw profile's hexadecimal
      as bytes.fromhex refuses it, at its place. Where Python goes on (an OSError reading a
      picture, a ValueError too reading an animation or whether one moves), Rust goes on
      without it, saying what Python says ("image 'x' could not be loaded (...)", a file not
      there as "[Errno 2] No such file or directory: '...'", on Windows the C runtime's errno
      and text for the system's error, as Python's open has them); what Python does not catch
      fails the widget, saying the same (for a name with a NUL what CPython 3.12 says now, "lstat: embedded null character in path",
"stat: ..." on Windows), and the
      background's fails the renderer, as Python's is not made. A file of none of lp-decode's
      kinds Rust still leaves to Python. The tests: 990 pictures broken in every way (cut
      short, bytes changed or put in, EXIF Pillow cannot read, sizes beyond the limit or of
      nothing, MP indexes of odd kinds, files of no picture) along the renderer's three ways of
      reading one, against Pillow: the same class and text each time (1613 read, 552 OSErrors,
      288 files not identified, 84 SyntaxErrors, 84 bombs, 28 TypeErrors, 24 struct.errors, 17
      ValueErrors, 10 IndexErrors), and 68 made for what the random breaks seldom reach (a
      gAMA, cHRM, sRGB or pHYs too short before the data and after it, after data zlib refuses;
      APNGs cut in their acTL, a later fcTL or fdAT; a GIF whose second frame grows the screen
      without a palette, one whose screen grows beyond the bomb's limit before its frame's
      flags; MP indexes of every kind Pillow reads); tRNS chunks with newlines, to the pixel;
      200 themes of them in image widgets, slideshows and as the background, with Python's
      warnings: 169 frames to the bit (83 going on without a picture, 38 with a widget failed),
      6 renderers not made, 25 left to Python for a file of no kind Rust knows; 7 of animations
      Pillow fails at after their first frame, drawn as stills. Of the theme's pictures Rust
      draws 124 frames instead of 105, of the animations 122 instead of 95. The counter-checks
      put 57 faults into the new code, and the tests catch all but five that change nothing
      either shows: a TypeError lp-decode never raises while opening, the place of a first
      character beyond ASCII (the same in characters and in bytes), the texts of a PNG's
      checksums (Image.open makes them "cannot identify image file"), an MPO of one picture (a
      plain JPEG either way). They found twelve gaps the tests have closed since and Rust wrong
      in one place: an MP entry cut short is a plain JPEG to Pillow, not a file of no kind
- [ ] the kinds of picture Pillow opens that lp-decode does not (BMP, TIFF, ICO ...); names
      that are no Unicode (R3b)
- [x] the computer's own zone on Windows (CPython's _PyTime_localtime, local(),
      local_timezone_from_timestamp and datetime_from_timet_and_us there): Rust asks the C
      runtime's localtime_s of each moment, as Python does (the UCRT's _localtime64_s, which
      reads TZ, else the system's zone, by rules of its own), so a naive time's timestamp,
      astimezone() and its offset, astimezone(zone) and fromtimestamp come of the same function;
      where it fails (a moment before 1970 or after 3000) Python raises OSError and Rust has
      none, and a fold is looked for only from a day after 1970 on, as CPython does there.
      The globes, the slideshows by the clock and the clocks of other places Rust draws on
      Windows now. The tests: on Windows, the zone probe's moments (the computer's changes
      found as Python finds them, both folds, the gaps, 1970 and 3000, 16 named zones) with TZ
      unset and 14 TZ values of the C runtime's kind, as Python takes them; the globes' and
      the other places' frames there
- [ ] the named zones on Windows: Python finds them in the tzdata package there, and Rust finds
      them where it is told the package is (LP_TZDATA: the tests tell lp-check where
      Python's is). The Rust app does not bring one yet: without it, no key is a zone there
- [ ] the C library's locale where Python's text follows it: format()'s 'n' and what strftime
      writes of a date in English (i18n.format_date: the names, %p, %c) are the C locale's
      unless something in the process sets another. GTK sets the person's when it starts, and
      on Linux the tray's AppIndicator (PyGObject) may start it in the app's process: to be
      checked on a desktop with PyGObject. lp-py keeps to the C locale for now
- [ ] the renderer's frame, widget by widget (texts, clocks, gauges, rings, bars, lists, graphs
      and their strips, fans, images, videos, icons, weather, globes, moon, sun path, slots,
      cards, notices), effects (glow, lit finish, dither), motion; each theme's frames to the
      bit

### R5 the panels' loop
- [x] `libre-panel`, the program of Rust (rust/app), running: it finds the app's files
      (LIBRE_PANEL_DATA, `share/libre-panel` beside it, the source tree), reads config.toml
      and reads it again when it changes, shows the theme that is due (the screens in turn as
      sequence.py's Rotation follows them, config.toml's, the panel's own) in its version for
      the panel, laid out on the panel's frames and dressed as the person chose (accent, light
      colours, the cards' opacity, the cards, the switches, the fans' looks), takes the
      readings at the theme's pace and keeps their histories, draws each frame and shows it:
      on a panel of the Hongtai family or the MAJESTY found on a serial port, else into the
      PNG file (devices/virtual.py: beside it first, then in its place). At the frame rate
      while something moves, at rest otherwise; the brightness by the night's times; upside
      down where the panel is mounted so; a panel that fails opened again. `run`, `render`
      (one frame), `themes`, `models`, `ports`, `config`. The tests: the program run on a
      settings folder of its own
- [ ] the rest of the main loop: what a service shows (modes, pages, toasts, the alarm's
      frames), transitions between themes, panels beside each other, the engine in the same
      process, videos (ffmpeg), the first panel's look on the others, the sun's times for the
      night

### R6 devices
- [x] the Hongtai / Jungle Leopard family (devices/hongtai.py, lp-devices) on a link of its
      own: its frames, its pictures as JPEGs of the panel's size (the quality going down until
      the firmware takes them), the answer it gives (read with Python's json and int()), the
      opening, the live picture kept from a thread of its own, the brightness, the letting go;
      whole sessions on a panel the tests script sent the same to the byte as the Python driver
      sends them. And the MAJESTY 9.16" (33c3:7792, issue #40), in Rust alone: set up as its
      owner found it on the panel (15 with 00 03, its answer waited for, the backlight at 95),
      its brightness in its pictures as devices/base.py dims them. Its model stays beside the
      catalog until the switch: the catalog is the Python app's, themes are checked against it
- [x] the serial port as pyserial opens it (8N1 at 2 Mbaud, DTR and RTS on, a read waiting a
      second, a write ten) and the ports found with their USB ids as its list_ports finds them
      (Linux's ttys from sysfs, Windows' COM ports from SetupAPI); over a pseudo-terminal every
      byte goes through as it is (XON and XOFF among them: 11 is the live picture) and the
      MAJESTY is opened, set up and shown. lp-panel, to try a panel with for its owner: the
      ports, then a card to tell its way round by, the brightness stepped, pictures as fast as
      it takes them (CI makes it for Windows, with the libjpeg it saves with). Not yet seen on
      a panel: a MAJESTY's owner can try it
- [x] TURZX USB panels, through Libre Panel's engine (lp-devices' engine, turzx, rate and
      usb: devices/engine.py's EngineLink and EngineTransport, devices/turzx.py,
      devices/turzx_video.py, devices/turzx_usb.py, h264.py's RateControl and
      video_state.py's rates). The panel picked among those on the bus as _pick picks it
      (a model, or "auto" beside what the other panels name; its place taken for this
      process) and opened by the engine, told its product id and its place. **Video:** the
      start (110 with the standby clip's name, 111, 112, the brightness, the transparent PNG
      layer, the player's rate, the block size), each frame as the parts of it that changed
      since the one before (compared here, in the engine's blocks) with the time it was
      drawn for, the engine's options as h264.py's engine_options makes them (the profile
      for the model, the key frames, the lead, the graphics card's encoder where this
      engine has it), the bit rate "auto" following the panel into the running stream (a
      step down while it takes less than it is sent, up after a calm minute, a failed step
      held off) and kept in video-state.json for the next run, the stop (123, the power-on
      rate, the last frame left as a PNG), a hung decoder's restart (11, the panel waited
      for to leave the bus and to come back, three in a row at most), what the engine
      counted said at the end. **Single pictures** where no video is shown: a PNG to the
      upper layer (filtered row by row as Pillow's encoder filters, within the megabyte the
      panel takes) or a 4:4:4 JPEG to the one under it, cut to the decoder's width, under a
      layer made transparent once a connection; a panel that stopped answering opened once
      more. The loop's link as app.py's _Link: the brightness set after opening, a panel
      that is gone tried again with growing pauses, a frame too detailed skipped;
      `driver = "auto"` as devices/auto.py: the PNG file while no panel is there, a panel
      looked for every five seconds, one that was in use waited for. The tests: the bit
      rate's control beside RateControl step by step, the choice among panels beside _pick,
      the pictures beside the Python driver's (the JPEG to the byte, the PNG's pixels and
      size), the changed parts covering what differs; and `libre-panel` with the simulated
      panel (tests/fake_panel.py behind its socket) and the engine itself: the video's
      start, pictures, stop and last frame, a slow panel's step down kept for the next run,
      PNG and JPEG stills, a hung decoder restarted and the video started anew
- [ ] TURZX: the engine in the same process (no pipe), frames as their layers (the engine
      composing them), videos and the screen through ffmpeg into the panel's stream
- [ ] Waveshare, udisp, BeadaPanel, NZXT Kraken, the other serial panels, the panel as a
      monitor (Windows' virtual display driver, X11, Wayland)

### R7 sensors
- [ ] Linux: hwmon, /proc, the cards' (amdgpu, NVML), storage, network, fans
- [ ] Windows: processor (with PawnIO for its registers), graphics cards (NVAPI/NVML, ADLX,
      Intel), memory, storage (SMART), network, mainboard chips and fans; HWiNFO, RTSS,
      PresentMon, MangoHud
- [x] the computer's own readings (sensors/psutil_provider.py; rust/app's psutil, system and
      osinfo), without psutil: the processor's load, clock and threads and every core's
      load, the memory and the swap, the system's disk and every drive a user thinks of
      (the system's first; Fedora Atomic's /sysroot), the network's and the disks' rates,
      the address, what was received and sent today, how the PC is connected (netlink.py:
      LAN or Wi-Fi, its speed and signal), the chips' temperatures and fans as psutil reads
      hwmon (their keys numbered as hwmon.py numbers them, the processor's and the card's
      own among them), the battery, the uptime, and, in threads that run only while a theme
      shows them, the programs that use the most processor time and memory and the time a
      connection to up to four hosts takes with the share of tries lost. On Linux from the
      kernel's files; on Windows from its API (GetSystemTimes, NtQuerySystemInformation's
      processors and programs, GlobalMemoryStatusEx, the page file's counter, the drives'
      letters and volumes, each adapter's and each drive's counters, GetBestInterface and
      the WLAN service, GetSystemPowerStatus; not yet run on a Windows PC). Run beside the
      Python source on the same computer: the same keys, units and labels, the same values
      where they stand still; programs, ping and link against a listener
      (tests/test_rust_app.py). Each number's history for the graphs (600), and readings
      made up (`--demo`)
- [x] LibreHardwareMonitor through the sensor service (rust/sensors, lp-sensors): the
      protocol's client side (sensorservice/protocol.py: the framing, the two requests, every
      check of an answer, in its words), the pipe (winpipe.py's connection with overlapped
      I/O and timeouts, the server checked to be the service the Service Control Manager
      names, a stopped service asked to start; not yet run on a Windows PC), the source that
      asks it (lhm_service.py: only the hardware a theme on show reads, growing pauses, told
      after 20 s of silence, 180 s before its first answer), the readings made of the sensors
      (librehardwaremonitor.py's readings_from_sensors: raw keys, the aliases of the first
      processor and the best graphics card, the cores' mean clock, every temperature and fan,
      the hottest memory module, the cable's power monitor, amounts of data as 0.9.6 told
      them, and which sensors each key is made from), the hardware's names (hwnames.py), the
      web server's tree and the provider that chooses (the service where it is installed,
      the web server while it is silent or missing; LibreHardwareMonitor inside the app's
      process is not in Rust: the service is there to spare that). `libre-panel sensors`
      lists the readings in the Python app's lines, `--check-service` asks the service once.
      Compared with Python: 3000 names, 400 computers' sensors, 500 trees, 4000 answers
      with one thing wrong each (tests/test_rust_sensors.py), the program's listing of a
      web server's tree (tests/test_rust_app.py); the provider's ways with a service and a
      web server made up (cargo test)
- [x] Python's regular expressions as far as the app's own patterns go (lp-py's regex):
      characters, sets, `\d \s \w \b`, groups, look ahead, atomic groups, greedy, lazy and
      possessive repeats, with or without re.IGNORECASE, matched by going back as re does
      (an empty round's captures, 3.12's `\B` in an empty text), search, match, fullmatch,
      sub and split: 3000 made-up patterns and the app's own against texts, the same spans,
      texts and refusals
- [x] what to do when Windows' temperatures and power are missing (hardware_hint for the
      command line, which `libre-panel sensors` says; user_hint, the sentence in the
      person's language for the tray and the pages; ring0_steps): every way it may be read
      and fail, with and without readings, in English and German, the same words
- [x] the load source (sensors/load.py, lp-sensors' load): the processor's load from
      GetSystemTimes on Windows and from the kernel's times on Linux as psutil's
      cpu_percent counts them, every core's over the last 0.3 s, every physical core's as
      the mean of its threads (topology.py: Windows' records of the cores, sysfs), and on
      Windows the graphics card's from the counters Task Manager shows (PDH: the card with
      the most dedicated memory in use, its busiest 3D engine); ten times a second in a
      thread that runs while a theme reads one of them, and read on every frame where the
      panel shows video (the hub's readings of every frame, with LibreHardwareMonitor's).
      The numbers compared with Python (300 samples, 300 sets of counters, 60 runs of the
      cores' times, 200 of Windows' records, 40 folders as sysfs has them); the Windows
      calls not yet run on a Windows PC. Linux's graphics card (the kernel's counter, NVML)
      comes with the source `linux`
- [x] the volume (sensors/audio.py, lp-sensors' audio): the default output device's level
      and mute and, while something asks for it, the microphone's; on Linux from wpctl or
      pactl four times a second (the one that works first, a failing one asked less often),
      on Windows from Core Audio with a callback made by hand, so that Windows tells every
      change itself, the default device looked at again every two seconds (compiled for
      Windows, not yet run there); a thread that runs while a theme reads audio.*, read on
      every frame; the microphone muted or turned on again. The tools' answers and the
      readings compared with Python (500 texts, 300 states), the program run against a
      stand-in wpctl
- [x] a game's frames (sensors/presentmon.py, lp-sensors' frames and presentmon): the
      frames counted as Python counts them (the rate, the frame time, the lows of the round,
      the peak, what limits it, the trace; random runs of rows compared), and PresentMon looked
      after as Python looks after it: found by its name in the settings, in `tools`, on the
      PATH or in Intel's folder, with the highest rights only from a safe place (rights.py,
      lp-devices' rights, which the engine's search follows too); started when a theme reads
      game.*, ended a minute after none does, started again slowly while it fails or a game
      runs, in a Windows job that ends it with the program, not started while Windows ends
      the session. The program run against a stand-in PresentMon; the Windows calls (the
      job, the session's release) not yet run on a Windows PC
- [x] the names of the computer's parts (sensors/hardware.py and smbios.py, lp-sensors'
      hardware and smbios): the processor from /proc/cpuinfo or Windows' registry, the
      graphics card from /sys/class/drm (the NVIDIA driver's name, AMD's list, pci.ids) or
      the registry's display adapters, the memory from the SMBIOS table's modules or its
      size; tidied, the user's own under `[hardware]` instead, the memory's speed; always
      on, and its names win over another source's. Compared with Python on 300 cpuinfo
      files, 120 trees of cards, 400 tables, 600 computers made up, and on the computer the
      tests run on; the Windows calls not yet run on a Windows PC
- [x] the Linux source (sensors/linux.py with hwmon.py, rapl.py, gpus.py and nvml.py; lp-sensors'
      linux, hwmon, rapl, gpus, nvml): every chip's temperatures and fans under the keys the
      psutil source gave them (the mainboard's fans as Windows names them), cpu.temp and
      mem.temp, the processor's power from RAPL, amd_energy or a chip, the graphics card from
      its hwmon chip, amdgpu's files, Intel's clock or NVIDIA's library (loaded from the
      driver, each call that fails left out); drives and memory modules read by a thread of
      their own, a card that sleeps left alone, a rescan every ten seconds. The load source's
      gpu.load on Linux from the same. 240 machines laid out in folders read at six times
      after the same changes, each reading compared with Python, NVIDIA's library a stand-in
      on both sides; the program run on a machine laid out in a folder. NVIDIA's real library
      not yet called
- [x] the sun and the moon (sensors/sky.py, lp-sensors' sky): the moon's phase, light, name
      and age, the sun's rise, noon, set, height and daylight for the place of `[weather]`,
      in the computer's local time; always on. 4000 moments of a century at places from pole
      to pole compared with Python in five zones, each value and its type
- [x] a game's frames on Linux (sensors/mangohud.py, lp-sensors' mangohud): the folder
      MangoHud.conf names, the newest log written in the last seconds, its rows read as they
      are written from where the reading began, counted by the same counter as PresentMon's
      (as samples); a thread that runs while a theme reads game.*; always on where Linux is.
      200 configurations, 150 folders and 120 logs written in pieces compared with Python,
      the program run against a log that grows
- [x] a web client (rust/net, lp-net): a file from an http or https address as urllib
      fetches one: the certificates the system trusts, redirects followed, the environment's
      proxy, an answer of 400 and above an error with its status, the first bytes up to a
      limit. Through ureq and rustls (their licences go into the downloads' list of crates
      with R11). Run against a web server of the test's own; an https address by hand
- [x] what is playing (sensors/media.py; lp-sensors' media and media_windows): title,
      artist, album, player, state, position, length and the cover. On Linux from playerctl,
      else from the session's D-Bus with busctl (the one that plays, else one paused); on
      Windows from the system's media controls through WinRT (the `windows` crate), by their
      events: a new session, track or playback looked at at once, a timeline event only for
      a seek, a slow poll as the safety net, the cover read when the track changes (compiled
      for Windows, not yet run there). A thread that runs while a theme reads media.*; read
      on every frame, the position moving on with the frame's time; the cover from its
      bytes, a file, a file:// or a web address made a picture of at most 512 pixels as
      Pillow's thumbnail makes it, handed to the frame when it changes. Always on. 800
      tracks, 1500 lines of playerctl, 1200 answers of busctl and 24 covers compared with
      Python; the program run against stand-ins of both tools. Open: covers in formats
      lp-decode does not read (BMP)
- [x] the player's icon (sensors/appicon.py, lp-sensors' appicon; media.app_icon): on Linux
      the player's desktop entry, its `Icon=` and the PNG of the hicolor theme or a pixmap;
      on Windows the logo a packaged app's manifest names (the file without a plate and
      nearest in size) or the icon of the running program of the player's name, taken from
      its file through GDI (compiled for Windows, not yet run there); a badge of 96 pixels,
      found once per player and again after a minute where there was none. Program names,
      1500 logo names, 200 packages with their manifests, 150 sets of desktop entries and
      icons and the badges compared with Python. XPM pixmaps are not read
- [x] str.capitalize in lp-py (the first character's title case, Unicode 15.0)
- [x] the weather (weather/open_meteo.py, lp-sensors' weather): Open-Meteo's forecast for the
      place in `[weather]`, asked for in the background through lp-net; the present weather,
      the next 24 hours, the week's days in the panel's language and the rain of the next two
      hours, relative to now at each reading. lp-py reads the forecast's times as
      datetime.fromisoformat does (dates, ISO weeks, times with and without colons,
      fractions, offsets). Compared with Python on made-up answers in four languages; the app
      asks a server of the test's own
- [ ] HWiNFO, RTSS, the other sources; Linux's hint of what system-setup brings
- [ ] the history as a ring, demo.py's values; the .NET service goes

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
- [x] TOML as Python's tomllib reads it (lp-py): every value (ints of any size, floats to
      the bit with -nan's sign, dates, times and their offsets), tables, inline tables and
      arrays of tables with what may be said twice, every error in tomllib's words with its
      line and column, the 4300 digits Python's int() takes, and how deep a file may nest
      before Python gives up (its 1000 frames, counted as tomllib's functions call each
      other, from where the caller stands). A file's text as open() reads it: UTF-8 strictly,
      the error in Python's words, `\r\n` and `\r` as one new line. The tests: generated
      texts beside tomllib itself, value by value and message by message, and nesting
      at both sides of the end, from six depths of the caller
- [x] config.toml read (lp-config: config.py's parse_config and load_config): every setting
      of every section with its defaults and its error in config.py's words (the panels
      beside the first, scenes, screens in turn, the cards and their times, the shortcuts as
      hotkeys.py reads them, in the app's language), the errors Python lets through as they
      are (an int too large for a float, int()'s 4300 digits), each further panel's own
      config (for_panel), a file that is missing, a folder, no UTF-8 or no TOML. The tests:
      every setting given every kind of value, some 11 000 texts, and 1500 of several
      settings at once, beside config.py on Linux, Windows and macOS, in English and German
- [x] config.toml's folders (lp-config's dirs: config_dir with LIBRE_PANEL_HOME, a portable
      copy's `data`, the user's own folder on each system; the user's themes, the log), the
      panel's brightness by the time of day (night_now, brightness_now) and the screens in
      turn on the clock (sequence.py's position and Rotation)
- [ ] config.toml written (the commented default, a setting changed in place); the panel a
      config is for
      (theme/defaults.py's panel_model: the configured model, the one a display reports, a
      TURZX panel on the USB bus) and the theme of its normal page
- [ ] community themes as one file (theme/share.py): the zip read as Python's zipfile reads it
      (every entry, name, method and damage) and written as it writes it, every check of an
      import in its words, the files a theme names, what is scrubbed from it

### R10 plugins
- [ ] WebAssembly plugins: services, sensors and pages, an API of their own

### R11 packaging
- [x] a folder to try on a Windows PC, built by CI (ci.yml's `try`, the artifact
      libre-panel-rust-windows): libre-panel.exe, the FreeType 2.14.3 it draws letters with
      (built there, as libjpeg-turbo and libwebp are), the app's themes, fonts and languages
      in `share/libre-panel`, the engine in `tools`, their licences, and how to try it
      (rust/app/try-windows.txt). CI draws two frames from the folder alone
- [ ] Windows: the installer and the zip, signing; Linux: the AppImage; the tools (ffmpeg)

### R12 the switch
- [ ] fixtures recorded from the Python app; the Rust app on main; the Python app goes
