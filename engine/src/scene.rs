//! A frame composed in the engine from its layers (stage 2 of docs/ROADMAP.md, "layers
//! instead of drawing anew"): the theme's ground, and the pieces Libre Panel draws
//! (render/renderer.py's), each sent once; a frame is the order they lie in, bottom first.
//! Where a layer came or went since the frame before, the frame is composed anew, pixel for
//! pixel as Renderer._compose composes it (Pillow's paste through a layer's alpha onto the
//! opaque frame; ImageChops.add for light; a video's frame as it is), and handed on as the
//! boxes of one of Libre Panel's own frames would be (yuv.rs compares them block by block).

use std::collections::{HashMap, HashSet};

use crate::bar;
use crate::fan;
use crate::globe;
use crate::ring;
use crate::wire::Boxes;

/// How a layer goes onto what lies under it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum How {
    /// RGBA, through its own alpha (Renderer._paste onto the opaque frame).
    Over,
    /// RGB, as it is (a video's frame: moving.solid).
    Solid,
    /// RGB light, added to what lies under it (Renderer._add_light).
    Add,
}

impl How {
    pub fn of(code: u32) -> Option<How> {
        match code {
            0 => Some(How::Over),
            1 => Some(How::Solid),
            2 => Some(How::Add),
            _ => None,
        }
    }

    fn channels(self) -> usize {
        if self == How::Over {
            4
        } else {
            3
        }
    }
}

/// The largest layer taken: twice the largest panel each way.
pub const MOST_SIDE: usize = 8192;
/// The longest source taken (a graph's strip reaches past its window: as wide as two layers).
pub const MOST_SOURCE: usize = 2 * MOST_SIDE;

/// A part of a layer cut from a source (wire.rs's kind 15): `w` x `h` pixels from (`sx`, `sy`)
/// in the source to (`dx`, `dy`) in the layer.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Part {
    pub source: u32,
    pub sx: i32,
    pub sy: i32,
    pub dx: i32,
    pub dy: i32,
    pub w: usize,
    pub h: usize,
}

/// A picture laid on a layer (Scene::laid): source `source`, its top left at (dx, dy).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Placed {
    pub source: u32,
    pub dx: i32,
    pub dy: i32,
}

/// A picture layers are cut from (RGBA, row by row): a graph's strip.
struct Source {
    w: usize,
    h: usize,
    pixels: Vec<u8>,
    /// Of each row: the first column whose alpha is not 0 and the one after the last (0, 0
    /// for none), worked out once: where a layer cut from it shows, without looking again.
    shown: Vec<(usize, usize)>,
}

impl Source {
    fn new(w: usize, h: usize, pixels: Vec<u8>) -> Source {
        let shown = pixels
            .chunks_exact(w * 4)
            .map(|row| {
                let alpha = |x: &usize| row[x * 4 + 3] != 0;
                match (0..w).find(alpha) {
                    Some(first) => (first, (first..w).rfind(alpha).map_or(first + 1, |x| x + 1)),
                    None => (0, 0),
                }
            })
            .collect();
        Source {
            w,
            h,
            pixels,
            shown,
        }
    }
}

/// The most rotors kept (fan.rs): those used last stay, the one used longest ago goes first
/// (devices/engine.py's SceneSender keeps count the same way).
pub const MOST_ROTORS: usize = 8;

#[derive(Clone)]
pub struct Layer {
    x: i64,
    y: i64,
    w: usize,
    how: How,
    pixels: Vec<u8>,
    /// The part of the layer that changes what lies under it (x0, y0, x1, y1 in the layer):
    /// where its alpha is not 0 (Over), its light not black (Add); all of it (Solid). Of a
    /// layer cut from sources, the box round where its parts show (Scene::view): it may hold a
    /// see-through pixel more (where a part lies over another), none less.
    part: (usize, usize, usize, usize),
    /// The ring it is a picture of (ring.rs), which stays while it does.
    ring: Option<u32>,
    /// The lit bar it is a picture or the glow of (bar.rs), which stays while it does.
    bar: Option<u32>,
    /// The sources it was cut from, which stay while it does.
    sources: Vec<u32>,
    /// The globe it is a picture of (globe.rs), which stays while it does.
    globe: Option<u32>,
    /// Its one colour (RGBA, through its alpha), where it is all that colour: no pixels kept
    /// (a card's inside, Layer::filled).
    fill: Option<[u8; 4]>,
    /// Cut from sources (Scene::view): no pixels kept, laid from theirs.
    cuts: Option<Box<Cuts>>,
}

/// How a layer cut from sources (Scene::view) is laid from their pixels, without pixels of
/// its own: its parts, and for each band of its rows (down the layer) the spans of its columns
/// and the part that shows in each, the last one pasted there (columns from, to, part).
#[derive(Clone, Debug, PartialEq, Eq)]
struct Cuts {
    parts: Vec<Part>,
    bands: Vec<Band>,
}

/// Of a layer cut from sources (Cuts): the columns of a row from, to, and the part in them.
type Span = (usize, usize, usize);
/// Its rows from, to, and the spans of each of them.
type Band = (usize, usize, Vec<Span>);

impl Cuts {
    /// The cuts of a layer of `w` x `h` from `parts`, each pasted over those before it.
    fn of(parts: &[Part], w: usize, h: usize) -> Cuts {
        let lying: Vec<(usize, usize, usize, usize)> = parts
            .iter()
            .map(|p| {
                let (dx, dy) = (i64::from(p.dx), i64::from(p.dy));
                let x1 = (dx + p.w as i64).clamp(0, w as i64) as usize;
                let y1 = (dy + p.h as i64).clamp(0, h as i64) as usize;
                (
                    dx.clamp(0, w as i64) as usize,
                    dy.clamp(0, h as i64) as usize,
                    x1,
                    y1,
                )
            })
            .collect();
        let mut rows: Vec<usize> = lying
            .iter()
            .filter(|r| r.2 > r.0 && r.3 > r.1)
            .flat_map(|r| [r.1, r.3])
            .collect();
        rows.sort_unstable();
        rows.dedup();
        let mut bands = Vec::new();
        for band in rows.windows(2) {
            let mut spans: Vec<Span> = Vec::new();
            for (k, &(x0, y0, x1, y1)) in lying.iter().enumerate() {
                if x1 > x0 && y0 <= band[0] && y1 >= band[1] {
                    // pasted over the spans before it: those keep what it leaves of them
                    let mut kept = Vec::with_capacity(spans.len() + 2);
                    for &(a, b, j) in &spans {
                        if b <= x0 || a >= x1 {
                            kept.push((a, b, j));
                            continue;
                        }
                        if a < x0 {
                            kept.push((a, x0, j));
                        }
                        if b > x1 {
                            kept.push((x1, b, j));
                        }
                    }
                    kept.push((x0, x1, k));
                    kept.sort_unstable();
                    spans = kept;
                }
            }
            if !spans.is_empty() {
                bands.push((band[0], band[1], spans));
            }
        }
        Cuts {
            parts: parts.to_vec(),
            bands,
        }
    }

    /// From row `v` of the layer down: the row its spans end at, and those spans (where no
    /// part lies, none, to the next band or past the last).
    fn rows(&self, v: usize) -> (usize, &[Span]) {
        let k = self.bands.partition_point(|band| band.1 <= v);
        match self.bands.get(k) {
            Some((v0, v1, spans)) if *v0 <= v => (*v1, spans),
            Some((v0, _, _)) => (*v0, &[]),
            None => (usize::MAX / 4, &[]),
        }
    }
}

impl Layer {
    pub fn new(
        x: i32,
        y: i32,
        w: usize,
        h: usize,
        how: How,
        pixels: Vec<u8>,
    ) -> Result<Layer, String> {
        if w == 0 || h == 0 || w > MOST_SIDE || h > MOST_SIDE {
            return Err(format!("a layer of {w}x{h}"));
        }
        if pixels.len() != w * h * how.channels() {
            return Err("a layer's pixels are not as many as it holds".into());
        }
        let part = match how {
            How::Solid => (0, 0, w, h),
            How::Over => bounds(&pixels, w, h, 4, |p| p[3] != 0),
            How::Add => bounds(&pixels, w, h, 3, |p| p != [0, 0, 0]),
        };
        Ok(Layer {
            x: i64::from(x),
            y: i64::from(y),
            w,
            how,
            pixels,
            part,
            ring: None,
            bar: None,
            sources: Vec::new(),
            globe: None,
            fill: None,
            cuts: None,
        })
    }

    /// A layer through its alpha whose `part` is known (cut from sources: Scene::view).
    fn cut(
        x: i32,
        y: i32,
        w: usize,
        h: usize,
        pixels: Vec<u8>,
        part: (usize, usize, usize, usize),
    ) -> Result<Layer, String> {
        if w == 0 || h == 0 || w > MOST_SIDE || h > MOST_SIDE || pixels.len() != w * h * 4 {
            return Err(format!("a layer of {w}x{h} and {} bytes", pixels.len()));
        }
        Ok(Layer {
            x: i64::from(x),
            y: i64::from(y),
            w,
            how: How::Over,
            pixels,
            part,
            ring: None,
            bar: None,
            sources: Vec::new(),
            globe: None,
            fill: None,
            cuts: None,
        })
    }

    /// A layer of `w` x `h` pixels all in the colour `rgba`, through its alpha: the inside of a
    /// card, whose edges and corners come as layers of their pixels round it (render/scene.py's
    /// Filled). It lies as a layer of its pixels would, and keeps none.
    pub fn filled(x: i32, y: i32, w: usize, h: usize, rgba: [u8; 4]) -> Result<Layer, String> {
        if w == 0 || h == 0 || w > MOST_SIDE || h > MOST_SIDE {
            return Err(format!("a layer of {w}x{h}"));
        }
        Ok(Layer {
            x: i64::from(x),
            y: i64::from(y),
            w,
            how: How::Over,
            pixels: Vec::new(),
            part: if rgba[3] == 0 {
                (0, 0, 0, 0)
            } else {
                (0, 0, w, h)
            },
            ring: None,
            bar: None,
            sources: Vec::new(),
            globe: None,
            fill: Some(rgba),
            cuts: None,
        })
    }

    /// Where it changes the frame (x0, y0, x1, y1 on it, not cut to it), or None.
    fn extent(&self) -> Option<(i64, i64, i64, i64)> {
        let (x0, y0, x1, y1) = self.part;
        (x1 > x0 && y1 > y0).then(|| {
            (
                self.x + x0 as i64,
                self.y + y0 as i64,
                self.x + x1 as i64,
                self.y + y1 as i64,
            )
        })
    }
}

