//! Facets (render/facets.py): lettering with a flat top in its own colour and a sharp 45-degree
//! bevel along its edges, lit from the top left; of bright metal that mirrors the sky above it
//! and the ground below (the facet finish), or of acrylic lit from within (the acrylic one). A
//! letter keeps its outline exactly (the flat text's pixels); its bevel lies inside it.
//!
//! Worked out as numpy works it out: in float32, each step rounded where numpy rounds it, its
//! sums in numpy's order.

use lp_pixels::{Filter, Image, Mode};
use lp_text::fallback::FallbackFont;
use lp_text::text::{draw, Text, TextFace};

use crate::piece::{Failed, Result};

/// The light's direction (towards it, from the top left and in front), made of length 1 in
/// float32 as numpy makes it; a flat top gets its third part.
fn light() -> [f32; 3] {
    let l = [-16f32, -16.0, 24.0];
    let length = ((l[0] * l[0] + l[1] * l[1]) + l[2] * l[2]).sqrt();
    l.map(|v| v / length)
}
/// How much lighter towards the light and how dark away from it: metal, acrylic, and what
/// small lettering gets instead (it must stay legible).
const METAL: (f64, f64) = (0.85, 0.30);
const ACRYLIC: (f64, f64) = (1.10, 0.80);
const LIGHT_TOUCH: (f64, f64) = (0.85, 0.88);
/// How far metal takes on its surroundings (acrylic: not at all).
const MIRROR: f64 = 0.55;
/// Smaller text stays flat: a bevel there is only mush.
pub(crate) const MIN_SIZE: f64 = 20.0;
const BEVEL_SCALE: f64 = 0.04;
const BEVEL_MIN: f64 = 1.0;
const BEVEL_MAX: f64 = 5.0;
/// At most this much of the local half-stroke becomes bevel, of small lettering this much.
const STROKE_SHARE: f64 = 0.5;
const STROKE_LIGHT: f64 = 0.12;
/// A light touch up to this size, the full look from this one.
const LIGHT_SIZE: f64 = 28.0;
const FULL_SIZE: f64 = 52.0;
/// The sky metal mirrors is the accent this far towards white.
const SKY_SHARE: f64 = 0.35;
/// Glyph geometry is worked out this much larger.
const SUPER: usize = 4;
/// Blank pixels round a glyph: an edge needs an outside for its bevel to follow.
const MARGIN: i64 = 2;
/// A "no pixel there" distance.
const FAR: i64 = 1 << 20;
/// The palette's names for the sky's colour, and the Libre look's where a theme has none.
const INFO: [&str; 3] = ["accent", "primary", "cpu"];
const LIBRE_ACCENT: &str = "#2EE6D6";

/// math.sin(math.radians(45)).
fn sin45() -> f64 {
    45f64.to_radians().sin()
}

/// The style of a finish's lettering.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) enum Style {
    Metal,
    Acrylic,
}

/// numpy's np.clip(x, lo, hi) of float32 (NaN stays NaN).
fn clip(x: f32, lo: f32, hi: f32) -> f32 {
    let x = if x < lo { lo } else { x };
    if x > hi {
        hi
    } else {
        x
    }
}

