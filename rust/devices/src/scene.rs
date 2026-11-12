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

/// The records of a frame of layers (wire.rs): its ground, a layer, the frame; a picture the
/// engine keeps, a layer it cuts from such pictures, and a picture it grows from one it keeps.
const GROUND: u8 = 9;
const LAYER: u8 = 10;
const SCENE: u8 = 11;
const SOURCE: u8 = 14;
const VIEW: u8 = 15;
const GROWN: u8 = 20;

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

/// A piece the engine has: kept alive (its key stays its own), its layers, and the pictures
/// they are cut from.
type Had = (Rc<dyn Any>, Vec<u32>, Vec<usize>);

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
    /// whether the engine cuts layers from pictures it keeps ("views"), and grows a picture
    /// from one it keeps ("grown")
    views: bool,
    grown: bool,
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

    /// Whether the engine cuts layers from pictures it keeps (VIEW records).
    pub fn views(&self) -> bool {
        self.views
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
        for entry in entries {
            match entry {
                Entry::Known(key) => {
                    if let Some(had) = now.get(&key).or_else(|| self.pieces.get(&key)) {
                        let had = (Rc::clone(&had.0), had.1.clone(), had.2.clone());
                        order.extend_from_slice(&had.1);
                        // (the pictures its layers are cut from stay)
                        for at in &had.2 {
                            if let Some(source) = self.sources.get(at) {
                                sources.insert(*at, (Rc::clone(&source.0), source.1));
                            }
                        }
                        now.insert(key, had);
                    }
                }
                Entry::New { key, keep, layers } => {
                    let (mut ids, mut cut_from) = (Vec::new(), Vec::new());
                    for layer in &layers {
                        if let Some((id, keys)) = self.view(&mut parts, layer, &mut sources) {
                            ids.push(id);
                            cut_from.extend(keys);
                        } else if let Some(id) = self.layer(&mut parts, layer) {
                            ids.push(id);
                        }
                    }
                    order.extend_from_slice(&ids);
                    now.insert(key, (keep, ids, cut_from));
                }
            }
        }
        self.pieces = now;
        self.sources = sources;
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
                },
                Layer {
                    image: &light,
                    x: 10,
                    y: 10,
                    how: How::Add,
                    cut: None,
                    left_out: None,
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
            }],
        }];
        let parts = plain.records(&Ground::Colour([0, 0, 0]), entries, 1, None, false);
        assert_eq!(kinds(&parts)[1].0, LAYER);
    }
}
