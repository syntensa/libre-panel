//! The band (render/band.py), a toast style after SPUR II's notices: a band across the panel,
//! drawn like one of its sections. A glowing edge ignites from the centre, the band rolls down
//! from it, and the edge glows on a little. The same message again (the volume while it turns)
//! changes in place without rolling in again; another one rolls down inside the open band; a
//! band already leaving rolls back up from where it was. A progress bar glides to its value.
//! Sizes are for a panel 480 px high and scale with the shorter side of the frame.

use std::hash::{Hash, Hasher};
use std::rc::{Rc, Weak};

use lp_pixels::{chops, Draw, Filter, Image, Mode};
use lp_py::Value;

use crate::icons;
use crate::kept::Kept;
use crate::light::{self, ease_in, ease_out, mix, py_max, py_min};
use crate::overlays::{accent_colour, Context, Toast};
use crate::piece::{Failed, Result};
use crate::renderer::Renderer;

const TITLE_FACE: &str = "Barlow-SemiBold";
const TEXT_FACE: &str = "Barlow-Bold";
/// how strongly lit parts shine into their surroundings
const GLOW: f64 = 0.6;
const MB: usize = 1 << 20;

/// Python's round(x) of a float, as an int.
fn round_int(x: f64) -> i64 {
    lp_py::round(x) as i64
}

fn failed(e: lp_pixels::Error) -> Failed {
    Failed(e.to_string())
}

/// size_of: the bytes kept of a picture (Pillow keeps RGB in four bytes a pixel).
fn size_of(image: &Image) -> usize {
    let per = if image.mode() == Mode::L { 1 } else { 4 };
    image.width() * image.height() * per + 128
}

/// One toast in the band (_Run): where in its life it was when it came (the one it continues
/// may have begun earlier), and what it carries over.
#[derive(Clone, Default)]
struct Run {
    /// seconds into its life (roll in, hold, roll out) when it came
    start: f64,
    /// seconds its edge had glowed when it came
    glow: f64,
    /// the progress shown: it glides to the toast's own
    bar: Option<f64>,
    /// its age when the bar last moved
    bar_at: f64,
    /// rolls down over (the toast before, its bar)
    change: Option<(Rc<Toast>, Option<f64>)>,
}

/// Where the band lies in a frame of a size (_Layout), and its parts, in band pixels.
#[derive(Clone)]
struct Layout {
    u: f64,
    x: i64,
    w: i64,
    h: i64,
    y: i64,
    edge: i64,
    thick: i64,
    above: i64,
    pad: i64,
    right: i64,
    tile: (i64, i64, i64),
    key: LayoutKey,
}

type LayoutKey = ((i64, i64), [i64; 4], bool);

impl Layout {
    fn new(size: (i64, i64), hidden: [i64; 4], top: bool) -> Layout {
        let (width, height) = size;
        let [h_top, h_right, h_bottom, h_left] = hidden;
        let u = width.min(height) as f64 / 480.0; // 1 on the 9.2" bar
        let w = 8.max(width - h_left - h_right);
        let h = 16.max(height.min(round_int(184.0 * u)));
        let pad = round_int(23.0 * u);
        Layout {
            u,
            x: h_left,
            w,
            h,
            y: if top { h_top } else { height - h_bottom - h },
            edge: round_int(14.0 * u), // the top of the frame line, where the light runs
            thick: 2.max(round_int(5.0 * u)),
            above: round_int(6.0 * u), // the line's glow above it rolls in with the band
            pad,
            right: w - round_int(26.0 * u),
            tile: (pad, round_int(66.0 * u), round_int(104.0 * u)),
            key: (size, hidden, top),
        }
    }

    /// text_room: (x, width, baseline, font sizes) of the main text; a bar beside it leaves it
    /// less room, a line under it less height.
    fn text_room(&self, tile: bool, bar: Option<&str>) -> (i64, f64, i64, Vec<i64>) {
        let u = self.u;
        let x = self.pad
            + if tile {
                self.tile.2 + round_int(33.0 * u)
            } else {
                0
            };
        let sizes: Vec<i64> = [80.0, 66.0, 54.0, 44.0]
            .iter()
            .map(|s| 8.max(round_int(s * u)))
            .collect();
        match bar {
            Some("line") => (
                x,
                (self.right - x) as f64,
                round_int(146.0 * u),
                sizes[1..].to_vec(),
            ),
            Some(_) => (
                x,
                py_max(200.0 * u, (self.right - x) as f64 - 620.0 * u),
                round_int(160.0 * u),
                sizes,
            ),
            None => (x, (self.right - x) as f64, round_int(160.0 * u), sizes),
        }
    }

    /// bar_beside: whether a bar fits beside the text (a wide bar), else it goes under it.
    fn bar_beside(&self, tile: bool) -> bool {
        let x = self.text_room(tile, None).0;
        (self.right - x) as f64 - 620.0 * self.u >= 200.0 * self.u
    }
}

