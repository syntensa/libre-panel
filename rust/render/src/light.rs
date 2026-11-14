//! Light over a frame (render/light.py): the theme colours it takes (ROLES, role_value,
//! role_color, mix), the curves that time it (clamp, smooth, ease_in, ease_out), a glowing line
//! (add_line: a core burning towards white in a halo of its colour, added onto the picture) and
//! a text cut to its room (ellipsize).

use lp_pixels::{chops, merge, Filter, Image, Mode};
use lp_py::{Dict, Value};
use lp_text::fallback::FallbackFont;
use lp_text::text::TextFace;

use crate::piece::{Failed, Result};

/// The Libre look's palette (libre_panel.design), where a theme has none of a role's names.
const LIBRE_CARD: &str = "#111822";
const LIBRE_BG: &str = "#0A0E13";
const LIBRE_TEXT: &str = "#E8EEF4";
const LIBRE_MUTED: &str = "#93A1B2";
const LIBRE_TRACK: &str = "#18212C";
const LIBRE_RULE: &str = "#1F2A36";
const LIBRE_ACCENT: &str = "#2EE6D6";
const LIBRE_WARN: &str = "#F5C46B";
const LIBRE_CRIT: &str = "#F87171";

/// ROLES: the palette names themes use for a role (the first one a theme has wins), and the
/// colour where a theme has none of them.
fn role(name: &str) -> (&'static [&'static str], &'static str) {
    match name {
        "card" => (&["card", "panel", "surface", "surf_1", "bg2"], LIBRE_CARD),
        "ground" => (&["bg", "base"], LIBRE_BG),
        "text" => (&["text", "tx_1"], LIBRE_TEXT),
        "muted" => (&["text3", "tx_3", "muted", "dim"], LIBRE_MUTED),
        "track" => (&["track", "edge", "line", "rule_soft"], LIBRE_TRACK),
        "rule" => (&["rule", "line", "edge", "dim"], LIBRE_RULE),
        "void" => (&["void"], "#000000"),
        "info" => (&["accent", "primary", "cpu"], LIBRE_ACCENT),
        "warning" => (&["warn", "warning"], LIBRE_WARN),
        "error" => (&["crit", "error", "danger", "krit"], LIBRE_CRIT),
        _ => (&[], "#000000"),
    }
}

/// role_value: the theme's colour for a role as the palette writes it: the first of its names
/// the palette has (and not empty), else the Libre look's.
pub(crate) fn role_value(palette: &Dict, name: &str) -> String {
    let (names, fallback) = role(name);
    names
        .iter()
        .find_map(|n| match palette.get(*n) {
            Some(Value::Str(s)) if !s.is_empty() => Some(s.clone()),
            _ => None,
        })
        .unwrap_or_else(|| fallback.to_string())
}

/// role_color: the theme's colour for a role as RGB (role_value); a colour Pillow cannot read,
/// the Libre look's.
pub(crate) fn role_colour(palette: &Dict, name: &str) -> [i64; 3] {
    let rgb = lp_pixels::colour::getrgb(&role_value(palette, name))
        .or_else(|_| lp_pixels::colour::getrgb(role(name).1))
        .unwrap_or_default();
    [0, 1, 2].map(|i| rgb.get(i).copied().unwrap_or(0))
}

