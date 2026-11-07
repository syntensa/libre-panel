//! What the panels' drivers share (devices/base.py): their errors, what they count, and a
//! picture dimmed for a panel whose brightness goes into its pixels.

use std::fmt;

use lp_pixels::Image;

/// What keeps a panel from showing (DeviceError): it is gone, or did not answer as it should.
/// The main loop connects again.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DeviceError(pub String);

impl fmt::Display for DeviceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for DeviceError {}

impl From<lp_pixels::Error> for DeviceError {
    fn from(e: lp_pixels::Error) -> DeviceError {
        DeviceError(e.0)
    }
}

impl From<lp_decode::Error> for DeviceError {
    fn from(e: lp_decode::Error) -> DeviceError {
        DeviceError(e.0)
    }
}

pub type Result<T> = std::result::Result<T, DeviceError>;

/// What a driver counts while the panel runs (Counters), for the editor's diagnostics: the
/// bytes it sent, the errors on its link that made it connect again.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Counters {
    pub bytes: u64,
    pub usb_errors: u64,
}

/// dimmed: `picture` at `percent` of its brightness (100 and more: as it is), each value
/// through int(value * level), the brightness of a panel that has no command for it.
pub fn dimmed(picture: &Image, percent: i64) -> Result<Image> {
    if percent >= 100 {
        return Ok(picture.clone());
    }
    let level = percent.max(0) as f64 / 100.0;
    let once: Vec<i64> = (0..256).map(|v| (f64::from(v) * level) as i64).collect();
    Ok(picture.point(&once.repeat(picture.bands()))?)
}
