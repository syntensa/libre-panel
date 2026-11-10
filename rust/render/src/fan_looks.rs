//! The fans' looks (render/fan_looks.py): each look's blades and colours, and the rotors and
//! square frames of the drawn ones made as numpy makes them: every pixel worked out from 3 x 3,
//! lit from the top left, in single precision where numpy works in it and in double where its
//! types take it there (a float64 scalar or a float64 array among them), each step as numpy
//! takes it, so the layers come out to the bit (its float32 exp as numpy's own, np::expf; the C
//! library's atan2f, powf and pow, as numpy takes them on a processor without AVX-512).

use crate::np;

/// The first channel's planes of a look's layers: shape, core and gloss (times alpha), alpha
/// and the part where the second colour goes (times alpha), each `size` x `size`.
pub(crate) const PLANES: usize = 5;

const SUPERSAMPLE: usize = 3;

/// The light, as the Blender rotor's: from the top left, in front (LIGHT and HALF as numpy
/// works them out: their bits).
const LIGHT: [f64; 3] = [
    f64::from_bits(13823651565409714303),
    f64::from_bits(13825119989551277098),
    f64::from_bits(4605517340253090388),
];
const HALF: [f64; 3] = [
    f64::from_bits(13819476296415123786),
    f64::from_bits(13821017682919526576),
    f64::from_bits(4606756058157622155),
];

/// np.degrees of a float32: times 180 / pi, both float32.
const DEGREES: f32 = 180.0f32 / std::f32::consts::PI;
/// np.radians of a float32: times pi / 180 as a float32.
const RADIANS: f32 = (std::f64::consts::PI / 180.0) as f32;
/// np.radians(1): a float64 scalar (which takes what it multiplies to float64).
const RADIAN: f64 = std::f64::consts::PI / 180.0;

const NOCTUA_BROWN: [u8; 3] = [104, 70, 54];
const NOCTUA_BEIGE: [u8; 3] = [212, 192, 164];

/// A fan's look: what its blades are like, and its own colours (fan_looks.Look).
#[derive(Clone, Debug, PartialEq)]
pub struct Look {
    pub(crate) name: &'static str,
    pub(crate) blades: i64,
    /// false: the Blender rotor's layers (render/fan.rs)
    pub(crate) drawn: bool,
    hub: f64,
    sweep: f64,
    bend: f64,
    fill_hub: f64,
    fill_tip: f64,
    round_tip: bool,
    ridge: bool,
    grooves: bool,
    sticker: f64,
    hub_ring: bool,
    pub(crate) blade_color: [u8; 3],
    pub(crate) hub_color: [u8; 3],
    pub(crate) frame_color: [u8; 3],
    pub(crate) pad_color: [u8; 3],
    /// "noctua": pads in the corners; "plain": a frame with a lit ring
    noctua_frame: bool,
}

/// Look's defaults.
const PLAIN: Look = Look {
    name: "",
    blades: 9,
    drawn: true,
    hub: 0.34,
    sweep: 0.0,
    bend: 1.4,
    fill_hub: 0.75,
    fill_tip: 0.85,
    round_tip: false,
    ridge: false,
    grooves: false,
    sticker: 0.0,
    hub_ring: false,
    blade_color: [230, 236, 244],
    hub_color: [34, 211, 238],
    frame_color: [64, 72, 86],
    pad_color: [34, 211, 238],
    noctua_frame: false,
};

/// LOOKS, in their order.
pub(crate) const LOOKS: [Look; 4] = [
    Look {
        name: "noctua-g2",
        blades: 9,
        drawn: false,
        blade_color: NOCTUA_BROWN,
        hub_color: NOCTUA_BROWN,
        frame_color: NOCTUA_BEIGE,
        pad_color: NOCTUA_BROWN,
        noctua_frame: true,
        ..PLAIN
    },
    Look {
        name: "noctua-a12x25",
        blades: 9,
        hub: 0.35,
        sweep: 48.0,
        bend: 1.25,
        fill_hub: 0.80,
        fill_tip: 0.88,
        grooves: true,
        sticker: 0.78,
        blade_color: NOCTUA_BROWN,
        hub_color: NOCTUA_BEIGE,
        frame_color: NOCTUA_BEIGE,
        pad_color: NOCTUA_BROWN,
        noctua_frame: true,
        ..PLAIN
    },
    Look {
        name: "noctua-a14",
        blades: 7,
        hub: 0.33,
        sweep: 34.0,
        bend: 1.35,
        fill_hub: 0.60,
        fill_tip: 0.74,
        grooves: true,
        sticker: 0.78,
        blade_color: NOCTUA_BROWN,
        hub_color: NOCTUA_BEIGE,
        frame_color: NOCTUA_BEIGE,
        pad_color: NOCTUA_BROWN,
        noctua_frame: true,
        ..PLAIN
    },
    Look {
        name: "libre",
        blades: 6,
        hub: 0.30,
        sweep: 26.0,
        bend: 1.1,
        fill_hub: 0.42,
        fill_tip: 0.62,
        round_tip: true,
        ridge: true,
        hub_ring: true,
        ..PLAIN
    },
];

