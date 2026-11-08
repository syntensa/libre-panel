//! Graph widgets (render/renderer.py's _draw_graph, _graph_piece, _graph_layer, _curve and
//! _draw_end_dot) in a still frame: the curve of a sensor's history (or of a course a service
//! published whole, as text) across the widget, drawn three times larger and reduced: its
//! background, its grid, its fill (fading from its peak, or flat), the line through its points
//! (smoothed, Catmull-Rom), a gap where a value is none; its range the widget's, else the
//! values' with room above and below (in whole steps where it says), as Python's has it in a
//! still frame; the lit finish's line as light (lit.py's LitGraph); the dot at the line's end.

use std::cmp::Ordering;

use lp_pixels::{chops, Draw, Filter, Image, Mode};
use lp_py::num as pn;
use lp_py::value::compare;
use lp_py::{Dict, Value};
use num_bigint::BigInt;
use num_traits::{FromPrimitive, Signed};

use crate::bar::Over;
use crate::finish::{self, Finish};
use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Renderer, MOST_PIXELS};
use crate::shapes::{self, ink, Rgba, SUPERSAMPLE};
use crate::snapshot::Snapshot;
use crate::widget;

/// A course a service publishes whole is drawn up to this long (COURSE_POINTS).
const COURSE_POINTS: usize = 4000;
/// Room above and below the values of a graph's own range, of their span (RANGE_ROOM).
const RANGE_ROOM: f64 = 0.1;

/// A graph's range as Python counts with it: each end the int or the float it is (an int a
/// theme gives, or one that whole steps of an int make: exact, however large).
pub(crate) type Range = (Value, Value);

/// A "number?" field (min, max) as the theme gives it, an int or a float; None where none.
fn end(w: &Dict, key: &str) -> Option<Value> {
    match w.get(key) {
        Some(v @ (Value::Int(_) | Value::Float(_) | Value::Bool(_))) => Some(v.clone()),
        _ => None,
    }
}

/// What Python's arithmetic raises, as the widget's failure.
fn py<T>(r: pn::Result<T>) -> Result<T> {
    r.map_err(|e| Failed(e.msg))
}

/// a == b of numbers as Python compares them: an int against a float exactly, NaN equal to
/// nothing.
fn same(a: &Value, b: &Value) -> bool {
    compare(a, b) == Some(Ordering::Equal)
}

/// a >= b of numbers as Python compares them.
fn at_least(a: &Value, b: &Value) -> bool {
    matches!(compare(a, b), Some(Ordering::Greater | Ordering::Equal))
}

/// abs() of a number.
fn magnitude(v: &Value) -> Value {
    match v {
        Value::Int(n) => Value::Int(n.abs()),
        Value::Bool(b) => Value::int(i64::from(*b)),
        Value::Float(f) => Value::Float(f.abs()),
        other => other.clone(),
    }
}

/// The float a true division gives.
fn float_of(v: &Value) -> f64 {
    match v {
        Value::Float(f) => *f,
        _ => f64::NAN,
    }
}

/// Python's max(a, b) of floats: `a` unless `b` is greater (a NaN is never greater).
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python's min(a, b) of floats: `a` unless `b` is less.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// Python's min() and max() of a list of floats: the first of the least (greatest).
fn least(numbers: &[f64]) -> f64 {
    numbers[1..]
        .iter()
        .fold(numbers[0], |m, &v| if v < m { v } else { m })
}

fn most(numbers: &[f64]) -> f64 {
    numbers[1..]
        .iter()
        .fold(numbers[0], |m, &v| if v > m { v } else { m })
}

/// What Rust says of a curve whose points are NaN as Pillow would get them.
pub(crate) const NAN_POINTS: &str =
    "graphs through points that are no numbers (a log scale of no finite span)";

/// int(x) of a float, as Python takes it: toward zero, an error for NaN and the infinities.
fn int_of(x: f64) -> Result<f64> {
    if x.is_nan() {
        return Err(Failed("cannot convert float NaN to integer".into()));
    }
    if x.is_infinite() {
        return Err(Failed("cannot convert float infinity to integer".into()));
    }
    Ok(x.trunc())
}

