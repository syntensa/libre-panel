//! More looks for the panel's own screens (render/bootlooks.py; render/boot.rs has what they
//! share), each drawn for its room whatever its shape; their clips move only in brightness at
//! fixed places, each slow change coming round a whole number of times a loop. So far:
//!
//! - Pulse: a heartbeat across the panel on a monitor's grid (more under each other on a bar on
//!   end); in the clip a soft light sweeps along it and leaves a trail.
//! - Circuit: traces that run in from both sides to a chip with the app's mark; in the clip
//!   light runs along them into the chip.
//! - Levels: an equaliser's bars from a line in the middle, teal to violet; in the clip they
//!   rise and fall.
//! - Dark: black, with the app's mark faintly in the middle; in the clip it breathes.
//!
//! In bootlooks.py's own numbers, as boot.rs has them; numpy's default_rng for the chances of
//! a room (its uniforms and its integers by Lemire's method), numpy's own float32 exp, the C
//! library's sin, cos and exp of doubles, Python 3.12's sum of floats.

use std::f64::consts::PI;
use std::hint::black_box;

use lp_pixels::random::Generator;
use lp_pixels::{Draw, FloatImage, Image, ALL_CORNERS};

use crate::boot::{
    blur, clip, coverage, failed, floats, glow, linspace, mark_into, maximum, paint, py_max,
    py_min, remainder_f32, remainder_f64, shine, tinted, vertical, weak, Canvas, Room, SS, WHITE,
};
use crate::branding::libre;
use crate::np;
use crate::piece::{Failed, Result};

const TAU: f64 = 2.0 * PI;

/// _rng: the same chance for the same room: a look is drawn alike every time.
fn chances(room: &Room, salt: usize) -> Generator {
    Generator::new((room.w * 100_003 + room.h * 101 + salt) as u32)
}

/// _long: the room along its long side and across it: (start, end) along, (start, end) across,
/// in picture coordinates of the long axis (x on a wide panel, y on a tall one).
fn long(room: &Room) -> (f64, f64, f64, f64) {
    if room.w >= room.h {
        (room.x0, room.x1, room.y0, room.y1)
    } else {
        (room.y0, room.y1, room.x0, room.x1)
    }
}

/// An integer of numpy's for a room's chances (an empty range does not come here).
fn integer(rng: &mut Generator, low: i64, high: i64) -> Result<i64> {
    rng.integers(low, high)
        .ok_or_else(|| Failed(format!("no integers from {low} to {high}")))
}

/// A float picture of mode F, every pixel `value`.
fn plane(w: usize, h: usize, value: f32) -> FloatImage {
    FloatImage {
        width: w,
        height: h,
        data: vec![value; w * h],
    }
}

fn plane_failed(e: lp_pixels::Error) -> Failed {
    failed(e)
}

// -- Pulse ------------------------------------------------------------------------------------

const PULSE: [u8; 3] = [70, 255, 160];
const PULSE_GRID: [u8; 3] = [14, 30, 26];
const PULSE_SKY: ([u8; 3], [u8; 3]) = ([6, 12, 12], [3, 7, 8]);

/// _beat: a heartbeat's trace over one beat (t 0 to 1), -1 to 1: flat, a small P, the sharp
/// QRS, the round T.
fn beat(t: f64) -> f64 {
    let bump = |at: f64, width: f64| {
        let z = (t - at) / width;
        (-(z * z)).exp()
    };
    let p = 0.10 * bump(0.18, 0.035);
    let q = -0.12 * bump(0.30, 0.010);
    let r = 1.0 * bump(0.33, 0.012);
    let s = -0.30 * bump(0.36, 0.012);
    let tw = 0.22 * bump(0.58, 0.06);
    p + q + r + s + tw
}

/// Pulse: a heartbeat across the panel (see the module's text).
pub struct Pulse {
    room: Room,
    base: Canvas,
    cover: Canvas,
    along: Vec<f32>,
}

