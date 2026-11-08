//! The shapes of the weather symbols and their two finishes (render/weather_art.py): the
//! outlines of the cloud, the cloud behind it, the bolt and the moon's crescent are Meteocons'
//! ("fill", by Bas Milius, MIT licence: assets/NOTICE.md), drawn at any size from their paths in
//! Meteocons' view box of 128 units; the sun, its rays, the drops, the snow crystals, the bands
//! of fog and the stars from their measures in the same units. ``flat`` gives each shape its
//! colour; ``glass`` finishes it as frosted glass lit from above. Polygons are drawn four times
//! the size and reduced; the rest is worked out on the pixels as numpy works it out: in float32,
//! a Python number taken as a float32 where it meets one (NEP 50), numpy's own exp.

use std::f64::consts::PI;

use lp_pixels::{alpha_composite, Draw, Image, Mode};

use crate::np;
use crate::piece::Result;

/// Meteocons' view box: every measure here is in its units.
pub(crate) const UNIT: f64 = 128.0;
/// Polygons are drawn this much larger and reduced.
pub(crate) const OVER: usize = 4;
/// The shapes a seventh larger than the view box holds them, about its middle.
const ZOOM: f64 = 1.14;
/// Straight pieces a curve is cut into.
const STEPS: usize = 20;
const INK: Option<[u8; 4]> = Some([255, 0, 0, 0]);

/// Meteocons' outlines (fill set, 3.0): the cloud, the one behind it, the bolt, the moon of a
/// clear night and the smaller one of a cloudy night.
fn path(name: &str) -> &'static str {
    match name {
        "cloud" => concat!(
            "M55.262 48.475c4.86-7.864 15.035-11.095 23.553-7.532 8.506 3.56 13.323 13.06 ",
            "11.088 22.022l-.161.65.669-.03c7.01-.306 13.089 5.407 13.089 12.443 0 6.811-5.728 12.472",
            "-12.523 12.472H37.954c-6.826.002-12.751-5.33-13.395-12.14-.643-6.808 4.178-13.148 10.884",
            "-14.415l.483-.092-.084-.484c-.816-4.745 1.284-9.652 5.263-12.356 3.99-2.712 9.34-2.86 ",
            "13.475-.373l.423.255z",
        ),
        "back" => concat!(
            "M83.84 48.693c2.404-3.735 7.375-5.164 11.478-3.516 4.043 1.624 6.496 6.012 5.392 ",
            "10.26l-.17.653.675-.029c3.281-.137 6.285 2.404 6.285 5.713 0 3.202-2.831 5.726-6.011 ",
            "5.726H74.977c-3.21 0-6.132-2.382-6.448-5.593-.315-3.2 2.088-6.066 5.235-6.636l.491-.09",
            "-.088-.49c-.394-2.198.645-4.442 2.518-5.664 1.925-1.256 4.492-1.32 6.483-.17l.413.237z",
        ),
        "bolt" => concat!(
            "m71.173 68.5-7.616 14.541-.384.731h11.829l-18.05 24.12 3.537-16.88.127-.603h-7.912",
            "L60.355 68.5z",
        ),
        "moon" => concat!(
            "M60.302 32.582C55.282 53.7 73.6 74.348 95.325 72.515 91.52 85.77 79.2 95.5 64.536 ",
            "95.5 46.837 95.5 32.5 81.344 32.5 63.898c0-16.03 12.107-29.27 27.802-31.316",
        ),
        "moon_small" => concat!(
            "M35.115 34.595c-1.737 8.568 5.638 16.62 14.198 16.188-1.64 5.05-6.424 8.717",
            "-12.095 8.717-7.03 0-12.718-5.621-12.718-12.541 0-6.214 4.588-11.375 10.615-12.364",
        ),
        _ => "",
    }
}

/// A token of a path as weather_art's _TOKEN finds it: a command, or a number
/// (`[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?`).
enum Token {
    Command(char),
    Number(f64),
}

