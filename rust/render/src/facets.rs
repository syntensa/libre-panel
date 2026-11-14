//! Facets (render/facets.py): lettering with a flat top in its own colour and a sharp 45-degree
//! bevel along its edges, lit from the top left; of bright metal that mirrors the sky above it
//! and the ground below (the facet finish), or of acrylic lit from within (the acrylic one). A
//! letter keeps its outline exactly (the flat text's pixels); its bevel lies inside it.
//!
//! Worked out as numpy works it out: in float32, each step rounded where numpy rounds it, its
//! sums in numpy's order.

use std::cell::RefCell;
use std::rc::Rc;

use lp_pixels::{Filter, Image, Mode};
use lp_text::fallback::FallbackFont;
use lp_text::text::{draw, Text, TextFace};

use lp_py::Dict;

use crate::bar::Paint;
use crate::kept::Kept;
use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Renderer, MOST_PIXELS};
use crate::shapes::Rgba;
use crate::widget;

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

/// facing: how much light a surface of normal `n` gets, as a share of what a flat top gets (1
/// for the top, above facing the light, below away; never negative), in float32.
fn facing(n: [f32; 3], light: [f32; 3]) -> f32 {
    clip(
        (n[0] * light[0] + n[1] * light[1]) + n[2] * light[2],
        0.0,
        1.0,
    ) / light[2]
}