impl Pulse {
    pub fn new(room: &Room) -> Result<Pulse> {
        let (w, h) = (room.w, room.h);
        let mut base = vertical(h, w, floats(PULSE_SKY.0), floats(PULSE_SKY.1));
        let u = room.unit();
        // the monitor's grid, every fifth line stronger
        let step = 8.max(lp_py::round(24.0 * u) as i64) as usize;
        let first = (lp_py::round(room.cy()) as i64).rem_euclid(step as i64) as usize;
        let grid = coverage((w, h), 2, |image, s| {
            let mut draw = Draw::new(image);
            let width = 1.max(lp_py::round(s * 0.8) as i32);
            for (k, x) in (0..w).step_by(step).enumerate() {
                let ink = [if k % 5 == 0 { 255 } else { 110 }, 0, 0, 0];
                let x = x as f64 * s;
                draw.line(&[(x, 0.0), (x, h as f64 * s)], Some(ink), width, false)
                    .map_err(failed)?;
            }
            for (k, y) in (first..h).step_by(step).enumerate() {
                let ink = [if k % 5 == 0 { 255 } else { 110 }, 0, 0, 0];
                let y = y as f64 * s;
                draw.line(&[(0.0, y), (w as f64 * s, y)], Some(ink), width, false)
                    .map_err(failed)?;
            }
            Ok(())
        })?;
        paint(&mut base, &grid, floats(PULSE_GRID), 0.8);
        // the traces across, as a monitor shows them: one through the middle of a broad
        // panel, more under each other on a tall one (a bar on end), each its own beats
        let tall = room.rh() > room.rw() * 1.25;
        let rows = if tall {
            2.max(4.min(lp_py::round(room.rh() / (room.rw() * 0.9)) as i64))
        } else {
            1
        };
        let band = room.rh() / rows as f64;
        let beats =
            (if rows == 1 { 2 } else { 1 }).max(lp_py::round(room.rw() / (band * 1.7)) as i64);
        let n = (room.rw() * 2.0) as i64;
        let xs = linspace(room.x0, room.x1, n.max(0) as usize);
        let along_trace: Vec<f64> = xs.iter().map(|&x| (x - room.x0) / room.rw()).collect();
        let mut lines: Vec<Vec<(f64, f64)>> = Vec::new();
        for row in 0..rows {
            let middle = room.y0 + band * (row as f64 + 0.5);
            let shift = 0.37 * row as f64; // (the rows' beats not one over the other)
            let points = xs
                .iter()
                .zip(&along_trace)
                .map(|(&x, &s)| {
                    let t = remainder_f64(s * beats as f64 + shift, 1.0);
                    (x, middle - band * 0.36 * beat(t))
                })
                .collect();
            lines.push(points);
        }
        let width = py_max(2.0, 3.2 * u);
        let cover = coverage((w, h), SS, |image, s| {
            let mut draw = Draw::new(image);
            let stroke = lp_py::round(width * s) as i32;
            for points in &lines {
                let scaled: Vec<(f64, f64)> = points.iter().map(|&(x, y)| (x * s, y * s)).collect();
                draw.line(&scaled, Some(WHITE), stroke, true)
                    .map_err(failed)?;
            }
            Ok(())
        })?;
        // how far along its trace each lit pixel is (for the light that sweeps along them)
        let mut place = plane(w, h, 0.0);
        {
            let mut draw = Draw::new(&mut place);
            let stroke = 3.max(lp_py::round(width + 4.0) as i32);
            for points in &lines {
                for (pair, &s0) in points.windows(2).zip(&along_trace) {
                    draw.line(&[pair[0], pair[1]], Some(s0 as f32), stroke, false)
                        .map_err(plane_failed)?;
                }
            }
        }
        Ok(Pulse {
            room: *room,
            base,
            cover,
            along: place.data,
        })
    }

    fn drawn(&self, level: impl Fn(usize) -> f32) -> Result<Canvas> {
        let mut out = self.base.clone();
        let mut light = self.cover.clone();
        for (i, v) in light.data.iter_mut().enumerate() {
            *v *= level(i);
        }
        let u = self.room.unit();
        glow(
            &mut out,
            &light,
            Some(floats(PULSE)),
            &[(6.0 * u, 1.2), (22.0 * u, 0.8)],
        )?;
        let lit = tinted(&light, floats(PULSE), 1.0);
        for (o, &v) in out.data.iter_mut().zip(&lit.data) {
            *o = maximum(*o, v);
        }
        Ok(out)
    }

    pub fn still(&self) -> Result<Canvas> {
        self.drawn(|_| 0.9)
    }

    pub fn frame(&self, phase: f64) -> Result<Canvas> {
        let ahead = weak(phase * 1.25);
        let lap = weak(1.25);
        self.drawn(|i| {
            let behind = remainder_f32(ahead - self.along[i], lap); // how far the sweep has gone past
            let trail = np::expf(-behind / weak(0.22)) * if behind < 1.0 { 1.0 } else { 0.0 };
            let z = behind / weak(0.012);
            let head = np::expf(-(z * z));
            weak(0.22) + weak(0.7) * trail + weak(0.3) * head
        })
    }
}

// -- Circuit ----------------------------------------------------------------------------------

const TRACE: [u8; 3] = [40, 200, 190];
const BOARD: ([u8; 3], [u8; 3]) = ([5, 14, 16], [2, 7, 9]);
const COPPER: [u8; 3] = [24, 58, 60];

/// Circuit: traces from both sides into a chip with the app's mark (see the module's text).
pub struct Circuit {
    room: Room,
    base: Canvas,
    cover: Canvas,
    along: Vec<f32>,
    which: Vec<i32>,
    offsets: Vec<f32>,
}

