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

/// How the message of a frame that cannot be shown begins (FrameError): the frame is
/// skipped, the panel stays open.
pub const FRAME_ERROR: &str = "frame too detailed for the panel";
/// How the drivers' FrameErrors begin, each as its driver says it (the USB screens' too).
const FRAME_ERRORS: [&str; 3] = [
    FRAME_ERROR,
    "frame too detailed for the display",
    "a picture of ",
];

impl DeviceError {
    /// Whether it is the frame that cannot be shown, not the panel that failed.
    pub fn is_frame(&self) -> bool {
        FRAME_ERRORS.iter().any(|start| self.0.starts_with(start))
    }
}

impl From<lp_pixels::Error> for DeviceError {
    fn from(e: lp_pixels::Error) -> DeviceError {
        DeviceError(e.0)
    }
}

impl From<lp_decode::Error> for DeviceError {
    fn from(e: lp_decode::Error) -> DeviceError {
        DeviceError(e.msg)
    }
}

pub type Result<T> = std::result::Result<T, DeviceError>;

/// What a driver counts while the panel runs (Counters), for the editor's diagnostics: the
/// bytes it sent, the errors on its link that made it connect again.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Counters {
    /// sent to the panel
    pub bytes: u64,
    /// errors on the panel's link that made the driver connect again
    pub usb_errors: u64,
    /// video: pictures handed to the panel's decoder
    pub blocks: u64,
    /// video: times the sender waited for room in the panel's queue
    pub waits: u64,
    /// video: pictures thrown away unsent
    pub drops: u64,
    /// video: the deepest queue the panel reported
    pub max_depth: u64,
    /// video: blocks the panel took longer than a frame to accept
    pub slow_blocks: u64,
    /// video: restarts of the panel to clear a hung decoder
    pub panel_restarts: u64,
}

/// The places on the bus that panels of this process have open (take_place, leave_place,
/// place_taken): two panels of one model are each on a place of their own.
fn taken() -> std::sync::MutexGuard<'static, std::collections::HashSet<String>> {
    static TAKEN: std::sync::OnceLock<std::sync::Mutex<std::collections::HashSet<String>>> =
        std::sync::OnceLock::new();
    TAKEN
        .get_or_init(Default::default)
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

/// take_place: note that a panel at `place` is open; false where one of this process has it
/// already.
pub fn take_place(place: &str) -> bool {
    taken().insert(place.to_string())
}

pub fn leave_place(place: &str) {
    taken().remove(place);
}

pub fn place_taken(place: &str) -> bool {
    taken().contains(place)
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

/// t(text, **values) with text values, as the drivers' messages take them (a message that
/// cannot take them says why, as Python's would fail).
pub fn tf(text: &str, values: &[(&str, &str)]) -> String {
    let values: Vec<(&str, lp_py::Value)> = values
        .iter()
        .map(|(name, value)| (*name, lp_py::Value::Str((*value).to_string())))
        .collect();
    lp_i18n::t_format(text, &values).unwrap_or_else(|why| why.msg)
}