/// _TOKEN.findall: the tokens left to right, what matches neither skipped.
fn tokens(text: &str) -> Vec<Token> {
    let b = text.as_bytes();
    let digits = |mut i: usize| {
        while i < b.len() && b[i].is_ascii_digit() {
            i += 1;
        }
        i
    };
    let mut out = vec![];
    let mut i = 0;
    while i < b.len() {
        let c = b[i] as char;
        if "MmLlHhVvCcSsZz".contains(c) {
            out.push(Token::Command(c));
            i += 1;
            continue;
        }
        let start = i;
        let mut j = if c == '-' || c == '+' { i + 1 } else { i };
        let int_end = digits(j);
        let has_int = int_end > j;
        j = int_end;
        let mut matched = has_int;
        if j < b.len() && b[j] == b'.' {
            let frac_end = digits(j + 1);
            if frac_end > j + 1 {
                (j, matched) = (frac_end, true);
            } else if has_int {
                j += 1; // ("5.": a whole number and its point)
            }
        }
        if !matched {
            i += 1;
            continue;
        }
        if j < b.len() && (b[j] == b'e' || b[j] == b'E') {
            let mut e = j + 1;
            if e < b.len() && (b[e] == b'-' || b[e] == b'+') {
                e += 1;
            }
            let exp_end = digits(e);
            if exp_end > e {
                j = exp_end;
            }
        }
        out.push(Token::Number(text[start..j].parse().unwrap_or(0.0)));
        i = j;
    }
    out
}

/// outline: the subpaths of path `name` (absolute and relative M, L, H, V, C, S and Z), each a
/// polygon in units, its curves cut into STEPS pieces, in doubles as numpy cuts them.
pub(crate) fn outline(name: &str) -> Vec<Vec<(f64, f64)>> {
    let tokens = tokens(path(name));
    let mut out = vec![];
    let mut points: Vec<(f64, f64)> = vec![];
    let (mut x, mut y) = (0.0f64, 0.0f64);
    let mut start = (0.0, 0.0);
    let mut control: Option<(f64, f64)> = None;
    let (mut i, mut command) = (0usize, 'M');
    let number = |i: &mut usize| {
        *i += 1;
        match tokens.get(*i - 1) {
            Some(Token::Number(v)) => *v,
            _ => 0.0,
        }
    };
    // (np.linspace(0, 1, STEPS + 1)[1:]: i times a twentieth, the last one 1)
    let step = 1.0 / STEPS as f64;
    let ts: Vec<f64> = (1..=STEPS)
        .map(|i| if i == STEPS { 1.0 } else { i as f64 * step })
        .collect();
    while i < tokens.len() {
        if let Token::Command(c) = tokens[i] {
            command = c;
            i += 1;
        }
        let relative = command.is_ascii_lowercase();
        let c = command.to_ascii_uppercase();
        let (ox, oy) = if relative { (x, y) } else { (0.0, 0.0) };
        match c {
            'Z' => {
                if !points.is_empty() {
                    out.push(std::mem::take(&mut points));
                }
                (x, y) = start;
                control = None;
            }
            'M' => {
                if !points.is_empty() {
                    out.push(std::mem::take(&mut points));
                }
                x = ox + number(&mut i);
                y = oy + number(&mut i);
                points = vec![(x, y)];
                start = (x, y);
                control = None;
                command = if relative { 'l' } else { 'L' };
            }
            'L' | 'H' | 'V' => {
                match c {
                    'L' => {
                        x = ox + number(&mut i);
                        y = oy + number(&mut i);
                    }
                    'H' => x = ox + number(&mut i),
                    _ => y = oy + number(&mut i),
                }
                points.push((x, y));
                control = None;
            }
            _ => {
                let c1 = if c == 'C' {
                    (ox + number(&mut i), oy + number(&mut i))
                } else {
                    match control {
                        Some((cx, cy)) => (2.0 * x - cx, 2.0 * y - cy),
                        None => (x, y),
                    }
                };
                let c2 = (ox + number(&mut i), oy + number(&mut i));
                let end = (ox + number(&mut i), oy + number(&mut i));
                for &t in &ts {
                    let u = 1.0 - t;
                    // (u**3 * p + 3*u*u*t * c1 + 3*u*t*t * c2 + t**3 * end, left to right; the
                    // cubes the C library's pow, as numpy's)
                    let (u3, t3) = (u.powf(3.0), t.powf(3.0));
                    let at = |p: f64, q1: f64, q2: f64, e: f64| {
                        u3 * p + 3.0 * u * u * t * q1 + 3.0 * u * t * t * q2 + t3 * e
                    };
                    points.push((at(x, c1.0, c2.0, end.0), at(y, c1.1, c2.1, end.1)));
                }
                control = Some(c2);
                (x, y) = end;
            }
        }
    }
    if !points.is_empty() {
        out.push(points);
    }
    out
}

// -- coverage --------------------------------------------------------------------------------

/// A plane of float32s, row by row (numpy's two-dimensional arrays).
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Plane {
    pub(crate) w: usize,
    pub(crate) h: usize,
    pub(crate) v: Vec<f32>,
}

impl Plane {
    pub(crate) fn zeros(w: usize, h: usize) -> Plane {
        Plane {
            w,
            h,
            v: vec![0.0; w * h],
        }
    }

