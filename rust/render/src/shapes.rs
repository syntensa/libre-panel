//! What shapes are drawn of (render/renderer.py): layers three times larger reduced to their
//! size, gradients (worked out in floating point and made 8 bits with the dither where asked),
//! and the corners a card shares with every card of its radius.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

use lp_pixels::{dither, round_half_even, Draw, Filter, Image, Mode, ALL_CORNERS};

use crate::piece::{Failed, Result};

/// Shapes are drawn at this scale and reduced for smooth edges.
pub const SUPERSAMPLE: usize = 3;

pub type Rgba = [i64; 4];

/// The colour a picture takes of an RGBA tuple (getink: each value clipped to a byte).
pub fn ink(colour: Rgba) -> [u8; 4] {
    colour.map(|v| v.clamp(0, 255) as u8)
}

/// _lerp: from `a` to `b` by `t` (clamped to 0..1), each band rounded.
pub fn lerp(a: Rgba, b: Rgba, t: f64) -> Rgba {
    let t = t.clamp(0.0, 1.0);
    std::array::from_fn(|i| round_half_even(a[i] as f64 + (b[i] - a[i]) as f64 * t))
}

/// A strip of colours (along x where `horizontal`) stretched to `size` (NEAREST: each row or
/// column the strip's).
fn stretched(colours: &[Rgba], size: (usize, usize), horizontal: bool) -> Result<Image> {
    let bytes: Vec<u8> = colours.iter().flat_map(|&c| ink(c)).collect();
    let (sw, sh) = if horizontal {
        (colours.len(), 1)
    } else {
        (1, colours.len())
    };
    let strip = Image::from_bytes(Mode::RGBA, sw, sh, bytes)?;
    Ok(strip.resize(size.0, size.1, Filter::Nearest, None, None)?)
}

/// _ramp_floats: the colours (steps of 4 floats) from `a` to `b`, each the mean of `sub` as a
/// gradient `sub` times as long has them, in numpy's float32.
pub fn ramp_floats(a: Rgba, b: Rgba, steps: usize, sub: usize) -> Vec<f32> {
    let last = (steps * sub).saturating_sub(1).max(1) as f32;
    let (lo, hi) = (a.map(|v| v as f32), b.map(|v| v as f32));
    let mut out = Vec::with_capacity(steps * 4);
    for i in 0..steps {
        for c in 0..4 {
            // (summed in order, then divided: numpy's mean along the short axis)
            let mut sum = 0.0f32;
            for k in 0..sub {
                let t = ((i * sub + k) as f32 / last).clamp(0.0, 1.0);
                let v = lo[c] + (hi[c] - lo[c]) * t;
                sum = if k == 0 { v } else { sum + v };
            }
            out.push(sum / sub as f32);
        }
    }
    out
}

/// The colours of the theme's background gradient along its `n` rows (or columns), three bands
/// each (_gradient_ground): from `a` to `b` by np.linspace(0, 1, n), which works its steps out in
/// float64 and gives them in float32, then in numpy's float32.
pub fn ground_line(a: [f32; 4], b: [f32; 4], n: usize) -> Vec<f32> {
    let step = if n > 1 { 1.0 / (n - 1) as f64 } else { 0.0 };
    let mut line = Vec::with_capacity(n * 3);
    for i in 0..n {
        let t = if n > 1 && i == n - 1 {
            1.0f32
        } else {
            (i as f64 * step) as f32
        };
        for c in 0..3 {
            line.push(a[c] + (b[c] - a[c]) * t);
        }
    }
    line
}

/// _dithered: a gradient of `size` along `ramp` (steps of 4 floats), made 8 bits with the
/// dither.
fn dithered(size: (usize, usize), ramp: &[f32], horizontal: bool) -> Result<Image> {
    let (w, h) = size;
    let n = ramp.len() / 4;
    let length = if horizontal { h } else { w };
    let bytes = dither::ramp_u8(ramp, n, 4, length, horizontal, true);
    Ok(Image::from_bytes(Mode::RGBA, w, h, bytes)?)
}

/// _gradient: from `a` to `b` across `size` (along x where `horizontal`); with `noise` worked
/// out in floating point and made 8 bits with the dither.
pub fn gradient(
    size: (usize, usize),
    a: Rgba,
    b: Rgba,
    horizontal: bool,
    noise: bool,
) -> Result<Image> {
    let steps = if horizontal { size.0 } else { size.1 };
    if noise {
        return dithered(size, &ramp_floats(a, b, steps, 1), horizontal);
    }
    let last = steps.saturating_sub(1).max(1) as f64;
    let colours: Vec<Rgba> = (0..steps).map(|i| lerp(a, b, i as f64 / last)).collect();
    stretched(&colours, size, horizontal)
}

