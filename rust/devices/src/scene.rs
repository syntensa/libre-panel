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

/// The records of a frame of layers (wire.rs): its ground, a layer, the frame.
const GROUND: u8 = 9;
const LAYER: u8 = 10;
const SCENE: u8 = 11;

/// How a layer lies on the frame (scene.rs's How): through its alpha, as it is, or added.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum How {
    Over = 0,
    Solid = 1,
    Add = 2,
}

/// A layer of a piece: its picture at x, y, laid so.
pub struct Layer<'a> {
    pub image: &'a Image,
    pub x: i64,
    pub y: i64,
    pub how: How,
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

/// What one stream of the engine has of the frames' layers. Used by the stream's writer alone.
#[derive(Default)]
pub struct SceneSender {
    ground: Option<(Kept, Option<Rc<Image>>)>,
    /// each piece's layers by its key, and the piece kept alive (its key stays its own)
    pieces: HashMap<usize, (Rc<dyn Any>, Vec<u32>)>,
    next: u32,
    /// layers sent, and their bytes
    pub layers: u64,
    pub bytes: u64,
}

impl SceneSender {
    pub fn new() -> SceneSender {
        SceneSender::default()
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
        let mut image = layer.image;
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
        let mut now: HashMap<usize, (Rc<dyn Any>, Vec<u32>)> =
            HashMap::with_capacity(entries.len());
        for entry in entries {
            match entry {
                Entry::Known(key) => {
                    if let Some(had) = now.get(&key).or_else(|| self.pieces.get(&key)) {
                        let had = (Rc::clone(&had.0), had.1.clone());
                        order.extend_from_slice(&had.1);
                        now.insert(key, had);
                    }
                }
                Entry::New { key, keep, layers } => {
                    let ids: Vec<u32> = layers
                        .iter()
                        .filter_map(|layer| self.layer(&mut parts, layer))
                        .collect();
                    order.extend_from_slice(&ids);
                    now.insert(key, (keep, ids));
                }
            }
        }
        self.pieces = now;
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
    }
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
                },
                Layer {
                    image: &light,
                    x: 10,
                    y: 10,
                    how: How::Add,
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
}