/// The look of that name, else the default (look()).
/// labels: each look's name for people, in the looks' order.
pub fn labels() -> &'static [(&'static str, &'static str)] {
    &[
        ("noctua-g2", "Noctua NF-A12x25 G2 / NF-A14x25 G2"),
        ("noctua-a12x25", "Noctua NF-A12x25 (G1)"),
        ("noctua-a14", "Noctua NF-A14 (G1, 140 mm)"),
        ("libre", "Libre Panel"),
    ]
}

pub fn look(name: &str) -> &'static Look {
    LOOKS
        .iter()
        .find(|look| look.name == name)
        .unwrap_or(&LOOKS[0])
}

/// The rotor's angles for `positions` steps over one blade's pitch (angles()).
pub(crate) fn angles(look: &Look, positions: usize) -> Vec<f64> {
    let pitch = 360.0 / look.blades as f64;
    (0..positions)
        .map(|p| pitch * p as f64 / positions as f64)
        .collect()
}

/// np.clip(x, lo, hi): the larger of x and lo (x where it is NaN), then the smaller of that and
/// hi (_NPY_CLIP: a zero's sign as it gives it).
fn clip32(x: f32, lo: f32, hi: f32) -> f32 {
    let m = if x.is_nan() || x > lo { x } else { lo };
    if m.is_nan() || m < hi {
        m
    } else {
        hi
    }
}

fn clip64(x: f64, lo: f64, hi: f64) -> f64 {
    let m = if x.is_nan() || x > lo { x } else { lo };
    if m.is_nan() || m < hi {
        m
    } else {
        hi
    }
}

/// np.maximum(a, b): a where it is at least b (or NaN), else b.
fn maximum32(a: f32, b: f32) -> f32 {
    if a >= b || a.is_nan() {
        a
    } else {
        b
    }
}

/// np.minimum(a, b): a where it is at most b (or NaN), else b.
fn minimum32(a: f32, b: f32) -> f32 {
    if a <= b || a.is_nan() {
        a
    } else {
        b
    }
}

fn minimum64(a: f64, b: f64) -> f64 {
    if a <= b || a.is_nan() {
        a
    } else {
        b
    }
}

