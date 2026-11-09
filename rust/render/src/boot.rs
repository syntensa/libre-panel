//! The panel's own two screens (render/bootscreen.py): the boot logo it shows when it is
//! powered on, and the standby clip it plays when the PC's USB comes up, until Libre Panel
//! streams. Each look is drawn for the panel's room (its size, in landscape or upright, less
//! the strip the bezel hides and the quiet ends of the long side; a round panel's circle) in
//! floating point, and made 8 bits only at the very end, with a dither of a level's worth.
//!
//! In bootscreen.py's own numbers: float32 where its arrays are, the Python floats doubles
//! made float32 where numpy takes them as weak scalars; the box blurs' running sums in doubles
//! as np.cumsum makes them, a wide blur done at a fraction of the size by Pillow's resampling
//! of float pictures; the shapes drawn by Pillow at three times the size and reduced; the C
//! library's powf and hypotf, which numpy takes where it has no SVML (numpy on Linux with
//! AVX-512 takes SVML's power, a unit in the last place apart now and then), and its pow, exp
//! and cos where Python's floats meet them; the dither numpy's default_rng draws. So far Libre
//! Panel's own look (render/bootlooks.py's others follow).

use std::f64::consts::PI;
use std::hint::black_box;

use lp_devices::models::PanelModel;
use lp_pixels::random::Pcg64;
use lp_pixels::{Draw, Filter, FloatImage, Image, Mode, ALL_CORNERS};
use lp_text::text::{draw, Text};
use lp_text::Font;

use crate::branding::{self, libre};
use crate::piece::{Failed, Result};

/// Libre Panel's own clip: four sequences of the 11 pictures the panel's decoder takes per
/// sequence, at the rate the panel itself sets at a cold start (44 pictures: about 1.5 s).
pub const CLIP_FRAMES: usize = 44;
pub const CLIP_FPS: u32 = 30;
pub const SEQUENCE: usize = 11;
/// Of the long side, at both ends: the boot JPEG leaves it as it was.
const QUIET: f64 = 64.0 / 1920.0;
/// Lumen's dots.
const DOTS: [u8; 3] = [22, 30, 40];
/// The supersampling of the shapes.
pub(crate) const SS: usize = 3;
/// The look of the panel's own screens where none is named.
pub const DEFAULT: &str = "libre-panel";
/// The ink of the shapes on a picture of their coverage.
pub(crate) const WHITE: [u8; 4] = [255, 0, 0, 0];

pub(crate) fn failed(e: lp_pixels::Error) -> Failed {
    Failed(e.0)
}

/// A Python float as numpy takes it beside a float32 array (a weak scalar): the float32 nearest.
pub(crate) fn weak(x: f64) -> f32 {
    x as f32
}

/// A colour as np.asarray(colour, np.float32) makes it.
pub(crate) fn floats(c: [u8; 3]) -> [f32; 3] {
    c.map(f32::from)
}

/// Python's max(a, b) of floats: b only where it is greater.
pub(crate) fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python's min(a, b) of floats: b only where it is less.
pub(crate) fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// Python's x ** y of floats as float_pow has it: its own cases, else the C library's pow
/// (of the magnitude, the sign put back for an odd whole exponent).
pub(crate) fn py_pow(x: f64, y: f64) -> f64 {
    if y == 0.0 || x == 1.0 {
        return 1.0;
    }
    if x < 0.0 && y == y.floor() {
        let odd = (y / 2.0).floor() * 2.0 != y;
        let p = (-x).powf(black_box(y));
        return if odd { -p } else { p };
    }
    x.powf(black_box(y))
}

/// np.clip of a float32 (_NPY_MIN(_NPY_MAX(x, lo), hi), a NaN kept).
pub(crate) fn clip(x: f32, lo: f32, hi: f32) -> f32 {
    let x = if x.is_nan() || x > lo { x } else { lo };
    if x.is_nan() || x < hi {
        x
    } else {
        hi
    }
}

/// np.maximum of two float32s.
pub(crate) fn maximum(a: f32, b: f32) -> f32 {
    if a >= b || a.is_nan() {
        a
    } else {
        b
    }
}

/// np.minimum of two float32s.
pub(crate) fn minimum(a: f32, b: f32) -> f32 {
    if a <= b || a.is_nan() {
        a
    } else {
        b
    }
}

/// The rows (or columns) a Python slice start:stop of `len` takes.
pub(crate) fn py_slice(start: i64, stop: i64, len: usize) -> (usize, usize) {
    let n = len as i64;
    let norm = |v: i64| if v < 0 { (v + n).max(0) } else { v.min(n) };
    let (a, b) = (norm(start), norm(stop));
    (a as usize, b.max(a) as usize)
}

// -- where a picture's parts go ---------------------------------------------------------------

/// Room: a picture `w` x `h` and the box its parts may take (x0, y0 to x1, y1): without the
/// strip the bezel hides and the quiet ends of the long side; `round`: a round panel, whose
/// parts keep within the circle.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Room {
    pub w: usize,
    pub h: usize,
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
    pub round: bool,
}

impl Room {
    pub fn cx(&self) -> f64 {
        (self.x0 + self.x1) / 2.0
    }

    pub fn cy(&self) -> f64 {
        (self.y0 + self.y1) / 2.0
    }

    pub fn rw(&self) -> f64 {
        self.x1 - self.x0
    }

