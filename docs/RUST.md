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
| py | what Python does that the app's files and messages depend on: its values (ints of any size, dicts in order, ==), their arithmetic and its errors, repr(), json, str's white space, digits, lower() and upper(), re's matching without case, rounding, the division of ints, float() of a str, sum() of floats, splitlines(), casefold(), format() and str.format, the wide characters of East Asian scripts, the zones zoneinfo finds (Python 3.12, Unicode 15.0: the release's) | — (Python's own rules) |
| pixels | Pillow's operations as Libre Panel uses them, to the bit | Pillow, numpy (in render/) |
| decode | pictures from their files as Pillow decodes them (PNG, GIF, JPEG, WebP) | Image.open, Pillow's decoders |
| text | letters and their layout as Pillow draws them (FreeType, shaping as libraqm) | fonts/, ImageFont |
| i18n | the app's languages: its texts and the fields' names in each, the system's language | i18n.py |
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
      gliding to a new reading and a colour fading to a threshold's are motion
      (render/motion.py): where the renderer animates them, and where a threshold is NaN, Rust
      says it does not draw them yet
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
      it out. Not here yet, and Rust says so: another place's time (a clock's time zone needs
      the computer's own and the other's), digits that roll or fade as they move, the years
      before 1000 (Python's own answer for them differs between its releases), %s (the seconds
      since 1970 in the computer's zone) and, on Windows, %#z and %#Z
- [x] bars (bar widgets) in a still frame: where the reading stands between min and max on
      a linear, square-root or logarithmic scale, in steps of the supersampled grid; drawn
      three times larger and reduced: the track, the filled part in one colour, a gradient
      across the whole track (the other way round for left and up) or the colour of the highest
      threshold the reading is above; segments, the last lit one partly, rising as levels on a
      mixer; a thin mark where a second reading stands, a dot at the bar's head placed in
      quarter pixels. The frames of random themes of bars the same to the bit; with a glow
      round the bar and its dot within a level, as a card's. A bar gliding to a new reading and
      a colour fading to a threshold's are motion: Rust says it does not draw them yet
- [x] ring gauges (gauge widgets) in a still frame: where the reading stands, in steps of the
      supersampled grid along the arc; drawn three times larger and reduced: the track with
      round caps or flat and the ticks across it; the value's arc a mask over the gauge's
      colour, a gradient along the scale (thin wedges round the centre) or a threshold's
      colour. A gauge Python cannot draw (no size, a box the wrong way round) is left out as
      there. The frames of random themes of gauges the same to the bit; with a glow round the
      ring within a level. A ring gliding and a colour fading are motion, and a bar's or a
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
      and reduced. The frames the same to the bit; a sign at work or waiting moves: Rust says
      it does not draw it yet in a moving frame
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
      card's. The facets' rings and graphs and the lit finish's rings, graphs, weather icons,
      symbols and downloads: Rust says it does not draw them yet
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
- [ ] the zones on Windows (Python finds them in the tzdata package there, which the Rust
      app does not bring yet: none is found there for now)
- [ ] the C library's locale where Python's text follows it: format()'s 'n' and what strftime
      writes of a date in English (i18n.format_date: the names, %p, %c) are the C locale's
      unless something in the process sets another. GTK sets the person's when it starts, and
      on Linux the tray's AppIndicator (PyGObject) may start it in the app's process: to be
      checked on a desktop with PyGObject. lp-py keeps to the C locale for now
- [ ] the renderer's frame, widget by widget (texts, clocks, gauges, rings, bars, lists, graphs
      and their strips, fans, images, videos, icons, weather, globes, moon, sun path, slots,
      cards, notices), effects (glow, lit finish, dither), motion, transitions, toasts, start
      screens; each theme's frames to the bit

### R5 the panels' loop
- [ ] the main loop (frames driven by change, pacing), the engine in the same process, panels
      beside each other, game mode and the alarm's frames, videos (ffmpeg)

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
- [ ] TURZX (the engine's), Waveshare, udisp, BeadaPanel, NZXT Kraken, virtual, the other
      serial panels, the panel as a monitor (Windows' virtual display driver, X11, Wayland)

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
