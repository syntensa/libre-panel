//! render/field.py: a light field under a theme, soft pools of light that drift slowly over the
//! panels that stand together, as one; each panel shows its part of it, where it stands (in
//! millimetres). Made at an eighth of the panel's pixels, a point in the middle of each block
//! of 8 by 8 (the C library's exp, sin and cos, the products and sums in Python's order), its
//! levels in sixteenths, and drawn up between them in whole numbers ([`drawn_up`]), each pixel
//! made a level of 0 to 255 only at the end, through a dither of four by four.
//!
//! The light is faint (a few dozen levels across a panel at rest): made whole levels at once,
//! as render/field.py made them, each step of a level stood out as an edge across the panel, its
//! points' rounding in steps of eight pixels, and more so through the panel's video. In
//! sixteenths, with an ordered dither (the same pattern in every frame, so that a frame that
//! does not change stays the same to the bit), a step is a mix of the levels on either side.

use std::f64::consts::PI;

use lp_pixels::{Image, Mode};

/// The panel's pixels a point of the field stands for, across and down.
pub const STEP: usize = 8;
/// A point's level in parts of a level (sixteenths: 0 to 4080).
pub const FINE: u16 = 16;
/// The dither's pattern (Bayer's, four by four): each pixel made a level from its exact one
/// plus (k + 1/2) / 16 of a level, k as it lies in the pattern (row by row: y % 4, x % 4).
pub const DITHER: [[u32; 4]; 4] = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]];
/// Fields a second (the panel's frames between are the same: the encoder skips them).
pub const RATE: f64 = 6.25;

/// x, y, width, height in millimetres.
pub type Rect = (f64, f64, f64, f64);

/// A pool of light: its colour at rest and under load, how far it drifts as a share of the
/// canvas across and down, in how many seconds it goes there and back each way, where in its
/// round it starts (radians), and how wide it is (its Gaussian's sigma, millimetres).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Pool {
    pub cool: [f64; 3],
    pub warm: [f64; 3],
    pub ax: f64,
    pub ay: f64,
    pub px: f64,
    pub py: f64,
    pub phase: f64,
    pub radius: f64,
}

const fn pool(
    cool: [f64; 3],
    warm: [f64; 3],
    drift: (f64, f64),
    round: (f64, f64),
    phase: f64,
    radius: f64,
) -> Pool {
    Pool {
        cool,
        warm,
        ax: drift.0,
        ay: drift.1,
        px: round.0,
        py: round.1,
        phase,
        radius,
    }
}

/// Lumen's: its CPU, GPU and memory colours at rest, amber, coral and rose under load.
#[rustfmt::skip]
pub const LUMEN: [Pool; 3] = [
    pool([46.0, 230.0, 214.0], [245.0, 168.0, 84.0], (0.46, 0.36), (330.0, 247.0), 0.4, 64.0),
    pool([127.0, 156.0, 255.0], [248.0, 113.0, 113.0], (0.44, 0.40), (390.0, 281.0), 2.5, 72.0),
    pool([199.0, 146.0, 255.0], [255.0, 150.0, 120.0], (0.42, 0.38), (450.0, 223.0), 4.4, 58.0),
];
/// Arena's: its CPU, GPU and memory colours (the Arctic look's) at rest, warm as Lumen's.
#[rustfmt::skip]
pub const ARENA: [Pool; 3] = [
    pool([19.0, 229.0, 215.0], [245.0, 168.0, 84.0], (0.46, 0.36), (330.0, 247.0), 0.4, 64.0),
    pool([163.0, 174.0, 255.0], [248.0, 113.0, 113.0], (0.44, 0.40), (390.0, 281.0), 2.5, 72.0),
    pool([224.0, 168.0, 255.0], [255.0, 150.0, 120.0], (0.42, 0.38), (450.0, 223.0), 4.4, 58.0),
];
/// A light in a picture's colours (background.field.colours: a cover's, cover.rs's colours):
/// each pool in one of them, at rest and under load alike (the load makes it brighter, not
/// warmer), this much stronger, as it stands for the picture behind the whole screen ...
pub const TINTED: f64 = 1.8;
/// ... and from one picture's colours to the next it glides this long (smoothstep), where the
/// frames stream; elsewhere it takes them at once.
pub const TINT_S: f64 = 2.0;
/// The light's strength at rest, and what full load adds.
const STRENGTH: (f64, f64) = (0.19, 0.15);
/// The light swells and ebbs once in this long.
const BREATH_S: f64 = 9.0;
/// By this share at rest, and what full load adds.
const BREATH: (f64, f64) = (0.04, 0.10);
/// How far toward its warm colour a pool goes at full load.
const WARMTH: f64 = 0.85;