/// edt: the Euclidean distance from each pixel inside to the nearest pixel outside (0
/// outside), exact; h + w everywhere where there is no pixel outside. (float32 of the square
/// root of the distance squared, in two passes: columns, then rows.)
fn edt(inside: &[bool], h: usize, w: usize) -> Vec<f32> {
    if !inside.iter().any(|&b| b) {
        return vec![0.0; h * w];
    }
    if inside.iter().all(|&b| b) {
        return vec![(h + w) as f32; h * w];
    }
    let cap = (h + w) as i64;
    // to the nearest outside pixel in the same column (a column without one: far), squared
    let mut vertical = vec![0i64; h * w];
    let mut up = vec![0i64; h];
    for x in 0..w {
        let mut above = -FAR;
        for (y, u) in up.iter_mut().enumerate() {
            if !inside[y * w + x] {
                above = y as i64;
            }
            *u = y as i64 - above;
        }
        let mut below = FAR;
        for y in (0..h).rev() {
            if !inside[y * w + x] {
                below = y as i64;
            }
            let near = up[y].min(below - y as i64).min(cap);
            vertical[y * w + x] = near * near;
        }
    }
    // the rows: the least of the column distances squared plus the offset squared, offset by
    // offset until no pixel can gain
    let mut best = vertical.clone();
    let most = |best: &[i64]| -> i64 {
        best.iter()
            .zip(inside)
            .filter(|(_, &i)| i)
            .map(|(&b, _)| b)
            .max()
            .unwrap_or(0)
    };
    let mut limit = most(&best);
    let mut step = 1usize;
    while ((step * step) as i64) < limit && step < w {
        let cost = (step * step) as i64;
        for y in 0..h {
            let (row, col) = (&mut best[y * w..(y + 1) * w], &vertical[y * w..(y + 1) * w]);
            for x in step..w {
                row[x] = row[x].min(col[x - step] + cost);
            }
            for x in 0..w - step {
                row[x] = row[x].min(col[x + step] + cost);
            }
        }
        if step.is_multiple_of(4) {
            limit = most(&best);
        }
        step += 1;
    }
    best.iter().map(|&b| (b as f64).sqrt() as f32).collect()
}

/// max_filter: the largest value over the square of `size` (odd) pixels around each, the edge
/// extended.
fn max_filter(a: &[f32], h: usize, w: usize, size: usize) -> Vec<f32> {
    let pad = (size / 2) as i64;
    let max = |x: f32, y: f32| if y > x { y } else { x };
    let mut across = vec![0f32; h * w];
    for y in 0..h {
        for x in 0..w as i64 {
            let mut m = f32::NEG_INFINITY;
            for i in x - pad..=x + pad {
                m = max(m, a[y * w + i.clamp(0, w as i64 - 1) as usize]);
            }
            across[y * w + x as usize] = m;
        }
    }
    let mut out = vec![0f32; h * w];
    for y in 0..h as i64 {
        for x in 0..w {
            let mut m = f32::NEG_INFINITY;
            for j in y - pad..=y + pad {
                m = max(m, across[j.clamp(0, h as i64 - 1) as usize * w + x]);
            }
            out[y as usize * w + x] = m;
        }
    }
    out
}

/// sobel: the Sobel gradient (gx, gy) of `a`, the edge extended: a central difference
/// smoothed across.
fn sobel(a: &[f32], h: usize, w: usize) -> (Vec<f32>, Vec<f32>) {
    let at = |y: i64, x: i64| {
        a[y.clamp(0, h as i64 - 1) as usize * w + x.clamp(0, w as i64 - 1) as usize]
    };
    let mut gx = vec![0f32; h * w];
    let mut gy = vec![0f32; h * w];
    for y in 0..h as i64 {
        for x in 0..w as i64 {
            let dx = |r: i64| at(r, x + 1) - at(r, x - 1);
            let dy = |c: i64| at(y + 1, c) - at(y - 1, c);
            gx[y as usize * w + x as usize] = dx(y - 1) + 2.0 * dx(y) + dx(y + 1);
            gy[y as usize * w + x as usize] = dy(x - 1) + 2.0 * dy(x) + dy(x + 1);
        }
    }
    (gx, gy)
}

/// bevel_width: the bevel of lettering at a font size, in px (0: too small, stays flat).
fn bevel_width(size: f64) -> f64 {
    if size < MIN_SIZE {
        return 0.0;
    }
    BEVEL_MAX.min(BEVEL_MIN.max(BEVEL_SCALE * size))
}

/// display: how much of the full look lettering has at a font size, 0 (a light touch) to 1.
fn display(size: f64) -> f64 {
    let t = (size - LIGHT_SIZE) / (FULL_SIZE - LIGHT_SIZE);
    // (Python's min(1.0, max(0.0, t)))
    let t = if t > 0.0 { t } else { 0.0 };
    if t < 1.0 {
        t
    } else {
        1.0
    }
}

/// bevel_share: the share of a stroke's half that lettering of this size may make bevel.
fn bevel_share(size: f64) -> f64 {
    STROKE_LIGHT + (STROKE_SHARE - STROKE_LIGHT) * display(size)
}

