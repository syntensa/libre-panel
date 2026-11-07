//! Bars (render/renderer.py's _draw_bar, _bar_piece, _draw_mark, _draw_bar_dot and _dot) in a
//! still frame: where the reading stands between min and max (linear, its square root or
//! logarithmic), in steps of the supersampled grid, drawn three times larger and reduced: the
//! track, the filled part (one colour, a gradient across the whole track, a threshold's
//! colour) or segments (the last lit one partly, rising as levels on a mixer); a thin mark
//! where a second reading stands, a dot at the bar's head.

use lp_pixels::{round_half_even, Draw, Image, Mode, ALL_CORNERS};
use lp_py::{Dict, Value};

use crate::effects;
use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Renderer, MOST_PIXELS};
use crate::shapes::{down, ink, lerp, Rgba, SUPERSAMPLE};
use crate::snapshot::{number, Snapshot};
use crate::widget;

/// A bar's colours: where it starts, and where a full bar ends (None: one colour).
pub(crate) type Paint = (Rgba, Option<Rgba>);

/// What lies over a bar (its mark, its dot): the dict its effects come from, what it shows,
/// and the piece.
pub(crate) type Over = (Dict, Value, Piece);

/// What a bar or a ring shows, as the piece is kept by: where it stands, its colours.
pub(crate) fn content(frac: f64, paint: Paint) -> Value {
    let colours = |c: Option<Rgba>| match c {
        Some(c) => Value::List(c.map(Value::int).to_vec()),
        None => Value::None,
    };
    Value::List(vec![
        Value::Float(frac),
        colours(Some(paint.0)),
        colours(paint.1),
    ])
}

/// Python's floor division by 2.
fn half(n: i64) -> i64 {
    n.div_euclid(2)
}

/// _fraction: where `value` stands between `lo` and `hi`, from 0 to 1: a square root and a
/// logarithm keep small values to be seen on huge ranges (network rates).
pub(crate) fn fraction(value: Option<f64>, lo: f64, hi: f64, scale: &str) -> f64 {
    let Some(value) = value else {
        return 0.0;
    };
    if hi == lo {
        return 0.0;
    }
    // (min(1.0, max(0.0, x)): a NaN is 0)
    let at = (value - lo) / (hi - lo);
    let linear = if at.is_nan() { 0.0 } else { at.clamp(0.0, 1.0) };
    match scale {
        "sqrt" => linear.sqrt(),
        "log" => {
            let span = (hi - lo).abs();
            (linear * span).ln_1p() / span.ln_1p()
        }
        _ => linear,
    }
}

/// _steps: `frac` of `length` pixels in steps of the supersampled grid.
pub(crate) fn steps(frac: f64, length: i64) -> Result<f64> {
    let steps = length.saturating_mul(SUPERSAMPLE as i64).max(1) as f64;
    let at = frac * steps;
    if !at.is_finite() {
        return Err(Failed("cannot convert float NaN to integer".into()));
    }
    Ok(round_half_even(at) as f64 / steps)
}

/// _gradient_colors: `n` colours from `a` to `b` (all `a` where there is no `b`).
fn gradient_colours(a: Rgba, b: Option<Rgba>, n: usize) -> Vec<Rgba> {
    match b {
        None => vec![a; n],
        Some(b) => (0..n)
            .map(|i| lerp(a, b, i as f64 / (n.saturating_sub(1)).max(1) as f64))
            .collect(),
    }
}

impl Renderer {
    /// _paint: a bar's colours: a threshold's colour plain, else `color` (to `color2`).
    fn paint(&self, w: &Dict, rule: Option<&str>) -> Result<Paint> {
        if let Some(rule) = rule.filter(|r| !r.is_empty()) {
            let colour = self.color(Some(rule), 1.0)?.unwrap_or([0, 0, 0, 0]);
            return Ok((colour, None));
        }
        let own = self.colour_of(w, "color")?.unwrap_or([0, 0, 0, 0]);
        Ok((own, self.colour_of(w, "color2")?))
    }