/// The smallest box of a picture's pixels where `shows` (x0, y0, x1, y1), empty if none:
/// rows from the top and the bottom until one shows, and in those between only what lies
/// beyond the box found so far.
fn bounds(
    pixels: &[u8],
    w: usize,
    h: usize,
    n: usize,
    shows: impl Fn(&[u8]) -> bool,
) -> (usize, usize, usize, usize) {
    let row = |y: usize| &pixels[y * w * n..(y + 1) * w * n];
    let shown = |y: usize| row(y).chunks_exact(n).any(&shows);
    let Some(y0) = (0..h).find(|&y| shown(y)) else {
        return (0, 0, 0, 0);
    };
    let y1 = (y0..h).rev().find(|&y| shown(y)).map_or(y0 + 1, |y| y + 1);
    let (mut x0, mut x1) = (w, 0);
    for y in y0..y1 {
        let line = row(y);
        if let Some(first) = line.chunks_exact(n).take(x0).position(&shows) {
            x0 = first;
        }
        if let Some(last) = line.chunks_exact(n).skip(x1).rposition(&shows) {
            x1 += last + 1;
        }
    }
    (x0, y0, x1.max(x0), y1)
}

enum Ground {
    Colour([u8; 3]),
    Picture(Vec<u8>),
}

/// What lies under the layers that changed in a block, as it was composed last: the layers
/// in it (bottom first), and its pixels. While a frame's layers in the block begin with those,
/// it starts from there (Renderer._compose_region keeps what lies under the first changed
/// piece of a region the same way): the cards and the backdrop under a value that glides are
/// not laid again in every frame.
struct Under {
    below: Vec<u32>,
    pixels: Vec<u8>,
}

/// A layer of a frame as its blocks are composed (Scene::frame): its number, where it changes
/// the frame, whether it is new in the frame.
struct Lying {
    id: u32,
    cut: Option<Rect>,
    fresh: bool,
}

pub struct Scene {
    width: usize,
    height: usize,
    /// The blocks the damage is made of (yuv.rs's: it compares and converts in them).
    block: usize,
    cols: usize,
    rows: usize,
    ground: Ground,
    layers: HashMap<u32, Layer>,
    /// The frame before's layers, bottom first.
    order: Vec<u32>,
    /// It was turned half a turn (the panel is mounted upside down).
    half: bool,
    /// The next frame is composed whole: there was none yet, the ground is new, or one of
    /// Libre Panel's own frames came between.
    whole: bool,
    under: Vec<Option<Under>>,
    /// The rings whose pictures the engine makes for each value (ring.rs).
    rings: HashMap<u32, ring::Base>,
    /// The pictures layers are cut from (a graph's strip, its window cut for each frame; a
    /// globe's map and its air).
    sources: HashMap<u32, Source>,
    /// The globes whose pictures the engine makes for each step of their turn (globe.rs).
    globes: HashMap<u32, globe::Tables>,
    /// The lit bars whose pictures and glows the engine makes for each value (bar.rs).
    bars: HashMap<u32, bar::Base>,
    /// The fans' rotors whose positions the engine makes (fan.rs), the one used last at the
    /// end: kept for the stream, the [`MOST_ROTORS`] used last.
    rotors: Vec<(u32, fan::Rotor)>,
}

pub type Rect = (usize, usize, usize, usize);

impl Scene {
    pub fn new(width: usize, height: usize, block: usize) -> Scene {
        let block = block.max(1);
        let (cols, rows) = (width.div_ceil(block), height.div_ceil(block));
        Scene {
            width,
            height,
            block,
            cols,
            rows,
            ground: Ground::Colour([0, 0, 0]),
            layers: HashMap::new(),
            order: Vec::new(),
            half: false,
            whole: true,
            under: (0..cols * rows).map(|_| None).collect(),
            rings: HashMap::new(),
            sources: HashMap::new(),
            globes: HashMap::new(),
            bars: HashMap::new(),
            rotors: Vec::new(),
        }
    }

    fn forget_unders(&mut self) {
        self.under.iter_mut().for_each(|u| *u = None);
    }

    pub fn ground_colour(&mut self, rgb: [u8; 3]) {
        self.ground = Ground::Colour(rgb);
        self.whole = true;
        self.forget_unders();
    }

    pub fn ground_picture(&mut self, pixels: Vec<u8>) -> Result<(), String> {
        if pixels.len() != self.width * self.height * 3 {
            return Err("the ground is not the frame's size".into());
        }
        self.ground = Ground::Picture(pixels);
        self.whole = true;
        self.forget_unders();
        Ok(())
    }

    /// A layer for the frames to come. Libre Panel numbers each layer anew; one that takes the
    /// number of another makes the next frame whole (what that one changed is not known).
    pub fn layer(&mut self, id: u32, layer: Layer) {
        if self.layers.insert(id, layer).is_some() {
            self.whole = true;
            self.forget_unders();
        }
    }

    /// A layer all in one colour (Layer::filled), for the frames to come.
    pub fn fill(
        &mut self,
        id: u32,
        (x, y, w, h): (i32, i32, usize, usize),
        rgba: [u8; 4],
    ) -> Result<(), String> {
        let layer = Layer::filled(x, y, w, h, rgba)?;
        self.layer(id, layer);
        Ok(())
    }

    /// A ring whose pictures are made here for its values (ring.rs), for the frames to come.
    pub fn ring_base(&mut self, id: u32, base: ring::Base) {
        self.rings.insert(id, base);
    }

    /// The picture of ring `ring` at `frac` of its sweep, as layer `id` at (x, y).
    pub fn ring(&mut self, id: u32, ring: u32, x: i32, y: i32, frac: f64) -> Result<(), String> {
        let base = self
            .rings
            .get_mut(&ring)
            .ok_or_else(|| format!("layer {id} of ring {ring}, which never came"))?;
        let side = base.side();
        let mut layer = Layer::new(x, y, side, side, How::Over, base.picture(frac))?;
        layer.ring = Some(ring);
        self.layer(id, layer);
        Ok(())
    }

    /// A lit bar whose pictures and glows are made here for its values (bar.rs), for the
    /// frames to come.
    pub fn bar_base(&mut self, id: u32, base: bar::Base) {
        self.bars.insert(id, base);
    }

    /// The picture of lit bar `bar` with its lit `spans` in `paint`, as layer `id` at (x, y),
    /// and its glow as layer `light` (0: none), light added over its area; a glow of nothing
    /// (none lit, or none in its area) is a layer that lays nothing.
    pub fn bar(
        &mut self,
        (id, light): (u32, u32),
        bar: u32,
        (x, y): (i32, i32),
        paint: &bar::Paint,
        spans: &[(f64, f64)],
    ) -> Result<(), String> {
        let base = self
            .bars
            .get_mut(&bar)
            .ok_or_else(|| format!("layer {id} of bar {bar}, which never came"))?;
        let (w, h) = base.size();
        let picture = base.picture(spans, paint);
        let glow = (light != 0).then(|| base.glow(spans, paint));
        let mut layer = Layer::new(x, y, w, h, How::Over, picture)?;
        layer.bar = Some(bar);
        self.layer(id, layer);
        if let Some(glow) = glow {
            let mut layer = match glow {
                Some(g) => Layer::new(g.x, g.y, g.w, g.h, How::Add, g.rgb)?,
                None => Layer::new(x, y, 1, 1, How::Add, vec![0; 3])?,
            };
            layer.bar = Some(bar);
            self.layer(light, layer);
        }
        Ok(())
    }

    /// A picture to cut layers from (RGBA, `w` x `h`), for the frames to come.
    pub fn source(&mut self, id: u32, w: usize, h: usize, pixels: Vec<u8>) -> Result<(), String> {
        if w == 0 || h == 0 || w > MOST_SOURCE || h > MOST_SOURCE || pixels.len() != w * h * 4 {
            return Err(format!("a source of {w}x{h} and {} bytes", pixels.len()));
        }
        self.sources.insert(id, Source::new(w, h, pixels));
        Ok(())
    }

    /// A rotor whose positions sources are made of (fan.rs), for the stream: the
    /// [`MOST_ROTORS`] used last are kept.
    pub fn rotor(&mut self, id: u32, rotor: fan::Rotor) {
        self.rotors.retain(|(kept, _)| *kept != id);
        self.rotors.push((id, rotor));
        if self.rotors.len() > MOST_ROTORS {
            self.rotors.remove(0);
        }
    }

    /// Source `id`: rotor `rotor` at a position in its colours, blurred and dimmed (fan.rs's
    /// Rotor::blades), made here; the rotor is the one used last now.
    pub fn blades(&mut self, id: u32, rotor: u32, blades: &fan::Blades) -> Result<(), String> {
        let at = self
            .rotors
            .iter()
            .position(|(kept, _)| *kept == rotor)
            .ok_or_else(|| format!("blades {id} of rotor {rotor}, which is not kept"))?;
        let used = self.rotors.remove(at);
        self.rotors.push(used);
        let made = &mut self.rotors.last_mut().expect("the rotor just used").1;
        let k = made.side();
        let pixels = made.blades(blades);
        self.sources.insert(id, Source::new(k, k, pixels));
        Ok(())
    }

    /// Source `id`: a fan's ring with its glowing arc (fan.rs's ring), made here.
    pub fn fan_ring(&mut self, id: u32, spec: &fan::RingSpec) -> Result<(), String> {
        let pixels = fan::ring(spec)?;
        let side = spec.canvas;
        self.sources.insert(id, Source::new(side, side, pixels));
        Ok(())
    }

    /// Source `id`, `w` x `h`, grown from source `from`: its first `keep` columns those of
    /// `from` from column `shift` on, the rest `rest` (RGBA, row by row): a graph's strip grown
    /// from the one before, which a layer of the frame before is cut from (render/renderer.py's
    /// _grown_strip). The same source as one that came with all its pixels.
    pub fn grown(
        &mut self,
        id: u32,
        (w, h): (usize, usize),
        from: u32,
        (shift, keep): (usize, usize),
        rest: &[u8],
    ) -> Result<(), String> {
        let base = self
            .sources
            .get(&from)
            .ok_or_else(|| format!("source {id} grown from source {from}, which never came"))?;
        if w == 0 || h == 0 || w > MOST_SOURCE || h > MOST_SOURCE || keep > w {
            return Err(format!("a grown source of {w}x{h}"));
        }
        if base.h != h || shift.checked_add(keep).is_none_or(|end| end > base.w) {
            return Err(format!(
                "source {id} grown from {keep} columns of source {from} ({}x{}) at {shift}",
                base.w, base.h
            ));
        }
        if rest.len() != (w - keep) * h * 4 {
            return Err(format!("a grown source's rest of {} bytes", rest.len()));
        }
        let (line, tail) = (base.w * 4, (w - keep) * 4);
        let mut pixels = Vec::with_capacity(w * h * 4);
        for y in 0..h {
            let at = y * line + shift * 4;
            pixels.extend_from_slice(&base.pixels[at..at + keep * 4]);
            pixels.extend_from_slice(&rest[y * tail..(y + 1) * tail]);
        }
        self.source(id, w, h, pixels)
    }