/// Whether the curve's points come out NaN and go to Pillow so. _fraction makes every point
/// NaN on a log scale whose span is no finite number (values near a float's largest); smoothing
/// keeps them between the rows, and a fill's fade fails on them first (int(min(ys))), but a
/// line or a fill drawn through them hands Pillow a NaN, which it turns into an int as C does:
/// what comes out is left to the processor (the least int on x86, 0 on ARM). Rust does not
/// draw these.
pub(crate) fn points_undrawable(w: &Dict, values: &[Option<f64>], range: Option<&Range>) -> bool {
    let Some((lo, hi)) = range else {
        return false;
    };
    // (a span that is a float's infinity: abs(hi - lo) as Python has it)
    let endless =
        matches!(pn::sub(hi, lo).map(|d| magnitude(&d)), Ok(Value::Float(f)) if f.is_infinite());
    let nan = widget::text(w, "scale") == "log" && !same(hi, lo) && endless;
    let run = values
        .windows(2)
        .any(|pair| pair[0].is_some() && pair[1].is_some());
    let fade_fails = widget::truthy(w, "fill")
        && widget::truthy(w, "fill_fade")
        && !widget::truthy(w, "range_step");
    nan && run && !widget::truthy(w, "smooth") && !fade_fails
}

/// _undrawable's other side: a value of a course that can be drawn, as a float (NaN, infinity,
/// a bool and ints over 2**1023 cannot).
fn drawable(v: &Value) -> Option<f64> {
    match v {
        Value::Float(f) if f.is_finite() => Some(*f),
        Value::Int(n) if n.bits() <= 1023 => lp_py::num::int_to_float(n).ok(),
        _ => None,
    }
}

/// course_of: the course in a reading's value (a list of numbers as text, "[61.5, null, 64.2]",
/// null or anything not a number a gap), from 2 to COURSE_POINTS points; None for any other.
pub(crate) fn course_of(value: &Value) -> Option<Vec<Option<f64>>> {
    let Value::Str(text) = value else {
        return None;
    };
    if !text.starts_with('[') {
        return None;
    }
    let Ok(Value::List(found)) = lp_py::json::loads(text) else {
        return None;
    };
    if !(2..=COURSE_POINTS).contains(&found.len()) {
        return None;
    }
    Some(found.iter().map(drawable).collect())
}

/// _gaps: a history with a gap where a value is not finite.
pub(crate) fn gaps(values: &[f64]) -> Vec<Option<f64>> {
    values.iter().map(|&v| v.is_finite().then_some(v)).collect()
}

/// The last `slots` values of a sensor's history, a gap where one is not finite.
pub(crate) fn history_of(snapshot: &Snapshot, w: &Dict) -> (Vec<Option<f64>>, usize) {
    let slots = widget::int(w, "history", 60).max(2);
    let history = snapshot
        .history
        .get(widget::text(w, "sensor"))
        .map_or(&[][..], Vec::as_slice);
    let from = history
        .len()
        .saturating_sub(usize::try_from(slots).unwrap_or(usize::MAX));
    (
        gaps(&history[from..]),
        usize::try_from(slots).unwrap_or(usize::MAX),
    )
}

/// _graph_bounds: the widget's range, else the values' (a range of one value from it to one
/// above), its ends the numbers they are.
pub(crate) fn bounds(w: &Dict, numbers: &[f64]) -> Result<Range> {
    let lo = end(w, "min").unwrap_or_else(|| Value::Float(least(numbers)));
    let hi = end(w, "max").unwrap_or_else(|| Value::Float(most(numbers)));
    if same(&hi, &lo) {
        let above = py(pn::add(&lo, &Value::int(1)))?;
        Ok((lo, above))
    } else {
        Ok((lo, hi))
    }
}

/// Whether the widget's range is all its own (min and max): then no range is kept for it.
pub(crate) fn fixed(w: &Dict) -> bool {
    end(w, "min").is_some() && end(w, "max").is_some()
}