    /// Each pixel's value of `f` (column, row), row by row.
    fn of(w: usize, h: usize, f: impl Fn(usize, usize) -> f32) -> Plane {
        let mut v = Vec::with_capacity(w * h);
        for y in 0..h {
            for x in 0..w {
                v.push(f(x, y));
            }
        }
        Plane { w, h, v }
    }

    fn map(&self, f: impl Fn(f32) -> f32) -> Plane {
        Plane {
            w: self.w,
            h: self.h,
            v: self.v.iter().map(|&a| f(a)).collect(),
        }
    }

    fn zip(&self, other: &Plane, f: impl Fn(f32, f32) -> f32) -> Plane {
        Plane {
            w: self.w,
            h: self.h,
            v: self
                .v
                .iter()
                .zip(&other.v)
                .map(|(&a, &b)| f(a, b))
                .collect(),
        }
    }

    /// An L picture's levels, each as numpy takes it to a float32 (`f`).
    pub(crate) fn of_levels(image: &Image, f: impl Fn(u8) -> f32) -> Plane {
        Plane {
            w: image.width(),
            h: image.height(),
            v: image.as_bytes().iter().map(|&b| f(b)).collect(),
        }
    }

    /// The plane as an L picture: np.clip(cov * 255 + 0.5, 0, 255).astype(np.uint8).
    pub(crate) fn levels(&self) -> Result<Image> {
        let data = self.v.iter().map(|&c| byte(c)).collect();
        Ok(Image::from_bytes(Mode::L, self.w, self.h, data)?)
    }
}

/// A float32 0 to 1 made a byte, as weather_art makes it: clip(v * 255 + 0.5), truncated.
pub(crate) fn byte(v: f32) -> u8 {
    (v * 255.0 + 0.5).clamp(0.0, 255.0) as u8
}

/// A Python number where it meets a float32 (NEP 50): made a float32.
pub(crate) fn f(v: f64) -> f32 {
    v as f32
}

/// clip(v, 0, 1) of a float32.
fn clip01(v: f32) -> f32 {
    v.clamp(0.0, 1.0)
}

/// Pixels a unit, on a `k` px symbol.
pub(crate) fn px(k: usize) -> f64 {
    k as f64 * ZOOM / UNIT
}

/// Where `v` units along (or down) lies on a `k` px symbol, in pixels.
pub(crate) fn at(k: usize, v: f64) -> f64 {
    k as f64 / 2.0 + (v - UNIT / 2.0) * px(k)
}

/// A clear L canvas OVER times `k` px for coverage.
fn canvas(k: usize) -> Image {
    Image::blank(Mode::L, k * OVER, k * OVER)
}

/// The canvas reduced: np.asarray(image.reduce(OVER), np.float32) * (1 / 255).
fn reduced(image: &Image) -> Result<Plane> {
    let small = image.reduce(OVER, OVER, None)?;
    let scale = f(1.0 / 255.0);
    Ok(Plane::of_levels(&small, |b| f32::from(b) * scale))
}

/// cover: how much of each pixel of a `k` px picture path `name` covers, drawn `scale` times
/// its size about the middle of the view box and moved by (dx, dy) units.
pub(crate) fn cover(name: &str, k: usize, scale: f64, dx: f64, dy: f64) -> Result<Plane> {
    let mut image = canvas(k);
    let (mid, s, over) = (UNIT / 2.0, px(k), OVER as f64);
    let half = k as f64 / 2.0;
    {
        let mut draw = Draw::new(&mut image);
        for poly in outline(name) {
            let q: Vec<(f64, f64)> = poly
                .iter()
                .map(|&(x, y)| {
                    (
                        (half + ((x - mid) * scale + dx) * s) * over,
                        (half + ((y - mid) * scale + dy) * s) * over,
                    )
                })
                .collect();
            draw.polygon(&q, INK, None, 1)?;
        }
    }
    reduced(&image)
}

/// A stroke from (x0, y0) to (x1, y1) of `width` with round ends, on a canvas (px of it).
fn stroke(
    draw: &mut Draw<'_, Image>,
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
    width: i32,
    round: f64,
) -> Result<()> {
    draw.line(&[(x0, y0), (x1, y1)], INK, width, false)?;
    for (x, y) in [(x0, y0), (x1, y1)] {
        let r = round / 2.0;
        draw.ellipse((x - r, y - r, x + r, y + r), INK, None, 1)?;
    }
    Ok(())
}

