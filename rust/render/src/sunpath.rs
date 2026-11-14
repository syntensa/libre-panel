//! The sun's way over the sky today (render/sunpath.py, the sunpath widget): an arc over the
//! horizon from sunrise to sunset, the part of the day gone in the sun's light and the part to
//! come dotted, the sun where it stands, glowing; by night the arc dim and the moon's crescent
//! over it. Drawn four times larger and reduced; the sun's light worked out in numpy's float32.

use std::f64::consts::PI;

use lp_pixels::{alpha_composite, Draw, Image, Mode};
use lp_py::{Dict, Value};

use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Renderer, MOST_PIXELS};
use crate::shapes::Rgba;
use crate::widget;

const OVER: i64 = 4;
/// The day in steps: the sun moves one about every minute and a half.
pub const STEPS: i64 = 500;

/// round(x) of a float, as a float (Python's errors for NaN and the infinities).
fn whole(x: f64) -> Result<f64> {
    if x.is_nan() {
        return Err(Failed("cannot convert float NaN to integer".into()));
    }
    if x.is_infinite() {
        return Err(Failed("cannot convert float infinity to integer".into()));
    }
    Ok(lp_py::round(x))
}

/// Python's max(a, b) of two floats: b only where b > a.
fn max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python's min(a, b) of two floats: b only where b < a.
fn min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// step: the step of the day `progress` (0 at sunrise, 1 at sunset) is in; None by night (or
/// for what float() does not read).
pub fn step(progress: &Value) -> Result<Option<i64>> {
    let p = match progress {
        Value::Str(s) => match lp_py::float_of_str(s) {
            Some(p) => p,
            None => return Ok(None),
        },
        Value::Bool(_) | Value::Int(_) | Value::Float(_) => {
            // (float() of an int past a float's: OverflowError, which step does not catch)
            lp_py::num::to_float(progress).map_err(|e| Failed(e.msg))?
        }
        _ => return Ok(None),
    };
    if !p.is_finite() || !(0.0..=1.0).contains(&p) {
        return Ok(None);
    }
    Ok(Some(whole(p * STEPS as f64)? as i64))
}

/// _Way: where the arc lies on a picture of w x h.
struct Way {
    sun: f64,
    line: f64,
    base: f64,
    a: f64,
    b: f64,
    cx: f64,
}

impl Way {
    fn new(w: i64, h: i64, thickness: f64) -> Way {
        let (w, h) = (w as f64, h as f64);
        let sun = max(2.5, min(h * 0.13, w * 0.05));
        let line = max(1.0, thickness);
        let base = h - max(line, 1.0) - 0.5;
        Way {
            sun,
            line,
            base,
            a: w / 2.0 - sun - 1.0,
            b: base - sun * 1.6,
            cx: w / 2.0,
        }
    }

    /// The point of the way at `p` of the day (0: sunrise, at the left).
    fn at(&self, p: f64) -> (f64, f64) {
        let angle = PI * (1.0 - p);
        (
            self.cx + self.a * angle.cos(),
            self.base - self.b * angle.sin(),
        )
    }
}

/// _rgba: the colour at `alpha` of its own alpha.
fn rgba(colour: Rgba, alpha: f64) -> Result<[u8; 4]> {
    let a = whole(255.0 * alpha * colour[3] as f64 / 255.0)?;
    Ok([
        colour[0].clamp(0, 255) as u8,
        colour[1].clamp(0, 255) as u8,
        colour[2].clamp(0, 255) as u8,
        a.clamp(0.0, 255.0) as u8,
    ])
}

/// A picture of the sun's way, `OVER` times w x h.
fn big(mode: Mode, w: i64, h: i64) -> Result<Image> {
    let (bw, bh) = (w.saturating_mul(OVER), h.saturating_mul(OVER));
    if bw < 0 || bh < 0 || bw.saturating_mul(bh) > MOST_PIXELS {
        return Err(Failed("a sun's way too large".into()));
    }
    Ok(Image::blank(mode, bw as usize, bh as usize))
}

/// _reduced: the picture four times larger reduced, premultiplied.
fn reduced(image: &Image) -> Result<Image> {
    let o = OVER as usize;
    Ok(image
        .convert(Mode::RGBa)?
        .reduce(o, o, None)?
        .convert(Mode::RGBA)?)
}

/// A line's width (the C int it is passed as).
fn width_of(w: f64) -> Result<i32> {
    i32::try_from(w as i64).map_err(|_| Failed("signed integer is greater than maximum".into()))
}

