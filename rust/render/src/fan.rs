//! A list's fans (render/fan.py) in a still frame. Each fan is its rotor in the default look
//! (noctua-g2: the Blender rotor's grey layers in assets/light3d/fan, read and averaged down as
//! fan.py's _plane and _reduced do it in numpy), coloured in the theme's colour (_coloured),
//! blurred into its disc by its speed (disc_image, then Image.blend) and dimmed by its light,
//! in its ring: a faint groove all round and an arc that glows as far round as its share of the
//! most (ring_image: ImageDraw's ellipse and arc drawn three times larger and reduced, their
//! glow blurred). A still frame shows a fan as Rotor.settle has it: where its key puts its
//! blades, as fast and as bright as its speed shows, its ring as full.
//!
//! The numbers are worked out in single precision in the order numpy works them out (each step
//! rounded to single precision, none fused): a mean of three channels summed from the first,
//! a mean over the positions summed from the first one on, as numpy sums along an axis that is
//! not the last. Pillow's steps (its resize of floats, its blend, its arc and ellipse, its
//! reduce, its GaussianBlur, its alpha_composite) are taken as Pillow takes them;
//! engine/src/fan.rs makes the same of the layers Python sends it.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use lp_pixels::{alpha_composite, chops, Draw, Filter, FloatImage, Image, Mode};

use crate::piece::{Failed, Result};

/// fan_looks.DEFAULT: the look of a fan that names none, and the one Rust draws.
pub(crate) const DEFAULT_LOOK: &str = "noctua-g2";
/// fan.py's POSITIONS: rotor pictures over one blade's pitch.
pub(crate) const POSITIONS: usize = 48;
/// fan.py's FAN_SIZE, LAYER_SIZE, RING_CENTRE, RING_STROKE, RING_HALO and MARGIN: the sizes,
/// for a fan of 48 px (they scale with it).
const FAN_SIZE: f64 = 48.0;
const LAYER_SIZE: f64 = 38.0;
const RING_CENTRE: f64 = 22.0;
const RING_STROKE: f64 = 5.0;
const RING_HALO: f64 = 5.0;
const MARGIN: f64 = 9.0;
/// fan.py's GROOVE, GLOW_WHITE and GLOW_SHARE: the empty ring's brightness, how far the arc's
/// core goes towards white, how strongly its glow stands beside the core.
const GROOVE: f64 = 0.22;
const GLOW_WHITE: f64 = 0.75;
const GLOW_SHARE: f64 = 1.45;
/// fan.py's RING_STEPS (lengths of the arc) and SHADES (steps of dimming and of blur).
const RING_STEPS: f64 = 120.0;
const SHADES: i64 = 8;
/// fan.py's AMBIENT, DIFFUSE, CORE and GLOSS (how the grey layers make a colour) and
/// CORE_FLOOR (the core layer's darkest grey).
const AMBIENT: f64 = 0.35;
const DIFFUSE: f64 = 0.65;
const CORE: f64 = 0.30;
const GLOSS: f64 = 0.55;
const CORE_FLOOR: f64 = 0.596;
/// fan.py's MIN_TURNS, MAX_TURNS and STANDING_RPM (what the fan shows), BLUR (how far the
/// blades blur into a disc at full speed).
const MIN_TURNS: f64 = 0.22;
const MAX_TURNS: f64 = 1.1;
const STANDING_RPM: f64 = 30.0;
const BLUR: f64 = 0.8;
/// fan.py's LIGHT: how bright a fan shows, running, stopped or without a reading.
const RUNNING: f64 = 1.0;
const STOPPED: f64 = 0.5;
const MISSING: f64 = 0.25;
/// The side of the rotor's pictures (px) and the largest rotor drawn from them.
const MOST_SIDE: usize = 1024;

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

/// fan.turns: the turns a second a fan shows for `rpm` (none: no reading), 0 where it stands.
fn turns(rpm: Option<f64>, max_rpm: f64) -> f64 {
    let Some(rpm) = rpm else {
        return 0.0;
    };
    if rpm < STANDING_RPM || max_rpm <= 0.0 {
        return 0.0;
    }
    // (full speed a little before the ring is full; the share is from 0 to 1, NaN taken as 1
    // by min, as Python's)
    let share = py_min(1.0, rpm / (max_rpm * 0.75)).powf(0.7);
    MIN_TURNS + (MAX_TURNS - MIN_TURNS) * share
}

