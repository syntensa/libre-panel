//! Panels behind a USB display chip, the MacroSilicon MS9132 (devices/ms9132.py; 345f:9132): the
//! JONSBO DS339 (376x960) and the VMAX 4.6" (320x960). Opened only where `device.model` names
//! such a panel, never by `auto` (USB-to-HDMI adapters have the chip too).
//!
//! - Controlled over its HID interface (0) with 8-byte feature reports, each written
//!   (SET_REPORT) and read back (GET_REPORT); the reply's bytes 3-7 are the data.
//! - Opening replays the vendor's start, reads the EDID 4 bytes at a time (its size picks the
//!   vendor's mode number) and sets the mode: RGB888 in, the mode out, frames taken.
//! - A frame goes to bulk endpoint 0x04 of interface 3 in pieces of 64 KB: a header, the pixels
//!   as BGR and a trailer; the panel's state is read after each. The output goes on once two
//!   frames are in. The brightness goes into the pixels; A6 07 00 turns the panel off.

use std::time::Duration;

use lp_pixels::{Filter, Image, Mode, Transpose};

use crate::base::{dimmed, tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::keep::Keeper;
use crate::models::{find_model, PanelModel};
use crate::usb_link::{self, UsbError, UsbIo, UsbResult};

pub const VENDOR_ID: u16 = 0x345F;
pub const PRODUCT_ID: u16 = 0x9132;
pub const MODEL_ID: &str = "jonsbo-ds339";
const CONTROL: u8 = 0;
const VIDEO: u8 = 3;
const ENDPOINT_OUT: u8 = 0x04;
const CHUNK: usize = 64 * 1024;
const WRITE_TIMEOUT_MS: u64 = 5000;
const REPORT_TIMEOUT_MS: u64 = 1000;
const FIRST_FRAME_S: f64 = 0.4;
const AGAIN_S: f64 = 1.0;
const TRAILER: [u8; 8] = [0xFF, 0xC0, 0, 0, 0, 0, 0, 0];

fn report(data: &[u8]) -> [u8; 8] {
    let mut out = [0u8; 8];
    out[..data.len()].copy_from_slice(data);
    out
}

fn status() -> [u8; 8] {
    report(&[0xB5, 0x00, 0x32])
}

/// power, registers and the EDID, as the vendor's application starts the chip
fn start() -> Vec<[u8; 8]> {
    let mut out = vec![
        report(&[0xB5, 0xFF, 0x00]),
        report(&[0xB5, 0xF0, 0x00]),
        report(&[0xB5, 0x00, 0x31]),
        report(&[0xB5, 0x00, 0x30]),
        report(&[0xB5, 0xF4, 0x39]),
        report(&[0xF5, 0x00, 0x1F, 0xE0]),
        report(&[0xF5, 0x00, 0x1F, 0xE8]),
        report(&[0xF5, 0x00, 0x1F, 0xF0]),
        report(&[0xF5, 0x00, 0x1F, 0xF8]),
        report(&[0xA6, 0x07, 0x01, 0x02]),
        report(&[0xB5, 0xC4, 0x54]),
        report(&[0xA6, 0x05, 0x00]),
        report(&[0xB5, 0xC5, 0x55]),
        report(&[0xB5, 0xF0, 0x05]),
        report(&[0xB6, 0xF0, 0x05, 0x20]),
        report(&[0xC5, 0xB0, 0x00]),
        report(&[0xC6, 0xB0, 0xDF]),
        report(&[0xC5, 0xA0, 0x00]),
        report(&[0xC6, 0xA0, 0xE4]),
        report(&[0xC5, 0xA0, 0x00]),
        report(&[0xC6, 0xA0, 0xF0]),
        report(&[0xB5, 0xF0, 0x16]),
        report(&[0xB6, 0xF0, 0x16, 0x00]),
        status(),
    ];
    out.extend(
        (0..128)
            .step_by(4)
            .map(|offset| report(&[0xB5, 0xC0, offset as u8])),
    );
    out
}

fn video_on() -> [[u8; 8]; 4] {
    [
        report(&[0xB5, 0xF0, 0x05]),
        report(&[0xB6, 0xF0, 0x05, 0x30]),
        report(&[0xA6, 0x05, 0x01]),
        report(&[0xB5, 0xC5, 0x55]),
    ]
}

/// The vendor's mode number for each size of panel (its framebuffer, standing on end).
pub fn vic(size: (i64, i64)) -> Option<u8> {
    Some(match size {
        (480, 480) => 143,
        (240, 320) => 147,
        (320, 240) => 148,
        (240, 240) => 156,
        (480, 272) => 159,
        (360, 960) => 160,
        (376, 960) => 171,
        (320, 960) => 178,
        (600, 1600) => 177,
        _ => return None,
    })
}

/// pause_after: the pause the vendor's application makes after a report, in seconds.
pub fn pause_after(report: &[u8; 8]) -> f64 {
    if report[0] == 0xA6 {
        return match report[1] {
            0x07 => 0.105,
            0x05 | 0x03 | 0x01 => 0.015,
            0x02 => 0.025,
            _ => 0.003,
        };
    }
    if report[..3] == status()[..3] {
        0.108
    } else {
        0.003
    }
}

/// edid_size: the size of the preferred timing of an EDID's block 0.
pub fn edid_size(edid: &[u8]) -> Option<(i64, i64)> {
    if edid.len() < 128 || edid[..8] != [0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00] {
        return None;
    }
    let width = (i64::from(edid[0x3A] & 0xF0) << 4) | i64::from(edid[0x38]);
    let height = (i64::from(edid[0x3D] & 0xF0) << 4) | i64::from(edid[0x3B]);
    (width != 0 && height != 0).then_some((width, height))
}

/// frame_packet: a frame as the chip takes it: its header, the pixels as BGR and the trailer.
pub fn frame_packet(native: &Image) -> Result<Vec<u8>> {
    let rgb;
    let native = if native.mode() == Mode::RGB {
        native
    } else {
        rgb = native.convert(Mode::RGB)?;
        &rgb
    };
    let (width, height) = native.size();
    let mut out = vec![0xFF, 0x00, 0x00, 0x00, 0x00, (width / 16) as u8];
    out.extend_from_slice(&((height as u16) | 0x8000).to_be_bytes());
    out.reserve(width * height * 3 + 8);
    for p in native.as_bytes().chunks_exact(3) {
        out.extend_from_slice(&[p[2], p[1], p[0]]);
    }
    out.extend_from_slice(&TRAILER);
    Ok(out)
}

/// to_native: a frame turned and sized into the framebuffer, which stands on end.
pub fn to_native(frame: &Image, size: (i64, i64)) -> Result<Image> {
    let mut picture = if frame.width() > frame.height() {
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

/// What the keeper guards: the chip, the frames in and the last frame's packet.
pub struct Link {
    io: Option<Box<dyn UsbIo>>,
    label: String,
    counters: Counters,
    frames: u64,
    packet: Vec<u8>,
    pauses: bool,
}

impl Link {
    /// report: one feature report written and read back; the reply's data.
    fn report(&mut self, report: &[u8; 8]) -> UsbResult<Vec<u8>> {
        let Some(io) = self.io.as_mut() else {
            return Err(UsbError::Other("the device is let go".into()));
        };
        io.control_out(
            0x21,
            0x09,
            0x0300,
            u16::from(CONTROL),
            report,
            REPORT_TIMEOUT_MS,
        )?;
        let reply = io.control_in(0xA1, 0x01, 0x0300, u16::from(CONTROL), 8, REPORT_TIMEOUT_MS)?;
        if self.pauses {
            std::thread::sleep(Duration::from_secs_f64(pause_after(report)));
        }
        Ok(reply.get(3..8.min(reply.len())).unwrap_or(&[]).to_vec())
    }

    fn send(&mut self, packet: &[u8]) -> Result<()> {
        let sent = (|| -> UsbResult<()> {
            let Some(io) = self.io.as_mut() else {
                return Err(UsbError::Other("the device is let go".into()));
            };
            for chunk in packet.chunks(CHUNK) {
                io.write(ENDPOINT_OUT, chunk, WRITE_TIMEOUT_MS)?;
                self.counters.bytes += chunk.len() as u64;
            }
            self.frames += 1;
            // (the state is only asked, as the vendor's application does)
            let _ = self.report(&status());
            if self.frames == 2 {
                for report in video_on() {
                    self.report(&report)?;
                }
            }
            Ok(())
        })();
        if let Err(e) = sent {
            self.counters.usb_errors += 1;
            return Err(DeviceError(tf(
                "the {panel} stopped answering: {error}",
                &[("panel", &self.label), ("error", &e.to_string())],
            )));
        }
        self.packet = packet.to_vec();
        Ok(())
    }

    fn again(link: &mut Link) -> Result<()> {
        if link.io.is_some() && !link.packet.is_empty() {
            let packet = link.packet.clone();
            return link.send(&packet);
        }
        Ok(())
    }
}

/// A panel behind the MS9132 (Ms9132Display).
pub struct Ms9132 {
    pub model: &'static PanelModel,
    size: (i64, i64),
    brightness: i64,
    picture: Option<Image>,
    keeper: Keeper<Link>,
}

impl Ms9132 {
    pub fn new(model: Option<&'static PanelModel>) -> Ms9132 {
        let model = model
            .or_else(|| find_model(MODEL_ID))
            .expect("the DS339 in the catalog");
        let link = Link {
            io: None,
            label: model.label(),
            counters: Counters::default(),
            frames: 0,
            packet: Vec::new(),
            pauses: true,
        };
        Ms9132 {
            model,
            size: (model.native_width, model.native_height),
            brightness: 100,
            picture: None,
            keeper: Keeper::new(AGAIN_S, link, Link::again, "ms9132-keep"),
        }
    }

    /// Without the chip's pauses (the tests').
    pub fn without_pauses(self) -> Ms9132 {
        self.keeper.with(|kept| kept.inner.pauses = false);
        self
    }

    pub fn open(&mut self) -> Result<()> {
        let (io, _) = usb_link::take(VENDOR_ID, &[PRODUCT_ID], &self.model.label())?;
        self.open_on(Box::new(io))
    }

    pub fn open_on(&mut self, io: Box<dyn UsbIo>) -> Result<()> {
        let label = self.model.label();
        self.keeper.with(|kept| {
            kept.inner.io = Some(io);
            kept.inner.frames = 0;
        });
        // the size its EDID says, where a mode is known for it
        let started = self.keeper.with(|kept| -> UsbResult<Option<(i64, i64)>> {
            let link = &mut kept.inner;
            let io = link.io.as_mut().expect("just put there");
            io.claim(&[CONTROL, VIDEO])?;
            let mut edid = [0u8; 128];
            for report in start() {
                let reply = link.report(&report)?;
                if report[..2] == [0xB5, 0xC0] {
                    let at = usize::from(report[2]);
                    let part = &reply[..reply.len().min(4)];
                    edid[at..at + part.len()].copy_from_slice(part);
                }
            }
            Ok(edid_size(&edid).filter(|size| vic(*size).is_some()))
        });
        let failed = match started {
            Ok(size) => {
                if let Some(size) = size {
                    self.size = size;
                }
                let (w, h) = self.size;
                let mode = vic(self.size).unwrap_or(171);
                self.keeper
                    .with(|kept| set_mode(&mut kept.inner, w, h, mode))
                    .err()
            }
            Err(e) => Some(e),
        };
        if let Some(e) = failed {
            self.close();
            self.keeper.with(|kept| kept.inner.counters.usb_errors += 1);
            return Err(DeviceError(tf(
                "the {panel} did not start: {error}",
                &[("panel", &label), ("error", &e.to_string())],
            )));
        }
        self.keeper.start();
        Ok(())
    }

    fn is_open(&self) -> bool {
        self.keeper.with(|kept| kept.inner.io.is_some())
    }
}

fn set_mode(link: &mut Link, width: i64, height: i64, vic: u8) -> UsbResult<()> {
    let w = (width as u16).to_be_bytes();
    let h = (height as u16).to_be_bytes();
    for report in [
        status(),
        report(&[0xB5, 0xF4, 0x39]),
        report(&[0xA6, 0x03, 0x03]),
        report(&[0xB5, 0xC5, 0x58]),
        report(&[0xA6, 0x01, w[0], w[1], h[0], h[1], 0x11, 0x00]),
        report(&[0xB5, 0xC5, 0x56]),
        report(&[0xA6, 0x02, vic, 0x00, w[0], w[1], h[0], h[1]]),
        report(&[0xB5, 0xC5, 0x57]),
        report(&[0xA6, 0x04, 0x01]),
    ] {
        link.report(&report)?;
    }
    if link.pauses {
        std::thread::sleep(Duration::from_secs_f64(FIRST_FRAME_S));
    }
    Ok(())
}

impl Driver for Ms9132 {
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
        let native = to_native(frame, self.size)?;
        let packet = frame_packet(&dimmed(&native, self.brightness)?)?;
        let mut checked = false;
        let shown = self.keeper.with(|kept| {
            kept.check()?;
            checked = true;
            kept.inner.send(&packet)?;
            if kept.inner.frames == 1 {
                kept.inner.send(&packet)?;
            }
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
                let packet = frame_packet(&dimmed(picture, self.brightness)?)?;
                self.keeper.with(|kept| {
                    kept.inner.send(&packet)?;
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

    fn screen_off(&mut self) -> bool {
        if !self.is_open() {
            return false;
        }
        self.keeper.stop();
        self.keeper
            .with(|kept| kept.inner.report(&report(&[0xA6, 0x07])).is_ok())
    }

    fn close(&mut self) {
        self.keeper.stop();
        self.keeper.with(|kept| {
            if let Some(mut io) = kept.inner.io.take() {
                io.release();
            }
            kept.inner.packet.clear();
        });
        self.picture = None;
    }
}

impl Drop for Ms9132 {
    fn drop(&mut self) {
        self.close();
    }
}