/// strokes: the coverage of straight strokes `width` units wide with round ends, each from
/// (x0, y0) to (x1, y1) units.
pub(crate) fn strokes(k: usize, lines: &[(f64, f64, f64, f64)], width: f64) -> Result<Plane> {
    let mut image = canvas(k);
    let over = OVER as f64;
    let w = width * px(k) * over;
    let thick = (lp_py::round(w) as i32).max(1);
    {
        let mut draw = Draw::new(&mut image);
        for &(x0, y0, x1, y1) in lines {
            let [x0, y0, x1, y1] = [x0, y0, x1, y1].map(|v| at(k, v) * over);
            stroke(&mut draw, x0, y0, x1, y1, thick, w)?;
        }
    }
    reduced(&image)
}

/// disc: a disc's coverage, worked out for each pixel, and each pixel's distance from its
/// outline (px, less than 0 inside).
pub(crate) fn disc(k: usize, cx: f64, cy: f64, r: f64) -> (Plane, Plane) {
    let (ax, ay, rr) = (f(at(k, cx)), f(at(k, cy)), f(r * px(k)));
    let d = Plane::of(k, k, |x, y| {
        (x as f32 + 0.5 - ax).hypot(y as f32 + 0.5 - ay) - rr
    });
    (d.map(|d| clip01(0.5 - d)), d)
}

/// blurred: `cov` blurred (Pillow's Gaussian of `radius` px).
pub(crate) fn blurred(cov: &Plane, radius: f64) -> Result<Plane> {
    let image = cov.levels()?;
    let r = f(radius);
    let soft = if r == 0.0 {
        image
    } else {
        image.gaussian_blur(r, r)?
    };
    let scale = f(1.0 / 255.0);
    Ok(Plane::of_levels(&soft, |b| f32::from(b) * scale))
}

/// _shifted: `cov` moved by (dx, dy) px (0 or more each, in fractions), clear where it came
/// from: down, then across, each a whole step and the share of the next.
pub(crate) fn shifted(cov: &Plane, dx: f64, dy: f64) -> Plane {
    let mut out = cov.clone();
    for (down, by) in [(true, dy), (false, dx)] {
        let whole = by.trunc() as i64;
        let part = by - whole as f64;
        let mut moved = Plane::zeros(out.w, out.h);
        for (step, share) in [(whole, 1.0 - part), (whole + 1, part)] {
            let n = if down { out.h } else { out.w } as i64;
            if share <= 0.0 || step >= n {
                continue;
            }
            let (share, step) = (f(share), step as usize);
            for y in 0..out.h {
                for x in 0..out.w {
                    let from = if down {
                        y.checked_sub(step).map(|sy| sy * out.w + x)
                    } else {
                        x.checked_sub(step).map(|sx| y * out.w + sx)
                    };
                    if let Some(from) = from {
                        moved.v[y * out.w + x] += share * out.v[from];
                    }
                }
            }
        }
        out = moved;
    }
    out
}

/// _ring: the outline's own pixels: what a pixel's step inwards takes away.
pub(crate) fn ring(cov: &Plane) -> Plane {
    let (w, h) = (cov.w as i64, cov.h as i64);
    let at = |x: i64, y: i64| {
        if x < 0 || y < 0 || x >= w || y >= h {
            0.0
        } else {
            cov.v[(y * w + x) as usize]
        }
    };
    Plane::of(cov.w, cov.h, |x, y| {
        let (x, y) = (x as i64, y as i64);
        let inner = [
            at(x, y),
            at(x, y - 1),
            at(x, y + 1),
            at(x - 1, y),
            at(x + 1, y),
        ]
        .into_iter()
        .fold(f32::INFINITY, f32::min);
        clip01(at(x, y) - inner)
    })
}

// -- colour ----------------------------------------------------------------------------------

/// A colour's red, green and blue as numpy holds them (float32; levels, or what lighter gave).
pub(crate) type Rgb = [f32; 3];

/// A colour of levels as a float32 Rgb.
pub(crate) fn levels_of(c: [i64; 4]) -> Rgb {
    [c[0] as f32, c[1] as f32, c[2] as f32]
}

/// unit: a colour's red, green and blue as 0 to 1 (divided by 255).
pub(crate) fn unit(c: Rgb) -> Rgb {
    c.map(|v| v / 255.0)
}

/// lighter: a colour `share` of the way to white (0 to 1 a channel).
pub(crate) fn lighter(c: Rgb, share: f64) -> Rgb {
    let share = f(share);
    unit(c).map(|v| v + (1.0 - v) * share)
}

/// `colour` (0 to 1) `share` of the way to white, as numpy's `base + (1 - base) * share`.
fn towards_white(base: Rgb, share: f64) -> Rgb {
    let share = f(share);
    base.map(|v| v + (1.0 - v) * share)
}