impl Circuit {
    pub fn new(room: &Room) -> Result<Circuit> {
        let (w, h) = (room.w, room.h);
        let wide = w >= h;
        let u = room.unit();
        let mut base = vertical(h, w, floats(BOARD.0), floats(BOARD.1));
        let (a0, a1, b0, b1) = long(room);
        let (ca, cb) = ((a0 + a1) / 2.0, (b0 + b1) / 2.0);
        let chip = py_min(b1 - b0, a1 - a0) * if room.round { 0.34 } else { 0.42 };
        let pitch = py_max(10.0 * py_min(1.0, u * 2.0), chip / 9.0); // (finer on a small one)
        let count = 2.max((chip / pitch) as i64 - 1) as usize;
        let pins: Vec<f64> = (0..count)
            .map(|k| cb - chip / 2.0 + chip * (k + 1) as f64 / (count + 1) as f64)
            .collect();
        let mut rng = chances(room, 3);
        let mut paths: Vec<[(f64, f64); 4]> = Vec::new(); // each in (along, across)
        for side in [-1.0f64, 1.0] {
            let edge = if side < 0.0 { a0 } else { a1 };
            let entry = ca + side * (chip / 2.0 + pitch * 1.2); // where they turn into the chip
            let spread: Vec<f64> = (0..pins.len())
                .map(|k| b0 + (b1 - b0) * (k as f64 + 0.5) / pins.len() as f64)
                .collect();
            for (k, (&pin, &start)) in pins.iter().zip(&spread).enumerate() {
                let dy = start - pin;
                let bend = entry + side * dy.abs(); // the diagonal ends at the entry
                let early = rng.uniform(0.0, (edge - bend).abs() * 0.45);
                let begin = if k % 3 == 1 {
                    edge
                } else {
                    edge + side * -early
                }; // some run from the edge
                paths.push([
                    (begin, start),
                    (bend, start),
                    (entry, pin),
                    (ca + side * chip / 2.0, pin),
                ]);
            }
        }
        let xy = |p: (f64, f64)| if wide { p } else { (p.1, p.0) };
        let width = py_max(1.6, 2.4 * u);
        let dist = |p: (f64, f64), q: (f64, f64)| lp_py::math::hypot(p.0 - q.0, p.1 - q.1);
        let lengths: Vec<f64> = paths
            .iter()
            .map(|path| {
                // (Python's sum, compensated)
                lp_py::sum_floats(path.windows(2).map(|pq| dist(pq[0], pq[1])))
            })
            .collect();
        let cover = coverage((w, h), SS, |image, s| {
            let mut draw = Draw::new(image);
            let stroke = lp_py::round(width * s) as i32;
            for path in &paths {
                let points: Vec<(f64, f64)> =
                    path.iter().map(|&p| (xy(p).0 * s, xy(p).1 * s)).collect();
                draw.line(&points, Some(WHITE), stroke, true)
                    .map_err(failed)?;
                let (x, y) = xy(path[0]);
                let r = width * 1.9;
                let bounds = ((x - r) * s, (y - r) * s, (x + r) * s, (y + r) * s);
                draw.ellipse(bounds, Some(WHITE), None, 1).map_err(failed)?;
            }
            Ok(())
        })?;
        paint(&mut base, &cover, floats(COPPER), 1.0);
        // how far along its trace each pixel is (0 at its start, 1 at the chip), and which
        let mut place = plane(w, h, -1.0);
        let mut which = plane(w, h, -1.0);
        {
            let mut dp = Draw::new(&mut place);
            let stroke = 3.max(lp_py::round(width * 3.2) as i32);
            for (path, &length) in paths.iter().zip(&lengths) {
                let mut done = 0.0;
                for pq in path.windows(2) {
                    let (p, q) = (pq[0], pq[1]);
                    let d = dist(p, q);
                    let pieces = 1.max((d / 2.0) as i64);
                    for i in 0..pieces {
                        let a = (
                            p.0 + (q.0 - p.0) * i as f64 / pieces as f64,
                            p.1 + (q.1 - p.1) * i as f64 / pieces as f64,
                        );
                        let b = (
                            p.0 + (q.0 - p.0) * (i + 1) as f64 / pieces as f64,
                            p.1 + (q.1 - p.1) * (i + 1) as f64 / pieces as f64,
                        );
                        let s = (done + d * i as f64 / pieces as f64) / length;
                        dp.line(&[xy(a), xy(b)], Some(s as f32), stroke, false)
                            .map_err(plane_failed)?;
                    }
                    done += d;
                }
                let (x, y) = xy(path[0]);
                let r = width * 2.6;
                dp.ellipse((x - r, y - r, x + r, y + r), Some(0.0), None, 1)
                    .map_err(plane_failed)?;
            }
        }
        {
            let mut dw = Draw::new(&mut which);
            let stroke = 3.max(lp_py::round(width * 3.2) as i32);
            for (n, path) in paths.iter().enumerate() {
                for pq in path.windows(2) {
                    let (p, q) = (pq[0], pq[1]);
                    let pieces = 1.max((dist(p, q) / 2.0) as i64);
                    for i in 0..pieces {
                        let a = (
                            p.0 + (q.0 - p.0) * i as f64 / pieces as f64,
                            p.1 + (q.1 - p.1) * i as f64 / pieces as f64,
                        );
                        let b = (
                            p.0 + (q.0 - p.0) * (i + 1) as f64 / pieces as f64,
                            p.1 + (q.1 - p.1) * (i + 1) as f64 / pieces as f64,
                        );
                        dw.line(&[xy(a), xy(b)], Some(n as f32), stroke, false)
                            .map_err(plane_failed)?;
                    }
                }
                let (x, y) = xy(path[0]);
                let r = width * 2.6;
                dw.ellipse((x - r, y - r, x + r, y + r), Some(n as f32), None, 1)
                    .map_err(plane_failed)?;
            }
        }
        let offsets: Vec<f32> = (0..paths.len())
            .map(|_| rng.uniform(0.0, 1.0) as f32)
            .collect();
        chip_into(&mut base, room, xy((ca, cb)), chip, u)?;
        Ok(Circuit {
            room: *room,
            base,
            cover,
            along: place.data,
            which: which.data.iter().map(|&v| v as i32).collect(),
            offsets,
        })
    }

    fn drawn(&self, phase: Option<f64>) -> Result<Canvas> {
        let mut out = self.base.clone();
        let mut light = self.cover.clone();
        for (i, v) in light.data.iter_mut().enumerate() {
            let on = self.which[i] >= 0;
            let level = match phase {
                _ if !on => 0.0,
                None => weak(0.55) + weak(0.45) * clip(self.along[i], 0.0, 1.0),
                Some(p) => {
                    let offset = self.offsets[self.which[i].max(0) as usize];
                    let front = remainder_f32(weak(p) + offset, 1.0);
                    let behind = remainder_f32(front * weak(1.4) - self.along[i], weak(1.4));
                    let ran = weak(0.75) * np::expf(-behind / weak(0.12));
                    weak(0.25) + ran * if behind < 1.0 { 1.0 } else { 0.0 }
                }
            };
            *v *= level;
        }
        let u = self.room.unit();
        glow(
            &mut out,
            &light,
            Some(floats(TRACE)),
            &[(4.0 * u, 1.0), (16.0 * u, 0.6)],
        )?;
        let lit = tinted(&light, floats(TRACE), 1.0);
        for (o, &v) in out.data.iter_mut().zip(&lit.data) {
            *o = maximum(*o, v);
        }
        Ok(out)
    }

    pub fn still(&self) -> Result<Canvas> {
        self.drawn(None)
    }

    pub fn frame(&self, phase: f64) -> Result<Canvas> {
        self.drawn(Some(phase))
    }
}