/// _gradient_down: the colours a gradient three times the size has once reduced; with `noise`
/// made 8 bits with the dither.
pub fn gradient_down(
    size: (usize, usize),
    a: Rgba,
    b: Rgba,
    horizontal: bool,
    noise: bool,
) -> Result<Image> {
    let steps = if horizontal { size.0 } else { size.1 };
    let s = SUPERSAMPLE;
    if noise {
        return dithered(size, &ramp_floats(a, b, steps, s), horizontal);
    }
    let last = (steps * s).saturating_sub(1).max(1) as f64;
    let colours: Vec<Rgba> = (0..steps)
        .map(|i| {
            let parts: Vec<Rgba> = (0..s)
                .map(|k| lerp(a, b, (s * i + k) as f64 / last))
                .collect();
            std::array::from_fn(|c| {
                let sum: i64 = parts.iter().map(|p| p[c]).sum();
                round_half_even(sum as f64 / s as f64)
            })
        })
        .collect();
    stretched(&colours, size, horizontal)
}

/// _down: a layer drawn `s` times larger reduced to its size on the panel: an exact reduction
/// averages each block (RGBA premultiplied, so edges do not darken), anything else LANCZOS.
pub fn down(layer: &Image, w: i64, h: i64, s: usize) -> Result<Image> {
    let (w, h) = (w.max(1) as usize, h.max(1) as usize);
    if layer.size() == (w * s, h * s) {
        if layer.mode() == Mode::RGBA {
            let reduced = layer.convert(Mode::RGBa)?.reduce(s, s, None)?;
            return Ok(reduced.convert(Mode::RGBA)?);
        }
        return Ok(layer.reduce(s, s, None)?);
    }
    Ok(layer.resize(w, h, Filter::Lanczos, None, None)?)
}

/// The corners of a card: how much of each pixel a card with corners of radius `r` (and an
/// outline `outline` px wide) covers near them, a small card drawn three times larger and
/// reduced, `2 * c + 2` px square, whose `c` px corners are those of any larger card.
pub type Corners = (Image, Option<Image>, i64);

fn made_corners(r: i64, outline: i64) -> Result<Corners> {
    let s = SUPERSAMPLE as i64;
    let c = r + 2;
    let m = 2 * c + 2;
    if m < 0 {
        return Err(Failed("Width and height must be >= 0".into()));
    }
    let side = (m * s) as usize;
    let bounds = (0.0, 0.0, (m * s - 1) as f64, (m * s - 1) as f64);
    let radius = (r * s) as f64;
    let mut shape = Image::new(Mode::L, side, side, &[0]);
    Draw::new(&mut shape).rounded_rectangle(
        bounds,
        radius,
        Some([255, 0, 0, 0]),
        None,
        1,
        ALL_CORNERS,
    )?;
    let ring = if outline != 0 {
        let mut ring = Image::new(Mode::L, side, side, &[0]);
        let width = i32::try_from(outline * s).map_err(|_| Failed("width too large".into()))?;
        Draw::new(&mut ring).rounded_rectangle(
            bounds,
            radius,
            None,
            Some([255, 0, 0, 0]),
            width,
            ALL_CORNERS,
        )?;
        Some(ring.reduce(SUPERSAMPLE, SUPERSAMPLE, None)?)
    } else {
        None
    };
    Ok((shape.reduce(SUPERSAMPLE, SUPERSAMPLE, None)?, ring, c))
}

/// _card_corners (made once for each radius and outline, as its lru_cache keeps them).
pub fn card_corners(r: i64, outline: i64) -> Result<Corners> {
    static KEPT: OnceLock<Mutex<HashMap<(i64, i64), Corners>>> = OnceLock::new();
    let kept = KEPT.get_or_init(Default::default);
    if let Some(hit) = kept.lock().ok().and_then(|k| k.get(&(r, outline)).cloned()) {
        return Ok(hit);
    }
    let made = made_corners(r, outline)?;
    if let Ok(mut k) = kept.lock() {
        if k.len() >= 64 {
            k.clear();
        }
        k.insert((r, outline), made.clone());
    }
    Ok(made)
}
