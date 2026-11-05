//! A lit bar of render/lit.py, made by the engine for each of its values (stage 2 of
//! docs/ROADMAP.md, element by element): bar_piece, and its glow as _glow_piece makes it.
//! What does not change with the value comes once (a [`Base`]): the bar's groove on its
//! canvas, the pill a span's light is made of (lit.py's _pill), and how its glow is worked out.
//! A pill longer than the short ones is a reference's first and last columns with the column
//! between them as often as its length asks: its caps reach no further, and Lanczos makes the
//! columns between them alike (the same pixels: tests/test_lit_bars.py holds them to it). For a
//! value, its lit spans and its paint, the light of each span goes on the groove as Pillow lays
//! it (alpha_composite), worked out in single precision as lit.py's light (its blur ring.rs's:
//! the same picture to a level) and kept by its length, as lit.py keeps it (a value gliding back
//! and forth lights two of three spans it lit before); its glow is worked out as Pillow works it
//! out (reduce, GaussianBlur, resize, point), in Pillow's fixed point: the same, to the bit.

use std::collections::HashMap;

use crate::ring::{self, Scratch, LIGHT_HALO, LIGHT_WHITE};

/// The steps of a glow's rise to the head (lit.py's GLOW_STEPS).
pub const GLOW_STEPS: usize = 7;
/// The longest a bar, its room or its glow's area is (px).
const MOST: usize = 8192;
/// The bytes of the lights of its spans a bar keeps (as lit.py keeps them): a value gliding
/// back and forth lights the spans it lit before.
const LIGHTS_KEPT: usize = 2 << 20;

/// How a bar's glow is worked out (lit.py's _glow_piece), as Libre Panel sends it.
pub struct Glow {
    /// The bar's top left on the panel (the widget's x, y).
    pub bar: (i32, i32),
    /// Its area on the panel (x, y, w, h): nothing of it outside.
    pub area: (i32, i32, usize, usize),
    /// It is worked out at 1 / scale of the area's size (1 to 4) ...
    pub scale: usize,
    /// ... blurred by this radius at the area's size ...
    pub radius: f64,
    /// ... and this strong (below 0.999: its levels scaled).
    pub strength: f64,
    /// It rises over this many pixels to the head ...
    pub head: f64,
    /// ... from this share of the colour along the lit part (lit.py's GLOW_TAIL) ...
    pub tail: f64,
    /// ... in steps of these shares (GLOW_TAIL + (1 - GLOW_TAIL) * t ** 1.4) ...
    pub levels: [f64; GLOW_STEPS],
    /// ... running this far towards white (GLOW_WHITE * t ** 2), t = (i + 1) / GLOW_STEPS.
    pub whites: [f64; GLOW_STEPS],
}

/// A bar's shape as lit.py lays it out: lying, along its length and across its thickness.
pub struct Geometry {
    pub length: usize,
    pub thick: usize,
    /// The room round the bar on its canvas, for the light's halo (lit.py's pad).
    pub pad: usize,
    /// The light's thickness (lit.py's core) and its top on the canvas, the pad's top above it
    /// (lit.py's offset).
    pub core: usize,
    pub offset: i64,
    /// It stands (drawn lying, then turned) ...
    pub upright: bool,
    /// ... and fills from its far end.
    pub reverse: bool,
    /// Its segments (0: none), each of one colour of a gradient.
    pub segments: u32,
    /// The softness of the light's halo (px).
    pub halo: f64,
    pub glow: Option<Glow>,
}

/// The pill a span's light is made of (lit.py's _pill: coverage in whole levels, `core + 2
/// pad` rows): of each short span whole, of the others in parts.
pub struct Pills {
    /// A reference's first `lw` columns, its column after them, and its last `rw` columns.
    pub left: Vec<u8>,
    pub lw: usize,
    pub middle: Vec<u8>,
    pub right: Vec<u8>,
    pub rw: usize,
    /// The pills of the spans 1, 2, ... long, `span + 2 pad` columns each.
    pub short: Vec<Vec<u8>>,
}

/// A value's paint (lit.py's paint): its colour, and the one its gradient runs to.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Paint {
    pub colour: [u8; 4],
    pub to: Option<[u8; 4]>,
}

/// What a bar's pictures are made of, for every value.
pub struct Base {
    g: Geometry,
    /// The groove on the canvas, lying (RGBA).
    canvas: Vec<u8>,
    pills: Pills,
    /// What the light's blur works in, from one picture to the next.
    scratch: Scratch,
    /// The light of each span made, by its length, its first column where a gradient's colours
    /// are those of its columns (else 0), and its paint; and their bytes.
    lights: HashMap<(usize, i64, Paint), Vec<u8>>,
    kept: usize,
}

/// A bar's colours (lit.py's _bar_colours, 0 to 1 in single precision): one, or one a column.
enum Colours {
    One([f32; 3]),
    Columns(Vec<[f32; 3]>),
}

impl Colours {
    fn at(&self, x: usize) -> [f32; 3] {
        match self {
            Colours::One(c) => *c,
            Colours::Columns(all) => all[x],
        }
    }
}