/// Circuit._chip_into: the chip, its rim's light round it, the app's mark on it.
fn chip_into(out: &mut Canvas, room: &Room, (cx, cy): (f64, f64), chip: f64, u: f64) -> Result<()> {
    let (w, h) = (room.w, room.h);
    let half = chip / 2.0;
    let radius = chip * 0.12;
    let bounds = |s: f64| {
        (
            (cx - half) * s,
            (cy - half) * s,
            (cx + half) * s,
            (cy + half) * s,
        )
    };
    let body = |image: &mut Image, s: f64| {
        Draw::new(image)
            .rounded_rectangle(bounds(s), radius * s, Some(WHITE), None, 1, ALL_CORNERS)
            .map_err(failed)
    };
    let rim = |image: &mut Image, s: f64| {
        let width = lp_py::round(py_max(1.5, 2.4 * u) * s) as i32;
        Draw::new(image)
            .rounded_rectangle(bounds(s), radius * s, None, Some(WHITE), width, ALL_CORNERS)
            .map_err(failed)
    };
    let halo = blur(
        &tinted(&coverage((w, h), 2, rim)?, floats(TRACE), 0.5),
        14.0 * u,
    )?;
    shine(out, &halo, (0, 0));
    paint(out, &coverage((w, h), SS, body)?, floats([9, 20, 23]), 1.0);
    paint(out, &coverage((w, h), SS, rim)?, floats([34, 92, 92]), 1.0);
    mark_into(out, (cx, cy), chip * 0.55, 1.0, 0.06)
}

// -- Levels -----------------------------------------------------------------------------------

/// A cell of the bars: its bar, its step from the middle and its box (along, across).
type Cell = (usize, usize, (f64, f64, f64, f64));

const LEVELS_LOW: [u8; 3] = [46, 230, 214];
const LEVELS_HIGH: [u8; 3] = [180, 90, 255];
/// How far each of the three swings moves the bars.
const DEPTH: [f32; 3] = [0.30, 0.18, 0.10];

/// Levels: an equaliser's bars from a line in the middle (see the module's text).
pub struct Levels {
    room: Room,
    base: Canvas,
    cover: Canvas,
    bar_of: Vec<i32>,
    step_of: Vec<f32>,
    colour: Vec<f32>,
    count: usize,
    segments: usize,
    shape: Vec<f32>,
    beats: Vec<i64>,
    offsets: Vec<f64>,
}

impl Levels {
    pub fn new(room: &Room) -> Result<Levels> {
        let (w, h) = (room.w, room.h);
        let u = room.unit();
        let mut base = vertical(h, w, floats(libre("bg")), floats(libre("bg2")));
        let (mut a0, mut a1, b0, b1) = long(room);
        if room.round {
            // within the circle
            (a0, a1) = (room.cx() - room.rw() * 0.36, room.cx() + room.rw() * 0.36);
        }
        let wide = w >= h;
        let span = a1 - a0;
        let count = 12.max((span / (22.0 * u)) as i64) as usize;
        let half = (b1 - b0) * 0.42; // the longest bar's reach from the middle
        let segments = 6.max((half / (11.0 * u)) as i64) as usize;
        let pitch = span / count as f64;
        let bars: Vec<(f64, f64)> = (0..count)
            .map(|k| {
                (
                    a0 + pitch * (k as f64 + 0.16),
                    a0 + pitch * (k as f64 + 0.84),
                )
            })
            .collect();
        let middle = (b0 + b1) / 2.0;
        let seg = half / segments as f64;
        let mut rng = chances(room, 5);
        // every segment's box (teal near the middle, violet at the ends)
        let mut boxes: Vec<Cell> = Vec::new();
        for (k, &(p, q)) in bars.iter().enumerate() {
            for i in 0..segments {
                for side in [-1.0f64, 1.0] {
                    let near = middle + side * (seg * i as f64 + seg * 0.18);
                    let far = middle + side * (seg * (i + 1) as f64 - seg * 0.18);
                    boxes.push((k, i, (p, py_min(near, far), q, py_max(near, far))));
                }
            }
        }
        let flip = |(p, lo, q, hi): (f64, f64, f64, f64)| {
            if wide {
                (p, lo, q, hi)
            } else {
                (lo, p, hi, q)
            }
        };
        let rounding = py_max(1.0, 1.6 * u);
        let cover = coverage((w, h), SS, |image, s| {
            let mut draw = Draw::new(image);
            for &(_, _, cell) in &boxes {
                let (x0, y0, x1, y1) = flip(cell);
                draw.rounded_rectangle(
                    (x0 * s, y0 * s, x1 * s, y1 * s),
                    rounding * s,
                    Some(WHITE),
                    None,
                    1,
                    ALL_CORNERS,
                )
                .map_err(failed)?;
            }
            Ok(())
        })?;
        let mut label = plane(w, h, -1.0);
        let mut step = plane(w, h, 0.0);
        {
            let mut dl = Draw::new(&mut label);
            for &(k, _, cell) in &boxes {
                let (x0, y0, x1, y1) = flip(cell);
                dl.rectangle(
                    (x0 - 1.0, y0 - 1.0, x1 + 1.0, y1 + 1.0),
                    Some(k as f32),
                    None,
                    1,
                )
                .map_err(plane_failed)?;
            }
        }
        {
            let mut ds = Draw::new(&mut step);
            for &(_, i, cell) in &boxes {
                let (x0, y0, x1, y1) = flip(cell);
                let ink = ((i as f64 + 0.5) / segments as f64) as f32;
                ds.rectangle((x0 - 1.0, y0 - 1.0, x1 + 1.0, y1 + 1.0), Some(ink), None, 1)
                    .map_err(plane_failed)?;
            }
        }
        let bar_of: Vec<i32> = label.data.iter().map(|&v| v as i32).collect();
        let step_of = step.data;
        let (low, high) = (floats(LEVELS_LOW), floats(LEVELS_HIGH));
        let mut colour = Vec::with_capacity(w * h * 3);
        for &s in &step_of {
            let mix = clip(s, 0.0, 1.0).powf(black_box(weak(1.2)));
            colour.extend([0, 1, 2].map(|k| (1.0 - mix) * low[k] + mix * high[k]));
        }
        paint(&mut base, &cover, floats([16, 22, 30]), 1.0); // the cells unlit
        let shape: Vec<f32> = linspace(-1.0, 1.0, count)
            .iter()
            .map(|&x| {
                let z = x / 0.75;
                (0.35 + 0.55 * (-(z * z)).exp()) as f32
            })
            .collect();
        let beats = (0..3 * count)
            .map(|_| integer(&mut rng, 1, 4))
            .collect::<Result<Vec<_>>>()?;
        let offsets = (0..3 * count).map(|_| rng.uniform(0.0, TAU)).collect();
        Ok(Levels {
            room: *room,
            base,
            cover,
            bar_of,
            step_of,
            colour,
            count,
            segments,
            shape,
            beats,
            offsets,
        })
    }

