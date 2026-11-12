//! A frame as its layers, for Libre Panel's engine, which composes them itself
//! (devices/engine.py's SceneSender, engine/src/scene.rs): each piece's layers are sent once,
//! numbered, while the piece stays in the frames; then for every frame only the order they lie
//! in. The engine forgets a layer that a frame leaves out, and so does this.
//!
//! The pieces are the renderer's; what they are does not matter here: each is known by a key
//! (the address of the piece, which the sender keeps alive while the engine has its layers),
//! and only a piece the engine does not have yet is asked for its layers.

use std::any::Any;
use std::collections::HashMap;
use std::rc::Rc;

use lp_pixels::{Image, Mode};

use crate::engine::head;

/// The records of a frame of layers (wire.rs): its ground, a layer, the frame; a lit ring and a
/// layer of it at a value (ring.rs); a picture the engine keeps, a layer it cuts from such
/// pictures, and a picture it grows from one it keeps; a lit bar and a layer of it at a value,
/// with its glow (bar.rs).
const GROUND: u8 = 9;
const LAYER: u8 = 10;
const SCENE: u8 = 11;
const RING: u8 = 12;
const RING_LAYER: u8 = 13;
const SOURCE: u8 = 14;
const VIEW: u8 = 15;
const GROWN: u8 = 20;
const BAR: u8 = 21;
const BAR_LAYER: u8 = 22;

/// The most a bar of the engine's is long or a glow's area wide (with its room, px), and the
/// most spans (segments) it lights (render/lit.py's NATIVE_SIDE, NATIVE_SPANS; bar.rs, wire.rs).
pub const NATIVE_SIDE: usize = 8192;
pub const NATIVE_SPANS: usize = 4096;

/// How a layer lies on the frame (scene.rs's How): through its alpha, as it is, or added.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum How {
    Over = 0,
    Solid = 1,
    Add = 2,
}

/// A layer of a piece: its picture at x, y, laid so; and where it is cut from pictures (a
/// scrolling graph's window from its strip), those parts.
pub struct Layer<'a> {
    pub image: &'a Image,
    pub x: i64,
    pub y: i64,
    pub how: How,
    pub cut: Option<Vec<Cut>>,
    /// the layer's size where its pixels were left out, `cut` giving them (`image` empty):
    /// cut by the engine from what it keeps, or made here from `cut` where it cannot
    pub left_out: Option<(usize, usize)>,
    /// the lit ring the engine makes itself, where it is one (`image` empty)
    pub ring: Option<Rc<RingLayer>>,
    /// the lit bar the engine makes itself, where it is one (`image` empty): its picture, and
    /// its glow where it has one (the piece's light, which goes with it)
    pub bar: Option<Rc<BarLayer>>,
}

/// A lit ring of one colour at a value, as the engine makes it (render/lit.py's Ring,
/// engine/src/ring.rs): of `groove` and `lit` (the groove with the whole sweep lit, both RGBA
/// and square), its shape and the share of it lit.
#[derive(Debug)]
pub struct RingLayer {
    pub groove: Rc<Image>,
    pub lit: Rc<Image>,
    pub frac: f64,
    /// the gauge's width and groove, where its arc starts and ends (degrees), the core's stroke
    /// (px), the halo and the colour (RGBA)
    pub w: f64,
    pub thickness: f64,
    pub start: f64,
    pub end: f64,
    pub stroke: u32,
    pub halo: f64,
    pub colour: [u8; 4],
}

impl RingLayer {
    /// Whether the engine takes it as it is: its pictures square RGBA of one size, a share lit
    /// that is a number (any other goes as its pixels).
    pub fn fits(&self) -> bool {
        let (groove, lit) = (&self.groove, &self.lit);
        groove.height() == groove.width()
            && lit.size() == groove.size()
            && groove.mode() == Mode::RGBA
            && lit.mode() == Mode::RGBA
            && self.frac.is_finite()
    }
}

/// A lit bar's shape as the engine takes it (render/lit.py's BarBase.shape, bar.rs's Geometry):
/// lying, along its length and across its thickness.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BarShape {
    pub length: usize,
    pub thick: usize,
    /// the room round it on its canvas, for the light's halo
    pub pad: usize,
    /// the light's thickness, and its top on the canvas below the room
    pub core: usize,
    pub offset: i64,
    /// it stands (drawn lying, then turned), and fills from its far end
    pub upright: bool,
    pub reverse: bool,
    /// its segments (0: none)
    pub segments: u32,
    /// the softness of the light's halo (px)
    pub halo: f64,
}

/// How a lit bar's glow is worked out (render/lit.py's _glow_shape, bar.rs's Glow): the bar's
/// x, y and its area on the panel, the scale it is worked out at, its radius and strength, its
/// rise to the head and its tail, and the steps of the rise (their levels and whites).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BarGlow {
    pub bar: (i64, i64),
    pub area: (i64, i64, usize, usize),
    pub scale: usize,
    pub radius: f64,
    pub strength: f64,
    pub rise: f64,
    pub tail: f64,
    pub steps: [(f64, f64); 7],
}

/// What a lit bar is made of at every value (render/scene.py's BarBase, engine/src/bar.rs): its
/// groove on its canvas (RGBA, lying, `length + 2 pad` x `thick + 2 pad`), its pill (render/
/// lit.py's _pill in whole levels, `core + 2 pad` rows): a reference's first `side` columns,
/// the column after them and its last `side` columns, and the pills of the spans 1, 2, ...
/// long whole; its shape and its glow's.
pub struct BarBase {
    pub canvas: Image,
    pub left: Vec<u8>,
    pub middle: Vec<u8>,
    pub right: Vec<u8>,
    pub side: usize,
    pub short: Vec<Vec<u8>>,
    pub shape: BarShape,
    pub glow: Option<BarGlow>,
}

impl std::fmt::Debug for BarBase {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BarBase")
            .field("shape", &self.shape)
            .field("glow", &self.glow)
            .finish_non_exhaustive()
    }
}

impl BarBase {
    /// Whether the engine takes it (bar.rs's Base::new): its parts of their sizes, a bar and a
    /// glow of a size and shape it makes.
    pub fn fits(&self) -> bool {
        let g = &self.shape;
        let (cw, ch) = (g.length + 2 * g.pad, g.thick + 2 * g.pad);
        let high = g.core + 2 * g.pad;
        let shape = g.length > 0
            && g.thick > 0
            && g.core > 0
            && g.core <= g.thick
            && cw <= NATIVE_SIDE
            && ch <= NATIVE_SIDE
            && g.halo > 0.0
            && g.halo.is_finite()
            && i32::try_from(g.offset).is_ok()
            && g.segments as usize <= NATIVE_SPANS;
        let parts = self.canvas.mode() == Mode::RGBA
            && self.canvas.size() == (cw, ch)
            && self.side > 0
            && self.left.len() == high * self.side
            && self.middle.len() == high
            && self.right.len() == high * self.side
            && self.short.len() + 1 + 2 * g.pad >= 2 * self.side
            && (self.short.iter().enumerate()).all(|(k, p)| p.len() == high * (k + 1 + 2 * g.pad));
        let glow = self.glow.is_none_or(|w| {
            let (ax, ay, aw, ah) = w.area;
            let known = [w.radius, w.strength, w.rise, w.tail]
                .into_iter()
                .chain(w.steps.iter().flat_map(|&(a, b)| [a, b]))
                .all(f64::is_finite);
            [w.bar.0, w.bar.1, ax, ay]
                .iter()
                .all(|&v| i32::try_from(v).is_ok())
                && (1..=NATIVE_SIDE).contains(&aw)
                && (1..=NATIVE_SIDE).contains(&ah)
                && (1..=4).contains(&w.scale)
                && known
                && w.radius > 0.0
                && w.strength > 0.0
        });
        shape && parts && glow
    }