    /// Layer `id` at (x, y), `w` x `h` RGBA through its alpha, cut from sources: transparent
    /// where no part lies, each part pasted over those before it, nothing where it lies off its
    /// source (as Pillow crops a picture, also beyond its edges, and pastes the crop). It keeps
    /// no pixels: it is laid from the sources' (Cuts), which stay while it does.
    pub fn view(
        &mut self,
        id: u32,
        (x, y, w, h): (i32, i32, usize, usize),
        parts: &[Part],
    ) -> Result<(), String> {
        if w == 0 || h == 0 || w > MOST_SIDE || h > MOST_SIDE {
            return Err(format!("a layer of {w}x{h}"));
        }
        let mut sources = Vec::with_capacity(parts.len());
        let (mut px0, mut py0, mut px1, mut py1) = (w, h, 0, 0);
        for part in parts {
            let source = self.sources.get(&part.source).ok_or_else(|| {
                format!(
                    "layer {id} cut from source {}, which never came",
                    part.source
                )
            })?;
            let (sx, sy) = (i64::from(part.sx), i64::from(part.sy));
            let (dx, dy) = (i64::from(part.dx), i64::from(part.dy));
            // the part where it lies on the layer, and of that where it lies on its source
            // (u, v from the part's corner)
            let (u0, v0) = ((-dx).max(0), (-dy).max(0));
            let u1 = (part.w as i64).min(w as i64 - dx);
            let v1 = (part.h as i64).min(h as i64 - dy);
            if u1 <= u0 || v1 <= v0 {
                continue;
            }
            let (s0, s1) = (u0.max(-sx), u1.min(source.w as i64 - sx));
            for v in v0..v1 {
                let sv = sy + v;
                if !(0..source.h as i64).contains(&sv) || s1 <= s0 {
                    continue;
                }
                // where it shows on this row of the layer: the source's shown columns in it
                let (first, after) = source.shown[sv as usize];
                let a = (first as i64 - sx).max(s0);
                let b = (after as i64 - sx).min(s1);
                if a < b {
                    let row = (dy + v) as usize;
                    px0 = px0.min((dx + a) as usize);
                    px1 = px1.max((dx + b) as usize);
                    py0 = py0.min(row);
                    py1 = py1.max(row + 1);
                }
            }
            if !sources.contains(&part.source) {
                sources.push(part.source);
            }
        }
        let part = if px1 > px0 && py1 > py0 {
            (px0, py0, px1, py1)
        } else {
            (0, 0, 0, 0)
        };
        let layer = Layer {
            x: i64::from(x),
            y: i64::from(y),
            w,
            how: How::Over,
            pixels: Vec::new(),
            part,
            ring: None,
            bar: None,
            sources,
            globe: None,
            fill: None,
            cuts: Some(Box::new(Cuts::of(parts, w, h))),
        };
        self.layer(id, layer);
        Ok(())
    }

    /// Layer `id` at (x, y), `w` x `h` RGBA through its alpha, laid from sources: each one over
    /// those before it on a clear picture, as Pillow's alpha_composite lays it (a fan's ring and
    /// its rotor over it), nothing where it lies off the layer. The sources in `kept` stay with
    /// the layer too, unused in it (a turning rotor's positions, each sent once).
    pub fn laid(
        &mut self,
        id: u32,
        (x, y, w, h): (i32, i32, usize, usize),
        parts: &[Placed],
        kept: &[u32],
    ) -> Result<(), String> {
        if w == 0 || h == 0 || w > MOST_SIDE || h > MOST_SIDE {
            return Err(format!("a layer of {w}x{h}"));
        }
        let mut pixels = vec![0u8; w * h * 4];
        let mut sources: Vec<u32> = Vec::with_capacity(parts.len() + kept.len());
        let mut seen: HashSet<u32> = HashSet::with_capacity(parts.len() + kept.len());
        let (mut px0, mut py0, mut px1, mut py1) = (w, h, 0, 0);
        for (n, part) in parts.iter().enumerate() {
            let source = self.sources.get(&part.source).ok_or_else(|| {
                format!(
                    "layer {id} laid from source {}, which never came",
                    part.source
                )
            })?;
            let (dx, dy) = (i64::from(part.dx), i64::from(part.dy));
            // where it lies on the layer, from its own corner (u across, v down)
            let (u0, v0) = ((-dx).max(0), (-dy).max(0));
            let u1 = (source.w as i64).min(w as i64 - dx);
            let v1 = (source.h as i64).min(h as i64 - dy);
            for v in v0..v1.max(v0) {
                // (where it is clear it leaves what lies under it as it is: its shown columns)
                let (first, after) = source.shown[v as usize];
                let (a, b) = ((first as i64).max(u0), (after as i64).min(u1));
                if a >= b {
                    continue;
                }
                let len = (b - a) as usize * 4;
                let from = (v as usize * source.w + a as usize) * 4;
                let to = ((dy + v) as usize * w + (dx + a) as usize) * 4;
                let (out, src) = (&mut pixels[to..to + len], &source.pixels[from..from + len]);
                if n == 0 {
                    // on what is all clear: the part itself where anything of it shows (its
                    // clear pixels keep their colour, which no frame shows)
                    out.copy_from_slice(src);
                } else {
                    composite(out, src);
                }
                px0 = px0.min((dx + a) as usize);
                px1 = px1.max((dx + b) as usize);
                py0 = py0.min((dy + v) as usize);
                py1 = py1.max((dy + v) as usize + 1);
            }
            if seen.insert(part.source) {
                sources.push(part.source);
            }
        }
        for &source in kept {
            if !self.sources.contains_key(&source) {
                return Err(format!(
                    "layer {id} keeps source {source}, which never came"
                ));
            }
            if seen.insert(source) {
                sources.push(source);
            }
        }
        let part = if px1 > px0 && py1 > py0 {
            (px0, py0, px1, py1)
        } else {
            (0, 0, 0, 0)
        };
        let mut layer = Layer::cut(x, y, w, h, pixels, part)?;
        layer.sources = sources;
        self.layer(id, layer);
        Ok(())
    }

    /// A globe whose pictures are made here for each step of its turn (globe.rs).
    pub fn globe(&mut self, id: u32, tables: globe::Tables) {
        self.globes.insert(id, tables);
    }

    /// The picture of globe `globe` at `step` of its turn, as layer `id` at (x, y): its map and
    /// air (sources; air 0 for none), its mark (pixels, w, h, where on the globe) over them.
    pub fn globe_layer(
        &mut self,
        id: u32,
        globe: u32,
        (map, air, step): (u32, u32, u32),
        (x, y): (i32, i32),
        mark: Option<(Vec<u8>, usize, usize, i32, i32)>,
    ) -> Result<(), String> {
        let tables = self
            .globes
            .get(&globe)
            .ok_or_else(|| format!("layer {id} of globe {globe}, which never came"))?;
        let source = |n: u32| {
            self.sources
                .get(&n)
                .ok_or_else(|| format!("layer {id} of a globe with source {n}, which never came"))
        };
        let map_pixels = &source(map)?.pixels;
        let air_pixels = if air == 0 {
            None
        } else {
            Some(source(air)?.pixels.as_slice())
        };
        let mark = mark
            .as_ref()
            .map(|(p, w, h, mx, my)| (p.as_slice(), *w, *h, i64::from(*mx), i64::from(*my)));
        let side = tables.size();
        let pixels = tables.picture(map_pixels, step as usize, air_pixels, mark)?;
        let mut layer = Layer::new(x, y, side, side, How::Over, pixels)?;
        layer.globe = Some(globe);
        layer.sources = if air == 0 { vec![map] } else { vec![map, air] };
        self.layer(id, layer);
        Ok(())
    }

    /// One of Libre Panel's own frames went to the panel: the next of the scene's is whole.
    pub fn frame_between(&mut self) {
        self.whole = true;
    }

    /// The frame of the layers in `order` (bottom first), turned half a turn with `half`: the
    /// boxes of it composed anew, as yuv.rs takes them (in the turned frame). Layers not in
    /// it are forgotten.
    pub fn frame(&mut self, order: Vec<u32>, half: bool) -> Result<Boxes, String> {
        if let Some(id) = order.iter().find(|id| !self.layers.contains_key(id)) {
            return Err(format!("a frame with layer {id}, which never came"));
        }
        let marked = if self.whole || half != self.half {
            Some(vec![true; self.cols * self.rows])
        } else {
            self.damage(&order)
        };
        let old: HashSet<u32> = self.order.iter().copied().collect();
        let fresh: HashSet<u32> = order
            .iter()
            .copied()
            .filter(|id| !old.contains(id))
            .collect();
        self.order = order;
        self.half = half;
        self.whole = false;
        let mut boxes = Vec::new();
        if let Some(marked) = marked {
            // the frame's layers as its blocks are composed (worked out once, not for each
            // block), and for each row of blocks those that reach into it
            let lying: Vec<Lying> = self
                .order
                .iter()
                .map(|id| {
                    let layer = &self.layers[id];
                    Lying {
                        id: *id,
                        cut: self.cut(layer),
                        fresh: fresh.contains(id),
                    }
                })
                .collect();
            let mut reach: Vec<Vec<usize>> = vec![Vec::new(); self.rows];
            for (k, l) in lying.iter().enumerate() {
                if let Some((_, y0, _, y1)) = l.cut {
                    for row in reach
                        .iter_mut()
                        .take(y1.div_ceil(self.block))
                        .skip(y0 / self.block)
                    {
                        row.push(k);
                    }
                }
            }
            for (x0, y0, x1, y1) in self.runs(&marked) {
                let mut pixels = vec![0u8; (x1 - x0) * (y1 - y0) * 3];
                let (r0, r1) = (y0 / self.block, y1.div_ceil(self.block));
                for (r, layers) in reach.iter().enumerate().take(r1).skip(r0) {
                    for c in x0 / self.block..x1.div_ceil(self.block) {
                        self.compose_block(r, c, layers, &lying, &mut pixels, (x0, y0, x1));
                    }
                }
                if half {
                    reverse(&mut pixels);
                    let (w, h) = (self.width, self.height);
                    boxes.push((w - x1, h - y1, w - x0, h - y0, pixels));
                } else {
                    boxes.push((x0, y0, x1, y1, pixels));
                }
            }
        }
        let keep: HashSet<u32> = self.order.iter().copied().collect();
        self.layers.retain(|id, _| keep.contains(id));
        let rings: HashSet<u32> = self.layers.values().filter_map(|l| l.ring).collect();
        self.rings.retain(|id, _| rings.contains(id));
        let sources: HashSet<u32> = self
            .layers
            .values()
            .flat_map(|l| l.sources.clone())
            .collect();
        self.sources.retain(|id, _| sources.contains(id));
        let globes: HashSet<u32> = self.layers.values().filter_map(|l| l.globe).collect();
        self.globes.retain(|id, _| globes.contains(id));
        let bars: HashSet<u32> = self.layers.values().filter_map(|l| l.bar).collect();
        self.bars.retain(|id, _| bars.contains(id));
        Ok(boxes)
    }

