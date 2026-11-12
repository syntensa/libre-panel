//! Graphs in video mode (render/renderer.py's _timeline and its strips). Every frame reaches
//! the panel, so a graph draws the gliding value of its sensor 25 times a second (the motion's
//! history), and its newest point is the value the numbers show now. Drawing that every frame
//! costs too much: the curve goes onto a strip, and each frame shows a window into it, in
//! half-pixel steps. Until the next reading the glide is known, so a strip reaches that far
//! ahead; a reading that comes before the next strip is drawn over the end of the window until
//! it is in.

use std::rc::Rc;

use lp_pixels::{chops, Filter, Image, Mode};
use lp_py::{Dict, Value};

use crate::effects;
use crate::graph::{self, Range};
use crate::motion::{self, Course, History, RATE};
use crate::piece::{Piece, Result};
use crate::renderer::Renderer;
use crate::shapes;
use crate::snapshot::Snapshot;
use crate::widget;

/// drawn twice as large: half-pixel steps, as SPUR II's curves
pub(crate) const STRIP_SCALE: i64 = 2;
/// a strip reaches this far past the next reading
const STRIP_AHEAD_S: f64 = 0.5;
/// a graph asks for a new strip at most this often ...
const STRIP_EVERY_S: f64 = 0.25;
/// ... and this often while its own range glides (small steps)
const STRIP_GLIDE_S: f64 = 0.1;
/// a slow graph's steps are cut into at most this many
const STRIP_PARTS: i64 = 8;
/// a strip's left edge lies on a grid of this many pixels (at its scale, times it)
const STRIP_GRID: i64 = 4;
/// how long a graph that steps (graph_steps) glides to a new reading
const STEP_S: f64 = 0.35;
/// how much later than its time a fine course is shown, beyond the time to the next reading
const TRACE_MARGIN_S: f64 = 0.35;
/// how far behind a fine course's last point lies (sensors/base.py's TRACE_LAG_S)
const TRACE_LAG_S: f64 = 0.25;

/// A graph's curve drawn ahead (_Strip): one image per half-pixel step, glow and all, `pad`
/// pixels larger on every side.
pub(crate) struct Strip {
    /// what tells this strip from the others (Python's id(strip))
    id: u64,
    /// the sensor's spring pieces it knew (Spring.made) ...
    made: u64,
    /// ... and those pieces: what it drew ahead with
    course: Course,
    /// when its values were taken
    taken: f64,
    /// the curve is right until here, if no reading comes in between
    until: f64,
    /// pixels between two history points, at the strip's scale
    per_point: f64,
    /// every how many history points it draws
    every: i64,
    /// its left edge: point k sits at k * per_point - origin
    origin: i64,
    /// in pixels, without the pad
    width: i64,
    /// the value range it was drawn with
    bounds: Range,
    /// where its fill's fade starts
    top: i64,
    pad: i64,
    /// per half-pixel step its line on its glow (_strip_phases' steps) ...
    steps: Vec<Rc<Image>>,
    /// ... each grown from the one before's where it was (render/scene.py's grew) ...
    growth: Vec<Option<crate::piece::Growth>>,
    /// ... and premultiplied, what the parts of a step are blended of (none for one part).
    /// _strip_phases blends every part of the whole strip; a window blends only its own
    /// pixels, which are the same: blending, premultiplying and cutting go pixel by pixel.
    weighed: Vec<Image>,
    parts: i64,
    /// the glow alone, for beside the window
    halo: Option<Image>,
    /// drawn while the graph's own range glided: the next comes soon
    gliding: bool,
    /// the values it was drawn through, from point `first` (the next is made of it where they
    /// stay the same: grown_strip)
    values: Vec<Option<f64>>,
    first: i64,
}

impl Strip {
    /// Whether a window of `w` pixels from `left` (at scale `s`) lies on it.
    fn covers(&self, left: i64, w: i64, s: i64) -> bool {
        let offset = left - self.origin;
        offset >= 0 && offset.div_euclid(s) + w <= self.width
    }

    /// How many images _strip_phases makes of it: per step, `parts`.
    fn phase_count(&self) -> i64 {
        self.steps.len() as i64 * self.parts.max(1)
    }

    /// Phase `k` (a step, and a part of one) from `column`, `w` x `h` pixels: as cut from
    /// _strip_phases' image `k`.
    fn phase_window(&self, k: i64, column: i64, w: i64, h: i64) -> Result<Image> {
        let parts = self.parts.max(1);
        let (step, part) = (k.div_euclid(parts) as usize, k.rem_euclid(parts));
        if part == 0 || self.weighed.is_empty() {
            return Ok(self.steps[step].crop(column, 0, column + w, h));
        }
        let mixed = self.weighed[step].crop(column, 0, column + w, h);
        let after = match self.weighed.get(step + 1) {
            Some(next) => next.crop(column, 0, column + w, h),
            // after the last step comes the first, a pixel on
            None => self.weighed[0].crop(column + 1, 0, column + 1 + w, h),
        };
        let blended = chops::blend(&mixed, &after, part as f32 / parts as f32)?;
        Ok(blended.convert(Mode::RGBA)?)
    }
}

/// A graph's fine course: as its text, and as numbers.
pub(crate) type Fine = (String, Vec<Option<f64>>);

/// A graph's end in video mode: the graph as it draws, the value at its right edge and the
/// range it is shown in.
pub(crate) type End = (Dict, f64, (f64, f64));

/// What a strip is drawn as: its steps, them premultiplied, its glow alone, its pad, origin,
/// width and fade.
type Drawn = (Vec<Image>, Vec<Image>, Option<Image>, i64, i64, i64, i64);

/// A graph's strip on show (_Strips).
pub(crate) struct Strips {
    per_point: f64,
    /// the sensor whose course it draws (a graph's value, or its trace)
    key: String,
    ready: Option<Rc<Strip>>,
    asked: f64,
    /// the frame it was last on show in
    drawn: f64,
}

impl Strips {
    fn new(per_point: f64, key: &str) -> Strips {
        Strips {
            per_point,
            key: key.to_string(),
            ready: None,
            asked: f64::NEG_INFINITY,
            drawn: f64::NEG_INFINITY,
        }
    }
}