/// shade: (A, B) with rgb = colour * A + B for light `k` (see facing), in float32: a surface
/// facing the light goes towards white, one facing away towards black.
fn shade(k: f32, (lighten, darken): (f64, f64)) -> (f32, f32) {
    let (lighten, rest, darken) = (lighten as f32, (1.0 - darken) as f32, darken as f32);
    let lit = k >= 1.0;
    let m = clip((k - 1.0) * lighten, 0.0, 1.0);
    let a = if lit { 1.0 - m } else { darken + rest * k };
    let b = if lit { 255.0 * m } else { 0.0 };
    (a, b)
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
    let shading = (
        soft_lighten + (lighten - soft_lighten) * t,
        soft_darken + (darken - soft_darken) * t,
    );
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
            let (a, b) = shade(facing(*n, light), shading);
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

// -- bars ------------------------------------------------------------------------------------

/// Thinner bars stay flat (a bevel does not read there), thicker too.
const MIN_BAR: i64 = 6;
const MAX_BAR: i64 = 400;
/// A window is worked out this much larger.
const SUB: usize = 4;
/// Bars and rings: narrow, so a hard contrast reads as an edge.
const SOLID: (f64, f64) = (1.35, 0.16);
/// A milled channel is background: it hints at an edge, no more.
const GROOVE: (f64, f64) = (0.20, 0.55);

/// The mean over each `SUB` x `SUB` block of `x` (height * SUB rows of width * SUB), as numpy
/// works out x.reshape(height, SUB, width, SUB).mean(axis=(1, 3)): a block's rows each summed
/// from 0, then added up.
fn small(x: &[f32], height: usize, width: usize) -> Vec<f32> {
    let row = width * SUB;
    let mut out = Vec::with_capacity(height * width);
    for i in 0..height {
        for j in 0..width {
            let mut sum = 0f32;
            for k in 0..SUB {
                let mut part = 0f32;
                for l in 0..SUB {
                    part += x[(i * SUB + k) * row + j * SUB + l];
                }
                sum = if k == 0 { part } else { sum + part };
            }
            out.push(sum / (SUB * SUB) as f32);
        }
    }
    out
}

/// A window's shading: the maps (A, B, coverage), each height x width, row by row.
struct Maps {
    a: Vec<f32>,
    b: Vec<f32>,
    alpha: Vec<f32>,
}

/// box_window: the shading of a window `width` x `height` px on a box whose edges lie at
/// (left, right, top, bottom) px of the window (an infinity for one beyond it): within `bevel`
/// of the nearest edge it tilts 45 degrees away from it (a mitre where two are as near); a
/// `groove` is the same milled in, the tilt mirrored.
fn box_window(
    width: usize,
    height: usize,
    edges: (f64, f64, f64, f64),
    bevel: f64,
    style: (f64, f64),
    groove: bool,
) -> Maps {
    let (left, right, top, bottom) = (
        edges.0 as f32,
        edges.1 as f32,
        edges.2 as f32,
        edges.3 as f32,
    );
    let s45 = sin45() as f32;
    let light = light();
    let bevel = bevel as f32;
    let sign = if groove { -1.0f32 } else { 1.0 };
    let sides = [[-s45, 0.0], [s45, 0.0], [0.0, -s45], [0.0, s45]];
    let (bw, bh) = (width * SUB, height * SUB);
    let mut a = Vec::with_capacity(bw * bh);
    let mut b = Vec::with_capacity(bw * bh);
    let mut cover = Vec::with_capacity(bw * bh);
    for y in 0..bh {
        let ys = (y as f32 + 0.5) / SUB as f32;
        for x in 0..bw {
            let xs = (x as f32 + 0.5) / SUB as f32;
            let to = [xs - left, right - xs, ys - top, bottom - ys];
            // (argmin: the first of the nearest; min: NaN where one is)
            let mut nearest = 0;
            for i in 1..4 {
                if to[i] < to[nearest] {
                    nearest = i;
                }
            }
            let least = to
                .iter()
                .copied()
                .fold(f32::INFINITY, |m, v| if v < m { v } else { m });
            let flat = least >= bevel;
            let n = if flat {
                [0.0, 0.0, 1.0]
            } else {
                let tilt = sides[nearest];
                [tilt[0] * sign, tilt[1] * sign, s45]
            };
            let (sa, sb) = shade(facing(n, light), style);
            let inside = if least >= 0.0 { 1.0 } else { 0.0 };
            a.push(sa * inside);
            b.push(sb * inside);
            cover.push(inside);
        }
    }
    let alpha = small(&cover, height, width);
    let floor = 1e-6f64 as f32;
    let safe: Vec<f32> = alpha
        .iter()
        .map(|&v| if v > floor { v } else { floor })
        .collect();
    let a = small(&a, height, width)
        .iter()
        .zip(&safe)
        .map(|(v, s)| v / s)
        .collect();
    let b = small(&b, height, width)
        .iter()
        .zip(&safe)
        .map(|(v, s)| v / s)
        .collect();
    Maps { a, b, alpha }
}

/// _body: the shading maps of a bar's body from `start` to `end` px along its length, over the
/// pixels from floor(start) to ceil(end): that first pixel, the maps (thick rows of n, or n
/// rows of thick), and n. Only its two ends and one column between are worked out; the column
/// is repeated.
fn body(
    horizontal: bool,
    thick: usize,
    start: f64,
    end: f64,
    groove: bool,
    style: (f64, f64),
) -> (i64, Maps, usize) {
    let bevel = thick as f64 / 6.0;
    let (first, last) = (start.floor() as i64, end.ceil() as i64);
    let n = (last - first).max(0) as usize;
    let cap = bevel.ceil() as usize + 2;
    let window = |px: usize, low: f64, high: f64| {
        if horizontal {
            box_window(
                px,
                thick,
                (low, high, 0.0, thick as f64),
                bevel,
                style,
                groove,
            )
        } else {
            box_window(
                thick,
                px,
                (0.0, thick as f64, low, high),
                bevel,
                style,
                groove,
            )
        }
    };
    let (s, e) = (start - first as f64, end - first as f64);
    if n <= 2 * cap + 2 {
        return (first, window(n, s, e), n);
    }
    let low = window(cap, s, f64::INFINITY);
    let high = window(cap, f64::NEG_INFINITY, end - (last - cap as i64) as f64);
    let middle = window(1, f64::NEG_INFINITY, f64::INFINITY);
    let repeat = n - 2 * cap;
    // (np.concatenate of the start, the middle column repeated and the end, along the bar)
    let joined = |pick: fn(&Maps) -> &Vec<f32>| -> Vec<f32> {
        let (lo, mid, hi) = (pick(&low), pick(&middle), pick(&high));
        let mut out = Vec::with_capacity(n * thick);
        if horizontal {
            for row in 0..thick {
                out.extend_from_slice(&lo[row * cap..(row + 1) * cap]);
                out.extend(std::iter::repeat_n(mid[row], repeat));
                out.extend_from_slice(&hi[row * cap..(row + 1) * cap]);
            }
        } else {
            out.extend_from_slice(lo);
            for _ in 0..repeat {
                out.extend_from_slice(mid);
            }
            out.extend_from_slice(hi);
        }
        out
    };
    let maps = Maps {
        a: joined(|m| &m.a),
        b: joined(|m| &m.b),
        alpha: joined(|m| &m.alpha),
    };
    (first, maps, n)
}

/// The colour (RGBA, float32) of each pixel along a bar, from its place on the bar.
type Colours<'a> = &'a dyn Fn(f32) -> [f32; 4];

/// _box_image: a bar's body as an image, and the pixel it starts at along the bar; `colours`
/// gives each pixel's colour from where it lies.
fn box_image(
    horizontal: bool,
    thick: usize,
    start: f64,
    end: f64,
    colours: Colours,
    groove: bool,
    style: (f64, f64),
) -> Result<(Image, i64)> {
    let (first, maps, n) = body(horizontal, thick, start, end, groove, style);
    let c: Vec<[f32; 4]> = (0..n)
        .map(|i| colours(first as f32 + i as f32 + 0.5))
        .collect();
    let (w, h) = if horizontal { (n, thick) } else { (thick, n) };
    let mut out = Vec::with_capacity(w * h * 4);
    for y in 0..h {
        for x in 0..w {
            let at = y * w + x;
            let colour = c[if horizontal { x } else { y }];
            let (a, b) = (maps.a[at], maps.b[at]);
            for &v in &colour[..3] {
                out.push(clip(clip(v * a + b + 0.5, 0.0, 255.0), 0.0, 255.0) as u8);
            }
            out.push(clip(maps.alpha[at] * colour[3] + 0.5, 0.0, 255.0) as u8);
        }
    }
    Ok((Image::from_bytes(Mode::RGBA, w, h, out)?, first))
}

/// _mix: a colour along `a` to `b` at `t` (0 to 1), in float32; `a` alone without `b`.
fn mix(a: [i64; 4], b: Option<[i64; 4]>, t: f32) -> [f32; 4] {
    let first = a.map(|v| v as f32);
    match b {
        None => first,
        Some(b) => {
            let t = clip(t, 0.0, 1.0);
            [0, 1, 2, 3].map(|i| first[i] + (b[i] as f32 - first[i]) * t)
        }
    }
}

/// bar_layer: a bar as RGBA, w x h: a raised body in `paint` (a colour, or a gradient along the
/// whole track to the second) filled `frac` of the way from the side `direction` names, with
/// straight, bevelled ends, in a milled track of colour `track`; or `segments` such blocks
/// `gap` px apart. None where a bevel does not read or the blocks do not fit.
#[allow(clippy::too_many_arguments)]
fn bar_layer(
    w: i64,
    h: i64,
    frac: f64,
    direction: &str,
    paint: Paint,
    track: Option<Rgba>,
    segments: i64,
    gap: f64,
) -> Result<Option<Image>> {
    let horizontal = matches!(direction, "right" | "left");
    let (span, thick) = if horizontal { (w, h) } else { (h, w) };
    let reverse = matches!(direction, "left" | "up");
    if !(MIN_BAR..=MAX_BAR).contains(&thick) || span < 2 {
        return Ok(None);
    }
    if w.saturating_mul(h) > MOST_PIXELS {
        return Err(Failed("a layer too large".into()));
    }
    let (w, h, span, thick) = (w as usize, h as usize, span as usize, thick as usize);
    let (a, b) = paint;
    let mut layer = Image::blank(Mode::RGBA, w, h);
    let put_at = |layer: &mut Image, image: &Image, first: i64| -> Result<()> {
        let at = if horizontal { (first, 0) } else { (0, first) };
        let whole = (0, 0, image.width() as i64, image.height() as i64);
        layer.alpha_composite_at(image, at, whole)?;
        Ok(())
    };
    let along = |start: f64, end: f64| -> (f64, f64) {
        if reverse {
            (span as f64 - end, span as f64 - start)
        } else {
            (start, end)
        }
    };
    let spanf = span as f32;
    let gradient = move |pos: f32| -> [f32; 4] {
        let t = if reverse { spanf - pos } else { pos } / spanf;
        mix(a, b, t)
    };
    let solid = |colour: [i64; 4], share: f64| -> [f32; 4] {
        [
            colour[0] as f32,
            colour[1] as f32,
            colour[2] as f32,
            (colour[3] as f64 * share) as f32,
        ]
    };
    let quarter = |v: f64| lp_py::round(v * SUB as f64) / SUB as f64;
    let boxed =
        |layer: &mut Image, start: f64, end: f64, colours: Colours, groove: bool| -> Result<()> {
            let style = if groove { GROOVE } else { SOLID };
            let (image, first) = box_image(
                horizontal,
                thick,
                quarter(start),
                quarter(end),
                colours,
                groove,
                style,
            )?;
            put_at(layer, &image, first)
        };
    if segments > 0 {
        let cell = (span as f64 - gap * (segments - 1) as f64) / segments as f64;
        if cell < 1.0 {
            return Ok(None);
        }
        let cells: Vec<(f64, f64)> = (0..segments)
            .map(|i| {
                let at = i as f64 * (cell + gap);
                along(at, at + cell)
            })
            .collect();
        if let Some(track) = track {
            let c = solid(track, 1.0);
            for &(start, end) in &cells {
                boxed(&mut layer, start, end, &|_| c, true)?;
            }
        }
        for (i, &(start, end)) in cells.iter().enumerate() {
            let amount = (frac * segments as f64 - i as f64).clamp(0.0, 1.0);
            if amount <= 0.0 {
                continue;
            }
            // (_mix of a float64 place: worked out in float64, then rounded half to even)
            let t = (i as f64 / (segments - 1).max(1) as f64).clamp(0.0, 1.0);
            let colour = match b {
                None => a,
                Some(b) => [0, 1, 2, 3].map(|c| {
                    let first = a[c] as f32 as f64;
                    let step = (b[c] as f32 - a[c] as f32) as f64;
                    (first + step * t).round_ties_even() as i64
                }),
            };
            let c = solid(colour, amount);
            boxed(&mut layer, start, end, &|_| c, false)?;
        }
        return Ok(Some(layer));
    }
    if let Some(track) = track {
        let c = solid(track, 1.0);
        boxed(&mut layer, 0.0, span as f64, &|_| c, true)?;
    }
    let length = (span as f64).min((frac * span as f64).max(0.0));
    if length > 0.0 {
        let (start, end) = along(0.0, length.max(1.0));
        boxed(&mut layer, start, end, &gradient, false)?;
    }
    Ok(Some(layer))
}

/// groove_bar: a bar's milled groove, RGBA w x h, lying along w (`vertical`: along h): a floor
/// of exactly `colour` and a 45-degree bevel all round; None where a bevel does not read.
pub(crate) fn groove_bar(
    w: usize,
    h: usize,
    colour: [i64; 4],
    vertical: bool,
) -> Result<Option<Image>> {
    let (thick, span) = if vertical { (w, h) } else { (h, w) };
    if !(MIN_BAR..=MAX_BAR).contains(&(thick as i64)) || span < 2 {
        return Ok(None);
    }
    let direction = if vertical { "down" } else { "right" };
    bar_layer(
        w as i64,
        h as i64,
        0.0,
        direction,
        (colour, None),
        Some(colour),
        0,
        0.0,
    )
}

impl Renderer {
    /// FacetBar's piece (facet.py): a raised bar with straight, bevelled ends in a milled track,
    /// at `frac` in `paint`; drawn flat where a bevel does not read.
    pub(crate) fn facet_bar_piece(&self, w: &Dict, frac: f64, paint: Paint) -> Result<Piece> {
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (bw, bh) = (widget::int(w, "w", 200), widget::int(w, "h", 16));
        let track = self.colour_of(w, "background")?;
        let (segments, gap) = (
            widget::int(w, "segments", 0),
            widget::number(w, "segment_gap", 2.0),
        );
        let direction = widget::text(w, "direction");
        match bar_layer(bw, bh, frac, direction, paint, track, segments, gap)? {
            None => self.bar_piece(w, frac, paint),
            Some(raised) => {
                let mut piece = Piece::new(raised, x, y);
                piece.hit = Some([x, y, bw, bh]);
                Ok(piece)
            }
        }
    }
}

/// For lp-check: bar_layer of a bar `w` x `h` (None where a bevel does not read).
#[allow(clippy::too_many_arguments)]
pub fn check_bar(
    w: i64,
    h: i64,
    frac: f64,
    direction: &str,
    paint: ([i64; 4], Option<[i64; 4]>),
    track: Option<[i64; 4]>,
    segments: i64,
    gap: f64,
) -> Result<Option<Image>> {
    bar_layer(w, h, frac, direction, paint, track, segments, gap)
}

// -- rings ----------------------------------------------------------------------------------

/// A ring's band is worked out this much larger.
const RING_SUB: usize = 2;

/// np.mod of float32: the remainder with the divisor's sign (numpy's npy_remainderf).
pub(crate) fn mod32(a: f32, b: f32) -> f32 {
    let m = a % b;
    if m == 0.0 {
        0.0f32.copysign(b)
    } else if (b < 0.0) != (m < 0.0) {
        m + b
    } else {
        m
    }
}

/// ring_band: the pixels a ring in a box covers (its circle the largest the box holds, its band
/// `thickness` px wide, from `start` to `end` degrees clockwise from 3 o'clock) and, per sample
/// (RING_SUB squared of them to a pixel), where it lies: samples first, pixels last, as numpy
/// keeps them.
struct RingBand {
    /// the pixels (row, column), row by row
    pixels: Vec<(usize, usize)>,
    /// radians from the start of the sweep, px from the middle, px to the inner and the outer
    /// edge (negative: outside), the light along the way round and along the radius
    along: Vec<f32>,
    radius: Vec<f32>,
    inner: Vec<f32>,
    outer: Vec<f32>,
    tangent: Vec<f32>,
    radial: Vec<f32>,
    /// 1 where a sample lies within both edges
    inside: Vec<f32>,
    /// for each pixel, 0 to 1: how far along the sweep its middle is
    place: Vec<f32>,
    /// radians, and the band's width (px)
    sweep: f64,
    thickness: f64,
    /// the shading of each sample without the ends, by groove and style: worked out once
    no_ends: RefCell<Vec<NoEnds>>,
}

/// A band's shading without ends: for a groove or not, in a style (its bits), (A, B) of each
/// sample.
type NoEnds = ((bool, u64, u64), Rc<(Vec<f32>, Vec<f32>)>);

impl RingBand {
    /// The shading of each sample as if the ring had no ends (band.kept[(groove, style)]).
    fn no_ends(&self, groove: bool, style: (f64, f64)) -> Rc<(Vec<f32>, Vec<f32>)> {
        let key = (groove, style.0.to_bits(), style.1.to_bits());
        if let Some((_, kept)) = self.no_ends.borrow().iter().find(|(k, _)| *k == key) {
            return Rc::clone(kept);
        }
        let (a, b) = (0..self.along.len())
            .map(|q| ring_shading(self, q, f32::INFINITY, f32::INFINITY, groove, style))
            .unzip();
        let made = Rc::new((a, b));
        self.no_ends.borrow_mut().push((key, Rc::clone(&made)));
        made
    }

    /// About how many bytes it holds.
    fn bytes(&self) -> usize {
        let samples = self.along.len();
        let shadings = self.no_ends.borrow().len();
        self.pixels.len() * (std::mem::size_of::<(usize, usize)>() + 4)
            + samples * 4 * (7 + 2 * shadings)
    }
}

fn ring_band(w: usize, h: usize, thickness: i64, start: f64, end: f64) -> RingBand {
    let outer_r = w.min(h) as f64 / 2.0;
    let inner_r = (outer_r - thickness.max(1) as f64).max(0.0);
    let sign = if end >= start { 1.0f32 } else { -1.0 };
    let sweep = (2.0 * std::f64::consts::PI).min((end - start).abs().to_radians());
    // (np.mgrid, np.nonzero: in a pixel and a half of the band, row by row; in doubles)
    let (half_w, half_h) = (w as f64 / 2.0, h as f64 / 2.0);
    let mut pixels = vec![];
    for row in 0..h {
        for col in 0..w {
            let centre = (col as f64 + 0.5 - half_w).hypot(row as f64 + 0.5 - half_h);
            if centre >= inner_r - 1.5 && centre <= outer_r + 1.5 {
                pixels.push((row, col));
            }
        }
    }
    let light = light();
    let start_at = start.to_radians() as f32;
    let turn = (2.0 * std::f64::consts::PI) as f32;
    let tiny = 1e-6f64 as f32;
    let (inner_at, outer_at) = (inner_r as f32, outer_r as f32);
    let samples = RING_SUB * RING_SUB;
    let size = samples * pixels.len();
    let mut band = RingBand {
        pixels: vec![],
        along: Vec::with_capacity(size),
        radius: Vec::with_capacity(size),
        inner: Vec::with_capacity(size),
        outer: Vec::with_capacity(size),
        tangent: Vec::with_capacity(size),
        radial: Vec::with_capacity(size),
        inside: Vec::with_capacity(size),
        place: vec![],
        sweep,
        thickness: outer_r - inner_r,
        no_ends: RefCell::new(vec![]),
    };
    for s in 0..samples {
        // (a sample a quarter or three quarters into its pixel down and across)
        let (down, across) = (
            (s / RING_SUB) as f64 * 0.5 + 0.25,
            (s % RING_SUB) as f64 * 0.5 + 0.25,
        );
        for &(row, col) in &pixels {
            let dx = (col as f64 + across - half_w) as f32;
            let dy = (row as f64 + down - half_h) as f32;
            let radius = dx.hypot(dy);
            let safe = if radius > tiny { radius } else { tiny };
            let angle = dy.atan2(dx);
            band.along.push(mod32(sign * (angle - start_at), turn));
            band.tangent
                .push(sign * (-dy * light[0] + dx * light[1]) / safe);
            band.radial.push((dx * light[0] + dy * light[1]) / safe);
            let (inner, outer) = (radius - inner_at, outer_at - radius);
            band.radius.push(radius);
            band.inner.push(inner);
            band.outer.push(outer);
            band.inside.push(if inner >= 0.0 && outer >= 0.0 {
                1.0
            } else {
                0.0
            });
        }
    }
    // (np.mod(sign * (mid - radians(start)), 2 pi) / max(sweep, 1e-6), each pixel's middle in
    // doubles, clipped to 0..1 and made float32)
    let sign = f64::from(sign);
    let start_rad = start.to_radians();
    let most = sweep.max(1e-6);
    band.place = (pixels.iter())
        .map(|&(row, col)| {
            let mid = (row as f64 + 0.5 - half_h).atan2(col as f64 + 0.5 - half_w);
            let at = mod64(sign * (mid - start_rad), 2.0 * std::f64::consts::PI) / most;
            at.clamp(0.0, 1.0) as f32
        })
        .collect();
    band.pixels = pixels;
    band
}

/// np.mod of doubles: the remainder with the divisor's sign (numpy's npy_divmod).
fn mod64(a: f64, b: f64) -> f64 {
    let m = a % b;
    if m == 0.0 {
        0.0f64.copysign(b)
    } else if (b < 0.0) != (m < 0.0) {
        m + b
    } else {
        m
    }
}

thread_local! {
    /// ring_band's bands (facets.rings) and groove_ring's coloured grooves (facets.tracks), by
    /// what they are made of; a ring of 300 px is some 3 MB of band.
    static RINGS: RefCell<Kept<RingKey, Rc<RingBand>>> = RefCell::new(Kept::new(RING_BYTES));
    static TRACKS: RefCell<Kept<(RingKey, Rgba), Rc<Image>>> =
        RefCell::new(Kept::new(TRACK_BYTES));
}

/// What a ring's band is made of: its box, its thickness, where it starts and ends (bits).
type RingKey = (usize, usize, i64, u64, u64);

/// How many bytes of rings' bands are kept, and of their coloured grooves.
const RING_BYTES: usize = 4 << 20;
const TRACK_BYTES: usize = 2 << 20;

/// ring_band, kept by what it is made of.
fn kept_band(w: usize, h: usize, thickness: i64, start: f64, end: f64) -> Rc<RingBand> {
    let key = (w, h, thickness, start.to_bits(), end.to_bits());
    if let Some(band) = RINGS.with(|kept| kept.borrow_mut().get(&key)) {
        return band;
    }
    let band = Rc::new(ring_band(w, h, thickness, start, end));
    let size = band.bytes();
    RINGS.with(|kept| kept.borrow_mut().put(key, Rc::clone(&band), size));
    band
}

/// shade in doubles (as numpy works a ring's out: its normals float64), made float32.
fn shade64(k: f64, (lighten, darken): (f64, f64)) -> (f32, f32) {
    let lit = k >= 1.0;
    let m = ((k - 1.0) * lighten).clamp(0.0, 1.0);
    let a = if lit {
        1.0 - m
    } else {
        darken + (1.0 - darken) * k
    };
    let b = if lit { 255.0 * m } else { 0.0 };
    (a as f32, b as f32)
}

/// _ring_shading: (A, B) of the band's sample `q`, its cuts `u` px from the start of the sweep
/// and `left` px from its end (infinite: none).
fn ring_shading(
    band: &RingBand,
    q: usize,
    u: f32,
    left: f32,
    groove: bool,
    style: (f64, f64),
) -> (f32, f32) {
    let (inner, outer) = (band.inner[q], band.outer[q]);
    let bevel = (band.thickness / 6.0) as f32;
    let nearest = inner.min(outer).min(u.min(left));
    let flat = nearest >= bevel;
    let at_start = u <= left && u <= inner && u <= outer;
    let at_end = !at_start && left <= inner && left <= outer;
    let at_inner = !at_start && !at_end && inner <= outer;
    let s45 = sin45();
    let (mut way, mut out) = match (flat, at_start, at_end) {
        (true, ..) => (0.0, 0.0),
        (false, true, _) => (-s45, 0.0),
        (false, false, true) => (s45, 0.0),
        _ => (0.0, if at_inner { -s45 } else { s45 }),
    };
    if groove {
        (way, out) = (-way, -out);
    }
    let top = if flat { 1.0 } else { s45 };
    let light = light();
    let k = way * f64::from(band.tangent[q])
        + out * f64::from(band.radial[q])
        + top * f64::from(light[2]);
    shade64(k.clamp(0.0, 1.0) / f64::from(light[2]), style)
}

/// ring_maps: (A, B, coverage) of each pixel of the band, the ring filled `frac` of its sweep:
/// within the bevel of its nearest edge (the inner and outer circle, the straight cuts at its
/// ends) a point tilts 45 degrees away from it; each pixel the mean of its samples.
fn ring_maps(band: &RingBand, frac: f64, groove: bool, style: (f64, f64)) -> [Vec<f32>; 3] {
    let arc = band.sweep * frac.clamp(0.0, 1.0);
    let closed = band.sweep >= 2.0 * std::f64::consts::PI - 1e-6 && frac >= 0.999;
    let arc_at = arc as f32;
    let bevel = (band.thickness / 6.0) as f32;
    let n = band.pixels.len();
    let samples = band.along.len() / n.max(1);
    let (mut a, mut b) = (vec![0f32; n], vec![0f32; n]);
    let mut alpha = vec![0f32; n];
    // (what the long edges make does not depend on the fill: worked out once; each fill only
    // the samples near its ends)
    let no_ends = band.no_ends(groove, style);
    for s in 0..samples {
        for p in 0..n {
            let q = s * n + p;
            let mut shaded = (no_ends.0[q], no_ends.1[q]);
            if !closed {
                let u = band.radius[q] * band.along[q];
                let left = band.radius[q] * arc_at - u;
                if (u < bevel || left < bevel) && left >= 0.0 {
                    shaded = ring_shading(band, q, u, left, groove, style);
                }
            }
            let cover = band.inside[q] * if band.along[q] <= arc_at { 1.0 } else { 0.0 };
            // (sums over the samples, one after another, as numpy sums down)
            if s == 0 {
                (a[p], b[p], alpha[p]) = (shaded.0 * cover, shaded.1 * cover, cover);
            } else {
                a[p] += shaded.0 * cover;
                b[p] += shaded.1 * cover;
                alpha[p] += cover;
            }
        }
    }
    let share = (1.0 / samples as f64) as f32;
    let floor = 1e-6f64 as f32;
    for p in 0..n {
        alpha[p] *= share;
        let safe = if alpha[p] > floor { alpha[p] } else { floor };
        a[p] = a[p] * share / safe;
        b[p] = b[p] * share / safe;
    }
    [a, b, alpha]
}

/// groove_ring: the milled groove of a ring, RGBA w x h: a band `thickness` px wide on the
/// largest circle the box holds, from `start` to `end` degrees (a sweep of 360 closed), a floor
/// of exactly `colour` and a 45-degree bevel thickness / 6 px wide along both edges and, in an
/// open ring, at its two straight ends.
pub(crate) fn groove_ring(
    w: usize,
    h: usize,
    thickness: i64,
    start: f64,
    end: f64,
    colour: Rgba,
) -> Result<Image> {
    let band = ring_band(w, h, thickness, start, end);
    let out = band_image(&band, w, h, 1.0, true, GROOVE, (colour, None));
    Ok(Image::from_bytes(Mode::RGBA, w, h, out)?)
}

/// groove_ring kept by what it is made of (facets.tracks): the same picture each time.
fn kept_groove(
    w: usize,
    h: usize,
    thickness: i64,
    start: f64,
    end: f64,
    colour: Rgba,
) -> Result<Rc<Image>> {
    let key = ((w, h, thickness, start.to_bits(), end.to_bits()), colour);
    if let Some(groove) = TRACKS.with(|kept| kept.borrow_mut().get(&key)) {
        return Ok(groove);
    }
    let band = kept_band(w, h, thickness, start, end);
    let out = band_image(&band, w, h, 1.0, true, GROOVE, (colour, None));
    let groove = Rc::new(Image::from_bytes(Mode::RGBA, w, h, out)?);
    TRACKS.with(|kept| (kept.borrow_mut()).put(key, Rc::clone(&groove), w * h * 4));
    Ok(groove)
}

/// _band_image: the RGBA bytes of a `w` x `h` picture holding the band, the ring filled `frac`
/// of its sweep, coloured by `paint` along it (a colour, or a gradient to the second).
fn band_image(
    band: &RingBand,
    w: usize,
    h: usize,
    frac: f64,
    groove: bool,
    style: (f64, f64),
    paint: Paint,
) -> Vec<u8> {
    let [a, b, alpha] = ring_maps(band, frac, groove, style);
    let byte = |v: f32| clip(v + 0.5, 0.0, 255.0) as u8;
    let mut out = vec![0u8; w * h * 4];
    for (p, &(row, col)) in band.pixels.iter().enumerate() {
        let c = mix(paint.0, paint.1, band.place[p]);
        let at = (row * w + col) * 4;
        for i in 0..3 {
            out[at + i] = byte(c[i] * a[p] + b[p]);
        }
        out[at + 3] = byte(alpha[p] * c[3]);
    }
    out
}

/// ring_layer: a ring as RGBA, `w` x `h`: a raised arc in `paint` (a gradient along the sweep
/// to the second colour), `frac` of the sweep from its start, with straight, bevelled ends,
/// in a milled track of colour `track`. Its ends are straight cuts, whatever the gauge's cap.
#[allow(clippy::too_many_arguments)]
fn ring_layer(
    w: usize,
    h: usize,
    thickness: i64,
    start: f64,
    end: f64,
    frac: f64,
    paint: Paint,
    track: Option<Rgba>,
) -> Result<Image> {
    let band = kept_band(w, h, thickness, start, end);
    // (the groove pasted on a clear layer: the groove itself)
    let mut layer = match track {
        Some(track) => (*kept_groove(w, h, thickness, start, end, track)?).clone(),
        None => Image::blank(Mode::RGBA, w, h),
    };
    if frac > 0.0 {
        let out = band_image(&band, w, h, frac, false, SOLID, paint);
        let raised = Image::from_bytes(Mode::RGBA, w, h, out)?;
        layer.alpha_composite_at(&raised, (0, 0), (0, 0, w as i64, h as i64))?;
    }
    Ok(layer)
}

impl Renderer {
    /// FacetGauge's piece (facet.py): a raised ring with straight, bevelled ends in a milled
    /// track, at `frac` in `paint`; its ticks, if any, flat on it.
    pub(crate) fn facet_gauge_piece(&self, w: &Dict, frac: f64, paint: Paint) -> Result<Piece> {
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (gw, gh) = (widget::int(w, "w", 120), widget::int(w, "h", 120));
        // (numpy's refusals of a band of no size: np.mgrid of a negative size, the reshape
        // of no pixels)
        if gw < 0 || gh < 0 {
            return Err(Failed("negative dimensions are not allowed".into()));
        }
        if gw == 0 || gh == 0 {
            return Err(Failed(
                "cannot reshape array of size 0 into shape (0,newaxis)".into(),
            ));
        }
        let (uw, uh) = crate::gauge::sized(gw, gh)?;
        let track = self.colour_of(w, "background")?;
        let mut layer = ring_layer(
            uw,
            uh,
            widget::int(w, "thickness", 12),
            widget::number(w, "start_angle", 135.0),
            widget::number(w, "end_angle", 405.0),
            frac,
            paint,
            track,
        )?;
        if widget::int(w, "ticks", 0) > 0 {
            let ticked = self.gauge_static(&crate::gauge::ticks_alone(w))?;
            let whole = (0, 0, ticked.width() as i64, ticked.height() as i64);
            layer.alpha_composite_at(&ticked, (0, 0), whole)?;
        }
        let mut piece = Piece::new(layer, x, y);
        piece.hit = Some([x, y, gw, gh]);
        Ok(piece)
    }
}

/// For lp-check: ring_layer of a ring `w` x `h`.
#[allow(clippy::too_many_arguments)]
pub fn check_ring(
    w: usize,
    h: usize,
    thickness: i64,
    start: f64,
    end: f64,
    frac: f64,
    paint: ([i64; 4], Option<[i64; 4]>),
    track: Option<[i64; 4]>,
) -> Result<Image> {
    ring_layer(w, h, thickness, start, end, frac, paint, track)
}

// -- curves ---------------------------------------------------------------------------------

/// A graph wider than this gets the slimmer ridge (the full one costs Python ~5 ms there).
pub(crate) const CURVE_WIDE: i64 = 1200;
/// How strong the halo round a curve's line stands; it is worked out at this share of the
/// size (final pixels per pixel), with this blur there: about eight pixels in the picture.
const HALO: f64 = 0.9;
const HALO_DIVIDER: usize = 4;
const HALO_BLUR: f32 = 2.0;
/// The ridge: steeper than the bevels elsewhere (degrees), turned this strongly towards or
/// from the light by the curve's slope; each flank this share of the line's width; the band
/// below it this many flanks down at most, and a third of the way to the graph's bottom.
const RIDGE_TILT: f64 = 62.0;
const RIDGE_STEEP: f64 = 1.8;
const RIDGE_SHARE: f64 = 0.8;
const BAND_SHARE: f64 = 2.6;
const BAND_DEPTH: f64 = 3.0;
/// The bright flank of a curve's ridge, the flank away from the light (nearly black), and
/// the band beneath.
const RIDGE: (f64, f64) = (0.85, 0.55);
const RIDGE_FAR: (f64, f64) = (0.85, 0.04);
const RIDGE_BAND: (f64, f64) = (0.85, 0.30);

/// curve_halo: a soft halo of `colour` round a curve's line, over `layer` (RGBA): `line` is the
/// line's coverage (L, the layer's size), `scale` the layer's pixels to a pixel of the picture.
/// Worked out small: the line at a quarter of the size, blurred, and raised again.
pub(crate) fn curve_halo(
    layer: &mut Image,
    line: &Image,
    colour: Rgba,
    scale: usize,
) -> Result<()> {
    let (w, h) = layer.size();
    let divider = HALO_DIVIDER * scale;
    // (too small both ways to be soft; a narrow band keeps one)
    if w <= 2 * scale || h <= 2 * scale || (w < 2 * divider && h < 2 * divider) {
        return Ok(());
    }
    // (_halo_table: min(255, int(v * HALO + 0.5)))
    let table: Vec<i64> = (0..256)
        .map(|v| 255.min((v as f64 * HALO + 0.5) as i64))
        .collect();
    let small = line.resize(
        (w / divider).max(1),
        (h / divider).max(1),
        Filter::Box,
        None,
        None,
    )?;
    let mut soft = (small.gaussian_blur(HALO_BLUR, HALO_BLUR)?.point(&table)?).resize(
        w,
        h,
        Filter::Bilinear,
        None,
        None,
    )?;
    if colour[3] < 255 {
        let through: Vec<i64> = (0..256).map(|v| v * colour[3] / 255).collect();
        soft = soft.point(&through)?;
    }
    // (the colour with that alpha laid over the layer: alpha_composite of it)
    let rgb = [colour[0], colour[1], colour[2]].map(|v| v.clamp(0, 255) as u8);
    lp_pixels::tint_over_at(layer, rgb, soft.as_bytes(), w, (0, 0))?;
    Ok(())
}

/// numpy's binary_search_with_guess: the last index whose value is not above `key` (-1 below
/// the first, `len` above the last), looking first about `guess`.
fn search_with_guess(key: f64, arr: &[f64], guess: usize) -> isize {
    const LIKELY_IN_CACHE_SIZE: usize = 8;
    let len = arr.len();
    if key > arr[len - 1] {
        return len as isize;
    } else if key < arr[0] {
        return -1;
    }
    if len <= 4 {
        let mut i = 1;
        while i < len && key >= arr[i] {
            i += 1;
        }
        return i as isize - 1;
    }
    let guess = guess.min(len - 3).max(1);
    let (mut imin, mut imax) = (0, len);
    if key < arr[guess] {
        if key < arr[guess - 1] {
            imax = guess - 1;
            if guess > LIKELY_IN_CACHE_SIZE && key >= arr[guess - LIKELY_IN_CACHE_SIZE] {
                imin = guess - LIKELY_IN_CACHE_SIZE;
            }
        } else {
            return guess as isize - 1;
        }
    } else if key < arr[guess + 1] {
        return guess as isize;
    } else if key < arr[guess + 2] {
        return guess as isize + 1;
    } else {
        imin = guess + 2;
        if guess + LIKELY_IN_CACHE_SIZE + 1 < len && key < arr[guess + LIKELY_IN_CACHE_SIZE] {
            imax = guess + LIKELY_IN_CACHE_SIZE;
        }
    }
    while imin < imax {
        let imid = imin + ((imax - imin) >> 1);
        if key >= arr[imid] {
            imin = imid + 1;
        } else {
            imax = imid;
        }
    }
    imin as isize - 1
}

/// np.interp(at, xp, fp) in doubles, as numpy's arr_interp works it out: the first and the
/// last value beyond the ends, a point's own value where it falls on one exactly.
fn interp(at: &[f64], xp: &[f64], fp: &[f64]) -> Vec<f64> {
    let len = xp.len();
    let (left, right) = (fp[0], fp[len - 1]);
    if len == 1 {
        return (at.iter())
            .map(|&x| {
                if x < xp[0] {
                    left
                } else if x > xp[0] {
                    right
                } else {
                    fp[0]
                }
            })
            .collect();
    }
    let mut j = 0usize;
    let mut out = Vec::with_capacity(at.len());
    for &x in at {
        if x.is_nan() {
            out.push(x);
            continue;
        }
        let found = search_with_guess(x, xp, j);
        let value = if found == -1 {
            left
        } else if found == len as isize {
            right
        } else {
            j = found as usize;
            if j == len - 1 || xp[j] == x {
                fp[j]
            } else {
                let slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]);
                let mut v = slope * (x - xp[j]) + fp[j];
                if v.is_nan() {
                    v = slope * (x - xp[j + 1]) + fp[j + 1];
                    if v.is_nan() && fp[j] == fp[j + 1] {
                        v = fp[j];
                    }
                }
                v
            }
        };
        if found == -1 {
            j = 0;
        } else if found == len as isize {
            j = len;
        }
        out.push(value);
    }
    out
}

