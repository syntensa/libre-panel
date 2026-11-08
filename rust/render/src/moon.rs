//! The moon as it looks (render/moon.py): a sphere with the near side's seas, craters and
//! rays, lit by the sun at its phase, with a halo as bright as the moon is full.
//!
//! The face (albedo and the slopes of its relief) is made once, at [`FACE`] px, from numpy's
//! default_rng(1969) as moon.py makes it, in numpy's own numbers: float32 where moon.py's
//! arrays are float32 and the scalars Python floats, float64 where a crater's place or a ray's
//! angle is a numpy double, and where it is so the C library's exp, pow, hypot and atan2 as
//! numpy calls them. numpy's own float32 exp and tanh (crate::np) where numpy takes its own;
//! np.convolve's sums as numpy and OpenBLAS make them.

use std::f64::consts::PI;
use std::sync::OnceLock;

use lp_pixels::random::Generator;
use lp_pixels::{alpha_composite, Draw, Filter, FloatImage, Image, Mode};
use lp_py::Dict;

use crate::np;
use crate::piece::{Piece, Result};
use crate::renderer::Renderer;
use crate::shapes::{self, Rgba, SUPERSAMPLE};
use crate::widget;

/// px of the face (albedo and relief), made once
pub const FACE: usize = 512;
/// the disc's share of the picture: the rest is the halo's
pub const DISC: f64 = 0.92;
/// pictures are lit at this many times their size and reduced
const SS: usize = 2;
/// seconds the halo takes to swell and settle
pub const BREATH: f64 = 7.0;
/// the strengths a halo is made in
pub const HALO_LEVELS: i64 = 48;
/// how often a moving moon's halo changes, per second (the renderer's MOON_STEP)
pub const MOON_STEP: f64 = 10.0;
/// px of the largest moon drawn here (lit at twice its size)
pub const LARGEST: i64 = 4096;

/// A sea: its centre (x, y on the disc, -1 to 1, y down), its radii, how dark it is (0 to 1).
type Sea = ((f64, f64), (f64, f64), f64);

/// The seas
const SEAS: [Sea; 13] = [
    ((-0.30, -0.44), (0.30, 0.25), 0.95), // Imbrium
    ((0.24, -0.40), (0.17, 0.15), 0.90),  // Serenitatis
    ((0.38, -0.06), (0.22, 0.17), 0.90),  // Tranquillitatis
    ((0.73, -0.29), (0.10, 0.12), 1.00),  // Crisium
    ((0.62, 0.16), (0.12, 0.16), 0.80),   // Fecunditatis
    ((0.40, 0.31), (0.09, 0.08), 0.75),   // Nectaris
    ((-0.62, -0.08), (0.24, 0.40), 0.85), // Oceanus Procellarum
    ((-0.42, 0.22), (0.20, 0.16), 0.70),  // its southern reach
    ((-0.16, 0.38), (0.16, 0.12), 0.75),  // Nubium
    ((-0.53, 0.43), (0.09, 0.08), 0.85),  // Humorum
    ((-0.05, -0.74), (0.34, 0.07), 0.28), // Frigoris
    ((0.02, -0.17), (0.08, 0.06), 0.65),  // Vaporum
    ((0.0, 0.0), (0.05, 0.05), 0.40),     // Sinus Medii
];

/// Bright young craters: centre, radius, rays (how far, 0: none)
const BRIGHT: [((f64, f64), f64, f64); 4] = [
    ((-0.12, 0.70), 0.035, 1.0),   // Tycho
    ((-0.31, -0.13), 0.035, 0.25), // Copernicus
    ((-0.56, -0.13), 0.022, 0.30), // Kepler
    ((-0.78, -0.33), 0.020, 0.20), // Aristarchus
];

/// A Python float where numpy meets it beside a float32 array: the float32 nearest to it.
fn weak(x: f64) -> f32 {
    x as f32
}