fn py_floor(x: f64) -> i64 {
    x.floor() as i64
}

fn py_ceil(x: f64) -> i64 {
    x.ceil() as i64
}

/// A number of a range's end, as Python's float() takes it.
fn float_of(v: &Value) -> f64 {
    v.to_f64().unwrap_or(f64::NAN)
}

/// _between: `part` / `parts` of the way from `one` to `other`, blended with their colours
/// weighed by their opacity: no dark seam where one is clear and the other is not.
fn between(one: &Image, other: &Image, part: i64, parts: i64) -> Result<Image> {
    let mixed = chops::blend(
        &one.convert(Mode::RGBa)?,
        &other.convert(Mode::RGBa)?,
        part as f32 / parts as f32,
    )?;
    Ok(mixed.convert(Mode::RGBA)?)
}

/// _blur with exact: a colour-true Gaussian blur of an RGBA image at full size (premultiplied
/// alpha): the same pixels wherever a part is cut from.
fn blur_exact(img: &Image, radius: i64) -> Result<Image> {
    if radius <= 0 {
        return Ok(img.clone());
    }
    let pre = img.convert(Mode::RGBa)?;
    let r = radius as f32;
    Ok(pre.gaussian_blur(r, r)?.convert(Mode::RGBA)?)
}

/// _halo with exact: the shadow and the glow of `layer` (without the layer), `pad` px larger
/// on every side, blurred at full size and kept in whole levels (a part of a strip is the same
/// wherever it is cut); None without either.
fn halo_exact(w: &Dict, layer: &Image, shadow: Option<[i64; 4]>) -> Result<Option<(Image, i64)>> {
    let glow = widget::number(w, "glow", 0.0);
    if glow == 0.0 && shadow.is_none() {
        return Ok(None);
    }
    let glow_radius = widget::int(w, "glow_radius", 10);
    let shadow_blur = widget::int(w, "shadow_blur", 6);
    let shadow_offset = widget::int(w, "shadow_offset", 3);
    let mut pad = 0i64;
    if glow != 0.0 {
        pad = pad.max(glow_radius.saturating_mul(2));
    }
    if shadow.is_some() {
        pad = pad.max(shadow_blur.saturating_mul(2) + shadow_offset.saturating_abs());
    }
    let (width, height) = (
        layer.width() + 2 * pad as usize,
        layer.height() + 2 * pad as usize,
    );
    let mut out = Image::blank(Mode::RGBA, width, height);
    if let Some(tint) = shadow {
        let ink = tint.map(|v| v.clamp(0, 255) as u8);
        let mut silhouette = Image::new(
            Mode::RGBA,
            layer.width(),
            layer.height(),
            &[ink[0], ink[1], ink[2], 0],
        );
        let lut: Vec<i64> = (0..256i64).map(|a| (a * tint[3]).div_euclid(255)).collect();
        silhouette.putalpha(&layer.getchannel(3)?.point(&lut)?)?;
        let mut base = Image::blank(Mode::RGBA, width, height);
        base.paste(&silhouette, pad + shadow_offset, pad + shadow_offset, None)?;
        out = lp_pixels::alpha_composite(&out, &blur_exact(&base, shadow_blur)?)?;
    }
    if glow != 0.0 {
        let mut base = Image::blank(Mode::RGBA, width, height);
        base.paste(layer, pad, pad, None)?;
        let boost = 1.0 + glow * 2.5; // (light spills: brighter than the shape's own alpha)
        let mut halo = blur_exact(&base, glow_radius)?;
        let lut: Vec<i64> = (0..256)
            .map(|a| ((f64::from(a) * boost * glow).trunc() as i64).min(255))
            .collect();
        let alpha = halo.getchannel(3)?.point(&lut)?;
        halo.putalpha(&alpha)?;
        out = lp_pixels::alpha_composite(&out, &halo)?;
    }
    Ok(Some((out, pad)))
}

/// `layer` with its alpha times `opacity` (_scale_alpha).
fn faded(layer: Image, opacity: f64) -> Result<Image> {
    Ok(effects::scale_alpha(&layer, opacity)?.unwrap_or(layer))
}

/// _strip_phases, its steps: per half-pixel step its line on its glow. The parts between two
/// steps are blended where a window is cut (Strip::phase_window).
fn strip_steps(lines: Vec<Image>, halo: Option<&Image>, pad: i64) -> Result<Vec<Image>> {
    let mut steps = Vec::with_capacity(lines.len());
    for line in lines {
        match halo {
            None => steps.push(line),
            Some(halo) => {
                let mut out = halo.clone();
                let (lw, lh) = (line.width() as i64, line.height() as i64);
                out.alpha_composite_at(&line, (pad, pad), (0, 0, lw, lh))?;
                steps.push(out);
            }
        }
    }
    Ok(steps)
}

/// The steps premultiplied, as _strip_phases blends them (none where a step has one part).
fn weigh(steps: &[Image], parts: i64) -> Result<Vec<Image>> {
    if parts <= 1 {
        return Ok(Vec::new());
    }
    Ok(steps
        .iter()
        .map(|step| step.convert(Mode::RGBa))
        .collect::<std::result::Result<_, _>>()?)
}

/// _dense: points this close (`step` pixels at the strip's scale) are a curve already: no
/// spline through them, and no round joints.
fn dense(w: &Dict, step: f64) -> Dict {
    let mut w = w.clone();
    if step < (3 * STRIP_SCALE) as f64 {
        w.insert("smooth".into(), Value::Bool(false));
        w.insert("\0joints".into(), Value::Bool(false));
    }
    w
}

/// _pad: the room the halo leaves around a layer for its effects.
fn pad_of(w: &Dict) -> i64 {
    let mut pad = 0;
    if widget::truthy(w, "glow") {
        pad = pad.max(widget::int(w, "glow_radius", 10).saturating_mul(2));
    }
    if widget::truthy(w, "shadow") {
        let blur = widget::int(w, "shadow_blur", 6).saturating_mul(2);
        pad = pad.max(blur.saturating_add(widget::int(w, "shadow_offset", 3).saturating_abs()));
    }
    pad
}