    /// Its record's body after the head (devices/engine.py's _bar_parts): its shape and its
    /// glow's, then its groove on its canvas and its pill's parts.
    fn parts(&self) -> Vec<Vec<u8>> {
        let g = &self.shape;
        let mut head = Vec::with_capacity(224);
        let u = |head: &mut Vec<u8>, v: usize| head.extend_from_slice(&(v as u32).to_le_bytes());
        let i = |head: &mut Vec<u8>, v: i64| head.extend_from_slice(&(v as i32).to_le_bytes());
        let f = |head: &mut Vec<u8>, v: f64| head.extend_from_slice(&v.to_le_bytes());
        u(&mut head, g.length);
        u(&mut head, g.thick);
        u(&mut head, g.pad);
        u(&mut head, g.core);
        i(&mut head, g.offset);
        u(
            &mut head,
            usize::from(g.upright) | (2 * usize::from(g.reverse)),
        );
        u(&mut head, g.segments as usize);
        u(&mut head, self.side);
        u(&mut head, self.side);
        u(&mut head, self.short.len());
        let none = BarGlow {
            bar: (0, 0),
            area: (0, 0, 0, 0),
            scale: 0,
            radius: 0.0,
            strength: 0.0,
            rise: 0.0,
            tail: 0.0,
            steps: [(0.0, 0.0); 7],
        };
        let w = self.glow.unwrap_or(none);
        u(&mut head, w.scale);
        for v in [w.bar.0, w.bar.1, w.area.0, w.area.1] {
            i(&mut head, v);
        }
        u(&mut head, w.area.2);
        u(&mut head, w.area.3);
        head.extend_from_slice(&[0; 4]);
        for v in [g.halo, w.radius, w.strength, w.rise, w.tail] {
            f(&mut head, v);
        }
        for (level, _) in w.steps {
            f(&mut head, level);
        }
        for (_, white) in w.steps {
            f(&mut head, white);
        }
        let mut parts = vec![
            head,
            self.canvas.as_bytes().to_vec(),
            self.left.clone(),
            self.middle.clone(),
            self.right.clone(),
        ];
        parts.extend(self.short.iter().cloned());
        parts
    }
}

/// A lit bar at a value, as the engine makes it (render/scene.py's Bar): of `base`, its lit
/// `spans` (from, to along it) in its colour (RGBA) and the one its gradient runs to; `glow`:
/// its glow goes with it (the piece's light, a layer added over its area).
#[derive(Debug)]
pub struct BarLayer {
    pub base: Rc<BarBase>,
    pub spans: Vec<(f64, f64)>,
    pub colour: [u8; 4],
    pub to: Option<[u8; 4]>,
    pub glow: bool,
}

impl BarLayer {
    /// Whether the engine takes it as it is (wire.rs's BAR_LAYER, bar.rs): its base, spans it
    /// knows (each from no further than it goes, within a million pixels), not too many; a
    /// glow where its base has one.
    pub fn fits(&self) -> bool {
        let known =
            |&(a, b): &(f64, f64)| a.is_finite() && b.is_finite() && a <= b && a > -1e6 && b < 1e6;
        self.base.fits()
            && self.spans.len() <= NATIVE_SPANS
            && self.spans.iter().all(known)
            && (!self.glow || self.base.glow.is_some())
    }
}

/// A ring the engine has by its pictures and shape: the pictures kept alive, its number there.
type RingKey = (usize, usize, [u64; 5], u32, [u8; 4]);
type Rings = HashMap<RingKey, (Rc<Image>, Rc<Image>, u32)>;

fn ring_key(ring: &RingLayer) -> RingKey {
    let shape = [ring.w, ring.thickness, ring.start, ring.end, ring.halo].map(f64::to_bits);
    (
        Rc::as_ptr(&ring.groove) as usize,
        Rc::as_ptr(&ring.lit) as usize,
        shape,
        ring.stroke,
        ring.colour,
    )
}

/// A part a layer is cut from (render/scene.py's View): `image` from (sx, sy), w x h, pasted at
/// (dx, dy) on a clear picture of the layer's size; `grown` where `image`'s first `keep` columns
/// are those of `base` from column `shift` on (render/scene.py's grew).
pub struct Cut {
    pub image: Rc<Image>,
    pub grown: Option<(Rc<Image>, i64, i64)>,
    pub sx: i64,
    pub sy: i64,
    pub dx: i64,
    pub dy: i64,
    pub w: i64,
    pub h: i64,
}

/// The ground under the layers: the theme's colour, or its picture (RGB, the frame's size;
/// the renderer keeps it: the same picture, the same Rc).
pub enum Ground<'a> {
    Colour([u8; 3]),
    Picture(&'a Rc<Image>),
}

/// A piece of the frame, in its order: one the engine has (its key), or one it has not, with
/// what keeps it alive and its layers.
pub enum Entry<'a> {
    Known(usize),
    New {
        key: usize,
        keep: Rc<dyn Any>,
        layers: Vec<Layer<'a>>,
    },
}

#[derive(PartialEq)]
enum Kept {
    Colour([u8; 3]),
    Picture(usize),
}

/// A piece the engine has: kept alive (its key stays its own), its layers, the pictures they
/// are cut from and the rings and bars they are of.
#[derive(Clone)]
struct Had {
    /// (never read: it keeps the piece, and so its key, alive)
    _keep: Rc<dyn Any>,
    ids: Vec<u32>,
    cut_from: Vec<usize>,
    rings: Vec<RingKey>,
    bars: Vec<usize>,
}

/// The bars the engine has, by their base's address: the base kept alive, its number there.
type Bars = HashMap<usize, (Rc<BarBase>, u32)>;

/// The pictures the engine keeps by their key (the picture's address): the picture kept alive,
/// its number there.
type Sources = HashMap<usize, (Rc<Image>, u32)>;

/// What one stream of the engine has of the frames' layers. Used by the stream's writer alone.
#[derive(Default)]
pub struct SceneSender {
    ground: Option<(Kept, Option<Rc<Image>>)>,
    /// each piece's layers by its key
    pieces: HashMap<usize, Had>,
    /// the pictures layers are cut from, while the frames' layers are (the engine forgets the
    /// others with them)
    sources: Sources,
    /// the rings and bars the engine has, while the frames' layers are of them
    rings: Rings,
    bars: Bars,
    /// whether the engine cuts layers from pictures it keeps ("views"), grows a picture from
    /// one it keeps ("grown"), makes lit rings ("rings") and lit bars ("bars")
    views: bool,
    grown: bool,
    makes_rings: bool,
    makes_bars: bool,
    next: u32,
    /// layers sent, and their bytes (pictures to cut from with them)
    pub layers: u64,
    pub bytes: u64,
    /// layers the engine cut from pictures it keeps, and pictures it grew
    pub cut: u64,
    pub grew: u64,
}