    pub fn rh(&self) -> f64 {
        self.y1 - self.y0
    }

    /// px per px of a picture 480 px across its short side.
    pub fn unit(&self) -> f64 {
        py_min(self.rw(), self.rh()) / 480.0
    }

    pub fn quiet(&self) -> i64 {
        lp_py::round(QUIET * self.w.max(self.h) as f64) as i64
    }
}

/// room: the room of `model`'s picture in `orientation` ("landscape": a round one square;
/// "portrait": upright, for a bar on end); `scale`: smaller, for the app's pictures of it.
pub fn room(model: &PanelModel, orientation: &str, scale: f64) -> Room {
    let (w, h) = model.size(orientation);
    let w = 8.max(lp_py::round(w as f64 * scale) as i64);
    let h = 8.max(lp_py::round(h as f64 * scale) as i64);
    let [top, right, bottom, left] = model.hidden_edges(orientation).map(|px| px as f64 * scale);
    let quiet = lp_py::round(QUIET * w.max(h) as f64) as i64;
    let (across, along) = if w >= h { (quiet, 0) } else { (0, quiet) };
    Room {
        w: w as usize,
        h: h as usize,
        x0: left + across as f64,
        y0: top + along as f64,
        x1: w as f64 - right - across as f64,
        y1: h as f64 - bottom - along as f64,
        round: model.shape == "round",
    }
}

// -- light in floating point ------------------------------------------------------------------

/// A picture in floating point (bootscreen.Canvas, rows x columns x 3, 0 to 255), or a plane of
/// one band: a float32 array as numpy keeps it, row by row.
#[derive(Clone, Debug)]
pub struct Canvas {
    pub h: usize,
    pub w: usize,
    /// bands: 3, or 1 for a plane
    pub c: usize,
    pub data: Vec<f32>,
}

impl Canvas {
    pub(crate) fn zeros(h: usize, w: usize, c: usize) -> Canvas {
        Canvas {
            h,
            w,
            c,
            data: vec![0.0; h * w * c],
        }
    }

    pub(crate) fn at(&self, y: usize, x: usize, k: usize) -> usize {
        (y * self.w + x) * self.c + k
    }
}

/// _boxes: the widths of three box blurs that together blur as a Gaussian of `sigma`.
fn boxes(sigma: f64) -> [i64; 3] {
    let ideal = (12.0 * sigma * sigma / 3.0 + 1.0).sqrt();
    let whole = ideal as i64;
    let low = whole - (1 - whole.rem_euclid(2)); // (odd)
    let m = (12.0 * sigma * sigma - (3 * low * low) as f64 - (12 * low) as f64 - 9.0)
        / (-4 * low - 4) as f64;
    let m = lp_py::round(m) as i64;
    [0, 1, 2].map(|i| if i < m { low } else { low + 2 })
}

/// _box: the mean over 2r + 1 along `axis` (0: down the rows, 1: along them) of doubles kept
/// as `a` (h x w x c); beyond the picture is dark. As numpy has it: the line padded with r + 1
/// zeros before and r after, summed from its start (np.cumsum), each mean the difference of
/// two of the sums over 2r + 1.
fn box_mean(a: &[f64], (h, w, c): (usize, usize, usize), r: usize, axis: usize) -> Vec<f64> {
    let (n, step, lines) = if axis == 0 {
        (h, w * c, w * c)
    } else {
        (w, c, h * c)
    };
    let div = (2 * r + 1) as f64;
    let mut out = vec![0.0; a.len()];
    let mut sums = vec![0.0f64; n + 2 * r + 1];
    for line in 0..lines {
        let base = if axis == 0 {
            line
        } else {
            (line / c) * w * c + line % c
        };
        let mut acc = 0.0f64;
        for (j, sum) in sums.iter_mut().enumerate() {
            let p = if j > r && j <= r + n {
                a[base + (j - r - 1) * step]
            } else {
                0.0
            };
            acc = if j == 0 { p } else { acc + p };
            *sum = acc;
        }
        for i in 0..n {
            out[base + i * step] = (sums[i + 2 * r + 1] - sums[i]) / div;
        }
    }
    out
}

/// _resized: a float array brought to (w, h), band by band, as Pillow resizes a picture of
/// mode F (bilinear).
fn resized(a: &Canvas, (w, h): (usize, usize)) -> Result<Canvas> {
    let mut out = Canvas::zeros(h, w, a.c);
    for k in 0..a.c {
        let plane: Vec<f32> = (0..a.h * a.w).map(|i| a.data[i * a.c + k]).collect();
        let made = FloatImage::from_vec(a.w, a.h, plane)
            .and_then(|p| p.resize(w, h, Filter::Bilinear, None))
            .map_err(failed)?;
        for (i, &v) in made.data.iter().enumerate() {
            out.data[i * a.c + k] = v;
        }
    }
    Ok(out)
}