/// math.floor's and math.ceil's int: none of NaN or an infinity.
fn whole(v: f64) -> Result<f64> {
    if v.is_nan() {
        Err(Failed("cannot convert float NaN to integer".into()))
    } else if v.is_infinite() {
        Err(Failed("cannot convert float infinity to integer".into()))
    } else {
        Ok(v)
    }
}

/// math.floor (math.ceil where `up`) of the float a true division gives: the int.
fn whole_int(v: &Value, up: bool) -> Result<Value> {
    let x = float_of(v);
    let x = whole(if up { x.ceil() } else { x.floor() })?;
    Ok(Value::Int(BigInt::from_f64(x).expect("a finite float")))
}

/// _glided_bounds in a still frame: the range the graph is drawn in: the widget's; where it
/// has none, the values' with a tenth of their span of room (in whole `range_step`s, from 0 up
/// where none is below it), counted as Python counts: an int step's multiples exact ints.
pub(crate) fn still_bounds(w: &Dict, numbers: &[f64]) -> Result<Range> {
    let (lo, hi) = bounds(w, numbers)?;
    let (fixed_lo, fixed_hi) = (end(w, "min").is_some(), end(w, "max").is_some());
    if fixed_lo && fixed_hi {
        return Ok((lo, hi));
    }
    let room = py(pn::mul(&py(pn::sub(&hi, &lo))?, &Value::Float(RANGE_ROOM)))?;
    let mut want = (
        if fixed_lo {
            lo.clone()
        } else {
            py(pn::sub(&lo, &room))?
        },
        if fixed_hi {
            hi.clone()
        } else {
            py(pn::add(&hi, &room))?
        },
    );
    // (widget.get("range_step") or 0)
    let step = w
        .get("range_step")
        .filter(|v| v.truthy())
        .cloned()
        .unwrap_or_else(|| Value::int(0));
    if py(pn::gt(&step, &Value::int(0)))? {
        let steps = |v: &Value, up: bool| -> Result<Value> {
            let n = whole_int(&py(pn::true_div(v, &step))?, up)?;
            py(pn::mul(&n, &step))
        };
        let mut down = steps(&want.0, false)?;
        if at_least(&lo, &Value::int(0)) {
            down = py(pn::max(&Value::Float(0.0), &down))?;
        }
        let up = py(pn::max(&py(pn::add(&down, &step))?, &steps(&want.1, true)?))?;
        want = (
            if fixed_lo { want.0 } else { down },
            if fixed_hi { want.1 } else { up },
        );
    }
    Ok(want)
}

/// still_bounds for the checks (lp-check's graph_range): the range of `numbers` (at least one)
/// as a still frame draws the graph in, or what Python raises for it.
pub fn still_range(w: &Dict, numbers: &[f64]) -> std::result::Result<Range, String> {
    still_bounds(w, numbers).map_err(|e| e.0)
}

/// _fraction of a graph's point: `value`'s place between the range's ends (0 to 1) as Python
/// counts with ends of an int or a float, bent by a sqrt or log scale; what Python raises (an
/// int too large for a float).
fn fraction(value: f64, (lo, hi): &Range, scale: &str) -> Result<f64> {
    if same(hi, lo) {
        return Ok(0.0);
    }
    let above = py(pn::sub(&Value::Float(value), lo))?;
    let at = float_of(&py(pn::true_div(&above, &py(pn::sub(hi, lo))?))?);
    // (min(1.0, max(0.0, at)): a NaN is 0)
    let linear = py_min(1.0, py_max(0.0, at));
    Ok(match scale {
        "sqrt" => linear.sqrt(),
        "log" => {
            let span = magnitude(&py(pn::sub(hi, lo))?);
            let top = float_of(&py(pn::mul(&Value::Float(linear), &span))?).ln_1p();
            let bottom = py(pn::to_float(&span))?.ln_1p();
            if bottom == 0.0 {
                return Err(Failed("float division by zero".into()));
            }
            top / bottom
        }
        _ => linear,
    })
}