/// picture: a picture (RGBA) of colours (0 to 1; `rgb` of each pixel, column and row) and
/// coverage.
pub(crate) fn picture(alpha: &Plane, rgb: impl Fn(usize, usize) -> Rgb) -> Result<Image> {
    let mut data = Vec::with_capacity(alpha.w * alpha.h * 4);
    for y in 0..alpha.h {
        for x in 0..alpha.w {
            let c = rgb(x, y);
            data.extend([
                byte(c[0]),
                byte(c[1]),
                byte(c[2]),
                byte(alpha.v[y * alpha.w + x]),
            ]);
        }
    }
    Ok(Image::from_bytes(Mode::RGBA, alpha.w, alpha.h, data)?)
}

/// flat: `cov` in a colour.
pub(crate) fn flat(cov: &Plane, colour: Rgb) -> Result<Image> {
    let c = unit(colour);
    picture(cov, |_, _| c)
}

/// _rows: the first and the last row (px, the bottom one past it) where `cov` covers anything.
fn rows(cov: &Plane) -> (f64, f64) {
    let floor = f(0.01);
    let covered: Vec<usize> = (0..cov.h)
        .filter(|&y| {
            cov.v[y * cov.w..(y + 1) * cov.w]
                .iter()
                .fold(f32::NEG_INFINITY, |m, &v| m.max(v))
                > floor
        })
        .collect();
    match (covered.first(), covered.last()) {
        (Some(&a), Some(&b)) => (a as f64, (b + 1) as f64),
        _ => (0.0, cov.h as f64),
    }
}

/// The shares down a picture of `h` rows from `top` to `bottom` (np.clip((rows + 0.5 - top) /
/// max(1, bottom - top), 0, 1)).
fn down(h: usize, top: f64, bottom: f64) -> Vec<f32> {
    let (top, span) = (f(top), f(1.0f64.max(bottom - top)));
    (0..h)
        .map(|y| clip01((y as f32 + 0.5 - top) / span))
        .collect()
}

/// glass: `cov` as frosted glass in a colour, lit from above: `lift` of the way to white at its
/// top, the colour times `deep` at its bottom, a breath of shade inside its floor and an edge of
/// light (`rim`) just inside the top of its outline, faintly all round.
pub(crate) fn glass(
    cov: &Plane,
    colour: Rgb,
    k: usize,
    lift: f64,
    deep: f64,
    rim: f64,
) -> Result<Image> {
    let base = unit(colour);
    let (top, bottom) = rows(cov);
    let t = down(cov.h, top, bottom);
    let high = towards_white(base, lift);
    let low = base.map(|b| b * f(deep));
    let soft = blurred(cov, 1.0f64.max(k as f64 * 0.03))?;
    let near = soft.zip(cov, |b, c| clip01(2.0 * (1.0 - b)) * c);
    let (from, by) = (f(0.55), f(0.45));
    let w = 0.7f64.max(k as f64 * 0.008);
    let (moved, outline) = (shifted(cov, 0.0, w), ring(cov));
    let (faint, rim) = (f(0.18), f(rim));
    let edge = cov.zip(&moved, |c, m| clip01(c - m));
    let edge = edge.zip(&outline, |e, o| e.max(faint * o) * rim);
    let light = towards_white(base, 0.92);
    let shade = f(0.05);
    picture(cov, |x, y| {
        let i = y * cov.w + x;
        let floor = clip01((t[y] - from) / by) * near.v[i];
        let e = edge.v[i];
        [0, 1, 2].map(|c| {
            let rgb = high[c] * (1.0 - t[y]) + low[c] * t[y];
            let rgb = rgb * (1.0 - shade * floor);
            rgb * (1.0 - e) + light[c] * e
        })
    })
}

// -- the sky ---------------------------------------------------------------------------------

/// The pixels' middles across and down (np.mgrid as float32, + 0.5).
fn middle(x: usize, y: usize) -> (f32, f32) {
    (x as f32 + 0.5, y as f32 + 0.5)
}