/// fan._start: where a fan's blades stand at first, in blade pitches (0 to 1): each fan in a
/// place of its own.
fn start(key: &str) -> f64 {
    let sum: u64 = key.chars().map(u64::from).sum();
    (sum % 97) as f64 / 97.0
}

/// A fan as a still frame shows it (fan.Rotor after settle): where its blades stand, how fast
/// it shows, how full its ring and how bright it is.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rotor {
    pub angle: f64,
    pub speed: f64,
    pub ring: f64,
    pub light: f64,
}

impl Rotor {
    /// Rotor(key).settle(rpm, max_rpm): the speed's look, standing.
    pub fn settled(key: &str, rpm: Option<f64>, max_rpm: f64) -> Rotor {
        let speed = turns(rpm, max_rpm);
        let ring = match rpm {
            Some(rpm) if max_rpm > 0.0 => py_min(1.0, py_max(0.0, rpm / max_rpm)),
            _ => 0.0,
        };
        let light = match rpm {
            None => MISSING,
            Some(_) if speed > 0.0 => RUNNING,
            Some(_) => STOPPED,
        };
        Rotor {
            angle: start(key),
            speed,
            ring,
            light,
        }
    }

    /// Its position (0 to POSITIONS - 1).
    pub fn phase(&self) -> usize {
        // (int() of a float from 0 up: cut towards 0)
        ((self.angle * POSITIONS as f64).trunc() as i64).rem_euclid(POSITIONS as i64) as usize
    }

    /// How far its blades blur, in steps of 1/SHADES.
    pub fn blur(&self) -> i64 {
        round_int(SHADES as f64 * (self.speed / MAX_TURNS).powf(2.0))
    }

    /// How much it is dimmed, 0 (not at all) to SHADES.
    pub fn dim(&self) -> i64 {
        round_int((1.0 - self.light) * SHADES as f64)
    }

    /// The lengths of its ring's arc (of RING_STEPS).
    pub fn steps(&self) -> i64 {
        round_int(self.ring * RING_STEPS)
    }
}

/// fan.geometry: a fan's diameter, the room its glow takes, and the square that holds both.
pub fn geometry(w: i64, h: i64) -> (i64, i64, i64) {
    let size = w.min(h).max(8);
    let margin = round_int(size as f64 * MARGIN / FAN_SIZE).max(2);
    (size, margin, size + 2 * margin)
}

/// fan.rotor_size: the rotor's diameter in a fan of `size` px.
fn rotor_size(size: i64) -> i64 {
    round_int(size as f64 * LAYER_SIZE / FAN_SIZE).max(6)
}

/// fan.colours for the default look in the theme's colours: `colour`, and a lighter one of it
/// for the hub.
fn colours(colour: [u8; 3]) -> ([u8; 3], [u8; 3]) {
    let lighter = colour.map(|v| {
        let v = f64::from(v);
        round_int(v + (255.0 - v) * 0.45).clamp(0, 255) as u8
    });
    (colour, lighter)
}

/// The rotor's grey layers as its pictures have them (fan.py's _plane, and what _reduced makes
/// of them before it averages them down): for each position its shape, core and gloss times
/// its alpha, and its alpha, each `side` x `side`.
struct Pictures {
    side: usize,
    planes: Vec<[Vec<f32>; 4]>,
}

/// The layers made at each size asked for lately, the newest last.
type MadeLayers = Mutex<Vec<((PathBuf, usize), Arc<Layers>)>>;
/// The discs made lately (a rotor's of a size, in a pair of colours), the newest last.
type MadeDiscs = Mutex<Vec<((PathBuf, usize, [u8; 3], [u8; 3]), Arc<Vec<u8>>)>>;

/// The rotor's layers at a size: `k` x `k` pixels, its five layers (shape, core and gloss times
/// alpha, alpha, and where a second colour goes) at each position, position after position,
/// layer after layer, row after row (fan.rotor_layers).
pub struct Layers {
    k: usize,
    planes: Vec<f32>,
}

/// The folder of the rotor's pictures beside the fonts (the app's assets/light3d/fan).
pub fn pictures_dir(fonts_dir: &Path) -> PathBuf {
    let package = fonts_dir.parent().unwrap_or(fonts_dir);
    package.join("assets").join("light3d").join("fan")
}