/// _range_rows: the rows (at `s`) a graph's top and bottom value lie on: a line's width in
/// from its edges (inset), else the edges themselves.
pub(crate) fn range_rows(w: &Dict, s: i64) -> (f64, f64) {
    let h = widget::int(w, "h", 60);
    if widget::flag(w, "inset", true) {
        let pad = widget::int(w, "line_width", 2).saturating_mul(s) as f64;
        return (pad, h.saturating_mul(s) as f64 - 1.0 - pad);
    }
    (0.0, (h - 1).saturating_mul(s) as f64)
}

/// A graph's values as its piece is kept by: a value or a gap each.
fn values_key(values: &[Option<f64>]) -> Value {
    Value::List(
        values
            .iter()
            .map(|v| v.map_or(Value::None, Value::Float))
            .collect(),
    )
}

/// A range as its piece is kept by (None for none).
fn range_key(range: Option<&Range>) -> Value {
    range.map_or(Value::None, |(lo, hi)| {
        Value::List(vec![lo.clone(), hi.clone()])
    })
}

/// The key _draw_graph keeps a graph of a course by: the course and the range.
pub(crate) fn course_key(course: &[Option<f64>], range: Option<&Range>) -> Value {
    Value::List(vec!["course".into(), values_key(course), range_key(range)])
}

/// The key _draw_graph keeps a graph of a history by: how many values, the last three, the
/// first, and the range (as Python's: values in between are not looked at).
pub(crate) fn history_key(values: &[Option<f64>], range: Option<&Range>) -> Value {
    let last = &values[values.len().saturating_sub(3)..];
    let first = values
        .first()
        .map_or(Value::None, |v| v.map_or(Value::None, Value::Float));
    Value::List(vec![
        Value::int(values.len() as i64),
        values_key(last),
        first,
        range_key(range),
    ])
}

/// _catmull_rom: a smooth curve through all points (uniform Catmull-Rom, six samples a span),
/// in Python's order of sums.
fn catmull_rom(points: &[(f64, f64)]) -> Vec<(f64, f64)> {
    if points.len() < 3 {
        return points.to_vec();
    }
    let samples = 6;
    let mut out = vec![points[0]];
    let mut padded = vec![points[0]];
    padded.extend_from_slice(points);
    padded.push(points[points.len() - 1]);
    for i in 1..padded.len() - 2 {
        let (p0, p1, p2, p3) = (padded[i - 1], padded[i], padded[i + 1], padded[i + 2]);
        for step in 1..=samples {
            let t = f64::from(step) / f64::from(samples);
            let (t2, t3) = (t * t, t * t * t);
            let at = |a: f64, b: f64, c: f64, d: f64| {
                0.5 * (2.0 * b
                    + (-a + c) * t
                    + (2.0 * a - 5.0 * b + 4.0 * c - d) * t2
                    + (-a + 3.0 * b - 3.0 * c + d) * t3)
            };
            out.push((at(p0.0, p1.0, p2.0, p3.0), at(p0.1, p1.1, p2.1, p3.1)));
        }
    }
    out
}

impl Renderer {
    /// _graph_piece: the graph of `values` (value `i` at slot `slots - len + i`), reduced, at
    /// the widget's corner, its box the widget's.
    pub(crate) fn graph_piece(
        &self,
        w: &Dict,
        values: &[Option<f64>],
        slots: usize,
        range: Option<&Range>,
    ) -> Result<Piece> {
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (gw, gh) = (widget::int(w, "w", 200), widget::int(w, "h", 60));
        let s = SUPERSAMPLE as i64;
        let step = (gw.saturating_mul(s) - 1) as f64 / (slots as f64 - 1.0);
        let first = slots as f64 - values.len() as f64;
        let points: Vec<(f64, Option<f64>)> = values
            .iter()
            .enumerate()
            .map(|(i, &v)| ((first + i as f64) * step, v))
            .collect();
        let layer = self.graph_curve(w, &points, gw, s, range)?;
        let mut piece = Piece::new(shapes::down(&layer, gw, gh, SUPERSAMPLE)?, x, y);
        piece.hit = Some([x, y, gw, gh]);
        Ok(piece)
    }