/// _reach: how far the curve's glow and shadow reach, in pixels.
fn reach(w: &Dict) -> i64 {
    let glow = match widget::truthy(w, "glow") {
        true => 3 * widget::int(w, "glow_radius", 10),
        false => 0,
    };
    let shadow = match widget::truthy(w, "shadow") {
        true => 3 * widget::int(w, "shadow_blur", 6) + widget::int(w, "shadow_offset", 3).abs(),
        false => 0,
    };
    glow.max(shadow) + widget::int(w, "line_width", 2) + 2
}

/// _graph_seconds: the time a graph shows in video mode: its `seconds`, else a pixel per
/// point (25 a second).
fn graph_seconds(w: &Dict) -> f64 {
    let mut seconds = widget::number(w, "seconds", 0.0);
    if seconds.is_nan() || seconds <= 0.0 {
        seconds = (widget::int(w, "w", 200) - 1) as f64 / RATE;
    }
    let most = (motion::MAX_POINTS as f64 - 4.0 * RATE) / RATE;
    seconds.max(2.0 / RATE).min(most)
}

/// _strip_origin: the left edge (at the strip's scale) of a strip whose first point is
/// `first`: a pixel before it, on the grid of STRIP_GRID.
fn strip_origin(first: i64, per_point: f64) -> i64 {
    let grid = STRIP_GRID * STRIP_SCALE;
    (py_floor(first as f64 * per_point) - 1).div_euclid(grid) * grid
}

/// _fade_top: where a strip's fill starts to fade: its highest point; for a range in whole
/// steps the range's top.
fn fade_top(w: &Dict, numbers: &[f64], bounds: &Range, s: i64) -> Result<i64> {
    let (first, last) = graph::range_rows(w, s);
    if widget::truthy(w, "range_step") {
        return Ok(first as i64);
    }
    if numbers.is_empty() {
        return Ok(0);
    }
    let scale = widget::text(w, "scale");
    let mut highest = f64::NEG_INFINITY;
    for &v in numbers {
        let f = graph::fraction(v, bounds, scale)?;
        if f > highest {
            highest = f;
        }
    }
    Ok((first + (last - first) * (1.0 - highest)) as i64)
}

/// The columns `x0..x1` of `from` (as `crop` takes them: beyond its edges nothing) into `to`
/// from its column `at` on, row for row, as far as both reach: what pasting that crop at `at`
/// does where `to` is blank and of the same mode.
fn copy_columns(to: &mut Image, from: &Image, x0: i64, x1: i64, at: i64) {
    let (tw, fw) = (to.width() as i64, from.width() as i64);
    // the source columns within `from`, and their place in `to` within it
    let lo = x0.max(0).max(x0 + (0 - at));
    let hi = x1.min(fw).min(x0 + (tw - at));
    if lo >= hi {
        return;
    }
    let b = from.bands();
    let (src, dst) = (lo as usize * b, (at + lo - x0) as usize * b);
    let n = (hi - lo) as usize * b;
    let rows = to.height().min(from.height());
    let (trow, frow) = (to.width() * b, from.width() * b);
    let source = from.as_bytes();
    let target = to.as_bytes_mut();
    for y in 0..rows {
        target[y * trow + dst..y * trow + dst + n]
            .copy_from_slice(&source[y * frow + src..y * frow + src + n]);
    }
}

/// _values: the values of the points `first` .. `last` that are drawn (every `every`-th):
/// recorded ones from the history, later ones ahead of time from the glide. Returns them and
/// the first point's number.
fn values_of(
    history: &History,
    course: Option<&Course>,
    first: i64,
    last: i64,
    every: i64,
) -> (Vec<Option<f64>>, i64) {
    let first = first - first.rem_euclid(every);
    let mut values: Vec<Option<f64>> = match history.last {
        Some(newest) if newest > last => Vec::new(), // (a clock that went back: none)
        Some(newest) => {
            let take = (newest - first + 1).max(0) as usize;
            let since = history.since(first);
            since
                .into_iter()
                .take(take)
                .step_by(every as usize)
                .collect()
        }
        None => Vec::new(),
    };
    let start = first + values.len() as i64 * every;
    let mut k = start;
    while k <= last {
        values.push(course.and_then(|c| c.at(k as f64 / RATE)));
        k += every;
    }
    (values, first)
}

impl Renderer {
    /// new_sample: the main loop took fresh readings at `at`; the next follow after
    /// `interval` s.
    pub fn new_sample(&mut self, at: f64, interval: f64) {
        let (before, every) = self.sample;
        let near = self.sampled && 0.0 < at - before && at - before <= 3.0 * every.max(interval);
        self.sample_before = near.then_some(before);
        self.sample = (at, interval.max(0.001));
        self.sampled = true;
    }

    /// _traced: the graph as it draws now: with its `trace`'s key as its sensor where that has
    /// a course (and the course, as its text and as numbers); else as it is.
    pub(crate) fn traced(&self, w: &Dict, snapshot: &Snapshot) -> (Dict, Option<Fine>) {
        let trace = widget::text(w, "trace");
        if trace.is_empty() {
            return (w.clone(), None);
        }
        let value = snapshot.value(trace);
        let Some(course) = graph::course_of(&value) else {
            return (w.clone(), None);
        };
        let text = match &value {
            Value::Str(text) => text.clone(),
            other => format!("{other:?}"),
        };
        let mut traced = w.clone();
        let own = widget::text(w, "sensor").to_string();
        traced.insert("sensor".into(), Value::Str(trace.to_string()));
        traced.insert("\0value".into(), Value::Str(own));
        (traced, Some((text, course)))
    }

    /// _trace_delay: how much later than its time a fine course is shown.
    fn trace_delay(&self) -> f64 {
        TRACE_LAG_S + self.sample.1 + TRACE_MARGIN_S
    }

    /// _parts: into how many parts a graph's steps are cut: so many that it moves on in every
    /// frame, by about the same.
    fn parts(&self, per_point: f64) -> i64 {
        let speed = if self.fps > 0.0 {
            RATE * per_point / self.fps
        } else {
            0.0
        };
        if speed <= 0.0 || speed >= 0.75 {
            return 1;
        }
        STRIP_PARTS.min(py_ceil(1.5 / speed))
    }

