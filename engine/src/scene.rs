//! A frame composed in the engine from its layers (stage 2 of docs/ROADMAP.md, "layers
//! instead of drawing anew"): the theme's ground, and the pieces Libre Panel draws
//! (render/renderer.py's), each sent once; a frame is the order they lie in, bottom first.
//! Where a layer came or went since the frame before, the frame is composed anew, pixel for
//! pixel as Renderer._compose composes it (Pillow's paste through a layer's alpha onto the
//! opaque frame; ImageChops.add for light; a video's frame as it is), and handed on as the
//! boxes of one of Libre Panel's own frames would be (yuv.rs compares them block by block).

use std::collections::{HashMap, HashSet};

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

#[derive(Clone)]
pub struct Layer {
    x: i64,
    y: i64,
    w: usize,
    how: How,
    pixels: Vec<u8>,
    /// The part of the layer that changes what lies under it (x0, y0, x1, y1 in the layer):
    /// where its alpha is not 0 (Over), its light not black (Add); all of it (Solid).
    part: (usize, usize, usize, usize),
    /// The ring it is a picture of (ring.rs), which stays while it does.
    ring: Option<u32>,
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
            // each row of blocks: the layers of the frame that reach into it
            let mut reach: Vec<Vec<usize>> = vec![Vec::new(); self.rows];
            for (k, id) in self.order.iter().enumerate() {
                if let Some((_, y0, _, y1)) = self.cut(&self.layers[id]) {
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
                        self.compose_block(r, c, layers, &fresh, &mut pixels, (x0, y0, x1));
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
        fresh: &HashSet<u32>,
        out: &mut [u8],
        (bx0, by0, bx1): (usize, usize, usize),
    ) {
        let (x0, y0) = (c * self.block, r * self.block);
        let (x1, y1) = (
            ((c + 1) * self.block).min(self.width),
            ((r + 1) * self.block).min(self.height),
        );
        let (w, h) = (x1 - x0, y1 - y0);
        let over: Vec<u32> = reach
            .iter()
            .map(|&k| self.order[k])
            .filter(|id| {
                self.cut(&self.layers[id])
                    .is_some_and(|(lx0, ly0, lx1, ly1)| {
                        lx0 < x1 && lx1 > x0 && ly0 < y1 && ly1 > y0
                    })
            })
            .collect();
        let index = r * self.cols + c;
        let start = match &self.under[index] {
            Some(u) if u.pixels.len() == w * h * 3 && over.starts_with(&u.below) => u.below.len(),
            _ => 0,
        };
        let mut pixels = match (&self.under[index], start) {
            (Some(u), s) if s > 0 => u.pixels.clone(),
            _ => self.ground_of((x0, y0, x1, y1)),
        };
        // (below the first layer new in this frame: kept for the frames after)
        let new_at = over
            .iter()
            .position(|id| fresh.contains(id))
            .unwrap_or(over.len());
        for (k, id) in over.iter().enumerate().skip(start) {
            if k == new_at && k > start {
                self.under[index] = Some(Under {
                    below: over[..k].to_vec(),
                    pixels: pixels.clone(),
                });
            }
            lay(&self.layers[id], &mut pixels, (x0, y0, x1, y1));
        }
        if new_at == over.len() && over.len() > start {
            self.under[index] = Some(Under {
                below: over,
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
fn lay(layer: &Layer, out: &mut [u8], (x0, y0, x1, y1): Rect) {
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
/// each channel (out (255 - a) + in a + 128), divided by 255 the way Pillow rounds.
fn over(dst: &mut [u8], src: &[u8]) {
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