/// sun_core: the sun's disc at (cx, cy) units, `r` units round: lit, lighter at its top and
/// towards its top left, a fine deeper edge; else in its colour.
pub(crate) fn sun_core(
    k: usize,
    cx: f64,
    cy: f64,
    r: f64,
    colour: Rgb,
    lit: bool,
) -> Result<Image> {
    let (cov, d) = disc(k, cx, cy, r);
    if !lit {
        return flat(&cov, colour);
    }
    let s = px(k);
    let base = unit(colour);
    let (ty, tspan) = (f(at(k, cy - r)), f(2.0 * r * s));
    let high = towards_white(base, 0.38);
    let (sx, sy) = (f(at(k, cx - 0.35 * r)), f(at(k, cy - 0.4 * r)));
    let spread = 0.55 * r * s;
    let spread = f(2.0 * (spread * spread));
    let (glint, deep, edge_by) = (f(0.28), f(0.12), f(0.6f64.max(k as f64 * 0.006)));
    let off = f(0.6);
    picture(&cov, |x, y| {
        let (xs, ys) = middle(x, y);
        let t = clip01((ys - ty) / tspan);
        let (a, b) = (xs - sx, ys - sy);
        let spot = np::expf(-(a * a + b * b) / spread);
        let edge = clip01(1.0 - (d.v[y * k + x] + off).abs() / edge_by);
        [0, 1, 2].map(|c| {
            let rgb = high[c] * (1.0 - t) + base[c] * t;
            let rgb = rgb + (1.0 - rgb) * (glint * spot);
            rgb * (1.0 - deep * edge)
        })
    })
}

/// rays: the sun's eight rays round (cx, cy), from `inner` to `outer` units off its middle,
/// `width` units wide with round ends; lit, a little lighter at their roots.
#[allow(clippy::too_many_arguments)]
pub(crate) fn rays(
    k: usize,
    cx: f64,
    cy: f64,
    inner: f64,
    outer: f64,
    width: f64,
    colour: Rgb,
    lit: bool,
) -> Result<Image> {
    let lines: Vec<(f64, f64, f64, f64)> = (0..8)
        .map(|i| {
            let a = i as f64 * PI / 4.0 - PI / 2.0;
            let (c, s) = (a.cos(), a.sin());
            (
                cx + inner * c,
                cy + inner * s,
                cx + outer * c,
                cy + outer * s,
            )
        })
        .collect();
    let cov = strokes(k, &lines, width)?;
    if !lit {
        return flat(&cov, colour);
    }
    let (ax, ay, unit_px) = (f(at(k, cx)), f(at(k, cy)), f(px(k)));
    let (from, span) = (f(inner), f(1.0f64.max(outer - inner)));
    let base = unit(colour);
    let root = towards_white(base, 0.22);
    picture(&cov, |x, y| {
        let (xs, ys) = middle(x, y);
        let away = (xs - ax).hypot(ys - ay) / unit_px;
        let away = clip01((away - from) / span);
        [0, 1, 2].map(|c| root[c] * (1.0 - away) + base[c] * away)
    })
}

/// corona: the light round the sun: soft, from under its disc out to over three times it.
pub(crate) fn corona(k: usize, cx: f64, cy: f64, r: f64, colour: Rgb) -> Result<Image> {
    let (_cov, d) = disc(k, cx, cy, r);
    let rr = f(r * px(k));
    let (fade, strength, power) = (f(2.2), f(0.32), f(2.2));
    let alpha = d.map(|d| strength * clip01(1.0 - d / rr / fade).powf(power));
    let c = lighter(colour, 0.15);
    picture(&alpha, |_, _| c)
}

/// glow: light round `cov`: its coverage blurred by `radius` px, `strength` at most (in
/// `colour` as unit takes it).
pub(crate) fn glow(cov: &Plane, colour: Rgb, radius: f64, strength: f64) -> Result<Image> {
    let strength = f(strength);
    let alpha = blurred(cov, radius)?.map(|b| b * strength);
    let c = unit(colour);
    picture(&alpha, |_, _| c)
}

/// moon: the moon's crescent (path `name`) and its coverage: lit, lighter at its top, with an
/// edge of light along its outer side and a soft light round it; else in its colour.
#[allow(clippy::too_many_arguments)]
pub(crate) fn moon(
    k: usize,
    name: &str,
    colour: Rgb,
    lit: bool,
    scale: f64,
    dx: f64,
    dy: f64,
) -> Result<(Image, Plane)> {
    let cov = cover(name, k, scale, dx, dy)?;
    if !lit {
        return Ok((flat(&cov, colour)?, cov));
    }
    let base = unit(colour);
    let (top, bottom) = rows(&cov);
    let t = down(k, top, bottom);
    let high = towards_white(base, 0.55);
    let low = base.map(|b| b * f(0.92));
    let w = 0.7f64.max(k as f64 * 0.007);
    let moved = shifted(&cov, 0.6 * w, 0.8 * w);
    let edge = cov.zip(&moved, |c, m| clip01(c - m) * f(0.8));
    let crescent = picture(&cov, |x, y| {
        let e = edge.v[y * k + x];
        [0, 1, 2].map(|c| {
            let rgb = high[c] * (1.0 - t[y]) + low[c] * t[y];
            rgb * (1.0 - e) + e
        })
    })?;
    // (its glow's colour a lighter one taken by unit once more, as weather_art takes it)
    let out = glow(
        &cov,
        lighter(colour, 0.5),
        1.0f64.max(k as f64 * 0.05),
        0.35,
    )?;
    Ok((alpha_composite(&out, &crescent)?, cov))
}

