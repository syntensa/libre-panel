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
        }
    }

    /// piece.box or [x, y, width, height] of its layer.
    pub fn bounds(&self) -> [i64; 4] {
        self.hit.unwrap_or([
            self.x,
            self.y,
            self.layer.width() as i64,
            self.layer.height() as i64,
        ])
    }
}
