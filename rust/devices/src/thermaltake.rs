//! Thermaltake's 6" LCD of 1480x720 (devices/thermaltake.py; USB 264a:2347), a HID device that
//! takes JPEG pictures. Reports of 1024 bytes without a report id; a command is text like an
//! HTTP request ("POST conn 1", then "SeqNumber=<n>", "Date=<ms>", for one with a body
//! "ContentType=json" and "ContentLength=<n>", an empty line and the body; lines end with CR
//! LF), framed: 5A 00, the length (the text's + 5), the text, a checksum, 5A 00. The panel
//! answers "200" where it took it. Opening: "POST conn 1", then "POST realtimeDisplay 1" with
//! {"enable":true}; closing the same with false. A picture is a JPEG in pieces of 1000 bytes,
//! each after a header of 24 bytes. The picture is dimmed here and goes again after a second
//! without one.

use std::time::Duration;

use lp_pixels::Image;

use crate::base::{dimmed, tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::keep::Keeper;
use crate::models::{find_model, PanelModel};
use crate::turzx_packet::{wall_clock, Clock};
use crate::usb_link::{self, HidReports, Screen, Target, UsbError, UsbIo};

pub const PROTOCOL: &str = "usb-thermaltake";
pub const VENDOR_ID: u16 = 0x264A;
pub const PRODUCT_ID: u16 = 0x2347;
pub const MODEL_ID: &str = "thermaltake-lcd-6";
pub const REPORT: usize = 1024;
const PIECE_HEADER: usize = 24;
pub const PIECE: usize = REPORT - PIECE_HEADER;
pub const MAX_JPEG: usize = 512 * 1024;
pub const QUALITIES: [i64; 4] = [85, 75, 65, 50];
const WRITE_TIMEOUT_MS: u64 = 5000;
const ANSWER_TIMEOUT_MS: u64 = 2000;
const ANSWER_PAUSE_S: f64 = 0.05;
const AGAIN_S: f64 = 1.0;

/// present_model: the LCD on the bus (not opened).
pub fn present_model() -> Option<&'static PanelModel> {
    usb_link::present(VENDOR_ID, &[PRODUCT_ID])?;
    find_model(MODEL_ID)
}

/// framed: a command's text, framed.
pub fn framed(text: &str) -> Result<Vec<u8>> {
    let content = text.as_bytes();
    let length = content.len() + 5;
    if length > 0xFF {
        return Err(DeviceError("a command is at most 250 bytes".into()));
    }
    let sum = (length + content.iter().map(|b| usize::from(*b)).sum::<usize>()) & 0xFF;
    let mut out = vec![0x5a, 0x00, length as u8];
    out.extend_from_slice(content);
    out.extend_from_slice(&[sum as u8, 0x5a, 0x00]);
    Ok(out)
}

/// command: `name` as the panel takes it, with its sequence number and the date `now`.
pub fn command(name: &str, number: i64, body: Option<&str>, now: f64) -> Result<Vec<u8>> {
    let mut lines = vec![
        name.to_string(),
        format!("SeqNumber={number}"),
        format!("Date={}", (now * 1000.0) as i64),
    ];
    if let Some(body) = body {
        lines.push("ContentType=json".into());
        lines.push(format!("ContentLength={}", body.chars().count()));
    }
    framed(&format!(
        "{}\r\n\r\n{}",
        lines.join("\r\n"),
        body.unwrap_or("")
    ))
}

/// pieces: a JPEG as the panel's reports.
pub fn pieces(jpeg: &[u8]) -> Vec<Vec<u8>> {
    let count = jpeg.len().div_ceil(PIECE).max(1);
    (0..count)
        .map(|number| {
            let mut report = vec![0u8; REPORT];
            report[0..4].copy_from_slice(&[0x5c, 0x03, 0xfd, 0x00]);
            report[4..6].copy_from_slice(&(count as u16).to_be_bytes());
            report[6..8].copy_from_slice(&(number as u16).to_be_bytes());
            report[8] = 0x01;
            let piece =
                &jpeg[(number * PIECE).min(jpeg.len())..((number + 1) * PIECE).min(jpeg.len())];
            report[PIECE_HEADER..PIECE_HEADER + piece.len()].copy_from_slice(piece);
            report
        })
        .collect()
}

pub fn jpeg(picture: &Image) -> Result<Vec<u8>> {
    let mut size = 0;
    for quality in QUALITIES {
        let data = lp_decode::save_jpeg(picture, quality)?;
        if data.len() <= MAX_JPEG {
            return Ok(data);
        }
        size = data.len();
    }
    Err(DeviceError(format!(
        "frame too detailed for the panel ({} KB)",
        size / 1024
    )))
}

pub fn to_native(frame: &Image, size: (i64, i64)) -> Result<Image> {
    crate::beada::to_native(frame, size)
}

/// Thermaltake's LCD (ThermaltakeDisplay).
pub struct Thermaltake {
    pub model: &'static PanelModel,
    interface: u8,
    reports: Option<HidReports>,
    number: i64,
    brightness: i64,
    picture: Option<Image>,
    /// the date the commands carry (the tests' a fixed one)
    pub clock: Clock,
    /// the pause between a command and the read of its answer
    pub pause_s: f64,
    keeper: Keeper<Screen>,
}

impl Thermaltake {
    pub fn new(model: Option<&'static PanelModel>) -> Thermaltake {
        let model = model
            .or_else(|| find_model(MODEL_ID))
            .expect("Thermaltake's LCD in the catalog");
        let empty = HidReports {
            interface: 0,
            size: REPORT,
            ids: false,
            out: None,
            into: None,
        };
        Thermaltake {
            model,
            interface: 0,
            reports: None,
            number: 100,
            brightness: 100,
            picture: None,
            clock: wall_clock,
            pause_s: ANSWER_PAUSE_S,
            keeper: Keeper::new(
                AGAIN_S,
                Screen::new(model.label(), Target::Hid(empty), WRITE_TIMEOUT_MS),
                Screen::again,
                "thermaltake-keep",
            ),
        }
    }