    /// The bars' heights: still, a gentle shape; in the clip each swings with three waves.
    fn heights(&self, phase: Option<f64>) -> Vec<f32> {
        let clip64 = |x: f64, lo: f64, hi: f64| {
            let x = if x.is_nan() || x > lo { x } else { lo };
            if x.is_nan() || x < hi {
                x
            } else {
                hi
            }
        };
        (0..self.count)
            .map(|k| match phase {
                None => {
                    let kf = k as f64;
                    let wobble = 0.12 * (kf * 1.7).sin() + 0.08 * (kf * 0.6 + 1.0).sin();
                    clip64(f64::from(self.shape[k]) + wobble, 0.08, 1.0) as f32
                }
                Some(p) => {
                    let mut swing = 0.0f64;
                    for (j, &depth) in DEPTH.iter().enumerate() {
                        let at = j * self.count + k;
                        let wave = (TAU * self.beats[at] as f64 * p + self.offsets[at]).sin();
                        let v = f64::from(depth) * wave;
                        swing = if j == 0 { 0.0 + v } else { swing + v };
                    }
                    clip64(f64::from(self.shape[k]) * (0.75 + swing), 0.06, 1.0) as f32
                }
            })
            .collect()
    }

    fn drawn(&self, phase: Option<f64>) -> Result<Canvas> {
        let mut out = self.base.clone();
        let heights = self.heights(phase);
        let segments = weak(self.segments as f64);
        let mut lit = Canvas::zeros(self.room.h, self.room.w, 3);
        for (i, &bar) in self.bar_of.iter().enumerate() {
            // a cell is lit below the bar's height, the last one in part (it glides)
            let reach = heights[bar.max(0) as usize];
            let part = clip((reach - self.step_of[i]) * segments + 0.5, 0.0, 1.0);
            let level = if bar >= 0 {
                weak(0.06) + weak(0.94) * part
            } else {
                0.0
            };
            let c = self.cover.data[i] * level;
            for k in 0..3 {
                lit.data[i * 3 + k] = self.colour[i * 3 + k] * c;
            }
        }
        let u = self.room.unit();
        glow(&mut out, &lit, None, &[(5.0 * u, 0.7), (18.0 * u, 0.5)])?;
        for (o, &v) in out.data.iter_mut().zip(&lit.data) {
            *o = maximum(*o, v);
        }
        Ok(out)
    }

    pub fn still(&self) -> Result<Canvas> {
        self.drawn(None)
    }

    pub fn frame(&self, phase: f64) -> Result<Canvas> {
        self.drawn(Some(phase))
    }
}

// -- Dark -------------------------------------------------------------------------------------

/// Dark: black, with the app's mark faintly (see the module's text).
pub struct Dark {
    room: Room,
    d: f64,
}

impl Dark {
    pub fn new(room: &Room) -> Dark {
        Dark {
            room: *room,
            d: py_min(room.rw(), room.rh()) * 0.18,
        }
    }

    fn drawn(&self, light: f64) -> Result<Canvas> {
        let mut out = Canvas::zeros(self.room.h, self.room.w, 3);
        mark_into(
            &mut out,
            (self.room.cx(), self.room.cy()),
            self.d,
            light,
            0.05,
        )?;
        let k = weak(0.18 + 0.12 * light);
        for v in &mut out.data {
            *v *= k;
        }
        Ok(out)
    }

    pub fn still(&self) -> Result<Canvas> {
        self.drawn(1.0)
    }

    pub fn frame(&self, phase: f64) -> Result<Canvas> {
        self.drawn(0.5 + 0.5 * (TAU * phase).cos())
    }
}

// -- Aurora -----------------------------------------------------------------------------------

const AURORA_FOOT: [u8; 3] = [60, 255, 170]; // green where the curtain hangs
const AURORA_TOP: [u8; 3] = [150, 80, 255]; // violet up its rays
const AURORA_SKY: ([u8; 3], [u8; 3]) = ([2, 4, 12], [6, 14, 24]);
/// Waves of brightness along a curtain, each shimmering on its own.
const RAYS: usize = 9;

/// np.maximum of two doubles.
fn maximum64(a: f64, b: f64) -> f64 {
    if a >= b || a.is_nan() {
        a
    } else {
        b
    }
}

/// np.clip of a double.
fn clip64(x: f64, lo: f64, hi: f64) -> f64 {
    let x = if x.is_nan() || x > lo { x } else { lo };
    if x.is_nan() || x < hi {
        x
    } else {
        hi
    }
}

