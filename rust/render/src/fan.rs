//! A list's fans and fan widgets (render/fan.py). Each fan is its rotor in its look (noctua-g2:
//! the Blender rotor's grey layers in assets/light3d/fan, read and averaged down as fan.py's
//! _plane and _reduced do it in numpy; the others drawn from their shape, render/fan_looks.rs),
//! coloured in the theme's colour or the look's own (_coloured), blurred into its disc by its
//! speed (disc_image, then Image.blend) and dimmed by its light, in its frame: a ring, a faint
//! groove all round and an arc that glows as far round as its share of the most (ring_image:
//! ImageDraw's ellipse and arc drawn three times larger and reduced, their glow blurred), a
//! lit square frame, brighter the faster it runs (frame_image: the Blender frame's layers, or
//! the look's own drawn one), or none. A still frame shows a fan as Rotor.settle has it: where its
//! blades, as fast and as bright as its speed shows, its ring as full. In a moving frame it
//! turns as Rotor.advance has it (Turning): its speed, ring and light follow the reading with
//! inertia, it runs out and stands at 0 RPM, and its blades stand where the beat of its sensor
//! has them (Beat, the same for every fan of the sensor at the panel's frame rate).
//!
//! The numbers are worked out in single precision in the order numpy works them out (each step
//! rounded to single precision, none fused): a mean of three channels summed from the first,
//! a mean over the positions summed from the first one on, as numpy sums along an axis that is
//! not the last. Pillow's steps (its resize of floats, its blend, its arc and ellipse, its
//! reduce, its GaussianBlur, its alpha_composite) are taken as Pillow takes them;
//! engine/src/fan.rs makes the same of the layers Python sends it.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::{Arc, Mutex, OnceLock};

use lp_devices::scene::{FanRing, MadePicture, Recipe, RotorPlanes};
use lp_pixels::{alpha_composite, chops, Draw, Filter, FloatImage, Image, Mode};

use crate::fan_looks::{self, Look};
use crate::piece::{Failed, Result};

/// fan_looks.DEFAULT: the look of a fan that names none.
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
/// fan.py's INERTIA_S (the time constant a fan follows its speed with), MAX_STEP (blade
/// pitches a frame at most: it never seems to turn backwards) and FRICTION (turns a second lost
/// every second while it runs out).
const INERTIA_S: f64 = 0.45;
const MAX_STEP: f64 = 0.3;
const FRICTION: f64 = 0.35;
/// fan.py's LIGHT: how bright a fan shows, running, stopped or without a reading.
const RUNNING: f64 = 1.0;
const STOPPED: f64 = 0.5;
const MISSING: f64 = 0.25;
/// The side of the rotor's pictures (px) and the largest rotor drawn from them.
const MOST_SIDE: usize = 1024;
/// fan.py's LAYER_BYTES: the bytes of the rotors' layers kept, and of the frames' apart (the
/// least recently used go, the newest always stays, as render/kept.py's Kept keeps them).
const LAYER_BYTES: usize = 24 << 20;

/// Kept.get: the value kept by `key`, now the most recently used.
fn kept<K: PartialEq, V: Clone>(made: &Mutex<Vec<(K, V, usize)>>, key: &K) -> Option<V> {
    let mut made = made.lock().ok()?;
    let at = made.iter().position(|(k, _, _)| k == key)?;
    let entry = made.remove(at);
    let value = entry.1.clone();
    made.push(entry);
    Some(value)
}

/// Kept.put with LAYER_BYTES: `value` (of `bytes`) kept as the newest; the least recently used
/// go while more than that is kept.
fn keep<K, V>(made: &Mutex<Vec<(K, V, usize)>>, key: K, value: V, bytes: usize) {
    let Ok(mut made) = made.lock() else {
        return;
    };
    made.push((key, value, bytes));
    let mut total: usize = made.iter().map(|(_, _, n)| n).sum();
    while total > LAYER_BYTES && made.len() > 1 {
        total -= made.remove(0).2;
    }
}

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

/// Rotor._targets: the speed (turns a second), the share of the ring and the light a fan shows
/// for `rpm` (none: no reading).
fn targets(rpm: Option<f64>, max_rpm: f64) -> (f64, f64, f64) {
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
    (speed, ring, light)
}

impl Rotor {
    /// Rotor(key).settle(rpm, max_rpm): the speed's look, standing.
    pub fn settled(key: &str, rpm: Option<f64>, max_rpm: f64) -> Rotor {
        let (speed, ring, light) = targets(rpm, max_rpm);
        Rotor {
            angle: start(key),
            speed,
            ring,
            light,
        }
    }