/// ellipsize: `text` as it is where `font` draws it within `room` px, else cut so that it, with
/// "…" after it, does (at least one letter stays). The font measures what it draws: the runs of
/// a system font that stands in for a missing glyph count at their own width.
pub(crate) fn ellipsize(text: &str, font: &FallbackFont, room: f64) -> Result<String> {
    let length = |t: &str| {
        font.getlength(t, "", None, None, None)
            .map_err(|e| Failed(e.to_string()))
    };
    if length(text)? <= room {
        return Ok(text.to_string());
    }
    let mut letters: Vec<char> = text.chars().collect();
    // (str.rstrip: the white space of every script)
    let trimmed = |letters: &[char]| -> String {
        let s: String = letters.iter().collect();
        s.trim_end_matches(lp_py::isspace).to_string()
    };
    while letters.len() > 1 && length(&(trimmed(&letters) + "…"))? > room {
        letters.pop();
    }
    Ok(trimmed(&letters) + "…")
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

/// phase: 0 before `start`, 1 after `end`, straight in between.
pub(crate) fn phase(s: f64, start: f64, end: f64) -> f64 {
    clamp((s - start) / (end - start))
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
pub(crate) fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python's min(a, b) of floats: `a` unless `b` is less.
pub(crate) fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// An int of a float as `how` makes it (round, floor, ceil); an error where Python's raises
/// (NaN, infinity), the most an i64 holds for a larger one (all that is asked of it here).
pub(crate) fn whole(x: f64, how: fn(f64) -> f64) -> Result<i64> {
    if x.is_nan() {
        return Err(Failed("cannot convert float NaN to integer".into()));
    }
    if x.is_infinite() {
        return Err(Failed("cannot convert float infinity to integer".into()));
    }
    Ok(how(x) as i64)
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
pub(crate) fn add_light(
    image: &mut Image,
    (x, y): (i64, i64),
    mask: &Image,
    colour: [i64; 3],
) -> Result<()> {
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
    // (round(), math.floor() and math.ceil() of NaN or infinity raise in Python)
    let half = whole(32.0 * look.unit, f64::round_ties_even)?.max(3);
    let top = whole(y, f64::round_ties_even)?.saturating_sub(half);
    let (a, b) = (
        top.max(0),
        (image.height() as i64).min(top.saturating_add(2 * half)),
    );
    let xa = whole(x0, f64::floor)?.max(0);
    let xb = (image.width() as i64).min(whole(x1, f64::ceil)?);
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

/// The light's own white (WHITE): what a line's core burns towards.
pub(crate) const WHITE: [i64; 3] = [234, 240, 248];

/// scaled: `mask` with each value times `strength` (as round() rounds), as it is from 0.999.
pub(crate) fn scaled(mask: Image, strength: f64) -> Result<Image> {
    if strength >= 0.999 {
        return Ok(mask);
    }
    mask.point_with(|v| f64::from(v) * strength).map_err(failed)
}

/// bloom: `layer` blurred into a soft glow, worked out at 1/`scale` of its size (much cheaper,
/// and it looks the same), at `strength`.
pub(crate) fn bloom(layer: &Image, radius: f64, strength: f64, scale: usize) -> Result<Image> {
    let (w, h) = (layer.width(), layer.height());
    let small = layer
        .resize(
            (w / scale).max(1),
            (h / scale).max(1),
            Filter::Bilinear,
            None,
            None,
        )
        .map_err(failed)?;
    let r = py_max(0.3, radius) as f32;
    let soft = small.gaussian_blur(r, r).map_err(failed)?;
    let wide = soft
        .resize(w, h, Filter::Bilinear, None, None)
        .map_err(failed)?;
    scaled(wide, strength)
}

/// paste_top: the top `rows` rows of `layer` at (x, y) on `image`; a part row is blended in by
/// its share, so an edge that moves slowly glides instead of stepping.
pub(crate) fn paste_top(image: &mut Image, layer: &Image, x: i64, y: i64, rows: f64) -> Result<()> {
    let (lw, lh) = (layer.width() as i64, layer.height() as i64);
    let rows = py_max(0.0, py_min(lh as f64, rows));
    let whole = rows as i64; // (int(): rows is not below 0)
    if whole > 0 {
        image
            .paste(&layer.crop(0, 0, lw, whole), x, y, None)
            .map_err(failed)?;
    }
    let part = rows - whole as f64;
    if part > 1.0 / 64.0 && whole < lh {
        let under = image.crop(x, y + whole, x + lw, y + whole + 1);
        let line = layer
            .crop(0, whole, lw, whole + 1)
            .convert(under.mode())
            .map_err(failed)?;
        let blended = chops::blend(&under, &line, part as f32).map_err(failed)?;
        image.paste(&blended, x, y + whole, None).map_err(failed)?;
    }
    Ok(())
}

/// The built-in faces made, by file and size.
type Faces = std::cell::RefCell<Vec<((std::path::PathBuf, i64), std::rc::Rc<FallbackFont>)>>;

thread_local! {
    /// face's: the built-in faces made, by file and size (lru_cache of 48)
    static FACES: Faces = const { std::cell::RefCell::new(Vec::new()) };
}

/// face: a built-in face ("Barlow-Bold") of its own, at `size` (6 at least).
pub(crate) fn face(
    env: &lp_theme::Env,
    name: &str,
    size: i64,
) -> Result<std::rc::Rc<FallbackFont>> {
    let size = size.max(6);
    let path = env
        .builtin_font_path(&format!("builtin:{name}"))
        .ok_or_else(|| Failed(format!("cannot open resource: the face {name}")))?;
    let key = (path, size);
    if let Some(found) = FACES.with(|f| {
        f.borrow()
            .iter()
            .find(|(k, _)| *k == key)
            .map(|(_, font)| font.clone())
    }) {
        return Ok(found);
    }
    let font = std::rc::Rc::new(
        FallbackFont::open(&key.0, size as f64, 0, None).map_err(|e| Failed(e.to_string()))?,
    );
    FACES.with(|f| {
        let mut faces = f.borrow_mut();
        if faces.len() >= 48 {
            faces.remove(0);
        }
        faces.push((key, font.clone()));
    });
    Ok(font)
}

/// font.getlength(text), as Pillow measures it.
pub(crate) fn length(font: &FallbackFont, text: &str) -> Result<f64> {
    font.getlength(text, "", None, None, None)
        .map_err(|e| Failed(e.to_string()))
}

/// tracked_width: the width of `text` letter-spaced by `tracking`.
pub(crate) fn tracked_width(font: &FallbackFont, text: &str, tracking: f64) -> Result<f64> {
    let mut lengths = Vec::new();
    for ch in text.chars() {
        lengths.push(length(font, &ch.to_string())?);
    }
    let gaps = text.chars().count().saturating_sub(1) as f64;
    Ok(lp_py::sum_floats(lengths) + tracking * gaps)
}

/// draw_tracked: letter-spaced text with its baseline at (x, y), letter by letter.
pub(crate) fn draw_tracked(
    image: &mut Image,
    (x, y): (f64, f64),
    text: &str,
    font: &FallbackFont,
    ink: [u8; 4],
    tracking: f64,
) -> Result<()> {
    let mode = image.mode().name();
    let mut x = x;
    for ch in text.chars() {
        let letter = ch.to_string();
        let written = lp_text::text::Text::new(&letter, font, mode);
        lp_text::text::draw(image, (x, y), &written, ink, None, Some("ls"), "left")
            .map_err(|e| Failed(e.to_string()))?;
        x += length(font, &letter)? + tracking;
    }
    Ok(())
}

/// fitted_text: `text` in the face `name` at the largest of `sizes` that fits `width`, else
/// cut with "…" at the smallest.
pub(crate) fn fitted_text(
    env: &lp_theme::Env,
    text: &str,
    name: &str,
    sizes: &[i64],
    width: f64,
) -> Result<(String, std::rc::Rc<FallbackFont>)> {
    let smallest = *sizes.last().ok_or_else(|| Failed("no sizes".into()))?;
    let mut font = face(env, name, smallest)?;
    for &size in sizes {
        font = face(env, name, size)?;
        if length(&font, text)? <= width {
            return Ok((text.to_string(), font));
        }
    }
    let mut letters: Vec<char> = text.chars().collect();
    let joined = |letters: &[char]| letters.iter().collect::<String>();
    while letters.len() > 1 && length(&font, &(joined(&letters) + "…"))? > width {
        letters.pop();
    }
    let cut = joined(&letters);
    Ok((cut.trim_end_matches(lp_py::isspace).to_string() + "…", font))
}