/// One picture as its grey (the mean of its colour channels, from the first) and its alpha,
/// each a 255th of a level (fan._plane).
fn plane(dir: &Path, name: &str) -> Result<(usize, Vec<f32>, Vec<f32>)> {
    let path = dir.join(name);
    let data =
        std::fs::read(&path).map_err(|e| Failed(format!("[Errno 2] {e}: '{}'", path.display())))?;
    let image = lp_decode::open(&data, Mode::RGBA).map_err(|e| Failed(e.to_string()))?;
    let (w, h) = image.size();
    if w != h || w == 0 || w > MOST_SIDE {
        return Err(Failed(format!("a fan's picture of {w} x {h} px")));
    }
    let (mut grey, mut alpha) = (Vec::with_capacity(w * h), Vec::with_capacity(w * h));
    for px in image.as_bytes().chunks_exact(4) {
        let [r, g, b, a] = [px[0], px[1], px[2], px[3]].map(|v| f32::from(v) / 255.0);
        grey.push((r + g + b) / 3.0);
        alpha.push(a);
    }
    Ok((w, grey, alpha))
}

/// The rotor's pictures read once (a process reads them once, whatever the size it draws).
fn pictures(dir: &Path) -> Result<Arc<Pictures>> {
    static READ: OnceLock<Mutex<HashMap<PathBuf, Arc<Pictures>>>> = OnceLock::new();
    let read = READ.get_or_init(|| Mutex::new(HashMap::new()));
    if let Some(found) = read.lock().ok().and_then(|r| r.get(dir).cloned()) {
        return Ok(found);
    }
    let floor = CORE_FLOOR as f32;
    let span = (1.0 - CORE_FLOOR) as f32;
    let mut side = 0;
    let mut planes = Vec::with_capacity(POSITIONS);
    for p in 0..POSITIONS {
        let (s, shape, alpha) = plane(dir, &format!("rotor_shape_{p:02}.png"))?;
        let (s1, core, _) = plane(dir, &format!("rotor_core_{p:02}.png"))?;
        let (s2, gloss, _) = plane(dir, &format!("rotor_gloss_{p:02}.png"))?;
        if s1 != s || s2 != s || (side != 0 && s != side) {
            return Err(Failed("the fan's pictures of sizes apart".into()));
        }
        side = s;
        // (_reduced: the core from its darkest grey up, from 0 to 1; each layer times alpha)
        let core: Vec<f32> = core
            .iter()
            .zip(&alpha)
            .map(|(&c, &a)| ((c - floor) / span).clamp(0.0, 1.0) * a)
            .collect();
        let shape: Vec<f32> = shape.iter().zip(&alpha).map(|(&s, &a)| s * a).collect();
        let gloss: Vec<f32> = gloss.iter().zip(&alpha).map(|(&g, &a)| g * a).collect();
        planes.push([shape, core, gloss, alpha]);
    }
    let made = Arc::new(Pictures { side, planes });
    if let Ok(mut read) = read.lock() {
        read.insert(dir.to_path_buf(), made.clone());
    }
    Ok(made)
}

/// fan.rotor_layers of the default look at `k` px: each layer averaged down from the
/// pictures' size (Image.resize of mode F with the box filter: edges do not darken), kept for
/// the last few sizes asked for.
pub fn layers(dir: &Path, k: usize) -> Result<Arc<Layers>> {
    static MADE: OnceLock<MadeLayers> = OnceLock::new();
    let made = MADE.get_or_init(|| Mutex::new(Vec::new()));
    let key = (dir.to_path_buf(), k);
    if let Some(found) = made
        .lock()
        .ok()
        .and_then(|m| m.iter().find(|(at, _)| *at == key).map(|(_, l)| l.clone()))
    {
        return Ok(found);
    }
    if k == 0 || k > MOST_SIDE {
        return Err(Failed(format!("a rotor of {k} px")));
    }
    let pictures = pictures(dir)?;
    let side = pictures.side;
    let kk = k * k;
    let mut planes = Vec::with_capacity(POSITIONS * 5 * kk);
    for position in &pictures.planes {
        for layer in position {
            if side == k {
                planes.extend_from_slice(layer);
            } else {
                let full = FloatImage::from_vec(side, side, layer.clone())
                    .map_err(|e| Failed(e.to_string()))?;
                let small = full
                    .resize(k, k, Filter::Box, None)
                    .map_err(|e| Failed(e.to_string()))?;
                planes.extend_from_slice(&small.data);
            }
        }
        // (the part where a second colour goes: none in this look)
        planes.extend(std::iter::repeat_n(0.0f32, kk));
    }
    let layers = Arc::new(Layers { k, planes });
    if let Ok(mut made) = made.lock() {
        if made.len() >= 8 {
            made.remove(0);
        }
        made.push((key, layers.clone()));
    }
    Ok(layers)
}