    /// _graph_view: the time at a scrolling graph's right edge, and whether it moves: `now`,
    /// the curve scrolling on; with graph_steps (not for a graph with its own fine course) the
    /// time the last reading's value is there, glided to from the one before in STEP_S and
    /// still until the next reading.
    fn graph_view(&self, now: f64, fine: bool, glide: f64) -> (f64, bool) {
        if !self.graph_steps || fine {
            return (now, true);
        }
        let at = self.sample.0;
        let k = (now - at) / STEP_S;
        let Some(before) = self.sample_before.filter(|_| k < 1.0) else {
            return (at + glide, false);
        };
        let k = k.max(0.0);
        (
            before + glide + (at - before) * k * k * (3.0 - 2.0 * k),
            true,
        )
    }

    /// _range_now in video mode: a graph's own range at `now`, on its way to the one it glides
    /// to, the newest point inside too; None for a graph with min and max, or none drawn yet.
    fn range_live(&self, w: &Dict, now: f64) -> Result<Option<(f64, f64)>> {
        if graph::fixed(w) {
            return Ok(None);
        }
        let Some(state) = self.ranges.get(widget::text(w, "id")) else {
            return Ok(None);
        };
        let (lo, hi) = graph::glide_now(w, state, now, self.options.animate)?;
        let (mut lo, mut hi) = (float_of(&lo), float_of(&hi));
        if self.options.animate && self.continuous {
            if let Some(live) = self.motion.value(widget::text(w, "sensor"), now) {
                // (Python's min and max: the first where neither is less or greater)
                if live < lo {
                    lo = live;
                }
                if live > hi {
                    hi = live;
                }
            }
        }
        Ok(Some((lo, hi)))
    }

    /// _stretch: how a strip's window drawn in the range `drawn` is stretched upright to look
    /// as in `now_range`: a row r of the drawn curve is a * r + c now, as (a, c); None where
    /// it stays as drawn (a curve on a scale other than linear, or hung from the top, and a
    /// stretch under a third of a pixel).
    fn stretch(w: &Dict, drawn: &Range, now_range: (f64, f64)) -> Option<(f64, f64)> {
        let scale = widget::text(w, "scale");
        if !(scale.is_empty() || scale == "linear") || widget::text(w, "direction") == "down" {
            return None;
        }
        let (first, last) = graph::range_rows(w, STRIP_SCALE);
        let s = STRIP_SCALE as f64;
        let (first, length) = (first / s, (last - first) / s);
        let (lo, hi) = (float_of(&drawn.0), float_of(&drawn.1));
        let (span, span_now) = (hi - lo, now_range.1 - now_range.0);
        if !(span > 0.0 && span_now > 0.0) {
            return None;
        }
        let a = span / span_now;
        let c = first + length - length * (hi - now_range.0) / span_now - a * first;
        if c.abs().max(((a - 1.0) * (first + length) + c).abs()) < 0.3 {
            return None;
        }
        Some((a, c))
    }

    /// _restretch: `layer` (a strip's window, `pad` px larger on every side), drawn in the
    /// range the stretch was worked out from, as it looks now: stretched or squeezed upright.
    fn restretch(layer: Image, pad: i64, (a, c): (f64, f64)) -> Result<Image> {
        let (width, height) = (layer.width(), layer.height());
        let top = pad as f64 - (pad as f64 + c) / a;
        let bottom = top + height as f64 / a;
        let (y0, y1) = (py_floor(top) - 1, py_ceil(bottom) + 1);
        let band = layer.crop(0, y0, width as i64, y1);
        let bounds = (0.0, top - y0 as f64, width as f64, bottom - y0 as f64);
        Ok(band.resize(width, height, Filter::Bilinear, Some(bounds), None)?)
    }

    /// _apart: how many pixels the strip's curve is off the sensor's at `t`.
    fn apart(&self, w: &Dict, strip: &Strip, t: f64) -> Result<f64> {
        let live = self.motion.value(widget::text(w, "sensor"), t);
        let old = strip.course.at(t);
        let (Some(live), Some(old)) = (live, old) else {
            return Ok(if live.is_none() && old.is_none() {
                0.0
            } else {
                f64::INFINITY
            });
        };
        let scale = widget::text(w, "scale");
        let apart = graph::fraction(live, &strip.bounds, scale)?
            - graph::fraction(old, &strip.bounds, scale)?;
        let room = widget::int(w, "h", 60) - 2 * widget::int(w, "line_width", 2);
        Ok(apart.abs() * room as f64)
    }

    /// _draw_strip: the curve through `values` (point `first` on, every `every`-th) onto a
    /// strip in the value range `bounds`: an image per half-pixel step, glow and all, and per
    /// part of a step one between it and the next.
    #[allow(clippy::too_many_arguments)]
    fn draw_strip(
        &mut self,
        w: &Dict,
        values: &[Option<f64>],
        first: i64,
        per_point: f64,
        every: i64,
        bounds: Range,
        parts: i64,
    ) -> Result<Drawn> {
        let s = STRIP_SCALE;
        let h = widget::int(w, "h", 60);
        let w = dense(w, every as f64 * per_point);
        let numbers: Vec<f64> = values.iter().flatten().copied().collect();
        let top = fade_top(&w, &numbers, &bounds, s)?;
        let origin = strip_origin(first, per_point);
        let last = first + (values.len() as i64 - 1) * every;
        let width = py_ceil((last as f64 * per_point - origin as f64 + 1.0) / s as f64) + 1;
        let step = every as f64 * per_point;
        let at = first.div_euclid(every);
        let points: Vec<(f64, Option<f64>)> = values
            .iter()
            .enumerate()
            .map(|(i, &v)| ((at + i as i64) as f64 * step - origin as f64, v))
            .collect();
        let layer = crate::prof::part("whole curve", || {
            self.graph_curve(&w, &points, width + 1, s, Some(&bounds), Some(top as f64))
        })?;
        let opacity = widget::number(&w, "opacity", 1.0);
        let mut lines = Vec::with_capacity(s as usize);
        let downing = crate::prof::Span::new("whole down");
        for p in 0..s {
            lines.push(faded(
                shapes::down_at(&layer, p, width, h, s as usize)?,
                opacity,
            )?);
        }
        drop(downing);
        drop(layer); // (drawn twice as large: the biggest picture here)
        let shadow = self.shadow_tint(&w)?;
        let (halo, pad) =
            match crate::prof::part("whole halo", || halo_exact(&w, &lines[0], shadow))? {
                Some((halo, pad)) => (Some(halo), pad),
                None => (None, 0),
            };
        let steps = crate::prof::part("whole steps", || strip_steps(lines, halo.as_ref(), pad))?;
        let weighed = crate::prof::part("whole weigh", || weigh(&steps, parts))?;
        Ok((steps, weighed, halo, pad, origin, width, top))
    }