/// ground: the horizon (fading out at its ends) and the day's way, dotted, in `track`.
pub fn ground(w: i64, h: i64, thickness: f64, track: Rgba) -> Result<Image> {
    let way = Way::new(w, h, thickness);
    let mut image = big(Mode::RGBA, w, h)?;
    let o = OVER as f64;
    let (wf, steps) = (w as f64, 24);
    let mut draw = Draw::new(&mut image);
    // the horizon: a hairline that fades towards its ends
    let y = way.base * o;
    let line = width_of(max(1.0, whole(way.line * 0.7 * o)?))?;
    for i in 0..steps {
        let (u0, u1) = (i as f64 / steps as f64, (i + 1) as f64 / steps as f64);
        let fade = (PI * (u0 + u1) / 2.0).sin().powf(0.6);
        let ends = [(u0 * wf * o, y), (u1 * wf * o, y)];
        draw.line(&ends, Some(rgba(track, 0.9 * fade)?), line, false)?;
    }
    // the way: dots a few line widths apart
    let length = PI * (way.a + way.b) / 2.0;
    let count = max(6.0, whole(length / (way.line * 3.2))?) as i64;
    let r = way.line * 0.62 * o;
    let fill = rgba(track, 1.0)?;
    for i in 0..=count {
        let (px, py) = way.at(i as f64 / count as f64);
        let bounds = (px * o - r, py * o - r, px * o + r, py * o + r);
        draw.ellipse(bounds, Some(fill), None, 1)?;
    }
    reduced(&image)
}

/// The sun's light where it stands, at (`sx`, `sy`), as numpy works it out in float32: a soft
/// corona, a disc lighter at its top left.
fn sun_light(w: usize, h: usize, way: &Way, at: (f64, f64), colour: Rgba) -> Result<Image> {
    let f = |x: f64| x as f32;
    let (sx, sy) = (f(at.0), f(at.1));
    let sun = f(way.sun);
    let wide = f(way.sun * 2.4);
    let edge = f(way.sun + 0.5);
    let top = f(at.1 - way.sun);
    let across = f(2.0 * way.sun);
    // (_lighter(colour, 0.35): each band 35 % of the way to white, rounded)
    let lighter = |c: i64| lp_py::round(c as f64 + (255 - c) as f64 * 0.35) as f32;
    let light = [0, 1, 2].map(|i| lighter(colour[i]) / 255.0);
    let base = [0, 1, 2].map(|i| colour[i] as f32 / 255.0);
    let mut data = Vec::with_capacity(w * h * 4);
    for row in 0..h {
        let ys = row as f32 + 0.5;
        let t = ((ys - top) / across).clamp(0.0, 1.0);
        for column in 0..w {
            let xs = column as f32 + 0.5;
            let d = (xs - sx).hypot(ys - sy);
            let fall = (1.0 - (d - sun) / wide).clamp(0.0, 1.0);
            let corona = 0.55f32 * (fall * fall);
            let disc = (edge - d).clamp(0.0, 1.0);
            let byte = |v: f32| (v * 255.0 + 0.5).clamp(0.0, 255.0) as u8;
            for c in 0..3 {
                let rgb = (light[c] * (1.0 - t) + base[c] * t) * disc + light[c] * (1.0 - disc);
                data.push(byte(rgb));
            }
            // (np.maximum: the larger; NaN where either is)
            data.push(byte(if disc >= corona { disc } else { corona }));
        }
    }
    Ok(Image::from_bytes(Mode::RGBA, w, h, data)?)
}

/// day: the day gone (sunrise to `progress`) as a line from `deep` to `colour` in its own soft
/// light, and the sun where it stands, glowing.
pub fn day(
    w: i64,
    h: i64,
    thickness: f64,
    progress: f64,
    colour: Rgba,
    deep: Rgba,
) -> Result<Image> {
    let way = Way::new(w, h, thickness);
    let mut picture = big(Mode::RGBA, w, h)?;
    let o = OVER as f64;
    let n = max(2.0, whole(48.0 * progress)?) as i64;
    let points: Vec<(f64, f64)> = (0..=n)
        .map(|i| way.at(0.0 + (progress - 0.0) * i as f64 / n as f64))
        .collect();
    let width = max(1.0, whole(way.line * o)?);
    let (line, half) = (width_of(width)?, width / 2.0);
    let mut draw = Draw::new(&mut picture);
    for i in 0..n as usize {
        let t = (i as f64 + 0.5) / n as f64;
        let c = [0, 1, 2].map(|j| {
            let v = lp_py::round(deep[j] as f64 + (colour[j] - deep[j]) as f64 * t);
            v.clamp(0.0, 255.0) as u8
        });
        let fill = [c[0], c[1], c[2], 255];
        let ((x0, y0), (x1, y1)) = (points[i], points[i + 1]);
        draw.line(
            &[(x0 * o, y0 * o), (x1 * o, y1 * o)],
            Some(fill),
            line,
            false,
        )?;
        let bounds = (x1 * o - half, y1 * o - half, x1 * o + half, y1 * o + half);
        draw.ellipse(bounds, Some(fill), None, 1)?;
    }
    let (x0, y0) = points[0];
    let bounds = (x0 * o - half, y0 * o - half, x0 * o + half, y0 * o + half);
    draw.ellipse(bounds, Some(rgba(deep, 1.0)?), None, 1)?;
    let line = reduced(&picture)?;
    // its light: the line blurred, under it
    let radius = max(1.0, way.line * 1.6) as f32;
    let mut glow = line.gaussian_blur(radius, radius)?;
    let lut: Vec<i64> = (0..256).map(|v| v * 3 / 5).collect();
    let alpha = glow.getchannel(3)?.point(&lut)?;
    glow.putalpha(&alpha)?;
    let (uw, uh) = (line.width(), line.height());
    let mut out = Image::blank(Mode::RGBA, uw, uh);
    out = alpha_composite(&out, &glow)?;
    out = alpha_composite(&out, &line)?;
    let sun = sun_light(uw, uh, &way, way.at(progress), colour)?;
    Ok(alpha_composite(&out, &sun)?)
}