impl Layers {
    /// Its side (px).
    pub fn side(&self) -> usize {
        self.k
    }

    /// Its numbers, position after position, layer after layer, row after row.
    pub fn numbers(&self) -> &[f32] {
        &self.planes
    }

    /// fan._coloured of position `phase`: its grey layers as RGBA in `colour`, and in `second`
    /// where its fifth layer says so (where the two differ).
    fn coloured(&self, phase: usize, colour: [u8; 3], second: [u8; 3]) -> Vec<u8> {
        let kk = self.k * self.k;
        let base = (phase % POSITIONS) * 5 * kk;
        let plane = |i: usize| &self.planes[base + i * kk..base + (i + 1) * kk];
        let (shape, core, gloss, alpha, part) = (plane(0), plane(1), plane(2), plane(3), plane(4));
        let main = colour.map(|v| f32::from(v) / 255.0);
        let other = second.map(|v| f32::from(v) / 255.0);
        let (ambient, diffuse) = (AMBIENT as f32, DIFFUSE as f32);
        let (flat, edge, shine) = ((1.0 - CORE) as f32, CORE as f32, GLOSS as f32);
        let mut out = vec![0u8; kk * 4];
        for (i, px) in out.chunks_exact_mut(4).enumerate() {
            let a = alpha[i];
            let safe = a.max(1e-6);
            let shade = (shape[i] / safe).clamp(0.0, 1.0);
            let dark = (core[i] / safe).clamp(0.0, 1.0);
            let lit = (gloss[i] / safe).clamp(0.0, 1.0);
            let c = if second != colour {
                let p = (part[i] / safe).clamp(0.0, 1.0);
                [0, 1, 2].map(|j| main[j] * (1.0 - p) + other[j] * p)
            } else {
                main
            };
            let (light, depth) = (ambient + diffuse * shade, flat + edge * dark);
            let rgb = c.map(|v| v * light * depth);
            // (the hue kept: a pastel does not wash out)
            let top = 1.0f32.max(rgb[0].max(rgb[1]).max(rgb[2]));
            for (o, v) in px.iter_mut().zip(rgb) {
                let v = (v / top + shine * lit).clamp(0.0, 1.0);
                *o = (v * 255.0 + 0.5).clamp(0.0, 255.0) as u8;
            }
            px[3] = (a.clamp(0.0, 1.0) * 255.0 + 0.5).clamp(0.0, 255.0) as u8;
        }
        out
    }

    /// fan.disc_image: the rotor blurred into a disc, the mean over all its positions, each
    /// coloured first.
    fn disc(&self, colour: [u8; 3], second: [u8; 3]) -> Vec<u8> {
        let kk = self.k * self.k;
        let mut alpha = vec![0f32; kk];
        let mut sums = vec![0f32; kk * 3];
        for p in 0..POSITIONS {
            let rgba = self.coloured(p, colour, second);
            for (i, px) in rgba.chunks_exact(4).enumerate() {
                let a = f32::from(px[3]) / 255.0;
                // (numpy's mean along the positions: summed from the first one on)
                if p == 0 {
                    alpha[i] = a;
                } else {
                    alpha[i] += a;
                }
                for c in 0..3 {
                    let v = f32::from(px[c]) * a;
                    if p == 0 {
                        sums[i * 3 + c] = v;
                    } else {
                        sums[i * 3 + c] += v;
                    }
                }
            }
        }
        let n = POSITIONS as f32;
        let mut out = vec![0u8; kk * 4];
        for (i, px) in out.chunks_exact_mut(4).enumerate() {
            let mean = alpha[i] / n;
            for c in 0..3 {
                let v = (sums[i * 3 + c] / n) / mean.max(1e-6);
                px[c] = v.round_ties_even().clamp(0.0, 255.0) as u8;
            }
            px[3] = (mean * 255.0).round_ties_even().clamp(0.0, 255.0) as u8;
        }
        out
    }
}