/// Where a band's bar goes: its form ("levels", "segments" or "line"), from x0 to x1 (and the
/// line's row).
type Bar = (&'static str, i64, i64, i64);

/// What a picture is, to know it again: its mode, size and bytes.
fn print_of(image: &Image) -> u64 {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    (image.mode().name(), image.width(), image.height()).hash(&mut hasher);
    image.as_bytes().hash(&mut hasher);
    hasher.finish()
}

type GroundKey = (
    LayoutKey,
    [i64; 3],
    String,
    Option<String>,
    u64,
    Option<u64>,
    Option<u64>,
);
type ContentKey = (usize, LayoutKey, [i64; 3], u64);
type Content = (Rc<Toast>, u64, Rc<Image>, Option<Bar>);

/// _seconds: an option's seconds, 0 to 10, else the default; None where float() of it raises
/// (an int too large for a float: the style then fails to start).
pub(crate) fn seconds_of(value: Option<&Value>, default: f64) -> Option<f64> {
    let seconds = match value {
        None => return Some(default),
        Some(Value::Float(x)) => *x,
        Some(Value::Bool(b)) => f64::from(u8::from(*b)),
        Some(Value::Int(n)) => lp_py::num::int_to_float(n).ok()?,
        Some(Value::Str(s)) => match lp_py::float_of_str(s) {
            Some(x) => x,
            None => return Some(default),
        },
        Some(_) => return Some(default),
    };
    Some(if seconds >= 0.0 {
        py_min(10.0, seconds)
    } else {
        default
    })
}

/// _share: a progress value as 0 to 1, or None for none (nothing, a bool, what float() refuses,
/// NaN); an int too large for a float fails, as float() raises there.
fn share_of(value: Option<&Value>) -> Result<Option<f64>> {
    let share = match value {
        None | Some(Value::None) | Some(Value::Bool(_)) => return Ok(None),
        Some(Value::Float(x)) => *x,
        Some(Value::Int(n)) => lp_py::num::int_to_float(n)
            .map_err(|_| Failed("int too large to convert to float".into()))?,
        Some(Value::Str(s)) => match lp_py::float_of_str(s) {
            Some(x) => x,
            None => return Ok(None),
        },
        Some(_) => return Ok(None),
    };
    if share.is_nan() {
        return Ok(None);
    }
    Ok(Some(py_min(1.0, py_max(0.0, share))))
}

/// The band (BandStyle).
pub(crate) struct BandStyle {
    pub(crate) anchor: String,
    roll_in: f64,
    roll_out: f64,
    /// the runs of the toasts it showed (a WeakKeyDictionary: none keeps a toast)
    runs: Vec<(Weak<Toast>, Run)>,
    /// (the size, at the top, the context, its layout)
    layout_for: Option<((i64, i64), bool, u64, Layout)>,
    /// the ground with frame, title and tile; the text on it; the bar on that (held by bytes)
    grounds: Kept<GroundKey, (Rc<Image>, bool)>,
    contents: Kept<ContentKey, Content>,
    bars: Kept<(usize, i64), (Rc<Image>, Rc<Image>)>,
}

impl BandStyle {
    /// another message rolls down inside the open band
    const CHANGE_S: f64 = 0.26;
    /// the bar is two thirds of the way after 90 ms, there after 270 ms
    const GLIDE_S: f64 = 0.09;
    const GROUND_BYTES: usize = 5 * MB;
    const CONTENT_BYTES: usize = 5 * MB;
    const BAR_BYTES: usize = 8 * MB;
    /// the edge glows on after the band is down
    const AFTERGLOW_S: f64 = 0.25;
    /// shares of the roll-in: the edge is full width by WIDE; the band rolls from FROM for FOR
    const WIDE: f64 = 0.36;
    const FROM: f64 = 0.22;
    const FOR: f64 = 0.67;

    /// The band with the theme's options (roll_in, roll_out); None where one cannot be read as
    /// a number at all (Python's style then fails to start).
    pub(crate) fn new(anchor: &str, options: &Value) -> Option<BandStyle> {
        let given = |key: &str| match options {
            Value::Dict(d) => d.get(key),
            _ => None,
        };
        Some(BandStyle {
            anchor: anchor.to_string(),
            roll_in: seconds_of(given("roll_in"), 0.45)?,
            roll_out: seconds_of(given("roll_out"), 0.32)?,
            runs: Vec::new(),
            layout_for: None,
            grounds: Kept::new(Self::GROUND_BYTES),
            contents: Kept::new(Self::CONTENT_BYTES),
            bars: Kept::new(Self::BAR_BYTES),
        })
    }

    fn run_at(&self, toast: &Rc<Toast>) -> Option<usize> {
        let wanted = Rc::downgrade(toast);
        self.runs.iter().position(|(w, _)| Weak::ptr_eq(w, &wanted))
    }

    fn known(&self, toast: &Rc<Toast>) -> Option<&Run> {
        self.run_at(toast).map(|i| &self.runs[i].1)
    }

    /// self._runs[toast] = run (the toasts gone go with theirs)
    fn set_run(&mut self, toast: &Rc<Toast>, run: Run) -> usize {
        self.runs.retain(|(w, _)| w.strong_count() > 0);
        if let Some(i) = self.run_at(toast) {
            self.runs[i].1 = run;
            return i;
        }
        self.runs.push((Rc::downgrade(toast), run));
        self.runs.len() - 1
    }

    /// lifetime: how long from its arrival the toast is gone, as it begins where it continues
    /// the one before (`previous` and that one's age).
    pub(crate) fn lifetime(
        &mut self,
        toast: &Rc<Toast>,
        previous: Option<(&Rc<Toast>, f64)>,
    ) -> f64 {
        let run = self.arrival(toast, previous);
        let start = run.start;
        self.set_run(toast, run);
        self.roll_in + or_zero(toast.seconds) + self.roll_out - start
    }

    /// _arrival: how `toast` begins when it replaces `previous` (that toast and its age).
    fn arrival(&self, toast: &Rc<Toast>, previous: Option<(&Rc<Toast>, f64)>) -> Run {
        let Some((before, age)) = previous else {
            return Run::default();
        };
        // (never drawn here: as if it had just come)
        let known = self.known(before).cloned().unwrap_or_default();
        let (s, glow) = (known.start + age, known.glow + age);
        let hold = or_zero(before.seconds);
        if s >= self.roll_in + hold + self.roll_out {
            return Run::default(); // it is gone: roll in afresh
        }
        let height = self.phases(s, glow, hold).2;
        let mut run = if s < self.roll_in {
            Run {
                start: s,
                glow,
                ..Run::default()
            } // still rolling in: roll on
        } else if s < self.roll_in + hold {
            // standing: stay open, the edge does not ignite again
            Run {
                start: self.roll_in,
                glow,
                ..Run::default()
            }
        } else {
            // leaving: roll back up from the height it reached, the edge ignites again
            let back = self.roll_in_time(height);
            Run {
                start: back,
                glow: back,
                ..Run::default()
            }
        };
        if before.key_of() == toast.key_of() {
            // the same thing again: the bar goes on from there
            run.bar = known.bar;
            run.bar_at = known.bar_at - age;
        } else if height > 0.0 {
            // another one: it rolls down inside the open band
            run.change = Some((before.clone(), known.bar));
        }
        run
    }

    /// _phases: (band width, line width, band height, edge light), shares from 0 to 1, `s`
    /// seconds into its life, the edge lit `glow` seconds.
    fn phases(&self, s: f64, glow: f64, hold: f64) -> (f64, f64, f64, f64) {
        if s < self.roll_in {
            let p = s / py_max(1e-3, self.roll_in);
            let wide = ease_out(p / Self::WIDE);
            let height = ease_out((p - Self::FROM) / Self::FOR);
            return (wide, wide, height, self.afterglow(glow));
        }
        if s < self.roll_in + hold {
            return (1.0, 1.0, 1.0, self.afterglow(glow));
        }
        let p = if self.roll_out > 0.0 {
            (s - self.roll_in - hold) / self.roll_out
        } else {
            1.0
        };
        // leaving, it rolls up into the edge, which brightens, then draws in to the centre
        (
            1.0,
            1.0 - ease_in((p - 0.5) / 0.5),
            1.0 - ease_in(p / 0.69),
            0.85 * ease_out(p / 0.5),
        )
    }

    fn afterglow(&self, g: f64) -> f64 {
        let roll_in = py_max(1e-3, self.roll_in);
        if g < roll_in {
            return 1.0 - 0.75 * ease_out((g - 0.44 * roll_in) / (0.67 * roll_in));
        }
        0.25 * (1.0 - ease_out((g - roll_in) / Self::AFTERGLOW_S))
    }

    /// _roll_in_time: when, rolling in, the band reaches `height`: the inverse of its curve.
    fn roll_in_time(&self, height: f64) -> f64 {
        if height <= 0.0 {
            return 0.0;
        }
        let q = 1.0 - (1.0 - py_min(1.0, height)).powf(1.0 / 3.0);
        self.roll_in * (Self::FROM + Self::FOR * q)
    }

    // -- drawing --------------------------------------------------------------------------

    /// draw: the frame (RGB) with the band of `toast` at `age` on it; None where nothing of it
    /// shows (the frame as it is).
    pub(crate) fn draw(
        &mut self,
        frame: &Image,
        toast: &Rc<Toast>,
        age: f64,
        previous: Option<(&Rc<Toast>, f64)>,
        context: &Context,
        renderer: &Renderer,
    ) -> Result<Option<Image>> {
        let at = match self.run_at(toast) {
            Some(i) => i,
            None => {
                // drawn without lifetime() first: as it comes now
                let run = self.arrival(toast, previous);
                self.set_run(toast, run)
            }
        };
        let run = self.runs[at].1.clone();
        let (wide, line, height, glow) =
            self.phases(run.start + age, run.glow + age, or_zero(toast.seconds));
        if height <= 0.0 && (line <= 0.0 || glow <= 0.01) {
            return Ok(None);
        }
        let size = (frame.width() as i64, frame.height() as i64);
        let layout = self.layout(size, context);
        let accent = accent_colour(renderer, &context.palette, toast);
        let mut out = if frame.mode() == Mode::RGB {
            frame.clone()
        } else {
            frame.convert(Mode::RGB).map_err(failed)?
        };
        let middle = layout.w as f64 / 2.0;
        if height > 0.0 {
            let band = self.band(toast, at, age, &layout, accent, context, renderer)?;
            let xa = round_int(middle - layout.w as f64 * wide / 2.0);
            let xb = round_int(middle + layout.w as f64 * wide / 2.0);
            let top = layout.edge - layout.above;
            if xb > xa {
                let rows = layout.above as f64 + (layout.h - layout.edge) as f64 * height;
                let part = band.crop(xa, top, xb, layout.h);
                light::paste_top(&mut out, &part, layout.x + xa, layout.y + top, rows)?;
            }
        }
        let reach = layout.w as f64 * line / 2.0;
        let y = (layout.y + layout.edge) as f64 + layout.thick as f64 / 2.0;
        let x = layout.x as f64 + middle;
        let look = line_look(layout.u, accent);
        light::add_line(&mut out, (x - reach, x + reach, y), glow, &look)?;
        Ok(Some(out))
    }

    /// _layout: the layout for a frame of `size`, clear of what the bezel hides.
    fn layout(&mut self, size: (i64, i64), context: &Context) -> Layout {
        let top = self.anchor.starts_with("top");
        if let Some((s, t, id, layout)) = &self.layout_for {
            if *s == size && *t == top && *id == context.id {
                return layout.clone();
            }
        }
        let orientation = if size.0 > size.1 {
            "landscape"
        } else {
            "portrait"
        };
        let hidden = context
            .model
            .map_or([0; 4], |model| model.hidden_edges(orientation));
        let layout = Layout::new(size, hidden, top);
        self.layout_for = Some((size, top, context.id, layout.clone()));
        layout
    }

    /// _band: the band of `toast` now: its content and bar, and while it takes over, rolling
    /// down over the one before.
    #[allow(clippy::too_many_arguments)]
    fn band(
        &mut self,
        toast: &Rc<Toast>,
        at: usize,
        age: f64,
        layout: &Layout,
        accent: [i64; 3],
        context: &Context,
        renderer: &Renderer,
    ) -> Result<Rc<Image>> {
        let value = self.bar_now(toast, at, age)?;
        let band = self.with_bar(toast, layout, accent, value, context, renderer)?;
        let Some((before, bar)) = self.runs[at].1.change.clone() else {
            return Ok(band);
        };
        let p = age / Self::CHANGE_S;
        if !(0.0..1.0).contains(&p) {
            self.runs[at].1.change = None;
            return Ok(band);
        }
        let before_accent = accent_colour(renderer, &context.palette, &before);
        let old = self.with_bar(&before, layout, before_accent, bar, context, renderer)?;
        let mut old = Image::clone(&old);
        let rows = (layout.h - layout.edge) as f64 * ease_out(p);
        let part = band.crop(0, layout.edge, layout.w, layout.h);
        light::paste_top(&mut old, &part, 0, layout.edge, rows)?;
        let row = (0.0, layout.w as f64, layout.edge as f64 + rows);
        light::add_line(
            &mut old,
            row,
            0.85 * (1.0 - p),
            &line_look(layout.u, accent),
        )?;
        Ok(Rc::new(old))
    }

    /// _bar: the progress shown at `age`: it glides to the toast's own, on time (not by
    /// frames), and starts where the one it continues stood.
    fn bar_now(&mut self, toast: &Toast, at: usize, age: f64) -> Result<Option<f64>> {
        let Some(target) = share_of(toast.payload.get("progress"))? else {
            return Ok(None);
        };
        let run = &mut self.runs[at].1;
        run.bar = Some(match run.bar {
            None => target,
            Some(bar) => {
                let dt = py_max(0.0, age - run.bar_at);
                bar + (target - bar) * (1.0 - (-dt / Self::GLIDE_S).exp())
            }
        });
        run.bar_at = age;
        Ok(run.bar)
    }

    // -- the band's pictures ----------------------------------------------------------------

    /// _picture: a picture a payload names (the key of one a source published), or none.
    fn picture<'r>(value: Option<&Value>, renderer: &'r Renderer) -> Option<&'r Image> {
        match value {
            Some(Value::Str(key)) if !key.is_empty() => renderer.image(key),
            _ => None,
        }
    }

    /// _ground: what messages with the same title and pictures share: the band's ground and
    /// frame, the title, the tile; and whether there is a tile.
    fn ground(
        &mut self,
        toast: &Toast,
        layout: &Layout,
        accent: [i64; 3],
        context: &Context,
        renderer: &Renderer,
    ) -> Result<(Rc<Image>, bool)> {
        let payload = &toast.payload;
        let image = Self::picture(payload.get("image"), renderer);
        let badge = Self::picture(payload.get("badge"), renderer);
        let icon = toast
            .icon
            .clone()
            .filter(|name| lp_theme::tables::ICON_NAMES.contains(&name.as_str()));
        let label = match payload.get("label") {
            Some(v) if v.truthy() => lp_py::upper(&v.str()),
            _ => String::new(),
        };
        let key = (
            layout.key,
            accent,
            label.clone(),
            icon.clone(),
            context.id,
            image.map(print_of),
            badge.map(print_of),
        );
        if let Some(kept) = self.grounds.get(&key) {
            return Ok(kept);
        }
        let (u, w, h) = (layout.u, layout.w, layout.h);
        let ground = light::role_colour(&context.palette, "ground");
        let text = light::role_colour(&context.palette, "text");
        let mut band = Image::new(Mode::RGB, w as usize, h as usize, &rgb(ground));
        // SPUR's faint raster
        let (grid, step) = (mix(text, ground, 0.045), 6.max(round_int(40.0 * u)));
        {
            let mut draw = Draw::new(&mut band);
            for gx in (step / 2..w).step_by(step as usize) {
                let line = [(gx as f64, 0.0), (gx as f64, h as f64)];
                draw.line(&line, Some(ink(grid)), 1, false)
                    .map_err(failed)?;
            }
            for gy in (step / 2..h).step_by(step as usize) {
                let line = [(0.0, gy as f64), (w as f64, gy as f64)];
                draw.line(&line, Some(ink(grid)), 1, false)
                    .map_err(failed)?;
            }
        }
        // the frame: across the top and down both sides, lit in the accent
        let (e, t) = (layout.edge, layout.thick);
        let (x0, x1) = (round_int(10.0 * u), w - 1 - round_int(10.0 * u));
        let mut lit = Image::blank(Mode::RGB, w as usize, h as usize);
        for (bx0, by0, bx1, by1) in [
            (x0, e, x1, e + t - 1),
            (x0, e, x0 + t - 1, h - 1),
            (x1 - t + 1, e, x1, h - 1),
        ] {
            let bounds = (bx0 as f64, by0 as f64, bx1 as f64, by1 as f64);
            let dim = ink(mix(accent, ground, 0.73));
            Draw::new(&mut band)
                .rectangle(bounds, Some(dim), None, 1)
                .map_err(failed)?;
            Draw::new(&mut lit)
                .rectangle(bounds, Some(ink(accent)), None, 1)
                .map_err(failed)?;
        }
        let glow = light::bloom(&lit, 2.0 * u, GLOW, 4)?;
        let mut band = chops::add(&band, &glow, 1.0, 0).map_err(failed)?;
        if !label.is_empty() {
            Self::title(&mut band, &label, layout, accent, ground, renderer)?;
        }
        let tile = Self::tile(
            &mut band,
            image,
            badge,
            icon.as_deref(),
            layout,
            accent,
            ground,
            context,
        )?;
        let made = (Rc::new(band), tile);
        let bytes = size_of(&made.0);
        self.grounds.put(key, made.clone(), bytes);
        Ok(made)
    }

    fn title(
        band: &mut Image,
        label: &str,
        layout: &Layout,
        accent: [i64; 3],
        ground: [i64; 3],
        renderer: &Renderer,
    ) -> Result<()> {
        let u = layout.u;
        let font = light::face(&renderer.env, TITLE_FACE, 7.max(round_int(26.0 * u)))?;
        let track = 3.0 * u;
        let (x, base) = (layout.pad, round_int(45.0 * u));
        let room = (layout.right - x) as f64;
        let mut label: String = label.to_string();
        if light::tracked_width(&font, &label, track)? > room {
            let mut letters: Vec<char> = label.chars().collect();
            let joined = |letters: &[char]| letters.iter().collect::<String>();
            while letters.len() > 1
                && light::tracked_width(&font, &(joined(&letters) + "…"), track)? > room
            {
                letters.pop();
            }
            label = joined(&letters)
                .trim_end_matches(lp_py::isspace)
                .to_string()
                + "…";
        }
        let at = (x as f64, base as f64);
        let mut halo = Image::blank(Mode::L, band.width(), band.height());
        light::draw_tracked(&mut halo, at, &label, &font, [255, 0, 0, 0], track)?;
        let halo = light::bloom(&halo, 4.0 * u, 0.9 * GLOW, 2)?;
        let whole = (0, 0, band.width() as i64, band.height() as i64);
        band.paste_colour(&rgb(accent), whole, Some(&halo))
            .map_err(failed)?;
        light::draw_tracked(band, at, &label, &font, ink(accent), track)?;
        let width = light::tracked_width(&font, &label, track)?;
        let rule = 1.max(round_int(2.0 * u));
        let y = base + round_int(6.0 * u);
        let bounds = (
            x as f64,
            y as f64,
            (x + round_int(width + 6.0 * u)) as f64,
            (y + rule - 1) as f64,
        );
        Draw::new(band)
            .rectangle(bounds, Some(ink(mix(accent, ground, 0.3))), None, 1)
            .map_err(failed)
    }

    /// _tile: a picture (with the badge on its corner) or the badge or icon on a tile; whether
    /// there is one.
    #[allow(clippy::too_many_arguments)]
    fn tile(
        band: &mut Image,
        image: Option<&Image>,
        badge: Option<&Image>,
        icon: Option<&str>,
        layout: &Layout,
        accent: [i64; 3],
        ground: [i64; 3],
        context: &Context,
    ) -> Result<bool> {
        let u = layout.u;
        let (x, y, size) = layout.tile;
        let (rim, deep) = (mix(accent, ground, 0.8), mix(ground, [0, 0, 0], 0.55));
        let line = 1.max(round_int(2.0 * u)) as i32;
        if let Some(image) = image {
            let side = size.max(0) as usize;
            let picture = image
                .convert(Mode::RGB)
                .and_then(|p| p.fit((side, side), Filter::Lanczos, 0.0, (0.5, 0.5)))
                .map_err(failed)?;
            band.paste(&picture, x, y, None).map_err(failed)?;
            let bounds = (
                (x - 1) as f64,
                (y - 1) as f64,
                (x + size) as f64,
                (y + size) as f64,
            );
            Draw::new(band)
                .rectangle(bounds, None, Some(ink(rim)), line)
                .map_err(failed)?;
            if let Some(badge) = badge {
                // the app's icon hangs over the lower corner
                let side = round_int(40.0 * u);
                let bx = x + size - side + round_int(10.0 * u);
                let by = y + size - side + round_int(6.0 * u);
                let rule = light::role_colour(&context.palette, "rule");
                let pad = round_int(4.0 * u);
                let bounds = (
                    (bx - pad) as f64,
                    (by - pad) as f64,
                    (bx + side + pad - 1) as f64,
                    (by + side + pad - 1) as f64,
                );
                Draw::new(band)
                    .rectangle(bounds, Some(ink(deep)), Some(ink(rule)), 1)
                    .map_err(failed)?;
                paste_rgba(band, badge, (bx, by), side)?;
            }
            return Ok(true);
        }
        if badge.is_none() && icon.is_none() {
            return Ok(false);
        }
        let bounds = (
            x as f64,
            y as f64,
            (x + size - 1) as f64,
            (y + size - 1) as f64,
        );
        Draw::new(band)
            .rectangle(bounds, Some(ink(deep)), Some(ink(rim)), line)
            .map_err(failed)?;
        if let Some(badge) = badge {
            let at = (x + round_int(16.0 * u), y + round_int(16.0 * u));
            paste_rgba(band, badge, at, round_int(72.0 * u))?;
        } else if let Some(icon) = icon {
            let side = round_int(64.0 * u);
            let mask = icons::draw_icon(icon, side, ink(accent), 2.0)?
                .getchannel(3)
                .map_err(failed)?;
            let (ix, iy) = (x + round_int(20.0 * u), y + round_int(20.0 * u));
            let bounds = (ix, iy, ix + mask.width() as i64, iy + mask.height() as i64);
            band.paste_colour(&rgb(accent), bounds, Some(&mask))
                .map_err(failed)?;
        }
        Ok(true)
    }

    /// _content: the band of `toast` without its bar's value, and where the bar goes.
    fn content(
        &mut self,
        toast: &Rc<Toast>,
        layout: &Layout,
        accent: [i64; 3],
        context: &Context,
        renderer: &Renderer,
    ) -> Result<(Rc<Image>, Option<Bar>)> {
        let key = (Rc::as_ptr(toast) as usize, layout.key, accent, context.id);
        let id = context.id;
        let kept = self
            .contents
            .get_if(&key, |(t, c, _, _)| Rc::ptr_eq(t, toast) && *c == id);
        if let Some((_, _, band, bar)) = kept {
            return Ok((band, bar));
        }
        let (ground_layer, tile) = self.ground(toast, layout, accent, context, renderer)?;
        let mut band = Image::clone(&ground_layer);
        let u = layout.u;
        let progress = share_of(toast.payload.get("progress"))?;
        let form = progress.map(|_| {
            let beside = layout.bar_beside(tile) && toast.kind != "music";
            match (beside, toast.kind.as_str()) {
                (false, _) => "line",
                (true, "volume") => "levels",
                (true, _) => "segments",
            }
        });
        let (x, width, base, sizes) = layout.text_room(tile, form);
        let (text, font) =
            light::fitted_text(&renderer.env, &toast.text, TEXT_FACE, &sizes, width)?;
        let colour = light::role_colour(&context.palette, "text");
        let written = lp_text::text::Text::new(&text, font.as_ref(), "RGB");
        lp_text::text::draw(
            &mut band,
            (x as f64, base as f64),
            &written,
            ink(colour),
            None,
            Some("ls"),
            "left",
        )
        .map_err(crate::text::failed)?;
        let bar = match form {
            Some("line") => Some(("line", x, round_int(170.0 * u), layout.right)),
            Some(form) => {
                let end = x as f64 + light::length(&font, &text)?;
                let start = py_min(
                    py_max(end + 60.0 * u, x as f64 + 540.0 * u),
                    layout.right as f64 - 560.0 * u,
                );
                Some((form, round_int(start), 0, layout.right))
            }
            None => None,
        };
        let band = Rc::new(band);
        let bytes = size_of(&band);
        let made = (toast.clone(), context.id, band.clone(), bar);
        self.contents.put(key, made, bytes);
        Ok((band, bar))
    }

    /// _with_bar: the band of `toast` with its bar at `value` (none: without).
    fn with_bar(
        &mut self,
        toast: &Rc<Toast>,
        layout: &Layout,
        accent: [i64; 3],
        value: Option<f64>,
        context: &Context,
        renderer: &Renderer,
    ) -> Result<Rc<Image>> {
        let (band, bar) = self.content(toast, layout, accent, context, renderer)?;
        let (Some((form, x0, y0, x1)), Some(value)) = (bar, value) else {
            return Ok(band);
        };
        let mut steps = match form {
            "levels" => 36,
            "segments" => 50,
            _ => 0,
        };
        let state = if steps > 0 {
            let fit = ((x1 - x0) as f64 / py_max(3.0, 8.0 * layout.u)).trunc() as i64;
            steps = 4.max(steps.min(fit));
            round_int(value * steps as f64)
        } else {
            round_int(x0 as f64 + (x1 - x0) as f64 * value)
        };
        let key = (Rc::as_ptr(&band) as usize, state);
        if let Some((_, out)) = self.bars.get_if(&key, |(b, _)| Rc::ptr_eq(b, &band)) {
            return Ok(out);
        }
        let mut out = Image::clone(&band);
        Self::draw_bar(
            &mut out,
            (form, x0, y0, x1),
            steps,
            state,
            layout,
            accent,
            context,
        )?;
        let out = Rc::new(out);
        let bytes = size_of(&out);
        self.bars.put(key, (band, out.clone()), bytes);
        Ok(out)
    }

    /// _draw_bar: lit parts get a glow under them, then everything is drawn sharp on top (glow
    /// added over a colour would wash it out).
    fn draw_bar(
        band: &mut Image,
        (form, x0, y0, x1): Bar,
        steps: i64,
        state: i64,
        layout: &Layout,
        accent: [i64; 3],
        context: &Context,
    ) -> Result<()> {
        let u = layout.u;
        let track = light::role_colour(&context.palette, "track");
        let rule = light::role_colour(&context.palette, "rule");
        let mut empty: Vec<[f64; 4]> = Vec::new();
        let mut full: Vec<[f64; 4]> = Vec::new();
        let (x0f, x1f) = (x0 as f64, x1 as f64);
        let zone = match form {
            "levels" => {
                // rising bars, as on a mixer
                let (bottom, step) = (round_int(168.0 * u), (x1f - x0f) / steps as f64);
                let gap = 1.max(round_int(9.0 * u * step / ((x1f - x0f) / 36.0)));
                for i in 0..steps {
                    let tall = (22.0 + 82.0 * i as f64 / 1.max(steps - 1) as f64) * u;
                    let left = x0f + i as f64 * step;
                    let bounds = [
                        left,
                        bottom as f64 - tall,
                        left + step - gap as f64,
                        bottom as f64,
                    ];
                    if i < state {
                        full.push(bounds)
                    } else {
                        empty.push(bounds)
                    }
                }
                (
                    x0 - round_int(16.0 * u),
                    bottom - round_int(124.0 * u),
                    x1 + round_int(16.0 * u),
                    bottom + round_int(14.0 * u),
                )
            }
            "segments" => {
                let (gap, y, tall) = (
                    1.max(round_int(5.0 * u)),
                    round_int(104.0 * u),
                    round_int(34.0 * u),
                );
                let width = (x1f - x0f - (gap * (steps - 1)) as f64) / steps as f64;
                for i in 0..steps {
                    let left = x0f + i as f64 * (width + gap as f64);
                    let bounds = [left, y as f64, left + width, (y + tall) as f64];
                    if i < state {
                        full.push(bounds)
                    } else {
                        empty.push(bounds)
                    }
                }
                (
                    x0 - round_int(16.0 * u),
                    y - round_int(16.0 * u),
                    x1 + round_int(16.0 * u),
                    y + tall + round_int(16.0 * u),
                )
            }
            _ => {
                // a thin line under the text, with a knob at its end
                let thick = 2.max(round_int(4.0 * u));
                empty.push([x0f, y0 as f64, x1f, (y0 + thick - 1) as f64]);
                if state > x0 {
                    full.push([x0f, y0 as f64, state as f64, (y0 + thick - 1) as f64]);
                }
                (
                    x0 - round_int(12.0 * u),
                    y0 - round_int(14.0 * u),
                    x1 + round_int(12.0 * u),
                    y0 + round_int(18.0 * u),
                )
            }
        };
        let (bw, bh) = (band.width() as i64, band.height() as i64);
        let zone = (zone.0.max(0), zone.1.max(0), bw.min(zone.2), bh.min(zone.3));
        if !full.is_empty() && zone.2 > zone.0 && zone.3 > zone.1 {
            let (zw, zh) = ((zone.2 - zone.0) as usize, (zone.3 - zone.1) as usize);
            let mut lit = Image::blank(Mode::RGB, zw, zh);
            {
                let mut draw = Draw::new(&mut lit);
                let (zx, zy) = (zone.0 as f64, zone.1 as f64);
                for b in &full {
                    let bounds = (b[0] - zx, b[1] - zy, b[2] - zx, b[3] - zy);
                    draw.rectangle(bounds, Some(ink(accent)), None, 1)
                        .map_err(failed)?;
                }
            }
            let glow = light::bloom(&lit, 2.5 * u, GLOW, 4)?;
            let under = band.crop(zone.0, zone.1, zone.2, zone.3);
            let sum = chops::add(&under, &glow, 1.0, 0).map_err(failed)?;
            band.paste(&sum, zone.0, zone.1, None).map_err(failed)?;
        }
        let outline = (form != "line").then(|| ink(rule));
        {
            let mut draw = Draw::new(band);
            for b in &empty {
                draw.rectangle((b[0], b[1], b[2], b[3]), Some(ink(track)), outline, 1)
                    .map_err(failed)?;
            }
            for b in &full {
                draw.rectangle((b[0], b[1], b[2], b[3]), Some(ink(accent)), None, 1)
                    .map_err(failed)?;
            }
        }
        if form == "line" {
            let text = light::role_colour(&context.palette, "text");
            let knob = knob(3.max(round_int(8.5 * u)), text, accent, u)?;
            let (kw, kh) = (knob.width() as f64, knob.height() as f64);
            let kx = round_int(x0.max(state) as f64 - kw / 2.0);
            let ky = round_int(y0 as f64 + 2.0 * u - kh / 2.0);
            band.paste(&knob, kx, ky, Some(&knob)).map_err(failed)?;
        }
        Ok(())
    }
}