/// Aurora: curtains of light over a dark horizon, green at their foot, violet up their rays;
/// in the clip the rays shimmer, each on its own. Drawn across the picture's width with the
/// sky's height above them, whatever the panel's shape.
pub struct Aurora {
    room: Room,
    sky: Canvas,
    /// each curtain's light, rows x columns x 3 (doubles, as numpy makes them of float64s)
    curtains: Vec<Vec<f64>>,
    /// each curtain's waves along it: rays x columns
    rays: Vec<Vec<f32>>,
    beats: Vec<i64>,
    offsets: Vec<f64>,
}

impl Aurora {
    pub fn new(room: &Room) -> Result<Aurora> {
        let (w, h) = (room.w, room.h);
        let mut rng = chances(room, 1);
        let mut sky = vertical(h, w, floats(AURORA_SKY.0), floats(AURORA_SKY.1));
        aurora_stars(&mut sky, room, &mut rng)?;
        let across = weak(w as f64);
        let u: Vec<f32> = (0..w).map(|x| x as f32 / across).collect();
        let tau = weak(TAU);
        let (foot_colour, top_colour) = (floats(AURORA_FOOT), floats(AURORA_TOP));
        let mut curtains = Vec::new();
        let mut rays = Vec::new();
        for (level, height, gain) in [(0.62, 0.55, 1.0), (0.70, 0.42, 0.75), (0.55, 0.65, 0.45)] {
            let phase = [0, 1, 2].map(|_| rng.uniform(0.0, TAU));
            // (a numpy float64 beside a float32 array: the sum in doubles)
            let wave = |ux: f32, f: f64, p: f64| (f64::from(tau * (ux * weak(f))) + p).sin();
            let foot: Vec<f64> = u
                .iter()
                .map(|&ux| {
                    let swing =
                        level + 0.05 * wave(ux, 0.9, phase[0]) + 0.03 * wave(ux, 2.3, phase[1]);
                    h as f64 * (swing + 0.015 * wave(ux, 5.1, phase[2]))
                })
                .collect();
            let (up, down, strength) = (h as f64 * height, h as f64 * 0.025, 0.55 * gain);
            let mut curtain = vec![0.0f64; h * w * 3];
            for y in 0..h {
                let ys = f64::from(y as f32);
                for (x, &f) in foot.iter().enumerate() {
                    let above = maximum64(f - ys, 0.0) / up; // 0 at its foot, up its rays
                    let below = maximum64(ys - f, 0.0) / down;
                    let mut light = (-above * 2.2).exp() * (-(below * below)).exp();
                    light *= clip64(1.0 - above, 0.0, 1.0).powf(black_box(0.6)); // the rays end
                    let mix = clip64(above * 1.4, 0.0, 1.0); // green at the foot, violet above
                    let at = (y * w + x) * 3;
                    for k in 0..3 {
                        let colour = (1.0 - mix) * f64::from(foot_colour[k])
                            + mix * f64::from(top_colour[k]);
                        curtain[at + k] = light * colour * strength;
                    }
                }
            }
            curtains.push(curtain);
            // the rays: soft streaks across, a few octaves of waves along the curtain
            let mut streaks = vec![0.0f32; RAYS * w];
            for k in 0..RAYS {
                let f = rng.uniform(6.0, 30.0) * (1.0 + k as f64 / 3.0);
                let shift = weak(rng.uniform(0.0, TAU));
                let tf = weak(TAU * f);
                for (i, &ux) in u.iter().enumerate() {
                    streaks[k * w + i] = 0.5 + 0.5 * np::sinf(tf * ux + shift);
                }
            }
            let share = weak(RAYS as f64);
            rays.push(streaks.iter().map(|&v| v / share).collect());
        }
        let beats = (0..3 * RAYS)
            .map(|_| integer(&mut rng, 1, 3))
            .collect::<Result<Vec<_>>>()?;
        let offsets = (0..3 * RAYS).map(|_| rng.uniform(0.0, TAU)).collect();
        Ok(Aurora {
            room: *room,
            sky,
            curtains,
            rays,
            beats,
            offsets,
        })
    }

    fn drawn(&self, phase: Option<f64>) -> Canvas {
        let (w, h) = (self.room.w, self.room.h);
        let mut out = self.sky.clone();
        for (j, (curtain, rays)) in self.curtains.iter().zip(&self.rays).enumerate() {
            // along the curtain: float32s still, doubles where the waves are a picture's
            let streak: Vec<f64> = match phase {
                None => (0..w)
                    .map(|i| {
                        let mut sum = rays[i];
                        for k in 1..RAYS {
                            sum += rays[k * w + i];
                        }
                        f64::from(weak(0.45) + weak(0.9) * sum)
                    })
                    .collect(),
                Some(p) => {
                    let weights: Vec<f64> = (0..RAYS)
                        .map(|k| {
                            let at = j * RAYS + k;
                            1.0 + 0.6 * (TAU * self.beats[at] as f64 * p + self.offsets[at]).sin()
                        })
                        .collect();
                    (0..w)
                        .map(|i| {
                            let mut sum = weights[0] * f64::from(rays[i]);
                            for (k, &weight) in weights.iter().enumerate().skip(1) {
                                sum += weight * f64::from(rays[k * w + i]);
                            }
                            0.45 + 0.9 * sum
                        })
                        .collect()
                }
            };
            for y in 0..h {
                for (x, &s) in streak.iter().enumerate() {
                    let at = (y * w + x) * 3;
                    for k in 0..3 {
                        let v = &mut out.data[at + k];
                        *v = (f64::from(*v) + curtain[at + k] * s) as f32;
                    }
                }
            }
        }
        out
    }

    pub fn still(&self) -> Result<Canvas> {
        Ok(self.drawn(None))
    }

    pub fn frame(&self, phase: f64) -> Result<Canvas> {
        Ok(self.drawn(Some(phase)))
    }
}

