//! What the app asks of a panel's driver (base.py's Display), for the panels that are neither
//! a TURZX USB panel (turzx: Libre Panel's engine has those) nor the Hongtai family: the serial
//! panels and the USB screens of other makers.

use lp_pixels::Image;

use crate::base::{Counters, Result};
use crate::models::PanelModel;

/// A box of a frame that changed: x0, y0, x1, y1.
pub type Region = (i64, i64, i64, i64);

pub trait Driver: Send {
    /// The panel this is (known once it is open).
    fn model(&self) -> Option<&'static PanelModel>;
    /// What frames go to, for status displays.
    fn describe(&self) -> String;
    /// Push `frame` (RGB, the theme's size); `region` is the changed box, which drivers that
    /// update a part send alone.
    fn show(&mut self, frame: &Image, region: Option<Region>) -> Result<()>;
    /// 0-100; drivers without brightness control take it into the pixels or leave it.
    fn set_brightness(&mut self, percent: i64) -> Result<()>;
    fn counters(&self) -> Counters;
    /// Every frame at `stream_fps`, changed or not (a display that takes whole pictures fast).
    fn streaming(&self) -> bool {
        false
    }
    fn stream_fps(&self) -> i64 {
        0
    }
    /// A slow link (pixels over a serial port): the main loop keeps the motion calm and sends
    /// the parts that changed.
    fn slow(&self) -> bool {
        false
    }
    /// The kind of picture a frame goes to the panel as (the doctor's speed check names it).
    fn picture(&self) -> String {
        "RGB565".into()
    }
    /// The layout of the JPEGs it sends, where it has a choice.
    fn jpeg_layout(&self) -> Option<String> {
        None
    }
    fn set_jpeg(&mut self, _layout: &str) -> Result<()> {
        Ok(())
    }
    /// The panel's own command that turns its screen off, where it has one; whether it did.
    fn screen_off(&mut self) -> bool {
        false
    }
    /// The device let go; safe to call twice.
    fn close(&mut self);
}