/// A band of a ridge laid by `over`: its rows from `lo` to `hi` in each column, its colour in
/// each, and the share of each of its pixels it takes (the layer's own coverage there).
struct RidgeBand {
    lo: Vec<f32>,
    hi: Vec<f32>,
    colour: Vec<[f32; 3]>,
    weight: Option<Image>,
}

/// _over: lay `band` over `acc` (premultiplied RGB of the strip starting at row `rows0`, `n_rows`
/// of them) and `alpha`: each row takes the share of it the band covers, so a curve moving by a
/// fraction of a pixel does not jump. (numpy's dtypes: the share in doubles, kept in float32.)
fn over(acc: &mut [[f32; 3]], alpha: &mut [f32], n_rows: usize, rows0: i64, band: &RidgeBand) {
    let n_cols = band.lo.len();
    if n_cols == 0 {
        return;
    }
    let span = (0..n_cols)
        .map(|c| band.hi[c] - band.lo[c])
        .fold(
            f32::NEG_INFINITY,
            |m, v| if v > m || v.is_nan() { v } else { m },
        );
    let depth = f64::from(span).ceil() as i64 + 1;
    if depth <= 0 {
        return;
    }
    for c in 0..n_cols {
        let (lo, hi) = (band.lo[c], band.hi[c]);
        let first = f64::from(lo).floor() as i64;
        for d in 0..depth {
            let row = first + d;
            let top = (row as f32).max(lo);
            let cover = ((row as f64 + 1.0).min(f64::from(hi)) - f64::from(top)).clamp(0.0, 1.0);
            let local = row - rows0;
            if local < 0 || local >= n_rows as i64 || cover.is_nan() || cover <= 0.0 {
                continue;
            }
            let r = local as usize;
            let share = match &band.weight {
                Some(fill) => cover * (f64::from(fill.pixel(c, r)[0]) / 255.0),
                None => cover,
            };
            let at = r * n_cols + c;
            for (v, &tint) in acc[at].iter_mut().zip(&band.colour[c]) {
                *v = (f64::from(tint) * share + f64::from(*v) * (1.0 - share)) as f32;
            }
            alpha[at] = (share + f64::from(alpha[at]) * (1.0 - share)) as f32;
        }
    }
}