impl SceneSender {
    pub fn new() -> SceneSender {
        SceneSender::default()
    }

    /// A sender for an engine that cuts layers from pictures it keeps (`views`) and grows a
    /// picture from one it keeps (`grown`), as it says it does.
    pub fn with(views: bool, grown: bool) -> SceneSender {
        SceneSender {
            views,
            grown: views && grown,
            ..SceneSender::default()
        }
    }

    /// The same for an engine that makes lit rings itself (`rings`: RING records).
    pub fn with_rings(mut self, rings: bool) -> SceneSender {
        self.makes_rings = rings;
        self
    }

    /// The same for an engine that makes lit bars itself (`bars`: BAR records).
    pub fn with_bars(mut self, bars: bool) -> SceneSender {
        self.makes_bars = bars;
        self
    }

    /// Whether the engine cuts layers from pictures it keeps (VIEW records).
    pub fn views(&self) -> bool {
        self.views
    }

    /// Whether the engine makes lit rings itself (RING records).
    pub fn rings(&self) -> bool {
        self.makes_rings
    }

    /// Whether the engine makes lit bars itself (BAR records).
    pub fn bars(&self) -> bool {
        self.makes_bars
    }

    /// The layers of a bar the engine makes at a value, its picture's and its glow's
    /// (devices/engine.py's _bar), the bar sent once while the frames' layers are of it; None
    /// where it goes as its pixels: an engine that makes no bars, or a layer that is no bar it
    /// makes.
    fn bar(
        &mut self,
        parts: &mut Vec<Vec<u8>>,
        layer: &Layer<'_>,
        now: &mut Bars,
    ) -> Option<(Vec<u32>, usize)> {
        let bar = layer.bar.as_ref()?;
        if !self.makes_bars || layer.how != How::Over || !bar.fits() {
            return None;
        }
        let key = Rc::as_ptr(&bar.base) as usize;
        let known = (now.get(&key).or_else(|| self.bars.get(&key)))
            .filter(|(base, _)| Rc::ptr_eq(base, &bar.base))
            .map(|hit| hit.1);
        let number = match known {
            Some(number) => number,
            None => {
                let number = self.next.wrapping_add(1);
                self.next = number;
                let body = bar.base.parts();
                let size: usize = body.iter().map(Vec::len).sum();
                parts.push(head(BAR, number, size as u32));
                parts.extend(body);
                self.bytes += size as u64;
                number
            }
        };
        now.insert(key, (Rc::clone(&bar.base), number));
        let id = self.next.wrapping_add(1);
        self.next = id;
        let glow = if bar.glow {
            self.next = self.next.wrapping_add(1);
            self.next
        } else {
            0
        };
        let mut record = head(BAR_LAYER, id, (32 + 16 * bar.spans.len()) as u32);
        record.extend_from_slice(&number.to_le_bytes());
        record.extend_from_slice(&(layer.x as i32).to_le_bytes());
        record.extend_from_slice(&(layer.y as i32).to_le_bytes());
        record.extend_from_slice(&glow.to_le_bytes());
        record.extend_from_slice(&bar.colour);
        record.extend_from_slice(&bar.to.unwrap_or([0; 4]));
        record.extend_from_slice(&u32::from(bar.to.is_some()).to_le_bytes());
        record.extend_from_slice(&(bar.spans.len() as u32).to_le_bytes());
        for &(a, b) in &bar.spans {
            record.extend_from_slice(&a.to_le_bytes());
            record.extend_from_slice(&b.to_le_bytes());
        }
        parts.push(record);
        if glow == 0 {
            self.layers += 1;
            Some((vec![id], key))
        } else {
            self.layers += 2;
            Some((vec![id, glow], key))
        }
    }

    /// The layer of a ring the engine makes (devices/engine.py's _ring), the ring sent once
    /// while the frames' layers are of it; None where it goes as its pixels: an engine that
    /// makes no rings, or a layer that is no ring it makes (laid otherwise than through its
    /// alpha, its pictures not square RGBA of one size).
    fn ring(
        &mut self,
        parts: &mut Vec<Vec<u8>>,
        layer: &Layer<'_>,
        now: &mut Rings,
    ) -> Option<(u32, RingKey)> {
        let ring: &RingLayer = layer.ring.as_ref()?;
        let (groove, lit) = (&ring.groove, &ring.lit);
        let side = groove.width();
        if !self.makes_rings || layer.how != How::Over || !ring.fits() {
            return None;
        }
        let key = ring_key(ring);
        let known = (now.get(&key).or_else(|| self.rings.get(&key)))
            .filter(|(g, l, _)| Rc::ptr_eq(g, groove) && Rc::ptr_eq(l, lit))
            .map(|hit| hit.2);
        let number = match known {
            Some(number) => number,
            None => {
                let number = self.next.wrapping_add(1);
                self.next = number;
                let mut head_bytes = Vec::with_capacity(56);
                head_bytes.extend_from_slice(&(side as u32).to_le_bytes());
                head_bytes.extend_from_slice(&ring.stroke.to_le_bytes());
                for v in [ring.w, ring.thickness, ring.start, ring.end, ring.halo] {
                    head_bytes.extend_from_slice(&v.to_le_bytes());
                }
                head_bytes.extend_from_slice(&ring.colour);
                head_bytes.extend_from_slice(&[0; 4]);
                let pictures = groove.as_bytes().len() + lit.as_bytes().len();
                let mut record = head(RING, number, (head_bytes.len() + pictures) as u32);
                record.extend_from_slice(&head_bytes);
                parts.push(record);
                parts.push(groove.as_bytes().to_vec());
                parts.push(lit.as_bytes().to_vec());
                self.bytes += pictures as u64;
                number
            }
        };
        now.insert(key, (Rc::clone(groove), Rc::clone(lit), number));
        let id = self.next.wrapping_add(1);
        self.next = id;
        let mut record = head(RING_LAYER, id, 24);
        record.extend_from_slice(&number.to_le_bytes());
        record.extend_from_slice(&(layer.x as i32).to_le_bytes());
        record.extend_from_slice(&(layer.y as i32).to_le_bytes());
        record.extend_from_slice(&0u32.to_le_bytes());
        record.extend_from_slice(&ring.frac.to_le_bytes());
        parts.push(record);
        self.layers += 1;
        Some((id, key))
    }

    /// Whether the engine has the layers of the piece of `key` (from the frame before).
    pub fn knows(&self, key: usize) -> bool {
        self.pieces.contains_key(&key)
    }