/// night: the moon's crescent over the middle of the way, in `colour`, softly lit.
pub fn night(w: i64, h: i64, thickness: f64, colour: Rgba) -> Result<Image> {
    let way = Way::new(w, h, thickness);
    let (x, y) = way.at(0.5);
    let r = way.sun * 1.05;
    let mut picture = big(Mode::L, w, h)?;
    let o = OVER as f64;
    let (big_r, cx, cy) = (r * o, x * o, y * o);
    let mut draw = Draw::new(&mut picture);
    draw.ellipse(
        (cx - big_r, cy - big_r, cx + big_r, cy + big_r),
        Some([255, 0, 0, 0]),
        None,
        1,
    )?;
    draw.ellipse(
        (
            cx - 0.35 * big_r,
            cy - 1.15 * big_r,
            cx + 1.65 * big_r,
            cy + 0.85 * big_r,
        ),
        Some([0, 0, 0, 0]),
        None,
        1,
    )?;
    let o = OVER as usize;
    let mask = picture.reduce(o, o, None)?;
    let (uw, uh) = (mask.width(), mask.height());
    let mut halo = Image::new(Mode::RGBA, uw, uh, &rgba(colour, 0.35)?);
    let radius = max(1.0, r * 0.8) as f32;
    let lut: Vec<i64> = (0..256).map(|v| v / 3).collect();
    halo.putalpha(&mask.gaussian_blur(radius, radius)?.point(&lut)?)?;
    let mut moon = Image::new(Mode::RGBA, uw, uh, &rgba(colour, 1.0)?);
    moon.putalpha(&mask)?;
    let out = alpha_composite(&Image::blank(Mode::RGBA, uw, uh), &halo)?;
    Ok(alpha_composite(&out, &moon)?)
}

/// What a sun's way is drawn of, as _draw_sunpath works it out: the step of the day (None by
/// night), its size, where it lies, its line's thickness and its colours.
pub(crate) struct Sunpath {
    at: Option<i64>,
    w: i64,
    h: i64,
    x: i64,
    y: i64,
    thick: f64,
    colour: Rgba,
    deep: Rgba,
    track: Rgba,
    moon: Rgba,
}

impl Renderer {
    /// _draw_sunpath's sizes and colours: the widget's, else the sun's own.
    pub(crate) fn sunpath_of(&self, w: &Dict, at: Option<i64>) -> Result<Sunpath> {
        let colour = self.colour_of(w, "color")?.unwrap_or([245, 196, 107, 255]);
        let deep = match self.colour_of(w, "color2")? {
            Some(deep) => deep,
            // (the colour, deeper)
            None => {
                let to = [60.0, 24.0, 8.0];
                let band = |i: usize| lp_py::round(colour[i] as f64 * 0.62 + to[i] * 0.38) as i64;
                [band(0), band(1), band(2), 255]
            }
        };
        let track = self
            .colour_of(w, "background")?
            .unwrap_or([42, 54, 68, 255]);
        let moon = self.colour_of(w, "night")?.unwrap_or([201, 211, 222, 255]);
        let thickness = w.get("thickness").cloned().unwrap_or(Value::int(2));
        let thick = lp_py::num::to_float(&thickness).map_err(|e| Failed(e.msg))?;
        Ok(Sunpath {
            at,
            w: widget::int(w, "w", 160).max(8),
            h: widget::int(w, "h", 44).max(6),
            x: widget::int(w, "x", 0),
            y: widget::int(w, "y", 0),
            thick: max(1.0, thick),
            colour,
            deep,
            track,
            moon,
        })
    }
}

impl Sunpath {
    /// What the piece is kept by.
    pub(crate) fn content(&self) -> Value {
        let colour = |c: Rgba| Value::List(c.map(Value::int).to_vec());
        Value::List(vec![
            self.at.map_or(Value::None, Value::int),
            colour(self.colour),
            colour(self.deep),
            colour(self.track),
            colour(self.moon),
            Value::int(self.w),
            Value::int(self.h),
            Value::Float(self.thick),
        ])
    }

    /// The way on its ground: the day gone and the sun, or the night's moon.
    pub(crate) fn piece(&self) -> Result<Piece> {
        let (w, h, thick) = (self.w, self.h, self.thick);
        let image = ground(w, h, thick, self.track)?;
        let over = match self.at {
            None => night(w, h, thick, self.moon)?,
            Some(at) => day(
                w,
                h,
                thick,
                at as f64 / STEPS as f64,
                self.colour,
                self.deep,
            )?,
        };
        let mut piece = Piece::new(alpha_composite(&image, &over)?, self.x, self.y);
        piece.hit = Some([self.x, self.y, w, h]);
        Ok(piece)
    }
}