impl Base {
    pub fn new(g: Geometry, canvas: Vec<u8>, pills: Pills) -> Result<Base, String> {
        let (cw, ch) = (g.length + 2 * g.pad, g.thick + 2 * g.pad);
        let high = g.core + 2 * g.pad;
        if g.length == 0 || g.thick == 0 || g.core == 0 || g.core > g.thick {
            return Err(format!(
                "a bar of {}x{} lit {} thick",
                g.length, g.thick, g.core
            ));
        }
        if cw > MOST || ch > MOST || !(g.halo > 0.0 && g.halo.is_finite()) {
            return Err("a bar of no shape known".into());
        }
        if canvas.len() != cw * ch * 4 {
            return Err("a bar's groove is not of its canvas".into());
        }
        let p = &pills;
        if p.lw == 0
            || p.rw == 0
            || p.left.len() != high * p.lw
            || p.middle.len() != high
            || p.right.len() != high * p.rw
        {
            return Err("a bar's pill is not of its parts".into());
        }
        if p.short.len() + 1 + 2 * g.pad < p.lw + p.rw {
            return Err("a bar's pill parts make not every length".into());
        }
        if p.short
            .iter()
            .enumerate()
            .any(|(k, pill)| pill.len() != high * (k + 1 + 2 * g.pad))
        {
            return Err("a bar's short pills are not of their lengths".into());
        }
        if let Some(glow) = &g.glow {
            let (_, _, aw, ah) = glow.area;
            let known = [glow.radius, glow.strength, glow.head, glow.tail]
                .iter()
                .chain(&glow.levels)
                .chain(&glow.whites)
                .all(|v| v.is_finite());
            if aw == 0 || ah == 0 || aw > MOST || ah > MOST || !(1..=4).contains(&glow.scale) {
                return Err("a bar's glow of no area".into());
            }
            if !known || glow.radius <= 0.0 || glow.strength <= 0.0 {
                return Err("a bar's glow of no shape known".into());
            }
        }
        Ok(Base {
            g,
            canvas,
            pills,
            scratch: Scratch::default(),
            lights: HashMap::new(),
            kept: 0,
        })
    }

    /// The size of its picture (w, h), turned upright where the bar stands.
    pub fn size(&self) -> (usize, usize) {
        let (cw, ch) = (
            self.g.length + 2 * self.g.pad,
            self.g.thick + 2 * self.g.pad,
        );
        if self.g.upright {
            (ch, cw)
        } else {
            (cw, ch)
        }
    }

    /// The bar's picture with its lit `spans` (from, to along it, as lit.py's _spans gives
    /// them) in `paint`: its groove, the light of each span over it (lit.py's bar_piece), RGBA,
    /// turned where it stands.
    pub fn picture(&mut self, spans: &[(f64, f64)], paint: &Paint) -> Vec<u8> {
        let (pad, offset) = (self.g.pad, self.g.offset);
        let (cw, ch) = (self.g.length + 2 * pad, self.g.thick + 2 * pad);
        let mut out = self.canvas.clone();
        if !spans.is_empty() {
            let mut colours = None;
            let fade = f64::from(paint.colour[3]) / 255.0;
            for &(a, b) in spans {
                let start = a.round_ties_even() as i64;
                let span = ((b - a).round_ties_even() as i64).max(1) as usize;
                let key = (span, if paint.to.is_some() { start } else { 0 }, *paint);
                if !self.lights.contains_key(&key) {
                    let colours = colours.get_or_insert_with(|| self::colours(&self.g, paint));
                    let made = self.light(start, span, colours);
                    if self.kept + made.len() > LIGHTS_KEPT {
                        self.lights.clear();
                        self.kept = 0;
                    }
                    self.kept += made.len();
                    self.lights.insert(key, made);
                }
                let light = &self.lights[&key];
                // laid at (start, offset): the pill's room is the pad; what lies off the canvas
                // is cut (as Pillow crops the canvas there and pastes it back)
                let w = span + 2 * pad;
                for (y, row) in light.chunks_exact(w * 4).enumerate() {
                    let cy = offset + y as i64;
                    if !(0..ch as i64).contains(&cy) {
                        continue;
                    }
                    for (x, s) in row.chunks_exact(4).enumerate() {
                        let cx = start + x as i64;
                        if !(0..cw as i64).contains(&cx) {
                            continue;
                        }
                        let at = (cy as usize * cw + cx as usize) * 4;
                        let s = [s[0], s[1], s[2], faded(s[3], fade)];
                        ring::alpha_composite(&mut out[at..at + 4], &s);
                    }
                }
            }
        }
        if self.g.upright {
            turned(&out, cw, ch, 4)
        } else {
            out
        }
    }

    /// The pill of a span `span` long (lit.py's _pill), `span + 2 pad` x `core + 2 pad`.
    fn pill(&self, span: usize) -> Vec<u8> {
        let p = &self.pills;
        if span <= p.short.len() {
            return p.short[span - 1].clone();
        }
        let high = self.g.core + 2 * self.g.pad;
        let w = span + 2 * self.g.pad;
        let mut out = Vec::with_capacity(high * w);
        for y in 0..high {
            out.extend_from_slice(&p.left[y * p.lw..(y + 1) * p.lw]);
            out.resize(out.len() + (w - p.lw - p.rw), p.middle[y]);
            out.extend_from_slice(&p.right[y * p.rw..(y + 1) * p.rw]);
        }
        out
    }