/// blur: `a` blurred as with a Gaussian of `sigma` px, in floating point: three box blurs each
/// way (in doubles); a wide one blurred at a fraction of the size and brought back.
pub(crate) fn blur(a: &Canvas, sigma: f64) -> Result<Canvas> {
    if sigma < 0.4 {
        return Ok(a.clone());
    }
    let shrink = 1.max((sigma / 3.0) as i64);
    if shrink > 1 {
        let (w, h) = (a.w, a.h);
        let small = |n: usize| 1.max(lp_py::round(n as f64 / shrink as f64) as i64) as usize;
        let less = resized(a, (small(w), small(h)))?;
        return resized(&blur(&less, sigma / shrink as f64)?, (w, h));
    }
    let shape = (a.h, a.w, a.c);
    let mut out: Vec<f64> = a.data.iter().map(|&v| f64::from(v)).collect();
    for width in boxes(sigma) {
        for axis in [0, 1] {
            out = box_mean(&out, shape, (width / 2) as usize, axis);
        }
    }
    Ok(Canvas {
        h: a.h,
        w: a.w,
        c: a.c,
        data: out.iter().map(|&v| v as f32).collect(),
    })
}

/// coverage: how much of each pixel (0 to 1) what `draw(picture, s)` draws covers: it draws
/// on a picture of mode L `ss` times the size (`s` its coordinates' scale), reduced.
pub(crate) fn coverage(
    (w, h): (usize, usize),
    ss: usize,
    draw: impl FnOnce(&mut Image, f64) -> Result<()>,
) -> Result<Canvas> {
    let mut image = Image::blank(Mode::L, w * ss, h * ss);
    draw(&mut image, ss as f64)?;
    let image = if ss > 1 {
        image.reduce(ss, ss, None).map_err(failed)?
    } else {
        image
    };
    Ok(Canvas {
        h,
        w,
        c: 1,
        data: image
            .as_bytes()
            .iter()
            .map(|&v| f32::from(v) / 255.0)
            .collect(),
    })
}

/// paint: `colour` over the canvas where `cover` covers it, by `alpha`.
pub(crate) fn paint(canvas: &mut Canvas, cover: &Canvas, colour: [f32; 3], alpha: f64) {
    let a = weak(alpha);
    for y in 0..canvas.h.min(cover.h) {
        for x in 0..canvas.w.min(cover.w) {
            let m = cover.data[y * cover.w + x] * a;
            for (k, &c) in colour.iter().enumerate() {
                let at = canvas.at(y, x, k);
                let p = canvas.data[at];
                canvas.data[at] = p + (c - p) * m;
            }
        }
    }
}

/// shine: light added (placed at `at`), as a glow adds to what is under it.
pub(crate) fn shine(canvas: &mut Canvas, light: &Canvas, (x, y): (i64, i64)) {
    let (x0, y0) = (x.max(0), y.max(0));
    let x1 = (canvas.w as i64).min(x + light.w as i64);
    let y1 = (canvas.h as i64).min(y + light.h as i64);
    for yy in y0..y1 {
        for xx in x0..x1 {
            for k in 0..3 {
                let from = light.at((yy - y) as usize, (xx - x) as usize, k);
                let at = canvas.at(yy as usize, xx as usize, k);
                canvas.data[at] += light.data[from];
            }
        }
    }
}

/// tinted: a plane as light of `colour` (by `gain`).
pub(crate) fn tinted(cover: &Canvas, colour: [f32; 3], gain: f64) -> Canvas {
    let g = weak(gain);
    let ink = colour.map(|c| c * g);
    let mut data = Vec::with_capacity(cover.data.len() * 3);
    for &v in &cover.data {
        data.extend(ink.map(|c| v * c));
    }
    Canvas {
        h: cover.h,
        w: cover.w,
        c: 3,
        data,
    }
}

/// glow: the glow of `light` added to the canvas: blurred by each (sigma, gain) of `parts`,
/// only in the box round what is lit (a pixel above 1e-3 in any band). `light` is a plane of
/// `colour`, or three bands with `colour` None.
pub(crate) fn glow(
    canvas: &mut Canvas,
    light: &Canvas,
    colour: Option<[f32; 3]>,
    parts: &[(f64, f64)],
) -> Result<()> {
    let (h, w) = (light.h, light.w);
    let edge = weak(1e-3);
    let lit = |i: usize| {
        if light.c == 1 {
            light.data[i]
        } else {
            let px = &light.data[i * 3..i * 3 + 3];
            maximum(maximum(px[0], px[1]), px[2])
        }
    };
    let mut found: Option<(usize, usize, usize, usize)> = None;
    for y in 0..h {
        for x in 0..w {
            if lit(y * w + x) > edge {
                found = Some(match found {
                    None => (y, y, x, x),
                    Some((r0, r1, c0, c1)) => (r0.min(y), r1.max(y), c0.min(x), c1.max(x)),
                });
            }
        }
    }
    let Some((r0, r1, c0, c1)) = found else {
        return Ok(());
    };
    let most = parts
        .iter()
        .fold(f64::NEG_INFINITY, |m, &(sigma, _)| py_max(m, sigma));
    let reach = (3.0 * most).ceil() as i64 + 1;
    let y0 = 0.max(r0 as i64 - reach) as usize;
    let y1 = (h as i64).min(r1 as i64 + 1 + reach) as usize;
    let x0 = 0.max(c0 as i64 - reach) as usize;
    let x1 = (w as i64).min(c1 as i64 + 1 + reach) as usize;
    let (ph, pw) = (y1 - y0, x1 - x0);
    let mut part = Canvas::zeros(ph, pw, light.c);
    for y in 0..ph {
        let from = light.at(y0 + y, x0, 0);
        let to = part.at(y, 0, 0);
        part.data[to..to + pw * light.c].copy_from_slice(&light.data[from..from + pw * light.c]);
    }
    let mut out: Option<Canvas> = None;
    for &(sigma, gain) in parts {
        let mut blurred = blur(&part, sigma)?;
        let g = weak(gain);
        for v in &mut blurred.data {
            *v *= g;
        }
        out = Some(match out {
            None => blurred,
            Some(mut sum) => {
                for (s, &v) in sum.data.iter_mut().zip(&blurred.data) {
                    *s += v;
                }
                sum
            }
        });
    }
    let Some(out) = out else {
        return Ok(());
    };
    for y in 0..ph {
        for x in 0..pw {
            for k in 0..3 {
                let v = match colour {
                    Some(c) => out.data[y * pw + x] * c[k],
                    None => out.data[out.at(y, x, k)],
                };
                let at = canvas.at(y0 + y, x0 + x, k);
                canvas.data[at] += v;
            }
        }
    }
    Ok(())
}

