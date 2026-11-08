//! Light over a frame (render/light.py): the theme colours it takes (ROLES, role_color, mix),
//! the curves that time it (clamp, smooth, ease_in, ease_out) and a glowing line (add_line:
//! a core burning towards white in a halo of its colour, added onto the picture).

use lp_pixels::{chops, merge, Filter, Image, Mode};
use lp_py::{Dict, Value};

use crate::piece::{Failed, Result};

/// The Libre look's palette (libre_panel.design), where a theme has none of a role's names.
const LIBRE_BG: &str = "#0A0E13";
const LIBRE_TEXT: &str = "#E8EEF4";
const LIBRE_ACCENT: &str = "#2EE6D6";
const LIBRE_WARN: &str = "#F5C46B";

/// ROLES: the palette names themes use for a role (the first one a theme has wins), and the
/// colour where a theme has none of them.
fn role(name: &str) -> (&'static [&'static str], &'static str) {
    match name {
        "ground" => (&["bg", "base"], LIBRE_BG),
        "text" => (&["text", "tx_1"], LIBRE_TEXT),
        "info" => (&["accent", "primary", "cpu"], LIBRE_ACCENT),
        "warning" => (&["warn", "warning"], LIBRE_WARN),
        _ => (&[], "#000000"),
    }
}

/// role_color: the theme's colour for a role as RGB: the first of its names the palette has
/// (and not empty), else the Libre look's; a colour Pillow cannot read, the Libre look's.
pub(crate) fn role_colour(palette: &Dict, name: &str) -> [i64; 3] {
    let (names, fallback) = role(name);
    let given = names.iter().find_map(|n| match palette.get(*n) {
        Some(Value::Str(s)) if !s.is_empty() => Some(s.as_str()),
        _ => None,
    });
    let rgb = given
        .and_then(|v| lp_pixels::colour::getrgb(v).ok())
        .or_else(|| lp_pixels::colour::getrgb(fallback).ok())
        .unwrap_or_default();
    [0, 1, 2].map(|i| rgb.get(i).copied().unwrap_or(0))
}

/// mix: `a` over `b` by `share` (0: all `b`, 1: all `a`), each channel rounded as Python's
/// round rounds (half to even).
pub(crate) fn mix(a: [i64; 3], b: [i64; 3], share: f64) -> [i64; 3] {
    [0, 1, 2].map(|i| lp_py::round(b[i] as f64 + (a[i] - b[i]) as f64 * share) as i64)
}

/// clamp: `u` from 0 to 1 (NaN and -0.0 as they are, as Python's comparisons leave them).
pub(crate) fn clamp(u: f64) -> f64 {
    u.clamp(0.0, 1.0)
}

/// smooth: the smooth step of `u` clamped.
pub(crate) fn smooth(u: f64) -> f64 {
    let u = clamp(u);
    u * u * (3.0 - 2.0 * u)
}

/// ease_out: fast, then settling (cubic, as Python's ** takes it: pow).
pub(crate) fn ease_out(u: f64) -> f64 {
    1.0 - (1.0 - clamp(u)).powf(3.0)
}

/// ease_in: slow, then faster (cubic).
pub(crate) fn ease_in(u: f64) -> f64 {
    clamp(u).powf(3.0)
}

/// Python's max(a, b) of floats: `a` unless `b` is greater.
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

/// Python's round(x) of a float, as an int.
fn round_int(x: f64) -> i64 {
    lp_py::round(x) as i64
}

fn failed(e: lp_pixels::Error) -> Failed {
    Failed(e.to_string())
}

/// _profile: how a horizontal line's light falls off above and below it, a bright core in a
/// wide halo (one column, L).
fn profile(height: usize, core: f64, halo: f64, share: f64) -> Result<Image> {
    let mid = (height as f64 - 1.0) / 2.0;
    let values = (0..height)
        .map(|y| {
            let d = y as f64 - mid;
            let mut light = (-(d / core).powf(2.0)).exp();
            if share != 0.0 {
                light += share * (-(d / halo).powf(2.0)).exp();
            }
            round_int(255.0 * light).min(255) as u8
        })
        .collect();
    Image::from_bytes(Mode::L, 1, height, values).map_err(failed)
}