/// Aurora._stars: a few faint stars in the sky above the curtains.
fn aurora_stars(sky: &mut Canvas, room: &Room, rng: &mut Generator) -> Result<()> {
    let (w, h) = (room.w, room.h);
    let n = ((w * h) as f64 / 2600.0) as usize;
    let sx: Vec<f64> = (0..n).map(|_| rng.uniform(0.0, w as f64)).collect();
    let sy: Vec<f64> = (0..n).map(|_| rng.uniform(0.0, h as f64 * 0.75)).collect();
    let level: Vec<f64> = (0..n)
        .map(|_| {
            let l = rng.uniform(0.15, 0.6);
            l * l
        })
        .collect();
    let mut star = Canvas::zeros(h, w, 1);
    for i in 0..n {
        // (np.add.at of doubles into float32s: each sum in doubles, then made float32)
        let at = (sy[i] as usize) * w + sx[i] as usize;
        star.data[at] = (f64::from(star.data[at]) + level[i] * 255.0) as f32;
    }
    let lit = blur(&star, 0.6 * py_max(1.0, room.unit()))?;
    let k = weak(0.9);
    for (i, &v) in lit.data.iter().enumerate() {
        let add = v * k;
        for c in 0..3 {
            sky.data[i * 3 + c] += add;
        }
    }
    Ok(())
}

// -- Night ------------------------------------------------------------------------------------

const NIGHT_SKY: ([u8; 3], [u8; 3]) = ([1, 2, 8], [10, 16, 36]);
const HORIZON: [u8; 3] = [30, 70, 120];
const MOONLIGHT: [u8; 3] = [238, 232, 214];
const STARS: [[u8; 3]; 3] = [[235, 240, 255], [190, 210, 255], [255, 228, 200]];

/// Generator.choice(len(p), n, p=p): doubles drawn and looked up in the normed running sum of
/// `p` (searchsorted, the side to the right).
fn choice(rng: &mut Generator, n: usize, p: &[f64]) -> Vec<usize> {
    let mut cdf = Vec::with_capacity(p.len());
    for (i, &v) in p.iter().enumerate() {
        cdf.push(if i == 0 { v } else { cdf[i - 1] + v });
    }
    let last = cdf[cdf.len() - 1];
    for c in &mut cdf {
        *c /= last;
    }
    let draws: Vec<f64> = (0..n).map(|_| rng.random()).collect();
    draws
        .iter()
        .map(|&u| cdf.iter().take_while(|&&c| c <= u).count())
        .collect()
}

/// Night: a sky of stars over a glow at the horizon, a crescent moon; in the clip the stars
/// twinkle.
pub struct Night {
    room: Room,
    base: Canvas,
    still_field: Canvas,
    /// for each number of beats, the stars' light by the cosine and the sine of their offsets
    waves: Vec<(usize, Canvas, Canvas)>,
    moon: Image,
    moon_at: (i64, i64),
}