/// The pools of the look `name`; None for a look not known.
pub fn look(name: &str) -> Option<&'static [Pool]> {
    match name {
        "lumen" => Some(&LUMEN),
        "arena" => Some(&ARENA),
        _ => None,
    }
}

/// tinted: `pools` in `colours` (one each, in turn): each in its colour at rest and under load,
/// [`TINTED`] stronger; where they drift as they do.
pub fn tinted(pools: &[Pool], colours: &[[u8; 3]]) -> Vec<Pool> {
    pools
        .iter()
        .enumerate()
        .map(|(i, p)| {
            let c = colours[i % colours.len()].map(|v| f64::from(v) * TINTED);
            Pool {
                cool: c,
                warm: c,
                ..*p
            }
        })
        .collect()
}

/// gliding: `k` (0 to 1, eased: smoothstep) of the way from the colours of `before` to those
/// of `after` (the same pools: where they drift as `after` has it).
pub fn gliding(before: &[Pool], after: &[Pool], k: f64) -> Vec<Pool> {
    let t = k.clamp(0.0, 1.0);
    let e = t * t * (3.0 - 2.0 * t);
    if e >= 1.0 {
        return after.to_vec();
    }
    before
        .iter()
        .zip(after)
        .map(|(a, b)| Pool {
            cool: std::array::from_fn(|c| a.cool[c] + (b.cool[c] - a.cool[c]) * e),
            warm: std::array::from_fn(|c| a.warm[c] + (b.warm[c] - a.warm[c]) * e),
            ..*b
        })
        .collect()
}

/// stepped: the moment of the field shown at `t` (wall clock, seconds): the last of its steps.
pub fn stepped(t: f64) -> f64 {
    (t * RATE).floor() / RATE
}

/// small: the field at an eighth of the panel (`size` its pixels) as levels in sixteenths
/// ([`FINE`]), RGB row by row:
/// `own` where the panel stands and `whole` the canvas of all the panels standing together
/// (millimetres), at `t` (seconds) under `load` (0 to 1), its light on `base` (the theme's
/// background colour); its width and height first. Each point in the middle of its block of
/// the panel's pixels, the last one's too where the panel ends within it.
pub fn small(
    size: (usize, usize),
    own: Rect,
    whole: Rect,
    t: f64,
    load: f64,
    pools: &[Pool],
    base: [u8; 3],
) -> (usize, usize, Vec<u16>) {
    let load = load.clamp(0.0, 1.0);
    let (fw, fh) = (size.0.div_ceil(STEP), size.1.div_ceil(STEP));
    let half = STEP as f64 / 2.0;
    let (across, down) = (own.2 / size.0 as f64, own.3 / size.1 as f64);
    let xs: Vec<f64> = (0..fw)
        .map(|i| own.0 + ((i * STEP) as f64 + half) * across)
        .collect();
    let ys: Vec<f64> = (0..fh)
        .map(|i| own.1 + ((i * STEP) as f64 + half) * down)
        .collect();
    let strength = STRENGTH.0 + STRENGTH.1 * load;
    let breathe = 1.0 + (BREATH.0 + BREATH.1 * load) * (2.0 * PI * t / BREATH_S).sin();
    let mix = (load * WARMTH).min(1.0);
    let mut acc: Vec<f64> = base
        .iter()
        .map(|&v| f64::from(v))
        .cycle()
        .take(fw * fh * 3)
        .collect();
    for p in pools {
        let cx = whole.0 + whole.2 * (0.5 + p.ax * (2.0 * PI * t / p.px + p.phase).sin());
        let cy = whole.1 + whole.3 * (0.5 + p.ay * (2.0 * PI * t / p.py + p.phase * 1.3).cos());
        let inv = 1.0 / (2.0 * p.radius * p.radius);
        let across: Vec<f64> = xs
            .iter()
            .map(|&x| (-((x - cx) * (x - cx)) * inv).exp())
            .collect();
        let down: Vec<f64> = ys
            .iter()
            .map(|&y| (-((y - cy) * (y - cy)) * inv).exp())
            .collect();
        let colour: [f64; 3] = std::array::from_fn(|c| {
            (p.cool[c] + (p.warm[c] - p.cool[c]) * mix) * strength * breathe
        });
        for (row, &d) in down.iter().enumerate() {
            for (col, &a) in across.iter().enumerate() {
                let weight = d * a;
                let at = (row * fw + col) * 3;
                for c in 0..3 {
                    acc[at + c] += weight * colour[c];
                }
            }
        }
    }
    let fine = f64::from(FINE);
    let levels = acc
        .iter()
        .map(|&v| (v * fine + 0.5).floor().clamp(0.0, 255.0 * fine) as u16)
        .collect();
    (fw, fh, levels)
}