/// star: a star of four points at (x, y) units, `r` units from its middle to a point: lit, its
/// points fine and its middle soft; else a plain one.
pub(crate) fn star(k: usize, x: f64, y: f64, r: f64, colour: Rgb, lit: bool) -> Result<Image> {
    if !lit {
        let mut image = canvas(k);
        let over = OVER as f64;
        let (a, q) = (0.28 * r * px(k) * over, r * px(k) * over);
        let (cx, cy) = (at(k, x) * over, at(k, y) * over);
        let points = [
            (cx, cy - q),
            (cx + a, cy - a),
            (cx + q, cy),
            (cx + a, cy + a),
            (cx, cy + q),
            (cx - a, cy + a),
            (cx - q, cy),
            (cx - a, cy - a),
        ];
        Draw::new(&mut image).polygon(&points, INK, None, 1)?;
        return flat(&reduced(&image)?, colour);
    }
    let (sx, sy) = (f(at(k, x)), f(at(k, y)));
    let rr = (r * px(k)).max(1.2);
    let (long, short, rr32) = (f(3.2), f(0.55), f(rr));
    let (soft, fall, gain) = (f(1.2), f(rr * 1.4), f(1.6));
    let cov = Plane::of(k, k, |px_, py_| {
        let (xs, ys) = middle(px_, py_);
        let (ax, ay) = ((xs - sx).abs(), (ys - sy).abs());
        let d = (ax * long + ay * short).min(ay * long + ax * short) - rr32;
        let c = clip01(0.5 - d / soft) * np::expf(-ax.hypot(ay) / fall);
        clip01(c * gain)
    });
    let c = lighter(colour, 0.4);
    picture(&cov, |_, _| c)
}

// -- what falls ------------------------------------------------------------------------------

/// drop: a drop on a picture of its own, its top `top` units wide and its foot `bottom`, its
/// length in units: lit, a streak that tapers upwards, lighter and `faint` at its top; else a
/// plain stroke of its foot's width.
#[allow(clippy::too_many_arguments)]
pub(crate) fn drop(
    k: usize,
    length: f64,
    top: f64,
    bottom: f64,
    colour: Rgb,
    lit: bool,
    faint: f64,
) -> Result<Image> {
    let s = px(k);
    let w = 3.max(((bottom + 2.0) * s).ceil() as i64) as usize;
    let h = 4.max(((length + bottom + 2.0) * s).ceil() as i64) as usize;
    let (x0, y0, y1) = (
        w as f64 / 2.0,
        (1.0 + bottom / 2.0) * s,
        (1.0 + bottom / 2.0 + length) * s,
    );
    let (x0, y032, span, fall) = (f(x0), f(y0), f((y1 - y0).max(1e-6)), f(y1 - y0));
    let (top32, taper, bottom32, s32, least) = (f(top), f(bottom - top), f(bottom), f(s), f(0.35));
    let t = Plane::of(w, h, |_, y| clip01((y as f32 + 0.5 - y032) / span));
    let cov = Plane::of(w, h, |x, y| {
        let (xs, ys) = middle(x, y);
        let t = t.v[y * w + x];
        let half = if lit { top32 + taper * t } else { bottom32 } * s32 / 2.0;
        let d = (xs - x0).hypot(ys - (y032 + t * fall)) - half.max(least);
        clip01(0.5 - d)
    });
    if !lit {
        return flat(&cov, colour);
    }
    let base = unit(colour);
    let high = towards_white(base, 0.35);
    let (faint, rest, power) = (f(faint), f(1.0 - faint), f(0.8));
    let alpha = cov.zip(&t, |c, t| c * (faint + rest * t.powf(power)));
    picture(&alpha, |x, y| {
        let t = t.v[y * w + x];
        [0, 1, 2].map(|c| high[c] * (1.0 - t) + base[c] * t)
    })
}

