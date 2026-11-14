//! BeadaPanel (NXElec, devices/beadapanel.py): USB screens from the 2.1" round one to the bars
//! of 8.8" and more, all on USB 4e58:1001; the panel says which it is.
//!
//! Interface 0 has two channels: Status-Link (bulk OUT 0x02, IN 0x82) for the panel's state,
//! Panel-Link (bulk OUT 0x01) for the pictures. A Status-Link message is "STATUS-LINK", version
//! 1, its type, 0, a sequence number (0), its length, a checksum of the 18 bytes before it, its
//! payload; type 1 asks for the panel's information, 2 resets the Panel-Link channel (a second
//! after it), 3 sets the backlight. A Panel-Link tag of 270 bytes starts the stream with its
//! format (RGB16 at the size the panel says); each picture follows as W x H pixels of RGB565,
//! little endian; the end tag ends it. The last picture goes again after a second without one.

use std::time::Duration;

use lp_pixels::{Filter, Image, Mode, Transpose};

use crate::base::{tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::keep::Keeper;
use crate::models::{find_model, PanelModel, MODELS};
use crate::usb_link::{self, Screen, Target, UsbError, UsbIo};

pub const PROTOCOL: &str = "usb-beada";
pub const VENDOR_ID: u16 = 0x4E58;
pub const PRODUCT_ID: u16 = 0x1001;
const STATUS_OUT: u8 = 0x02;
const STATUS_IN: u8 = 0x82;
const MEDIA_OUT: u8 = 0x01;
const GET_INFO: u8 = 1;
const RESET_LINK: u8 = 2;
const SET_BACKLIGHT: u8 = 3;
const START: u8 = 5;
const END: u8 = 6;
const START_V1: u8 = 1;
const END_V1: u8 = 2;
const INFO: usize = 80;
const RESET_SETTLE_S: f64 = 1.0;
const WRITE_TIMEOUT_MS: u64 = 3000;
const READ_TIMEOUT_MS: u64 = 2000;
const AGAIN_S: f64 = 1.0;

/// The vendor's model codes (the information's byte 5): its name and the catalog's model.
pub const CODES: [(u8, &str, &str); 22] = [
    (0, "5", "beadapanel-5"),
    (1, "7", "beadapanel-7"),
    (2, "6", "beadapanel-6"),
    (3, "3", "beadapanel-3"),
    (4, "4", "beadapanel-4"),
    (10, "5C", "beadapanel-5"),
    (11, "5T", "beadapanel-5"),
    (12, "7C", "beadapanel-7"),
    (13, "3C", "beadapanel-3"),
    (14, "4C", "beadapanel-4"),
    (15, "6C", "beadapanel-6"),
    (16, "6S", "beadapanel-6"),
    (17, "2", "beadapanel-2"),
    (18, "2W", "beadapanel-2w"),
    (19, "7S", "beadapanel-7s"),
    (20, "5S", "beadapanel-5s"),
    (21, "8", "beadapanel-8"),
    (22, "11", "beadapanel-11"),
    (23, "9", "beadapanel-9"),
    (24, "Y", "beadapanel-8"),
    (25, "X", "beadapanel-11"),
    (26, "Z", "beadapanel-9"),
];

fn code(code: u8) -> Option<(&'static str, &'static str)> {
    CODES
        .iter()
        .find(|(c, ..)| *c == code)
        .map(|(_, name, model)| (*name, *model))
}

pub fn models() -> Vec<&'static PanelModel> {
    MODELS.iter().filter(|m| m.protocol == PROTOCOL).collect()
}

/// present_model: a BeadaPanel on the bus (none opened): the first of the catalog until it
/// says which it is.
pub fn present_model() -> Option<&'static PanelModel> {
    usb_link::present(VENDOR_ID, &[PRODUCT_ID])?;
    models().into_iter().next()
}

/// checksum: the one's complement of the sum of `data` in 16-bit words (little endian), the
/// carries folded in.
pub fn checksum(data: &[u8]) -> u16 {
    let mut total: u64 = data
        .chunks(2)
        .map(|w| u64::from(w[0]) | (u64::from(*w.get(1).unwrap_or(&0)) << 8))
        .sum();
    total = (total >> 16) + (total & 0xFFFF);
    total += total >> 16;
    !(total as u16)
}

pub fn status_message(kind: u8, payload: &[u8]) -> Vec<u8> {
    let mut header = b"STATUS-LINK".to_vec();
    header.extend_from_slice(&[1, kind, 0]);
    header.extend_from_slice(&0u16.to_le_bytes());
    header.extend_from_slice(&((20 + payload.len()) as u16).to_le_bytes());
    let sum = checksum(&header);
    header.extend_from_slice(&sum.to_le_bytes());
    header.extend_from_slice(payload);
    header
}