    /// _strip_job, done at once: a strip of the curve from the window's width before `edge`
    /// (the time at its right edge: `now`, or the last reading's on a graph that steps) to past
    /// the next reading.
    #[allow(clippy::too_many_arguments)]
    fn strip_job(
        &mut self,
        w: &Dict,
        history: &History,
        now: f64,
        per_point: f64,
        every: i64,
        margin: i64,
        parts: i64,
        edge: f64,
        before: Option<&Strip>,
    ) -> Result<Strip> {
        let key = widget::text(w, "sensor").to_string();
        let last = motion::grid(edge);
        let ahead = py_ceil((self.sample.1 + STRIP_AHEAD_S) * RATE);
        let span = (widget::int(w, "w", 200) * STRIP_SCALE - 1) as f64 / per_point;
        let made_course = self.motion.course(&key);
        let (values, first) = values_of(
            history,
            made_course.as_ref().map(|(_, c)| c),
            last - py_ceil(span) - margin - every,
            last + ahead + margin,
            every,
        );
        let (made, course) = made_course.unwrap_or_default();
        let until = (last + ahead) as f64 / RATE;
        // its range from the values up to now: those it draws ahead come in over the next
        // moment, and the range with them
        let upto = ((last - first).div_euclid(every) + 1).max(0) as usize;
        let mut numbers: Vec<f64> = values.iter().take(upto).flatten().copied().collect();
        if numbers.is_empty() {
            numbers = values.iter().flatten().copied().collect();
        }
        let (mut bounds, mut gliding) = ((Value::Float(0.0), Value::Float(1.0)), false);
        if !numbers.is_empty() {
            let animate = self.options.animate;
            (bounds, gliding) = graph::glided_bounds(w, &numbers, now, animate, &mut self.ranges)?;
        }
        let grown = match before {
            Some(before) => crate::prof::part("strip grown", || {
                self.grown_strip(w, before, &values, first, per_point, every, &bounds, parts)
            })?,
            None => None,
        };
        let (drawn, grew) = match grown {
            Some((drawn, grew)) => (drawn, Some(grew)),
            None => (
                crate::prof::part("strip whole", || {
                    self.draw_strip(w, &values, first, per_point, every, bounds.clone(), parts)
                })?,
                None,
            ),
        };
        let (steps, weighed, halo, pad, origin, width, top) = drawn;
        // (each step grown from the one before's: its first columns that one's)
        let growth = match (grew, before) {
            (Some((shift, keep)), Some(before)) => (before.steps.iter())
                .map(|base| {
                    Some(crate::piece::Growth {
                        base: Rc::downgrade(base),
                        shift,
                        keep,
                    })
                })
                .collect(),
            _ => vec![None; steps.len()],
        };
        let steps: Vec<Rc<Image>> = steps.into_iter().map(Rc::new).collect();
        self.strip_count += 1;
        Ok(Strip {
            id: self.strip_count,
            made,
            course,
            taken: now,
            until,
            per_point,
            every,
            origin,
            width,
            bounds,
            top,
            pad,
            steps,
            growth,
            weighed,
            parts,
            halo,
            gliding,
            values,
            first,
        })
    }

