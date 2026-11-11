//! What plugins draw (render/renderer.py `_draw_plugin`, `_screen_image`): widget types and
//! screens of installed plugins, which are Python code. The app gives the renderer a [`Plugins`]
//! that has them drawn (its plugin host); without one, a frame that holds either is not made here.

use std::rc::Rc;

use lp_pixels::Image;
use lp_py::Dict;

use crate::piece::Piece;
use crate::snapshot::Snapshot;

/// What a plugin's drawing is told of the frame, beyond the readings (RenderContext's fps,
/// continuous and progress).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Frame {
    pub now: f64,
    pub fps: f64,
    pub continuous: bool,
    /// how far `now` is from the last reading to the next, 0 to 1 (Renderer.progress)
    pub progress: f64,
}

/// A plugin's drawing: its piece or picture, and whether it asks for the next frame at full
/// rate (context.keep_moving, a screen's `moving`).
pub struct Drawing<T> {
    pub made: Option<T>,
    pub moving: bool,
    /// what the drawing warned of (the renderer's warnings)
    pub warnings: Vec<String>,
}

pub trait Plugins {
    /// The piece a plugin's widget type draws of `widget` (its effects applied, as `_cached`
    /// applies them); None where it draws nothing, or no plugin has the type (the theme loader
    /// has warned). Err: it failed (the widget is left out, with a warning).
    fn widget(
        &self,
        widget: &Dict,
        snapshot: &Snapshot,
        frame: &Frame,
    ) -> Result<Drawing<Rc<Piece>>, String>;

    /// The picture of the theme's screen (theme.screen), RGB or RGBA; None where no plugin has
    /// it or it failed to start. Err: it failed this frame.
    fn screen(&self, snapshot: &Snapshot, frame: &Frame) -> Result<Drawing<Image>, String>;
}