/// bevel_normals: the normals of a bevel running inwards along the edge of `mask` (coverage 0
/// to 1, more than a half inside), `bevel` px wide, held to `stroke` of the local half-stroke;
/// the rest flat.
fn bevel_normals(mask: &[f32], h: usize, w: usize, bevel: f64, stroke: f64) -> Vec<[f32; 3]> {
    let inside: Vec<bool> = mask.iter().map(|&v| v > 0.5).collect();
    let mut n = vec![[0.0, 0.0, 1.0]; h * w];
    if bevel <= 0.0 || !inside.iter().any(|&b| b) {
        return n;
    }
    let d = edt(&inside, h, w);
    let reach = (lp_py::round(bevel) as i64).max(1) as usize;
    let near = max_filter(&d, h, w, 2 * reach + 1);
    let (bevel32, stroke32) = (bevel as f32, stroke as f32);
    // (np.minimum: the first where they are equal)
    let width: Vec<f32> = near
        .iter()
        .map(|&m| {
            let v = m * stroke32;
            if v < bevel32 {
                v
            } else {
                bevel32
            }
        })
        .collect();
    if !width.iter().any(|&v| v > 0.0) {
        return n;
    }
    let (gx, gy) = sobel(&d, h, w);
    let s45 = sin45() as f32;
    let tiny = 1e-4f64 as f32;
    for i in 0..h * w {
        let length = gx[i].hypot(gy[i]);
        let steady = length > tiny;
        let safe = if steady { length } else { 1.0 };
        let (ux, uy) = if steady {
            (-gx[i] / safe, -gy[i] / safe)
        } else {
            (0.0, 0.0)
        };
        if inside[i] && d[i] <= width[i] {
            n[i] = [ux * s45, uy * s45, s45];
        }
    }
    n
}

/// shrink: the normals averaged `SUPER` x `SUPER` down, weighted by coverage, and made unit
/// again (numpy's means: a block's rows summed each from 0 and then added up, for the weights;
/// the weighted normals summed one after the other).
fn shrink(n: &[[f32; 3]], weight: &[f32], bh: usize, bw: usize) -> Vec<[f32; 3]> {
    let (h, w) = (bh / SUPER, bw / SUPER);
    let count = (SUPER * SUPER) as f32;
    let floor = 1e-6f64 as f32;
    let tiny = 1e-9f64 as f32;
    let mut out = Vec::with_capacity(h * w);
    for i in 0..h {
        for j in 0..w {
            let mut cover = 0f32;
            let mut mean = [0f32; 3];
            for k in 0..SUPER {
                let mut part = 0f32;
                for l in 0..SUPER {
                    let at = (i * SUPER + k) * bw + j * SUPER + l;
                    part += weight[at];
                    for c in 0..3 {
                        mean[c] += n[at][c] * weight[at];
                    }
                }
                cover = if k == 0 { part } else { cover + part };
            }
            let cover = cover / count;
            let by = if cover > floor { cover } else { floor };
            let mean = mean.map(|m| m / count / by);
            let length = ((mean[0] * mean[0] + mean[1] * mean[1]) + mean[2] * mean[2]).sqrt();
            out.push(if length > floor {
                let by = if length > tiny { length } else { tiny };
                mean.map(|m| m / by)
            } else {
                [0.0, 0.0, 1.0]
            });
        }
    }
    out
}