impl Night {
    pub fn new(room: &Room) -> Result<Night> {
        let (w, h) = (room.w, room.h);
        let u = room.unit();
        let mut rng = chances(room, 4);
        let mut base = vertical(h, w, floats(NIGHT_SKY.0), floats(NIGHT_SKY.1));
        // the horizon's glow, low
        let (high, low) = (weak(h as f64), weak(h as f64 * 0.28));
        let horizon = floats(HORIZON);
        for y in 0..h {
            let glow = np::expf(-((high - y as f32) / low).powf(black_box(weak(1.6))));
            for px in base.data[y * w * 3..(y + 1) * w * 3].chunks_exact_mut(3) {
                for (v, &c) in px.iter_mut().zip(&horizon) {
                    *v += glow * c * weak(0.55);
                }
            }
        }
        // a band of haze across, the milky way
        let angle = -0.35f64;
        let (cos, sin) = (weak(angle.cos()), weak(angle.sin()));
        let (yc, xc) = (weak(h as f64 * 0.45), weak(w as f64 * 0.5));
        let span = weak(w.min(h) as f64 * 0.22);
        let mut band = vec![0.0f32; w * h];
        for y in 0..h {
            for x in 0..w {
                let d = (y as f32 - yc) * cos - (x as f32 - xc) * sin;
                let z = d / span;
                band[y * w + x] = np::expf(-(z * z));
            }
        }
        let (sh, sw) = (h / 4 + 1, w / 4 + 1);
        let noise = Canvas {
            h: sh,
            w: sw,
            c: 1,
            data: (0..sh * sw).map(|_| rng.random() as f32).collect(),
        };
        let haze = blur(&noise, 2.5)?;
        let haze = FloatImage::from_vec(sw, sh, haze.data)
            .and_then(|p| p.resize(w, h, lp_pixels::Filter::Bilinear, None))
            .map_err(plane_failed)?;
        let veil = floats([10, 14, 26]);
        for ((px, &b), &mist) in base.data.chunks_exact_mut(3).zip(&band).zip(&haze.data) {
            let v = b * (weak(0.4) + mist);
            for (o, &c) in px.iter_mut().zip(&veil) {
                *o += v * c;
            }
        }
        // stars: many faint, few bright, more in the haze
        let n = ((w * h) as f64 / 900.0) as usize;
        let sx: Vec<f64> = (0..n).map(|_| rng.uniform(0.0, w as f64)).collect();
        let sy: Vec<f64> = (0..n).map(|_| rng.uniform(0.0, h as f64)).collect();
        let keep: Vec<bool> = (0..n)
            .map(|i| {
                let r = rng.random();
                let b = band[(sy[i] as usize) * w + sx[i] as usize];
                r < f64::from(weak(0.35) + weak(0.65) * b)
            })
            .collect();
        let kept: Vec<(f64, f64)> = (0..n)
            .filter(|&i| keep[i])
            .map(|i| (sx[i], sy[i]))
            .collect();
        let m = kept.len();
        let bright: Vec<f64> = (0..m).map(|_| rng.pareto(2.6) * 0.25 + 0.12).collect();
        let tint = choice(&mut rng, m, &[0.6, 0.25, 0.15]);
        let beats = (0..m)
            .map(|_| integer(&mut rng, 1, 4))
            .collect::<Result<Vec<_>>>()?;
        let offsets: Vec<f32> = (0..m).map(|_| rng.uniform(0.0, TAU) as f32).collect();
        let depth: Vec<f32> = (0..m).map(|_| rng.uniform(0.1, 0.5) as f32).collect(); // how much each twinkles
        let sigma = 0.55 * py_max(1.0, u);
        let at: Vec<usize> = kept
            .iter()
            .map(|&(x, y)| (y as i32 as usize) * w + x as i32 as usize)
            .collect();
        let level: Vec<f32> = bright
            .iter()
            .map(|&b| clip64(b, 0.05, 1.4) as f32)
            .collect();
        let colours = STARS.map(floats);
        // the stars' light with these weights, blurred as a star shines
        let field = |weights: &[f32]| -> Result<Canvas> {
            let mut out = Canvas::zeros(h, w, 3);
            for (i, &weight) in weights.iter().enumerate() {
                let px = &mut out.data[at[i] * 3..at[i] * 3 + 3];
                for (o, &c) in px.iter_mut().zip(&colours[tint[i]]) {
                    *o += c * weight;
                }
            }
            let mut near = blur(&out, sigma)?;
            let far = blur(&out, sigma * 4.0)?;
            for (v, &f) in near.data.iter_mut().zip(&far.data) {
                *v = *v * 4.0 + f * 2.0;
            }
            Ok(near)
        };
        // a star's light, level * (1 + depth * sin(2 pi beats t + offset)), is the still field
        // and, for each number of beats, a field each for the sine and the cosine of the time
        let still_field = field(&level)?;
        let mut waves = Vec::new();
        for b in 1..=3usize {
            let mine: Vec<f32> = (0..m)
                .map(|i| (if beats[i] == b as i64 { 1.0f32 } else { 0.0 }) * level[i] * depth[i])
                .collect();
            let by_cos: Vec<f32> = (0..m).map(|i| mine[i] * np::cosf(offsets[i])).collect();
            let by_sin: Vec<f32> = (0..m).map(|i| mine[i] * np::sinf(offsets[i])).collect();
            waves.push((b, field(&by_cos)?, field(&by_sin)?));
        }
        // the moon, a crescent, high on the side (within the circle of a round panel)
        let (short, long_side) = (py_min(room.rw(), room.rh()), py_max(room.rw(), room.rh()));
        let bar = long_side >= 2.4 * short;
        let size = lp_py::round(short * if bar { 0.34 } else { 0.22 }) as i64;
        if size < 1 {
            return Err(Failed(format!("no moon of {size} px")));
        }
        let moon = crate::moon::picture(
            size as usize,
            0.18,
            MOONLIGHT,
            Some([40, 46, 60]),
            false,
            None,
        );
        let (mx, my) = if room.round {
            (room.cx() + room.rw() * 0.16, room.cy() - room.rh() * 0.2)
        } else if w >= h {
            (
                room.x0 + room.rw() * if bar { 0.8 } else { 0.76 },
                room.y0 + room.rh() * 0.3,
            )
        } else {
            (
                room.x0 + room.rw() * 0.62,
                room.y0 + room.rh() * if bar { 0.12 } else { 0.24 },
            )
        };
        let half = size as f64 / 2.0;
        let moon_at = (
            lp_py::round(mx - half) as i64,
            lp_py::round(my - half) as i64,
        );
        Ok(Night {
            room: *room,
            base,
            still_field,
            waves,
            moon,
            moon_at,
        })
    }

    fn drawn(&self, phase: Option<f64>) -> Canvas {
        let (w, h) = (self.room.w, self.room.h);
        let mut out = self.base.clone();
        for (v, &s) in out.data.iter_mut().zip(&self.still_field.data) {
            *v += s;
        }
        if let Some(p) = phase {
            for (b, by_cos, by_sin) in &self.waves {
                let turn = TAU * *b as f64 * p;
                let (s, c) = (weak(turn.sin()), weak(turn.cos()));
                for ((v, &a), &b) in out.data.iter_mut().zip(&by_cos.data).zip(&by_sin.data) {
                    *v += a * s + b * c;
                }
            }
        }
        let (mw, mh) = (self.moon.width(), self.moon.height());
        let rgba = self.moon.as_bytes();
        let (mx, my) = self.moon_at;
        let (ry0, ry1) = crate::boot::py_slice(0.max(my), my + mh as i64, h);
        let (rx0, rx1) = crate::boot::py_slice(0.max(mx), mx + mw as i64, w);
        // (the moon's own corner at the region's: numpy's part of it from its start)
        for yy in 0..(ry1 - ry0).min(mh) {
            for xx in 0..(rx1 - rx0).min(mw) {
                let px = &rgba[(yy * mw + xx) * 4..(yy * mw + xx) * 4 + 4];
                let alpha = f32::from(px[3]) / 255.0;
                let at = ((ry0 + yy) * w + rx0 + xx) * 3;
                for (v, &c) in out.data[at..at + 3].iter_mut().zip(&px[..3]) {
                    *v += (f32::from(c) - *v) * alpha;
                }
            }
        }
        out
    }

    pub fn still(&self) -> Result<Canvas> {
        Ok(self.drawn(None))
    }

    pub fn frame(&self, phase: f64) -> Result<Canvas> {
        Ok(self.drawn(Some(phase)))
    }
}