/// np.linspace(start, stop, n) of doubles: arange(n) times the step, the start added, the
/// last one `stop` itself.
pub(crate) fn linspace(start: f64, stop: f64, n: usize) -> Vec<f64> {
    if n == 0 {
        return Vec::new();
    }
    let delta = stop - start;
    let mut out: Vec<f64> = if n > 1 {
        let step = delta / (n - 1) as f64;
        if step == 0.0 {
            (0..n).map(|i| i as f64 / (n - 1) as f64 * delta).collect()
        } else {
            (0..n).map(|i| i as f64 * step).collect()
        }
    } else {
        vec![0.0 * delta]
    };
    for v in &mut out {
        *v += start;
    }
    if n > 1 {
        out[n - 1] = stop;
    }
    out
}

/// np.remainder of float32s (npy_divmodf's: fmodf, the sign of the divisor's).
pub(crate) fn remainder_f32(a: f32, b: f32) -> f32 {
    let m = a % b;
    if b == 0.0 {
        return m;
    }
    if m != 0.0 {
        if (b < 0.0) != (m < 0.0) {
            m + b
        } else {
            m
        }
    } else {
        0.0f32.copysign(b)
    }
}

/// np.remainder of doubles (npy_divmod's).
pub(crate) fn remainder_f64(a: f64, b: f64) -> f64 {
    let m = a % b;
    if b == 0.0 {
        return m;
    }
    if m != 0.0 {
        if (b < 0.0) != (m < 0.0) {
            m + b
        } else {
            m
        }
    } else {
        0.0f64.copysign(b)
    }
}

/// vertical: a canvas from `top` to `bottom`, evenly (np.linspace's float32s down it).
pub(crate) fn vertical(h: usize, w: usize, top: [f32; 3], bottom: [f32; 3]) -> Canvas {
    let ys: Vec<f32> = (0..h)
        .map(|i| {
            if h > 1 && i == h - 1 {
                1.0
            } else if h > 1 {
                (i as f64 * (1.0 / (h - 1) as f64)) as f32
            } else {
                0.0
            }
        })
        .collect();
    let diff = [0, 1, 2].map(|k| bottom[k] - top[k]);
    let mut out = Canvas::zeros(h, w, 3);
    for (y, &t) in ys.iter().enumerate() {
        let colour = [0, 1, 2].map(|k| top[k] + diff[k] * t);
        for x in 0..w {
            let at = out.at(y, x, 0);
            out.data[at..at + 3].copy_from_slice(&colour);
        }
    }
    out
}

/// _dither: a level's worth of noise (triangular, -1 to 1), the same for every picture of a
/// size: numpy's default_rng(w * 7919 + h), two pictures of its float32s added.
fn dither(w: usize, h: usize) -> Vec<f32> {
    let mut rng = Pcg64::new((w * 7919 + h) as u32);
    let first: Vec<f32> = (0..w * h).map(|_| rng.next_f32()).collect();
    first.iter().map(|&a| (a + rng.next_f32()) - 1.0).collect()
}

/// _ends: how much of a picture shows (0 to 1): the short ends fade to black over a little
/// more than the quiet ends; a round panel's corners are dark.
fn ends(w: usize, h: usize, quiet: i64, round_panel: bool) -> Vec<f32> {
    let long_axis = w >= h;
    let n = if long_axis { w } else { h };
    let ramp = 1.max(lp_py::round(quiet as f64 * 2.2) as i64) as f32;
    let fade: Vec<f32> = (0..n)
        .map(|i| clip(i as f32 / ramp, 0.0, 1.0).powf(black_box(weak(1.6))))
        .collect();
    let edge: Vec<f32> = (0..n).map(|i| minimum(fade[i], fade[n - 1 - i])).collect();
    let mut out = vec![0.0f32; w * h];
    for y in 0..h {
        for x in 0..w {
            out[y * w + x] = if long_axis { edge[x] } else { edge[y] };
        }
    }
    if round_panel {
        let (mx, my) = (weak((w as f64 - 1.0) / 2.0), weak((h as f64 - 1.0) / 2.0));
        let half = weak(w.min(h) as f64 / 2.0);
        let width = weak(0.04);
        for y in 0..h {
            for x in 0..w {
                let r = (x as f32 - mx).hypot(y as f32 - my) / half;
                out[y * w + x] *= clip((1.0 - r) / width, 0.0, 1.0);
            }
        }
    }
    out
}

/// What finish takes for a room's pictures, made once for them (as bootscreen.py keeps _ends
/// and _dither for a size): how much of each pixel shows, and the dither where there is one.
pub struct Ending {
    shown: Vec<f32>,
    noise: Option<Vec<f32>>,
}