    /// _grown_strip: the strip draw_strip would draw, made of the one `before` where the curve
    /// stays as it was and drawn anew only from where its values change (a reading came: from
    /// the point before it, and what its glow reaches), with the same range and fade: about a
    /// tenth of it each second instead of all of it. None where that is not so (another range,
    /// another fade, a strip drawn otherwise): drawn whole. The pixels are the whole drawing's,
    /// but in its first columns, where the one before saw further back (no window shows them:
    /// they are its glow's margin).
    #[allow(clippy::too_many_arguments, clippy::type_complexity)]
    fn grown_strip(
        &self,
        w: &Dict,
        before: &Strip,
        values: &[Option<f64>],
        first: i64,
        per_point: f64,
        every: i64,
        bounds: &Range,
        parts: i64,
    ) -> Result<Option<(Drawn, (i64, i64))>> {
        let same_range = float_of(&before.bounds.0) == float_of(&bounds.0)
            && float_of(&before.bounds.1) == float_of(&bounds.1);
        if before.steps.is_empty()
            || before.per_point != per_point
            || before.every != every
            || before.parts != parts
            || !same_range
            || (first - before.first).rem_euclid(every) != 0
            || first < before.first
        {
            return Ok(None);
        }
        let s = STRIP_SCALE;
        let h = widget::int(w, "h", 60);
        let dense = dense(w, every as f64 * per_point);
        let numbers: Vec<f64> = values.iter().flatten().copied().collect();
        if numbers.is_empty() || fade_top(&dense, &numbers, bounds, s)? != before.top {
            return Ok(None);
        }
        // (where `values` begin in the one before)
        let start = ((first - before.first) / every) as usize;
        let same = values
            .iter()
            .zip(before.values.iter().skip(start))
            .take_while(|(value, was)| value == was)
            .count() as i64;
        let changed = first + same * every; // the first point that is not as it was
        let grid = STRIP_GRID * s;
        let origin = strip_origin(first, per_point);
        let last = first + (values.len() as i64 - 1) * every;
        let width = py_ceil((last as f64 * per_point - origin as f64 + 1.0) / s as f64) + 1;
        let pad = before.pad;
        // (the lit halo too)
        let reach = reach(&dense) + 3 * widget::int(&dense, "glow_radius", 10) + grid;
        let shift = (origin - before.origin).div_euclid(s); // the one before's columns, from
                                                            // this one's columns from `seam` on are drawn anew: from the segment that leads to the
                                                            // first point changed on, and the glow and line around it
        let seam =
            py_floor(((changed - every) as f64 * per_point - origin as f64) / s as f64) - reach - 2;
        let seam = seam.min(before.width - shift - reach - 2);
        if seam < 2 * reach + pad || seam >= width {
            return Ok(None); // (little of it stays: drawn whole)
        }
        let step = grid / s; // (columns)
                             // drawn from here: the glow at `seam` is the whole's, and its grid the same
        let begin = (seam - reach - 2).div_euclid(step) * step;
        let x0 = origin + s * begin; // its left edge, at `s`
        let lo = ((py_floor(x0 as f64 / per_point) - first).div_euclid(every) - 1).max(0) as usize;
        let points: Vec<(f64, Option<f64>)> = (lo..values.len())
            .map(|i| {
                (
                    (first + i as i64 * every) as f64 * per_point - x0 as f64,
                    values[i],
                )
            })
            .collect();
        let tail_w = width - begin;
        let top = Some(before.top as f64);
        let layer = crate::prof::part("strip curve", || {
            self.graph_curve(&dense, &points, tail_w + 1, s, Some(bounds), top)
        })?;
        let opacity = widget::number(&dense, "opacity", 1.0);
        let downing = crate::prof::Span::new("strip down");
        let mut lines = Vec::with_capacity(s as usize);
        for p in 0..s {
            lines.push(faded(
                shapes::down_at(&layer, p, tail_w, h, s as usize)?,
                opacity,
            )?);
        }
        drop(layer);
        drop(downing);
        let shadow = self.shadow_tint(&dense)?;
        let haloed = crate::prof::part("strip halo", || halo_exact(&dense, &lines[0], shadow))?;
        let (halo, tail_pad) = match haloed {
            Some((halo, pad)) => (Some(halo), pad),
            None => (None, 0),
        };
        if tail_pad != pad {
            return Ok(None);
        }
        let tails = crate::prof::part("strip steps", || strip_steps(lines, halo.as_ref(), pad))?;
        let tails_weighed = crate::prof::part("strip weigh", || weigh(&tails, parts))?;
        let _joining = crate::prof::Span::new("strip join");
        let size = ((width + 2 * pad) as usize, (h + 2 * pad) as usize);
        let keep = seam + pad;
        let cut = seam - begin + pad; // the tail's column of this one's column `seam`
        let joined = |old: &Image, tail: &Image| -> Result<Image> {
            let mut out = Image::blank(old.mode(), size.0, size.1);
            if old.mode() == tail.mode() {
                // (what pasting the two crops does: the parts within each copied, the rest
                // left blank, as their zeros would leave it)
                copy_columns(&mut out, old, shift, shift + keep, 0);
                copy_columns(&mut out, tail, cut, tail.width() as i64, keep);
                return Ok(out);
            }
            out.paste(
                &old.crop(shift, 0, shift + keep, old.height() as i64),
                0,
                0,
                None,
            )?;
            let rest = tail.crop(cut, 0, tail.width() as i64, tail.height() as i64);
            out.paste(&rest, keep, 0, None)?;
            Ok(out)
        };
        let steps = before
            .steps
            .iter()
            .zip(&tails)
            .map(|(old, tail)| joined(old, tail))
            .collect::<Result<Vec<Image>>>()?;
        let weighed = before
            .weighed
            .iter()
            .zip(&tails_weighed)
            .map(|(old, tail)| joined(old, tail))
            .collect::<Result<Vec<Image>>>()?;
        let halo = match (&before.halo, &halo) {
            (Some(old), Some(tail)) => Some(joined(old, tail)?),
            _ => None,
        };
        let drawn = (steps, weighed, halo, pad, origin, width, before.top);
        Ok(Some((drawn, (shift, keep))))
    }

    /// _patch_piece: the end of the window (from `left`) drawn again from where the strip no
    /// longer holds (a reading it did not know): the picture that goes over the window's
    /// columns from the one it gives on, to the window's right edge.
    fn patch_piece(
        &self,
        w: &Dict,
        strip: &Strip,
        history: &History,
        valid: f64,
        left: i64,
    ) -> Result<(Image, i64)> {
        let s = STRIP_SCALE;
        let (gw, h, pad) = (widget::int(w, "w", 200), widget::int(w, "h", 60), strip.pad);
        let w = dense(w, strip.every as f64 * strip.per_point);
        let reach = reach(&w);
        // window columns from `start` on change (and the glow that reaches them); from `begin`
        // on, the patch draws what that glow sees
        let changed = (valid * RATE * strip.per_point - left as f64) / s as f64;
        let start = (-pad).max(py_floor(changed) - reach);
        let (begin, end) = (start - reach, gw + pad + reach);
        let x0 = left + s * begin;
        let first = py_floor(x0 as f64 / strip.per_point) - strip.every;
        let last = py_ceil((x0 + s * (end - begin)) as f64 / strip.per_point) + strip.every;
        let course = self.motion.course(widget::text(&w, "sensor"));
        let (values, first) = values_of(
            history,
            course.as_ref().map(|(_, c)| c),
            first,
            last,
            strip.every,
        );
        let step = strip.every as f64 * strip.per_point;
        let at = first.div_euclid(strip.every);
        let points: Vec<(f64, Option<f64>)> = values
            .iter()
            .enumerate()
            .map(|(i, &v)| ((at + i as i64) as f64 * step - x0 as f64, v))
            .collect();
        let top = Some(strip.top as f64);
        let part = self.graph_curve(&w, &points, end - begin, s, Some(&strip.bounds), top)?;
        let opacity = widget::number(&w, "opacity", 1.0);
        let mut line = faded(shapes::down(&part, end - begin, h, s as usize)?, opacity)?;
        let shadow = self.shadow_tint(&w)?;
        if let Some((mut halo, _)) = halo_exact(&w, &line, shadow)? {
            // the curve inside the window, its glow also beside it
            let from = (-begin).max(0);
            let inside = line.crop(from, 0, gw - begin, h);
            let (iw, ih) = (inside.width() as i64, inside.height() as i64);
            halo.alpha_composite_at(&inside, (pad + from, pad), (0, 0, iw, ih))?;
            line = halo;
        }
        let cut = start - begin + pad; // the patch's column of window column `start`
        let height = line.height() as i64;
        Ok((
            line.crop(cut, 0, cut + gw + pad - start, height),
            start + pad,
        ))
    }