/// flake: a snow crystal of six arms `size` px across, its arms `width` px wide, in the middle
/// of a picture `side` px: branched, two pairs of branches an arm; else three plain strokes
/// crossing.
pub(crate) fn flake(
    side: f64,
    size: f64,
    width: f64,
    colour: Rgb,
    branched: bool,
) -> Result<Image> {
    let n = 3.max(side.ceil() as i64) as usize;
    let over = OVER as f64;
    let mut image = Image::blank(Mode::L, n * OVER, n * OVER);
    let (mid, arm) = ((n * OVER) as f64 / 2.0, size * over / 2.0);
    let thick = (lp_py::round(width * over) as i32).max(1);
    let branch = (lp_py::round(f64::from(thick) * 0.8) as i32).max(1);
    {
        let mut draw = Draw::new(&mut image);
        for i in 0..6 {
            let a = i as f64 * PI / 3.0 - PI / 2.0;
            let tip = (mid + arm * a.cos(), mid + arm * a.sin());
            stroke(&mut draw, mid, mid, tip.0, tip.1, thick, f64::from(thick))?;
            if !branched {
                continue;
            }
            for (along, length) in [(0.45, 0.32), (0.72, 0.22)] {
                let (bx, by) = (mid + arm * along * a.cos(), mid + arm * along * a.sin());
                for turn in [-PI / 3.0, PI / 3.0] {
                    let ex = bx + arm * length * (a + turn).cos();
                    let ey = by + arm * length * (a + turn).sin();
                    stroke(&mut draw, bx, by, ex, ey, branch, f64::from(branch))?;
                }
            }
        }
    }
    flat(&reduced(&image)?, colour)
}

/// band: a band of fog from x0 to x1 at y (units), `width` units thick: lit, a veil a little
/// lighter than its colour, soft at its edges and fading out towards its ends; else a plain
/// stroke with round ends.
#[allow(clippy::too_many_arguments)]
pub(crate) fn band(
    k: usize,
    x0: f64,
    x1: f64,
    y: f64,
    width: f64,
    colour: Rgb,
    opacity: f64,
    lit: bool,
) -> Result<Image> {
    let opacity32 = f(opacity);
    if !lit {
        let cov = strokes(k, &[(x0, y, x1, y)], width)?.map(|c| c * opacity32);
        return flat(&cov, colour);
    }
    let s = px(k);
    let (left, right) = (at(k, x0), at(k, x1));
    let (l32, r32, yy) = (f(left), f(right), f(at(k, y)));
    let (half, soft_by, span) = (
        f(width * s / 2.0),
        f(1.0f64.max(width * s * 0.45)),
        f(right - left),
    );
    let (reach, power) = (f(0.34), f(1.6));
    let alpha = Plane::of(k, k, |x, y| {
        let (xs, ys) = middle(x, y);
        let d = (xs - xs.clamp(l32, r32)).hypot(ys - yy) - half;
        let soft = clip01(0.5 - d / soft_by);
        let u = clip01((xs - l32) / span);
        let ends = clip01(u.min(1.0 - u) / reach).powf(power);
        soft * ends * opacity32
    });
    let c = lighter(colour, 0.2);
    picture(&alpha, |_, _| c)
}

/// bolt: the bolt (moved down by `dy` units) as it flashes (`flash`), or between flashes, a
/// little deeper: lit, lighter at its top, glowing as it flashes; else in its colour, lighter
/// as it flashes.
pub(crate) fn bolt(k: usize, colour: Rgb, lit: bool, flash: bool, dy: f64) -> Result<Image> {
    let cov = cover("bolt", k, 1.0, 0.0, dy)?;
    let base = unit(colour);
    if !lit {
        let c = if flash {
            lighter(colour, 0.35)
        } else {
            base.map(|b| b * f(0.85))
        };
        return picture(&cov, |_, _| c);
    }
    let (ty, tspan) = (f(at(k, 68.0 + dy)), f(40.0 * px(k)));
    let t: Vec<f32> = (0..k)
        .map(|y| clip01((y as f32 + 0.5 - ty) / tspan))
        .collect();
    if !flash {
        let high = towards_white(base, 0.15);
        let low = base.map(|b| b * f(0.9));
        return picture(&cov, |_, y| {
            [0, 1, 2].map(|c| high[c] * (1.0 - t[y]) + low[c] * t[y])
        });
    }
    let (high, low) = (towards_white(base, 0.65), towards_white(base, 0.3));
    let body = picture(&cov, |_, y| {
        [0, 1, 2].map(|c| high[c] * (1.0 - t[y]) + low[c] * t[y])
    })?;
    let out = glow(
        &cov,
        lighter(colour, 0.4),
        1.0f64.max(k as f64 * 0.035),
        0.75,
    )?;
    Ok(alpha_composite(&out, &body)?)
}