    /// The layer of `image` at x, y, numbered, as much of it as shows: where it lies through
    /// its alpha, not its see-through margin; where it adds light, not the black round it
    /// (neither changes a pixel). None for one that shows nothing.
    fn layer(&mut self, parts: &mut Vec<Vec<u8>>, layer: &Layer<'_>) -> Option<u32> {
        let how = layer.how;
        let converted;
        let made;
        let mut image = layer.image;
        if let (Some((w, h)), Some(cut)) = (layer.left_out, &layer.cut) {
            made = cut_image(cut, w, h)?;
            image = &made;
        }
        // (added light onto RGB, as Pillow pastes it there and adds it; laid as it is: RGB)
        let rgb_wanted = how != How::Over;
        if rgb_wanted && image.mode() != Mode::RGB {
            converted = image.convert(Mode::RGB).ok()?;
            image = &converted;
        }
        let (mut x, mut y) = (layer.x, layer.y);
        // (what shows of it: its rows cut to the box, copied once into the record's pixels)
        let (mut x0, mut y0, mut x1, mut y1) = (0, 0, image.width(), image.height());
        if how != How::Solid {
            (x0, y0, x1, y1) = image.getbbox(how == How::Over)?;
            x += x0 as i64;
            y += y0 as i64;
        }
        let bands = image.bands();
        let data = if (x0, y0, x1, y1) == (0, 0, image.width(), image.height()) {
            image.as_bytes().to_vec()
        } else {
            let mut data = Vec::with_capacity((x1 - x0) * (y1 - y0) * bands);
            for row in y0..y1 {
                data.extend_from_slice(&image.row(row)[x0 * bands..x1 * bands]);
            }
            data
        };
        let number = self.next.wrapping_add(1);
        self.next = number;
        let mut geometry = Vec::with_capacity(20);
        geometry.extend_from_slice(&(x as i32).to_le_bytes());
        geometry.extend_from_slice(&(y as i32).to_le_bytes());
        geometry.extend_from_slice(&((x1 - x0) as u32).to_le_bytes());
        geometry.extend_from_slice(&((y1 - y0) as u32).to_le_bytes());
        geometry.extend_from_slice(&(how as u32).to_le_bytes());
        let mut record = head(LAYER, number, (geometry.len() + data.len()) as u32);
        record.extend_from_slice(&geometry);
        parts.push(record);
        self.layers += 1;
        self.bytes += data.len() as u64;
        parts.push(data);
        Some(number)
    }

    /// The layer the engine cuts from pictures it keeps (devices/engine.py's _view), those sent
    /// once while the frames' layers are cut from them; None where it goes as its pixels: an
    /// engine that does not cut, a layer laid otherwise than through its alpha, or one that is
    /// not (byte for byte) what its parts cut.
    fn view(
        &mut self,
        parts: &mut Vec<Vec<u8>>,
        layer: &Layer<'_>,
        now: &mut Sources,
    ) -> Option<(u32, Vec<usize>)> {
        let cut = layer.cut.as_ref().filter(|cut| !cut.is_empty())?;
        let image = layer.image;
        let rgba = |image: &Image| image.mode() == Mode::RGBA;
        if !self.views || layer.how != How::Over || !rgba(image) {
            return None;
        }
        // (a layer whose pixels were left out is what its parts cut, by the renderer's making)
        let (width, height) = layer.left_out.unwrap_or_else(|| image.size());
        if !cut.iter().all(|part| rgba(&part.image))
            || (layer.left_out.is_none() && !cut_is(image, cut))
        {
            return None;
        }
        let mut keys = Vec::with_capacity(cut.len());
        let mut body = Vec::with_capacity(20 + 28 * cut.len());
        body.extend_from_slice(&(layer.x as i32).to_le_bytes());
        body.extend_from_slice(&(layer.y as i32).to_le_bytes());
        body.extend_from_slice(&(width as u32).to_le_bytes());
        body.extend_from_slice(&(height as u32).to_le_bytes());
        body.extend_from_slice(&(cut.len() as u32).to_le_bytes());
        for part in cut {
            keys.push(Rc::as_ptr(&part.image) as usize);
            let number = self.source(parts, part, now);
            body.extend_from_slice(&number.to_le_bytes());
            for v in [part.sx, part.sy, part.dx, part.dy] {
                body.extend_from_slice(&(v as i32).to_le_bytes());
            }
            body.extend_from_slice(&(part.w as u32).to_le_bytes());
            body.extend_from_slice(&(part.h as u32).to_le_bytes());
        }
        let number = self.next.wrapping_add(1);
        self.next = number;
        let mut record = head(VIEW, number, body.len() as u32);
        record.extend_from_slice(&body);
        parts.push(record);
        self.layers += 1;
        self.cut += 1;
        Some((number, keys))
    }

    /// The number of the picture the engine keeps for `part`'s image, sent the first time and
    /// kept while the frames' layers are cut from it (devices/engine.py's _source); one grown
    /// from a picture it keeps goes as the rest of its columns.
    fn source(&mut self, parts: &mut Vec<Vec<u8>>, part: &Cut, now: &mut Sources) -> u32 {
        let key = Rc::as_ptr(&part.image) as usize;
        let known = (now.get(&key).or_else(|| self.sources.get(&key)))
            .filter(|(image, _)| Rc::ptr_eq(image, &part.image))
            .map(|(image, number)| (Rc::clone(image), *number));
        if let Some(hit) = known {
            let number = hit.1;
            now.insert(key, hit);
            return number;
        }
        let number = self.next.wrapping_add(1);
        self.next = number;
        let image = &part.image;
        let (w, h) = (image.width(), image.height());
        let base = part
            .grown
            .as_ref()
            .filter(|_| self.grown)
            .and_then(|(base, shift, keep)| {
                let at = Rc::as_ptr(base) as usize;
                let (had, n) = now.get(&at).or_else(|| self.sources.get(&at))?;
                let (shift, keep) = (usize::try_from(*shift).ok()?, usize::try_from(*keep).ok()?);
                let fits = Rc::ptr_eq(had, base)
                    && base.height() == h
                    && keep <= w
                    && shift
                        .checked_add(keep)
                        .is_some_and(|end| end <= base.width());
                // (and its first columns are those of the one it grew from, byte for byte)
                let same = fits
                    && (0..h).all(|y| {
                        image.row(y)[..keep * 4] == base.row(y)[shift * 4..(shift + keep) * 4]
                    });
                same.then_some((*n, shift, keep))
            });
        match base {
            Some((from, shift, keep)) => {
                let mut rest = Vec::with_capacity((w - keep) * h * 4);
                for y in 0..h {
                    rest.extend_from_slice(&image.row(y)[keep * 4..]);
                }
                let mut record = head(GROWN, number, (20 + rest.len()) as u32);
                for v in [w, h, from as usize, shift, keep] {
                    record.extend_from_slice(&(v as u32).to_le_bytes());
                }
                parts.push(record);
                self.bytes += rest.len() as u64;
                self.grew += 1;
                parts.push(rest);
            }
            None => {
                let data = image.as_bytes().to_vec();
                let mut record = head(SOURCE, number, (8 + data.len()) as u32);
                record.extend_from_slice(&(w as u32).to_le_bytes());
                record.extend_from_slice(&(h as u32).to_le_bytes());
                parts.push(record);
                self.bytes += data.len() as u64;
                parts.push(data);
            }
        }
        now.insert(key, (Rc::clone(&part.image), number));
        number
    }