/// np.clip of a float (numpy's _NPY_CLIP: the larger of it and `lo`, then the smaller of that
/// and `hi`, a NaN kept).
fn clip(x: f32, lo: f32, hi: f32) -> f32 {
    let x = if x.is_nan() || x > lo { x } else { lo };
    if x.is_nan() || x < hi {
        x
    } else {
        hi
    }
}

/// np.remainder of doubles (npy_divmod's: fmod, then the sign of the divisor).
fn remainder(a: f64, b: f64) -> f64 {
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

/// int() of a double, for the windows' bounds (moon.py's are never past an i64).
fn trunc(x: f64) -> i64 {
    x as i64
}

/// The near side: albedo (0.35 to 1.15) and the slopes of its relief (d height / dx and
/// d height / dy) in disc coordinates, FACE x FACE, row by row.
pub struct Face {
    pub albedo: Vec<f32>,
    pub gx: Vec<f32>,
    pub gy: Vec<f32>,
}

/// _noise: smooth noise (n, n), about -1 to 1, octaves of random grids enlarged (bicubic, as
/// Pillow enlarges a float picture), each a little more than half as strong as the last.
fn noise(n: usize, rng: &mut Generator, octaves: u32) -> Vec<f32> {
    let mut out = vec![0.0f32; n * n];
    let (mut amp, mut total) = (1.0f64, 0.0f64);
    for octave in 0..octaves {
        let cells = 4usize << octave;
        let mut grid = FloatImage::new(cells, cells);
        for v in grid.data.iter_mut() {
            *v = rng.standard_normal() as f32;
        }
        // (a grid of at least 4 px enlarged to n px: Pillow's checks all pass)
        let image = grid
            .resize(n, n, Filter::Bicubic, None)
            .unwrap_or_else(|_| FloatImage::new(n, n));
        let a = weak(amp);
        for (o, &v) in out.iter_mut().zip(&image.data) {
            *o += a * v;
        }
        total += amp;
        amp *= 0.55;
    }
    let t = weak(total);
    for o in out.iter_mut() {
        *o /= t;
    }
    out
}

/// A window's rows or columns: max(0, int((c - reach + 1) / 2 * n)) to
/// min(n, int((c + reach + 1) / 2 * n) + more).
fn span(c: f64, reach: f64, n: usize, more: i64) -> (i64, i64) {
    let nf = n as f64;
    let lo = trunc((c - reach + 1.0) / 2.0 * nf).max(0);
    let hi = (trunc((c + reach + 1.0) / 2.0 * nf) + more).min(n as i64);
    (lo, hi)
}

/// _crater of a random crater: its centre and radius numpy doubles, so its numbers doubles
/// (np.hypot's and np.exp's the C library's), each added to the float32 tables rounded.
fn crater_double(height: &mut [f32], albedo: &mut [f32], at: &[f32], c: (f64, f64), r: f64) {
    let n = at.len();
    let (cx, cy) = c;
    let reach = r * 1.8;
    let (i0, i1) = span(cy, reach, n, 2);
    let (j0, j1) = span(cx, reach, n, 2);
    if i1 <= i0 || j1 <= j0 {
        return;
    }
    for i in i0 as usize..i1 as usize {
        for j in j0 as usize..j1 as usize {
            let d = (f64::from(at[j]) - cx).hypot(f64::from(at[i]) - cy) / r;
            let bowl = if d < 1.0 { (d * d - 1.0) * 0.55 } else { 0.0 };
            let t = (d - 1.0) / 0.25;
            let rim = 0.22 * (-(t * t)).exp();
            let h = &mut height[i * n + j];
            *h = (f64::from(*h) + r * (bowl + rim)) as f32;
            let t = (d - 1.0) / 0.3;
            let ring = (-(t * t)).exp();
            // (bright is 0.0: its share, 0.0 times a finite exp, adds nothing)
            let a = &mut albedo[i * n + j];
            *a = (f64::from(*a) + 0.015 * ring) as f32;
        }
    }
}

/// _crater of a bright crater: its centre and radius Python floats, so float32 throughout
/// (hypotf the C library's, exp numpy's own).
fn crater_float(h: &mut [f32], albedo: &mut [f32], at: &[f32], c: (f64, f64), r: f64, b: f64) {
    let n = at.len();
    let (cx, cy) = c;
    let reach = r * 1.8;
    let (i0, i1) = span(cy, reach, n, 2);
    let (j0, j1) = span(cx, reach, n, 2);
    if i1 <= i0 || j1 <= j0 {
        return;
    }
    let (fx, fy, fr, bright) = (weak(cx), weak(cy), weak(r), weak(b));
    for i in i0 as usize..i1 as usize {
        for j in j0 as usize..j1 as usize {
            let d = (at[j] - fx).hypot(at[i] - fy) / fr;
            let bowl = if d < 1.0 {
                (d * d - 1.0) * weak(0.55)
            } else {
                0.0
            };
            let t = (d - 1.0) / 0.25;
            let rim = weak(0.22) * np::expf(-(t * t));
            h[i * n + j] += fr * (bowl + rim);
            let t = (d - 1.0) / weak(0.3);
            let ring = np::expf(-(t * t));
            let u = d / weak(1.1);
            albedo[i * n + j] += weak(0.015) * ring + bright * np::expf(-(u * u));
        }
    }
}

/// _rays: bright rays thrown out from a young crater, 26 of them at random angles, each of a
/// random width and length, faint streaks that fade with distance, and the light ground close
/// by. The angles are numpy doubles, so how far a pixel is from a ray's line is a double.
fn rays(albedo: &mut [f32], at: &[f32], c: (f64, f64), r: f64, reach: f64, rng: &mut Generator) {
    let n = at.len();
    let (cx, cy) = c;
    let (i0, i1) = span(cy, reach, n, 1);
    let (j0, j1) = span(cx, reach, n, 1);
    if i1 <= i0 || j1 <= j0 {
        return;
    }
    let (i0, i1, j0, j1) = (i0 as usize, i1 as usize, j0 as usize, j1 as usize);
    let w = j1 - j0;
    let (fx, fy) = (weak(cx), weak(cy));
    let mut angle = Vec::with_capacity((i1 - i0) * w);
    let mut dist = Vec::with_capacity((i1 - i0) * w);
    for i in i0..i1 {
        for j in j0..j1 {
            let (wx, wy) = (at[j] - fx, at[i] - fy);
            angle.push(wy.atan2(wx));
            dist.push(wx.hypot(wy));
        }
    }
    let mut streaks = vec![0.0f32; dist.len()];
    let angles: Vec<f64> = (0..26)
        .map(|_| rng.uniform(0.0, 2.0 * std::f64::consts::PI))
        .collect();
    let (pi, tau) = (std::f64::consts::PI, 2.0 * std::f64::consts::PI);
    for a in angles {
        let base = weak(rng.uniform(0.006, 0.016));
        let length = reach * rng.uniform(0.35, 1.0);
        let fl = weak(length);
        for k in 0..dist.len() {
            let width = base + weak(0.05) * dist[k];
            let turn = (remainder(f64::from(angle[k]) - a + pi, tau) - pi).abs();
            let off = turn * f64::from(dist[k]);
            let q = off / f64::from(width);
            let fade = clip(1.0 - dist[k] / fl, 0.0, 1.0);
            let s = &mut streaks[k];
            *s = (f64::from(*s) + (-(q * q)).exp() * f64::from(fade)) as f32;
        }
    }
    let (fr, f4r) = (weak(r), weak(4.0 * r));
    for (k, s) in streaks.iter_mut().enumerate() {
        *s *= clip((dist[k] - fr) / fr, 0.0, 1.0);
    }
    for (k, (&s, &d)) in streaks.iter().zip(&dist).enumerate() {
        let u = d / f4r;
        let nimbus = np::expf(-(u * u));
        let (i, j) = (i0 + k / w, j0 + k % w);
        albedo[i * n + j] += weak(0.03) * clip(s, 0.0, 1.0) + weak(0.035) * nimbus;
    }
}

/// FLOAT_dot through OpenBLAS's sdot, as np.convolve's ends take it for a short overlap: the
/// float products summed in a double (sdot's loop for fewer than 32), the sum a float.
fn dot(xs: &[f32], k: f32) -> f32 {
    let mut s = 0.0f64;
    for &x in xs {
        s += f64::from(x * k);
    }
    s as f32
}

/// np.convolve(row, kernel, "same") with a kernel of five of `k` (correlate's: the ends by
/// FLOAT_dot, the rest by small_correlate's float sums, in turn).
fn convolve5(row: &[f32], k: f32, out: &mut [f32]) {
    let len = row.len();
    out[0] = dot(&row[0..3], k);
    out[1] = dot(&row[0..4], k);
    for i in 0..len - 4 {
        let mut s = 0.0f32;
        for &v in &row[i..i + 5] {
            s += v * k;
        }
        out[i + 2] = s;
    }
    out[len - 2] = dot(&row[len - 4..], k);
    out[len - 1] = dot(&row[len - 3..], k);
}

/// _blurred(table, 2): a box of five pixels down the columns, then along the rows, twice
/// (np.apply_along_axis of np.convolve), square tables of at least five.
fn blurred(table: &[f32], n: usize) -> Vec<f32> {
    let k = 1.0f32 / 5.0;
    let mut out = table.to_vec();
    let (mut line, mut done) = (vec![0.0f32; n], vec![0.0f32; n]);
    for _ in 0..2 {
        for j in 0..n {
            for i in 0..n {
                line[i] = out[i * n + j];
            }
            convolve5(&line, k, &mut done);
            for i in 0..n {
                out[i * n + j] = done[i];
            }
        }
        for i in 0..n {
            line.copy_from_slice(&out[i * n..(i + 1) * n]);
            convolve5(&line, k, &mut out[i * n..(i + 1) * n]);
        }
    }
    out
}

/// np.gradient(table, h) of a square float32 table: (down the columns, along the rows), the
/// middle by central differences over 2 h, the edges by one-sided ones over h.
fn gradient(t: &[f32], n: usize, h: f64) -> (Vec<f32>, Vec<f32>) {
    let (two, one) = (weak(2.0 * h), weak(h));
    let mut gy = vec![0.0f32; n * n];
    let mut gx = vec![0.0f32; n * n];
    for i in 0..n {
        for j in 0..n {
            let at = |a: usize, b: usize| t[a * n + b];
            gy[i * n + j] = if i == 0 {
                (at(1, j) - at(0, j)) / one
            } else if i == n - 1 {
                (at(n - 1, j) - at(n - 2, j)) / one
            } else {
                (at(i + 1, j) - at(i - 1, j)) / two
            };
            gx[i * n + j] = if j == 0 {
                (at(i, 1) - at(i, 0)) / one
            } else if j == n - 1 {
                (at(i, n - 1) - at(i, n - 2)) / one
            } else {
                (at(i, j + 1) - at(i, j - 1)) / two
            };
        }
    }
    (gy, gx)
}

/// The face as moon.py's face() makes it, the numbers drawn in its order.
fn make_face() -> Face {
    let n = FACE;
    let mut rng = Generator::new(1969);
    // (the pixels' centres: x along a row, y down a column)
    let at: Vec<f32> = (0..n)
        .map(|k| (k as f32 + 0.5) / n as f32 * 2.0 - 1.0)
        .collect();
    let rough = noise(n, &mut rng, 5);
    let mut sea = vec![0.0f32; n * n];
    for ((cx, cy), (rx, ry), depth) in SEAS {
        let (cx, cy, rx, ry, depth) = (weak(cx), weak(cy), weak(rx), weak(ry), weak(depth));
        let (k38, k125, k65, k11) = (weak(0.38), weak(1.25), weak(0.65), weak(1.1));
        for i in 0..n {
            let ty = (at[i] - cy) / ry;
            for j in 0..n {
                let tx = (at[j] - cx) / rx;
                let d = (tx * tx + ty * ty).sqrt() + k38 * rough[i * n + j];
                let v = depth * clip((k125 - d) / k65, 0.0, 1.0).powf(k11);
                let s = &mut sea[i * n + j];
                if !(s.is_nan() || *s >= v) {
                    *s = v;
                }
            }
        }
    }
    let fine = noise(n, &mut rng, 3);
    let patches = noise(n, &mut rng, 4);
    let grain_a = noise(n, &mut rng, 6);
    let grain_b = noise(n, &mut rng, 2);
    let mut albedo = vec![0.0f32; n * n];
    for k in 0..n * n {
        let grain = grain_a[k] - grain_b[k] * 0.5;
        let a = 1.0 - weak(0.40) * sea[k] * (weak(0.85) + weak(0.30) * patches[k])
            + weak(0.05) * rough[k]
            + weak(0.04) * fine[k];
        albedo[k] = a + weak(0.07) * grain * (1.0 - sea[k]);
    }
    let mut height = vec![0.0f32; n * n];
    // craters: many small, few large, most in the highlands (seas are younger)
    let radii: Vec<f64> = (0..320).map(|_| 0.004 + 0.045 * rng.power(0.22)).collect();
    let pick = |v: i64| -> usize { v.min(n as i64 - 1).rem_euclid(n as i64) as usize };
    for r in radii {
        let mut found = None;
        for _try in 0..4 {
            let cx = rng.uniform(-0.98, 0.98);
            let cy = rng.uniform(-0.98, 0.98);
            if cx * cx + cy * cy > 0.97 {
                continue;
            }
            let nf = n as f64;
            let (i, j) = (trunc((cy + 1.0) / 2.0 * nf), trunc((cx + 1.0) / 2.0 * nf));
            // (a float32 beside a Python float: compared as float32s)
            if sea[pick(i) * n + pick(j)] < rng.uniform(0.0, 1.4) as f32 {
                found = Some((cx, cy));
                break;
            }
        }
        if let Some(c) = found {
            crater_double(&mut height, &mut albedo, &at, c, r);
        }
    }
    for (c, r, reach) in BRIGHT {
        crater_float(&mut height, &mut albedo, &at, c, r, 0.22);
        if reach != 0.0 {
            rays(&mut albedo, &at, c, r, reach, &mut rng);
        }
    }
    let height = blurred(&height, n); // no crater wall is a cliff of one pixel
    let (gy, gx) = gradient(&height, n, 2.0 / n as f64);
    for a in albedo.iter_mut() {
        *a = clip(*a, weak(0.35), weak(1.15));
    }
    Face { albedo, gx, gy }
}

/// The face, made the first time it is asked for.
pub fn face() -> &'static Face {
    static MADE: OnceLock<Face> = OnceLock::new();
    MADE.get_or_init(make_face)
}