/// add_light: `colour` added through `mask` (L, 255 all of it) at (x, y) onto `image` (RGB),
/// only where the mask lies.
fn add_light(image: &mut Image, (x, y): (i64, i64), mask: &Image, colour: [i64; 3]) -> Result<()> {
    let (iw, ih) = (image.width() as i64, image.height() as i64);
    let (mw, mh) = (mask.width() as i64, mask.height() as i64);
    let (x0, y0, x1, y1) = (x.max(0), y.max(0), iw.min(x + mw), ih.min(y + mh));
    if x1 <= x0 || y1 <= y0 {
        return Ok(());
    }
    let part = mask.crop(x0 - x, y0 - y, x1 - x, y1 - y);
    let tint = colour.map(|c| c as u8);
    let flat = Image::new(Mode::RGB, part.width(), part.height(), &tint);
    let light = chops::multiply(
        &flat,
        &merge(Mode::RGB, &[&part, &part, &part]).map_err(failed)?,
    )
    .map_err(failed)?;
    let sum = chops::add(&image.crop(x0, y0, x1, y1), &light, 1.0, 0).map_err(failed)?;
    image.paste(&sum, x0, y0, None).map_err(failed)
}

/// How a line looks: its unit, the length its ends fade over, its halo's width and share, its
/// colour and the white its core burns towards.
#[derive(Clone, Copy)]
pub(crate) struct Look {
    pub unit: f64,
    pub soft: f64,
    pub halo: f64,
    pub share: f64,
    pub colour: [i64; 3],
    pub white: [i64; 3],
}

/// _strip: the light of add_line at full strength (RGB), from `x0` to `x1` of `width`.
fn strip(width: usize, height: usize, x0: f64, x1: f64, look: &Look) -> Result<Image> {
    let ramp = py_max(1.0, py_min(look.soft, (x1 - x0) / 2.0));
    let row = (0..width)
        .map(|x| {
            let x = x as f64;
            round_int(255.0 * clamp(py_min(x + 0.5 - x0, x1 - x - 0.5) / ramp)) as u8
        })
        .collect();
    let window = Image::from_bytes(Mode::L, width, 1, row)
        .and_then(|row| row.resize(width, height, Filter::Nearest, None, None))
        .map_err(failed)?;
    let core = py_max(0.7, 2.0 * look.unit);
    let mut light = Image::blank(Mode::RGB, width, height);
    let grey = mix(look.white, [0, 0, 0], 0.45);
    for (tint, glow) in [(look.colour, look.share), (grey, 0.0)] {
        let column = profile(height, core, py_max(1.5, look.halo * look.unit), glow)?;
        let column = column
            .resize(width, height, Filter::Nearest, None, None)
            .map_err(failed)?;
        let mask = chops::multiply(&window, &column).map_err(failed)?;
        add_light(&mut light, (0, 0), &mask, tint)?;
    }
    Ok(light)
}

/// add_line: a horizontal line of light from `x0` to `x1` around row `y` of `image` (RGB): a
/// core burning towards white in a halo of its colour, at `strength` (0 to 1), its ends fading
/// over `soft` px, added onto what is there.
pub(crate) fn add_line(
    image: &mut Image,
    (x0, x1, y): (f64, f64, f64),
    strength: f64,
    look: &Look,
) -> Result<()> {
    if strength <= 0.004 || x1 - x0 < 1.0 {
        return Ok(());
    }
    let half = round_int(32.0 * look.unit).max(3);
    let top = round_int(y) - half;
    let (a, b) = (top.max(0), (image.height() as i64).min(top + 2 * half));
    let xa = (x0.floor() as i64).max(0);
    let xb = (image.width() as i64).min(x1.ceil() as i64);
    if b <= a || xb <= xa {
        return Ok(());
    }
    let (w, h) = ((xb - xa) as usize, (2 * half) as usize);
    let light =
        strip(w, h, x0 - xa as f64, x1 - xa as f64, look)?.crop(0, a - top, xb - xa, b - top);
    let light = if strength < 0.999 {
        light
            .point_with(|v| f64::from(v) * strength)
            .map_err(failed)?
    } else {
        light
    };
    let sum = chops::add(&image.crop(xa, a, xb, b), &light, 1.0, 0).map_err(failed)?;
    image.paste(&sum, xa, a, None).map_err(failed)
}