    /// The light of a span `span` long from column `start` of the bar (lit.py's light of its
    /// pill, in the colours of the columns it lies over): RGBA, `span + 2 pad` x `core + 2 pad`.
    fn light(&mut self, start: i64, span: usize, colours: &Colours) -> Vec<u8> {
        let (pad, halo, last) = (self.g.pad, self.g.halo, self.g.length as i64 - 1);
        let (w, h) = (span + 2 * pad, self.g.core + 2 * pad);
        let mask: Vec<f32> = self
            .pill(span)
            .iter()
            .map(|&v| f32::from(v) / 255.0)
            .collect();
        let cut: Vec<f32> = mask.iter().map(|m| (m * 255.0) as u8 as f32).collect();
        let soft = ring::blur_in(&cut, w, h, halo, &mut self.scratch);
        let scale = (1.0f64 / 255.0) as f32;
        let column: Vec<[f32; 3]> = (0..w as i64)
            .map(|x| {
                let c = colours.at((start - pad as i64 + x).clamp(0, last) as usize);
                [c[0] * 255.0, c[1] * 255.0, c[2] * 255.0]
            })
            .collect();
        let mut out = Vec::with_capacity(w * h * 4);
        for y in 0..h {
            for (x, c) in column.iter().enumerate() {
                let i = y * w + x;
                let (m, s) = (mask[i], soft[i] * scale);
                // (0 and 1 as they are: powf's, to the bit)
                let core = if m == 0.0 || m == 1.0 { m } else { m.powf(0.7) };
                let white = LIGHT_WHITE * core;
                for &c in c {
                    out.push(ring::level(c + (255.0 - c) * white));
                }
                let soft = (s - m).clamp(0.0, 1.0);
                out.push(ring::level(
                    (core + LIGHT_HALO * soft).clamp(0.0, 1.0) * 255.0,
                ));
            }
        }
        out
    }

    /// The bar's glow with its lit `spans` in `paint` (lit.py's _glow_piece): the light it
    /// adds over its area (x, y, w, h on the panel; RGB), None where it has none.
    pub fn glow(&self, spans: &[(f64, f64)], paint: &Paint) -> Option<Glowed> {
        let glow = self.g.glow.as_ref()?;
        if spans.is_empty() {
            return None;
        }
        let colours = colours(&self.g, paint);
        let (length, thick) = (self.g.length, self.g.thick);
        let lying = self.glow_source(glow, spans, &colours);
        let (source, sw, sh) = if self.g.upright {
            (turned(&lying, length, thick, 3), thick, length)
        } else {
            (lying, length, thick)
        };
        if source.iter().all(|&v| v == 0) {
            return None;
        }
        // the source where it lies inside the area
        let (ax, ay, aw, ah) = glow.area;
        let (bx, by) = (i64::from(glow.bar.0), i64::from(glow.bar.1));
        let (ax, ay) = (i64::from(ax), i64::from(ay));
        let (x0, y0) = (bx.max(ax), by.max(ay));
        let x1 = (bx + sw as i64).min(ax + aw as i64);
        let y1 = (by + sh as i64).min(ay + ah as i64);
        if x1 <= x0 || y1 <= y0 {
            return None;
        }
        let mut plane = vec![0u8; aw * ah * 3];
        let run = (x1 - x0) as usize * 3;
        for y in y0..y1 {
            let from = (((y - by) as usize * sw) + (x0 - bx) as usize) * 3;
            let to = (((y - ay) as usize * aw) + (x0 - ax) as usize) * 3;
            plane[to..to + run].copy_from_slice(&source[from..from + run]);
        }
        // (a channel at a time from here: each is worked out on its own there too)
        let scale = glow.scale;
        let (mut small, sw, sh) = if scale > 1 {
            reduce(&plane, aw, ah, scale)
        } else {
            (planes(&plane), aw, ah)
        };
        gaussian(&mut small, sw, sh, (glow.radius / scale as f64) as f32);
        let mut out = interleaved(&if scale > 1 {
            bilinear(small, sw, sh, aw, ah)
        } else {
            small
        });
        if glow.strength < 0.999 {
            let table: Vec<u8> = (0..256)
                .map(|v| (f64::from(v) * glow.strength).round_ties_even().min(255.0) as u8)
                .collect();
            out.iter_mut().for_each(|v| *v = table[*v as usize]);
        }
        Some(Glowed {
            x: ax as i32,
            y: ay as i32,
            w: aw,
            h: ah,
            rgb: out,
        })
    }

