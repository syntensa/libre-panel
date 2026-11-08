//! The earth as it stands at a moment (render/earth.py's picture): a globe lit by the sun where
//! it stands then, the cities' lights on its night side, the clouds, the glint of the sun on the
//! sea, the air glowing at its rim, a place marked; made at twice its size and reduced.
//!
//! In earth.py's own numbers: float32 where its arrays are, and the Python floats doubles, made
//! float32 where numpy takes them as weak scalars (and the one sum where a Python float meets
//! an array of bytes, float64 as numpy makes it); numpy's own float32 exp (crate::np); the C
//! library's asinf, atan2f, powf and hypotf, which numpy takes where it has no SVML (numpy on
//! Linux with AVX-512 takes SVML's, a unit or two in the last place apart: docs/RUST.md says
//! how near that comes); np.dot's float32 products summed in a double (OpenBLAS's sdot for
//! fewer than 32), the sum a float. The maps read as earth._read reads them: a JPEG drafted to
//! the scale that covers the width needed, by libjpeg (lp-decode); the sun where
//! sensors/sky.py's sunrise equation puts it.

use std::f64::consts::PI;
use std::path::{Path, PathBuf};

use lp_decode::Kind;
use lp_pixels::{alpha_composite, Image, Mode};

use lp_py::{Dict, Value};

use crate::np;
use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Drawn, Renderer};
use crate::widget;

/// earth.SHARE: the disc's share of the picture, the rest the air's glow.
pub const SHARE: f64 = 0.86;
/// earth.SS: a standing globe is made at twice its size and reduced.
const SS: usize = 2;
/// earth.AIR: the air's colour, where none is given.
pub const AIR: [u8; 3] = [107, 173, 255];
/// earth.picture's mark: your place's colour, where none is given.
pub const MARK: [u8; 3] = [255, 210, 122];
/// earth.LIGHTS: the colour of the cities' lights.
const LIGHTS: [f32; 3] = [1.0, 0.80, 0.50];
/// earth.DUSK: the band along the line between day and night.
const DUSK: [f32; 3] = [1.0, 0.42, 0.16];
/// earth.BAND: the rows worked on at a time (where the glint is looked for, band by band).
const BAND: usize = 128;
/// earth.DAY_S: a turn of the earth against the stars.
const DAY_S: f64 = 86164.1;
/// sky._UNIX_EPOCH: 1 January 1970 as a Julian date.
const UNIX_EPOCH: f64 = 2440587.5;
/// date(1970, 1, 1).toordinal() and date(2000, 1, 1).toordinal().
const ORDINAL_1970: i64 = 719_163;
const ORDINAL_2000: i64 = 730_120;

/// A Python float as numpy takes it beside a float32 array (a weak scalar): the float32 nearest.
fn weak(x: f64) -> f32 {
    x as f32
}

/// Python's x % m for floats (the sign of m, never -0).
fn py_mod(x: f64, m: f64) -> f64 {
    let r = x % m;
    if r == 0.0 {
        0.0f64.copysign(m)
    } else if (m < 0.0) != (r < 0.0) {
        r + m
    } else {
        r
    }
}

/// np.clip(x, lo, hi) of a float32 (NaN stays NaN).
fn clip(x: f32, lo: f32, hi: f32) -> f32 {
    if x < lo {
        lo
    } else if x > hi {
        hi
    } else {
        x
    }
}

/// The same of a double.
fn clip64(x: f64, lo: f64, hi: f64) -> f64 {
    if x < lo {
        lo
    } else if x > hi {
        hi
    } else {
        x
    }
}

/// np.power(x, e) of a float32 by a weak scalar: the C library's powf (never folded into
/// multiplications: the exponent is opaque to the compiler).
fn powf(x: f32, e: f64) -> f32 {
    x.powf(std::hint::black_box(weak(e)))
}

