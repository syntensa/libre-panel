//! A widget drawn (render/renderer.py's Piece): an RGBA layer placed on the panel, and what goes
//! with it: the box the editor hits, a frosted backdrop, light added onto what lies beneath.

use lp_pixels::Image;

/// Why a widget is not drawn: what Python raised (the renderer leaves the widget out and warns,
/// as Python's does: one broken widget must not blank the panel).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Failed(pub String);

impl std::fmt::Display for Failed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl From<lp_pixels::Error> for Failed {
    fn from(e: lp_pixels::Error) -> Failed {
        Failed(e.0)
    }
}

pub type Result<T> = std::result::Result<T, Failed>;

/// What a picture was grown from (render/scene.py's grew): its first `keep` columns are
/// `base`'s from column `shift` on (a graph's strip grown from the one before).
#[derive(Clone, Debug)]
pub struct Growth {
    pub base: std::rc::Weak<Image>,
    pub shift: i64,
    pub keep: i64,
}

/// A part a piece's layer is cut from (render/scene.py's View): `image` from (sx, sy), w x h,
/// pasted at (dx, dy) on a clear picture of the layer's size (nothing where it lies off
/// `image`); `growth` where `image` was grown from another.
#[derive(Clone, Debug)]
pub struct CutPart {
    pub image: std::rc::Rc<Image>,
    pub growth: Option<Growth>,
    pub sx: i64,
    pub sy: i64,
    pub dx: i64,
    pub dy: i64,
    pub w: i64,
    pub h: i64,
}

/// A rendered widget: its layer (RGBA) with its corner at (x, y).
#[derive(Clone, Debug)]
pub struct Piece {
    pub layer: Image,
    pub x: i64,
    pub y: i64,
    /// the box the editor hits: [x, y, w, h] (the layer's where None)
    pub hit: Option<[i64; 4]>,
    /// an L mask the size of `hit`: the panel beneath blurred first, by `backdrop_radius`
    pub backdrop: Option<Image>,
    pub backdrop_radius: f32,
    /// light: added onto what lies beneath (the card slot's line of light)
    pub add: bool,
    /// light added onto the panel after the layer: a glow
    pub light: Option<Box<Piece>>,
    /// what its layer is cut from, where it is so cut (a scrolling graph's window from its
    /// strip): an engine that keeps those pictures cuts it itself (render/scene.py's View)
    pub cut: Option<Vec<CutPart>>,
    /// the layer's size where its pixels are left out, `cut` giving them (render/scene.py's
    /// View: made only where something asks for them, [`Piece::whole`]); `layer` is then empty
    pub left_out: Option<(usize, usize)>,
}

impl Piece {
    /// A piece of `layer` alone at (x, y).
    pub fn new(layer: Image, x: i64, y: i64) -> Piece {
        Piece {
            layer,
            x,
            y,
            hit: None,
            backdrop: None,
            backdrop_radius: 0.0,
            add: false,
            light: None,
            cut: None,
            left_out: None,
        }
    }

    /// The size of its layer, its pixels left out or not.
    pub fn size(&self) -> (usize, usize) {
        self.left_out.unwrap_or_else(|| self.layer.size())
    }

    /// Its layer's pixels: the layer, or where they are left out, cut from what `cut` names
    /// (each part pasted on a clear picture of the layer's size, as View.image makes it).
    pub fn whole(&self) -> std::borrow::Cow<'_, Image> {
        use std::borrow::Cow;
        let (Some((w, h)), Some(cut)) = (self.left_out, &self.cut) else {
            return Cow::Borrowed(&self.layer);
        };
        if let [part] = cut.as_slice() {
            if (part.dx, part.dy, part.w, part.h) == (0, 0, w as i64, h as i64) {
                return Cow::Owned(part.image.crop(
                    part.sx,
                    part.sy,
                    part.sx + part.w,
                    part.sy + part.h,
                ));
            }
        }
        let mut made = Image::blank(lp_pixels::Mode::RGBA, w, h);
        for part in cut {
            let piece = part
                .image
                .crop(part.sx, part.sy, part.sx + part.w, part.sy + part.h);
            // (RGBA onto RGBA without a mask: it cannot fail)
            let _ = made.paste(&piece, part.dx, part.dy, None);
        }
        Cow::Owned(made)
    }

    /// The piece with its layer's pixels, where they were left out.
    pub fn filled(&self) -> std::borrow::Cow<'_, Piece> {
        use std::borrow::Cow;
        if self.left_out.is_none() {
            return Cow::Borrowed(self);
        }
        let mut piece = self.clone();
        piece.layer = self.whole().into_owned();
        piece.left_out = None;
        Cow::Owned(piece)
    }

    /// piece.box or [x, y, width, height] of its layer.
    pub fn bounds(&self) -> [i64; 4] {
        let (w, h) = self.size();
        self.hit.unwrap_or([self.x, self.y, w as i64, h as i64])
    }
}