    /// The glow before it is blurred (lit.py's _glow_source), lying: thick x length, RGB, on
    /// the bar a pixel in from each edge, dim along the lit part and rising in steps to the
    /// head, which runs towards white; whole pixels and whole levels.
    fn glow_source(&self, glow: &Glow, spans: &[(f64, f64)], colours: &Colours) -> Vec<u8> {
        let (length, thick, reverse) = (self.g.length, self.g.thick, self.g.reverse);
        let len = length as f64;
        let mut out = vec![0u8; thick * length * 3];
        // filling from the far end: worked out mirrored, and mirrored back
        let along: Vec<(f64, f64)> = if reverse {
            spans.iter().map(|&(a, b)| (len - b, len - a)).collect()
        } else {
            spans.to_vec()
        };
        let start = along
            .iter()
            .map(|s| s.0)
            .fold(f64::INFINITY, f64::min)
            .round_ties_even() as i64;
        let head = along.iter().map(|s| s.1).fold(f64::NEG_INFINITY, f64::max);
        let (first, last) = (start + 1, ((len - 2.0) * head / len).floor() as i64);
        if last < first {
            return out;
        }
        // its colours in whole levels, as the theme gave them (mirrored with it)
        let c = |k: usize| -> [f64; 3] {
            let c = colours.at(if reverse { length - 1 - k } else { k });
            c.map(|v| (f64::from(v) * 255.0).round_ties_even())
        };
        let mut source = vec![[0f64; 3]; length];
        let rise = glow.head.min((last - first) as f64);
        let tail_end = (last as f64 - rise).floor() as i64;
        if last as f64 - rise > first as f64 {
            for k in first..=tail_end.min(length as i64 - 1) {
                source[k as usize] = c(k as usize).map(|v| (v * glow.tail).floor());
            }
        }
        let steps = GLOW_STEPS as f64;
        for i in 0..GLOW_STEPS {
            // (a later step covers the column they share)
            let a = (last as f64 - rise + rise * i as f64 / steps).floor() as i64;
            let b = (last as f64 - rise + rise * (i + 1) as f64 / steps).floor() as i64;
            for k in a.max(0)..=b.min(length as i64 - 1) {
                source[k as usize] = c(k as usize).map(|v| {
                    let step = (v * glow.levels[i]).floor();
                    (step + (255.0 - step) * glow.whites[i]).floor()
                });
            }
        }
        let rows = if thick > 2 { 1..thick - 1 } else { 0..thick };
        for y in rows {
            for (k, s) in source.iter().enumerate() {
                let x = if reverse { length - 1 - k } else { k };
                let at = (y * length + x) * 3;
                for (o, v) in out[at..at + 3].iter_mut().zip(s) {
                    *o = *v as u8;
                }
            }
        }
        out
    }
}

/// A bar's glow, made: the light it adds (RGB, `w` x `h`) at (x, y) on the panel.
pub struct Glowed {
    pub x: i32,
    pub y: i32,
    pub w: usize,
    pub h: usize,
    pub rgb: Vec<u8>,
}

/// lit.py's _bar_colours: the paint's colour, or the gradient from it along the bar (a
/// colour per segment where it has them), in single precision as numpy works it out.
fn colours(g: &Geometry, paint: &Paint) -> Colours {
    let unit = |c: [u8; 4]| [c[0], c[1], c[2]].map(|v| f32::from(v) / 255.0);
    let start = unit(paint.colour);
    let Some(to) = paint.to else {
        return Colours::One(start);
    };
    let end = unit(to);
    let (n, length) = (g.segments, g.length as f32);
    Colours::Columns(
        (0..g.length)
            .map(|k| {
                let mut t = (k as f32 + 0.5) / length; // along the bar, lying
                if n > 0 {
                    // a colour per segment, as the flat bar
                    t = (t * n as f32).floor().min((n - 1) as f32) / (n - 1).max(1) as f32;
                }
                if g.reverse {
                    t = 1.0 - t; // it starts at the far end
                }
                [0, 1, 2].map(|i| start[i] + (end[i] - start[i]) * t)
            })
            .collect(),
    )
}

/// The colour's alpha on the light's (lit.py's _alpha_scaled: rounded half to even).
fn faded(a: u8, fade: f64) -> u8 {
    if fade >= 0.999 {
        a
    } else {
        (f64::from(a) * fade).round_ties_even().min(255.0) as u8
    }
}

/// `pixels` (`w` x `h`, `n` bytes a pixel) turned over its diagonal: `h` x `w` (Pillow's
/// TRANSPOSE).
fn turned(pixels: &[u8], w: usize, h: usize, n: usize) -> Vec<u8> {
    let mut out = vec![0u8; pixels.len()];
    for y in 0..h {
        for x in 0..w {
            out[(x * h + y) * n..][..n].copy_from_slice(&pixels[(y * w + x) * n..][..n]);
        }
    }
    out
}

// -- Pillow's reduce, GaussianBlur and BILINEAR resize of an RGB picture, to the bit ----------

/// Pillow's division_UINT32(divider, 8) (Reduce.c): 2 ** 32 / (256 * divider) in single
/// precision, cut to a whole number.
fn division(divider: u32) -> u32 {
    (4_294_967_296.0f32 / (256 * divider) as f32) as u32
}

/// The three channels of a picture, apart.
type Planes = [Vec<u8>; 3];

/// Pillow's reduce of an RGB picture (`w` x `h`, three bytes a pixel) by `s` (2, 3 or 4) each
/// way (Reduce.c's 2x2, 3x3, 4x4 and corners): each pixel the mean of its s x s, of what is
/// left over in the last column and row; (its channels, w, h). Each is Pillow's
/// `((sum + n / 2) * division(n)) >> 24` of its n pixels: its 2x2 and 4x4 shift as that does
/// (their division is a power of two), its 3x3 and its corners are that.
fn reduce(rgb: &[u8], w: usize, h: usize, s: usize) -> (Planes, usize, usize) {
    let (ow, oh) = (w.div_ceil(s), h.div_ceil(s));
    let mut out: Planes = [0, 1, 2].map(|_| vec![0u8; ow * oh]);
    let mut sums = vec![0u32; ow * 3];
    let multipliers: Vec<u32> = (0..=s * s).map(|n| division(n.max(1) as u32)).collect();
    for y in 0..oh {
        let rows = y * s..(y * s + s).min(h);
        let high = rows.len();
        sums.fill(0);
        for line in rows.map(|yy| &rgb[yy * w * 3..(yy + 1) * w * 3]) {
            for (sum, block) in sums.chunks_exact_mut(3).zip(line.chunks(s * 3)) {
                for px in block.chunks_exact(3) {
                    sum[0] += u32::from(px[0]);
                    sum[1] += u32::from(px[1]);
                    sum[2] += u32::from(px[2]);
                }
            }
        }
        for (x, sum) in sums.chunks_exact(3).enumerate() {
            let n = (w - x * s).min(s) * high;
            let (multiplier, amend) = (multipliers[n], n as u32 / 2);
            for (plane, &sum) in out.iter_mut().zip(sum) {
                plane[y * ow + x] = ((sum + amend).wrapping_mul(multiplier) >> 24) as u8;
            }
        }
    }
    (out, ow, oh)
}