/// _smooth of a float32 array (its edges Python's floats, taken to float32 as numpy does).
fn smooth32(edge0: f64, edge1: f64, x: f32) -> f32 {
    let t = clip32((x - edge0 as f32) / (edge1 - edge0) as f32, 0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// _smooth of a float64 array.
fn smooth64(edge0: f64, edge1: f64, x: f64) -> f64 {
    let t = clip64((x - edge0) / (edge1 - edge0), 0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// np.remainder of float32s: fmodf, the sign of the divisor.
fn remainder32(a: f32, b: f32) -> f32 {
    let r = a % b;
    if r != 0.0 {
        if (b < 0.0) != (r < 0.0) {
            r + b
        } else {
            r
        }
    } else {
        0.0f32.copysign(b)
    }
}

/// _grid's axis: pixel centres from -1 to 1 over `n` pixels (float32).
fn axis(n: usize) -> Vec<f32> {
    (0..n)
        .map(|i| (i as f32 + 0.5) / n as f32 * 2.0 - 1.0)
        .collect()
}

/// A number type numpy's gradient and the light work in (float32 or float64).
trait Float: Copy + std::ops::Sub<Output = Self> + std::ops::Div<Output = Self> {
    fn two() -> Self;
}

impl Float for f32 {
    fn two() -> f32 {
        2.0
    }
}

impl Float for f64 {
    fn two() -> f64 {
        2.0
    }
}

/// np.gradient of an `n` x `n` array: along the rows (gy) and along the columns (gx), the
/// middles' differences halved, the edges' one-sided.
fn gradient<T: Float>(f: &[T], n: usize) -> (Vec<T>, Vec<T>) {
    let at = |i: usize, j: usize| f[i * n + j];
    let mut gy = Vec::with_capacity(n * n);
    let mut gx = Vec::with_capacity(n * n);
    for i in 0..n {
        for j in 0..n {
            gy.push(if i == 0 {
                at(1, j) - at(0, j)
            } else if i == n - 1 {
                at(n - 1, j) - at(n - 2, j)
            } else {
                (at(i + 1, j) - at(i - 1, j)) / T::two()
            });
            gx.push(if j == 0 {
                at(i, 1) - at(i, 0)
            } else if j == n - 1 {
                at(i, n - 1) - at(i, n - 2)
            } else {
                (at(i, j + 1) - at(i, j - 1)) / T::two()
            });
        }
    }
    (gy, gx)
}

/// _lit of float32 heights (the frame's): the slope and its normal in float32, its light and
/// gloss in float64 (LIGHT and HALF are float64).
fn lit32(height: &[f32], scale: f64, n: usize) -> (Vec<f64>, Vec<f64>) {
    let scaled: Vec<f32> = height.iter().map(|&h| h * scale as f32).collect();
    let (gy, gx) = gradient(&scaled, n);
    let mut diffuse = Vec::with_capacity(n * n);
    let mut spec = Vec::with_capacity(n * n);
    for (&gx, &gy) in gx.iter().zip(&gy) {
        let norm = (gx * gx + gy * gy + 1.0f32).sqrt();
        let (nx, ny, nz) = (
            (-gx / norm) as f64,
            (-gy / norm) as f64,
            (1.0f32 / norm) as f64,
        );
        let (d, s) = shade(nx, ny, nz);
        diffuse.push(d);
        spec.push(s);
    }
    (diffuse, spec)
}

/// _lit of float64 heights (the rotor's).
fn lit64(height: &[f64], scale: f64, n: usize) -> (Vec<f64>, Vec<f64>) {
    let scaled: Vec<f64> = height.iter().map(|&h| h * scale).collect();
    let (gy, gx) = gradient(&scaled, n);
    let mut diffuse = Vec::with_capacity(n * n);
    let mut spec = Vec::with_capacity(n * n);
    for (&gx, &gy) in gx.iter().zip(&gy) {
        let norm = (gx * gx + gy * gy + 1.0).sqrt();
        let (d, s) = shade(-gx / norm, -gy / norm, 1.0 / norm);
        diffuse.push(d);
        spec.push(s);
    }
    (diffuse, spec)
}

/// The diffuse shade (a flat face 0.9) and the gloss of a normal.
fn shade(nx: f64, ny: f64, nz: f64) -> (f64, f64) {
    let diffuse = clip64(
        (nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]) / LIGHT[2] * 0.9,
        0.0,
        1.0,
    );
    let spec = clip64(nx * HALF[0] + ny * HALF[1] + nz * HALF[2], 0.0, 1.0).powf(60.0);
    (diffuse, spec)
}

/// _reduce: the planes (each times alpha) and alpha, averaged over each 3 x 3 (numpy's mean of
/// float64: each row of three summed, the rows summed, by nine), as float32.
fn reduce(planes: [&[f64]; PLANES], size: usize) -> Vec<f32> {
    let n = size * SUPERSAMPLE;
    let mut out = Vec::with_capacity(PLANES * size * size);
    for plane in planes {
        for i in 0..size {
            for j in 0..size {
                let mut sum = 0.0f64;
                for r in 0..SUPERSAMPLE {
                    let row = (i * SUPERSAMPLE + r) * n + j * SUPERSAMPLE;
                    let mut part = 0.0f64;
                    for c in 0..SUPERSAMPLE {
                        part += plane[row + c];
                    }
                    sum += part;
                }
                out.push((sum / 9.0) as f32);
            }
        }
    }
    out
}

/// rotor: the rotor turned `angle` degrees clockwise, `size` px square: its planes (PLANES x
/// size x size).
pub fn rotor(look: &Look, size: usize, angle: f64) -> Vec<f32> {
    let n = size * SUPERSAMPLE;
    let grid = axis(n);
    let px = 2.0 / n as f64;
    let hub = look.hub;
    let pitch = 360.0 / look.blades as f64;
    let count = n * n;
    let (mut height, mut core) = (vec![0.0f64; count], vec![0.0f64; count]);
    let (mut part, mut alpha) = (vec![0.0f32; count], vec![0.0f32; count]);
    for (i, &y) in grid.iter().enumerate() {
        for (j, &x) in grid.iter().enumerate() {
            let k = i * n + j;
            let r = (x * x + y * y).sqrt();
            // (clockwise on the screen)
            let theta = y.atan2(x) * DEGREES - angle as f32;
            // the blades: where each pixel stands across the blade nearest to it
            let out = clip32((r - hub as f32) / (1.0 - hub) as f32, 0.0, 1.0);
            let middle = look.sweep as f32 * out.powf(look.bend as f32);
            let across = remainder32(theta - middle + (pitch / 2.0) as f32, pitch as f32)
                - (pitch / 2.0) as f32;
            let fill = look.fill_hub as f32 + (look.fill_tip - look.fill_hub) as f32 * out;
            let half = (pitch / 2.0) as f32 * fill;
            let edge = (half - across.abs()) as f64 * RADIAN * r as f64;
            let mut reach = 1.0f32 - r;
            if look.round_tip {
                // a round end: as far from the tip's centre as the blade is wide
                let tip_r = half * RADIANS * 0.98f32;
                let width = across * RADIANS * r;
                let rest = tip_r * tip_r - width * width;
                let end = (0.97f32 - tip_r) - r + maximum32(0.0, rest).sqrt();
                reach = minimum32(reach + 0.0, end);
            }
            let inner = minimum64(edge, reach as f64);
            let blade = inner > 0.0;
            let s = clip32(across / maximum32(half, 1e-6), -1.0, 1.0);
            let bevel = 3.0 * px;
            let mut h = 0.25 + 0.75 * smooth64(0.0, bevel, inner);
            h = if look.ridge {
                // two faces meeting along the middle: one lit, one in shade
                h * (1.0f32 - 0.45f32 * s.abs()) as f64
            } else {
                // a twisted blade: it rises towards its leading edge
                h * (0.75f32 + 0.25f32 * s) as f64
            };
            if look.grooves {
                // short grooves near the tip, behind the trailing edge
                let g = (s + 0.55f32) / 0.07f32;
                let groove = np::expf(-(g * g))
                    * smooth32(0.66, 0.78, r)
                    * (1.0f32 - smooth32(0.9, 0.97, r));
                h -= (0.35f32 * groove) as f64;
            }
            let mut c = 0.35 + 0.65 * smooth64(0.0, 6.0 * px, inner);
            // the hub: a dome, a flat sticker on it or a ring round it
            let on_hub = r <= hub as f32;
            let q = clip32(r / hub as f32, 0.0, 1.0);
            let mut dome = 0.95f32 + 0.35f32 * clip32(1.0 - q * q, 0.0, 1.0).sqrt();
            let mut p = 0.0f32;
            if look.sticker != 0.0 {
                let flat = q < look.sticker as f32;
                if flat {
                    dome = 1.34f32;
                    if on_hub {
                        p = 1.0;
                    }
                }
            }
            if look.hub_ring {
                let ring = q > 0.72f32 && q < 0.92f32;
                if q < 0.72f32 {
                    dome = 1.1f32 - 0.15f32 * q;
                }
                if on_hub && ring {
                    p = 1.0;
                }
            }
            if on_hub {
                let inside = hub as f32 - r;
                h = (dome * smooth32(0.0, 2.0 * px, inside) + 0.2f32) as f64;
                c = (0.55f32 + 0.45f32 * smooth32(0.0, 4.0 * px, inside)) as f64;
            }
            height[k] = h;
            core[k] = c;
            part[k] = p;
            alpha[k] = if on_hub || blade { 1.0 } else { 0.0 };
        }
    }
    let (diffuse, spec) = lit64(&height, 1.0 / (6.0 * px), n);
    let times = |plane: &[f64]| -> Vec<f64> {
        plane
            .iter()
            .zip(&alpha)
            .map(|(&v, &a)| v * a as f64)
            .collect()
    };
    let shape = times(&diffuse);
    let core = times(&core);
    let gloss: Vec<f64> = spec
        .iter()
        .zip(&alpha)
        .map(|(&s, &a)| 0.55 * s * a as f64)
        .collect();
    let wide_alpha: Vec<f64> = alpha.iter().map(|&a| a as f64).collect();
    let part: Vec<f64> = part
        .iter()
        .zip(&alpha)
        .map(|(&p, &a)| (p * a) as f64)
        .collect();
    reduce([&shape, &core, &gloss, &wide_alpha, &part], size)
}

/// frame: the square frame round the rotor, `size` px: its planes as rotor's (the part:
/// Noctua's pads in the corners, Libre Panel's lit ring).
pub fn frame(look: &Look, size: usize) -> Vec<f32> {
    let n = size * SUPERSAMPLE;
    let grid = axis(n);
    let px = 2.0 / n as f64;
    let corner = 0.16;
    let opening = 0.84; // the rotor's 38 of 48 px, and a little room
    let count = n * n;
    let (mut height, mut core) = (vec![0.0f32; count], vec![0.0f32; count]);
    let (mut part, mut alpha) = (vec![0.0f32; count], vec![0.0f32; count]);
    for (i, &y) in grid.iter().enumerate() {
        for (j, &x) in grid.iter().enumerate() {
            let k = i * n + j;
            let qx = maximum32(x.abs() - (1.0 - corner) as f32, 0.0);
            let qy = maximum32(y.abs() - (1.0 - corner) as f32, 0.0);
            let outside = (qx * qx + qy * qy).sqrt() - corner as f32; // < 0 inside
            let r = (x * x + y * y).sqrt();
            let mut body = outside < 0.0 && r > opening as f32;
            let inner = minimum32(-outside, r - opening as f32);
            let mut h = 0.3f32 + 0.7f32 * smooth32(0.0, 4.0 * px, inner);
            let mut p = 0.0f32;
            if look.noctua_frame {
                // pads over the corners, each round a screw hole
                let (cx, cy) = (x.abs() - 0.83f32, y.abs() - 0.83f32);
                let d = (cx * cx + cy * cy).sqrt();
                let pad = d < 0.2f32 && outside < 0.0;
                let hole = d < 0.07f32;
                if pad {
                    p = 1.0;
                    h += 0.25f32 * smooth32(0.0, 3.0 * px, 0.2f32 - d);
                }
                body = (body || pad) && !hole;
            } else {
                // a lit ring round the opening
                let ring = r > opening as f32 && r < (opening + 0.05) as f32;
                if ring {
                    p = 1.0;
                    h += 0.2f32;
                }
            }
            height[k] = h;
            core[k] = 0.45f32 + 0.55f32 * smooth32(0.0, 5.0 * px, inner);
            part[k] = p;
            alpha[k] = if body { 1.0 } else { 0.0 };
        }
    }
    let (diffuse, spec) = lit32(&height, 1.0 / (6.0 * px), n);
    let shape: Vec<f64> = diffuse
        .iter()
        .zip(&alpha)
        .map(|(&d, &a)| d * a as f64)
        .collect();
    let core: Vec<f64> = core
        .iter()
        .zip(&alpha)
        .map(|(&c, &a)| (c * a) as f64)
        .collect();
    let gloss: Vec<f64> = spec
        .iter()
        .zip(&alpha)
        .map(|(&s, &a)| 0.45 * s * a as f64)
        .collect();
    let wide_alpha: Vec<f64> = alpha.iter().map(|&a| a as f64).collect();
    let part: Vec<f64> = part
        .iter()
        .zip(&alpha)
        .map(|(&p, &a)| (p * a) as f64)
        .collect();
    reduce([&shape, &core, &gloss, &wide_alpha, &part], size)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numpy_s_degrees_and_radians_of_a_float32() {
        assert_eq!(DEGREES.to_bits(), 0x4265_2EE0);
        assert_eq!(RADIANS, 0.017_453_292_f32);
        assert_eq!(look("nothing").name, "noctua-g2");
        assert_eq!(look("libre").blades, 6);
        assert_eq!(angles(look("noctua-a14"), 48).len(), 48);
    }
}