/// field: the panel's part of the field (RGB, `size`): [`small`] drawn up.
pub fn field(
    size: (usize, usize),
    own: Rect,
    whole: Rect,
    t: f64,
    load: f64,
    pools: &[Pool],
    base: [u8; 3],
) -> lp_pixels::Result<Image> {
    let (fw, fh, levels) = small(size, own, whole, t, load, pools, base);
    Image::from_bytes(Mode::RGB, size.0, size.1, drawn_up(&levels, fw, fh, size))
}

/// For each of `n` pixels in a row, the two points of the field it lies between and how near
/// the second, in sixteenths (render/field.py's _between): the pixel's middle is
/// `(2x - 7) / 16` of a block from the first point's.
fn between(n: usize, points: usize) -> Vec<(usize, usize, u16)> {
    let last = points as i64 - 1;
    (0..n as i64)
        .map(|x| {
            let sixteenths = 2 * x - (STEP as i64 - 1);
            let first = sixteenths.div_euclid(16);
            let near = (sixteenths - 16 * first) as u16;
            let at = |i: i64| i.clamp(0, last) as usize;
            (at(first), at(first + 1), near)
        })
        .collect()
}

/// The dither's thresholds for the rows of a frame `w` pixels wide (RGB), four of them (by the
/// row's place in the pattern, y % 4): (k + 1/2) / 16 of a level in 256ths, k as [`DITHER`]
/// has it at the pixel's place across.
pub fn thresholds(w: usize) -> [Vec<u16>; 4] {
    std::array::from_fn(|row| {
        (0..w)
            .flat_map(|x| [(DITHER[row][x % 4] * 16 + 8) as u16; 3])
            .collect()
    })
}

/// drawn_up: the field's levels in sixteenths (`fw` x `fh`, RGB) drawn up to `size` as the
/// engine draws them up, to the bit: each pixel between the four points nearest its middle,
/// weighted in sixteenths each way (across first, in sixteenths of a level, then down, in
/// 256ths), and made a level once, through the dither ([`DITHER`]); beyond the first and the
/// last points, as they are. A whole level stays that level everywhere (black stays black), and
/// over four by four pixels the levels make the exact one.
pub fn drawn_up(levels: &[u16], fw: usize, fh: usize, size: (usize, usize)) -> Vec<u8> {
    let (w, h) = size;
    let taps = between(w, fw);
    let across: Vec<Vec<u16>> = levels
        .chunks_exact(fw * 3)
        .take(fh)
        .map(|row| {
            let mut out = Vec::with_capacity(w * 3);
            for &(a, b, f) in &taps {
                for c in 0..3 {
                    let (va, vb) = (u32::from(row[a * 3 + c]), u32::from(row[b * 3 + c]));
                    out.push(((va * u32::from(16 - f) + vb * u32::from(f) + 8) >> 4) as u16);
                }
            }
            out
        })
        .collect();
    let thresholds = thresholds(w);
    let mut out = vec![0u8; w * h * 3];
    let rows = between(h, fh).into_iter().zip(out.chunks_exact_mut(w * 3));
    for (y, ((a, b, f), line)) in rows.enumerate() {
        let (first, second, g) = (&across[a], &across[b], 16 - f);
        // (at most 4080 * 16 + 248: sixteen bits)
        for (((o, &u), &v), &k) in line
            .iter_mut()
            .zip(first)
            .zip(second)
            .zip(&thresholds[y % 4])
        {
            *o = ((u * g + v * f + k) >> 8) as u8;
        }
    }
    out
}

// -- the light as a frame's ground ------------------------------------------------------------

/// The readings whose load the light follows (the higher of them), and over how many of them.
pub const LOAD_SENSORS: [&str; 2] = ["cpu.load", "gpu.load"];
pub const LOAD_SAMPLES: usize = 10;