/// Pillow's reduce of a picture of one channel (`w` x `h`, an "L" picture) by `s` each way:
/// each pixel the mean of its s x s (of what is left over at the last column and row), as
/// [`reduce`] makes each channel; (its pixels, w, h).
pub(crate) fn reduce_one(l: &[u8], w: usize, h: usize, s: usize) -> (Vec<u8>, usize, usize) {
    let (ow, oh) = (w.div_ceil(s), h.div_ceil(s));
    let mut out = vec![0u8; ow * oh];
    let mut sums = vec![0u32; ow];
    let multipliers: Vec<u32> = (0..=s * s).map(|n| division(n.max(1) as u32)).collect();
    for y in 0..oh {
        let rows = y * s..(y * s + s).min(h);
        let high = rows.len();
        sums.fill(0);
        for line in rows.map(|yy| &l[yy * w..(yy + 1) * w]) {
            for (sum, block) in sums.iter_mut().zip(line.chunks(s)) {
                *sum += block.iter().map(|&v| u32::from(v)).sum::<u32>();
            }
        }
        for (x, &sum) in sums.iter().enumerate() {
            let n = (w - x * s).min(s) * high;
            let (multiplier, amend) = (multipliers[n], n as u32 / 2);
            out[y * ow + x] = ((sum + amend).wrapping_mul(multiplier) >> 24) as u8;
        }
    }
    (out, ow, oh)
}

/// Pillow's GaussianBlur of `radius` of a picture of one channel (`w` x `h`), in place, as
/// [`gaussian`] blurs each channel.
pub(crate) fn gaussian_one(plane: &mut [u8], w: usize, h: usize, radius: f32) {
    let r = pillow_radius(radius, 3);
    if r == 0.0 || w == 0 || h == 0 {
        return;
    }
    let (mut line, mut before, mut sums) = (Vec::new(), Vec::new(), Vec::new());
    for _ in 0..3 {
        box_pass(plane, w, h, r, &mut line);
    }
    for _ in 0..3 {
        box_down(plane, w, h, r, (&mut before, &mut sums));
    }
}

/// Pillow's box of each of `passes` passes that together blur as a Gaussian of `radius`
/// (BoxBlur.c's _gaussian_blur_radius), in single precision as it works it out.
fn pillow_radius(radius: f32, passes: i32) -> f32 {
    let sigma2 = radius * radius / passes as f32;
    let big = (12.0 * f64::from(sigma2) + 1.0).sqrt() as f32;
    let l = ((f64::from(big) - 1.0) / 2.0).floor() as f32;
    let mut a = (2.0 * l + 1.0) * (l * (l + 1.0) - 3.0 * sigma2);
    a /= 6.0 * (sigma2 - (l + 1.0) * (l + 1.0));
    l + a
}

/// The three channels of an RGB picture apart, and together again.
fn planes(rgb: &[u8]) -> Planes {
    [0, 1, 2].map(|c| rgb.iter().skip(c).step_by(3).copied().collect())
}

fn interleaved(planes: &Planes) -> Vec<u8> {
    let [r, g, b] = planes;
    r.iter()
        .zip(g)
        .zip(b)
        .flat_map(|((&r, &g), &b)| [r, g, b])
        .collect()
}

/// Pillow's GaussianBlur of `radius` (BoxBlur.c, three passes) of a picture's channels: three
/// box passes along the rows, then three down the columns (Pillow's along the rows of the
/// picture turned), in fixed point.
fn gaussian(channels: &mut Planes, w: usize, h: usize, radius: f32) {
    let r = pillow_radius(radius, 3);
    if r == 0.0 || w == 0 || h == 0 {
        return;
    }
    let (mut line, mut before, mut sums) = (Vec::new(), Vec::new(), Vec::new());
    for plane in channels.iter_mut() {
        for _ in 0..3 {
            box_pass(plane, w, h, r, &mut line);
        }
        for _ in 0..3 {
            box_down(plane, w, h, r, (&mut before, &mut sums));
        }
    }
}

/// Pillow's numbers of a box pass of `float_radius` (ImagingHorizontalBoxBlur): its whole
/// pixels each way, the weight of a pixel inside and of the two beside (fixed point).
fn box_weights(float_radius: f32) -> (i32, u32, u32) {
    let radius = float_radius as i32;
    let ww = (16_777_216.0f32 / (float_radius * 2.0 + 1.0)) as u32;
    let fw = 16_777_216u32.wrapping_sub(((radius * 2 + 1) as u32).wrapping_mul(ww)) / 2;
    (radius, ww, fw)
}