    /// The records of the frame numbered `number` (drawn for `planned`; turned half a turn
    /// where `half`), with the ground and the layers the engine does not have yet.
    pub fn records(
        &mut self,
        ground: &Ground<'_>,
        entries: Vec<Entry<'_>>,
        number: u32,
        planned: Option<f64>,
        half: bool,
    ) -> Vec<Vec<u8>> {
        let mut parts = Vec::new();
        let kept = match ground {
            Ground::Colour(rgb) => Kept::Colour(*rgb),
            Ground::Picture(picture) => Kept::Picture(Rc::as_ptr(picture) as usize),
        };
        if self.ground.as_ref().map(|(k, _)| k) != Some(&kept) {
            match ground {
                Ground::Colour(rgb) => {
                    let mut record = head(GROUND, 0, 3);
                    record.extend_from_slice(rgb);
                    parts.push(record);
                    self.ground = Some((kept, None));
                }
                Ground::Picture(picture) => {
                    let rgb;
                    let data = match picture.mode() {
                        Mode::RGB => picture.as_bytes(),
                        _ => {
                            rgb = picture
                                .convert(Mode::RGB)
                                .unwrap_or_else(|_| (***picture).clone());
                            rgb.as_bytes()
                        }
                    };
                    parts.push(head(GROUND, 1, data.len() as u32));
                    parts.push(data.to_vec());
                    self.bytes += data.len() as u64;
                    self.ground = Some((kept, Some(Rc::clone(picture))));
                }
            }
        }
        let mut order: Vec<u32> = Vec::new();
        let mut now: HashMap<usize, Had> = HashMap::with_capacity(entries.len());
        let mut sources: Sources = HashMap::new();
        let mut rings: Rings = HashMap::new();
        let mut bars: Bars = HashMap::new();
        for entry in entries {
            match entry {
                Entry::Known(key) => {
                    if let Some(had) = now.get(&key).or_else(|| self.pieces.get(&key)) {
                        let had = had.clone();
                        order.extend_from_slice(&had.ids);
                        // (the pictures its layers are cut from stay, and its rings and bars)
                        for at in &had.cut_from {
                            if let Some(source) = self.sources.get(at) {
                                sources.insert(*at, (Rc::clone(&source.0), source.1));
                            }
                        }
                        for at in &had.rings {
                            if let Some(ring) = self.rings.get(at) {
                                rings.insert(*at, (Rc::clone(&ring.0), Rc::clone(&ring.1), ring.2));
                            }
                        }
                        for at in &had.bars {
                            if let Some(bar) = self.bars.get(at) {
                                bars.insert(*at, (Rc::clone(&bar.0), bar.1));
                            }
                        }
                        now.insert(key, had);
                    }
                }
                Entry::New { key, keep, layers } => {
                    let mut had = Had {
                        _keep: keep,
                        ids: Vec::new(),
                        cut_from: Vec::new(),
                        rings: Vec::new(),
                        bars: Vec::new(),
                    };
                    for layer in &layers {
                        if let Some((id, ring)) = self.ring(&mut parts, layer, &mut rings) {
                            had.ids.push(id);
                            had.rings.push(ring);
                        } else if let Some((ids, bar)) = self.bar(&mut parts, layer, &mut bars) {
                            had.ids.extend(ids);
                            had.bars.push(bar);
                        } else if let Some((id, keys)) = self.view(&mut parts, layer, &mut sources)
                        {
                            had.ids.push(id);
                            had.cut_from.extend(keys);
                        } else if let Some(id) = self.layer(&mut parts, layer) {
                            had.ids.push(id);
                        }
                    }
                    order.extend_from_slice(&had.ids);
                    now.insert(key, had);
                }
            }
        }
        self.pieces = now;
        self.sources = sources;
        self.rings = rings;
        self.bars = bars;
        let mut record = head(SCENE, number, order.len() as u32);
        record.extend_from_slice(&planned.unwrap_or(f64::NAN).to_le_bytes());
        record.extend_from_slice(&(if half { 180u32 } else { 0 }).to_le_bytes());
        for id in &order {
            record.extend_from_slice(&id.to_le_bytes());
        }
        parts.push(record);
        parts
    }

    /// Everything forgotten: a new stream begins without the layers of the one before.
    pub fn forget(&mut self) {
        self.ground = None;
        self.pieces.clear();
        self.sources.clear();
        self.rings.clear();
        self.bars.clear();
    }
}

/// Whether `image` is what `cut` cuts: its parts pasted in turn on a clear picture of its size,
/// each nothing where it lies off its picture (as Pillow crops and pastes).
fn cut_is(image: &Image, cut: &[Cut]) -> bool {
    let (w, h) = (image.width() as i64, image.height() as i64);
    // (the usual one: a part over all of it, its rows compared where they are)
    if let [part] = cut {
        if (part.dx, part.dy, part.w, part.h) == (0, 0, w, h) {
            let (pw, ph) = (part.image.width() as i64, part.image.height() as i64);
            let (a, b) = ((-part.sx).clamp(0, w), (pw - part.sx).clamp(0, w));
            return (0..h).all(|y| {
                let row = image.row(y as usize);
                let sy = part.sy + y;
                if !(0..ph).contains(&sy) || b <= a {
                    return row.iter().all(|&v| v == 0);
                }
                let from = &part.image.row(sy as usize)[((part.sx + a) * 4) as usize..];
                row[..(a * 4) as usize].iter().all(|&v| v == 0)
                    && row[(a * 4) as usize..(b * 4) as usize] == from[..((b - a) * 4) as usize]
                    && row[(b * 4) as usize..].iter().all(|&v| v == 0)
            });
        }
    }
    cut_image(cut, w as usize, h as usize).is_some_and(|made| made.as_bytes() == image.as_bytes())
}