    /// _rule_paint in a still frame: the bar's own colours, or the highest threshold's that
    /// `value` is above.
    pub(crate) fn rule_paint(
        &self,
        w: &Dict,
        value: Option<f64>,
        rules: &[(Value, String)],
    ) -> Result<Paint> {
        let step = match value {
            None => 0,
            Some(v) => rules
                .iter()
                .filter(|(above, _)| lp_py::num::gt(&Value::Float(v), above).unwrap_or(false))
                .count(),
        };
        if step == 0 {
            return self.paint(w, None);
        }
        self.paint(w, Some(&rules[step.min(rules.len()) - 1].1))
    }

    /// _bar_piece: the bar at `frac` in `paint`, drawn three times larger and reduced.
    pub(crate) fn bar_piece(&self, w: &Dict, frac: f64, paint: Paint) -> Result<Piece> {
        let s = SUPERSAMPLE as i64;
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (bw, bh) = (widget::int(w, "w", 200), widget::int(w, "h", 16));
        let (lw, lh) = (bw.saturating_mul(s).max(1), bh.saturating_mul(s).max(1));
        if lw.saturating_mul(lh) > MOST_PIXELS {
            return Err(Failed("a layer too large".into()));
        }
        let mut layer = Image::blank(Mode::RGBA, lw as usize, lh as usize);
        let (big_w, big_h) = ((bw * s) as f64, (bh * s) as f64);
        let radius = (widget::int(w, "radius", 4).min(half(bw)).min(half(bh)) * s) as f64;
        let direction = widget::text(w, "direction");
        let horizontal = direction == "right" || direction == "left";
        let (a, b) = paint;
        let track = self.colour_of(w, "background")?;
        // the part p0..p1 of the bar (0 its start, 1 its end); the smallest stays a sliver
        let span = |p0: f64, p1: f64| -> (f64, f64, f64, f64) {
            let (x0, y0, x1, y1) = match direction {
                "right" => (p0 * big_w, 0.0, p1 * big_w - 1.0, big_h - 1.0),
                "left" => (
                    (1.0 - p1) * big_w,
                    0.0,
                    (1.0 - p0) * big_w - 1.0,
                    big_h - 1.0,
                ),
                "down" => (0.0, p0 * big_h, big_w - 1.0, p1 * big_h - 1.0),
                _ => (
                    0.0,
                    (1.0 - p1) * big_h,
                    big_w - 1.0,
                    (1.0 - p0) * big_h - 1.0,
                ),
            };
            (x0, y0, x1.max(x0), y1.max(y0))
        };
        let rect = |layer: &mut Image, bounds, r: f64, fill: Rgba| -> Result<()> {
            Draw::new(layer).rounded_rectangle(bounds, r, Some(ink(fill)), None, 1, ALL_CORNERS)?;
            Ok(())
        };
        let n = widget::int(w, "segments", 0);
        if n > 0 {
            let along = if horizontal { big_w } else { big_h };
            if along == 0.0 {
                return Err(Failed("float division by zero".into()));
            }
            let gap = (widget::int(w, "segment_gap", 2) * s) as f64 / along;
            let cell = (1.0 - gap * (n - 1) as f64) / n as f64;
            let colours = gradient_colours(a, b, n as usize);
            let rising = widget::number(w, "rising", 0.0);
            for (i, colour) in colours.iter().enumerate() {
                let start = i as f64 * (cell + gap);
                let (x0, mut y0, mut x1, y1) = span(start, start + cell);
                if rising > 0.0 {
                    // levels on a mixer: standing on the bottom (the left) edge
                    let share = rising + (1.0 - rising) * i as f64 / (n - 1).max(1) as f64;
                    if horizontal {
                        y0 = y1 - (s as f64).max(share * big_h);
                    } else {
                        x1 = x0 + (s as f64).max(share * big_w);
                    }
                }
                let r = radius.min((x1 - x0) / 2.0).min((y1 - y0) / 2.0);
                // (frac is a number: steps refused NaN)
                let amount = (frac * n as f64 - i as f64).clamp(0.0, 1.0);
                if let Some(track) = track.filter(|_| amount < 1.0) {
                    rect(&mut layer, (x0, y0, x1, y1), r, track)?;
                }
                if amount > 0.0 {
                    let alpha = (colour[3] as f64 * amount) as i64;
                    rect(
                        &mut layer,
                        (x0, y0, x1, y1),
                        r,
                        [colour[0], colour[1], colour[2], alpha],
                    )?;
                }
            }
        } else {
            if let Some(track) = track {
                rect(
                    &mut layer,
                    (0.0, 0.0, big_w - 1.0, big_h - 1.0),
                    radius,
                    track,
                )?;
            }
            if frac > 0.0 {
                let (x0, y0, x1, y1) = span(0.0, frac);
                let r = radius.min((x1 - x0) / 2.0).min((y1 - y0) / 2.0);
                match b {
                    None => rect(&mut layer, (x0, y0, x1, y1), r, a)?,
                    Some(b) => {
                        // (the gradient spans the whole track: a colour means the same value)
                        let (first, last) = if direction == "left" || direction == "up" {
                            (b, a)
                        } else {
                            (a, b)
                        };
                        self.fill_shape(
                            &mut layer,
                            (x0, y0, x1, y1),
                            r,
                            first,
                            Some(last),
                            horizontal,
                        )?;
                    }
                }
            }
        }
        let mut piece = Piece::new(down(&layer, bw, bh, SUPERSAMPLE)?, x, y);
        piece.hit = Some([x, y, bw, bh]);
        Ok(piece)
    }