    /// Whether it turns.
    pub fn turning(&self) -> bool {
        self.speed > 0.0
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

    /// How far its square frame is lit (of SHADES).
    pub fn shades(&self) -> i64 {
        round_int(self.ring * SHADES as f64)
    }
}

/// fan.Beat: where the blades of a sensor's fans stand, for every fan of it drawn at the same
/// pace (two panels side by side show it in the same position): each frame adds up the angle
/// to its planned time; a frame a moment behind another's takes it back by the speed.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Beat {
    /// in blade pitches (0 to 1), at `at`
    angle: f64,
    at: Option<f64>,
}

impl Beat {
    /// Beats.of's new one: where the blades of the sensor's fans stand at first.
    pub fn new(sensor: &str) -> Beat {
        Beat {
            angle: start(sensor),
            at: None,
        }
    }

    /// Beat.begin: where the fan stands for a rotor's first frame (the beat begins then, if it
    /// has not begun).
    fn begin(&mut self, now: f64) -> f64 {
        if self.at.is_none() {
            self.at = Some(now);
        }
        self.angle
    }

    /// Beat.turn: the angle at `now` of a fan that turns `rate` blade pitches a second.
    fn turn(&mut self, now: f64, rate: f64) -> f64 {
        // (the first, or on another clock: more than a fifth of a second before the last)
        let (first, at) = match self.at {
            None => (true, now),
            Some(at) => (at - now > 0.2, at),
        };
        if first {
            self.at = Some(now);
            return self.angle;
        }
        if now > at {
            // (after a pause, as far as a frame of 0.2 s at most, as a rotor)
            let step = py_min(MAX_STEP, rate * py_min(0.2, now - at));
            self.angle = lp_py::num::float_rem(self.angle + step, 1.0);
            self.at = Some(now);
            return self.angle;
        }
        // (a frame drawn for a later time before: back by the speed)
        lp_py::num::float_rem(self.angle - py_min(MAX_STEP, rate * (at - now)), 1.0)
    }
}

/// fan.Rotor in a moving frame: a fan from one frame to the next, as Rotor.advance has it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Turning {
    /// what it shows
    pub rotor: Rotor,
    /// the frame it was drawn for last
    at: Option<f64>,
}

impl Turning {
    /// fan.Rotor(key): standing, where its key puts its blades, as bright as running.
    pub fn new(key: &str) -> Turning {
        let rotor = Rotor {
            angle: start(key),
            speed: 0.0,
            ring: 0.0,
            light: RUNNING,
        };
        Turning { rotor, at: None }
    }

    /// Rotor.advance with its beat: the fan at the frame planned for `now` with `rpm` (none: no
    /// reading), `blades` blades in its look. Its first frame shows it as it is, where the beat
    /// stands; then its speed, ring and light follow with inertia (from the frame before, a
    /// fifth of a second at most), a fan that is to stand runs out, and its blades stand where
    /// the beat has them at its speed.
    pub fn advance(
        &mut self,
        rpm: Option<f64>,
        max_rpm: f64,
        now: f64,
        blades: i64,
        beat: &mut Beat,
    ) {
        let (speed, ring, light) = targets(rpm, max_rpm);
        let r = &mut self.rotor;
        let Some(at) = self.at else {
            (r.speed, r.ring, r.light, self.at) = (speed, ring, light, Some(now));
            r.angle = beat.begin(now);
            return;
        };
        let dt = py_max(0.0, py_min(0.2, now - at));
        self.at = Some(now);
        let follow = 1.0 - (-dt / INERTIA_S).exp();
        r.speed += (speed - r.speed) * follow;
        r.ring += (ring - r.ring) * follow;
        r.light += (light - r.light) * follow;
        if speed == 0.0 {
            // (the exponential alone would creep on for seconds, hardly seen; a fan stops)
            r.speed = py_max(0.0, r.speed - FRICTION * dt);
            if r.speed < 0.02 {
                r.speed = 0.0;
            }
        }
        r.angle = beat.turn(now, r.speed * blades as f64);
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

/// fan.colours: the colours a fan's rotor (or its square `frame`) is painted in: the look's own
/// (`own`: Noctua's brown and beige), else the theme's `colour` with a lighter one of it for the
/// hub, the sticker or the frame's pads.
fn colours(look: &Look, own: bool, colour: [u8; 3], frame: bool) -> ([u8; 3], [u8; 3]) {
    if own {
        return if frame {
            (look.frame_color, look.pad_color)
        } else {
            (look.blade_color, look.hub_color)
        };
    }
    let lighter = colour.map(|v| {
        let v = f64::from(v);
        round_int(v + (255.0 - v) * 0.45).clamp(0, 255) as u8
    });
    (colour, lighter)
}

/// fan.Choices: what the person chose for the fans ([hardware] in config.toml): a look and
/// colours for all, and for some fans by their sensor ("" or none: nothing chosen). A widget's
/// own look and colours come first; "auto" takes these.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Choices {
    pub look: String,
    pub paint: String,
    pub looks: HashMap<String, String>,
    pub paints: HashMap<String, String>,
}

impl Choices {
    /// Choices.for_widget: the look of a fan of `sensor` whose widget names `look` and `paint`
    /// ("auto" or a look not known: the person's for the sensor, else for all, else Noctua's
    /// G2; a paint but "theme" and "own": likewise, else the theme's), and whether it is in the
    /// look's own colours.
    pub fn for_widget(&self, sensor: &str, look: &str, paint: &str) -> (&'static Look, bool) {
        let chosen = |by: &HashMap<String, String>, all: &str, none: &'static str| -> String {
            [by.get(sensor).map(String::as_str).unwrap_or(""), all]
                .into_iter()
                .find(|v| !v.is_empty())
                .unwrap_or(none)
                .to_string()
        };
        let look = if look != "auto" && fan_looks::LOOKS.iter().any(|l| l.name == look) {
            look.to_string()
        } else {
            chosen(&self.looks, &self.look, DEFAULT_LOOK)
        };
        let paint = if paint == "theme" || paint == "own" {
            paint.to_string()
        } else {
            chosen(&self.paints, &self.paint, "theme")
        };
        (fan_looks::look(&look), paint == "own")
    }
}