/// lit_share: how much of the disc is lit at `phase` (0 new, 0.5 full), 0 to 1.
pub fn lit_share(phase: f64) -> f64 {
    (1.0 - (2.0 * PI * phase).cos()) / 2.0
}

/// halo_level: the halo's strength at `t` (seconds; None: still) in [`HALO_LEVELS`], as
/// bright as the moon is full, breathing slowly where there is a time.
pub fn halo_level(phase: f64, t: Option<f64>) -> i64 {
    let breath = match t {
        None => 1.0,
        Some(t) => 0.80 + 0.20 * (0.5 + 0.5 * (2.0 * PI * t / BREATH).sin()),
    };
    lp_py::round(HALO_LEVELS as f64 * 0.40 * lit_share(phase) * breath) as i64
}

/// _sampled: a table of the face reduced to `size` px (a box filter, as Pillow reduces a
/// float picture).
fn sampled(table: &[f32], size: usize) -> Vec<f32> {
    let image = FloatImage {
        width: FACE,
        height: FACE,
        data: table.to_vec(),
    };
    // (a table of FACE px reduced to at least 2 px: Pillow's checks all pass)
    image
        .resize(size, size, Filter::Box, None)
        .map(|im| im.data)
        .unwrap_or_else(|_| vec![0.0; size * size])
}