    /// Where a layer changes the frame, cut to it (x0, y0, x1, y1), or None.
    fn cut(&self, layer: &Layer) -> Option<Rect> {
        let (x0, y0, x1, y1) = layer.extent()?;
        let (x0, y0) = (x0.max(0) as usize, y0.max(0) as usize);
        let x1 = x1.clamp(0, self.width as i64) as usize;
        let y1 = y1.clamp(0, self.height as i64) as usize;
        (x1 > x0 && y1 > y0).then_some((x0, y0, x1, y1))
    }

    /// The blocks that changed since the frame before: where layers came or went (None: all of
    /// them, where the ones that stayed lie in another order).
    fn damage(&self, order: &[u32]) -> Option<Vec<bool>> {
        let new: HashSet<u32> = order.iter().copied().collect();
        let old: HashSet<u32> = self.order.iter().copied().collect();
        let stayed_old = self.order.iter().filter(|id| new.contains(id));
        let stayed_new = order.iter().filter(|id| old.contains(id));
        if !stayed_old.eq(stayed_new) {
            // (also a layer twice in one of them, as many times as in the other)
            return Some(vec![true; self.cols * self.rows]);
        }
        let mut marked = vec![false; self.cols * self.rows];
        let changed = self
            .order
            .iter()
            .filter(|id| !new.contains(id))
            .chain(order.iter().filter(|id| !old.contains(id)));
        for id in changed {
            // (a layer that went is still kept: it goes after this frame)
            let Some((x0, y0, x1, y1)) = self.layers.get(id).and_then(|l| self.cut(l)) else {
                continue;
            };
            for r in y0 / self.block..y1.div_ceil(self.block) {
                for c in x0 / self.block..x1.div_ceil(self.block) {
                    marked[r * self.cols + c] = true;
                }
            }
        }
        Some(marked)
    }

    /// The marked blocks as boxes: runs of them along each row of blocks, one box down as far
    /// as the same run goes on in the rows below.
    fn runs(&self, marked: &[bool]) -> Vec<Rect> {
        let (cols, block) = (self.cols, self.block);
        let mut boxes: Vec<Rect> = Vec::new();
        let mut open: HashMap<(usize, usize), usize> = HashMap::new(); // (c0, c1) -> box
        for r in 0..self.rows {
            let mut still = HashMap::new();
            let y1 = ((r + 1) * block).min(self.height);
            let mut c = 0;
            while c < cols {
                if !marked[r * cols + c] {
                    c += 1;
                    continue;
                }
                let start = c;
                while c < cols && marked[r * cols + c] {
                    c += 1;
                }
                let run = (start, c);
                match open.get(&run) {
                    Some(&i) => {
                        boxes[i].3 = y1;
                        still.insert(run, i);
                    }
                    None => {
                        let x1 = (c * block).min(self.width);
                        boxes.push((start * block, r * block, x1, y1));
                        still.insert(run, boxes.len() - 1);
                    }
                }
            }
            open = still;
        }
        boxes
    }

    /// The block at row `r`, column `c`: its ground with the layers laid on it in turn (of
    /// `reach`, those of the frame that reach into its row; `fresh`, those new in this frame),
    /// into `out`, the pixels of the box (x0, y0)-(x1, ..) it lies in.
    fn compose_block(
        &mut self,
        r: usize,
        c: usize,
        reach: &[usize],
        lying: &[Lying],
        out: &mut [u8],
        (bx0, by0, bx1): (usize, usize, usize),
    ) {
        let (x0, y0) = (c * self.block, r * self.block);
        let (x1, y1) = (
            ((c + 1) * self.block).min(self.width),
            ((r + 1) * self.block).min(self.height),
        );
        let (w, h) = (x1 - x0, y1 - y0);
        let index = r * self.cols + c;
        let over: Vec<usize> = reach
            .iter()
            .copied()
            .filter(|&k| {
                lying[k].cut.is_some_and(|(lx0, ly0, lx1, ly1)| {
                    lx0 < x1 && lx1 > x0 && ly0 < y1 && ly1 > y0
                })
            })
            .collect();
        let ids: Vec<u32> = over.iter().map(|&k| lying[k].id).collect();
        let start = match &self.under[index] {
            Some(u) if u.pixels.len() == w * h * 3 && ids.starts_with(&u.below) => u.below.len(),
            _ => 0,
        };
        let mut pixels = match (&self.under[index], start) {
            (Some(u), s) if s > 0 => u.pixels.clone(),
            _ => self.ground_of((x0, y0, x1, y1)),
        };
        // (below the first layer new in this frame: kept for the frames after)
        let new_at = over
            .iter()
            .position(|&k| lying[k].fresh)
            .unwrap_or(over.len());
        for (j, &k) in over.iter().enumerate().skip(start) {
            if j == new_at && j > start {
                self.under[index] = Some(Under {
                    below: ids[..j].to_vec(),
                    pixels: pixels.clone(),
                });
            }
            lay(
                &self.layers[&lying[k].id],
                &self.sources,
                &mut pixels,
                (x0, y0, x1, y1),
            );
        }
        if new_at == over.len() && over.len() > start {
            self.under[index] = Some(Under {
                below: ids,
                pixels: pixels.clone(),
            });
        }
        let wide = bx1 - bx0;
        for (row, line) in pixels.chunks_exact(w * 3).enumerate() {
            let at = ((y0 - by0 + row) * wide + (x0 - bx0)) * 3;
            out[at..at + w * 3].copy_from_slice(line);
        }
    }

    /// The ground of the box (x0, y0)-(x1, y1), three bytes a pixel, row by row.
    fn ground_of(&self, (x0, y0, x1, y1): Rect) -> Vec<u8> {
        let w = x1 - x0;
        match &self.ground {
            Ground::Colour(rgb) => rgb.repeat(w * (y1 - y0)),
            Ground::Picture(pixels) => {
                let mut out = Vec::with_capacity(w * (y1 - y0) * 3);
                for y in y0..y1 {
                    let at = (y * self.width + x0) * 3;
                    out.extend_from_slice(&pixels[at..at + w * 3]);
                }
                out
            }
        }
    }
}

/// `layer` laid on `out`, the pixels of the box (x0, y0)-(x1, y1) of the frame.
fn lay(layer: &Layer, sources: &HashMap<u32, Source>, out: &mut [u8], (x0, y0, x1, y1): Rect) {
    let (px0, py0, px1, py1) = layer.part;
    // the part of the layer in the box, on the frame
    let lx0 = (layer.x + px0 as i64).max(x0 as i64);
    let ly0 = (layer.y + py0 as i64).max(y0 as i64);
    let lx1 = (layer.x + px1 as i64).min(x1 as i64);
    let ly1 = (layer.y + py1 as i64).min(y1 as i64);
    if lx1 <= lx0 || ly1 <= ly0 {
        return;
    }
    let n = layer.how.channels();
    let span = (lx1 - lx0) as usize;
    let w = x1 - x0;
    if let Some(colour) = layer.fill {
        for fy in ly0..ly1 {
            let dst_at = ((fy as usize - y0) * w + (lx0 as usize - x0)) * 3;
            over_colour(&mut out[dst_at..dst_at + span * 3], colour);
        }
        return;
    }
    if let Some(cuts) = &layer.cuts {
        // band by band of the layer's rows in the box, span by span of each
        let mut fy = ly0;
        while fy < ly1 {
            let (rows, spans) = cuts.rows((fy - layer.y) as usize);
            let end = (layer.y + rows as i64).min(ly1);
            for &(u0, u1, k) in spans {
                // the span on the frame, in the box; of that, where its part lies on its source
                let part = &cuts.parts[k];
                let Some(source) = sources.get(&part.source) else {
                    continue; // (kept while the layer is: Scene::frame)
                };
                let off = i64::from(part.sx) - i64::from(part.dx) - layer.x; // (frame to source)
                let a = (layer.x + u0 as i64).max(lx0).max(-off);
                let b = (layer.x + u1 as i64).min(lx1).min(source.w as i64 - off);
                // its rows on its source (frame row to source row)
                let down = i64::from(part.sy) - i64::from(part.dy) - layer.y;
                let (r0, r1) = (fy.max(-down), end.min(source.h as i64 - down));
                if a >= b || r0 >= r1 {
                    continue;
                }
                let len = (b - a) as usize;
                for ry in r0..r1 {
                    let from = ((ry + down) as usize * source.w + (a + off) as usize) * 4;
                    let at = ((ry as usize - y0) * w + (a as usize - x0)) * 3;
                    over(
                        &mut out[at..at + len * 3],
                        &source.pixels[from..from + len * 4],
                    );
                }
            }
            fy = end;
        }
        return;
    }
    for fy in ly0..ly1 {
        let src_at = (((fy - layer.y) as usize) * layer.w + (lx0 - layer.x) as usize) * n;
        let src = &layer.pixels[src_at..src_at + span * n];
        let dst_at = ((fy as usize - y0) * w + (lx0 as usize - x0)) * 3;
        let dst = &mut out[dst_at..dst_at + span * 3];
        match layer.how {
            How::Over => over(dst, src),
            How::Solid => dst.copy_from_slice(src),
            How::Add => add(dst, src),
        }
    }
}

/// RGBA pixels pasted through their alpha onto RGB ones, as Pillow does (paste_mask_RGBA):
/// each channel (out (255 - a) + in a + 128), divided by 255 the way Pillow rounds. Four at a
/// time where the processor has SSSE3, the same bytes: in 16-bit lanes the sums fit (65,153
/// at most, and 65,407 once the division adds its share), and a clear pixel gives what lay
/// there and an opaque one itself, so no pixel needs a way of its own.
fn over(dst: &mut [u8], src: &[u8]) {
    #[cfg(target_arch = "x86_64")]
    if std::arch::is_x86_feature_detected!("ssse3") {
        // SAFETY: the processor has SSSE3 (just asked)
        let done = unsafe { over_ssse3(dst, src) };
        over_one_by_one(&mut dst[done * 3..], &src[done * 4..]);
        return;
    }
    over_one_by_one(dst, src);
}