    /// _curve: background, grid and the curve through `points` (x in pixels at `s`; a value
    /// None breaks it) on a canvas `width` px wide, its range `range` (else the widget's, else
    /// the points').
    fn graph_curve(
        &self,
        w: &Dict,
        points: &[(f64, Option<f64>)],
        width: i64,
        s: i64,
        range: Option<&Range>,
    ) -> Result<Image> {
        let h = widget::int(w, "h", 60);
        let (cw, ch) = (width.saturating_mul(s).max(1), h.saturating_mul(s).max(1));
        if cw.saturating_mul(ch) > MOST_PIXELS {
            return Err(Failed("a graph too large".into()));
        }
        let (cw, ch) = (cw as usize, ch as usize);
        let mut layer = Image::blank(Mode::RGBA, cw, ch);
        let (wf, hf) = (cw as f64, ch as f64);
        if let Some(background) = self.colour_of(w, "background")? {
            let mut draw = Draw::new(&mut layer);
            draw.rectangle(
                (0.0, 0.0, wf - 1.0, hf - 1.0),
                Some(ink(background)),
                None,
                1,
            )?;
        }
        let grid = widget::int(w, "grid", 0);
        if grid > 0 {
            if let Some(colour) = self.colour_of(w, "grid_color")? {
                let mut draw = Draw::new(&mut layer);
                let width = (s / 2).max(1) as i32;
                for i in 1..=grid {
                    let gy = lp_py::round(hf * i as f64 / (grid + 1) as f64);
                    draw.line(
                        &[(0.0, gy), (wf - 1.0, gy)],
                        Some(ink(colour)),
                        width,
                        false,
                    )?;
                }
            }
        }
        let numbers: Vec<f64> = points.iter().filter_map(|p| p.1).collect();
        if numbers.len() < 2 {
            return Ok(layer);
        }
        let range = match range {
            Some(range) => range.clone(),
            None => bounds(w, &numbers)?,
        };
        let (first, last) = range_rows(w, s);
        let scale = widget::text(w, "scale");
        let down = widget::text(w, "direction") == "down";
        let mut runs: Vec<Vec<(f64, f64)>> = vec![vec![]];
        for &(px, v) in points {
            let Some(v) = v else {
                if runs.last().is_some_and(|run| !run.is_empty()) {
                    runs.push(vec![]);
                }
                continue;
            };
            let py = first + (last - first) * (1.0 - fraction(v, &range, scale)?);
            let py = if down { first + last - py } else { py };
            if let Some(run) = runs.last_mut() {
                run.push((px, py));
            }
        }
        runs.retain(|run| run.len() >= 2);
        if widget::truthy(w, "smooth") {
            // (min(last, max(first, py)), as Python's min and max take them)
            let within = |py: f64| py_min(last, py_max(first, py));
            runs = runs
                .iter()
                .map(|run| {
                    let smooth = catmull_rom(run).into_iter();
                    smooth.map(|(px, py)| (px, within(py))).collect()
                })
                .collect();
        }
        let colour = self.colour_of(w, "color")?.unwrap_or([0; 4]);
        if widget::truthy(w, "fill") && !runs.is_empty() {
            layer = self.graph_fill(w, &layer, &runs, colour, (first, last), down)?;
        }
        if finish::of(w) == Finish::LitGraph {
            lit_curve(w, &mut layer, &runs, colour, s)?;
            return Ok(layer);
        }
        let width = widget::int(w, "line_width", 2).saturating_mul(s) as i32;
        let mut draw = Draw::new(&mut layer);
        for run in &runs {
            draw.line(run, Some(ink(colour)), width, true)?;
        }
        Ok(layer)
    }