/// _smooth: 0 below `lo`, 1 above `hi`, smoothly between (float32, as numpy has it).
fn smooth(lo: f64, hi: f64, x: f32) -> f32 {
    let t = clip((x - weak(lo)) / weak(hi - lo), 0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// disc: the moon at `phase` on a clear picture `size` px square, its disc DISC of it wide,
/// lit in `colour` and its night side in `night` (None: a faint earthshine in `colour`, on
/// nothing); `mirror` as seen south of the equator. Lit at twice its size and reduced.
pub fn disc(
    size: usize,
    phase: f64,
    colour: [u8; 3],
    night: Option<[u8; 3]>,
    mirror: bool,
) -> Image {
    let m = size * SS;
    let r = m as f64 * DISC / 2.0;
    let span = (lp_py::round(2.0 * r) as usize).max(2);
    let face = face();
    let (a, sx) = (sampled(&face.albedo, span), sampled(&face.gx, span));
    let x0 = (m as f64 - span as f64) / 2.0;
    let half = weak(span as f64 / 2.0);
    let at: Vec<f32> = (0..span).map(|k| (k as f32 + 0.5 - half) / half).collect();
    let theta = 2.0 * PI * phase;
    let (mut lx, lz) = (theta.sin(), -theta.cos());
    if mirror {
        lx = -lx;
    }
    let (lx, lz) = (weak(lx), weak(lz));
    let lit = colour.map(|c| f32::from(c) / 255.0);
    let dark = night.map(|n| n.map(|c| f32::from(c) / 255.0));
    let (night_alpha, day_alpha) = if night.is_some() {
        (1.0, 0.0)
    } else {
        (0.10, 0.9)
    };
    let (night_alpha, day_alpha) = (weak(night_alpha), weak(day_alpha));
    let i0 = lp_py::round(x0) as usize;
    let mut body = vec![0u8; m * m * 4];
    // (pixels left at nothing: np.clip(0 * 255 + 0.5, 0, 255) is 0 as a byte)
    for i in 0..span {
        for j in 0..span {
            let (xs, ys) = (at[j], at[i]);
            let k = i * span + j;
            let rho2 = xs * xs + ys * ys;
            let z = clip(1.0 - rho2, 0.0, 1.0).sqrt();
            // the sphere's own light: day and night, the terminator a soft line between them
            let mu0 = xs * lx + z * lz;
            let mu = clip(z, weak(0.02), 1.0);
            let lifted = clip(mu0, 0.0, f32::INFINITY);
            let lommel = 2.0 * lifted / (lifted + mu);
            // the relief: the slopes of the craters, where the light comes in low
            let graze = 1.0 - clip(mu0, 0.0, 1.0);
            let tilt = -(sx[k] * lx) * z * 4.0 * graze;
            let relief = clip(1.0 + tilt, weak(0.15), weak(1.9));
            let day = smooth(-0.05, 0.07, mu0 + weak(0.03) * np::tanhf(tilt))
                * clip(lommel, 0.0, 1.25)
                * relief;
            let tone = clip(a[k], 0.0, weak(1.2));
            let w = clip(day, 0.0, 1.0);
            let lit_day = tone * clip(day, 0.0, 1.25);
            let mut rgb = [0.0f32; 3];
            for c in 0..3 {
                let rgb_day = lit[c] * lit_day;
                let rgb_night = match dark {
                    Some(d) => {
                        d[c] * (weak(0.80) + weak(0.35) * (tone - weak(0.8)))
                            + lit[c] * (weak(0.05) * tone)
                    }
                    None => lit[c] * (weak(0.40) * tone),
                };
                rgb[c] = rgb_night * (1.0 - w) + rgb_day;
            }
            // bright ground goes towards white, not past it
            let peak = max(max(rgb[0], rgb[1]), rgb[2]);
            let over = max(1.0, peak);
            // the disc's edge, smooth; the night side as opaque as `night` allows
            let edge = clip((1.0 - rho2.sqrt()) * span as f32 / 2.0 + 0.5, 0.0, 1.0);
            let alpha = edge * (night_alpha + day_alpha * w);
            let at_px = ((i0 + i) * m + i0 + j) * 4;
            for c in 0..3 {
                body[at_px + c] = byte((rgb[c] / over) * alpha);
            }
            body[at_px + 3] = byte(alpha);
        }
    }
    // (RGBa, reduced, then RGBA: the image is made of its own bytes, the sizes its own)
    let reduced = Image::from_bytes(Mode::RGBa, m, m, body)
        .and_then(|im| im.reduce(SS, SS, None))
        .and_then(|im| im.convert(Mode::RGBA));
    reduced.unwrap_or_else(|_| Image::blank(Mode::RGBA, size, size))
}

/// np.maximum of floats (a NaN kept).
fn max(a: f32, b: f32) -> f32 {
    if a.is_nan() || a >= b {
        a
    } else {
        b
    }
}

/// np.clip(v * 255 + 0.5, 0, 255).astype(np.uint8)
fn byte(v: f32) -> u8 {
    clip(v * 255.0 + 0.5, 0.0, 255.0) as u8
}

/// _halo_alpha: the halo's light round a disc DISC of `size` px wide: bright at the disc's
/// edge, fading outwards in two steps, to nothing at the picture's edge.
fn halo_alpha(size: usize) -> Vec<f32> {
    let half = weak(size as f64 / 2.0);
    let at: Vec<f32> = (0..size).map(|k| k as f32 + 0.5 - half).collect();
    let (radius, rim) = (weak(size as f64 * DISC / 2.0), weak(size as f64 * 0.02));
    let (near, far, start) = (weak(0.03), weak(0.09), weak(0.985));
    let mut out = Vec::with_capacity(size * size);
    for &ys in &at {
        for &xs in &at {
            let h = xs.hypot(ys);
            let d = h / radius;
            let beyond = clip(d - 1.0, 0.0, f32::INFINITY);
            let shown = if d >= start { 1.0 } else { 0.0 };
            let mut glow = np::expf(-beyond / near) * shown;
            glow += weak(0.30) * np::expf(-beyond / far) * shown;
            let fade = clip((half - h) / rim, 0.0, 1.0);
            out.push(glow * fade);
        }
    }
    out
}

/// halo: a soft glow round the disc, in `colour`, `strength` 0 to 1 at its brightest.
pub fn halo(size: usize, colour: [u8; 3], strength: f64) -> Image {
    let s = weak(strength);
    let mut data = Vec::with_capacity(size * size * 4);
    for a in halo_alpha(size) {
        let alpha = clip(a * s, 0.0, 1.0);
        data.extend_from_slice(&colour);
        data.push((alpha * 255.0 + 0.5) as u8);
    }
    Image::from_bytes(Mode::RGBA, size, size, data)
        .unwrap_or_else(|_| Image::blank(Mode::RGBA, size, size))
}

/// picture: the moon with its halo (as bright as it is full), the halo breathing where there
/// is a time `t`.
pub fn picture(
    size: usize,
    phase: f64,
    colour: [u8; 3],
    night: Option<[u8; 3]>,
    mirror: bool,
    t: Option<f64>,
) -> Image {
    let glow = halo(
        size,
        colour,
        halo_level(phase, t) as f64 / HALO_LEVELS as f64,
    );
    let moon = disc(size, lp_py::round_digits(phase, 3), colour, night, mirror);
    alpha_composite(&glow, &moon).unwrap_or(glow)
}

/// A colour as the bytes ImageDraw inks with.
fn ink(c: Rgba) -> [u8; 4] {
    c.map(|v| v.clamp(0, 255) as u8)
}

impl Renderer {
    /// _moon_classic: the moon lit as at `phase` (0 new, 0.5 full): a disc in `color2` (where
    /// there is one), its lit side in `color` bounded by the terminator, an ellipse; drawn
    /// three times its size and reduced.
    pub(crate) fn moon_classic(&self, w: &Dict, phase: f64) -> Result<Piece> {
        let size = widget::int(w, "size", 64).max(8);
        let s = SUPERSAMPLE as f64;
        let k = size as usize * SUPERSAMPLE;
        let mut layer = Image::blank(Mode::RGBA, k, k);
        let r = size as f64 * s / 2.0 - s;
        let c = size as f64 * s / 2.0;
        let mut draw = Draw::new(&mut layer);
        if widget::truthy(w, "color2") {
            if let Some(dark) = self.colour_of(w, "color2")? {
                draw.ellipse((c - r, c - r, c + r, c + r), Some(ink(dark)), None, 1)?;
            }
        }
        let turn = (2.0 * PI * phase).cos();
        let mut waxing = phase <= 0.5;
        if widget::truthy(w, "mirror") {
            waxing = !waxing;
        }
        let (mut right, mut left) = (Vec::new(), Vec::new());
        let steps = 90;
        for i in 0..=steps {
            let dy = -r + 2.0 * r * f64::from(i) / f64::from(steps);
            let square = r * r - dy * dy;
            let half = if square > 0.0 { square } else { 0.0 }.sqrt();
            if waxing {
                // lit on the right, from the terminator to the edge
                right.push((c + half, c + dy));
                left.push((c + half * turn, c + dy));
            } else {
                left.push((c - half, c + dy));
                right.push((c - half * turn, c + dy));
            }
        }
        if 0.004 < phase && phase < 0.996 {
            if let Some(lit) = self.colour_of(w, "color")? {
                right.extend(left.into_iter().rev());
                draw.polygon(&right, Some(ink(lit)), None, 1)?;
            }
        }
        let image = shapes::down(&layer, size, size, SUPERSAMPLE)?;
        Ok(Piece::new(
            image,
            widget::int(w, "x", 0),
            widget::int(w, "y", 0),
        ))
    }

    /// _moon_piece: the moon as it looks at `phase` (render/moon.py's picture), its halo as it
    /// breathes at `t` (seconds; None: still).
    pub(crate) fn moon_piece(&self, w: &Dict, phase: f64, t: Option<f64>) -> Result<Piece> {
        let size = widget::int(w, "size", 64).max(8);
        let night = if widget::truthy(w, "color2") {
            self.colour_of(w, "color2")?
        } else {
            None
        };
        let Some(lit) = self.colour_of(w, "color")? else {
            // (moon.picture of None: tuple(None[:3]))
            return Err(crate::piece::Failed(
                "'NoneType' object is not subscriptable".into(),
            ));
        };
        let rgb = |c: Rgba| [c[0], c[1], c[2]].map(|v| v.clamp(0, 255) as u8);
        let mirror = widget::truthy(w, "mirror");
        let image = picture(size as usize, phase, rgb(lit), night.map(rgb), mirror, t);
        Ok(Piece::new(
            image,
            widget::int(w, "x", 0),
            widget::int(w, "y", 0),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numpy_s_remainder_and_clip() {
        assert_eq!(
            remainder(-1.0, 2.0 * std::f64::consts::PI),
            2.0 * std::f64::consts::PI - 1.0
        );
        assert_eq!(remainder(7.0, 3.0), 1.0);
        assert!(remainder(-6.0, 3.0).is_sign_positive() && remainder(6.0, -3.0).is_sign_negative());
        assert_eq!(clip(-0.0, 0.0, 1.0).to_bits(), 0.0f32.to_bits());
        assert!(clip(f32::NAN, 0.0, 1.0).is_nan());
    }
}