impl Ending {
    pub fn new(room: &Room, with_dither: bool) -> Ending {
        Ending {
            shown: ends(room.w, room.h, room.quiet(), room.round),
            noise: with_dither.then(|| dither(room.w, room.h)),
        }
    }
}

/// finish: the canvas as the RGB picture the panel gets: its ends faded, and made 8 bits with
/// the dither where anything is lit (black stays black); without it for the app's small
/// pictures, whose PNG it would make large.
pub fn finish(canvas: &Canvas, room: &Room, ending: &Ending) -> Result<Image> {
    let mut out = canvas.data.clone();
    for (i, &s) in ending.shown.iter().enumerate() {
        for v in &mut out[i * 3..i * 3 + 3] {
            *v *= s;
        }
    }
    if let Some(noise) = &ending.noise {
        for (i, &d) in noise.iter().enumerate() {
            let px = &mut out[i * 3..i * 3 + 3];
            let lit = clip(maximum(maximum(px[0], px[1]), px[2]), 0.0, 1.0);
            let add = d * lit;
            for v in px.iter_mut() {
                *v += add;
            }
        }
    }
    let bytes: Vec<u8> = out
        .iter()
        .map(|&v| clip((v + 0.5).floor(), 0.0, 255.0) as u8)
        .collect();
    Image::from_bytes(Mode::RGB, room.w, room.h, bytes).map_err(failed)
}

// -- what the looks draw with -----------------------------------------------------------------

/// font: a built-in face at `size` (6 at least), as ImageFont.truetype opens it.
fn font(env: &lp_theme::Env, size: f64, face: &str) -> Result<Font> {
    let path = env
        .builtin_font_path(&format!("builtin:{face}"))
        .ok_or_else(|| Failed(format!("cannot open resource: the face {face}")))?;
    let size = 6.max(lp_py::round(size) as i64);
    lp_text::truetype(&path, size as f64, 0, "", None).map_err(|e| Failed(e.to_string()))
}

/// mark_into: the app's mark (branding.mark), its ring `d` px across round (cx, cy), with its
/// glow in floating point (reaching `glow` of `d`); `light` dims what is lit.
pub(crate) fn mark_into(
    canvas: &mut Canvas,
    (cx, cy): (f64, f64),
    d: f64,
    light: f64,
    glow: f64,
) -> Result<()> {
    let shape = branding::mark(d, light)?;
    let (sw, sh) = (shape.width(), shape.height());
    let rgba: Vec<f32> = shape.as_bytes().iter().map(|&v| f32::from(v)).collect();
    let alpha: Vec<f32> = (0..sw * sh).map(|i| rgba[i * 4 + 3] / 255.0).collect();
    let x = lp_py::round(cx - sw as f64 / 2.0) as i64;
    let y = lp_py::round(cy - sh as f64 / 2.0) as i64;
    if glow > 0.0 {
        let pad = (d * glow * 3.0).ceil() as usize;
        let mut lit = Canvas::zeros(sh + 2 * pad, sw + 2 * pad, 3);
        for yy in 0..sh {
            for xx in 0..sw {
                for k in 0..3 {
                    let at = lit.at(yy + pad, xx + pad, k);
                    lit.data[at] = rgba[(yy * sw + xx) * 4 + k] * alpha[yy * sw + xx];
                }
            }
        }
        let mut halo = blur(&lit, py_max(1.0, d * glow))?;
        let gain = weak(0.5 + 0.35 * light);
        for v in &mut halo.data {
            *v *= gain;
        }
        shine(canvas, &halo, (x - pad as i64, y - pad as i64));
    }
    let (ry0, ry1) = py_slice(0.max(y), y + sh as i64, canvas.h);
    let (rx0, rx1) = py_slice(0.max(x), x + sw as i64, canvas.w);
    let (dy, dx) = (0.max(-y) as usize, 0.max(-x) as usize);
    for yy in 0..(ry1 - ry0).min(sh.saturating_sub(dy)) {
        for xx in 0..(rx1 - rx0).min(sw.saturating_sub(dx)) {
            let from = (dy + yy) * sw + dx + xx;
            let cut = alpha[from];
            for k in 0..3 {
                let at = canvas.at(ry0 + yy, rx0 + xx, k);
                let p = canvas.data[at];
                canvas.data[at] = p + (rgba[from * 4 + k] - p) * cut;
            }
        }
    }
    Ok(())
}

/// text_into: words in their colours, one after the other from `x` on `baseline`, smooth
/// (drawn at three times the size, each in a picture of the canvas's size).
fn text_into(
    canvas: &mut Canvas,
    parts: &[(&str, [f32; 3])],
    (x, baseline): (f64, f64),
    face: &Font,
    alpha: f64,
) -> Result<()> {
    let (w, h) = (canvas.w, canvas.h);
    let big = face
        .variant(Some(face.size() * SS as f64), None, None, None)
        .map_err(|e| Failed(e.to_string()))?;
    let mut at = x;
    for &(words, colour) in parts {
        let here = at;
        let cover = coverage((w, h), SS, |image, s| {
            let text = Text::new(words, &big, "L");
            draw(
                image,
                (here * s, baseline * s),
                &text,
                WHITE,
                None,
                Some("ls"),
                "left",
            )
            .map_err(|e| Failed(e.to_string()))
        })?;
        paint(canvas, &cover, colour, alpha);
        at += face
            .getlength(words, "", None, None, None)
            .map_err(|e| Failed(e.to_string()))?;
    }
    Ok(())
}