pub fn panel_tag(kind: u8, form: &str) -> Vec<u8> {
    let mut body = b"PANEL-LINK".to_vec();
    body.extend_from_slice(&[1, kind]);
    let mut form = form.as_bytes().to_vec();
    form.resize(form.len().max(256), 0);
    body.extend_from_slice(&form);
    let sum = checksum(&body);
    body.extend_from_slice(&sum.to_le_bytes());
    body
}

pub fn stream_format(width: i64, height: i64) -> String {
    format!("video/x-raw, format=RGB16, width={width}, height={height}, framerate=0/1")
}

/// What the panel says of itself.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Info {
    pub firmware: u16,
    pub panel_link: u8,
    pub platform: u8,
    pub code: u8,
    pub size: (i64, i64),
    /// the highest backlight
    pub top: u8,
}

/// panel_info: what the panel says of itself; None for no answer of that shape.
pub fn panel_info(answer: &[u8]) -> Option<Info> {
    if answer.len() < 20 + INFO || &answer[..11] != b"STATUS-LINK" || answer[12] != GET_INFO {
        return None;
    }
    let info = &answer[20..20 + INFO];
    let u16_at = |at: usize| u16::from_le_bytes([info[at], info[at + 1]]);
    Some(Info {
        firmware: u16_at(0),
        panel_link: info[2],
        platform: info[4],
        code: info[5],
        size: (i64::from(u16_at(70)), i64::from(u16_at(72))),
        top: info[78],
    })
}

/// model_of: the catalog's model for the panel's code, else the first of its size.
pub fn model_of(code_: u8, size: (i64, i64)) -> Option<&'static PanelModel> {
    if let Some((_, id)) = code(code_) {
        return find_model(id);
    }
    let wanted = (size.0.min(size.1), size.0.max(size.1));
    models().into_iter().find(|m| {
        (
            m.native_width.min(m.native_height),
            m.native_width.max(m.native_height),
        ) == wanted
    })
}

/// to_native: a frame turned and sized into the picture the panel's stream has.
pub fn to_native(frame: &Image, size: (i64, i64)) -> Result<Image> {
    let mut picture = if (frame.width() > frame.height()) != (size.0 > size.1) {
        frame.transpose(Transpose::Rotate270)
    } else {
        frame.clone()
    };
    if (picture.width() as i64, picture.height() as i64) != size {
        picture = picture.resize(
            size.0 as usize,
            size.1 as usize,
            Filter::Lanczos,
            None,
            None,
        )?;
    }
    if picture.mode() != Mode::RGB {
        picture = picture.convert(Mode::RGB)?;
    }
    Ok(picture)
}

/// A BeadaPanel (BeadaDisplay).
pub struct Beada {
    pub model: Option<&'static PanelModel>,
    brightness: i64,
    info: Option<Info>,
    size: (i64, i64),
    /// how long the panel is given after its reset (the tests give it none)
    pub settle_s: f64,
    keeper: Keeper<Screen>,
}

impl Beada {
    /// `brightness`: [device] brightness, set as the panel opens.
    pub fn new(model: Option<&'static PanelModel>, brightness: i64) -> Beada {
        let label = model.map_or("BeadaPanel".into(), |m| m.label());
        Beada {
            model,
            brightness,
            info: None,
            size: (800, 480),
            settle_s: RESET_SETTLE_S,
            keeper: Keeper::new(
                AGAIN_S,
                Screen::new(label, Target::Endpoint(MEDIA_OUT), WRITE_TIMEOUT_MS),
                Screen::again,
                "beada-keep",
            ),
        }
    }

    pub fn open(&mut self) -> Result<()> {
        let (io, _) = usb_link::take(VENDOR_ID, &[PRODUCT_ID], "BeadaPanel")?;
        self.open_on(Box::new(io))
    }

    fn level(&self, percent: i64) -> u8 {
        let top = self.info.map_or(0, |info| info.top);
        let top = if top == 0 { 100.0 } else { f64::from(top) };
        (percent.clamp(0, 100) as f64 * top / 100.0).round_ties_even() as u8
    }

    fn status(io: &mut dyn UsbIo, kind: u8, payload: &[u8]) -> std::result::Result<(), UsbError> {
        io.write(STATUS_OUT, &status_message(kind, payload), WRITE_TIMEOUT_MS)
    }