    /// _timeline: a graph in video mode, its window cut from its strip for this frame.
    pub(crate) fn timeline(&mut self, w: &Dict, snapshot: &Snapshot) -> Result<Rc<Piece>> {
        let now = self.at;
        let (w, fine) = self.traced(w, snapshot);
        let wid = widget::text(&w, "id").to_string();
        let key = widget::text(&w, "sensor").to_string();
        let (gw, gh) = (widget::int(&w, "w", 200), widget::int(&w, "h", 60));
        let s = STRIP_SCALE;
        let glide = self.glide_s(None);
        let (mut readings_key, mut last_at) = (key.clone(), self.sample.0);
        match &fine {
            Some((text, course)) => {
                let delay = self.trace_delay();
                let end = self.sample.0 - TRACE_LAG_S + delay;
                self.motion.trace(&key, text, course, end, now);
                readings_key = widget::text(&w, "\0value").to_string();
                last_at += delay;
            }
            None => {
                let target = motion::number(&snapshot.value(&key));
                let lead = self.lead();
                self.motion.glide(&key, target, now, glide, lead);
            }
        }
        let readings: Vec<Value> = snapshot
            .history
            .get(&readings_key)
            .map(|h| h.iter().map(|&v| Value::Float(v)).collect())
            .unwrap_or_default();
        let span = graph_seconds(&w) * RATE; // points across the window
        let per_point = (gw * s - 1) as f64 / span; // their distance, in pixels at `s`
        let every = ((s as f64 / per_point) as i64).max(1); // long windows: a point a pixel
        let margin = py_ceil(s as f64 * (pad_of(&w) + reach(&w) + 1) as f64 / per_point);
        let (view, scrolls) = self.graph_view(now, fine.is_some(), glide);
        self.moving |= scrolls;
        // a graph that steps shows up to a reading or two before now: as many points more
        let lag = if view >= now {
            0
        } else {
            py_ceil((now - view).max(2.0 * self.sample.1) * RATE)
        };
        let size = (py_ceil(span) + margin + 2 * every + lag).max(0) as usize;
        let interval = self.sample.1;
        let history = self
            .motion
            .history(&key, size, now, &readings, last_at, interval, glide)
            .clone();
        let fresh = match self.strips.get(&wid) {
            Some(strips) => strips.per_point != per_point || strips.key != key,
            None => true,
        };
        if fresh {
            self.strips
                .insert(wid.clone(), Strips::new(per_point, &key));
        }
        if let Some(strips) = self.strips.get_mut(&wid) {
            strips.drawn = now;
        }
        // the window's left edge at `s`, point k sitting at k * per_point on every strip: the
        // newest point at the right edge, now
        let exact = view * RATE * per_point - (gw * s - 1) as f64;
        let left = py_floor(exact);
        let parts = self.parts(per_point);
        let ready = self
            .strips
            .get(&wid)
            .and_then(|strips| strips.ready.clone());
        let mut strip = match ready {
            Some(strip) if strip.covers(left, gw, s) && strip.covers(left + 1, gw, s) => strip,
            _ => {
                let made = crate::prof::part("strip", || {
                    self.strip_job(
                        &w, &history, now, per_point, every, margin, parts, view, None,
                    )
                })?;
                let made = Rc::new(made);
                if let Some(strips) = self.strips.get_mut(&wid) {
                    strips.ready = Some(made.clone());
                }
                made
            }
        };
        let mut valid = self
            .motion
            .valid_until(&key, strip.made, strip.taken, strip.until);
        let due = strip.gliding || valid < strip.until || view >= strip.until - STRIP_EVERY_S;
        let every_s = if strip.gliding {
            STRIP_GLIDE_S
        } else {
            STRIP_EVERY_S
        };
        let asked = self.strips.get(&wid).map_or(f64::NEG_INFINITY, |s| s.asked);
        if due && now - asked >= every_s {
            let before = Rc::clone(&strip);
            let made = crate::prof::part("strip due", || {
                let before = Some(&*before);
                self.strip_job(
                    &w, &history, now, per_point, every, margin, parts, view, before,
                )
            })?;
            strip = Rc::new(made);
            valid = strip.until;
            if let Some(strips) = self.strips.get_mut(&wid) {
                strips.asked = now;
                strips.ready = Some(strip.clone());
            }
        }
        // a step, and a part of one
        let stepped = lp_py::round(exact * strip.parts as f64) as i64;
        let (left, part) = (
            stepped.div_euclid(strip.parts),
            stepped.rem_euclid(strip.parts),
        );
        let (offset, phase) = (
            (left - strip.origin).div_euclid(s),
            (left - strip.origin).rem_euclid(s),
        );
        let pad = strip.pad;
        // a reading the strip did not know: draw the end again once it shows, at the newest
        // point or in the glow just past it (a big change)
        let beyond = view + (s * (pad + reach(&w))) as f64 / (per_point * RATE);
        let patch = valid < view
            && (self.apart(&w, &strip, view)? >= 0.4 || self.apart(&w, &strip, beyond)? >= 4.0);
        // (its own range: where it is this frame)
        let mut now_range = self.range_live(&w, now)?;
        if let Some((lo, hi)) = now_range {
            let rounded = (lp_py::round_digits(lo, 3), lp_py::round_digits(hi, 3));
            let drawn = (
                lp_py::round_digits(float_of(&strip.bounds.0), 3),
                lp_py::round_digits(float_of(&strip.bounds.1), 3),
            );
            now_range = (rounded != drawn).then_some(rounded);
        }
        // (a window its own range leaves as drawn is the strip's window, cut as any)
        let stretch = now_range.and_then(|r| Self::stretch(&w, &strip.bounds, r));
        let content = Value::List(vec![
            Value::Str("timeline".into()),
            Value::int(strip.id as i64),
            Value::int(offset),
            Value::int(phase),
            Value::int(part),
            match stretch {
                Some((a, c)) => Value::List(vec![Value::Float(a), Value::Float(c)]),
                None => Value::None,
            },
        ]);
        if !patch {
            if let Some((kept, piece)) = self.cache.get(&wid) {
                if *kept == content {
                    return Ok(piece.clone());
                }
            }
        }
        let window = |k: i64, column: i64, at: i64| -> Result<Image> {
            let k = k.clamp(0, strip.phase_count() - 1);
            let mut layer = strip.phase_window(k, column, gw + 2 * pad, gh + 2 * pad)?;
            if let Some(halo) = &strip.halo {
                // beside the window only the glow: the curve ends there
                for x0 in [0, gw + pad] {
                    let side = halo.crop(column + x0, 0, column + x0 + pad, gh + 2 * pad);
                    layer.paste(&side, x0, 0, None)?;
                }
            }
            if patch {
                let (piece, x) = self.patch_piece(&w, &strip, &history, valid, at)?;
                layer.paste(&piece, x, 0, None)?;
            }
            Ok(layer)
        };
        // (a window cut from a step alone, nothing laid on it: what it is cut from, for an
        // engine that keeps the strip's steps and cuts it itself)
        let mut cut = None;
        let k = (phase * strip.parts + part).clamp(0, strip.phase_count() - 1);
        let parts_of = strip.parts.max(1);
        if !patch
            && stretch.is_none()
            && strip.halo.is_none()
            && (k.rem_euclid(parts_of) == 0 || strip.weighed.is_empty())
        {
            let step = k.div_euclid(parts_of) as usize;
            cut = Some(vec![crate::piece::CutPart {
                image: Rc::clone(&strip.steps[step]),
                growth: strip.growth.get(step).cloned().flatten(),
                sx: offset,
                sy: 0,
                dx: 0,
                dy: 0,
                w: gw + 2 * pad,
                h: gh + 2 * pad,
            }]);
        }
        // (where the engine cuts it from what it keeps, its pixels are left out: made only
        // where something asks for them, Piece::whole)
        let clip = widget::truthy(&w, "clip");
        let leave_out = cut.is_some() && self.left_out_windows && !clip;
        let mut layer = if leave_out {
            Image::blank(Mode::RGBA, 0, 0)
        } else if !patch || part == 0 {
            window(phase * strip.parts + part, offset, left)?
        } else {
            // the end drawn again: on both steps, and blended as the strip's parts are
            let ahead = (phase + 1).rem_euclid(s);
            let following = window(
                ahead * strip.parts,
                offset + i64::from(ahead == 0),
                left + 1,
            )?;
            let this = window(phase * strip.parts, offset, left)?;
            between(&this, &following, part, strip.parts)?
        };
        if let Some(stretch) = stretch {
            // its own range glides: on its way in every frame
            layer = Self::restretch(layer, pad, stretch)?;
        }
        let (x, y) = (widget::int(&w, "x", 0), widget::int(&w, "y", 0));
        let mut piece = Piece::new(layer, x - pad, y - pad);
        piece.hit = Some([x, y, gw, gh]);
        piece.cut = cut;
        if leave_out {
            piece.left_out = Some(((gw + 2 * pad) as usize, (gh + 2 * pad) as usize));
        }
        if clip {
            piece = effects::clip_sides(piece);
        }
        let piece = Rc::new(piece);
        let kept = if patch {
            Value::Str("\0patched".into()) // (never the same: drawn again next frame)
        } else {
            content
        };
        self.cache.insert(wid, (kept, piece.clone()));
        Ok(piece)
    }