// -- Libre Panel's own look -------------------------------------------------------------------

/// A piece of the line of light: its box, the radius of its corners and how lit it is.
type Piece = ((f64, f64, f64, f64), f64, f64);

/// Layout: where the parts of Libre Panel's own picture go: the mark's box (x, y, d), the
/// name's left, the top of its capitals and its size, the line of light (x0, x1, y) and its
/// dashes, and px per px of the 1920 x 480 design.
#[derive(Clone, Copy, Debug)]
struct Layout {
    size: (usize, usize),
    mark: (f64, f64, f64),
    name: (f64, f64, f64),
    line: (f64, f64, f64),
    dashes: usize,
    unit: f64,
}

/// _name_width: "Libre Panel" in the name's face at `size`.
fn name_width(env: &lp_theme::Env, size: f64) -> Result<f64> {
    font(env, size, "Barlow-SemiBold")?
        .getlength("Libre Panel", "", None, None, None)
        .map_err(|e| Failed(e.to_string()))
}

/// layout_in: the parts side by side on a bar, the name under the mark on a broad panel or a
/// bar on end, and smaller within the circle of a round one.
fn layout_in(room: &Room, env: &lp_theme::Env) -> Result<Layout> {
    let (w, h) = (room.w, room.h);
    let (room_w, room_h) = (room.rw(), room.rh());
    let (cx, cy) = (room.cx(), room.cy());
    let aspect = room_w / py_max(1.0, room_h);
    if aspect >= 2.4 {
        // a bar: the mark beside the name, the line under both
        let mut d = room_h * 0.42;
        let mut size = room_h * 0.2;
        let mut gap = d * 0.28;
        let mut group = d + gap + name_width(env, size)?;
        let scale = py_min(1.0, room_w * 0.8 / group);
        (d, size, gap, group) = (d * scale, size * scale, gap * scale, group * scale);
        let x = cx - group / 2.0;
        let block = d + room_h * 0.16;
        let y = cy - block / 2.0;
        let name_top = y + d / 2.0 - 0.7 * size / 2.0;
        let line_y = y + d + room_h * 0.16;
        let half = py_max(group * 0.62, room_w * 0.28);
        return Ok(Layout {
            size: (w, h),
            mark: (x, y, d),
            name: (x + d + gap, name_top, size),
            line: (cx - half, cx + half, line_y),
            dashes: 31,
            unit: room_h / 480.0,
        });
    }
    // broad, square, round or a bar on end: the name under the mark
    let fit = if room.round { 0.62 } else { 0.86 };
    let tall = aspect <= 0.5;
    let d = if tall {
        room_w * 0.56
    } else {
        py_min(room_h * 0.4, room_w * 0.3) * if room.round { 0.78 } else { 1.0 }
    };
    let mut size = d * 0.42;
    let mut name_w = name_width(env, size)?;
    if name_w > room_w * fit {
        size *= room_w * fit / name_w;
        name_w = name_width(env, size)?;
    }
    let block = d + d * 0.24 + 0.7 * size + d * 0.3;
    let y = cy - block / 2.0;
    let name_top = y + d + d * 0.24;
    let line_y = name_top + 0.7 * size + d * 0.3;
    let half = py_min(py_max(name_w * 0.62, d * 0.9), room_w * fit / 2.0);
    let unit = if tall {
        py_min(room_w, room_h) / 480.0
    } else {
        py_min(room_w / 1920.0, room_h / 480.0) * 1.6
    };
    Ok(Layout {
        size: (w, h),
        mark: (cx - d / 2.0, y, d),
        name: (cx - name_w / 2.0, name_top, size),
        line: (cx - half, cx + half, line_y),
        dashes: 21,
        unit,
    })
}

/// backdrop: Lumen's backdrop, a dark gradient with a dot every 22 px (at 480 px high).
fn backdrop(room: &Room) -> Result<Canvas> {
    let (w, h) = (room.w, room.h);
    let mut out = vertical(h, w, floats(libre("bg")), floats(libre("bg2")));
    let unit = w.min(h) as f64 / 480.0;
    let step = 6.max(lp_py::round(22.0 * unit) as i64) as usize;
    let r = py_max(0.6, 1.1 * unit);
    let dots = coverage((w, h), SS, |image, s| {
        let mut draw = Draw::new(image);
        for y in (step / 2..h).step_by(step) {
            for x in (step / 2..w).step_by(step) {
                let (x, y) = (x as f64, y as f64);
                let bounds = ((x - r) * s, (y - r) * s, (x + r) * s, (y + r) * s);
                draw.ellipse(bounds, Some(WHITE), None, 1).map_err(failed)?;
            }
        }
        Ok(())
    })?;
    paint(&mut out, &dots, floats(DOTS), 0.9);
    Ok(out)
}

/// _wave: how lit dash `i` of `count` is at `phase` (0 to 1): a soft wave running out from
/// the middle to both ends, over a light that never goes out.
fn wave(i: usize, count: usize, phase: f64) -> f64 {
    let middle = (count as f64 - 1.0) / 2.0;
    let distance = (i as f64 - middle).abs() / py_max(1.0, middle);
    let front = phase * 1.35 - 0.15;
    let crest = (-py_pow((distance - front) / 0.16, 2.0)).exp();
    0.28 + 0.72 * crest * (1.0 - 0.35 * distance)
}