/// load_of: the load the light follows (0 to 1): the higher of [`LOAD_SENSORS`]' means over
/// their last [`LOAD_SAMPLES`] numbers in `history`, `eased` (0 to 1) of the way from the mean
/// before the newest number to it (smoothstep); 0 where none of them has a number.
pub fn load_of(history: &indexmap::IndexMap<String, Vec<f64>>, eased: f64) -> f64 {
    let t = eased.clamp(0.0, 1.0);
    let k = t * t * (3.0 - 2.0 * t);
    let mut load: f64 = 0.0;
    for key in LOAD_SENSORS {
        let Some(values) = history.get(key).filter(|v| !v.is_empty()) else {
            continue;
        };
        let n = values.len();
        let now = &values[n.saturating_sub(LOAD_SAMPLES)..];
        let before = &values[n.saturating_sub(LOAD_SAMPLES + 1)..n - 1];
        let before = if before.is_empty() { now } else { before };
        // (Python's sum, left to right, then divided)
        let mean = |v: &[f64]| v.iter().fold(0.0, |a, b| a + b) / v.len() as f64;
        let (a, b) = (mean(before), mean(now));
        load = load.max(a + (b - a) * k);
    }
    (load / 100.0).clamp(0.0, 1.0)
}

/// What a light was made of: the frame's size, where the panel stands and the canvas, the
/// field's moment and load, its pools (its look's, or in a picture's colours) and the colour
/// under it.
#[derive(Clone, Debug, PartialEq)]
pub struct LightKey {
    pub size: (usize, usize),
    pub own: Rect,
    pub whole: Rect,
    pub t: f64,
    pub load: f64,
    pub pools: Vec<Pool>,
    pub base: [u8; 3],
}

/// Light: a light field as a frame's ground (render/field.py's Light): its points (`fw` x `fh`,
/// RGB) and drawn up to the frame (made the first time it is asked for, where the frame is
/// composed here: the engine draws it up itself).
pub struct Light {
    /// its points' levels in sixteenths ([`FINE`])
    pub small: Vec<u16>,
    pub fw: usize,
    pub fh: usize,
    pub size: (usize, usize),
    pub key: LightKey,
    picture: std::cell::OnceCell<Image>,
}

impl Light {
    /// light: the light of [`field`] as a frame's ground.
    pub fn new(key: LightKey) -> Light {
        let (fw, fh, small) = small(
            key.size, key.own, key.whole, key.t, key.load, &key.pools, key.base,
        );
        Light {
            small,
            fw,
            fh,
            size: key.size,
            key,
            picture: std::cell::OnceCell::new(),
        }
    }

    /// The light drawn up to the frame (RGB).
    pub fn picture(&self) -> &Image {
        self.picture.get_or_init(|| {
            let drawn = drawn_up(&self.small, self.fw, self.fh, self.size);
            Image::from_bytes(Mode::RGB, self.size.0, self.size.1, drawn).expect("its size")
        })
    }

    /// same: whether two lights are the same light (the same points for a frame of one size).
    pub fn same(&self, other: &Light) -> bool {
        self.size == other.size && self.fw == other.fw && self.small == other.small
    }
}

/// alone: where a panel showing a theme of `size` on the model `model` stands by itself
/// (millimetres, at 0, 0): the size of its model's picture, else as dense as the 9.2" bar.
pub fn alone(model: &str, size: (usize, usize)) -> Rect {
    const GUESS_MM_PER_PX: f64 = 0.118;
    let mm = lp_devices::models::find_model(model).and_then(|m| {
        let inch = m.size_inch.get();
        (inch != 0.0).then(|| {
            let per = inch * 25.4 / (m.native_width as f64).hypot(m.native_height as f64);
            (size.0 as f64 * per, size.1 as f64 * per)
        })
    });
    let (w, h) = mm.unwrap_or((
        size.0 as f64 * GUESS_MM_PER_PX,
        size.1 as f64 * GUESS_MM_PER_PX,
    ));
    (0.0, 0.0, w, h)
}

// -- what lies in front of the light -------------------------------------------------------------

/// `v` / 255 rounded, as Pillow divides (exact for 0 to 65,025).
fn div255(v: u32) -> u32 {
    let t = v + 128;
    (t + (t >> 8)) >> 8
}