/// fan.disc_image of the rotor's `layers` (read from `dir`) in its colours, kept for the last
/// few asked for (as fan.py keeps its pictures: the fans of a list share theirs).
fn disc(dir: &Path, layers: &Layers, colour: [u8; 3], second: [u8; 3]) -> Arc<Vec<u8>> {
    static MADE: OnceLock<MadeDiscs> = OnceLock::new();
    let made = MADE.get_or_init(|| Mutex::new(Vec::new()));
    let key = (dir.to_path_buf(), layers.side(), colour, second);
    if let Some(found) = made
        .lock()
        .ok()
        .and_then(|m| m.iter().find(|(at, _)| *at == key).map(|(_, d)| d.clone()))
    {
        return found;
    }
    let disc = Arc::new(layers.disc(colour, second));
    if let Ok(mut made) = made.lock() {
        if made.len() >= 16 {
            made.remove(0);
        }
        made.push((key, disc.clone()));
    }
    disc
}

/// fan.blades_image: the rotor at `phase` in its colours, blurred into its disc by `blur` and
/// dimmed by `dim` (steps of SHADES).
fn blades(
    dir: &Path,
    layers: &Layers,
    colours: ([u8; 3], [u8; 3]),
    phase: usize,
    blur: i64,
    dim: i64,
) -> Result<Image> {
    let (colour, second) = colours;
    let k = layers.side();
    let failed = |e: lp_pixels::Error| Failed(e.to_string());
    let mut image = Image::from_bytes(Mode::RGBA, k, k, layers.coloured(phase, colour, second))
        .map_err(failed)?;
    if blur != 0 {
        let disc = disc(dir, layers, colour, second);
        let disc = Image::from_bytes(Mode::RGBA, k, k, disc.to_vec()).map_err(failed)?;
        // (Image.blend's alpha, in single precision as Pillow's C takes it)
        let alpha = (BLUR * blur as f64 / SHADES as f64) as f32;
        image = chops::blend(&image, &disc, alpha).map_err(failed)?;
    }
    if dim != 0 {
        // (_DIMMED: each level of opacity times what is left of it, rounded as Python rounds)
        let keep = 1.0 - dim as f64 / SHADES as f64;
        let table: Vec<i64> = (0..256).map(|v| round_int(f64::from(v) * keep)).collect();
        let alpha = image
            .getchannel(3)
            .map_err(failed)?
            .point(&table)
            .map_err(failed)?;
        image.putalpha(&alpha).map_err(failed)?;
    }
    Ok(image)
}

/// fan._ring_mask: the coverage (L) of the arc from twelve o'clock, `share` of the way round
/// (all of it from 0.995 on), drawn three times larger and reduced.
fn ring_mask(canvas: usize, centre: f64, stroke: f64, share: f64) -> Result<Image> {
    const LARGER: usize = 3;
    let failed = |e: lp_pixels::Error| Failed(e.to_string());
    let g = canvas * LARGER;
    let mut mask = Image::blank(Mode::L, g, g);
    if share > 0.0 {
        // (Pillow draws a wide arc inwards from its box)
        let r = (centre + stroke / 2.0) * LARGER as f64;
        let half = g as f64 / 2.0;
        let bounds = (half - r, half - r, half + r, half + r);
        let width = round_int(stroke * LARGER as f64).max(1) as i32;
        let ink = Some([255, 0, 0, 0]);
        let mut draw = Draw::new(&mut mask);
        if share >= 0.995 {
            draw.ellipse(bounds, None, ink, width).map_err(failed)?;
        } else {
            draw.arc(bounds, -90.0, -90.0 + 360.0 * share, ink, width)
                .map_err(failed)?;
        }
    }
    mask.reduce(LARGER, LARGER, None).map_err(failed)
}

/// fan._glow of a ring's arc: a core that burns towards white, in a halo blurred by `halo`.
fn glow(mask: &Image, colour: [u8; 3], halo: f64) -> Result<Image> {
    let failed = |e: lp_pixels::Error| Failed(e.to_string());
    let soft = mask
        .gaussian_blur(halo as f32, halo as f32)
        .map_err(failed)?;
    let c = colour.map(f32::from);
    let (white, share) = (GLOW_WHITE as f32, GLOW_SHARE as f32);
    let (w, h) = mask.size();
    let mut out = vec![0u8; w * h * 4];
    for ((px, &l), &s) in out
        .chunks_exact_mut(4)
        .zip(mask.as_bytes())
        .zip(soft.as_bytes())
    {
        let m = f32::from(l) / 255.0;
        let soft = f32::from(s) / 255.0;
        // (m ** 0.7: whichever powf a system has, the levels made of it come out the same)
        let core = m.powf(0.7);
        let towards = white * core;
        for j in 0..3 {
            px[j] = (c[j] + (255.0 - c[j]) * towards + 0.5).clamp(0.0, 255.0) as u8;
        }
        let alpha = (core + share * (soft - m).clamp(0.0, 1.0)).clamp(0.0, 1.0);
        px[3] = (alpha * 255.0 + 0.5).clamp(0.0, 255.0) as u8;
    }
    Image::from_bytes(Mode::RGBA, w, h, out).map_err(failed)
}