/// `toast.seconds or 0.0`
fn or_zero(seconds: Option<f64>) -> f64 {
    match seconds {
        Some(s) if s != 0.0 => s,
        _ => 0.0,
    }
}

/// The colour as a band's ink.
fn ink(colour: [i64; 3]) -> [u8; 4] {
    [colour[0], colour[1], colour[2], 255].map(|v| v.clamp(0, 255) as u8)
}

fn rgb(colour: [i64; 3]) -> [u8; 3] {
    colour.map(|v| v.clamp(0, 255) as u8)
}

/// The band's edge of light: add_line(..., unit, 40 * unit, 7.0, 0.8).
fn line_look(u: f64, accent: [i64; 3]) -> light::Look {
    light::Look {
        unit: u,
        soft: 40.0 * u,
        halo: 7.0,
        share: 0.8,
        colour: accent,
        white: light::WHITE,
    }
}

/// _paste_rgba: `picture` in RGBA, `side` px square, through its alpha onto `band`.
fn paste_rgba(band: &mut Image, picture: &Image, xy: (i64, i64), side: i64) -> Result<()> {
    let side = side.max(0) as usize;
    let picture = picture
        .convert(Mode::RGBA)
        .and_then(|p| p.resize(side, side, Filter::Lanczos, None, None))
        .map_err(failed)?;
    band.paste(&picture, xy.0, xy.1, Some(&picture))
        .map_err(failed)
}

/// _knob: a round knob, smooth at the edge (drawn four times larger).
fn knob(radius: i64, fill: [i64; 3], ring: [i64; 3], u: f64) -> Result<Image> {
    let s = 4;
    let size = 2 * radius + 2;
    let mut big = Image::blank(Mode::RGBA, (size * s) as usize, (size * s) as usize);
    let bounds = (
        s as f64,
        s as f64,
        ((size - 1) * s) as f64,
        ((size - 1) * s) as f64,
    );
    let width = 1.max(round_int(2.0 * u * s as f64)) as i32;
    Draw::new(&mut big)
        .ellipse(bounds, Some(ink(fill)), Some(ink(ring)), width)
        .map_err(failed)?;
    big.resize(size as usize, size as usize, Filter::Lanczos, None, None)
        .map_err(failed)
}