    /// _curve's fill: the area under each run (above it where the graph hangs from the top) in
    /// the line's colour, fading from the highest point (or the range's top) to the bottom, or
    /// flat, laid over `layer`.
    fn graph_fill(
        &self,
        w: &Dict,
        layer: &Image,
        runs: &[Vec<(f64, f64)>],
        colour: Rgba,
        (first, last): (f64, f64),
        down: bool,
    ) -> Result<Image> {
        let (cw, ch) = layer.size();
        let hf = ch as f64;
        let mut area = Image::blank(Mode::L, cw, ch);
        let base = if down {
            0.0
        } else if widget::flag(w, "inset", true) {
            hf - 1.0
        } else {
            last
        };
        for run in runs {
            let mut polygon = run.clone();
            polygon.push((run[run.len() - 1].0, base));
            polygon.push((run[0].0, base));
            Draw::new(&mut area).polygon(&polygon, Some([255, 0, 0, 0]), None, 1)?;
        }
        // (min(1.0, max(0.0, fill_alpha)): a NaN is 0)
        let solid = py_min(1.0, py_max(0.0, widget::number(w, "fill_alpha", 0.0)));
        let mut alpha = if widget::truthy(w, "fill_fade") {
            let peak = if solid != 0.0 {
                lp_py::round(255.0 * solid)
            } else {
                115.0
            };
            let top = if widget::truthy(w, "range_step") {
                first.trunc()
            } else {
                // (int(first + last - max(ys) if down else min(ys)): Python's max and min, the
                // first of the greatest or least; NaN where the first is NaN, and int() fails)
                let ys: Vec<f64> = runs.iter().flatten().map(|p| p.1).collect();
                int_of(if down {
                    first + last - most(&ys)
                } else {
                    least(&ys)
                })?
            };
            let span = (hf - top).max(1.0);
            let mut rows: Vec<u8> = (0..ch)
                .map(|row| {
                    let share = 1.0 - (row as f64 - top) / span;
                    (peak * share.clamp(0.0, 1.0)) as u8
                })
                .collect();
            if down {
                rows.reverse();
            }
            let fade = Image::from_bytes(Mode::L, 1, ch, rows)?;
            chops::multiply(&area, &fade.resize(cw, ch, Filter::Bicubic, None, None)?)?
        } else {
            let flat = if solid != 0.0 {
                lp_py::round(255.0 * solid) as i64
            } else {
                64
            };
            area.point(&(0..256).map(|v| v * flat / 255).collect::<Vec<i64>>())?
        };
        let [r, g, b, a] = ink(colour);
        let mut tint = Image::new(Mode::RGBA, cw, ch, &[r, g, b, 255]);
        if a < 255 {
            alpha = chops::multiply(&alpha, &Image::new(Mode::L, cw, ch, &[a]))?;
        }
        tint.putalpha(&alpha)?;
        Ok(lp_pixels::alpha_composite(layer, &tint)?)
    }

    /// _draw_end_dot in a still frame: a dot of `end_dot` px radius in the line's colour where
    /// the line ends, at the right edge, at the newest value of the history (in the range the
    /// graph keeps, else its values'), with the widget's glow; none where the line has no end.
    pub(crate) fn graph_dot(&self, w: &Dict, snapshot: &Snapshot) -> Result<Option<Over>> {
        let (values, _) = history_of(snapshot, w);
        let value = if values.len() > 1 {
            values[values.len() - 1]
        } else {
            None
        };
        let known: Vec<f64> = values.iter().flatten().copied().collect();
        let mut range = if !known.is_empty() && values.len() > 1 {
            Some(bounds(w, &known)?)
        } else {
            None
        };
        if range.is_some() {
            if let Some(kept) = self.range_now(w) {
                range = Some(kept);
            }
        }
        let (Some(value), Some(range)) = (value, range) else {
            return Ok(None);
        };
        let s = SUPERSAMPLE as i64;
        let (first, last) = range_rows(w, s);
        let frac = fraction(value, &range, widget::text(w, "scale"))?;
        let mut row = first + (last - first) * (1.0 - frac);
        if widget::text(w, "direction") == "down" {
            row = first + last - row;
        }
        let (x, y, gw) = (
            widget::int(w, "x", 0),
            widget::int(w, "y", 0),
            widget::int(w, "w", 200),
        );
        let cx = x.saturating_add(gw) as f64 - 0.5 / s as f64;
        let cy = y as f64 + (row + 0.5) / s as f64;
        let colour = self.colour_of(w, "color")?.unwrap_or([0; 4]);
        self.dot(w, cx, cy, colour).map(Some)
    }
}

// -- the lit finish's line --------------------------------------------------------------------