    /// _forget_strips: the strips of a graph not on show go once they end before `now`.
    pub(crate) fn forget_strips(&mut self, now: f64) {
        self.strips.retain(|_, strips| {
            strips.drawn >= now
                || strips
                    .ready
                    .as_ref()
                    .is_some_and(|ready| ready.until + 1.0 >= now)
        });
    }

    /// The end dot of a graph in video mode (_draw_end_dot's continuous part): the gliding
    /// value at the right edge, in the range the strip is shown in this frame; None where
    /// there is none.
    pub(crate) fn timeline_end(&self, w: &Dict, snapshot: &Snapshot) -> Result<Option<End>> {
        let w = self.traced(w, snapshot).0;
        let now = self.at;
        let value = self.motion.value(widget::text(&w, "sensor"), now);
        let strip = self
            .strips
            .get(widget::text(&w, "id"))
            .and_then(|strips| strips.ready.clone());
        let Some(strip) = strip else {
            return Ok(None);
        };
        let bounds = match self.range_live(&w, now)? {
            Some(range) => range,
            None => (float_of(&strip.bounds.0), float_of(&strip.bounds.1)),
        };
        Ok(value.map(|value| (w, value, bounds)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use lp_pixels::Mode;

    #[test]
    fn columns_copied_are_the_crop_pasted() {
        let mut seed = 7u64;
        let mut next = |n: i64| {
            seed = seed
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            ((seed >> 33) as i64).rem_euclid(n)
        };
        for _ in 0..400 {
            let (fw, fh) = (1 + next(30) as usize, 1 + next(12) as usize);
            let (tw, th) = (1 + next(30) as usize, 1 + next(12) as usize);
            let mut from = Image::blank(Mode::RGBA, fw, fh);
            for (i, v) in from.as_bytes_mut().iter_mut().enumerate() {
                *v = (i * 37 % 251) as u8 + 1;
            }
            let (x0, x1, at) = (next(50) - 15, next(50) - 15, next(50) - 15);
            let mut want = Image::blank(Mode::RGBA, tw, th);
            want.paste(&from.crop(x0, 0, x1.max(x0), fh as i64), at, 0, None)
                .unwrap();
            let mut got = Image::blank(Mode::RGBA, tw, th);
            copy_columns(&mut got, &from, x0, x1, at);
            assert_eq!(
                got.as_bytes(),
                want.as_bytes(),
                "{fw}x{fh} -> {tw}x{th} {x0}..{x1} at {at}"
            );
        }
    }
}