/// The shading terms of lettering with these normals (A * 255, B, S * 255, G * 255, with
/// rgb = colour * A + B + sky * S + ground * G): the light first, then what a metal bevel takes
/// on of its surroundings; small lettering gets a light touch, bigger more.
fn lettering_terms(normals: &[[f32; 3]], style: Style, size: f64) -> Vec<[u8; 4]> {
    let t = display(size);
    let ((lighten, darken), mirror) = match style {
        Style::Metal => (METAL, MIRROR),
        Style::Acrylic => (ACRYLIC, 0.0),
    };
    let (soft_lighten, soft_darken) = LIGHT_TOUCH;
    let lighten = (soft_lighten + (lighten - soft_lighten) * t) as f32;
    let (darken, rest) = {
        let darken = soft_darken + (darken - soft_darken) * t;
        (darken as f32, (1.0 - darken) as f32)
    };
    let mirror = mirror * t;
    let s45 = sin45();
    let (sky_steep, up_by) = ((1.0 - s45) as f32, s45 as f32);
    let strength = mirror as f32;
    let exponent = 1.4f64 as f32;
    let byte = |v: f32| clip(v + 0.5, 0.0, 255.0) as u8;
    let light = light();
    normals
        .iter()
        .map(|n| {
            // facing: how much light it gets, a flat top's 1
            let k = clip(
                (n[0] * light[0] + n[1] * light[1]) + n[2] * light[2],
                0.0,
                1.0,
            ) / light[2];
            // shade: towards white facing the light, towards black away from it
            let lit = k >= 1.0;
            let m = clip((k - 1.0) * lighten, 0.0, 1.0);
            let a = if lit { 1.0 - m } else { darken + rest * k };
            let b = if lit { 255.0 * m } else { 0.0 };
            // mirrors: the sky facing up, the ground facing down or to a side
            let (sky, ground) = if mirror != 0.0 {
                let steep = clip((1.0 - n[2]) / sky_steep, 0.0, 1.0);
                let up = clip(0.5 - n[1] / up_by * 0.5, 0.0, 1.0).powf(exponent);
                let share = steep * strength;
                (share * up, share * (1.0 - up))
            } else {
                (0.0, 0.0)
            };
            let keep = 1.0 - sky - ground;
            [
                byte(a * keep * 255.0),
                byte(b * keep),
                byte(sky * 255.0),
                byte(ground * 255.0),
            ]
        })
        .collect()
}

/// A character at one font size, quarter pixel and style: where its picture goes from the pen
/// (left) and the line's top, its coverage (the flat text's), and its shading terms.
pub(crate) struct Glyph {
    left: i64,
    top: i64,
    w: usize,
    h: usize,
    cover: Vec<u8>,
    terms: Vec<[u8; 4]>,
}

/// _glyph_geometry: the glyph's coverage as the flat text draws it `quarter` / 4 px right of
/// its pixel, and its bevel's shading (from that same mask enlarged); None for a blank.
pub(crate) fn glyph_geometry(
    font: &FallbackFont,
    ch: char,
    quarter: i64,
    style: Style,
) -> Result<Option<Glyph>> {
    let failed = |e: lp_text::Error| Failed(e.to_string());
    let text = ch.to_string();
    let (left, top, right, bottom) = font
        .getbbox(&text, "", None, None, None, 0.0, Some("la"))
        .map_err(failed)?;
    if right <= left || bottom <= top {
        return Ok(None);
    }
    let (l0, t0) = (left.floor() as i64 - MARGIN, top.floor() as i64 - MARGIN);
    let w1 = (right.ceil() as i64 - l0 + 2).max(1);
    let h1 = (bottom.ceil() as i64 - t0 + 2).max(1);
    let big_pixels = (w1 * SUPER as i64).saturating_mul(h1 * SUPER as i64);
    if big_pixels > crate::renderer::MOST_PIXELS {
        return Err(Failed("a letter too large".into()));
    }
    let (w1, h1) = (w1 as usize, h1 as usize);
    let mut mask = Image::blank(Mode::L, w1, h1);
    let at = (-l0 as f64 + quarter as f64 / 4.0, -t0 as f64);
    draw(
        &mut mask,
        at,
        &Text::new(&text, font, "L"),
        [255, 0, 0, 0],
        None,
        Some("la"),
        "left",
    )
    .map_err(failed)?;
    let cover = mask.as_bytes().to_vec();
    if cover.iter().all(|&v| v <= 127) {
        return Ok(None);
    }
    let (bw, bh) = (w1 * SUPER, h1 * SUPER);
    let big: Vec<f32> = mask
        .resize(bw, bh, Filter::Bilinear, None, None)?
        .as_bytes()
        .iter()
        .map(|&v| f32::from(v) / 255.0)
        .collect();
    let size = font.font.size();
    let normals = bevel_normals(
        &big,
        bh,
        bw,
        bevel_width(size) * SUPER as f64,
        bevel_share(size),
    );
    let terms = lettering_terms(&shrink(&normals, &big, bh, bw), style, size);
    Ok(Some(Glyph {
        left: l0,
        top: t0,
        w: w1,
        h: h1,
        cover,
        terms,
    }))
}