    /// _draw_mark: a thin line across the bar where the reading `mark` stands on the bar's own
    /// scale, reaching a little beyond it on both sides; none where it has no value. Its
    /// place, size and colour, and the dict its effects come from (its opacity).
    pub(crate) fn bar_mark(&self, w: &Dict, snapshot: &Snapshot) -> Result<Option<Over>> {
        let Some(value) = number(&snapshot.value(widget::text(w, "mark"))) else {
            return Ok(None);
        };
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (bw, bh) = (widget::int(w, "w", 200), widget::int(w, "h", 16));
        let direction = widget::text(w, "direction");
        let horizontal = direction == "right" || direction == "left";
        let (length, across) = if horizontal { (bw, bh) } else { (bh, bw) };
        let thick = widget::int(w, "mark_width", 2).min(length).max(1);
        let (lo, hi) = (
            widget::number(w, "min", 0.0),
            widget::number(w, "max", 100.0),
        );
        let frac = fraction(Some(value), lo, hi, widget::text(w, "scale"));
        let mut along = ((length - thick) as f64 * frac) as i64;
        if direction == "left" || direction == "up" {
            along = length - thick - along; // (these start at the far end)
        }
        let reach = round_half_even(across as f64 * 0.22);
        let colour = self.colour_of(w, "mark_color")?.unwrap_or([0, 0, 0, 0]);
        let (at, size) = if horizontal {
            ((x + along, y - reach), (thick, bh + 2 * reach))
        } else {
            ((x - reach, y + along), (bw + 2 * reach, thick))
        };
        if size.0 < 0 || size.1 < 0 || size.0.saturating_mul(size.1) > MOST_PIXELS {
            return Err(Failed("a mark of no size".into()));
        }
        let layer = Image::new(Mode::RGBA, size.0 as usize, size.1 as usize, &ink(colour));
        let mut marked = Dict::new();
        marked.insert(
            "id".into(),
            Value::Str(format!("{}\0mark", widget::text(w, "id"))),
        );
        marked.insert(
            "opacity".into(),
            w.get("opacity").cloned().unwrap_or(Value::Float(1.0)),
        );
        let content = Value::List(
            [
                at.0, at.1, size.0, size.1, colour[0], colour[1], colour[2], colour[3],
            ]
            .map(Value::int)
            .to_vec(),
        );
        Ok(Some((marked, content, Piece::new(layer, at.0, at.1))))
    }