/// Classic: Libre Panel's own look: the app's mark, the name and the line of light under
/// them, on Lumen's dark backdrop with its dots; in the clip the line a row of dashes a soft
/// wave of light runs along outwards from the middle, the mark's arc breathing with it.
pub struct Classic {
    lay: Layout,
    base: Canvas,
}

impl Classic {
    pub fn new(room: &Room, env: &lp_theme::Env) -> Result<Classic> {
        let lay = layout_in(room, env)?;
        let mut base = backdrop(room)?;
        let (nx, top, size) = lay.name;
        let parts = [
            ("Libre ", floats(libre("text"))),
            ("Panel", floats(libre("accent"))),
        ];
        let face = font(env, size, "Barlow-SemiBold")?;
        text_into(&mut base, &parts, (nx, top + 0.7 * size), &face, 1.0)?;
        Ok(Classic { lay, base })
    }

    fn drawn(&self, phase: Option<f64>) -> Result<Canvas> {
        let mut out = self.base.clone();
        let (x, y, d) = self.lay.mark;
        let breathe = phase.map_or(1.0, |p| 0.75 + 0.25 * (2.0 * PI * p).cos());
        let light = lp_py::round_digits(breathe, 3);
        mark_into(&mut out, (x + d / 2.0, y + d / 2.0), d, light, 0.06)?;
        self.line(&mut out, phase)?;
        Ok(out)
    }

    /// _line: the line of light: one soft line in the boot logo, a row of dashes the light
    /// runs along in the clip; with its glow. Drawn in a band round it.
    fn line(&self, out: &mut Canvas, phase: Option<f64>) -> Result<()> {
        let lay = &self.lay;
        let (x0, x1, y) = lay.line;
        let (w, h) = lay.size;
        let thick = py_max(2.0, 4.0 * lay.unit);
        let reach = py_max(12.0, 70.0 * lay.unit).ceil() as i64; // how far its glow goes
        let (bx0, by0) = (
            0.max(x0.floor() as i64 - reach),
            0.max(y.floor() as i64 - reach),
        );
        let bx1 = (w as i64).min(x1.ceil() as i64 + reach);
        let by1 = (h as i64).min(y.ceil() as i64 + reach);
        let (bw, bh) = ((bx1 - bx0).max(0) as usize, (by1 - by0).max(0) as usize);
        let mut pieces: Vec<Piece> = Vec::new();
        match phase {
            None => {
                // a line, brightest in the middle
                let steps = 64;
                for i in 0..steps {
                    let a = x0 + (x1 - x0) * i as f64 / steps as f64;
                    let b = x0 + (x1 - x0) * (i + 1) as f64 / steps as f64;
                    let middle = ((a + b) / 2.0 - (x0 + x1) / 2.0).abs() / ((x1 - x0) / 2.0);
                    let level = py_pow(1.0 - py_pow(middle, 1.8), 1.2);
                    let bounds = (a, y - thick / 2.0, b + 1.0 / SS as f64, y + thick / 2.0);
                    pieces.push((bounds, 0.0, level));
                }
            }
            Some(p) => {
                // dashes the light runs along
                let count = lay.dashes;
                let step = (x1 - x0) / count as f64;
                for i in 0..count {
                    let a = x0 + i as f64 * step + step * 0.18;
                    let b = x0 + (i + 1) as f64 * step - step * 0.18;
                    let bounds = (a, y - thick / 2.0, b, y + thick / 2.0);
                    pieces.push((bounds, thick / 2.0, wave(i, count, p)));
                }
            }
        }
        let mut lit = Canvas::zeros(bh, bw, 1);
        for (bounds, radius, level) in pieces {
            // each in a box of its own, a pixel round it
            let (px0, py0) = (bounds.0.floor() as i64 - 1, bounds.1.floor() as i64 - 1);
            let (px1, py1) = (bounds.2.ceil() as i64 + 1, bounds.3.ceil() as i64 + 1);
            let size = ((px1 - px0).max(0) as usize, (py1 - py0).max(0) as usize);
            let piece = coverage(size, SS, |image, s| {
                let corners = (
                    (bounds.0 - px0 as f64) * s,
                    (bounds.1 - py0 as f64) * s,
                    (bounds.2 - px0 as f64) * s,
                    (bounds.3 - py0 as f64) * s,
                );
                let mut draw = Draw::new(image);
                if radius != 0.0 {
                    draw.rounded_rectangle(corners, radius * s, Some(WHITE), None, 1, ALL_CORNERS)
                } else {
                    draw.rectangle(corners, Some(WHITE), None, 1)
                }
                .map_err(failed)
            })?;
            let level = weak(level);
            let (ya, yb) = py_slice(py0 - by0, py1 - by0, lit.h);
            let (xa, xb) = py_slice(px0 - bx0, px1 - bx0, lit.w);
            for yy in 0..(yb - ya).min(piece.h) {
                for xx in 0..(xb - xa).min(piece.w) {
                    let v = piece.data[yy * piece.w + xx] * level;
                    let at = (ya + yy) * lit.w + xa + xx;
                    lit.data[at] = maximum(lit.data[at], v);
                }
            }
        }
        let accent = floats(libre("accent"));
        let near = blur(&lit, py_max(2.0, 10.0 * lay.unit))?;
        let mut both = blur(&near, py_max(4.0, 26.0 * lay.unit))?;
        for (v, &n) in both.data.iter_mut().zip(&near.data) {
            *v += n;
        }
        let light = tinted(&both, accent, 1.0);
        let line = tinted(&lit, accent, 1.0);
        for yy in 0..bh {
            for xx in 0..bw {
                for k in 0..3 {
                    let at = out.at(by0 as usize + yy, bx0 as usize + xx, k);
                    let here = light.at(yy, xx, k);
                    out.data[at] = maximum(out.data[at] + light.data[here], line.data[here]);
                }
            }
        }
        Ok(())
    }
}