/// Foreground: what lies in front of the light over a box (render/field.py's Foreground):
/// every piece laid on it in turn as on a clear picture, its colours premultiplied by their
/// cover, eight bits each (RGBA, row by row).
pub struct Foreground {
    pub width: usize,
    pub height: usize,
    pub rgba: Vec<u8>,
}

impl Foreground {
    pub fn new(width: usize, height: usize) -> Foreground {
        Foreground {
            width,
            height,
            rgba: vec![0; width * height * 4],
        }
    }

    /// The part of a picture `w` x `h` at (x, y) on the box: its corners on the box.
    fn on_box(&self, x: i64, y: i64, w: usize, h: usize) -> Option<(usize, usize, usize, usize)> {
        let (x0, y0) = (x.max(0), y.max(0));
        let x1 = x.saturating_add(w as i64).min(self.width as i64);
        let y1 = y.saturating_add(h as i64).min(self.height as i64);
        (x1 > x0 && y1 > y0).then_some((x0 as usize, y0 as usize, x1 as usize, y1 as usize))
    }

    /// Each pixel of a picture `w` x `h` at (x, y) that lies on the box, with the pixel it
    /// lies on: `lay(foreground pixel, index of the picture's pixel)`.
    fn each(&mut self, x: i64, y: i64, w: usize, h: usize, mut lay: impl FnMut(&mut [u8], usize)) {
        let Some((x0, y0, x1, y1)) = self.on_box(x, y, w, h) else {
            return;
        };
        for fy in y0..y1 {
            let sy = (fy as i64 - y) as usize;
            for fx in x0..x1 {
                let sx = (fx as i64 - x) as usize;
                let at = (fy * self.width + fx) * 4;
                lay(&mut self.rgba[at..at + 4], sy * w + sx);
            }
        }
    }

    /// A picture through an alpha (`colour` RGB, `alpha` one byte each, `w` x `h`) at (x, y):
    /// `F = div255(s a + F (255 - a))`, `A = a + div255(A (255 - a))`.
    pub fn over(&mut self, colour: &[u8], alpha: &[u8], w: usize, h: usize, x: i64, y: i64) {
        self.each(x, y, w, h, |d, i| {
            let a = u32::from(alpha[i]);
            let rest = 255 - a;
            for c in 0..3 {
                d[c] = div255(u32::from(colour[i * 3 + c]) * a + u32::from(d[c]) * rest) as u8;
            }
            d[3] = (a + div255(u32::from(d[3]) * rest)) as u8;
        });
    }

    /// An RGBA picture through its alpha.
    pub fn over_rgba(&mut self, rgba: &[u8], w: usize, h: usize, x: i64, y: i64) {
        self.each(x, y, w, h, |d, i| {
            let s = &rgba[i * 4..i * 4 + 4];
            let a = u32::from(s[3]);
            let rest = 255 - a;
            for c in 0..3 {
                d[c] = div255(u32::from(s[c]) * a + u32::from(d[c]) * rest) as u8;
            }
            d[3] = (a + div255(u32::from(d[3]) * rest)) as u8;
        });
    }

    /// An RGB picture as it is, opaque.
    pub fn solid(&mut self, rgb: &[u8], w: usize, h: usize, x: i64, y: i64) {
        self.each(x, y, w, h, |d, i| {
            d[..3].copy_from_slice(&rgb[i * 3..i * 3 + 3]);
            d[3] = 255;
        });
    }

    /// Light (RGB) added, as light adds up.
    pub fn add(&mut self, rgb: &[u8], w: usize, h: usize, x: i64, y: i64) {
        self.each(x, y, w, h, |d, i| {
            for c in 0..3 {
                d[c] = d[c].saturating_add(rgb[i * 3 + c]);
            }
        });
    }

    /// The foreground of `(x0, y0, x1, y1)` within the box over `light` (RGB, that box's
    /// size): `min(255, F + div255(g (255 - A)))`.
    pub fn on(&self, light: &[u8], (x0, y0, x1, y1): (usize, usize, usize, usize)) -> Vec<u8> {
        let mut out = Vec::with_capacity((x1 - x0) * (y1 - y0) * 3);
        for y in y0..y1 {
            for x in x0..x1 {
                let f = &self.rgba[(y * self.width + x) * 4..][..4];
                let g = &light[((y - y0) * (x1 - x0) + (x - x0)) * 3..][..3];
                let rest = 255 - u32::from(f[3]);
                for c in 0..3 {
                    out.push((u32::from(f[c]) + div255(u32::from(g[c]) * rest)).min(255) as u8);
                }
            }
        }
        out
    }
}