    /// _draw_bar_dot and _dot: a dot of `end_dot` px radius in the bar's colour at its head
    /// (the middle of its last pixel), placed in quarter pixels, with the widget's glow; none
    /// for an empty bar. The dict its effects come from (its opacity) with it.
    pub(crate) fn bar_dot(
        &self,
        w: &Dict,
        snapshot: &Snapshot,
        rules: &[(Value, String)],
    ) -> Result<Option<Over>> {
        let value = number(&snapshot.value(widget::text(w, "sensor")));
        let (lo, hi) = (
            widget::number(w, "min", 0.0),
            widget::number(w, "max", 100.0),
        );
        let frac = fraction(value, lo, hi, widget::text(w, "scale"));
        let (x, y) = (widget::int(w, "x", 0) as f64, widget::int(w, "y", 0) as f64);
        let (bw, bh) = (
            widget::int(w, "w", 200) as f64,
            widget::int(w, "h", 16) as f64,
        );
        let direction = widget::text(w, "direction");
        let length = if direction == "right" || direction == "left" {
            bw
        } else {
            bh
        };
        if value.is_none() || frac * length < 0.5 {
            return Ok(None);
        }
        let reach = frac * length - 0.5;
        let cx = match direction {
            "right" => x + reach,
            "left" => x + bw - reach,
            _ => x + bw / 2.0,
        };
        let cy = match direction {
            "down" => y + reach,
            "up" => y + bh - reach,
            _ => y + bh / 2.0,
        };
        let (start, end) = self.rule_paint(w, value, rules)?;
        let colour = match end {
            None => start,
            Some(end) => lerp(start, end, frac),
        };
        // _dot
        let radius = widget::number(w, "end_dot", 0.0);
        let (left, top) = (cx - radius - 1.0, cy - radius - 1.0);
        let (ix, iy) = (left.floor(), top.floor());
        let px = (((left - ix) * 4.0) as i64).rem_euclid(4);
        let py = (((top - iy) * 4.0) as i64).rem_euclid(4);
        let size = (2.0 * radius + 3.0).ceil() as i64;
        if size < 0 || (size * 4).saturating_mul(size * 4) > MOST_PIXELS {
            return Err(Failed("a dot of no size".into()));
        }
        let mut big = Image::blank(Mode::RGBA, (size * 4) as usize, (size * 4) as usize);
        let (mx, my) = (
            radius + 1.0 + px as f64 / 4.0,
            radius + 1.0 + py as f64 / 4.0,
        );
        let bounds = (
            (mx - radius) * 4.0,
            (my - radius) * 4.0,
            (mx + radius) * 4.0,
            (my + radius) * 4.0,
        );
        Draw::new(&mut big).ellipse(bounds, Some(ink(colour)), None, 1)?;
        let mut dot = down(&big, size, size, 4)?;
        let mut glow = Dict::new();
        glow.insert(
            "glow".into(),
            w.get("glow").cloned().unwrap_or(Value::Float(0.0)),
        );
        glow.insert(
            "glow_radius".into(),
            w.get("glow_radius").cloned().unwrap_or(Value::int(10)),
        );
        let mut pad = 0;
        if let Some((mut halo, p)) = effects::halo(&glow, &dot, None, self.dither)? {
            halo.alpha_composite_at(
                &dot,
                (p, p),
                (0, 0, dot.width() as i64, dot.height() as i64),
            )?;
            dot = halo;
            pad = p;
        }
        let mut plain = Dict::new();
        plain.insert(
            "id".into(),
            Value::Str(format!("{}\0dot", widget::text(w, "id"))),
        );
        plain.insert(
            "opacity".into(),
            w.get("opacity").cloned().unwrap_or(Value::Float(1.0)),
        );
        let mut look = vec![Value::Float(ix), Value::Float(iy), Value::Float(radius)];
        look.extend(colour.map(Value::int));
        look.extend([Value::int(px), Value::int(py)]);
        look.extend(
            [glow.get("glow"), glow.get("glow_radius")]
                .into_iter()
                .flatten()
                .cloned(),
        );
        let content = Value::List(look);
        Ok(Some((
            plain,
            content,
            Piece::new(dot, ix as i64 - pad, iy as i64 - pad),
        )))
    }
}