/// The picture `cut` makes, `w` x `h`: each part pasted on a clear picture in turn (the
/// renderer's View.image); None where a part cannot be.
fn cut_image(cut: &[Cut], w: usize, h: usize) -> Option<Image> {
    let mut made = Image::blank(Mode::RGBA, w, h);
    for part in cut {
        let piece = part
            .image
            .crop(part.sx, part.sy, part.sx + part.w, part.sy + part.h);
        made.paste(&piece, part.dx, part.dy, None).ok()?;
    }
    Some(made)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kinds(parts: &[Vec<u8>]) -> Vec<(u8, u32, u32)> {
        parts
            .iter()
            .filter(|p| p.len() >= 16 && &p[..4] == b"LPE1")
            .map(|p| {
                let a = u32::from_le_bytes(p[8..12].try_into().unwrap());
                let b = u32::from_le_bytes(p[12..16].try_into().unwrap());
                (p[4], a, b)
            })
            .collect()
    }

    #[test]
    fn a_pieces_layers_go_once_cut_to_what_shows_and_each_frame_their_order() {
        let mut layer = Image::blank(Mode::RGBA, 10, 6);
        // one opaque pixel at (3, 2) and one see-through at (7, 4): the box between them
        layer.as_bytes_mut()[(2 * 10 + 3) * 4..(2 * 10 + 3) * 4 + 4]
            .copy_from_slice(&[9, 8, 7, 255]);
        layer.as_bytes_mut()[(4 * 10 + 7) * 4 + 3] = 1;
        let piece: Rc<dyn Any> = Rc::new(1u8);
        let key = Rc::as_ptr(&piece) as *const () as usize;
        let mut sender = SceneSender::new();
        let entries = vec![Entry::New {
            key,
            keep: Rc::clone(&piece),
            layers: vec![Layer {
                image: &layer,
                x: 100,
                y: 50,
                how: How::Over,
                cut: None,
                left_out: None,
                ring: None,
                bar: None,
            }],
        }];
        let parts = sender.records(&Ground::Colour([1, 2, 3]), entries, 7, Some(1.5), false);
        assert_eq!(
            kinds(&parts),
            vec![(GROUND, 0, 3), (LAYER, 1, 20 + 5 * 3 * 4), (SCENE, 7, 1)]
        );
        // the layer's geometry: cut to (3, 2)-(8, 5)
        let geometry = &parts[1][16..36];
        let at = |k: usize| i32::from_le_bytes(geometry[k..k + 4].try_into().unwrap());
        assert_eq!((at(0), at(4), at(8), at(12), at(16)), (103, 52, 5, 3, 0));
        assert_eq!(&parts[2][..4], &[9, 8, 7, 255]);
        assert!(sender.knows(key));
        // the next frame: the piece known, the ground the same: the order alone
        let parts = sender.records(
            &Ground::Colour([1, 2, 3]),
            vec![Entry::Known(key)],
            8,
            None,
            true,
        );
        assert_eq!(kinds(&parts), vec![(SCENE, 8, 1)]);
        let scene = &parts[0];
        assert!(f64::from_le_bytes(scene[16..24].try_into().unwrap()).is_nan());
        assert_eq!(u32::from_le_bytes(scene[24..28].try_into().unwrap()), 180);
        assert_eq!(u32::from_le_bytes(scene[28..32].try_into().unwrap()), 1);
        // a frame without it: forgotten
        let parts = sender.records(&Ground::Colour([1, 2, 3]), vec![], 9, None, false);
        assert_eq!(kinds(&parts), vec![(SCENE, 9, 0)]);
        assert!(!sender.knows(key));
    }

    #[test]
    fn light_goes_as_rgb_without_the_black_round_it_and_nothing_seen_goes_not() {
        let mut light = Image::blank(Mode::RGBA, 4, 4);
        // (the pixel at (1, 1))
        light.as_bytes_mut()[5 * 4..5 * 4 + 4].copy_from_slice(&[5, 0, 0, 0]);
        let empty = Image::blank(Mode::RGBA, 4, 4);
        let piece: Rc<dyn Any> = Rc::new(2u8);
        let key = Rc::as_ptr(&piece) as *const () as usize;
        let mut sender = SceneSender::new();
        let entries = vec![Entry::New {
            key,
            keep: piece,
            layers: vec![
                Layer {
                    image: &empty,
                    x: 0,
                    y: 0,
                    how: How::Over,
                    cut: None,
                    left_out: None,
                    ring: None,
                    bar: None,
                },
                Layer {
                    image: &light,
                    x: 10,
                    y: 10,
                    how: How::Add,
                    cut: None,
                    left_out: None,
                    ring: None,
                    bar: None,
                },
            ],
        }];
        let ground = Rc::new(Image::blank(Mode::RGB, 4, 4));
        let parts = sender.records(&Ground::Picture(&ground), entries, 1, None, false);
        assert_eq!(
            kinds(&parts),
            vec![(GROUND, 1, 48), (LAYER, 1, 20 + 3), (SCENE, 1, 1)]
        );
        assert_eq!(&parts[3], &vec![5, 0, 0]);
        // the same ground picture: not sent again
        let parts = sender.records(&Ground::Picture(&ground), vec![], 2, None, false);
        assert_eq!(kinds(&parts), vec![(SCENE, 2, 0)]);
    }

    /// A picture of `w` x `h` whose pixels are their place: no two alike.
    fn numbered(w: usize, h: usize) -> Image {
        let data = (0..w * h * 4).map(|i| (i * 7 % 251) as u8 | 1).collect();
        Image::from_bytes(Mode::RGBA, w, h, data).unwrap()
    }

    #[test]
    fn a_layer_cut_from_a_picture_goes_as_its_parts_the_picture_once_and_grown_as_its_rest() {
        let strip = Rc::new(numbered(12, 3));
        let window = strip.crop(2, 0, 7, 3);
        let cut = |image: &Rc<Image>, grown: Option<(Rc<Image>, i64, i64)>, sx: i64| {
            Some(vec![Cut {
                image: Rc::clone(image),
                grown,
                sx,
                sy: 0,
                dx: 0,
                dy: 0,
                w: 5,
                h: 3,
            }])
        };
        let mut sender = SceneSender::with(true, true);
        let piece: Rc<dyn Any> = Rc::new(1u8);
        let key = Rc::as_ptr(&piece) as *const () as usize;
        let layer = Layer {
            image: &window,
            x: 4,
            y: 5,
            how: How::Over,
            cut: cut(&strip, None, 2),
            left_out: None,
            ring: None,
            bar: None,
        };
        let entries = vec![Entry::New {
            key,
            keep: Rc::clone(&piece),
            layers: vec![layer],
        }];
        let parts = sender.records(&Ground::Colour([0, 0, 0]), entries, 1, None, false);
        assert_eq!(
            kinds(&parts),
            vec![
                (GROUND, 0, 3),
                (SOURCE, 1, 8 + 12 * 3 * 4),
                (VIEW, 2, 20 + 28),
                (SCENE, 1, 1)
            ]
        );
        // the next window of the same strip: the strip not again
        let window = strip.crop(3, 0, 8, 3);
        let piece2: Rc<dyn Any> = Rc::new(2u8);
        let key2 = Rc::as_ptr(&piece2) as *const () as usize;
        let entries = vec![Entry::New {
            key: key2,
            keep: Rc::clone(&piece2),
            layers: vec![Layer {
                image: &window,
                x: 4,
                y: 5,
                how: How::Over,
                cut: cut(&strip, None, 3),
                left_out: None,
                ring: None,
                bar: None,
            }],
        }];
        let parts = sender.records(&Ground::Colour([0, 0, 0]), entries, 2, None, false);
        assert_eq!(kinds(&parts), vec![(VIEW, 3, 20 + 28), (SCENE, 2, 1)]);
        // a strip grown from it: its first 9 columns those of the one before from column 3 on
        let mut grown = numbered(12, 3);
        for y in 0..3 {
            let from = strip.row(y)[3 * 4..12 * 4].to_vec();
            grown.row_mut(y)[..9 * 4].copy_from_slice(&from);
        }
        let grown = Rc::new(grown);
        let window = grown.crop(0, 0, 5, 3);
        let piece3: Rc<dyn Any> = Rc::new(3u8);
        let key3 = Rc::as_ptr(&piece3) as *const () as usize;
        let entries = vec![Entry::New {
            key: key3,
            keep: Rc::clone(&piece3),
            layers: vec![Layer {
                image: &window,
                x: 4,
                y: 5,
                how: How::Over,
                cut: cut(&grown, Some((Rc::clone(&strip), 3, 9)), 0),
                left_out: None,
                ring: None,
                bar: None,
            }],
        }];
        let parts = sender.records(&Ground::Colour([0, 0, 0]), entries, 3, None, false);
        assert_eq!(
            kinds(&parts),
            vec![
                (GROWN, 4, 20 + 3 * 3 * 4),
                (VIEW, 5, 20 + 28),
                (SCENE, 3, 1)
            ]
        );
        assert_eq!((sender.cut, sender.grew), (3, 1));
        // a layer that is not what its parts cut goes as its pixels
        let mut other = grown.crop(1, 0, 6, 3);
        other.as_bytes_mut()[0] ^= 0xFF;
        let piece4: Rc<dyn Any> = Rc::new(4u8);
        let key4 = Rc::as_ptr(&piece4) as *const () as usize;
        let entries = vec![Entry::New {
            key: key4,
            keep: Rc::clone(&piece4),
            layers: vec![Layer {
                image: &other,
                x: 4,
                y: 5,
                how: How::Over,
                cut: cut(&grown, None, 1),
                left_out: None,
                ring: None,
                bar: None,
            }],
        }];
        let parts = sender.records(&Ground::Colour([0, 0, 0]), entries, 4, None, false);
        assert_eq!(kinds(&parts)[0].0, LAYER);
        // an engine that does not cut: its pixels too
        let mut plain = SceneSender::with(false, false);
        let first = strip.crop(2, 0, 7, 3);
        let entries = vec![Entry::New {
            key,
            keep: Rc::clone(&piece),
            layers: vec![Layer {
                image: &first,
                x: 4,
                y: 5,
                how: How::Over,
                cut: cut(&strip, None, 2),
                left_out: None,
                ring: None,
                bar: None,
            }],
        }];
        let parts = plain.records(&Ground::Colour([0, 0, 0]), entries, 1, None, false);
        assert_eq!(kinds(&parts)[1].0, LAYER);
    }
    #[test]
    fn a_ring_goes_to_the_engine_once_and_each_value_as_its_share_lit() {
        let side = 6usize;
        let groove = Rc::new(Image::blank(Mode::RGBA, side, side));
        let mut lit_picture = Image::blank(Mode::RGBA, side, side);
        lit_picture.as_bytes_mut().iter_mut().for_each(|v| *v = 200);
        let lit = Rc::new(lit_picture);
        let empty = Image::blank(Mode::RGBA, 0, 0);
        let ring = |frac: f64| RingLayer {
            groove: Rc::clone(&groove),
            lit: Rc::clone(&lit),
            frac,
            w: 4.0,
            thickness: 1.0,
            start: 135.0,
            end: 405.0,
            stroke: 2,
            halo: 1.5,
            colour: [10, 20, 30, 255],
        };
        let entry = |key: usize, frac: f64| {
            vec![Entry::New {
                key,
                keep: Rc::new(key) as Rc<dyn Any>,
                layers: vec![Layer {
                    image: &empty,
                    x: -3,
                    y: 7,
                    how: How::Over,
                    cut: None,
                    left_out: Some((side, side)),
                    ring: Some(Rc::new(ring(frac))),
                    bar: None,
                }],
            }]
        };
        let mut sender = SceneSender::with(true, true).with_rings(true);
        let ground = Ground::Colour([0, 0, 0]);
        let first = sender.records(&ground, entry(1, 0.25), 0, None, false);
        let kinds_of =
            |parts: &[Vec<u8>]| -> Vec<u8> { kinds(parts).iter().map(|k| k.0).collect() };
        assert_eq!(kinds_of(&first), [GROUND, RING, RING_LAYER, SCENE]);
        // (the ring as the engine reads it, wire.rs's kind 12: its side, its stroke, five
        // doubles, the colour and four bytes, then the groove and the lit sweep)
        let at = first
            .iter()
            .position(|p| p.len() >= 16 && p[4] == RING)
            .unwrap();
        let record = &first[at];
        let word = |p: &[u8], i: usize| u32::from_le_bytes(p[i..i + 4].try_into().unwrap());
        assert_eq!(word(record, 12) as usize, 56 + 2 * side * side * 4);
        let body = &record[16..];
        assert_eq!(body.len(), 56);
        assert_eq!(word(body, 0), side as u32);
        assert_eq!(word(body, 4), 2);
        let f = |i: usize| f64::from_le_bytes(body[i..i + 8].try_into().unwrap());
        assert_eq!(
            [f(8), f(16), f(24), f(32), f(40)],
            [4.0, 1.0, 135.0, 405.0, 1.5]
        );
        assert_eq!(&body[48..56], &[10, 20, 30, 255, 0, 0, 0, 0]);
        assert_eq!(first[at + 1], groove.as_bytes());
        assert_eq!(first[at + 2], lit.as_bytes());
        // (a layer of it: the ring's number, x, y, nothing, the share lit)
        let layer = first
            .iter()
            .find(|p| p.len() >= 16 && p[4] == RING_LAYER)
            .unwrap();
        assert_eq!(word(layer, 12), 24);
        assert_eq!(word(&layer[16..], 0), word(record, 8));
        assert_eq!(i32::from_le_bytes(layer[20..24].try_into().unwrap()), -3);
        assert_eq!(i32::from_le_bytes(layer[24..28].try_into().unwrap()), 7);
        assert_eq!(f64::from_le_bytes(layer[32..40].try_into().unwrap()), 0.25);
        // another value of the same ring: a layer of it alone
        let next = sender.records(&ground, entry(2, 0.5), 1, None, false);
        assert_eq!(kinds_of(&next), [RING_LAYER, SCENE]);
        // a frame without it forgets it: it goes again with the next that has it
        sender.records(&ground, vec![], 2, None, false);
        let again = sender.records(&ground, entry(3, 0.75), 3, None, false);
        assert_eq!(kinds_of(&again), [RING, RING_LAYER, SCENE]);
        // an engine that makes no rings takes no ring (its pixels come from the app)
        let mut plain = SceneSender::with(true, true);
        let parts = plain.records(&ground, entry(4, 0.5), 0, None, false);
        assert!(!kinds_of(&parts).contains(&RING));
    }

    /// A bar of `length` x `thick` with its pill in parts as render/lit.py's _bar_base cuts
    /// them, and a glow where `glow`.
    fn bar_base(glow: bool) -> BarBase {
        let (length, thick, pad, core) = (10usize, 4usize, 2usize, 3usize);
        let side = pad + core + 4;
        let high = core + 2 * pad;
        let mut canvas = Image::blank(Mode::RGBA, length + 2 * pad, thick + 2 * pad);
        canvas.as_bytes_mut().iter_mut().for_each(|v| *v = 7);
        BarBase {
            canvas,
            left: vec![1; high * side],
            middle: vec![2; high],
            right: vec![3; high * side],
            side,
            short: (1..2 * (side - pad))
                .map(|k| vec![4; high * (k + 2 * pad)])
                .collect(),
            shape: BarShape {
                length,
                thick,
                pad,
                core,
                offset: 1,
                upright: true,
                reverse: false,
                segments: 3,
                halo: 4.5,
            },
            glow: glow.then_some(BarGlow {
                bar: (40, -5),
                area: (30, -15, 30, 24),
                scale: 2,
                radius: 6.0,
                strength: 0.5,
                rise: 30.0,
                tail: 0.187,
                steps: std::array::from_fn(|i| (i as f64 / 10.0, i as f64 / 20.0)),
            }),
        }
    }

    #[test]
    fn a_bar_goes_to_the_engine_once_and_each_value_as_its_spans_with_its_glow() {
        let base = Rc::new(bar_base(true));
        assert!(base.fits());
        let empty = Image::blank(Mode::RGBA, 0, 0);
        let bar = |spans: Vec<(f64, f64)>, glow: bool| BarLayer {
            base: Rc::clone(&base),
            spans,
            colour: [10, 20, 30, 255],
            to: glow.then_some([200, 100, 50, 255]),
            glow,
        };
        let entry = |key: usize, layer: BarLayer| {
            vec![Entry::New {
                key,
                keep: Rc::new(key) as Rc<dyn Any>,
                layers: vec![Layer {
                    image: &empty,
                    x: 38,
                    y: -7,
                    how: How::Over,
                    cut: None,
                    left_out: Some((8, 14)),
                    ring: None,
                    bar: Some(Rc::new(layer)),
                }],
            }]
        };
        let kinds_of =
            |parts: &[Vec<u8>]| -> Vec<u8> { kinds(parts).iter().map(|k| k.0).collect() };
        let mut sender = SceneSender::with(true, true).with_bars(true);
        let ground = Ground::Colour([0, 0, 0]);
        let lit = vec![(0.0, 3.5), (4.0, 7.25)];
        let first = sender.records(&ground, entry(1, bar(lit.clone(), true)), 0, None, false);
        assert_eq!(kinds_of(&first), [GROUND, BAR, BAR_LAYER, SCENE]);
        // (the bar as the engine reads it, wire.rs's kind 21: its head of 224 bytes, then the
        // groove on its canvas and the pill's parts)
        let at = first
            .iter()
            .position(|p| p.len() >= 16 && p[4] == BAR)
            .unwrap();
        let word = |p: &[u8], i: usize| u32::from_le_bytes(p[i..i + 4].try_into().unwrap());
        let int = |p: &[u8], i: usize| i32::from_le_bytes(p[i..i + 4].try_into().unwrap());
        let float = |p: &[u8], i: usize| f64::from_le_bytes(p[i..i + 8].try_into().unwrap());
        let body: Vec<u8> = first[at + 1..at + 6 + base.short.len()].concat();
        assert_eq!(word(&first[at], 12) as usize, body.len());
        let head = &first[at + 1];
        assert_eq!(head.len(), 224);
        let fields: Vec<u32> = (0..11).map(|k| word(head, 4 * k)).collect();
        assert_eq!(fields, [10, 4, 2, 3, 1, 1, 3, 9, 9, 13, 2]);
        assert_eq!(
            [int(head, 44), int(head, 48), int(head, 52), int(head, 56)],
            [40, -5, 30, -15]
        );
        assert_eq!((word(head, 60), word(head, 64)), (30, 24));
        assert_eq!(
            [72, 80, 88, 96, 104].map(|i| float(head, i)),
            [4.5, 6.0, 0.5, 30.0, 0.187]
        );
        assert_eq!((float(head, 112 + 8), float(head, 168 + 8)), (0.1, 0.05));
        assert_eq!(first[at + 2], base.canvas.as_bytes());
        assert_eq!(
            (first[at + 3][0], first[at + 4][0], first[at + 5][0]),
            (1, 2, 3)
        );
        // (a layer of it: the bar's number, x, y, its glow's number, the colours, the spans)
        let layer = first
            .iter()
            .find(|p| p.len() >= 16 && p[4] == BAR_LAYER)
            .unwrap();
        assert_eq!(word(layer, 12) as usize, 32 + 16 * lit.len());
        let more = &layer[16..];
        assert_eq!(word(more, 0), word(&first[at], 8));
        assert_eq!((int(more, 4), int(more, 8)), (38, -7));
        let glow = word(more, 12);
        assert_ne!(glow, 0);
        assert_eq!(&more[16..24], &[10, 20, 30, 255, 200, 100, 50, 255]);
        assert_eq!((word(more, 24), word(more, 28)), (1, 2));
        assert_eq!([float(more, 32), float(more, 40)], [0.0, 3.5]);
        assert_eq!([float(more, 48), float(more, 56)], [4.0, 7.25]);
        // the frame lays the bar and then its glow
        let scene = first.last().unwrap();
        assert_eq!(word(scene, 12), 2);
        assert_eq!((word(scene, 28), word(scene, 32)), (word(layer, 8), glow));
        // another value of the same bar, unlit: a layer of it alone, without a glow
        let next = sender.records(&ground, entry(2, bar(vec![], false)), 1, None, false);
        assert_eq!(kinds_of(&next), [BAR_LAYER, SCENE]);
        assert_eq!(word(&next[0][16..], 12), 0);
        assert_eq!(word(next.last().unwrap(), 12), 1);
        // the piece known: its layers again, the bar kept for it
        let known = sender.records(&ground, vec![Entry::Known(2)], 2, None, false);
        assert_eq!(kinds_of(&known), [SCENE]);
        let after = sender.records(&ground, entry(3, bar(lit.clone(), true)), 3, None, false);
        assert_eq!(kinds_of(&after), [BAR_LAYER, SCENE]);
        // a frame without it forgets it: it goes again with the next that has it
        sender.records(&ground, vec![], 4, None, false);
        let again = sender.records(&ground, entry(4, bar(lit.clone(), true)), 5, None, false);
        assert_eq!(kinds_of(&again), [BAR, BAR_LAYER, SCENE]);
        // an engine that makes no bars takes no bar (its pixels come from the app)
        let mut plain = SceneSender::with(true, true);
        let parts = plain.records(&ground, entry(5, bar(lit, true)), 0, None, false);
        assert!(!kinds_of(&parts).contains(&BAR));
    }

    #[test]
    fn a_bar_the_engine_would_refuse_is_not_one_it_takes() {
        let base = bar_base(false);
        assert!(base.fits());
        let bar = |spans: Vec<(f64, f64)>, glow: bool| BarLayer {
            base: Rc::new(bar_base(false)),
            spans,
            colour: [1, 2, 3, 4],
            to: None,
            glow,
        };
        assert!(bar(vec![(0.0, 2.0)], false).fits());
        // (a glow where its base has none; spans it does not know)
        assert!(!bar(vec![(0.0, 2.0)], true).fits());
        for span in [
            (2.0, 1.0),
            (f64::NAN, 1.0),
            (0.0, f64::INFINITY),
            (-2e6, 0.0),
            (0.0, 1e6),
        ] {
            assert!(!bar(vec![span], false).fits(), "{span:?}");
        }
        assert!(!bar(vec![(0.0, 1.0); NATIVE_SPANS + 1], false).fits());
        // (parts not of its sizes, a light thicker than the bar, a halo of none)
        let mut short = bar_base(false);
        short.short.pop();
        short.short.pop();
        assert!(!short.fits());
        let mut thick = bar_base(false);
        thick.shape.core = 5;
        assert!(!thick.fits());
        let mut soft = bar_base(false);
        soft.shape.halo = 0.0;
        assert!(!soft.fits());
        let mut far = bar_base(true);
        if let Some(glow) = far.glow.as_mut() {
            glow.area.0 = 1 << 40;
        }
        assert!(!far.fits());
    }
}