    pub fn open_on(&mut self, mut io: Box<dyn UsbIo>) -> Result<()> {
        if let Err(e) = usb_link::claim(io.as_mut(), &[0]) {
            io.release();
            return Err(e);
        }
        let started = self.start(io.as_mut());
        match started {
            Ok(Ok(info)) => {
                let label = self.describe();
                self.keeper.with(|kept| {
                    kept.inner.io = Some(io);
                    kept.inner.label = label;
                    kept.inner.last.clear();
                });
                self.info = Some(info);
                self.keeper.start();
                Ok(())
            }
            Ok(Err(e)) => {
                self.info = None;
                io.release();
                Err(e)
            }
            Err(e) => {
                // (a USB error: the stream's end if it began, then let go)
                if let Some(info) = self.info.take() {
                    let end = if info.panel_link == 1 { END_V1 } else { END };
                    let _ = io.write(MEDIA_OUT, &panel_tag(end, ""), WRITE_TIMEOUT_MS);
                }
                io.release();
                self.keeper.with(|kept| kept.inner.counters.usb_errors += 1);
                Err(DeviceError(tf(
                    "the {panel} did not start: {error}",
                    &[("panel", &self.describe()), ("error", &e.to_string())],
                )))
            }
        }
    }

    /// The panel asked what it is and its stream started: its information; a USB error as
    /// such (outer), the panel's refusal as a DeviceError (inner).
    fn start(&mut self, io: &mut dyn UsbIo) -> std::result::Result<Result<Info>, UsbError> {
        Self::status(io, GET_INFO, &[])?;
        let answer = match io.read(STATUS_IN, 512, READ_TIMEOUT_MS) {
            Ok(answer) => answer,
            Err(UsbError::Timeout) => Vec::new(),
            Err(e) => return Err(e),
        };
        let Some(info) = panel_info(&answer).filter(|info| info.size.0 != 0 && info.size.1 != 0)
        else {
            return Ok(Err(DeviceError(tf(
                "the {panel} does not say what it is",
                &[("panel", "BeadaPanel")],
            ))));
        };
        let Some(model) = model_of(info.code, info.size) else {
            return Ok(Err(DeviceError(tf(
                "the {panel} is a kind Libre Panel does not know yet ({kind})",
                &[("panel", "BeadaPanel"), ("kind", &info.code.to_string())],
            ))));
        };
        self.info = Some(info);
        self.model = Some(model);
        self.size = info.size;
        Self::status(io, RESET_LINK, &[])?;
        if self.settle_s > 0.0 {
            std::thread::sleep(Duration::from_secs_f64(self.settle_s));
        }
        Self::status(io, SET_BACKLIGHT, &[self.level(self.brightness)])?;
        let start = if info.panel_link == 1 {
            START_V1
        } else {
            START
        };
        let tag = panel_tag(start, &stream_format(self.size.0, self.size.1));
        io.write(MEDIA_OUT, &tag, WRITE_TIMEOUT_MS)?;
        Ok(Ok(info))
    }

    fn is_open(&self) -> bool {
        self.keeper.with(|kept| kept.inner.io.is_some())
    }
}

impl Driver for Beada {
    fn model(&self) -> Option<&'static PanelModel> {
        self.model
    }

    fn describe(&self) -> String {
        self.model.map_or("BeadaPanel".into(), |m| m.label())
    }

    fn show(&mut self, frame: &Image, _region: Option<Region>) -> Result<()> {
        if !self.is_open() {
            return Err(DeviceError(tf(
                "the {panel} is not open",
                &[("panel", &self.describe())],
            )));
        }
        let pixels = crate::udisp::rgb565(&to_native(frame, self.size)?)?;
        self.keeper.with(|kept| {
            kept.check()?;
            kept.inner.picture(vec![pixels])?;
            kept.sent();
            Ok(())
        })
    }

    fn set_brightness(&mut self, percent: i64) -> Result<()> {
        if !self.is_open() {
            return Ok(());
        }
        let message = status_message(SET_BACKLIGHT, &[self.level(percent)]);
        self.keeper.with(|kept| {
            let Some(io) = kept.inner.io.as_mut() else {
                return Ok(());
            };
            match io.write(STATUS_OUT, &message, WRITE_TIMEOUT_MS) {
                Ok(()) => Ok(()),
                Err(e) => Err(kept.inner.stopped(&e)),
            }
        })
    }

    fn counters(&self) -> Counters {
        self.keeper.with(|kept| kept.inner.counters)
    }

    fn screen_off(&mut self) -> bool {
        if !self.is_open() {
            return false;
        }
        self.keeper.stop();
        let message = status_message(SET_BACKLIGHT, &[0]);
        self.keeper.with(|kept| {
            kept.inner
                .io
                .as_mut()
                .is_some_and(|io| io.write(STATUS_OUT, &message, WRITE_TIMEOUT_MS).is_ok())
        })
    }

    fn close(&mut self) {
        self.keeper.stop();
        let info = self.info.take();
        self.keeper.with(|kept| {
            if let Some(mut io) = kept.inner.io.take() {
                if let Some(info) = info {
                    let end = if info.panel_link == 1 { END_V1 } else { END };
                    let _ = io.write(MEDIA_OUT, &panel_tag(end, ""), WRITE_TIMEOUT_MS);
                }
                io.release();
            }
            kept.inner.last.clear();
        });
    }
}

impl Drop for Beada {
    fn drop(&mut self) {
        self.close();
    }
}