// -- the looks --------------------------------------------------------------------------------

/// A look drawn for a room: its still (the boot logo) and its frames (the standby clip at a
/// point of its loop, 0 to 1), each a canvas of its own.
pub enum Look {
    Classic(Classic),
    Pulse(crate::bootlooks::Pulse),
    Circuit(crate::bootlooks::Circuit),
    Levels(crate::bootlooks::Levels),
    Dark(crate::bootlooks::Dark),
    Aurora(crate::bootlooks::Aurora),
    Night(crate::bootlooks::Night),
}

impl Look {
    /// The look called `design` (Libre Panel's own for ""), made for `room`; an error for a
    /// look there is not (Python's KeyError) or not yet in Rust.
    pub fn new(design: &str, room: &Room, env: &lp_theme::Env) -> Result<Look> {
        use crate::bootlooks::{Aurora, Circuit, Dark, Levels, Night, Pulse};
        Ok(match if design.is_empty() { DEFAULT } else { design } {
            DEFAULT => Look::Classic(Classic::new(room, env)?),
            "pulse" => Look::Pulse(Pulse::new(room)?),
            "circuit" => Look::Circuit(Circuit::new(room)?),
            "levels" => Look::Levels(Levels::new(room)?),
            "dark" => Look::Dark(Dark::new(room)),
            "aurora" => Look::Aurora(Aurora::new(room)?),
            "night" => Look::Night(Night::new(room)?),
            other => {
                return Err(Failed(format!(
                    "no look of the panel's own screens: {other:?}"
                )))
            }
        })
    }

    /// The clip's pictures of the look (a multiple of SEQUENCE).
    pub fn frames(&self) -> usize {
        match self {
            Look::Classic(_) => CLIP_FRAMES,
            Look::Pulse(_) | Look::Circuit(_) => 66,
            Look::Levels(_) => 44, // (it moves everywhere: a short loop fits)
            Look::Dark(_) | Look::Aurora(_) | Look::Night(_) => 88,
        }
    }

    pub fn still(&self) -> Result<Canvas> {
        match self {
            Look::Classic(look) => look.drawn(None),
            Look::Pulse(look) => look.still(),
            Look::Circuit(look) => look.still(),
            Look::Levels(look) => look.still(),
            Look::Dark(look) => look.still(),
            Look::Aurora(look) => look.still(),
            Look::Night(look) => look.still(),
        }
    }

    pub fn frame(&self, phase: f64) -> Result<Canvas> {
        match self {
            Look::Classic(look) => look.drawn(Some(phase)),
            Look::Pulse(look) => look.frame(phase),
            Look::Circuit(look) => look.frame(phase),
            Look::Levels(look) => look.frame(phase),
            Look::Dark(look) => look.frame(phase),
            Look::Aurora(look) => look.frame(phase),
            Look::Night(look) => look.frame(phase),
        }
    }
}

/// boot_logo: the boot logo for `model` in `orientation`, in the look `design`; `scale`:
/// smaller, for the app (without the dither).
pub fn boot_logo(
    env: &lp_theme::Env,
    model: &PanelModel,
    orientation: &str,
    design: &str,
    scale: f64,
) -> Result<Image> {
    let room = room(model, orientation, scale);
    let look = Look::new(design, &room, env)?;
    finish(&look.still()?, &room, &Ending::new(&room, scale == 1.0))
}

/// standby_frames: the standby clip's pictures for `model`, one loop (`count` of them, else
/// the look's own number), in `orientation`; `scale`: smaller, for the app (without the
/// dither).
pub fn standby_frames(
    env: &lp_theme::Env,
    model: &PanelModel,
    count: Option<usize>,
    orientation: &str,
    design: &str,
    scale: f64,
) -> Result<Vec<Image>> {
    let room = room(model, orientation, scale);
    let look = Look::new(design, &room, env)?;
    let count = count.filter(|&n| n > 0).unwrap_or(look.frames());
    let ending = Ending::new(&room, scale == 1.0);
    (0..count)
        .map(|i| finish(&look.frame(i as f64 / count as f64)?, &room, &ending))
        .collect()
}

/// picture: the picture for `model` in `orientation`: the boot logo with `phase` None, else
/// the standby clip at that point of its loop (0 to 1).
pub fn picture(
    env: &lp_theme::Env,
    model: &PanelModel,
    phase: Option<f64>,
    orientation: &str,
    design: &str,
) -> Result<Image> {
    let room = room(model, orientation, 1.0);
    let look = Look::new(design, &room, env)?;
    let canvas = match phase {
        None => look.still()?,
        Some(p) => look.frame(p)?,
    };
    finish(&canvas, &room, &Ending::new(&room, true))
}