/// lay: `piece` laid on `fore`, whose top left corner is `origin` on the panel, as
/// render/field.py's lay: a frosted backdrop first (what shows beneath it at this moment, the
/// light of the box `light` included, blurred, laid through its mask), then the piece (added
/// light, an opaque picture as it is, an L through itself, else through its alpha), then the
/// light it gives.
pub fn lay(
    fore: &mut Foreground,
    piece: &crate::piece::Piece,
    origin: (i64, i64),
    light: &[u8],
) -> lp_pixels::Result<()> {
    let (ox, oy) = origin;
    if let (Some(mask), Some([bx, by, bw, bh])) = (&piece.backdrop, piece.hit) {
        let (bx, by) = (bx - ox, by - oy);
        let region = (
            bx.max(0),
            by.max(0),
            bx.saturating_add(bw).min(fore.width as i64),
            by.saturating_add(bh).min(fore.height as i64),
        );
        if region.2 > region.0 && region.3 > region.1 {
            let (rx0, ry0, rx1, ry1) = (
                region.0 as usize,
                region.1 as usize,
                region.2 as usize,
                region.3 as usize,
            );
            let mut under = Vec::with_capacity((rx1 - rx0) * (ry1 - ry0) * 3);
            for y in ry0..ry1 {
                let at = (y * fore.width + rx0) * 3;
                under.extend_from_slice(&light[at..at + (rx1 - rx0) * 3]);
            }
            let beneath = fore.on(&under, (rx0, ry0, rx1, ry1));
            let r = piece.backdrop_radius;
            let blurred =
                Image::from_bytes(Mode::RGB, rx1 - rx0, ry1 - ry0, beneath)?.gaussian_blur(r, r)?;
            let cut = mask.crop(region.0 - bx, region.1 - by, region.2 - bx, region.3 - by);
            let cut = if cut.mode() == Mode::L {
                cut
            } else {
                cut.convert(Mode::L)?
            };
            fore.over(
                blurred.as_bytes(),
                cut.as_bytes(),
                rx1 - rx0,
                ry1 - ry0,
                region.0,
                region.1,
            );
        }
    }
    let layer = piece.whole();
    let (x, y) = (piece.x - ox, piece.y - oy);
    let (w, h) = (layer.width(), layer.height());
    if piece.add {
        let rgb = if layer.mode() == Mode::RGB {
            std::borrow::Cow::Borrowed(&*layer)
        } else {
            std::borrow::Cow::Owned(layer.convert(Mode::RGB)?)
        };
        fore.add(rgb.as_bytes(), w, h, x, y);
        return Ok(());
    }
    match layer.mode() {
        Mode::RGB => fore.solid(layer.as_bytes(), w, h, x, y),
        Mode::L => {
            let grey: Vec<u8> = layer.as_bytes().iter().flat_map(|&v| [v, v, v]).collect();
            fore.over(&grey, layer.as_bytes(), w, h, x, y);
        }
        Mode::RGBA => fore.over_rgba(layer.as_bytes(), w, h, x, y),
        _ => {
            let rgba = layer.convert(Mode::RGBA)?;
            fore.over_rgba(rgba.as_bytes(), w, h, x, y);
        }
    }
    if let Some(glow) = &piece.light {
        let glow_layer = glow.whole();
        let rgb = glow_layer.convert(Mode::RGB)?;
        fore.add(
            rgb.as_bytes(),
            rgb.width(),
            rgb.height(),
            glow.x - ox,
            glow.y - oy,
        );
    }
    Ok(())
}