/// The least and the greatest of float32 numbers, as numpy's min() and max() (NaN wins).
fn least32(v: &[f32]) -> f32 {
    v.iter().fold(
        f32::INFINITY,
        |m, &x| if x < m || x.is_nan() { x } else { m },
    )
}

fn most32(v: &[f32]) -> f32 {
    v.iter().fold(
        f32::NEG_INFINITY,
        |m, &x| if x > m || x.is_nan() { x } else { m },
    )
}

/// curve_ridge: the line of a curve on `layer` (RGBA, the fill already drawn) replaced by a
/// ridge: a flank lit from the top left on the side away from the fill, a dark one on the side
/// towards it, and a faint band beneath, each tilted by how the curve rises or falls there.
/// `run` is the curve's points in the layer's pixels, `thick` the line's width in them; a
/// `wide` graph gets one band only; `below`: the fill lies below the curve (else above it, and
/// the ridge is mirrored, its light still from the top left). Worked out in the picture's
/// pixels (`scale` of the layer's to one) and laid on the layer enlarged. False where the
/// points do not run from left to right: the caller draws a line.
#[allow(clippy::too_many_arguments)]
pub(crate) fn curve_ridge(
    layer: &mut Image,
    run: &[(f64, f64)],
    thick: f64,
    colour: Rgba,
    wide: bool,
    scale: usize,
    below: bool,
) -> Result<bool> {
    let sf = scale as f32;
    let xs: Vec<f32> = run.iter().map(|p| p.0 as f32 / sf).collect();
    let ys: Vec<f32> = run.iter().map(|p| p.1 as f32 / sf).collect();
    let thick = thick / scale as f64;
    let (lw, lh) = layer.size();
    let (width, height) = (lw.div_ceil(scale) as i64, lh.div_ceil(scale) as i64);
    let back = -1e-3f64 as f32;
    if xs.len() < 2 || xs.windows(2).any(|p| p[1] - p[0] < back) {
        return Ok(false);
    }
    let x0 = (f64::from(xs[0]).floor() as i64).max(0);
    let x1 = (f64::from(xs[xs.len() - 1]).ceil() as i64).min(width);
    if x1 - x0 < 2 {
        return Ok(false);
    }
    // (np.interp of the columns' middles, in doubles, made float32)
    let at: Vec<f64> = (x0..x1).map(|i| f64::from(i as f32 + 0.5)).collect();
    let xp: Vec<f64> = xs.iter().map(|&v| f64::from(v)).collect();
    let fp: Vec<f64> = ys.iter().map(|&v| f64::from(v)).collect();
    let y: Vec<f32> = interp(&at, &xp, &fp)
        .into_iter()
        .map(|v| v as f32)
        .collect();
    let n = y.len();
    // (np.gradient: the middle ones over two columns, the ends over one)
    let gain = (if wide { 1.0 } else { RIDGE_STEEP }) as f32;
    let steep: Vec<f32> = (0..n)
        .map(|i| {
            let g = if i == 0 {
                y[1] - y[0]
            } else if i == n - 1 {
                y[n - 1] - y[n - 2]
            } else {
                (y[i + 1] - y[i - 1]) / 2.0
            };
            g * gain
        })
        .collect();
    let (tilt_sin, tilt_cos) = if wide {
        (sin45(), sin45())
    } else {
        let tilt = RIDGE_TILT.to_radians();
        (tilt.sin(), tilt.cos())
    };
    let (ts, tc) = (tilt_sin as f32, tilt_cos as f32);
    // (the outside below the curve where it hangs from the top: its flank faces down)
    let flip = if below { 1.0f32 } else { -1.0 };
    let out: Vec<[f32; 3]> = (steep.iter())
        .map(|&s| {
            let length = (1.0 + s * s).sqrt();
            [s / length * ts * flip, -1.0 / length * ts * flip, tc]
        })
        .collect();
    let away = if below { 1.0f64 } else { -1.0 };
    let base = [colour[0], colour[1], colour[2]].map(|v| v as f32);
    let light = light();
    let paint = |normals: &[[f32; 3]], style: (f64, f64)| -> Vec<[f32; 3]> {
        (normals.iter())
            .map(|&n| {
                let (a, b) = shade(facing(n, light), style);
                base.map(|c| clip(c * a + b, 0.0, 255.0))
            })
            .collect()
    };
    // (the rows `near` to `far` beyond the line, towards the fill: `y + away * near`, a number
    // of rows for all columns worked out in doubles, or one for each column in float32)
    let rows =
        |offset: &dyn Fn(usize) -> f32| -> Vec<f32> { (0..n).map(|i| y[i] + offset(i)).collect() };
    let zone = |near: Vec<f32>, far: Vec<f32>| if below { (near, far) } else { (far, near) };
    let number = |rows_beyond: f64| rows(&|_| (away * rows_beyond) as f32);
    let each = |rows_beyond: &[f32]| rows(&|i| away as f32 * rows_beyond[i]);
    let mut bands = vec![];
    let (rows0, rows1);
    if wide {
        // (the line itself, shaded by the slope)
        let (lo, hi) = zone(number(-thick / 2.0), number(thick));
        rows0 = f64::from(least32(&lo)).floor() as i64 - 1;
        rows1 = f64::from(most32(&hi)).ceil() as i64 + 2;
        let tint = paint(&out, RIDGE_BAND);
        bands.push(RidgeBand {
            lo,
            hi,
            colour: tint,
            weight: None,
        });
    } else {
        let flank = RIDGE_SHARE * thick;
        let flank32 = flank as f32;
        let (least, most) = (flank32, (BAND_SHARE * flank) as f32);
        let end: Vec<f32> = (y.iter())
            .map(|&v| {
                let avail = if below { (height - 1) as f32 - v } else { v };
                clip(avail / BAND_DEPTH as f32, least, most) + flank32
            })
            .collect();
        // (tilted the other way)
        let inner: Vec<[f32; 3]> = out.iter().map(|n| [-n[0], -n[1], n[2]]).collect();
        let (lo, hi) = zone(number(-flank), each(&end));
        rows0 = f64::from(least32(&lo)).floor() as i64 - 1;
        rows1 = f64::from(most32(&hi)).ceil() as i64 + 2;
        // (a graph that is high enough has a band below, as much of each pixel as the layer
        // covers there)
        let deep = (2.0 * flank + 1e-3) as f32;
        if end.iter().any(|&e| e > deep) {
            let above = rows0.max(0);
            let bottom = height.min(rows1);
            if bottom < above {
                return Err(Failed("Coordinate 'lower' is less than 'upper'".into()));
            }
            let s = scale as i64;
            let cover = layer.crop(x0 * s, above * s, x1 * s, bottom * s);
            let cover = cover.getchannel(3)?;
            let fill = if scale > 1 {
                cover.reduce(scale, scale, None)?
            } else {
                cover
            };
            let lower: Vec<f32> = end.iter().map(|&e| e - flank32).collect();
            let (lo, hi) = zone(number(flank), each(&lower));
            let tint = paint(&out, RIDGE_BAND);
            bands.push(RidgeBand {
                lo,
                hi,
                colour: tint,
                weight: Some(fill),
            });
        }
        let (lo, hi) = zone(number(0.0), number(flank));
        let tint = paint(&inner, RIDGE_FAR);
        bands.push(RidgeBand {
            lo,
            hi,
            colour: tint,
            weight: None,
        });
        let (lo, hi) = zone(number(-flank), number(0.0));
        let tint = paint(&out, RIDGE);
        bands.push(RidgeBand {
            lo,
            hi,
            colour: tint,
            weight: None,
        });
    }
    let (rows0, rows1) = (rows0.max(0), rows1.min(height));
    if rows1 <= rows0 {
        return Ok(false);
    }
    let (n_rows, n_cols) = ((rows1 - rows0) as usize, n);
    let mut acc = vec![[0f32; 3]; n_rows * n_cols];
    let mut alpha = vec![0f32; n_rows * n_cols];
    for band in &bands {
        over(&mut acc, &mut alpha, n_rows, rows0, band);
    }
    let opacity = (colour[3] as f64 / 255.0) as f32;
    let floor = 1e-6f64 as f32;
    let byte = |v: f32| clip(v + 0.5, 0.0, 255.0) as u8;
    // (the picture's pixels, each laid on the layer `scale` times larger, no further than it)
    let (left, top) = (x0 as usize * scale, rows0 as usize * scale);
    let (pw, ph) = (
        (n_cols * scale).min(lw - left),
        (n_rows * scale).min(lh - top),
    );
    let mut picture = vec![0u8; pw * ph * 4];
    for (py, line) in picture.chunks_exact_mut(pw * 4).enumerate() {
        let r = py / scale;
        for (px, pixel) in line.chunks_exact_mut(4).enumerate() {
            let at = r * n_cols + px / scale;
            let a = alpha[at];
            let safe = if a > floor || a.is_nan() { a } else { floor };
            for (v, &sum) in pixel.iter_mut().zip(&acc[at]) {
                *v = byte(sum / safe);
            }
            pixel[3] = byte(a * 255.0 * opacity);
        }
    }
    let picture = Image::from_bytes(Mode::RGBA, pw, ph, picture)?;
    let whole = (0, 0, pw as i64, ph as i64);
    layer.alpha_composite_at(&picture, (left as i64, top as i64), whole)?;
    Ok(true)
}

/// For lp-check: curve_halo of a layer.
pub fn check_halo(layer: &mut Image, line: &Image, colour: [i64; 4], scale: usize) -> Result<()> {
    curve_halo(layer, line, colour, scale)
}

/// For lp-check: curve_ridge of a run on a layer (whether it was drawn).
#[allow(clippy::too_many_arguments)]
pub fn check_ridge(
    layer: &mut Image,
    run: &[(f64, f64)],
    thick: f64,
    colour: [i64; 4],
    wide: bool,
    scale: usize,
    below: bool,
) -> Result<bool> {
    curve_ridge(layer, run, thick, colour, wide, scale, below)
}