/// For a line of `size` pixels and each pixel of it (x): the one whose value the running sum
/// loses and the one it gains, and the two beside the box (ImagingLineBoxBlur's three runs).
fn box_runs(size: i32, radius: i32, x: i32) -> (i32, i32, i32, i32) {
    let (edge_a, edge_b, last) = ((radius + 1).min(size), (size - radius - 1).max(0), size - 1);
    if edge_a <= edge_b {
        if x < edge_a {
            (0, x + radius, 0, x + radius + 1)
        } else if x < edge_b {
            (x - radius - 1, x + radius, x - radius - 1, x + radius + 1)
        } else {
            (x - radius - 1, last, x - radius - 1, last)
        }
    } else if x < edge_b {
        (0, x + radius, 0, x + radius + 1)
    } else if x < edge_a {
        (0, last, 0, last)
    } else {
        (x - radius - 1, last, x - radius - 1, last)
    }
}

/// One box pass of `float_radius` along each row of a channel (`w` x `h`), in place: Pillow's
/// ImagingHorizontalBoxBlur and ImagingLineBoxBlur, its running sum and edges as there.
fn box_pass(plane: &mut [u8], w: usize, h: usize, float_radius: f32, line: &mut Vec<u32>) {
    let (radius, ww, fw) = box_weights(float_radius);
    let laid = |acc: u32, left: u32, right: u32| {
        let bulk = acc
            .wrapping_mul(ww)
            .wrapping_add((left + right).wrapping_mul(fw));
        (bulk.wrapping_add(1 << 23) >> 24) as u8
    };
    let xsize = w as i32;
    let edge_a = (radius + 1).min(xsize);
    let edge_b = (xsize - radius - 1).max(0);
    let lastx = xsize - 1;
    let r = radius as usize;
    for row in plane.chunks_exact_mut(w).take(h) {
        line.clear();
        line.extend(row.iter().map(|&v| u32::from(v)));
        let p = &line[..];
        let at = |x: i32| p[x as usize];
        let mut acc = at(0).wrapping_mul((radius + 1) as u32);
        for x in 0..edge_a - 1 {
            acc = acc.wrapping_add(at(x));
        }
        acc = acc.wrapping_add(at(lastx).wrapping_mul((radius - edge_a + 1) as u32));
        if edge_a <= edge_b {
            // the middle, most of the row: the pixel that goes is the left one beside the box,
            // the one that comes the box's last, and the right one beside it is the next
            let (a, b) = (edge_a as usize, edge_b as usize);
            for x in 0..edge_a {
                acc = acc.wrapping_add(at(x + radius)).wrapping_sub(at(0));
                row[x as usize] = laid(acc, at(0), at(x + radius + 1));
            }
            let coming = p[a + r..].iter().zip(&p[a + r + 1..]);
            for ((out, &gone), (&came, &right)) in
                row[a..b].iter_mut().zip(&p[a - r - 1..]).zip(coming)
            {
                acc = acc.wrapping_add(came).wrapping_sub(gone);
                *out = laid(acc, gone, right);
            }
            for x in edge_b..=lastx {
                acc = acc.wrapping_add(at(lastx)).wrapping_sub(at(x - radius - 1));
                row[x as usize] = laid(acc, at(x - radius - 1), at(lastx));
            }
        } else {
            for x in 0..=lastx {
                let (gone, came, left, right) = box_runs(xsize, radius, x);
                acc = acc.wrapping_add(at(came)).wrapping_sub(at(gone));
                row[x as usize] = laid(acc, at(left), at(right));
            }
        }
    }
}

/// One box pass of `float_radius` down each column of a channel (`w` x `h`), in place: what
/// Pillow's pass along each row of the picture turned makes of it (ImagingBoxBlur), all the
/// columns at once, a row of running sums moving down.
fn box_down(
    plane: &mut [u8],
    w: usize,
    h: usize,
    float_radius: f32,
    (before, sums): (&mut Vec<u8>, &mut Vec<u32>),
) {
    let (radius, ww, fw) = box_weights(float_radius);
    let ysize = h as i32;
    let edge_a = (radius + 1).min(ysize);
    before.clear();
    before.extend_from_slice(&plane[..w * h]);
    let row = |y: i32| &before[y as usize * w..(y as usize + 1) * w];
    sums.clear();
    sums.extend(
        row(0)
            .iter()
            .map(|&v| u32::from(v).wrapping_mul((radius + 1) as u32)),
    );
    for y in 0..edge_a - 1 {
        for (s, &v) in sums.iter_mut().zip(row(y)) {
            *s = s.wrapping_add(u32::from(v));
        }
    }
    let times = (radius - edge_a + 1) as u32;
    for (s, &v) in sums.iter_mut().zip(row(ysize - 1)) {
        *s = s.wrapping_add(u32::from(v).wrapping_mul(times));
    }
    for (y, out) in plane.chunks_exact_mut(w).take(h).enumerate() {
        let (gone, came, left, right) = box_runs(ysize, radius, y as i32);
        let beside = row(left).iter().zip(row(right));
        let moved = sums.iter_mut().zip(row(gone)).zip(row(came));
        for ((o, ((s, &g), &c)), (&l, &r)) in out.iter_mut().zip(moved).zip(beside) {
            *s = s.wrapping_add(u32::from(c)).wrapping_sub(u32::from(g));
            let bulk = s
                .wrapping_mul(ww)
                .wrapping_add((u32::from(l) + u32::from(r)).wrapping_mul(fw));
            *o = (bulk.wrapping_add(1 << 23) >> 24) as u8;
        }
    }
}

