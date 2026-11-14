//! JONSBO DS916 (devices/jonsbo_ds916.py): a 9.16" screen of 462x1920, "HLVMAX" firmware (USB
//! 33c3:f101) behind a USB serial port that takes whole JPEG pictures.
//!
//! - F0 A5 5A 0F asks what it is: 26 ASCII characters, its model (7), the size of its
//!   framebuffer, which stands on end ("0462*1920"), and its serial number.
//! - A picture is a JPEG of the framebuffer's size, written as it is.
//! - The last picture goes again every half second while nothing changes; the brightness goes
//!   into the pixels.

use lp_pixels::Image;

use crate::base::{dimmed, leave_place, take_place, tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::keep::Keeper;
use crate::link::Link;
use crate::models::{find_model, PanelModel};
use crate::ports::FoundPort;

pub const MODEL_ID: &str = "jonsbo-ds916";
const HELLO: [u8; 4] = [0xF0, 0xA5, 0x5A, 0x0F];
const ANSWER: usize = 26;
pub const MAX_JPEG: usize = 256 * 1024;
pub const QUALITIES: [i64; 5] = [90, 80, 65, 50, 30];
const AGAIN_S: f64 = 0.5;

/// jpeg: `native` as a JPEG small enough for the panel (the lowest quality where none is).
pub fn jpeg(native: &Image) -> Result<Vec<u8>> {
    let mut data = Vec::new();
    for quality in QUALITIES {
        data = lp_decode::save_jpeg(native, quality)?;
        if data.len() <= MAX_JPEG {
            break;
        }
    }
    Ok(data)
}

/// to_native: a frame turned and sized into the framebuffer, which stands on end.
pub fn to_native(frame: &Image, size: (i64, i64)) -> Result<Image> {
    crate::ms9132::to_native(frame, size)
}

/// The identity a panel says: its model, width and height ("VMAXB16" "0462*1920" ...); None
/// where it is not of that shape (IDENTITY's match).
pub fn identity(answer: &str) -> Option<(String, i64, i64)> {
    let chars: Vec<char> = answer.chars().collect();
    // (re's `.` takes no line feed)
    if chars.len() < 7 || chars[..7].contains(&'\n') {
        return None;
    }
    let name: String = chars[..7].iter().collect();
    let rest: String = chars[7..].iter().collect();
    let (width, tail) = rest.split_once('*')?;
    let digits = |text: &str| -> Option<i64> {
        let count = text
            .chars()
            .take_while(|c| lp_py::decimal(*c).is_some())
            .count();
        if !(3..=4).contains(&count) {
            return None;
        }
        text.chars()
            .take(count)
            .try_fold(0i64, |n, c| Some(n * 10 + i64::from(lp_py::decimal(c)?)))
    };
    // (\d{3,4}) before the star is all of the width; after it, the first 3 or 4 digits
    let width_digits = width.chars().count();
    if !(3..=4).contains(&width_digits) || digits(width).is_none() {
        return None;
    }
    let lead: String = tail
        .chars()
        .take_while(|c| lp_py::decimal(*c).is_some())
        .collect();
    let height_text: String = lead.chars().take(4).collect();
    if height_text.chars().count() < 3 {
        return None;
    }
    Some((name, digits(width)?, digits(&height_text)?))
}

/// What the keeper guards.
pub struct Port {
    link: Option<Box<dyn Link>>,
    place: String,
    counters: Counters,
    jpeg: Vec<u8>,
}

impl Port {
    fn send(&mut self, data: &[u8]) -> Result<()> {
        let Some(link) = self.link.as_mut() else {
            return Err(DeviceError("panel not open".into()));
        };
        match link.write(data) {
            Ok(()) => {
                self.counters.bytes += data.len() as u64;
                self.jpeg = data.to_vec();
                Ok(())
            }
            Err(e) => {
                self.counters.usb_errors += 1;
                Err(e)
            }
        }
    }

    fn again(port: &mut Port) -> Result<()> {
        if port.link.is_some() && !port.jpeg.is_empty() {
            let last = port.jpeg.clone();
            return port.send(&last);
        }
        Ok(())
    }
}

/// The DS916 (Ds916Display).
pub struct Ds916 {
    pub model: &'static PanelModel,
    port: String,
    pub native: (i64, i64),
    brightness: i64,
    picture: Option<Image>,
    keeper: Keeper<Port>,
}

impl Ds916 {
    pub fn new(model: Option<&'static PanelModel>, port: &str) -> Ds916 {
        let model = model
            .or_else(|| find_model(MODEL_ID))
            .expect("the DS916 in the catalog");
        let link = Port {
            link: None,
            place: String::new(),
            counters: Counters::default(),
            jpeg: Vec::new(),
        };
        Ds916 {
            model,
            port: port.to_string(),
            native: (462, 1920),
            brightness: 100,
            picture: None,
            keeper: Keeper::new(AGAIN_S, link, Port::again, "ds916-keep"),
        }
    }

    pub fn open(&mut self) -> Result<()> {
        let Some(found) = crate::ports::find_port(Some(self.model), &self.port)? else {
            return Err(DeviceError(tf(
                "no {panel} found on a serial port",
                &[("panel", "JONSBO DS916")],
            )));
        };
        if !take_place(&found.device) {
            return Err(DeviceError(tf(
                "{port} is in use by another panel of Libre Panel",
                &[("port", &found.device)],
            )));
        }
        let link = crate::serial::SerialPort::open(&found.device, 115_200, false).map_err(|e| {
            leave_place(&found.device);
            DeviceError(tf(
                "cannot open {port}: {error}",
                &[("port", &found.device), ("error", &e.to_string())],
            ))
        })?;
        let place = found.device.clone();
        self.open_on(Box::new(link), &found, place)
    }

    /// open on `link`, the port of `found`, taken as `place` ("" for none).
    pub fn open_on(
        &mut self,
        mut link: Box<dyn Link>,
        found: &FoundPort,
        place: String,
    ) -> Result<()> {
        let mut said = None;
        let mut asked = || -> Result<()> {
            for _ in 0..2 {
                link.drain_input();
                link.write(&HELLO)?;
                let answer = link.read(ANSWER)?;
                // (decode("ascii", "replace"): a byte past ASCII is U+FFFD)
                let text: String = answer
                    .iter()
                    .map(|b| {
                        if b.is_ascii() {
                            char::from(*b)
                        } else {
                            '\u{FFFD}'
                        }
                    })
                    .collect();
                let text = text.trim_matches(['\0', '\r', '\n', ' ']);
                if let Some(identity) = identity(text) {
                    said = Some(identity);
                    break;
                }
            }
            Ok(())
        };
        let asked = asked();
        let failed = match (asked, said) {
            (Err(e), _) => Some(e),
            (Ok(()), None) => Some(DeviceError(tf(
                "the panel on {port} does not say what it is",
                &[("port", &found.device)],
            ))),
            (Ok(()), Some((_, width, height))) => {
                link.drain_input();
                if width != 0 && height != 0 {
                    self.native = (width, height);
                }
                self.model = found.model;
                None
            }
        };
        if let Some(e) = failed {
            link.close();
            if !place.is_empty() {
                leave_place(&place);
            }
            return Err(e);
        }
        self.keeper.with(|kept| {
            kept.inner.link = Some(link);
            kept.inner.place = place;
            kept.inner.jpeg.clear();
        });
        self.keeper.start();
        Ok(())
    }

    fn is_open(&self) -> bool {
        self.keeper.with(|kept| kept.inner.link.is_some())
    }
}

impl Driver for Ds916 {
    fn model(&self) -> Option<&'static PanelModel> {
        Some(self.model)
    }

    fn describe(&self) -> String {
        self.model.label()
    }

    fn show(&mut self, frame: &Image, _region: Option<Region>) -> Result<()> {
        if !self.is_open() {
            return Err(DeviceError("panel not open".into()));
        }
        let native = to_native(frame, self.native)?;
        let data = jpeg(&dimmed(&native, self.brightness)?)?;
        let mut checked = false;
        let shown = self.keeper.with(|kept| {
            kept.check()?;
            checked = true;
            kept.inner.send(&data)?;
            kept.sent();
            Ok(())
        });
        if checked {
            self.picture = Some(native);
        }
        shown
    }

    fn set_brightness(&mut self, percent: i64) -> Result<()> {
        self.brightness = percent.clamp(0, 100);
        if self.is_open() {
            if let Some(picture) = &self.picture {
                let data = jpeg(&dimmed(picture, self.brightness)?)?;
                self.keeper.with(|kept| {
                    kept.inner.send(&data)?;
                    kept.sent();
                    Ok::<(), DeviceError>(())
                })?;
            }
        }
        Ok(())
    }

    fn counters(&self) -> Counters {
        self.keeper.with(|kept| kept.inner.counters)
    }

    fn close(&mut self) {
        self.keeper.stop();
        self.keeper.with(|kept| {
            if let Some(mut link) = kept.inner.link.take() {
                link.close();
            }
            if !kept.inner.place.is_empty() {
                leave_place(&std::mem::take(&mut kept.inner.place));
            }
            kept.inner.jpeg.clear();
        });
        self.picture = None;
    }
}

impl Drop for Ds916 {
    fn drop(&mut self) {
        self.close();
    }
}
