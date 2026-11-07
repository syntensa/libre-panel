//! The sign of an AI agent at work (render/agent.py, the agent widget) in a still frame, in
//! Libre Panel's own look: three lights on a faint ring while it works, the ring in the waiting
//! colour while it waits for you, the ring calm with a light resting at its top while idle,
//! the ring faint while off; drawn four times larger and reduced.

use std::f64::consts::PI;

use lp_pixels::{Draw, Filter, Image, Mode};

use crate::piece::{Failed, Piece, Result};
use crate::renderer::MOST_PIXELS;
use crate::shapes::{ink, Rgba};

const SUPERSAMPLE: i64 = 4;

/// _alpha: the colour, its alpha times `share` (rounded half to even, within a byte).
fn alpha(colour: Rgba, share: f64) -> [u8; 4] {
    let [r, g, b, _] = ink(colour);
    [
        r,
        g,
        b,
        lp_py::round(colour[3] as f64 * share).clamp(0.0, 255.0) as u8,
    ]
}

/// _draw: the sign, `size` square (8 px at least), for `state` (working, waiting, idle, off)
/// where its motion starts.
pub fn picture(size: i64, state: &str, colour: Rgba, waiting: Rgba, muted: Rgba) -> Result<Image> {
    let side = size.max(8);
    let k = side.saturating_mul(SUPERSAMPLE);
    if k.saturating_mul(k) > MOST_PIXELS {
        return Err(Failed("a sign too large".into()));
    }
    let mut layer = Image::blank(Mode::RGBA, k as usize, k as usize);
    let big = k as f64;
    let (c, r) = (big / 2.0, big * 0.36);
    let width = (lp_py::round(big * 0.055) as i64).max(SUPERSAMPLE) as f64;
    let phase = 0.0f64;
    let mut draw = Draw::new(&mut layer);
    // (its outline round(w) wide, a pixel at least)
    let ring = |draw: &mut Draw<Image>, fill: [u8; 4], w: f64| -> Result<()> {
        let width = i32::try_from((lp_py::round(w) as i64).max(1))
            .map_err(|_| Failed("signed integer is greater than maximum".into()))?;
        draw.ellipse((c - r, c - r, c + r, c + r), None, Some(fill), width)?;
        Ok(())
    };
    // (a dot of `radius` at `at` from the middle, at `angle` from the top, clockwise)
    let light = |draw: &mut Draw<Image>, angle: f64, radius: f64, fill: [u8; 4], at: f64| {
        let (x, y) = (c + at * angle.sin(), c - at * angle.cos());
        let bounds = (x - radius, y - radius, x + radius, y + radius);
        draw.ellipse(bounds, Some(fill), None, 1)
            .map_err(Failed::from)
    };
    let dot = big * 0.075;
    match state {
        "working" => {
            ring(&mut draw, alpha(colour, 0.22), width)?;
            let head = 2.0 * PI * phase;
            for (i, share) in [1.0, 0.55, 0.28].into_iter().enumerate() {
                let i = i as f64;
                let fill = alpha(colour, share);
                light(&mut draw, head - i * 0.42, dot * (1.0 - 0.18 * i), fill, r)?;
            }
            let middle = big * 0.05 * (0.8 + 0.2 * (2.0 * PI * phase).cos());
            light(&mut draw, 0.0, middle, alpha(colour, 0.5), 0.0)?;
        }
        "waiting" => {
            let breath = 0.5 - 0.5 * (2.0 * PI * phase).cos();
            let fill = alpha(waiting, 0.55 + 0.45 * breath);
            ring(&mut draw, fill, width * (1.0 + 0.6 * breath))?;
            let fill = alpha(waiting, 0.6 + 0.4 * breath);
            light(&mut draw, 0.0, big * (0.07 + 0.05 * breath), fill, 0.0)?;
        }
        "idle" => {
            ring(&mut draw, alpha(colour, 0.6), width)?;
            light(&mut draw, 0.0, dot * 0.85, alpha(colour, 0.9), r)?;
        }
        _ => ring(&mut draw, alpha(muted, 0.45), width * 0.8)?,
    }
    Ok(layer.resize(side as usize, side as usize, Filter::Lanczos, None, None)?)
}

/// The agent widget's piece: the sign at (x, y), its box `size` square.
pub fn piece(x: i64, y: i64, size: i64, image: Image) -> Piece {
    let mut piece = Piece::new(image, x, y);
    piece.hit = Some([x, y, size, size]);
    piece
}