/// Pillow's coefficients of a BILINEAR resize of `size` pixels to `out` (Resample.c's
/// precompute_coeffs and normalize_coeffs_8bpc): for each pixel of the result the first of
/// those it is made of and how many, and their weights in fixed point (`ksize` each).
fn coefficients(size: usize, out: usize) -> (Vec<(usize, usize)>, Vec<i32>, usize) {
    let scale = f64::from(size as f32) / out as f64;
    let filterscale = scale.max(1.0);
    let support = filterscale; // (the filter's support, 1, times the scale)
    let ksize = support.ceil() as usize * 2 + 1;
    let inverse = 1.0 / filterscale;
    let mut bounds = Vec::with_capacity(out);
    let mut weights = vec![0f64; out * ksize];
    for xx in 0..out {
        let center = (xx as f64 + 0.5) * scale;
        let xmin = ((center - support + 0.5) as i64).max(0);
        let xmax = ((center + support + 0.5) as i64).min(size as i64);
        let n = ((xmax - xmin).max(0) as usize).min(ksize);
        let k = &mut weights[xx * ksize..(xx + 1) * ksize];
        let mut sum = 0.0;
        for (x, k) in k.iter_mut().enumerate().take(n) {
            let d = ((x as i64 + xmin) as f64 - center + 0.5) * inverse;
            let d = d.abs();
            *k = if d < 1.0 { 1.0 - d } else { 0.0 };
            sum += *k;
        }
        if sum != 0.0 {
            k.iter_mut().take(n).for_each(|k| *k /= sum);
        }
        bounds.push((xmin as usize, n));
    }
    let one = f64::from(1 << 22);
    let fixed = weights
        .iter()
        .map(|&k| {
            if k < 0.0 {
                (-0.5 + k * one) as i32
            } else {
                (0.5 + k * one) as i32
            }
        })
        .collect();
    (bounds, fixed, ksize)
}

/// A sum of Pillow's fixed point as a level (Resample.c's clip8).
fn clip8(ss: i32) -> u8 {
    (ss >> 22).clamp(0, 255) as u8
}

/// Pillow's BILINEAR resize of a picture's channels to `ow` x `oh` (Resample.c, 8 bits a
/// channel): along the rows, then down the columns, each cut to whole levels.
fn bilinear(mut channels: Planes, w: usize, h: usize, ow: usize, oh: usize) -> Planes {
    let across = (ow != w).then(|| coefficients(w, ow));
    let down = (oh != h).then(|| coefficients(h, oh));
    for plane in &mut channels {
        let mut width = w;
        if let Some((bounds, k, ksize)) = &across {
            let mut out = vec![0u8; ow * h];
            if *ksize == 3 {
                // (made larger, each pixel of three at most: the weights beyond its own are 0,
                // and the line has two more pixels for them, which add nothing)
                let taps: Vec<(usize, [i32; 3])> = bounds
                    .iter()
                    .enumerate()
                    .map(|(xx, &(xmin, n))| {
                        let mut t = [0; 3];
                        t[..n].copy_from_slice(&k[xx * 3..xx * 3 + n]);
                        (xmin, t)
                    })
                    .collect();
                let mut padded = Vec::with_capacity(w + 2);
                for (line, row) in plane.chunks_exact(w).zip(out.chunks_exact_mut(ow)) {
                    padded.clear();
                    padded.extend(line.iter().map(|&v| i32::from(v)));
                    padded.extend([0, 0]);
                    for (o, &(xmin, t)) in row.iter_mut().zip(&taps) {
                        let p = &padded[xmin..xmin + 3];
                        *o = clip8((1 << 21) + p[0] * t[0] + p[1] * t[1] + p[2] * t[2]);
                    }
                }
            } else {
                for (line, row) in plane.chunks_exact(w).zip(out.chunks_exact_mut(ow)) {
                    for (xx, (o, &(xmin, n))) in row.iter_mut().zip(bounds).enumerate() {
                        let mut ss = 1i32 << 21;
                        for (&v, &k) in line[xmin..xmin + n].iter().zip(&k[xx * ksize..]) {
                            ss += i32::from(v) * k;
                        }
                        *o = clip8(ss);
                    }
                }
            }
            *plane = out;
            width = ow;
        }
        if let Some((bounds, k, ksize)) = &down {
            let mut out = vec![0u8; width * oh];
            let mut ss = vec![0i32; width];
            for (yy, (row, &(ymin, n))) in out.chunks_exact_mut(width).zip(bounds).enumerate() {
                ss.fill(1 << 21);
                let lines = plane[ymin * width..(ymin + n) * width].chunks_exact(width);
                for (line, &k) in lines.zip(&k[yy * ksize..]) {
                    for (s, &v) in ss.iter_mut().zip(line) {
                        *s += i32::from(v) * k;
                    }
                }
                for (o, &s) in row.iter_mut().zip(&ss) {
                    *o = clip8(s);
                }
            }
            *plane = out;
        }
    }
    channels
}