/// fan.ring_image: the faint groove all round with the glowing arc `steps` of RING_STEPS long,
/// on a canvas of `canvas` px.
fn ring(colour: [u8; 3], size: i64, steps: i64, canvas: usize) -> Result<Image> {
    let failed = |e: lp_pixels::Error| Failed(e.to_string());
    let scale = size as f64 / FAN_SIZE;
    let (centre, stroke) = (RING_CENTRE * scale, py_max(1.5, RING_STROKE * scale));
    let groove = ring_mask(canvas, centre, stroke, 1.0)?;
    let mut out = vec![0u8; canvas * canvas * 4];
    let groove_level = GROOVE as f32;
    for (px, &g) in out.chunks_exact_mut(4).zip(groove.as_bytes()) {
        px[..3].copy_from_slice(&colour);
        px[3] = (f32::from(g) / 255.0 * groove_level * 255.0 + 0.5).clamp(0.0, 255.0) as u8;
    }
    let image = Image::from_bytes(Mode::RGBA, canvas, canvas, out).map_err(failed)?;
    if steps <= 0 {
        return Ok(image);
    }
    let share = steps as f64 / RING_STEPS;
    let arc = glow(
        &ring_mask(canvas, centre, stroke, share)?,
        colour,
        RING_HALO * scale,
    )?;
    alpha_composite(&image, &arc).map_err(failed)
}

/// A fan's picture as the renderer lays it from fan.fan_parts (_laid_fan): its ring (where it
/// has one: `ring` false is a fan of no frame) as it is, its rotor over it; `size` px with the
/// room its glow takes round it.
pub fn picture(
    dir: &Path,
    colour: [u8; 3],
    size: i64,
    rotor: &Rotor,
    ring_shown: bool,
) -> Result<Image> {
    let failed = |e: lp_pixels::Error| Failed(e.to_string());
    let (size, _margin, canvas) = geometry(size, size);
    let k = rotor_size(size);
    let (side, canvas_px) = (k as usize, canvas as usize);
    let mut laid = if ring_shown {
        ring(colour, size, rotor.steps(), canvas_px)?
    } else {
        Image::blank(Mode::RGBA, canvas_px, canvas_px)
    };
    let layers = layers(dir, side)?;
    let rotor_picture = blades(
        dir,
        &layers,
        colours(colour),
        rotor.phase(),
        rotor.blur(),
        rotor.dim(),
    )?;
    let at = (canvas - k).div_euclid(2);
    laid.alpha_composite_at(&rotor_picture, (at, at), (0, 0, k, k))
        .map_err(failed)?;
    Ok(laid)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_fan_stands_as_its_speed_shows() {
        let still = Rotor::settled("l0\0fan.1", None, 2000.0);
        assert_eq!((still.speed, still.ring, still.light), (0.0, 0.0, MISSING));
        assert_eq!(still.dim(), 6);
        let slow = Rotor::settled("l0\0fan.1", Some(20.0), 2000.0);
        assert_eq!((slow.speed, slow.light), (0.0, STOPPED));
        let fast = Rotor::settled("l0\0fan.1", Some(4000.0), 2000.0);
        assert_eq!(
            (fast.speed, fast.ring, fast.light),
            (MAX_TURNS, 1.0, RUNNING)
        );
        assert_eq!((fast.blur(), fast.dim(), fast.steps()), (8, 0, 120));
        // (NaN: min takes 1 for it, max 0, as Python's)
        let odd = Rotor::settled("x", Some(f64::NAN), 2000.0);
        assert_eq!((odd.speed, odd.ring), (MAX_TURNS, 0.0));
        assert!(Rotor::settled("x", Some(500.0), 2000.0).phase() < POSITIONS);
    }

    #[test]
    fn a_fan_s_sizes_scale_with_it() {
        assert_eq!(geometry(48, 60), (48, 9, 66));
        assert_eq!(geometry(4, 4), (8, 2, 12));
        assert_eq!(rotor_size(48), 38);
        assert_eq!(rotor_size(4), 6);
        assert_eq!(colours([0, 100, 255]), ([0, 100, 255], [115, 170, 255]));
    }
}