    fn size(&self) -> (i64, i64) {
        let (w, h) = (self.model.native_width, self.model.native_height);
        (w.max(h), w.min(h))
    }

    pub fn open(&mut self) -> Result<()> {
        let (io, _) = usb_link::take(VENDOR_ID, &[PRODUCT_ID], &self.model.label())?;
        self.open_on(Box::new(io))
    }

    pub fn open_on(&mut self, mut io: Box<dyn UsbIo>) -> Result<()> {
        self.interface = usb_link::hid_interface(io.as_ref()).unwrap_or(0);
        if let Err(e) = usb_link::claim(io.as_mut(), &[self.interface]) {
            io.release();
            return Err(e);
        }
        let reports = HidReports::new(io.as_ref(), self.interface, REPORT, false);
        self.reports = Some(reports.clone());
        let started = (|| -> std::result::Result<Result<()>, UsbError> {
            for _ in 0..16 {
                if reports.read(io.as_mut(), 10)?.is_none() {
                    break;
                }
            }
            for (name, body) in [
                ("POST conn 1", None),
                ("POST realtimeDisplay 1", Some(r#"{"enable":true}"#)),
            ] {
                match self.ask(io.as_mut(), name, body) {
                    Ok(Ok(true)) => {}
                    Ok(Ok(false)) => {
                        return Ok(Err(DeviceError(tf(
                            "the {panel} did not start: {error}",
                            &[
                                ("panel", &self.model.label()),
                                ("error", &format!("{name}: no 200")),
                            ],
                        ))))
                    }
                    Ok(Err(e)) => return Ok(Err(e)),
                    Err(e) => return Err(e),
                }
            }
            Ok(Ok(()))
        })();
        let usb = started.is_err();
        let failed = match started {
            Ok(Ok(())) => None,
            Ok(Err(e)) => Some(e),
            Err(e) => Some(DeviceError(tf(
                "the {panel} did not start: {error}",
                &[("panel", &self.model.label()), ("error", &e.to_string())],
            ))),
        };
        if let Some(e) = failed {
            self.give_back(io.as_mut());
            io.release();
            self.reports = None;
            if usb {
                self.keeper.with(|kept| kept.inner.counters.usb_errors += 1);
            }
            return Err(e);
        }
        self.keeper.with(|kept| {
            kept.inner.io = Some(io);
            kept.inner.target = Target::Hid(reports);
            kept.inner.last.clear();
        });
        self.keeper.start();
        Ok(())
    }

    /// _ask: whether the panel took the command (its answer says 200).
    fn ask(
        &mut self,
        io: &mut dyn UsbIo,
        name: &str,
        body: Option<&str>,
    ) -> std::result::Result<Result<bool>, UsbError> {
        let Some(reports) = self.reports.clone() else {
            return Ok(Ok(false));
        };
        let message = match command(name, self.number, body, (self.clock)()) {
            Ok(message) => message,
            Err(e) => return Ok(Err(e)),
        };
        reports.write(io, &message, WRITE_TIMEOUT_MS)?;
        self.number += 1;
        if self.pause_s > 0.0 {
            std::thread::sleep(Duration::from_secs_f64(self.pause_s));
        }
        let answer = reports.read(io, ANSWER_TIMEOUT_MS)?;
        Ok(Ok(answer.is_some_and(|a| a.windows(3).any(|w| w == b"200"))))
    }

    /// The screen back to the panel (closing).
    fn give_back(&mut self, io: &mut dyn UsbIo) {
        if self.reports.is_some() {
            let _ = self.ask(io, "POST realtimeDisplay 1", Some(r#"{"enable":false}"#));
        }
    }

    fn is_open(&self) -> bool {
        self.keeper.with(|kept| kept.inner.io.is_some())
    }
}

impl Driver for Thermaltake {
    fn model(&self) -> Option<&'static PanelModel> {
        Some(self.model)
    }

    fn describe(&self) -> String {
        self.model.label()
    }

    fn show(&mut self, frame: &Image, _region: Option<Region>) -> Result<()> {
        if !self.is_open() {
            return Err(DeviceError(tf(
                "the {panel} is not open",
                &[("panel", &self.describe())],
            )));
        }
        let native = to_native(frame, self.size())?;
        let reports = pieces(&jpeg(&dimmed(&native, self.brightness)?)?);
        let mut checked = false;
        let shown = self.keeper.with(|kept| {
            kept.check()?;
            checked = true;
            kept.inner.picture(reports)?;
            kept.sent();
            Ok(())
        });
        // (kept before it goes, as thermaltake.py keeps it under the keeper's lock)
        if checked {
            self.picture = Some(native);
        }
        shown
    }

    fn set_brightness(&mut self, percent: i64) -> Result<()> {
        self.brightness = percent.clamp(0, 100);
        if self.is_open() {
            if let Some(picture) = &self.picture {
                let reports = pieces(&jpeg(&dimmed(picture, self.brightness)?)?);
                self.keeper.with(|kept| {
                    kept.inner.picture(reports)?;
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
        let io = self.keeper.with(|kept| {
            kept.inner.last.clear();
            kept.inner.io.take()
        });
        if let Some(mut io) = io {
            self.give_back(io.as_mut());
            io.release();
        }
        self.reports = None;
        self.picture = None;
    }
}

impl Drop for Thermaltake {
    fn drop(&mut self) {
        self.close();
    }
}