#[cfg(test)]
mod tests {
    use super::*;

    /// How long a value's picture and glow take, a bar as Lumen's progress on the 12.3"
    /// (cargo test --release bar_timing -- --ignored --nocapture).
    #[test]
    #[ignore]
    fn bar_timing() {
        let (length, thick, pad, core) = (494, 7, 14, 4);
        let high = core + 2 * pad;
        let side = pad + core + 4;
        let glow = Glow {
            bar: (47, 597),
            area: (29, 579, 530, 43),
            scale: 2,
            radius: 7.0,
            strength: 0.18,
            head: 45.0,
            tail: 0.187,
            levels: [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0],
            whites: [0.01, 0.05, 0.12, 0.21, 0.33, 0.48, 0.65],
        };
        let g = Geometry {
            length,
            thick,
            pad,
            core,
            offset: 2,
            upright: false,
            reverse: false,
            segments: 0,
            halo: 6.75,
            glow: Some(glow),
        };
        let short = (1..2 * (side - pad))
            .map(|s| vec![128u8; high * (s + 2 * pad)])
            .collect();
        let pills = Pills {
            left: vec![200; high * side],
            lw: side,
            middle: vec![255; high],
            right: vec![200; high * side],
            rw: side,
            short,
        };
        let canvas = vec![30u8; (length + 2 * pad) * (thick + 2 * pad) * 4];
        let mut base = Base::new(g, canvas, pills).expect("a bar");
        let paint = Paint {
            colour: [28, 113, 110, 255],
            to: Some([46, 230, 214, 255]),
        };
        let n = 200;
        let (mut picture, mut glowed) = (0.0, 0.0);
        for k in 0..n {
            let spans = [(0.0, 100.0 + 300.0 * k as f64 / n as f64)];
            let t = std::time::Instant::now();
            std::hint::black_box(base.picture(&spans, &paint));
            picture += t.elapsed().as_secs_f64();
            let t = std::time::Instant::now();
            std::hint::black_box(base.glow(&spans, &paint));
            glowed += t.elapsed().as_secs_f64();
        }
        let each = |s: f64| s / n as f64 * 1000.0;
        println!(
            "picture {:.3} ms, glow {:.3} ms",
            each(picture),
            each(glowed)
        );
    }

    #[test]
    fn pillows_box_radius_and_division() {
        // ImageFilter.GaussianBlur(r) on a line: its boxes as BoxBlur.c works them out
        assert_eq!(pillow_radius(0.0, 3), 0.0);
        let r = pillow_radius(3.5, 3); // (dither._box_radius(3.5): 3 and 0.0244755...)
        assert!((r - 3.024_475_5).abs() < 1e-5, "{r}");
        assert_eq!(division(9), 1_864_135);
        assert_eq!(division(1), 16_777_216);
        assert_eq!(division(4), 4_194_304);
    }

    #[test]
    fn a_reduce_keeps_a_level_and_its_corners_are_the_means_of_what_is_left() {
        let (w, h) = (7, 5);
        let flat = vec![200u8; w * h * 3];
        for s in 2..=4 {
            let (out, ow, oh) = reduce(&flat, w, h, s);
            assert_eq!((ow, oh), (w.div_ceil(s), h.div_ceil(s)));
            assert!(out.iter().flatten().all(|&v| v == 200), "{s}: {out:?}");
        }
        // one bright pixel in the last column's leftover: the corner's mean of 1 x 2
        let mut dot = vec![0u8; w * h * 3];
        dot[6 * 3] = 255;
        let (out, ow, _) = reduce(&dot, w, h, 2);
        assert_eq!(out[0][3], 128); // (1 + 255) * division(2) >> 24
        assert_eq!(ow, 4);
    }

    #[test]
    fn a_blur_and_a_resize_keep_a_level() {
        let (w, h) = (23, 9);
        let mut flat = planes(&vec![77u8; w * h * 3]);
        gaussian(&mut flat, w, h, 3.5);
        assert!(flat.iter().flatten().all(|&v| v == 77));
        let big = interleaved(&bilinear(flat, w, h, 69, 27));
        assert_eq!(big.len(), 69 * 27 * 3);
        assert!(big.iter().all(|&v| v == 77));
    }

    #[test]
    fn a_pill_of_parts_is_its_caps_with_the_middle_between() {
        let pad = 2;
        let g = Geometry {
            length: 30,
            thick: 4,
            pad,
            core: 2,
            offset: 1,
            upright: false,
            reverse: false,
            segments: 0,
            halo: 1.5,
            glow: None,
        };
        let high = 2 + 2 * pad;
        let (lw, rw) = (3, 4);
        let short: Vec<Vec<u8>> = (1..=4)
            .map(|s| vec![s as u8; high * (s + 2 * pad)])
            .collect();
        let pills = Pills {
            left: vec![1; high * lw],
            lw,
            middle: vec![2; high],
            right: vec![3; high * rw],
            rw,
            short,
        };
        let canvas = vec![0u8; 34 * 8 * 4];
        let base = Base::new(g, canvas, pills).expect("a bar");
        assert_eq!(base.pill(3), vec![3u8; high * 7]);
        let long = base.pill(9);
        assert_eq!(long.len(), high * 13);
        assert_eq!(&long[..13], &[1, 1, 1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3]);
    }
}