/// The frame round a fan (its widget's frame): the ring that shows the load, the lit square
/// frame, or none.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Frame {
    Ring,
    Square,
    Bare,
}

impl Frame {
    /// The frame a widget names (any but "ring" and "square": none).
    pub fn named(name: &str) -> Frame {
        match name {
            "ring" => Frame::Ring,
            "square" => Frame::Square,
            _ => Frame::Bare,
        }
    }
}

/// The rotor's grey layers as its pictures have them (fan.py's _plane, and what _reduced makes
/// of them before it averages them down): for each position its shape, core and gloss times
/// its alpha, and its alpha, each `side` x `side`.
struct Pictures {
    side: usize,
    planes: Vec<[Vec<f32>; 4]>,
}

/// The layers made of each look at each size asked for lately, the most recently used last,
/// with their bytes.
type MadeLayers = Mutex<Vec<((PathBuf, &'static str, usize), Arc<Layers>, usize)>>;
/// The discs made lately (a look's rotor of a size, in a pair of colours), the newest last.
type MadeDiscs = Mutex<
    Vec<(
        (PathBuf, &'static str, usize, [u8; 3], [u8; 3]),
        Arc<Vec<u8>>,
    )>,
>;
/// The square frames' layers made lately (the Blender frame's, "", or a drawn look's, at a
/// size), the most recently used last, with their bytes.
type MadeFrames = Mutex<Vec<((PathBuf, &'static str, usize), Arc<Vec<f32>>, usize)>>;

/// fan.py's IMAGE_BYTES: what its pictures kept may hold together.
const IMAGE_BYTES: usize = 6 << 20;

/// What fan.py keeps a picture by (fan._kept): a ring by how far it is lit, a square frame by
/// its brightness, a rotor by its position, the blades by their position, blur and light.
#[derive(Clone, PartialEq, Eq, Hash)]
enum Pictured {
    Ring([u8; 3], i64, i64, usize),
    Frame(PathBuf, &'static str, bool, [u8; 3], usize, i64),
    Rotor(PathBuf, &'static str, [u8; 3], [u8; 3], usize, usize),
    Blades(
        PathBuf,
        &'static str,
        [u8; 3],
        [u8; 3],
        usize,
        usize,
        i64,
        i64,
    ),
}

/// fan._kept: the picture of `key`, made by `make` where it is not kept (the most recently used
/// kept within IMAGE_BYTES: what goes is only made again, the pixels the same).
fn kept_picture(key: Pictured, make: impl FnOnce() -> Result<Image>) -> Result<Arc<Image>> {
    static IMAGES: OnceLock<Mutex<crate::kept::Kept<Pictured, Arc<Image>>>> = OnceLock::new();
    let images = IMAGES.get_or_init(|| Mutex::new(crate::kept::Kept::new(IMAGE_BYTES)));
    if let Some(found) = images.lock().ok().and_then(|mut kept| kept.get(&key)) {
        return Ok(found);
    }
    let made = Arc::new(make()?);
    if let Ok(mut kept) = images.lock() {
        kept.put(key, Arc::clone(&made), made.as_bytes().len());
    }
    Ok(made)
}

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

/// The rotor's pictures, read for the layers of a size as fan.rotor_layers reads them (not
/// kept: the layers made of them are).
fn pictures(dir: &Path) -> Result<Pictures> {
    let mut side = 0;
    let mut planes = Vec::with_capacity(POSITIONS);
    for p in 0..POSITIONS {
        let (s, layers) = picture_planes(dir, &format!("rotor_{{}}_{p:02}.png"))?;
        if side != 0 && s != side {
            return Err(Failed("the fan's pictures of sizes apart".into()));
        }
        side = s;
        planes.push(layers);
    }
    Ok(Pictures { side, planes })
}

/// One set of pictures (`name` with "shape", "core" and "gloss" for its {}) as _reduced takes
/// them before it averages them down: their side, and their shape, core and gloss times alpha
/// (the core from its darkest grey up, from 0 to 1), and alpha (the shape's).
fn picture_planes(dir: &Path, name: &str) -> Result<(usize, [Vec<f32>; 4])> {
    let floor = CORE_FLOOR as f32;
    let span = (1.0 - CORE_FLOOR) as f32;
    let (s, shape, alpha) = plane(dir, &name.replace("{}", "shape"))?;
    let (s1, core, _) = plane(dir, &name.replace("{}", "core"))?;
    let (s2, gloss, _) = plane(dir, &name.replace("{}", "gloss"))?;
    if s1 != s || s2 != s {
        return Err(Failed("the fan's pictures of sizes apart".into()));
    }
    let core: Vec<f32> = core
        .iter()
        .zip(&alpha)
        .map(|(&c, &a)| ((c - floor) / span).clamp(0.0, 1.0) * a)
        .collect();
    let shape: Vec<f32> = shape.iter().zip(&alpha).map(|(&s, &a)| s * a).collect();
    let gloss: Vec<f32> = gloss.iter().zip(&alpha).map(|(&g, &a)| g * a).collect();
    Ok((s, [shape, core, gloss, alpha]))
}

/// _reduced's layers of one set of pictures at `k` px: each averaged down from their `side`
/// (Image.resize of mode F with the box filter: edges do not darken), then the part where a
/// second colour goes (none: the Blender layers are of one colour).
fn reduced(side: usize, layers: &[Vec<f32>; 4], k: usize, planes: &mut Vec<f32>) -> Result<()> {
    for layer in layers {
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
    planes.extend(std::iter::repeat_n(0.0f32, k * k));
    Ok(())
}

/// fan.rotor_layers of `look` at `k` px: the Blender rotor's, each layer averaged down from the
/// pictures' size, or the look's own drawn at each position (fan_looks.rotor, the positions
/// drawn side by side on the processor's cores: each is drawn alone, the same numbers in any
/// order); kept as fan.py keeps them (LAYER_BYTES).
pub fn layers(dir: &Path, look: &'static Look, k: usize) -> Result<Arc<Layers>> {
    static MADE: OnceLock<MadeLayers> = OnceLock::new();
    let made = MADE.get_or_init(|| Mutex::new(Vec::new()));
    let key = (dir.to_path_buf(), look.name, k);
    if let Some(found) = kept(made, &key) {
        return Ok(found);
    }
    if k == 0 || k > MOST_SIDE {
        return Err(Failed(format!("a rotor of {k} px")));
    }
    let mut planes = Vec::with_capacity(POSITIONS * fan_looks::PLANES * k * k);
    if look.drawn {
        let angles = fan_looks::angles(look, POSITIONS);
        let cores = std::thread::available_parallelism().map_or(1, |n| n.get());
        let share = angles.len().div_ceil(cores.clamp(1, 8));
        let drawn = std::thread::scope(|scope| {
            let workers: Vec<_> = angles
                .chunks(share)
                .map(|some| {
                    scope.spawn(move || {
                        some.iter()
                            .map(|&angle| fan_looks::rotor(look, k, angle))
                            .collect::<Vec<_>>()
                    })
                })
                .collect();
            workers
                .into_iter()
                .map(|worker| worker.join())
                .collect::<std::result::Result<Vec<_>, _>>()
        })
        .map_err(|_| Failed(format!("the rotor of {} failed", look.name)))?;
        for position in drawn.into_iter().flatten() {
            planes.extend(position);
        }
    } else {
        let pictures = pictures(dir)?;
        for position in &pictures.planes {
            reduced(pictures.side, position, k, &mut planes)?;
        }
    }
    let bytes = planes.len() * 4;
    let layers = Arc::new(Layers { k, planes });
    keep(made, key, layers.clone(), bytes);
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
        let size = fan_looks::PLANES * self.k * self.k;
        let base = (phase % POSITIONS) * size;
        coloured(&self.planes[base..base + size], colour, second)
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

/// fan._coloured of one set of grey layers (shape, core and gloss times alpha, alpha, and the
/// part where the second colour goes times alpha): RGBA in `colour`, and in `second` where its
/// fifth layer says so (where the two differ).
fn coloured(planes: &[f32], colour: [u8; 3], second: [u8; 3]) -> Vec<u8> {
    let kk = planes.len() / fan_looks::PLANES;
    let plane = |i: usize| &planes[i * kk..(i + 1) * kk];
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

/// fan.disc_image of the rotor's `layers` (of `look`, read from `dir`) in its colours, kept for
/// the last few asked for (as fan.py keeps its pictures: the fans of a list share theirs).
fn disc(
    dir: &Path,
    look: &'static Look,
    layers: &Layers,
    colour: [u8; 3],
    second: [u8; 3],
) -> Arc<Vec<u8>> {
    static MADE: OnceLock<MadeDiscs> = OnceLock::new();
    let made = MADE.get_or_init(|| Mutex::new(Vec::new()));
    let key = (dir.to_path_buf(), look.name, layers.side(), colour, second);
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

/// fan.blades_image: the rotor (of `look`) at `phase` in its colours, blurred into its disc by
/// `blur` and dimmed by `dim` (steps of SHADES).
fn blades(
    dir: &Path,
    look: &'static Look,
    layers: &Layers,
    colours: ([u8; 3], [u8; 3]),
    phase: usize,
    blur: i64,
    dim: i64,
) -> Result<Arc<Image>> {
    let (colour, second) = colours;
    let (k, phase) = (layers.side(), phase % POSITIONS);
    let failed = |e: lp_pixels::Error| Failed(e.to_string());
    // (fan.rotor_image: the rotor at its position, kept by it)
    let rotor = || {
        let key = Pictured::Rotor(dir.to_path_buf(), look.name, colour, second, k, phase);
        kept_picture(key, || {
            Image::from_bytes(Mode::RGBA, k, k, layers.coloured(phase, colour, second))
                .map_err(failed)
        })
    };
    if blur == 0 && dim == 0 {
        return rotor();
    }
    let key = Pictured::Blades(
        dir.to_path_buf(),
        look.name,
        colour,
        second,
        k,
        phase,
        blur,
        dim,
    );
    kept_picture(key, || {
        let image = (*rotor()?).clone();
        blurred_and_dimmed(dir, look, layers, colours, image, blur, dim)
    })
}

/// fan._blades past the rotor's picture: `image` blended towards the rotor's disc by `blur`
/// and its opacity dimmed by `dim`.
fn blurred_and_dimmed(
    dir: &Path,
    look: &'static Look,
    layers: &Layers,
    (colour, second): ([u8; 3], [u8; 3]),
    mut image: Image,
    blur: i64,
    dim: i64,
) -> Result<Image> {
    let k = layers.side();
    let failed = |e: lp_pixels::Error| Failed(e.to_string());
    if blur != 0 {
        let disc = disc(dir, look, layers, colour, second);
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
fn ring(colour: [u8; 3], size: i64, steps: i64, canvas: usize) -> Result<Arc<Image>> {
    kept_picture(Pictured::Ring(colour, size, steps, canvas), || {
        ring_made(colour, size, steps, canvas)
    })
}

fn ring_made(colour: [u8; 3], size: i64, steps: i64, canvas: usize) -> Result<Image> {
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

/// fan.frame_layers: the square frame's layers at `size` px, like a rotor's: the Blender
/// frame's (its pictures beside the rotor's, averaged down), or the look's own drawn one (a
/// drawn look's, or any look's in its own colours: `own`); kept as fan.py keeps them
/// (LAYER_BYTES).
fn frame_layers(dir: &Path, look: &'static Look, own: bool, size: usize) -> Result<Arc<Vec<f32>>> {
    static MADE: OnceLock<MadeFrames> = OnceLock::new();
    let made = MADE.get_or_init(|| Mutex::new(Vec::new()));
    let drawn = look.drawn || own;
    let key = (dir.to_path_buf(), if drawn { look.name } else { "" }, size);
    if let Some(found) = kept(made, &key) {
        return Ok(found);
    }
    if size == 0 || size > MOST_SIDE {
        return Err(Failed(format!("a fan's frame of {size} px")));
    }
    let layers = if drawn {
        fan_looks::frame(look, size)
    } else {
        let (side, planes) = picture_planes(dir, "frame_{}.png")?;
        let mut layers = Vec::with_capacity(fan_looks::PLANES * size * size);
        reduced(side, &planes, size, &mut layers)?;
        layers
    };
    let bytes = layers.len() * 4;
    let layers = Arc::new(layers);
    keep(made, key, layers.clone(), bytes);
    Ok(layers)
}

/// fan.frame_image: the lit square frame of a fan of `size` px, brighter the faster it runs
/// (`steps` of SHADES): its gloss and its colours (rounded as Python rounds) times that.
fn frame_image(
    dir: &Path,
    colour: [u8; 3],
    size: usize,
    steps: i64,
    look: &'static Look,
    own: bool,
) -> Result<Arc<Image>> {
    let key = Pictured::Frame(dir.to_path_buf(), look.name, own, colour, size, steps);
    kept_picture(key, || frame_made(dir, colour, size, steps, look, own))
}

fn frame_made(
    dir: &Path,
    colour: [u8; 3],
    size: usize,
    steps: i64,
    look: &'static Look,
    own: bool,
) -> Result<Image> {
    // (the idle frame is dark, a fast one as bright as the blades)
    let bright = 0.58 + 0.42 * steps as f64 / SHADES as f64;
    let mut layers = frame_layers(dir, look, own, size)?.to_vec();
    let kk = size * size;
    // (the gloss follows the brightness: float32 times it, as numpy takes a Python float)
    let times = bright as f32;
    for gloss in &mut layers[2 * kk..3 * kk] {
        *gloss *= times;
    }
    let (main, second) = colours(look, own, colour, true);
    let scale = |c: [u8; 3]| c.map(|v| round_int(f64::from(v) * bright).clamp(0, 255) as u8);
    let rgba = coloured(&layers, scale(main), scale(second));
    Image::from_bytes(Mode::RGBA, size, size, rgba).map_err(|e| Failed(e.to_string()))
}

/// A fan's picture as the renderer lays it from fan.fan_parts (_laid_fan): its frame (the ring
/// as it is, or the square in the middle, or none) and its rotor of `look` over it, in the
/// theme's `colour` or the look's own (`own`); `size` px with the room its glow takes round it.
pub fn picture(
    dir: &Path,
    colour: [u8; 3],
    size: i64,
    rotor: &Rotor,
    frame: Frame,
    look: &'static Look,
    own: bool,
) -> Result<Image> {
    let failed = |e: lp_pixels::Error| Failed(e.to_string());
    let (size, _margin, canvas) = geometry(size, size);
    let k = rotor_size(size);
    let (side, canvas_px) = (k as usize, canvas as usize);
    let mut laid = match frame {
        Frame::Ring => (*ring(colour, size, rotor.steps(), canvas_px)?).clone(),
        Frame::Square => {
            let square = frame_image(dir, colour, size as usize, rotor.shades(), look, own)?;
            let mut laid = Image::blank(Mode::RGBA, canvas_px, canvas_px);
            let at = (canvas - size).div_euclid(2);
            laid.alpha_composite_at(&square, (at, at), (0, 0, size, size))
                .map_err(failed)?;
            laid
        }
        Frame::Bare => Image::blank(Mode::RGBA, canvas_px, canvas_px),
    };
    let layers = layers(dir, look, side)?;
    let rotor_picture = blades(
        dir,
        look,
        &layers,
        colours(look, own, colour, false),
        rotor.phase(),
        rotor.blur(),
        rotor.dim(),
    )?;
    let at = (canvas - k).div_euclid(2);
    laid.alpha_composite_at(&rotor_picture, (at, at), (0, 0, k, k))
        .map_err(failed)?;
    Ok(laid)
}

impl lp_devices::scene::Planes for Layers {
    fn numbers(&self) -> &[f32] {
        &self.planes
    }
}

/// Whether a fan widget's picture may be laid by an engine of the pictures it makes itself:
/// drawn as it is (no glow or shadow round it, no fade, cut sides or turn over it, which need
/// its pixels here).
pub fn native(w: &lp_py::Dict) -> bool {
    use crate::widget;
    widget::number(w, "glow", 0.0) == 0.0
        && widget::optional(w, "shadow").is_none_or(str::is_empty)
        && !widget::truthy(w, "clip")
        && !widget::truthy(w, "rotate")
        && widget::number(w, "opacity", 1.0) >= 0.999
}

/// fan.ring_recipe: what an engine makes a fan's ring of, as ring_image makes it (_ring_mask's
/// box and width as Pillow takes them: the box in whole pixels).
fn ring_recipe(colour: [u8; 3], size: i64, steps: i64, canvas: usize) -> FanRing {
    const LARGER: f64 = 3.0;
    let scale = size as f64 / FAN_SIZE;
    let (centre, stroke) = (RING_CENTRE * scale, py_max(1.5, RING_STROKE * scale));
    let g = canvas as f64 * LARGER;
    let r = (centre + stroke / 2.0) * LARGER;
    let (low, high) = ((g / 2.0 - r) as i32, (g / 2.0 + r) as i32);
    let share = steps as f64 / RING_STEPS;
    let arc = if steps <= 0 {
        0
    } else if share >= 0.995 {
        1
    } else {
        2
    };
    let angles = if arc == 2 {
        (-90.0, (-90.0 + 360.0 * share) as f32)
    } else {
        (0.0, 0.0)
    };
    FanRing {
        canvas,
        colour,
        arc,
        bounds: [low, low, high, high],
        width: round_int(stroke * LARGER).max(1) as i32,
        angles,
        halo: (RING_HALO * scale) as f32,
        groove: GROOVE as f32,
        white: GLOW_WHITE as f32,
        share: GLOW_SHARE as f32,
    }
}

/// What a picture an engine makes of a fan shows (fan.py's _made keys): a ring by its colour,
/// size, how far it is lit and its canvas; a rotor by its look, colours, size, position, blur
/// and dimming.
#[derive(Clone, PartialEq, Eq, Hash)]
enum MadeKey {
    Ring([u8; 3], i64, i64, usize),
    Blades(&'static str, [u8; 3], [u8; 3], usize, usize, i64, i64),
}

/// The pictures of fans an engine makes itself, by what they show (fan.py's _made): so a fan's
/// ring that stays lit as it is, and a rotor at a position, are the same picture from frame to
/// frame, which the engine keeps and does not make again while frames show it; at most
/// MADE_KEPT (then they all go: the next are made anew). The rotors they are of, one a look
/// and size.
const MADE_KEPT: usize = 512;
type MadeKept = std::cell::RefCell<HashMap<MadeKey, Rc<MadePicture>>>;
type RotorsKept = std::cell::RefCell<HashMap<(&'static str, usize), Rc<RotorPlanes>>>;
type RecentKept = std::cell::RefCell<HashMap<String, Vec<Rc<MadePicture>>>>;
thread_local! {
    static MADE: MadeKept = std::cell::RefCell::new(HashMap::new());
    static ROTORS: RotorsKept = std::cell::RefCell::new(HashMap::new());
    static RECENT: RecentKept = std::cell::RefCell::new(HashMap::new());
}

/// The pictures of a fan's rotor an engine made last that it keeps with the fan's layer
/// (renderer.KEPT_ROTORS: a rotor turning steadily shows 48 of them), so a position the rotor
/// comes back to is laid there, not made again; and the most fans they are kept for (then they
/// all go, kept anew).
const KEPT_ROTORS: usize = 64;
const KEPT_FANS: usize = 64;

/// The fan `key`'s rotor at its position now, `blades`, kept as the newest of those it showed:
/// the others it showed last (renderer._rotor_kept).
fn kept_with(key: &str, blades: &Rc<MadePicture>) -> Vec<Rc<MadePicture>> {
    RECENT.with(|recent| {
        let mut recent = recent.borrow_mut();
        if !recent.contains_key(key) && recent.len() >= KEPT_FANS {
            recent.clear();
        }
        let kept = recent.entry(key.to_string()).or_default();
        kept.retain(|had| !Rc::ptr_eq(had, blades));
        kept.push(Rc::clone(blades));
        if kept.len() > KEPT_ROTORS {
            let gone = kept.len() - KEPT_ROTORS;
            kept.drain(..gone);
        }
        kept[..kept.len() - 1].to_vec()
    })
}

/// What a fan is laid of where an engine makes its pictures itself (fan.made_parts): its
/// `parts` on a canvas of `side` px, and the pictures of its rotor the engine keeps besides.
pub struct Made {
    pub parts: Vec<(Rc<MadePicture>, i64, i64)>,
    pub kept: Vec<Rc<MadePicture>>,
    pub side: usize,
}

/// The picture an engine makes of a fan that shows `key`, made of `make` where none is kept.
fn made_picture(
    key: MadeKey,
    make: impl FnOnce() -> Result<MadePicture>,
) -> Result<Rc<MadePicture>> {
    if let Some(found) = MADE.with(|kept| kept.borrow().get(&key).cloned()) {
        return Ok(found);
    }
    let made = Rc::new(make()?);
    MADE.with(|kept| {
        let mut kept = kept.borrow_mut();
        if kept.len() >= MADE_KEPT {
            kept.clear();
        }
        kept.insert(key, Rc::clone(&made));
    });
    Ok(made)
}

/// The rotor of `look` at `k` px as an engine takes it (fan.rotor_layers, fan.SHADING), one
/// kept a look and size.
fn rotor_planes(dir: &Path, look: &'static Look, k: usize) -> Result<Rc<RotorPlanes>> {
    if let Some(found) = ROTORS.with(|kept| kept.borrow().get(&(look.name, k)).cloned()) {
        return Ok(found);
    }
    let planes = Rc::new(RotorPlanes {
        look: look.name.to_string(),
        k,
        positions: POSITIONS,
        layers: fan_looks::PLANES,
        shading: [AMBIENT, DIFFUSE, 1.0 - CORE, CORE, GLOSS].map(|v| v as f32),
        planes: layers(dir, look, k)?,
    });
    ROTORS.with(|kept| {
        let mut kept = kept.borrow_mut();
        // (as many as an engine keeps for a stream: a theme's fans are of a few sizes)
        if kept.len() >= lp_devices::scene::MOST_ROTORS {
            kept.clear();
        }
        kept.insert((look.name, k), Rc::clone(&planes));
    });
    Ok(planes)
}

/// fan.made_parts: the pictures a fan of `size` px is laid from as an engine makes them itself
/// (its ring where it has one, from its corner; its rotor at its position, blurred and dimmed,
/// in the middle), the same as those [`picture`] lays; None for a fan whose frame is not one
/// an engine makes (a square one). With the canvas they are laid on and the positions of the
/// fan `key`'s rotor the engine made last.
#[allow(clippy::too_many_arguments)]
pub fn made_parts(
    dir: &Path,
    colour: [u8; 3],
    size: i64,
    rotor: &Rotor,
    frame: Frame,
    look: &'static Look,
    own: bool,
    key: &str,
) -> Result<Option<Made>> {
    if frame == Frame::Square {
        return Ok(None);
    }
    let (size, _margin, canvas) = geometry(size, size);
    let k = rotor_size(size);
    let canvas_px = canvas as usize;
    let mut parts = Vec::with_capacity(2);
    if frame == Frame::Ring {
        let steps = rotor.steps();
        let shows = MadeKey::Ring(colour, size, steps, canvas_px);
        let ring = made_picture(shows, || {
            Ok(MadePicture {
                side: canvas_px,
                recipe: Recipe::Ring(ring_recipe(colour, size, steps, canvas_px)),
            })
        })?;
        parts.push((ring, 0, 0));
    }
    let side = k as usize;
    let (main, second) = colours(look, own, colour, false);
    let (phase, blur, dim) = (rotor.phase() % POSITIONS, rotor.blur(), rotor.dim());
    let shows = MadeKey::Blades(look.name, main, second, side, phase, blur, dim);
    let blades = made_picture(shows, || {
        // (Image.blend's alpha, in single precision; _DIMMED's table, rounded as Python
        // rounds)
        let blend = if blur != 0 {
            (BLUR * blur as f64 / SHADES as f64) as f32
        } else {
            0.0
        };
        let dim = (dim != 0).then(|| {
            let keep = 1.0 - dim as f64 / SHADES as f64;
            (0..256)
                .map(|v| round_int(f64::from(v) * keep).clamp(0, 255) as u8)
                .collect()
        });
        Ok(MadePicture {
            side,
            recipe: Recipe::Blades {
                rotor: rotor_planes(dir, look, side)?,
                phase,
                colour: main,
                second,
                blend,
                dim,
            },
        })
    })?;
    let kept = kept_with(key, &blades);
    let at = (canvas - k).div_euclid(2);
    parts.push((blades, at, at));
    Ok(Some(Made {
        parts,
        kept,
        side: canvas_px,
    }))
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
        let g2 = fan_looks::look(DEFAULT_LOOK);
        assert_eq!(
            colours(g2, false, [0, 100, 255], false),
            ([0, 100, 255], [115, 170, 255])
        );
        // (the look's own: Noctua's brown blades in a beige frame with brown pads)
        assert_eq!(
            colours(g2, true, [0, 100, 255], false),
            ([104, 70, 54], [104, 70, 54])
        );
        assert_eq!(
            colours(g2, true, [0, 100, 255], true),
            ([212, 192, 164], [104, 70, 54])
        );
    }
}
