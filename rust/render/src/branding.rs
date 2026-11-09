//! The Libre Panel mark, drawn in code (branding.py): a gauge's ring, open at the bottom, lit
//! from the lower left over the top to the right and dark for its last part, round three bars
//! that rise to the right, the last one lit. So far the mark alone without its glow, as the
//! panel's own screens take it (render/boot.rs); the app's icon, the tray's and the SVG are
//! still Python's.

use std::hint::black_box;

use lp_pixels::{Draw, Image, Mode, ALL_CORNERS};

use crate::piece::{Failed, Result};

/// The mark, in units of the ring's radius (to the middle of its stroke), its centre at (0, 0)
/// and y down; angles in degrees clockwise from three o'clock: the ring's width, the ring and
/// where its light ends, each bar's width, left edge and height, the line they stand on and
/// the radius of their corners.
const STROKE: f64 = 0.26;
const OPEN_FROM: f64 = 135.0;
const LIT_TO: f64 = 345.0;
const OPEN_TO: f64 = 405.0;
const BAR_W: f64 = 0.24;
const BARS: [(f64, f64); 3] = [(-0.51, 0.42), (-0.12, 0.66), (0.27, 0.90)];
const BASE: f64 = 0.40;
const BAR_ROUND: f64 = 0.08;
/// branding.TRACK: the ring's dark end.
const TRACK: [u8; 3] = [42, 52, 64];

fn failed(e: lp_pixels::Error) -> Failed {
    Failed(e.0)
}

/// EXTENT: the ring's outer radius.
fn extent() -> f64 {
    1.0 + STROKE / 2.0
}

/// LIFT: how much lower than the middle of its box the mark is drawn, the ring being open
/// below, so that it sits in the middle from the top of the ring to the ends of its opening.
fn lift() -> f64 {
    // (the C library's sine at run time, as Python's)
    (extent() - (black_box(OPEN_FROM).to_radians().sin() + STROKE / 2.0)) / 2.0
}

/// A colour of the Libre look (design.LOOKS' palette), as branding.py takes it.
pub(crate) fn libre(role: &str) -> [u8; 3] {
    let colour = lp_theme::design::looks()
        .get("libre")
        .and_then(|look| look.as_dict()?.get("palette"))
        .and_then(|palette| palette.as_dict()?.get(role))
        .and_then(|c| c.as_str())
        .unwrap_or("#000000");
    let part = |at: usize| u8::from_str_radix(colour.get(at..at + 2).unwrap_or("00"), 16);
    [part(1), part(3), part(5)].map(|p| p.unwrap_or(0))
}

/// _paint: the mark on `draw`, its ring `radius` px (to the middle of its stroke) round
/// (cx, cy): the dark end of the ring first, then the lit part over it, then the bars; every
/// end of the ring round.
fn paint(
    draw: &mut Draw<'_, Image>,
    (cx, cy): (f64, f64),
    radius: f64,
    accent: [u8; 4],
    bars: [u8; 4],
    track: [u8; 4],
) -> Result<()> {
    let width = STROKE * radius;
    let outer = radius + width / 2.0;
    let bounds = (cx - outer, cy - outer, cx + outer, cy + outer); // (a wide arc goes inward)
    let cap = |draw: &mut Draw<'_, Image>, angle: f64, colour: [u8; 4]| -> Result<()> {
        let a = black_box(angle).to_radians();
        let (x, y) = (cx + radius * a.cos(), cy + radius * a.sin());
        let half = width / 2.0;
        draw.ellipse(
            (x - half, y - half, x + half, y + half),
            Some(colour),
            None,
            1,
        )
        .map_err(failed)
    };
    let stroke = lp_py::round(width) as i32;
    draw.arc(bounds, LIT_TO, OPEN_TO, Some(track), stroke)
        .map_err(failed)?;
    cap(draw, OPEN_TO, track)?;
    draw.arc(bounds, OPEN_FROM, LIT_TO, Some(accent), stroke)
        .map_err(failed)?;
    cap(draw, OPEN_FROM, accent)?;
    cap(draw, LIT_TO, accent)?;
    for (i, &(left, height)) in BARS.iter().enumerate() {
        let (x0, y1) = (cx + left * radius, cy + BASE * radius);
        let ink = if i == BARS.len() - 1 { accent } else { bars };
        let bar = (x0, y1 - height * radius, x0 + BAR_W * radius, y1);
        draw.rounded_rectangle(bar, BAR_ROUND * radius, Some(ink), None, 1, ALL_CORNERS)
            .map_err(failed)?;
    }
    Ok(())
}

/// mark(diameter, light, glow=0): the mark alone, its ring `diameter` px across, in the middle
/// of a transparent square half as large again; `light` (0 to 1) dims what is lit, as the
/// panel's start screen breathes. Drawn at four times the size and reduced premultiplied.
pub fn mark(diameter: f64, light: f64) -> Result<Image> {
    const SCALE: usize = 4;
    let size = 8.max((diameter * 1.5).ceil() as i64) as usize;
    let side = size * SCALE;
    let mut shape = Image::blank(Mode::RGBA, side, side);
    let radius = diameter / 2.0 / extent() * SCALE as f64;
    let dim = 0.55 + 0.45 * light;
    let [r, g, b] = libre("accent").map(|c| lp_py::round(f64::from(c) * dim) as u8);
    let [tr, tg, tb] = libre("text");
    let [kr, kg, kb] = TRACK;
    let middle = side as f64 / 2.0;
    paint(
        &mut Draw::new(&mut shape),
        (middle, middle + lift() * radius),
        radius,
        [r, g, b, 255],
        [tr, tg, tb, 255],
        [kr, kg, kb, 255],
    )?;
    shape
        .convert(Mode::RGBa)
        .and_then(|s| s.reduce(SCALE, SCALE, None))
        .and_then(|s| s.convert(Mode::RGBA))
        .map_err(failed)
}