/// :func:`over`, four pixels at a time: how many it laid (a multiple of four; the rest, at the
/// end, for the caller).
#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "ssse3")]
unsafe fn over_ssse3(dst: &mut [u8], src: &[u8]) -> usize {
    use std::arch::x86_64::*;
    let quads = (dst.len() / 3).min(src.len() / 4) / 4;
    let to_rgbx = _mm_setr_epi8(0, 1, 2, -1, 3, 4, 5, -1, 6, 7, 8, -1, 9, 10, 11, -1);
    let to_rgb = _mm_setr_epi8(0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13, 14, -1, -1, -1, -1);
    let zero = _mm_setzero_si128();
    let (full, half) = (_mm_set1_epi16(255), _mm_set1_epi16(128));
    let lay = |d: __m128i, s: __m128i| {
        // (each pixel's alpha in its four lanes: the fourth, dropped, is the alpha's own sum)
        let a = _mm_shufflehi_epi16::<0xFF>(_mm_shufflelo_epi16::<0xFF>(s));
        let t = _mm_add_epi16(
            _mm_add_epi16(
                _mm_mullo_epi16(d, _mm_sub_epi16(full, a)),
                _mm_mullo_epi16(s, a),
            ),
            half,
        );
        _mm_srli_epi16::<8>(_mm_add_epi16(t, _mm_srli_epi16::<8>(t)))
    };
    for q in 0..quads {
        // SAFETY: q < quads: the four pixels' 16 bytes of `src` and 12 of `dst` are in them
        let (s, at) = (src.as_ptr().add(q * 16), dst.as_mut_ptr().add(q * 12));
        let s = _mm_loadu_si128(s.cast());
        let d = _mm_unpacklo_epi64(
            _mm_loadl_epi64(at.cast()),
            _mm_cvtsi32_si128(at.add(8).cast::<i32>().read_unaligned()),
        );
        let d = _mm_shuffle_epi8(d, to_rgbx);
        let low = lay(_mm_unpacklo_epi8(d, zero), _mm_unpacklo_epi8(s, zero));
        let high = lay(_mm_unpackhi_epi8(d, zero), _mm_unpackhi_epi8(s, zero));
        let out = _mm_shuffle_epi8(_mm_packus_epi16(low, high), to_rgb);
        _mm_storel_epi64(at.cast(), out);
        at.add(8)
            .cast::<i32>()
            .write_unaligned(_mm_cvtsi128_si32(_mm_srli_si128::<8>(out)));
    }
    quads * 4
}

/// :func:`over`, a pixel at a time.
fn over_one_by_one(dst: &mut [u8], src: &[u8]) {
    for (d, s) in dst.chunks_exact_mut(3).zip(src.chunks_exact(4)) {
        let a = u32::from(s[3]);
        if a == 0 {
            continue;
        }
        if a == 255 {
            d.copy_from_slice(&s[..3]);
            continue;
        }
        for c in 0..3 {
            let t = u32::from(d[c]) * (255 - a) + u32::from(s[c]) * a + 128;
            d[c] = (((t >> 8) + t) >> 8) as u8;
        }
    }
}

/// RGBA pixels `src` laid over RGBA pixels `dst` (straight alpha) as Pillow's alpha_composite
/// lays them, to the bit (ring.rs's alpha_composite): where `src` is clear, `dst` stays; where
/// it is opaque, or `dst` clear, Pillow's sums give `src` itself.
fn composite(dst: &mut [u8], src: &[u8]) {
    for (d, s) in dst.chunks_exact_mut(4).zip(src.chunks_exact(4)) {
        match (s[3], d[3]) {
            (0, _) => {}
            (255, _) | (_, 0) => d.copy_from_slice(s),
            _ => ring::alpha_composite(d, &[s[0], s[1], s[2], s[3]]),
        }
    }
}

/// One colour (RGBA) pasted through its alpha onto RGB pixels, as `over` pastes a layer of it.
fn over_colour(dst: &mut [u8], s: [u8; 4]) {
    let a = u32::from(s[3]);
    if a == 0 {
        return;
    }
    if a == 255 {
        for d in dst.chunks_exact_mut(3) {
            d.copy_from_slice(&s[..3]);
        }
        return;
    }
    let lit = [
        u32::from(s[0]) * a + 128,
        u32::from(s[1]) * a + 128,
        u32::from(s[2]) * a + 128,
    ];
    for d in dst.chunks_exact_mut(3) {
        for c in 0..3 {
            let t = u32::from(d[c]) * (255 - a) + lit[c];
            d[c] = (((t >> 8) + t) >> 8) as u8;
        }
    }
}

/// RGB light added onto RGB pixels, as ImageChops.add: the sum, at most 255.
fn add(dst: &mut [u8], src: &[u8]) {
    for (d, s) in dst.iter_mut().zip(src) {
        *d = d.saturating_add(*s);
    }
}