/// A lit graph's halo at glow 1, and how far its line runs towards white (lit.HALO, LINE_WHITE).
const LIT_HALO: f64 = 0.9;
const LINE_WHITE: f64 = 0.45;

/// lit._scale: a table that scales a level by `factor` (Python's round, at most 255).
fn scale_table(factor: f64) -> Vec<i64> {
    (0..256)
        .map(|v| (lp_py::round(v as f64 * factor) as i64).min(255))
        .collect()
}

/// _lay_halo: `colour` through the soft halo of `mask` onto `layer`: worked out at 1/`q` of
/// the size, blurred by `radius`, scaled by `share`, laid only where it shows (a pixel more
/// round it), over what is there.
fn lay_halo(
    layer: &mut Image,
    mask: &Image,
    q: usize,
    radius: f64,
    share: f64,
    colour: [u8; 4],
) -> Result<()> {
    let small = mask.reduce(q, q, None)?;
    let r = radius as f32;
    let halo = small.gaussian_blur(r, r)?.point(&scale_table(share))?;
    let Some((bx0, by0, bx1, by1)) = halo.getbbox(true) else {
        return Ok(());
    };
    let (x0, y0) = (bx0.saturating_sub(1), by0.saturating_sub(1));
    let (x1, y1) = ((bx1 + 1).min(halo.width()), (by1 + 1).min(halo.height()));
    let (left, top) = (x0 * q, y0 * q);
    let size = (
        layer.width().min(x1 * q) - left,
        layer.height().min(y1 * q) - top,
    );
    let qf = q as f64;
    let source = (
        x0 as f64,
        y0 as f64,
        x0 as f64 + size.0 as f64 / qf,
        y0 as f64 + size.1 as f64 / qf,
    );
    let big = halo.resize(size.0, size.1, Filter::Bilinear, Some(source), None)?;
    let mut glow = Image::new(
        Mode::RGBA,
        size.0,
        size.1,
        &[colour[0], colour[1], colour[2], 0],
    );
    glow.putalpha(&big)?;
    let whole = (0, 0, size.0 as i64, size.1 as i64);
    layer.alpha_composite_at(&glow, (left as i64, top as i64), whole)?;
    Ok(())
}

/// LitGraph.curve: the line as light: a halo of the widget's glow under it (worked out at a
/// quarter of the size), the line itself towards white; see-through, laid through its mask.
fn lit_curve(
    w: &Dict,
    layer: &mut Image,
    runs: &[Vec<(f64, f64)>],
    colour: Rgba,
    s: i64,
) -> Result<()> {
    let width = widget::int(w, "line_width", 2).saturating_mul(s) as i32;
    let rgb = crate::light::mix(
        [255, 255, 255],
        [colour[0], colour[1], colour[2]],
        LINE_WHITE,
    );
    let core = ink([rgb[0], rgb[1], rgb[2], colour[3]]);
    let strength = widget::number(w, finish::HALO, 0.0);
    let (cw, ch) = layer.size();
    let mask = if strength > 0.0 || colour[3] < 255 {
        let mut m = Image::blank(Mode::L, cw, ch);
        let mut draw = Draw::new(&mut m);
        for run in runs {
            draw.line(run, Some([255, 0, 0, 0]), width, true)?;
        }
        Some(m)
    } else {
        None
    };
    if let (true, Some(m)) = (strength > 0.0, &mask) {
        let radius = 0.5f64.max(widget::number(w, "glow_radius", 10.0) / 4.0);
        let share = py_min(1.0, LIT_HALO * strength);
        lay_halo(layer, m, 4 * s as usize, radius, share, ink(colour))?;
    }
    match (colour[3] < 255, &mask) {
        (true, Some(m)) => {
            let solid = Image::new(Mode::RGBA, cw, ch, &core);
            let through = m.point(&scale_table(colour[3] as f64 / 255.0))?;
            layer.paste(&solid, 0, 0, Some(&through))?;
        }
        _ => {
            let mut draw = Draw::new(layer);
            for run in runs {
                draw.line(run, Some(core), width, true)?;
            }
        }
    }
    Ok(())
}