/// compose: the frame of `pieces` over `light` (RGB) in the box (x0, y0, x1, y1) on the panel,
/// as render/field.py composes it; a piece that fails is left out, its error kept in `failed`.
pub fn compose<'a>(
    light: &Light,
    pieces: impl IntoIterator<Item = &'a (String, std::rc::Rc<crate::piece::Piece>)>,
    (x0, y0, x1, y1): (i64, i64, i64, i64),
    failed: &mut Vec<(String, String)>,
) -> lp_pixels::Result<Image> {
    let (w, h) = ((x1 - x0) as usize, (y1 - y0) as usize);
    let ground = light.picture().crop(x0, y0, x1, y1);
    let mut fore = Foreground::new(w, h);
    for (id, piece) in pieces {
        if let Err(e) = lay(&mut fore, piece, (x0, y0), ground.as_bytes()) {
            failed.push((id.clone(), e.0));
        }
    }
    Image::from_bytes(Mode::RGB, w, h, fore.on(ground.as_bytes(), (0, 0, w, h)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_field_runs_on_from_one_panel_to_the_next() {
        // two panels side by side as one canvas: where they meet, the light is the same
        let whole = (0.0, 0.0, 200.0, 50.0);
        let left = small(
            (800, 400),
            (0.0, 0.0, 100.0, 50.0),
            whole,
            37.0,
            0.4,
            &LUMEN,
            [0, 0, 0],
        );
        let right = small(
            (800, 400),
            (100.0, 0.0, 100.0, 50.0),
            whole,
            37.0,
            0.4,
            &LUMEN,
            [0, 0, 0],
        );
        let both = small((1600, 400), whole, whole, 37.0, 0.4, &LUMEN, [0, 0, 0]);
        let (w, h) = (both.0, both.1);
        for row in 0..h {
            for col in 0..w {
                let (part, x) = if col < w / 2 {
                    (&left, col)
                } else {
                    (&right, col - w / 2)
                };
                for c in 0..3 {
                    assert_eq!(
                        part.2[(row * part.0 + x) * 3 + c],
                        both.2[(row * w + col) * 3 + c]
                    );
                }
            }
        }
    }

    #[test]
    fn a_level_stays_that_level_and_a_part_of_one_is_a_mix_of_its_neighbours() {
        let (fw, fh, size) = (5, 3, (40, 24));
        // a whole level everywhere: that level in every pixel (black stays black)
        for level in [0u16, 37, 255] {
            let flat = vec![level * FINE; fw * fh * 3];
            let up = drawn_up(&flat, fw, fh, size);
            assert!(up.iter().all(|&v| u16::from(v) == level), "{level}");
        }
        // a quarter of a level above 20: in each four by four pixels, four at 21
        let quarter = vec![20 * FINE + FINE / 4; fw * fh * 3];
        let up = drawn_up(&quarter, fw, fh, size);
        assert!(up.iter().all(|&v| v == 20 || v == 21));
        for (by, bx) in (0..6).flat_map(|by| (0..10).map(move |bx| (by, bx))) {
            let at = |y: usize, x: usize| up[((by * 4 + y) * 40 + bx * 4 + x) * 3];
            let lit = (0..4)
                .flat_map(|y| (0..4).map(move |x| (y, x)))
                .filter(|&(y, x)| at(y, x) == 21)
                .count();
            assert_eq!(lit, 4, "{by} {bx}");
        }
        // the same light, the same pixels: the pattern stays where it is, frame after frame
        assert_eq!(up, drawn_up(&quarter, fw, fh, size));
    }

    #[test]
    fn a_step_of_a_level_is_no_edge_but_a_mix() {
        // from 20 to 21 over a row of points: the pixels between mix the two, more and more
        let (fw, fh, size) = (6, 1, (48, 4));
        let levels: Vec<u16> = (0..fw)
            .flat_map(|i| [20 * FINE + (i as u16) * FINE / 5; 3])
            .collect();
        let up = drawn_up(&levels, fw, fh, size);
        let lit = |x0: usize| (x0..x0 + 8).filter(|&x| up[x * 3] == 21).count();
        let mixes: Vec<usize> = (0..6).map(|i| lit(i * 8)).collect();
        assert!(mixes.windows(2).all(|w| w[0] <= w[1]), "{mixes:?}");
        assert!(
            mixes[0] < 8 && mixes[5] > 0 && mixes.iter().filter(|&&n| n > 0 && n < 8).count() >= 3
        );
    }

    #[test]
    fn load_warms_and_brightens_it() {
        let own = (0.0, 0.0, 100.0, 50.0);
        let sum = |load| {
            small((800, 400), own, own, 0.0, load, &LUMEN, [0, 0, 0])
                .2
                .iter()
                .map(|&v| v as u64)
                .sum::<u64>()
        };
        assert!(sum(1.0) > sum(0.0) * 3 / 2, "{} {}", sum(1.0), sum(0.0));
        // (a field each 0.16 s: the panels' frames between it and the next are the same)
        assert_eq!(stepped(1.159), 1.12);
        assert_eq!(stepped(1.119), 0.96);
    }
}
