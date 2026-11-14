//! The Libre Panel mark, drawn in code (branding.py): a gauge's ring, open at the bottom, lit
//! from the lower left over the top to the right and dark for its last part, round three bars
//! that rise to the right, the last one lit: the mark alone, as the panel's own screens take it
//! (render/boot.rs), and the app's icon with its glow and the tray's dot (`logo`); the SVG is
//! still Python's.

use std::hint::black_box;

use lp_pixels::{Draw, Filter, Image, Mode, ALL_CORNERS};

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

/// STATUS_COLORS: the tray icon's dot for how the panel is doing.
fn status_colour(status: &str) -> Option<[u8; 3]> {
    Some(match status {
        "showing" => [34, 197, 94],
        "waiting" | "starting" => [245, 158, 11],
        "paused" => [100, 116, 139],
        "error" => [239, 68, 68],
        _ => return None,
    })
}

/// logo(size, status): the app's icon, the mark on a rounded tile, its light glowing; with a
/// dot for `status` where it has a colour (the tray). Drawn at four times the size and reduced.
pub fn logo(size: usize, status: Option<&str>) -> Result<Image> {
    let s = size * 4;
    let sf = s as f64;
    let mut img = Image::blank(Mode::RGBA, s, s);
    let mut tile = Image::blank(Mode::L, s, s);
    let corner = (sf - 1.0, sf - 1.0);
    Draw::new(&mut tile)
        .rounded_rectangle(
            (0.0, 0.0, corner.0, corner.1),
            sf * 0.22,
            Some([255, 0, 0, 0]),
            None,
            1,
            ALL_CORNERS,
        )
        .map_err(failed)?;
    let (top, bottom) = ([22u8, 32, 48], [9u8, 13, 20]);
    let gradient = Image::linear_gradient()
        .resize(s, s, Filter::Bicubic, None, None)
        .map_err(failed)?;
    let body = lp_pixels::chops::composite(
        &Image::new(Mode::RGB, s, s, &bottom),
        &Image::new(Mode::RGB, s, s, &top),
        &gradient,
    )
    .and_then(|body| body.convert(Mode::RGBA))
    .map_err(failed)?;
    img.paste(&body, 0, 0, Some(&tile)).map_err(failed)?;
    let radius = sf * 0.335 / extent();
    let centre = (sf / 2.0, sf / 2.0 + lift() * radius);
    let none = [0, 0, 0, 0];
    let [ar, ag, ab] = libre("accent");
    let accent = [ar, ag, ab, 255];
    let mut lit = Image::blank(Mode::RGBA, s, s);
    paint(&mut Draw::new(&mut lit), centre, radius, accent, none, none)?;
    let glow = (sf * 0.03) as f32;
    let glow = lit.gaussian_blur(glow, glow).map_err(failed)?;
    img = lp_pixels::alpha_composite(&img, &glow).map_err(failed)?;
    let [tr, tg, tb] = libre("text");
    let [kr, kg, kb] = TRACK;
    paint(
        &mut Draw::new(&mut img),
        centre,
        radius,
        accent,
        [tr, tg, tb, 255],
        [kr, kg, kb, 255],
    )?;
    if let Some([r, g, b]) = status.and_then(status_colour) {
        let dot = sf * 0.17;
        let (x, y) = (sf - dot - sf * 0.02, sf - dot - sf * 0.02);
        let rim = dot + sf * 0.03;
        let mut draw = Draw::new(&mut img);
        draw.ellipse(
            (x - rim, y - rim, x + rim, y + rim),
            Some([9, 13, 20, 255]),
            None,
            1,
        )
        .map_err(failed)?;
        draw.ellipse(
            (x - dot, y - dot, x + dot, y + dot),
            Some([r, g, b, 255]),
            None,
            1,
        )
        .map_err(failed)?;
    }
    img.resize(size, size, Filter::Lanczos, None, None)
        .map_err(failed)
}