/// The pixels (three bytes each) in reverse order: the box turned half a turn.
fn reverse(pixels: &mut [u8]) {
    let n = pixels.len() / 3;
    for i in 0..n / 2 {
        let j = n - 1 - i;
        for c in 0..3 {
            pixels.swap(i * 3 + c, j * 3 + c);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    impl Scene {
        /// Layer `id`'s pixels, RGBA row by row (`h` rows): those it keeps, or of one cut from
        /// sources, which keeps none, its parts pasted in turn on a clear picture, each crop
        /// clear where it lies off its source (as Pillow crops and pastes).
        fn picture_of(&self, id: u32, h: usize) -> Vec<u8> {
            let layer = &self.layers[&id];
            let Some(cuts) = &layer.cuts else {
                return layer.pixels.clone();
            };
            let w = layer.w;
            let mut out = vec![0u8; w * h * 4];
            for p in &cuts.parts {
                let source = &self.sources[&p.source];
                for v in 0..p.h as i64 {
                    let ly = i64::from(p.dy) + v;
                    if !(0..h as i64).contains(&ly) {
                        continue;
                    }
                    for u in 0..p.w as i64 {
                        let lx = i64::from(p.dx) + u;
                        if !(0..w as i64).contains(&lx) {
                            continue;
                        }
                        let (sx, sy) = (i64::from(p.sx) + u, i64::from(p.sy) + v);
                        let at = (ly as usize * w + lx as usize) * 4;
                        let on = (0..source.w as i64).contains(&sx)
                            && (0..source.h as i64).contains(&sy);
                        let pixel = if on {
                            let from = (sy as usize * source.w + sx as usize) * 4;
                            [
                                source.pixels[from],
                                source.pixels[from + 1],
                                source.pixels[from + 2],
                                source.pixels[from + 3],
                            ]
                        } else {
                            [0; 4]
                        };
                        out[at..at + 4].copy_from_slice(&pixel);
                    }
                }
            }
            out
        }
    }

    fn rgba(w: usize, h: usize, p: [u8; 4]) -> Vec<u8> {
        p.repeat(w * h)
    }

    #[test]
    fn a_paste_through_alpha_and_light_added_are_pillows() {
        // (under, over, alpha, Pillow's result): paste(layer, box, layer) onto RGB
        for (under, top, a, want) in [
            (200u8, 50u8, 128u8, 125u8),
            (0, 255, 1, 1),
            (255, 0, 254, 1),
            (17, 230, 77, 81),
            (128, 128, 128, 128),
            (90, 10, 200, 27),
        ] {
            let mut d = [under; 3];
            over(&mut d, &[top, top, top, a]);
            assert_eq!(d, [want; 3], "{under} under {top} at {a}");
        }
        for (a, b, want) in [
            (200u8, 100u8, 255u8),
            (10, 20, 30),
            (255, 1, 255),
            (0, 0, 0),
        ] {
            let mut d = [a; 3];
            add(&mut d, &[b; 3]);
            assert_eq!(d, [want; 3]);
        }
    }

    #[test]
    fn a_layer_of_one_colour_lies_as_a_layer_of_its_pixels() {
        // (over a ground and a layer under it, partly off the frame, across its blocks)
        for colour in [
            [12, 18, 24, 178],
            [200, 10, 90, 255],
            [5, 5, 5, 0],
            [250, 240, 230, 1],
        ] {
            let (w, h) = (37, 23);
            let mut frames = Vec::new();
            for filled in [false, true] {
                let mut s = Scene::new(48, 32, 8);
                s.ground_colour([40, 50, 60]);
                let under: Vec<u8> = (0..20 * 20 * 4).map(|i| (i * 37 % 251) as u8).collect();
                s.layer(1, Layer::new(3, 2, 20, 20, How::Over, under).unwrap());
                if filled {
                    s.fill(2, (-4, 13, w, h), colour).unwrap();
                } else {
                    s.layer(
                        2,
                        Layer::new(-4, 13, w, h, How::Over, rgba(w, h, colour)).unwrap(),
                    );
                }
                frames.push(s.frame(vec![1, 2], false).unwrap());
                // (and in a frame after, from what lay under what came)
                let top: Vec<u8> = (0..6 * 6 * 4).map(|i| (i * 11 % 256) as u8).collect();
                s.layer(3, Layer::new(9, 15, 6, 6, How::Over, top).unwrap());
                frames.push(s.frame(vec![1, 2, 3], false).unwrap());
            }
            assert!(
                frames[0] == frames[2] && frames[1] == frames[3],
                "{colour:?}"
            );
        }
        assert!(Layer::filled(0, 0, 0, 4, [1, 2, 3, 4]).is_err());
    }

    #[test]
    fn layers_lie_in_turn_on_the_ground_and_off_its_edges() {
        let mut s = Scene::new(4, 2, 1);
        s.ground_colour([10, 20, 30]);
        s.layer(
            1,
            Layer::new(-1, 0, 2, 1, How::Over, rgba(2, 1, [255, 0, 0, 255])).unwrap(),
        );
        s.layer(
            2,
            Layer::new(3, 1, 4, 4, How::Add, [5u8, 5, 5].repeat(16)).unwrap(),
        );
        let boxes = s.frame(vec![1, 2], false).unwrap();
        assert_eq!(boxes.len(), 1);
        let (x0, y0, x1, y1, ref p) = boxes[0];
        assert_eq!((x0, y0, x1, y1), (0, 0, 4, 2));
        assert_eq!(&p[..3], &[255, 0, 0]); // (its left half lies off the frame)
        assert_eq!(&p[3..6], &[10, 20, 30]);
        assert_eq!(&p[21..24], &[15, 25, 35]); // (3, 1): the light on the ground
    }

    #[test]
    fn only_where_a_layer_came_or_went_is_composed_anew() {
        let mut s = Scene::new(64, 32, 16);
        s.ground_colour([0, 0, 0]);
        let dot = |x| Layer::new(x, 4, 2, 2, How::Over, rgba(2, 2, [9, 9, 9, 255])).unwrap();
        s.layer(1, dot(2));
        s.layer(2, dot(40));
        assert_eq!(s.frame(vec![1, 2], false).unwrap().len(), 1); // (the first: whole)
        assert!(s.frame(vec![1, 2], false).unwrap().is_empty());
        s.layer(3, dot(20)); // layer 2 goes, 3 comes: their blocks
        let boxes = s.frame(vec![1, 3], false).unwrap();
        let rects: Vec<_> = boxes.iter().map(|b| (b.0, b.1, b.2, b.3)).collect();
        assert_eq!(rects, vec![(16, 0, 48, 16)]);
        assert!(!s.layers.contains_key(&2)); // forgotten with the frame it left
                                             // the ones that stay in another order: all of it
        assert_eq!(s.frame(vec![3, 1], false).unwrap()[0].2, 64);
        assert!(s.frame(vec![3, 9], false).is_err());
    }

    #[test]
    fn a_picture_laid_over_another_is_pillow_s_alpha_composite_to_the_bit() {
        // (source, under it, Pillow's Image.alpha_composite of the two)
        let cases: [([u8; 4], [u8; 4], [u8; 4]); 89] = [
            ([200, 30, 255, 0], [10, 240, 128, 0], [10, 240, 128, 0]),
            ([200, 30, 255, 0], [10, 240, 128, 1], [10, 240, 128, 1]),
            ([200, 30, 255, 0], [10, 240, 128, 2], [10, 240, 128, 2]),
            ([200, 30, 255, 0], [10, 240, 128, 127], [10, 240, 128, 127]),
            ([200, 30, 255, 0], [10, 240, 128, 128], [10, 240, 128, 128]),
            ([200, 30, 255, 0], [10, 240, 128, 254], [10, 240, 128, 254]),
            ([200, 30, 255, 0], [10, 240, 128, 255], [10, 240, 128, 255]),
            ([200, 30, 255, 1], [10, 240, 128, 0], [200, 30, 255, 1]),
            ([200, 30, 255, 1], [10, 240, 128, 1], [105, 135, 192, 2]),
            ([200, 30, 255, 1], [10, 240, 128, 2], [73, 170, 170, 3]),
            ([200, 30, 255, 1], [10, 240, 128, 127], [11, 238, 129, 128]),
            ([200, 30, 255, 1], [10, 240, 128, 128], [11, 238, 129, 128]),
            ([200, 30, 255, 1], [10, 240, 128, 254], [11, 239, 128, 254]),
            ([200, 30, 255, 1], [10, 240, 128, 255], [11, 239, 128, 255]),
            ([200, 30, 255, 2], [10, 240, 128, 0], [200, 30, 255, 2]),
            ([200, 30, 255, 2], [10, 240, 128, 1], [137, 100, 213, 3]),
            ([200, 30, 255, 2], [10, 240, 128, 2], [105, 135, 192, 4]),
            ([200, 30, 255, 2], [10, 240, 128, 127], [13, 237, 130, 128]),
            ([200, 30, 255, 2], [10, 240, 128, 128], [13, 237, 130, 129]),
            ([200, 30, 255, 2], [10, 240, 128, 254], [11, 238, 129, 254]),
            ([200, 30, 255, 2], [10, 240, 128, 255], [11, 238, 129, 255]),
            ([200, 30, 255, 127], [10, 240, 128, 0], [200, 30, 255, 127]),
            ([200, 30, 255, 127], [10, 240, 128, 1], [199, 31, 254, 128]),
            ([200, 30, 255, 127], [10, 240, 128, 2], [199, 32, 254, 128]),
            (
                [200, 30, 255, 127],
                [10, 240, 128, 127],
                [136, 100, 213, 191],
            ),
            (
                [200, 30, 255, 127],
                [10, 240, 128, 128],
                [136, 101, 212, 191],
            ),
            (
                [200, 30, 255, 127],
                [10, 240, 128, 254],
                [105, 135, 191, 254],
            ),
            (
                [200, 30, 255, 127],
                [10, 240, 128, 255],
                [105, 135, 191, 255],
            ),
            ([200, 30, 255, 128], [10, 240, 128, 0], [200, 30, 255, 128]),
            ([200, 30, 255, 128], [10, 240, 128, 1], [199, 31, 255, 128]),
            ([200, 30, 255, 128], [10, 240, 128, 2], [199, 32, 254, 129]),
            (
                [200, 30, 255, 128],
                [10, 240, 128, 127],
                [137, 99, 213, 191],
            ),
            (
                [200, 30, 255, 128],
                [10, 240, 128, 128],
                [137, 100, 213, 192],
            ),
            (
                [200, 30, 255, 128],
                [10, 240, 128, 254],
                [106, 134, 192, 255],
            ),
            (
                [200, 30, 255, 128],
                [10, 240, 128, 255],
                [105, 135, 192, 255],
            ),
            ([200, 30, 255, 254], [10, 240, 128, 0], [200, 30, 255, 254]),
            ([200, 30, 255, 254], [10, 240, 128, 1], [200, 30, 255, 254]),
            ([200, 30, 255, 254], [10, 240, 128, 2], [200, 30, 255, 254]),
            (
                [200, 30, 255, 254],
                [10, 240, 128, 127],
                [200, 30, 255, 254],
            ),
            (
                [200, 30, 255, 254],
                [10, 240, 128, 128],
                [200, 30, 255, 255],
            ),
            (
                [200, 30, 255, 254],
                [10, 240, 128, 254],
                [199, 31, 255, 255],
            ),
            (
                [200, 30, 255, 254],
                [10, 240, 128, 255],
                [199, 31, 255, 255],
            ),
            ([200, 30, 255, 255], [10, 240, 128, 0], [200, 30, 255, 255]),
            ([200, 30, 255, 255], [10, 240, 128, 1], [200, 30, 255, 255]),
            ([200, 30, 255, 255], [10, 240, 128, 2], [200, 30, 255, 255]),
            (
                [200, 30, 255, 255],
                [10, 240, 128, 127],
                [200, 30, 255, 255],
            ),
            (
                [200, 30, 255, 255],
                [10, 240, 128, 128],
                [200, 30, 255, 255],
            ),
            (
                [200, 30, 255, 255],
                [10, 240, 128, 254],
                [200, 30, 255, 255],
            ),
            (
                [200, 30, 255, 255],
                [10, 240, 128, 255],
                [200, 30, 255, 255],
            ),
            ([165, 77, 202, 24], [37, 48, 187, 29], [98, 62, 194, 50]),
            ([109, 19, 44, 222], [214, 35, 123, 46], [112, 19, 46, 228]),
            ([217, 30, 63, 114], [31, 203, 25, 113], [151, 91, 50, 176]),
            ([23, 68, 148, 214], [73, 60, 157, 92], [26, 67, 149, 229]),
            ([52, 96, 190, 49], [32, 30, 105, 254], [36, 43, 121, 254]),
            (
                [218, 160, 238, 232],
                [185, 153, 127, 92],
                [217, 160, 234, 240],
            ),
            (
                [124, 41, 153, 253],
                [175, 229, 147, 37],
                [124, 41, 153, 253],
            ),
            ([60, 214, 84, 175], [77, 250, 215, 20], [61, 215, 89, 181]),
            ([39, 160, 174, 179], [254, 233, 35, 47], [55, 165, 164, 193]),
            (
                [138, 242, 33, 31],
                [158, 228, 145, 197],
                [155, 230, 128, 204],
            ),
            ([177, 11, 236, 181], [86, 59, 252, 30], [173, 13, 237, 190]),
            (
                [111, 147, 66, 126],
                [203, 200, 254, 41],
                [124, 154, 93, 147],
            ),
            (
                [85, 229, 205, 142],
                [70, 220, 142, 212],
                [79, 225, 180, 236],
            ),
            ([183, 194, 118, 77], [42, 90, 77, 118], [110, 140, 97, 159]),
            ([119, 6, 248, 93], [134, 144, 2, 74], [124, 52, 165, 140]),
            (
                [214, 189, 163, 64],
                [27, 233, 200, 203],
                [82, 220, 189, 216],
            ),
            ([204, 201, 53, 246], [205, 31, 97, 34], [204, 200, 53, 247]),
            ([106, 225, 83, 56], [174, 26, 52, 0], [106, 225, 83, 56]),
            ([77, 51, 186, 13], [36, 106, 192, 76], [42, 98, 191, 85]),
            (
                [129, 177, 186, 242],
                [62, 59, 249, 238],
                [126, 171, 189, 254],
            ),
            (
                [245, 247, 159, 43],
                [73, 52, 175, 135],
                [121, 106, 171, 155],
            ),
            ([245, 82, 11, 105], [185, 75, 13, 152], [217, 79, 12, 194]),
            (
                [46, 133, 187, 85],
                [182, 114, 168, 114],
                [110, 124, 178, 161],
            ),
            (
                [99, 122, 205, 116],
                [102, 252, 182, 14],
                [99, 130, 204, 124],
            ),
            (
                [14, 143, 241, 132],
                [99, 176, 228, 178],
                [48, 156, 236, 218],
            ),
            (
                [186, 41, 112, 52],
                [116, 240, 100, 172],
                [135, 185, 103, 189],
            ),
            ([104, 247, 0, 245], [176, 43, 61, 198], [106, 241, 2, 253]),
            (
                [102, 244, 91, 222],
                [170, 44, 202, 237],
                [110, 220, 104, 253],
            ),
            ([205, 43, 81, 87], [65, 14, 77, 238], [115, 24, 78, 244]),
            ([74, 242, 179, 79], [67, 10, 7, 52], [72, 170, 125, 115]),
            ([71, 222, 99, 108], [14, 128, 108, 149], [46, 180, 103, 194]),
            (
                [123, 166, 132, 214],
                [67, 31, 181, 234],
                [115, 146, 139, 252],
            ),
            ([215, 66, 77, 9], [225, 93, 2, 76], [224, 90, 10, 82]),
            ([88, 72, 242, 61], [31, 166, 247, 54], [65, 110, 244, 102]),
            ([29, 127, 97, 141], [21, 50, 231, 14], [29, 124, 103, 147]),
            (
                [32, 226, 166, 102],
                [141, 231, 244, 126],
                [78, 228, 199, 178],
            ),
            (
                [132, 103, 229, 70],
                [213, 62, 200, 226],
                [189, 74, 209, 234],
            ),
            ([161, 37, 123, 219], [37, 108, 155, 62], [156, 40, 124, 228]),
            ([79, 187, 73, 129], [70, 239, 112, 48], [78, 195, 79, 153]),
            (
                [203, 249, 83, 114],
                [82, 220, 206, 173],
                [148, 236, 139, 210],
            ),
        ];
        for (src, dst, want) in cases {
            let mut out = dst;
            composite(&mut out, &src);
            assert_eq!(out, want, "{src:?} over {dst:?}");
        }
    }

    #[test]
    fn four_pixels_at_a_time_are_laid_as_one_by_one() {
        // every alpha over every level, each pixel with others of every kind beside it, in
        // spans of every length up to 37 (the rest at the end laid one by one)
        let mut seed = 0x2545_f491_4f6c_dd1du64;
        let mut next = || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed
        };
        for a in 0..=255u8 {
            for level in (0..=255u8).step_by(3) {
                let n = (usize::from(a) + usize::from(level)) % 38;
                let mut src: Vec<u8> = (0..n * 4).map(|_| next() as u8).collect();
                let mut dst: Vec<u8> = (0..n * 3).map(|_| next() as u8).collect();
                for (i, p) in src.chunks_exact_mut(4).enumerate() {
                    p[3] = match i % 4 {
                        0 => a,
                        1 => 0,
                        2 => 255,
                        _ => p[3],
                    };
                    if i % 2 == 0 {
                        p[0] = level;
                    }
                }
                if let Some(d) = dst.get_mut(1) {
                    *d = level;
                }
                let mut want = dst.clone();
                over_one_by_one(&mut want, &src);
                over(&mut dst, &src);
                assert_eq!(dst, want, "alpha {a}, level {level}, {n} pixels");
            }
        }
        // and every source level over every level under it, for every alpha
        for a in 0..=255u8 {
            let src: Vec<u8> = (0..=255u8)
                .flat_map(|s| (0..=255u8).flat_map(move |d| [s, d, 255 - s, a]))
                .collect();
            let dst: Vec<u8> = (0..=255u8)
                .flat_map(|s| (0..=255u8).flat_map(move |d| [d, s, 255 - d]))
                .collect();
            let (mut fast, mut slow) = (dst.clone(), dst);
            over(&mut fast, &src);
            over_one_by_one(&mut slow, &src);
            assert!(fast == slow, "alpha {a}");
        }
    }

    #[test]
    fn over_what_is_clear_or_as_what_is_opaque_a_picture_laid_over_is_itself() {
        for c in 0..=255u8 {
            for a in 1..=255u8 {
                let src = [c, 255 - c, c / 3, a];
                let mut clear = [7, 99, 200, 0];
                ring::alpha_composite(&mut clear, &src);
                assert_eq!(clear, src, "{src:?} over what is clear");
                let mut under = [a, c, 3, c];
                ring::alpha_composite(&mut under, &[c, a, 9, 255]);
                assert_eq!(under, [c, a, 9, 255], "{c}, {a} opaque");
            }
        }
    }

    #[test]
    fn a_layer_laid_from_sources_is_each_laid_over_the_one_before_and_keeps_what_it_says() {
        let mut s = Scene::new(8, 4, 2);
        // a ring of 3 x 2, half see-through on its left, clear in its middle at the top
        s.source(
            5,
            3,
            2,
            vec![
                200, 10, 10, 128, 0, 0, 0, 0, 200, 10, 10, 255, 200, 10, 10, 128, 200, 10, 10, 64,
                200, 10, 10, 255,
            ],
        )
        .unwrap();
        s.source(6, 1, 1, vec![10, 220, 30, 200]).unwrap(); // a rotor of one pixel
        s.source(7, 1, 1, vec![1, 2, 3, 255]).unwrap(); // another position of it
        let placed = |source, dx, dy| Placed { source, dx, dy };
        let parts = [
            placed(5, 0, 0),
            placed(6, 1, 1),
            placed(6, 2, 0),
            placed(6, -1, 0),
        ];
        s.laid(1, (2, 1, 3, 2), &parts, &[7, 6]).unwrap();
        let layer = &s.layers[&1];
        let px = |x: usize, y: usize| &layer.pixels[(y * 3 + x) * 4..(y * 3 + x) * 4 + 4];
        let over = |dst: [u8; 4], src: [u8; 4]| {
            let mut out = dst;
            composite(&mut out, &src);
            out
        };
        assert_eq!(px(0, 0), &[200, 10, 10, 128]); // (on what is clear: itself)
        assert_eq!(px(1, 0), &[0, 0, 0, 0]); // (clear, and nothing over it)
        assert_eq!(px(2, 0), &over([200, 10, 10, 255], [10, 220, 30, 200]));
        assert_eq!(px(1, 1), &over([200, 10, 10, 64], [10, 220, 30, 200]));
        assert_eq!(px(2, 1), &[200, 10, 10, 255]);
        assert_eq!(layer.part, bounds(&layer.pixels, 3, 2, 4, |p| p[3] != 0));
        assert_eq!(layer.sources, vec![5, 6, 7]); // (the rotor's other position kept too)
        assert!(s.laid(2, (0, 0, 1, 1), &[placed(9, 0, 0)], &[]).is_err());
        assert!(s.laid(2, (0, 0, 1, 1), &[placed(5, 0, 0)], &[9]).is_err());
        s.frame(vec![1], false).unwrap();
        assert_eq!(s.sources.len(), 3); // (kept with the layer laid from them)
        s.layer(
            3,
            Layer::new(0, 0, 1, 1, How::Over, vec![0, 0, 0, 255]).unwrap(),
        );
        s.frame(vec![3], false).unwrap();
        assert!(s.sources.is_empty()); // (gone with it)
    }

    #[test]
    fn a_layer_cut_from_sources_is_the_crops_pasted_and_its_sources_stay_with_it() {
        let mut s = Scene::new(8, 4, 2);
        // a source of 4 x 2: pixel (x, y) is [x, y, 7, 255]
        let strip: Vec<u8> = (0..2u8)
            .flat_map(|y| (0..4u8).flat_map(move |x| [x, y, 7, 255]))
            .collect();
        s.source(5, 4, 2, strip).unwrap();
        s.source(6, 1, 1, vec![1, 1, 1, 255]).unwrap();
        let part = |source, sx, sy, dx, dy, w, h| Part {
            source,
            sx,
            sy,
            dx,
            dy,
            w,
            h,
        };
        // a crop from x 2 (partly off the source, beyond its right edge), and over its first
        // column one off the source above it: transparent there
        let parts = [part(5, 2, 0, 0, 0, 3, 2), part(6, 0, -1, 0, 0, 1, 2)];
        s.view(1, (1, 1, 3, 2), &parts).unwrap();
        let layer = &s.layers[&1];
        assert!(layer.pixels.is_empty()); // (none kept: laid from the sources')
        let pixels = s.picture_of(1, 2);
        let px = |x: usize, y: usize| &pixels[(y * 3 + x) * 4..(y * 3 + x) * 4 + 4];
        assert_eq!(px(0, 0), &[0, 0, 0, 0]); // (the second part: off its source)
        assert_eq!(px(0, 1), &[1, 1, 1, 255]);
        assert_eq!(px(1, 0), &[3, 0, 7, 255]);
        assert_eq!(px(1, 1), &[3, 1, 7, 255]);
        assert_eq!(px(2, 0), &[0, 0, 0, 0]); // (the first: beyond its source)
                                             // where it shows, worked out from the sources' rows: as a look at its pixels finds it
        assert_eq!(layer.part, bounds(&pixels, 3, 2, 4, |p| p[3] != 0));
        assert_eq!(layer.sources, vec![5, 6]);
        assert!(s
            .view(2, (0, 0, 1, 1), &[part(9, 0, 0, 0, 0, 1, 1)])
            .is_err());
        s.frame(vec![1], false).unwrap();
        assert_eq!(s.sources.len(), 2); // (kept with the layer cut from them)
        s.layer(
            3,
            Layer::new(0, 0, 1, 1, How::Over, vec![0, 0, 0, 255]).unwrap(),
        );
        s.frame(vec![3], false).unwrap();
        assert!(s.sources.is_empty()); // (gone with it)
        assert!(s.source(7, 2, 2, vec![0; 4]).is_err());
    }

    #[test]
    fn a_source_grown_from_another_is_the_one_that_came_whole() {
        // a strip, and the next grown from it: its first columns the strip's from a column on,
        // the rest new (clear at the ends of rows too: where a row shows is worked out anew)
        let mut seed = 0x0123_4567_89ab_cdefu64;
        let mut picture = |w: usize, h: usize| -> Vec<u8> {
            (0..w * h * 4)
                .map(|i| {
                    seed ^= seed << 13;
                    seed ^= seed >> 7;
                    seed ^= seed << 17;
                    if i % 4 == 3 && (seed >> 8).is_multiple_of(3) {
                        0
                    } else {
                        seed as u8
                    }
                })
                .collect()
        };
        let (bw, h) = (40usize, 6usize);
        let mut base = picture(bw, h);
        for v in base.chunks_exact_mut(bw * 4).take(2) {
            v.iter_mut().skip(3).step_by(4).for_each(|a| *a = 0); // (rows all clear)
        }
        for (w, shift, keep) in [
            (45usize, 7usize, 30usize),
            (12, 0, 12),
            (10, 3, 0),
            (40, 0, 40),
        ] {
            let rest = picture(w - keep, h);
            let whole: Vec<u8> = (0..h)
                .flat_map(|y| {
                    let row = &base[(y * bw + shift) * 4..(y * bw + shift + keep) * 4];
                    let tail = &rest[y * (w - keep) * 4..(y + 1) * (w - keep) * 4];
                    row.iter().chain(tail).copied().collect::<Vec<u8>>()
                })
                .collect();
            let mut s = Scene::new(64, 32, 16);
            s.source(1, bw, h, base.clone()).unwrap();
            s.grown(2, (w, h), 1, (shift, keep), &rest).unwrap();
            s.source(3, w, h, whole).unwrap();
            let (grown, sent) = (&s.sources[&2], &s.sources[&3]);
            assert!(
                grown.pixels == sent.pixels,
                "{w} columns, {keep} kept from {shift}"
            );
            assert_eq!(grown.shown, sent.shown);
            assert_eq!((grown.w, grown.h), (w, h));
        }
        let mut s = Scene::new(64, 32, 16);
        s.source(1, bw, h, base.clone()).unwrap();
        let rest = picture(5, h);
        assert!(s.grown(2, (10, h), 9, (0, 5), &rest).is_err()); // (from one that never came)
        assert!(s.grown(2, (10, h), 1, (36, 5), &rest).is_err()); // (beyond its columns)
        assert!(s.grown(2, (10, h + 1), 1, (0, 5), &rest).is_err()); // (of another height)
        assert!(s.grown(2, (11, h), 1, (0, 5), &rest).is_err()); // (a rest of another length)
        assert!(s.grown(2, (4, h), 1, (0, 5), &rest).is_err()); // (more kept than it has)
        assert!(!s.sources.contains_key(&2));
    }

    #[test]
    fn frames_of_windows_laid_from_their_strips_are_those_of_the_windows_pixels() {
        // a strip of pixels of every kind, and windows cut from it in many ways: laid from
        // the strip's pixels, the frames are those of the windows' own pixels (as they were
        // cut), between layers below and above, also turned half a turn and as they scroll
        let mut seed = 0x9e37_79b9_7f4a_7c15u64;
        let mut next = move || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed
        };
        let (sw, sh) = (90usize, 40usize);
        let mut picture = |w: usize, h: usize| -> Vec<u8> {
            (0..w * h)
                .flat_map(|_| {
                    let r = next();
                    let a = match (r >> 40) % 5 {
                        0 => 0,
                        1 => 255,
                        _ => (r >> 24) as u8,
                    };
                    [r as u8, (r >> 8) as u8, (r >> 16) as u8, a]
                })
                .collect()
        };
        let strip = picture(sw, sh);
        let (below, above) = (picture(64, 48), picture(20, 12));
        let part = |sx, sy, dx, dy, w, h| Part {
            source: 9,
            sx,
            sy,
            dx,
            dy,
            w,
            h,
        };
        type Window = ((i32, i32, usize, usize), Vec<Part>);
        let windows: Vec<Window> = vec![
            // all of it, and a graph's glow beside it
            (
                (5, 3, 40, 20),
                vec![
                    part(0, 0, 0, 0, 40, 20),
                    part(80, 0, 0, 0, 4, 20),
                    part(84, 0, 36, 0, 4, 20),
                ],
            ),
            // partly off the frame, its part partly off the strip above and to the left
            ((-7, -5, 40, 20), vec![part(-3, -2, 0, 0, 40, 20)]),
            // off the strip to the right and below
            ((40, 20, 30, 25), vec![part(70, 20, 0, 0, 30, 25)]),
            // one over another, and where none lies
            (
                (10, 10, 30, 20),
                vec![part(0, 0, 0, 0, 20, 20), part(30, 5, 10, 5, 20, 10)],
            ),
            // pasted over another where it lies off the strip, on every side of it: clear
            (
                (5, 25, 30, 20),
                vec![
                    part(0, 0, 0, 0, 30, 20),
                    part(10, 35, 5, 10, 15, 10),
                    part(84, 0, 20, 0, 15, 20),
                    part(-4, -3, 2, 1, 6, 15),
                ],
            ),
            // rows where none lies, above, between and below
            (
                (2, 8, 30, 20),
                vec![part(5, 10, 0, 6, 30, 6), part(40, 2, 3, 15, 20, 3)],
            ),
            // parts beyond the window, and none
            (
                (3, 4, 16, 16),
                vec![part(0, 0, 20, 0, 5, 5), part(0, 0, -9, 0, 5, 5)],
            ),
            ((3, 4, 16, 16), vec![]),
        ];
        for block in [16, 8] {
            for (n, (place, parts)) in windows.iter().enumerate() {
                let (x, y, w, h) = *place;
                let mut cut = Scene::new(64, 48, block);
                let mut own = Scene::new(64, 48, block);
                // (the frames as shown: what each composed laid on what was there; where a
                // window's box holds a see-through pixel more, a block more is composed)
                let (mut shown_cut, mut shown_own) =
                    (vec![0u8; 64 * 48 * 3], vec![0u8; 64 * 48 * 3]);
                let show = |shown: &mut Vec<u8>, boxes: Boxes| {
                    for (x0, y0, x1, y1, pixels) in boxes {
                        for (row, line) in pixels.chunks_exact((x1 - x0) * 3).enumerate() {
                            let at = ((y0 + row) * 64 + x0) * 3;
                            shown[at..at + line.len()].copy_from_slice(line);
                        }
                        assert!(y1 <= 48);
                    }
                };
                for s in [&mut cut, &mut own] {
                    s.ground_colour([30, 60, 90]);
                    s.layer(
                        1,
                        Layer::new(0, 0, 64, 48, How::Over, below.clone()).unwrap(),
                    );
                    s.layer(
                        3,
                        Layer::new(30, 30, 20, 12, How::Over, above.clone()).unwrap(),
                    );
                }
                for (k, half) in [(0, false), (1, false), (2, true)] {
                    if !cut.sources.contains_key(&9) {
                        // (gone with the window before, where none of it lay on it)
                        cut.source(9, sw, sh, strip.clone()).unwrap();
                    }
                    // (as it scrolls: a column on with each frame)
                    let id = 10 + k as u32;
                    let moved: Vec<Part> =
                        parts.iter().map(|p| Part { sx: p.sx + k, ..*p }).collect();
                    cut.view(id, (x, y, w, h), &moved).unwrap();
                    let pixels = cut.picture_of(id, h);
                    own.layer(id, Layer::new(x, y, w, h, How::Over, pixels).unwrap());
                    show(&mut shown_cut, cut.frame(vec![1, id, 3], half).unwrap());
                    show(&mut shown_own, own.frame(vec![1, id, 3], half).unwrap());
                    assert!(
                        shown_cut == shown_own,
                        "window {n}, frame {k}, blocks of {block}"
                    );
                }
            }
        }
    }

    #[test]
    fn where_a_layer_cut_from_sources_shows_is_known_from_its_sources_rows() {
        let mut s = Scene::new(64, 32, 16);
        // a strip of 20 x 6 shown in columns 3..9 and 12..15 of rows 1..5 (a gap between)
        let mut strip = vec![0u8; 20 * 6 * 4];
        for y in 1..5 {
            for x in (3..9).chain(12..15) {
                strip[(y * 20 + x) * 4..(y * 20 + x) * 4 + 4].copy_from_slice(&[9, 8, 7, 200]);
            }
        }
        s.source(1, 20, 6, strip).unwrap();
        let part = |sx, sy, dx, dy, w, h| Part {
            source: 1,
            sx,
            sy,
            dx,
            dy,
            w,
            h,
        };
        let scanned = |s: &Scene, id: u32| {
            let w = s.layers[&id].w;
            let h = if id == 5 { 6 } else { 8 };
            bounds(&s.picture_of(id, h), w, h, 4, |p| p[3] != 0)
        };
        // parts side by side, partly off the strip: what a look at the pixels finds, exactly
        for (id, parts) in [
            (2, vec![part(0, 0, 0, 0, 10, 6), part(10, 0, 10, 0, 10, 6)]),
            (3, vec![part(5, -2, 0, 0, 12, 8), part(-4, 3, 12, 0, 8, 8)]),
        ] {
            s.view(id, (0, 0, 20, 8), &parts).unwrap();
            assert_eq!(s.layers[&id].part, scanned(&s, id), "layer {id}");
        }
        // where a part lies over another, or cuts a row of its strip between what shows in
        // it: a box round all that shows (a see-through pixel more in it changes none)
        let round = |a: Rect, b: Rect| {
            b == (0, 0, 0, 0) || (a.0 <= b.0 && a.1 <= b.1 && a.2 >= b.2 && a.3 >= b.3)
        };
        s.view(4, (0, 0, 20, 8), &[part(10, 0, 0, 0, 2, 6)])
            .unwrap(); // (the gap alone)
        assert_eq!(scanned(&s, 4), (0, 0, 0, 0));
        assert_eq!(s.layers[&4].part, (0, 1, 2, 5)); // (its rows show, beyond it)
        let over = [part(0, 0, 0, 0, 20, 6), part(0, 10, 0, 0, 6, 6)];
        s.view(5, (0, 0, 20, 6), &over).unwrap();
        assert_eq!(scanned(&s, 5), (6, 1, 15, 5)); // (columns 3..6 laid over: see-through)
        assert!(round(s.layers[&5].part, scanned(&s, 5)));
    }

    #[test]
    fn a_transparent_layer_changes_nothing_and_a_frame_between_makes_the_next_whole() {
        let mut s = Scene::new(32, 32, 16);
        s.frame(vec![], false).unwrap();
        s.layer(
            1,
            Layer::new(0, 0, 8, 8, How::Over, rgba(8, 8, [255, 255, 255, 0])).unwrap(),
        );
        assert!(s.frame(vec![1], false).unwrap().is_empty());
        s.frame_between();
        assert_eq!(s.frame(vec![1], false).unwrap().len(), 1);
    }

    #[test]
    fn upside_down_the_boxes_and_their_pixels_are_turned() {
        let mut s = Scene::new(4, 2, 1);
        s.ground_colour([0, 0, 0]);
        s.layer(
            1,
            Layer::new(0, 0, 1, 1, How::Over, vec![1, 2, 3, 255]).unwrap(),
        );
        let boxes = s.frame(vec![1], true).unwrap();
        let (x0, y0, x1, y1, ref p) = boxes[0];
        assert_eq!((x0, y0, x1, y1), (0, 0, 4, 2));
        assert_eq!(&p[p.len() - 3..], &[1, 2, 3]); // (0, 0) is now the last pixel
                                                   // a layer that comes later: its box where it lies once turned
        s.layer(
            2,
            Layer::new(3, 1, 1, 1, How::Over, vec![7, 7, 7, 255]).unwrap(),
        );
        let boxes = s.frame(vec![1, 2], true).unwrap();
        assert_eq!(
            (boxes[0].0, boxes[0].1, boxes[0].2, boxes[0].3),
            (0, 0, 1, 1)
        );
        assert_eq!(boxes[0].4, vec![7, 7, 7]);
    }

    /// A small generator of numbers, the same in every run.
    struct Dice(u64);

    impl Dice {
        fn roll(&mut self, n: u64) -> u64 {
            self.0 = self
                .0
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            (self.0 >> 33) % n
        }

        fn layer(&mut self, w: usize, h: usize) -> Layer {
            let (lw, lh) = (1 + self.roll(40) as usize, 1 + self.roll(30) as usize);
            let how =
                [How::Over, How::Over, How::Over, How::Solid, How::Add][self.roll(5) as usize];
            let mut pixels = vec![0u8; lw * lh * how.channels()];
            for p in pixels.iter_mut() {
                *p = match self.roll(4) {
                    0 => 0,
                    1 => 255,
                    _ => self.roll(256) as u8,
                };
            }
            let x = self.roll(w as u64 + 20) as i32 - 10;
            let y = self.roll(h as u64 + 20) as i32 - 10;
            Layer::new(x, y, lw, lh, how, pixels).unwrap()
        }
    }

    #[test]
    fn frames_composed_where_they_changed_from_what_lay_under_are_frames_composed_whole() {
        let (w, h) = (96, 64);
        let mut dice = Dice(7);
        let mut kept = Scene::new(w, h, 16);
        kept.ground_colour([12, 34, 56]);
        let mut canvas = vec![0u8; w * h * 3];
        let mut order: Vec<u32> = Vec::new();
        let mut next = 1u32;
        let mut made: HashMap<u32, Layer> = HashMap::new();
        for frame in 0..60 {
            // some layers go, some are new (on top, or where one went), the rest stay
            let mut now = Vec::new();
            for id in &order {
                match dice.roll(6) {
                    0 => {}
                    1 => {
                        let layer = dice.layer(w, h);
                        made.insert(next, layer.clone());
                        kept.layer(next, layer);
                        now.push(next);
                        next += 1;
                    }
                    _ => now.push(*id),
                }
            }
            while now.len() < 12 {
                let layer = dice.layer(w, h);
                made.insert(next, layer.clone());
                kept.layer(next, layer);
                now.push(next);
                next += 1;
            }
            order = now;
            for (x0, y0, x1, y1, pixels) in kept.frame(order.clone(), false).unwrap() {
                for y in y0..y1 {
                    let row = &pixels[(y - y0) * (x1 - x0) * 3..(y - y0 + 1) * (x1 - x0) * 3];
                    canvas[(y * w + x0) * 3..(y * w + x1) * 3].copy_from_slice(row);
                }
            }
            let mut whole = Scene::new(w, h, 16);
            whole.ground_colour([12, 34, 56]);
            for id in &order {
                whole.layer(*id, made[id].clone());
            }
            let all = whole.frame(order.clone(), false).unwrap();
            assert_eq!(all.len(), 1);
            assert!(
                all[0].4 == canvas,
                "frame {frame} differs from the same frame composed whole"
            );
        }
    }

    #[test]
    fn a_picture_ground_and_wrong_sizes() {
        let mut s = Scene::new(2, 1, 1);
        assert!(s.ground_picture(vec![0; 5]).is_err());
        s.ground_picture(vec![1, 2, 3, 4, 5, 6]).unwrap();
        s.layer(
            1,
            Layer::new(1, 0, 1, 1, How::Solid, vec![9, 9, 9]).unwrap(),
        );
        let boxes = s.frame(vec![1], false).unwrap();
        assert_eq!(boxes[0].4, vec![1, 2, 3, 9, 9, 9]);
        assert!(Layer::new(0, 0, 2, 2, How::Over, vec![0; 15]).is_err());
        assert!(Layer::new(0, 0, 0, 2, How::Add, vec![]).is_err());
    }
}
