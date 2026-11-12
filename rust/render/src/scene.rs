//! A frame as its layers, for an engine that composes them (render/scene.py): the renderer
//! hands out the ground and its pieces in their order instead of composing them, where every
//! piece is one the engine lays as the renderer would. Its pixels are made only when something
//! asks for them (a transition that starts from it), composed then as the renderer composes.

use std::rc::Rc;

use lp_pixels::{Image, Mode};

use crate::compose;
use crate::piece::Piece;
use crate::piece::{Failed, Result};

/// What lies under a frame's pieces: the theme's colour, or its picture (RGB, the frame's
/// size; the same Rc while the renderer keeps it).
#[derive(Clone)]
pub enum Ground {
    Colour([u8; 3]),
    Picture(Rc<Image>),
}

/// A frame of `size`: `ground` with `pieces` (widget id, piece) laid on it in turn. Its pieces
/// are never changed: the renderer hands out new ones.
#[derive(Clone)]
pub struct SceneFrame {
    pub size: (i64, i64),
    pub ground: Ground,
    pub pieces: Vec<(String, Rc<Piece>)>,
}

/// Whether the engine composes `piece` as the renderer does: no frosted glass beneath it (the
/// engine has no blur of what lies under a piece), and layers it takes as they are: through
/// their alpha (RGBA), light that adds (RGBA, RGB or L).
pub fn layerable(piece: &Piece) -> bool {
    if piece.backdrop.is_some() {
        return false;
    }
    let light = |layer: &Image| matches!(layer.mode(), Mode::RGBA | Mode::RGB | Mode::L);
    if piece.add {
        return light(&piece.layer);
    }
    piece.layer.mode() == Mode::RGBA && piece.light.as_ref().is_none_or(|l| light(&l.layer))
}

impl SceneFrame {
    /// The frame (RGB) as the renderer composes it: its ground with each piece laid on it in
    /// turn (onto the opaque frame).
    pub fn compose(&self) -> Result<Image> {
        let (w, h) = (self.size.0.max(0) as usize, self.size.1.max(0) as usize);
        let mut canvas = match &self.ground {
            Ground::Colour(rgb) => Image::new(Mode::RGB, w, h, rgb),
            Ground::Picture(picture) => {
                if picture.mode() != Mode::RGB {
                    return Err(Failed("a frame's ground is not RGB".into()));
                }
                (**picture).clone()
            }
        };
        for (_, piece) in &self.pieces {
            compose::composite(&mut canvas, piece, (0, 0), true)?;
        }
        Ok(canvas)
    }
}