/// _coloured: the glyph in `fill` (RGBA) with its shading, the sky and the ground it mirrors.
pub(crate) fn coloured(
    g: &Glyph,
    fill: [i64; 4],
    environment: ([u8; 3], [u8; 3]),
) -> Result<Image> {
    let (sky, ground) = (environment.0.map(f32::from), environment.1.map(f32::from));
    let colour = [fill[0] as f32, fill[1] as f32, fill[2] as f32];
    let share = (fill[3] as f64 / 255.0) as f32;
    let mut out = Vec::with_capacity(g.w * g.h * 4);
    for (&cover, terms) in g.cover.iter().zip(&g.terms) {
        let [a, b, s, t] = terms.map(f32::from);
        for c in 0..3 {
            let rgb = colour[c] * (a / 255.0) + b + sky[c] * (s / 255.0) + ground[c] * (t / 255.0);
            out.push(clip(clip(rgb + 0.5, 0.0, 255.0), 0.0, 255.0) as u8);
        }
        out.push(clip(f32::from(cover) * share + 0.5, 0.0, 255.0) as u8);
    }
    Ok(Image::from_bytes(Mode::RGBA, g.w, g.h, out)?)
}

/// environment_for: what metal lettering mirrors: the sky above it (the theme's accent, a third
/// of the way to white) and the ground below (the theme's background).
pub(crate) fn environment_for(palette: &lp_py::Dict, ground: [i64; 4]) -> ([u8; 3], [u8; 3]) {
    // (light.role_color: the first of the palette's names it has, else the Libre look's; a
    // colour Pillow cannot read, the Libre look's)
    let named = INFO.iter().find_map(|name| match palette.get(*name) {
        Some(lp_py::Value::Str(s)) if !s.is_empty() => Some(s.as_str()),
        _ => None,
    });
    let rgb = named
        .and_then(|v| lp_pixels::colour::getrgb(v).ok())
        .unwrap_or_else(|| lp_pixels::colour::getrgb(LIBRE_ACCENT).expect("the Libre accent"));
    let sky = [0, 1, 2].map(|i| {
        let b = rgb[i] as f64;
        lp_py::round(b + (255.0 - b) * SKY_SHARE).clamp(0.0, 255.0) as u8
    });
    (sky, [0, 1, 2].map(|i| ground[i].clamp(0, 255) as u8))
}

/// facets.usable: whether lettering in `font` can have facets (a font of a size where a bevel is
/// not mush).
pub(crate) fn usable(font: &FallbackFont) -> bool {
    font.font.size() >= MIN_SIZE
}

/// The quarter pixel a letter is lettered at from its pen's fraction, and the whole pixel it
/// moves on where that rounds up to the next.
pub(crate) fn quarter_of(frac: f64) -> (i64, i64) {
    let quarter = lp_py::round(frac * 4.0) as i64;
    if quarter == 4 {
        (0, 1)
    } else {
        (quarter, 0)
    }
}

/// Where a glyph's picture goes from the pen's pixel and the line's top.
pub(crate) fn placed(g: &Glyph, shift: i64) -> (i64, i64) {
    (g.left + shift, g.top)
}

/// For lp-check: the glyph of `ch` at `quarter`, of metal or acrylic (where it goes, its
/// coverage, its terms as an image's four bands) and coloured in `fill`; None for a blank.
#[allow(clippy::type_complexity)]
pub fn check_glyph(
    font: &FallbackFont,
    ch: char,
    quarter: i64,
    metal: bool,
    fill: [i64; 4],
    environment: ([u8; 3], [u8; 3]),
) -> Result<Option<(i64, i64, Image, Image, Image)>> {
    let style = if metal { Style::Metal } else { Style::Acrylic };
    let Some(g) = glyph_geometry(font, ch, quarter, style)? else {
        return Ok(None);
    };
    let cover = Image::from_bytes(Mode::L, g.w, g.h, g.cover.clone())?;
    let terms: Vec<u8> = g.terms.iter().flatten().copied().collect();
    let terms = Image::from_bytes(Mode::RGBA, g.w, g.h, terms)?;
    let patch = coloured(&g, fill, environment)?;
    Ok(Some((g.left, g.top, cover, terms, patch)))
}