/// earth._smooth(lo, hi, x) of a float32: Hermite's step from lo to hi.
fn smooth(lo: f64, hi: f64, x: f32) -> f32 {
    let t = clip((x - weak(lo)) * weak(1.0 / (hi - lo)), 0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// The same of a double (where a Python float meets numpy's bytes, numpy makes float64s).
fn smooth64(lo: f64, hi: f64, x: f64) -> f64 {
    let t = clip64((x - lo) * (1.0 / (hi - lo)), 0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// float(np.dot(a, b)) of two float32 vectors of three: their products summed in a double
/// (OpenBLAS's sdot for fewer than 32), the sum a float32 (FLOAT_dot's), as a double.
fn dot(a: [f32; 3], b: [f32; 3]) -> f64 {
    let mut sum = 0.0f64;
    for i in 0..3 {
        sum += f64::from(a[i] * b[i]);
    }
    f64::from(sum as f32)
}

/// sky._sun_path(day, longitude): solar noon (a Julian date), the sun's declination and its
/// mean anomaly (radians), of the day of proleptic Gregorian `ordinal`.
fn sun_path(ordinal: i64, longitude: f64) -> (f64, f64, f64) {
    let n = (ordinal - ORDINAL_2000) as f64;
    let mean = n - longitude / 360.0;
    let anomaly = py_mod(357.5291 + 0.98560028 * mean, 360.0).to_radians();
    let mut centre = 1.9148 * anomaly.sin() + 0.02 * (2.0 * anomaly).sin();
    centre += 0.0003 * (3.0 * anomaly).sin();
    let ecliptic = py_mod(anomaly.to_degrees() + centre + 180.0 + 102.9372, 360.0).to_radians();
    let noon = 2451545.0 + mean + 0.0053 * anomaly.sin() - 0.0069 * (2.0 * ecliptic).sin();
    let declination = (ecliptic.sin() * 23.4397f64.to_radians().sin()).asin();
    (noon, declination, anomaly)
}

/// datetime.fromtimestamp(t, UTC).date().toordinal(): the day of `t` (its microsecond rounded
/// half to even, as fromtimestamp rounds it).
fn day_of(t: f64) -> Result<i64> {
    if !t.is_finite() || t.abs() > 1e16 {
        return Err(Failed("timestamp out of range for platform time_t".into()));
    }
    let (whole, frac) = (t.trunc(), t.fract());
    let us = (frac * 1e6).round_ties_even();
    let mut seconds = whole as i64;
    if us >= 1e6 {
        seconds += 1;
    } else if us < 0.0 {
        seconds -= 1;
    }
    let ordinal = seconds.div_euclid(86_400) + ORDINAL_1970;
    if !(1..=3_652_059).contains(&ordinal) {
        return Err(Failed("year is out of range".into()));
    }
    Ok(ordinal)
}

/// earth.subsolar(t): where the sun stands overhead at `t` (Unix time), latitude and longitude
/// in degrees.
pub fn subsolar(t: f64) -> Result<(f64, f64)> {
    let (noon, declination, _) = sun_path(day_of(t)?, 0.0);
    let past = 360.0 * (t / 86400.0 + UNIX_EPOCH - noon); // (degrees past noon at Greenwich)
    Ok((
        declination.to_degrees(),
        py_mod(180.0 - past, 360.0) - 180.0,
    ))
}

/// earth.redraw_s(size): how long a globe `size` px wide shows the same day.
pub fn redraw_s(size: i64) -> f64 {
    let r = size as f64 * SHARE / 2.0;
    let r = if r > 1.0 { r } else { 1.0 };
    let every = DAY_S / (2.0 * PI * r);
    let every = if every < 600.0 { every } else { 600.0 };
    if every > 20.0 {
        every
    } else {
        20.0
    }
}

/// earth._point(lat, lon): the point of the sphere there, in float32.
fn point(lat: f64, lon: f64) -> [f32; 3] {
    let (a, b) = (lat.to_radians(), lon.to_radians());
    [
        (a.cos() * b.cos()) as f32,
        (a.cos() * b.sin()) as f32,
        a.sin() as f32,
    ]
}

/// earth._basis(lat, lon): right, up and towards the viewer of the point in the middle.
fn basis(lat: f64, lon: f64) -> [[f32; 3]; 3] {
    let (a, b) = (lat.to_radians(), lon.to_radians());
    let right = [(-b.sin()) as f32, b.cos() as f32, 0.0];
    let up = [
        (-a.sin() * b.cos()) as f32,
        (-a.sin() * b.sin()) as f32,
        a.cos() as f32,
    ];
    [right, up, point(lat, lon)]
}

/// Where the maps are: the package's assets/earth (beside the fonts' folder).
pub fn maps_dir(fonts_dir: &Path) -> PathBuf {
    let package = fonts_dir.parent().unwrap_or(fonts_dir);
    package.join("assets").join("earth")
}

/// A map as earth._read reads it: `width` px wide or the next size a JPEG reads fast above it
/// (draft), converted to `mode`; its bands a pixel, row after row.
struct Map {
    width: usize,
    height: usize,
    bands: usize,
    data: Vec<u8>,
}

fn read(dir: &Path, name: &str, mode: Mode, width: usize) -> Result<Map> {
    let failed = |e: String| Failed(e);
    let data = std::fs::read(dir.join(name)).map_err(|e| Failed(e.to_string()))?;
    let kind = if mode == Mode::L { Kind::L } else { Kind::RGB };
    let picture = lp_decode::decode_draft(&data, Some((Some(kind), (width, width / 2))))
        .map_err(|e| failed(e.0))?;
    let image = picture.convert(mode).map_err(|e| failed(e.0))?;
    let (w, h) = image.size();
    Ok(Map {
        width: w,
        height: h,
        bands: image.bands(),
        data: image.as_bytes().to_vec(),
    })
}

/// earth.maps(width): day and night (RGB), clouds and water (L).
struct Maps {
    day: Map,
    night: Map,
    clouds: Map,
    water: Map,
}

fn maps(dir: &Path, width: usize) -> Result<Maps> {
    Ok(Maps {
        day: read(dir, "day.jpg", Mode::RGB, width)?,
        night: read(dir, "night.jpg", Mode::RGB, width)?,
        clouds: read(dir, "clouds.jpg", Mode::L, width)?,
        water: read(dir, "water.png", Mode::L, width)?,
    })
}

impl Map {
    /// earth._lookup: the offset of the map's pixel nearest the point (radians).
    fn at(&self, lat: f32, lon: f32) -> usize {
        let (h, w) = (self.height as i64, self.width as i64);
        let row = ((weak(0.5) - lat * weak(1.0 / PI)) * h as f32) as i32 as i64;
        let row = row.clamp(0, h - 1);
        let col = ((lon * weak(0.5 / PI) + weak(0.5)) * w as f32) as i32 as i64;
        let col = col.rem_euclid(w);
        ((row * w + col) as usize) * self.bands
    }
}

/// earth._lit for a pixel: the colour (0 to 1) of ground with these maps' values under the sun
/// `mu` (the cosine of its angle from overhead, below the horizon less than nought).
fn lit(day: [u8; 3], night: [u8; 3], cloud: u8, mu: f32, clouds: bool, lights: bool) -> [f32; 3] {
    let mut land = day.map(|v| f32::from(v) * weak(1.12 / 255.0));
    let cover = clouds.then(|| smooth(0.22, 0.80, f32::from(cloud) * weak(1.0 / 255.0)));
    if let Some(cover) = cover {
        let keep = 1.0 - weak(0.92) * cover;
        let add = weak(0.97) * cover;
        land = land.map(|v| v * keep + add);
    }
    let shone = smooth(-0.10, 0.10, mu);
    let bright = weak(0.45) + weak(0.55) * clip(mu, 0.0, 1.0).sqrt();
    let k = bright * shone;
    let mut rgb = land.map(|v| v * k);
    let dark = 1.0 - smooth(-0.16, 0.04, mu);
    if dark > 0.0 {
        // the night side: the ground faintly, and the lights
        let n = night.map(|v| f32::from(v) * weak(1.0 / 255.0));
        let mut base = n.map(|v| v * weak(0.30));
        if let Some(veil) = cover {
            let keep = 1.0 - weak(0.5) * veil;
            let tint = [weak(0.03), weak(0.04), weak(0.07)];
            for c in 0..3 {
                base[c] *= keep;
                base[c] += veil * tint[c];
            }
        }
        if lights {
            let mut glow = powf(
                clip((n[0] - weak(0.6) * n[2]) * weak(1.0 / 0.62), 0.0, 1.0),
                0.85,
            ) * weak(1.25);
            if let Some(veil) = cover {
                glow *= 1.0 - weak(0.7) * veil;
            }
            for c in 0..3 {
                base[c] += glow * LIGHTS[c];
            }
        }
        for c in 0..3 {
            rgb[c] += base[c] * dark;
        }
    }
    let q = (mu - weak(0.015)) * weak(1.0 / 0.045);
    let band = np::expf(-(q * q)) * weak(0.22); // dusk and dawn
    let keep = 1.0 - weak(0.5) * band;
    for c in 0..3 {
        rgb[c] *= keep;
        rgb[c] += band * (DUSK[c] * weak(0.45));
    }
    rgb
}

/// earth._air_pixels(size): the pixels where a globe's air shows (its rim inside, the glow
/// outside), in order.
fn air_pixels(size: usize) -> Vec<usize> {
    let r = size as f64 * SHARE / 2.0;
    let (half, fr) = (weak(size as f64 / 2.0), weak(r));
    let mut found = Vec::new();
    for i in 0..size {
        let ys = (i as f32 + 0.5) - half;
        for j in 0..size {
            let xs = (j as f32 + 0.5) - half;
            let rho = xs.hypot(ys) / fr;
            if rho > weak(0.55) && rho < weak(1.12) {
                found.push(i * size + j);
            }
        }
    }
    found
}

/// earth._air: the air over a globe `size` px (straight RGBA), bright at the rim of the day
/// side, inside the disc and as a glow round it, none on the night side.
fn air(size: usize, view: &[[f32; 3]; 3], sun: [f32; 3], colour: [u8; 3]) -> Result<Image> {
    let [right, up, toward] = *view;
    let r = size as f64 * SHARE / 2.0;
    let (sx, sy, sz) = (dot(right, sun), dot(up, sun), dot(toward, sun)); // (the sun, seen)
    let (wx, wy, wz) = (weak(sx), weak(sy), weak(sz));
    let rgb = colour.map(|c| {
        let v = f32::from(c) * weak(1.13);
        (if v < 255.0 { v } else { 255.0 }) as u32
    });
    let width = 0.05f64;
    let mut out = vec![0u8; size * size * 4];
    for idx in air_pixels(size) {
        let (ys, xs) = (idx / size, idx % size);
        let x = (xs as f32 + weak(0.5 - size as f64 / 2.0)) * weak(1.0 / r);
        let y = (weak(size as f64 / 2.0 - 0.5) - ys as f32) * weak(1.0 / r);
        let rho = (x * x + y * y).sqrt();
        let z = clip(1.0 - rho * rho, 0.0, 1.0).sqrt();
        let inner = rho < 1.0;
        // inside: the light on the ground there; outside: on the rim beside it
        let across = x * wx + y * wy;
        let floor = if rho > weak(1e-6) { rho } else { weak(1e-6) };
        let mu = if inner {
            across + z * wz
        } else {
            across / floor
        };
        let fres = if inner { powf(1.0 - z, 2.5) } else { 0.0 };
        let mut halo =
            np::expf(-(if rho - 1.0 > 0.0 { rho - 1.0 } else { 0.0 }) * weak(1.0 / (width * 0.30)));
        halo *= clip(
            (weak(1.0 + width * 2.2) - rho) * weak(1.0 / (width * 0.6)),
            0.0,
            1.0,
        );
        let edge = clip((1.0 - rho) * weak(r) + 0.5, 0.0, 1.0); // the disc's own edge, smooth
        let mut alpha = weak(0.75) * fres * smooth(-0.35, 0.55, mu) * edge;
        alpha += clip(halo * smooth(-0.30, 0.45, mu) * weak(0.85), 0.0, 1.0) * (1.0 - edge);
        let lit = clip(alpha * 255.0 + 0.5, 0.0, 255.0) as u8;
        out[idx * 4..idx * 4 + 4].copy_from_slice(&[rgb[0] as u8, rgb[1] as u8, rgb[2] as u8, lit]);
    }
    Image::from_bytes(Mode::RGBA, size, size, out).map_err(|e| Failed(e.to_string()))
}

/// earth._spot: your place on the globe, a dot with a soft ring (straight RGBA), and where it
/// goes; none on the far side (it fades as it goes round).
fn spot(
    size: usize,
    view: &[[f32; 3]; 3],
    place: (f64, f64),
    colour: [u8; 3],
) -> Result<Option<(Image, (i64, i64))>> {
    let [right, up, toward] = *view;
    let p = point(place.0, place.1);
    let depth = dot(p, toward);
    if depth <= 0.05 {
        return Ok(None);
    }
    let r = size as f64 * SHARE / 2.0;
    let cx = size as f64 / 2.0 + dot(p, right) * r;
    let cy = size as f64 / 2.0 - dot(p, up) * r;
    let dot_r = {
        let d = size as f64 * 0.008;
        if d > 1.4 {
            d
        } else {
            1.4
        }
    };
    let reach = (dot_r * 4.0) as i64 + 2;
    let side = size as i64;
    let (x0, x1) = (
        (cx as i64 - reach).max(0),
        (cx as i64 + reach + 1).min(side),
    );
    let (y0, y1) = (
        (cy as i64 - reach).max(0),
        (cy as i64 + reach + 1).min(side),
    );
    if x1 <= x0 || y1 <= y0 {
        return Ok(None);
    }
    let fade = {
        let f = (depth - 0.05) / 0.25;
        weak(if f < 1.0 { f } else { 1.0 })
    };
    let (w, h) = ((x1 - x0) as usize, (y1 - y0) as usize);
    let mut patch = vec![0u8; w * h * 4];
    let (fx, fy) = (weak(cx), weak(cy));
    for i in 0..h {
        let ys = (y0 + i as i64) as f32 + 0.5;
        for j in 0..w {
            let xs = (x0 + j as i64) as f32 + 0.5;
            let d = (xs - fx).hypot(ys - fy);
            let core = clip(weak(dot_r) - d + 0.5, 0.0, 1.0);
            let q = (d - weak(dot_r * 2.4)) / weak(dot_r * 0.45);
            let ring = np::expf(-(q * q)) * weak(0.45);
            let s = d / weak(dot_r * 2.2);
            let halo = np::expf(-(s * s)) * weak(0.25);
            let a = clip(core + ring + halo * (1.0 - core), 0.0, 1.0) * fade;
            let at = (i * w + j) * 4;
            patch[at..at + 3].copy_from_slice(&colour);
            patch[at + 3] = clip(a * 255.0 + 0.5, 0.0, 255.0) as u8;
        }
    }
    let image = Image::from_bytes(Mode::RGBA, w, h, patch).map_err(|e| Failed(e.to_string()))?;
    Ok(Some((image, (x0, y0))))
}

/// The look of a globe: the air's colour, your place's, and whether the clouds and the cities'
/// lights show.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Look {
    pub air: [u8; 3],
    pub mark: [u8; 3],
    pub clouds: bool,
    pub lights: bool,
}

impl Default for Look {
    fn default() -> Look {
        Look {
            air: AIR,
            mark: MARK,
            clouds: true,
            lights: true,
        }
    }
}

/// earth.picture(size, t, centre, place, air, mark, clouds, lights): the earth at `t` (Unix
/// time) seen from above `centre` (latitude, longitude), `size` px square (8 at least), the
/// disc SHARE of it and the air round it; `place` marked.
pub fn picture(
    dir: &Path,
    size: i64,
    t: f64,
    centre: (f64, f64),
    place: Option<(f64, f64)>,
    look: Look,
) -> Result<Image> {
    let size = size.max(8);
    if size > 8192 {
        return Err(Failed("a globe too large".into()));
    }
    let size = size as usize;
    let m = size * SS;
    let r = m as f64 * SHARE / 2.0;
    let view = basis(centre.0, centre.1);
    let [right, up, toward] = view;
    let (lat_s, lon_s) = subsolar(t)?;
    let sun = point(lat_s, lon_s);
    // the glint of the sun on water: where the ground's normal halves sun and viewer
    let mut half: [f32; 3] = std::array::from_fn(|i| sun[i] + toward[i]);
    let norm = (dot(half, half) as f32).sqrt();
    let by = weak(if f64::from(norm) > 1e-6 {
        f64::from(norm)
    } else {
        1e-6
    });
    half = half.map(|v| v / by);
    let width = (PI * 2.0 * r).round_ties_even() as usize;
    let found = maps(dir, width)?;
    let mut rgba = vec![0u8; m * m * 4]; // (premultiplied)
    let top = ((m as f64 / 2.0 - r) as i64 - 1).max(0) as usize;
    let bottom = ((m as f64 / 2.0 + r) as i64 + 2).min(m as i64) as usize;
    let (inv, fr) = (weak(1.0 / r), weak(r));
    let xs: Vec<f32> = (0..m)
        .map(|j| (j as f32 + weak(0.5 - m as f64 / 2.0)) * inv)
        .collect();
    let mid = weak(m as f64 / 2.0 - 0.5);
    let tint = [1.0f32, weak(0.93), weak(0.80)];
    let mut y0 = top;
    while y0 < bottom {
        let y1 = bottom.min(y0 + BAND);
        // (the band's pixels on the disc, row by row, and what each sees of the sun)
        let mut seen = Vec::new();
        let mut most = f32::NEG_INFINITY;
        for i in y0..y1 {
            let y = (mid - i as f32) * inv;
            for (j, &x) in xs.iter().enumerate() {
                let rho2 = x * x + y * y;
                if rho2 >= 1.0 || rho2.is_nan() {
                    continue; // (rho2 < 1.0 is false for NaN too: outside)
                }
                let z = (1.0 - rho2).sqrt();
                let px = x * right[0] + y * up[0] + z * toward[0];
                let py = x * right[1] + y * up[1] + z * toward[1];
                let pz = x * right[2] + y * up[2] + z * toward[2];
                let mu = px * sun[0] + py * sun[1] + pz * sun[2];
                // (numpy's max: a NaN, once met, is the max)
                if !most.is_nan() && (mu.is_nan() || mu > most) {
                    most = mu;
                }
                seen.push((i, j, rho2, px, py, pz, mu));
            }
        }
        let glints = most > 0.0; // (mu.max() > 0)
        for (i, j, rho2, px, py, pz, mu) in seen {
            let lat = clip(pz, -1.0, 1.0).asin();
            let lon = py.atan2(px);
            let c = found.clouds.at(lat, lon);
            let cover = found.clouds.data[c];
            let d = found.day.at(lat, lon);
            let n = found.night.at(lat, lon);
            let day = [
                found.day.data[d],
                found.day.data[d + 1],
                found.day.data[d + 2],
            ];
            let night = [
                found.night.data[n],
                found.night.data[n + 1],
                found.night.data[n + 2],
            ];
            let mut rgb = lit(day, night, cover, mu, look.clouds, look.lights);
            if glints {
                // the glint of the sun on the water
                let water =
                    f32::from(found.water.data[found.water.at(lat, lon)]) * weak(1.0 / 255.0);
                let nh = clip(px * half[0] + py * half[1] + pz * half[2], 0.0, 1.0);
                let mut glint = water
                    * (powf(nh, 160.0) * weak(0.35) + powf(nh, 18.0) * weak(0.06))
                    * smooth(-0.10, 0.10, mu);
                if look.clouds {
                    // (the clouds' bytes times a Python float: numpy's float64s)
                    let veil = 1.0 - smooth64(0.22, 0.80, f64::from(cover) * (1.0 / 255.0));
                    glint = (f64::from(glint) * veil) as f32;
                }
                for k in 0..3 {
                    rgb[k] += glint * tint[k];
                }
            }
            let edge = clip((1.0 - rho2.sqrt()) * fr + 0.5, 0.0, 1.0); // the disc's edge
            let at = (i * m + j) * 4;
            let shown = edge * 255.0;
            for k in 0..3 {
                rgba[at + k] = clip(rgb[k] * shown + 0.5, 0.0, 255.0) as u8;
            }
            rgba[at + 3] = (edge * 255.0 + 0.5) as u8;
        }
        y0 = y1;
    }
    drop(found);
    let failed = |e: lp_pixels::Error| Failed(e.to_string());
    let globe = Image::from_bytes(Mode::RGBa, m, m, rgba)
        .map_err(failed)?
        .reduce(SS, SS, None)
        .map_err(failed)?
        .convert(Mode::RGBA)
        .map_err(failed)?;
    let mut out = alpha_composite(&globe, &air(size, &view, sun, look.air)?).map_err(failed)?;
    if let Some(place) = place {
        if let Some((mark, (x0, y0))) = spot(size, &view, place, look.mark)? {
            let (w, h) = (mark.width() as i64, mark.height() as i64);
            out.alpha_composite_at(&mark, (x0, y0), (0, 0, w, h))
                .map_err(failed)?;
        }
    }
    Ok(out)
}

// -- the earth widget ---------------------------------------------------------------------------

/// earth.zone_longitude: the longitude of the computer's time zone, its offset from UTC now (as
/// datetime.now() finds it when asked), 15 degrees an hour; None where Python fails at it.
fn zone_longitude(local: &lp_py::zones::Zone) -> Option<f64> {
    let hours = lp_py::zones::now_offset(local)? as f64 / 3600.0;
    Some((hours * 15.0).clamp(-180.0, 180.0))
}

impl Renderer {
    /// _draw_earth, the standing globe: the earth as it is at the frame's moment, its time on
    /// the wall clock in steps of redraw_s (the moment it is lit for), seen from above your
    /// place (else your time zone's longitude a little north of the equator), your place
    /// marked where the widget asks; drawn again where its day, its view or its look moves.
    /// A globe that turns in a moving frame: Rust says it does not draw it yet.
    pub(crate) fn earth_widget(&mut self, w: &Dict) -> Drawn {
        if self.options.animate && widget::number(w, "spin", 0.0) > 0.0 {
            return Drawn::Unported("turning globes".into());
        }
        let Some(local) = lp_py::zones::local() else {
            return Drawn::Unported("the earth without the computer's own time zone".into());
        };
        let size = widget::int(w, "size", 160).max(8);
        let mark = if widget::truthy(w, "place") {
            self.place
        } else {
            None
        };
        let mut colours = [[0u8; 3]; 2];
        for (colour, key) in colours.iter_mut().zip(["color", "color2"]) {
            match self.colour_of(w, key) {
                Ok(Some(c)) => *colour = [c[0] as u8, c[1] as u8, c[2] as u8],
                Ok(None) => {
                    return Drawn::Failed(Failed("'NoneType' object is not subscriptable".into()))
                }
                Err(e) => return Drawn::Failed(e),
            }
        }
        let look = Look {
            air: colours[0],
            mark: colours[1],
            clouds: widget::truthy(w, "clouds"),
            lights: widget::truthy(w, "lights"),
        };
        let every = redraw_s(size);
        // (the moment it is lit for, on the wall clock)
        let Some(stamp) = lp_py::zones::timestamp(local, &self.now(), self.fold()) else {
            return Drawn::Unported("the earth at a time that Python fails at".into());
        };
        let moment = (stamp / every).floor() * every;
        // (earth.view_of)
        let centre = match self.place {
            Some(place) => place,
            None => match zone_longitude(local) {
                Some(longitude) => (20.0, longitude),
                None => return Drawn::Unported("the earth at a time that Python fails at".into()),
            },
        };
        let pair = |p: (f64, f64)| Value::List(vec![Value::Float(p.0), Value::Float(p.1)]);
        let rgb = |c: [u8; 3]| Value::List(c.iter().map(|&v| Value::int(i64::from(v))).collect());
        let content = Value::List(vec![
            Value::int(size),
            pair(centre),
            mark.map_or(Value::None, pair),
            rgb(look.air),
            rgb(look.mark),
            Value::Bool(look.clouds),
            Value::Bool(look.lights),
            Value::Float(moment),
        ]);
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let dir = maps_dir(&self.env.fonts_dir);
        match self.cached(w, content, |_| {
            let globe = picture(&dir, size, moment, centre, mark, look)?;
            let mut piece = Piece::new(globe, x, y);
            piece.hit = Some([x, y, size, size]);
            Ok(piece)
        }) {
            Ok(piece) => Drawn::Piece(piece),
            Err(e) => Drawn::Failed(e),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_sun_stands_where_the_sunrise_equation_puts_it() {
        // (the equinox of March 2026 about noon at Greenwich: the sun near the equator, near
        // the prime meridian)
        let (lat, lon) = subsolar(1_774_094_400.0).unwrap();
        assert!(lat.abs() < 1.0 && lon.abs() < 5.0, "{lat} {lon}");
        assert_eq!(day_of(-0.0000004).unwrap(), ORDINAL_1970);
        assert_eq!(day_of(-0.0000006).unwrap(), ORDINAL_1970 - 1);
        assert_eq!(day_of(86_399.9999996).unwrap(), ORDINAL_1970 + 1);
    }

    #[test]
    fn a_globe_is_drawn_again_when_its_day_moves_a_pixel() {
        assert_eq!(redraw_s(8), 600.0);
        assert_eq!(redraw_s(4000), 20.0);
        assert!((redraw_s(160) - DAY_S / (2.0 * PI * 68.8)).abs() < 1e-9);
    }
}
