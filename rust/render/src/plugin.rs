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

    /// A plugin's transition `name` with `params`, made for this renderer's theme: None where
    /// no plugin has it; Err where it failed to start.
    fn transition(&self, _name: &str, _params: &Dict) -> Option<Result<Started, String>> {
        None
    }

    /// A plugin's toast style `name` with its `options`, for this renderer's theme: None where
    /// no plugin has it; Err where it failed to start.
    fn toast_style(
        &self,
        _name: &str,
        _options: &Dict,
        _anchor: &str,
    ) -> Option<Result<Box<dyn PluginToastStyle>, String>> {
        None
    }

    /// Which of the drawings this is (a toast style that goes on is bound to it).
    fn handle(&self) -> i64 {
        0
    }
}

/// What a plugin's transition says of itself (Transition's duration, switch, toasts and
/// from_shown).
#[derive(Clone, Debug, PartialEq)]
pub struct TransitionInfo {
    pub duration: f64,
    pub switch: String,
    pub toasts: String,
    pub from_shown: bool,
}

/// A plugin's transition, playing.
pub trait PluginTransition {
    /// frame(old, new, t): the frame it shows `t` of the way (RGB, the frames' size).
    fn frame(&mut self, old: &Image, new: &Image, t: f64) -> Result<Image, String>;

    /// on_switch(theme, mode, mode_changed): what a switch does while it plays.
    fn on_switch(
        &mut self,
        theme: &str,
        mode: Option<&str>,
        mode_changed: bool,
    ) -> Result<String, String>;
}

/// A started plugin transition: what it says of itself, and it.
pub type Started = (TransitionInfo, Box<dyn PluginTransition>);

/// A plugin's toast style (ToastStyle), started for a theme.
pub trait PluginToastStyle {
    /// draw(frame, toast, age, previous): the frame with the toast on it.
    fn draw(
        &self,
        frame: &Image,
        toast: &crate::overlays::Toast,
        age: f64,
        previous: Option<(&crate::overlays::Toast, f64)>,
    ) -> Result<Image, String>;

    /// lifetime(toast, previous): seconds from its arrival until the toast is gone.
    fn lifetime(
        &self,
        toast: &crate::overlays::Toast,
        previous: Option<(&crate::overlays::Toast, f64)>,
    ) -> Result<f64, String>;

    /// hold_s(toast, theme_seconds): how long a toast without its own seconds holds.
    fn hold_s(&self, toast: &crate::overlays::Toast, theme_seconds: f64) -> Result<f64, String>;

    /// The time a toast takes to leave after its hold time (leave_s).
    fn leave_s(&self) -> f64;

    /// The style goes on with a new theme (the same style and options at the same size): its
    /// context becomes that theme's, from what draws its plugins.
    fn rebind(&self, plugins: &dyn Plugins, anchor: &str);
}
