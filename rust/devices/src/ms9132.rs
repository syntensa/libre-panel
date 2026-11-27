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
//!
//! The JONSBO DS339 flickers in a washed-out purple with that start (issue #46). What its owner
//! found to end it on the panel goes to the DS339 alone (the VMAX 4.6" keeps the vendor's):
//! display control B6 F0 05 40 at the start and 50 to turn the output on (not 20 and 30),
//! B6 F0 16 08 (not 00); the reports that only write (A6, B6, C6) are not read back; a packet
//! of no bytes after each frame; the state asked about every 0.5 s, not after each frame; and
//! no pixel byte FF (FE in its place).
//!
//! The Valkyrie VK03 6.2" (360x960, issue #75) is driven as its owner found it on the panel,
//! from a cold start (Myth.Cool's MSDisplay): power and source, the flash's serial number
//! read (its "0360*0960" says the size), the chip's registers read and its EDID four bytes at a
//! time, the serial number again; then its size and timing (mode 160) with pixels as UYVY
//! (4:2:2, BT.601's studio range) in place of RGB888. A frame is its header (the width and
//! height in 12 bits each), the pixels and the trailer, in pieces of 64 KB and a packet of no
//! bytes; after the first the output goes on, and its state is asked at least every 250 ms
//! while it runs (B5 00 32), a frame or not. The reports that only write (A6, B6) are not read
//! back, as the DS339's; the vendor's pauses are made while it starts, not while it runs.

use std::time::{Duration, Instant};

use lp_pixels::{Filter, Image, Mode, Transpose};

use crate::base::{dimmed, tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::keep::Keeper;
use crate::models::{find_model, PanelModel};
use crate::usb_link::{self, UsbError, UsbIo, UsbResult};

pub const VENDOR_ID: u16 = 0x345F;
pub const PRODUCT_ID: u16 = 0x9132;
pub const MODEL_ID: &str = "jonsbo-ds339";
/// The Valkyrie VK03 6.2" (issue #75).
pub const VK03_ID: &str = "valkyrie-vk03-6.2";
/// its pixels: UYVY (RGB888 is 0x11)
const UYVY: u8 = 0x22;
/// how long the VK03's keeper waits before it asks its state (it wants it within 250 ms)
const VK03_KEEP_S: f64 = 0.15;
/// how often a frame of the VK03's is followed by its state asked, at most
const VK03_ALIVE_S: f64 = 0.2;
const CONTROL: u8 = 0;
const VIDEO: u8 = 3;
const ENDPOINT_OUT: u8 = 0x04;
const CHUNK: usize = 64 * 1024;
const WRITE_TIMEOUT_MS: u64 = 5000;
const REPORT_TIMEOUT_MS: u64 = 1000;
const FIRST_FRAME_S: f64 = 0.4;
const AGAIN_S: f64 = 1.0;
const TRAILER: [u8; 8] = [0xFF, 0xC0, 0, 0, 0, 0, 0, 0];
/// the reports the DS339's start does not read back
const WRITE_ONLY: [u8; 3] = [0xA6, 0xB6, 0xC6];
/// how often the DS339's state is asked while frames go out
const STATE_EVERY_S: f64 = 0.5;

/// The DS339's own report where it has one (issue #46).
fn ds339(report: [u8; 8]) -> [u8; 8] {
    match report[..4] {
        [0xB6, 0xF0, 0x05, 0x20] => self::report(&[0xB6, 0xF0, 0x05, 0x40]),
        [0xB6, 0xF0, 0x16, 0x00] => self::report(&[0xB6, 0xF0, 0x16, 0x08]),
        [0xB6, 0xF0, 0x05, 0x30] => self::report(&[0xB6, 0xF0, 0x05, 0x50]),
        _ => report,
    }
}

/// driver_hint: what to do where Windows has another driver than WinUSB on the chip's
/// interfaces (the USB layer says the driver is incompatible, or the operation not
/// supported); None else. An owner's DS339 had libusb0 on its video interface (3) and
/// Windows' HID driver on the other (0), and ran once both had WinUSB.
pub fn driver_hint(error: &str, windows: bool) -> Option<String> {
    let said = error.to_lowercase();
    let wrong = [
        "incompatible driver",
        "deviceinterfaceguid",
        "could not determine driver",
        "not supported",
    ];
    (windows && wrong.iter().any(|word| said.contains(word))).then(|| {
        lp_i18n::t(
            "On Windows its two USB interfaces (0 and 3) need the WinUSB driver: set it once \
             with Zadig; the hardware guide has the steps.",
        )
    })
}

/// An error's text with the driver's hint after it, where there is one.
fn hinted(error: String, windows: bool) -> String {
    match driver_hint(&error, windows) {
        Some(hint) => format!("{error}. {hint}"),
        None => error,
    }
}

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

/// The flash's serial number, 8 bytes at each of four places.
fn serial_reads() -> [[u8; 8]; 4] {
    [0xE0, 0xE8, 0xF0, 0xF8].map(|at| report(&[0xF5, 0x00, 0x1F, at]))
}

/// The VK03's power and source, as its owner's start has them.
fn vk03_power() -> Vec<[u8; 8]> {
    vec![
        report(&[0xA6, 0x03, 0x03]),
        report(&[0xB5, 0xC5, 0x58]),
        report(&[0xB6, 0xDE, 0xEE, 0x01]),
        report(&[0xA6, 0x07, 0x01, 0x02]),
        report(&[0xB5, 0xC4, 0x54]),
        report(&[0xA6, 0x05, 0x00]),
        report(&[0xB5, 0xC5, 0x55]),
        report(&[0xB5, 0xF0, 0x04]),
        report(&[0xB6, 0xF0, 0x04, 0x6D]),
    ]
}

/// The VK03's registers read, then its EDID four bytes at a time (without them it stays dark
/// from a cold start).
fn vk03_reads() -> Vec<[u8; 8]> {
    let mut out: Vec<[u8; 8]> = [
        [0xC4, 0x24],
        [0xC6, 0x50],
        [0xC3, 0x0F],
        [0xFF, 0x00],
        [0xF0, 0x00],
        [0x00, 0x31],
        [0x00, 0x30],
        [0x00, 0x32],
        [0x00, 0x32],
    ]
    .iter()
    .map(|[a, b]| report(&[0xB5, *a, *b]))
    .collect();
    out.extend(
        (0..128)
            .step_by(4)
            .map(|offset| report(&[0xB5, 0xC0, offset as u8])),
    );
    out
}

/// The VK03's size and timing (`vic`), its pixels UYVY, and its output made ready.
fn vk03_mode(width: i64, height: i64, vic: u8) -> Vec<[u8; 8]> {
    let w = (width as u16).to_be_bytes();
    let h = (height as u16).to_be_bytes();
    vec![
        report(&[0xA6, 0x05, 0x00]),
        report(&[0xB5, 0xC5, 0x55]),
        report(&[0xB5, 0xF5, 0x07]),
        report(&[0xB6, 0xF5, 0x07, 0x02]),
        report(&[0xA6, 0x04, 0x00]),
        report(&[0xB5, 0xC5, 0x55]),
        status(),
        report(&[0xA6, 0x03, 0x03]),
        report(&[0xB5, 0xC5, 0x58]),
        report(&[0xA6, 0x01, w[0], w[1], h[0], h[1], UYVY, 0x00]),
        report(&[0xB5, 0xC5, 0x55]),
        report(&[0xA6, 0x02, vic, 0x00, w[0], w[1], h[0], h[1]]),
        report(&[0xB5, 0xC5, 0x57]),
        report(&[0xA6, 0x04, 0x01]),
        report(&[0xB5, 0xC5, 0x55]),
        report(&[0xB6, 0xF2, 0x4E, 0x00]),
        status(),
        report(&[0xB5, 0xF2, 0x42]),
    ]
}

/// The VK03's output on, after its first frame.
fn vk03_on() -> [[u8; 8]; 4] {
    [
        report(&[0xB5, 0xF5, 0x07]),
        report(&[0xB6, 0xF5, 0x07, 0x00]),
        report(&[0xA6, 0x05, 0x01]),
        report(&[0xB5, 0xC5, 0x55]),
    ]
}

/// serial_of: the flash's serial number from its four blocks as read (8 bytes each): the
/// first after its header 5A A5 1A, the others whole, up to a byte 00 or FF.
pub fn serial_of(blocks: &[Vec<u8>]) -> String {
    let mut bytes = Vec::new();
    for (n, block) in blocks.iter().enumerate() {
        let part = if n == 0 {
            match block.strip_prefix(&[0x5A, 0xA5, 0x1A]) {
                Some(rest) => rest,
                None => return String::new(),
            }
        } else {
            &block[..]
        };
        match part.iter().position(|&b| b == 0x00 || b == 0xFF) {
            Some(end) => {
                bytes.extend_from_slice(&part[..end]);
                break;
            }
            None => bytes.extend_from_slice(part),
        }
    }
    bytes
        .iter()
        .filter(|b| b.is_ascii_graphic())
        .map(|&b| char::from(b))
        .collect()
}

/// size_of_serial: the size a serial number names ("MCVKC030360*0960S..." is 360x960), where
/// the chip has a mode for it.
pub fn size_of_serial(serial: &str) -> Option<(i64, i64)> {
    let bytes = serial.as_bytes();
    (0..bytes.len().saturating_sub(8)).find_map(|at| {
        let part = &bytes[at..at + 9];
        let digits = |range: std::ops::Range<usize>| {
            part[range.clone()]
                .iter()
                .all(u8::is_ascii_digit)
                .then(|| serial[at + range.start..at + range.end].parse::<i64>().ok())
                .flatten()
        };
        let size = (part[4] == b'*')
            .then(|| digits(0..4).zip(digits(5..9)))
            .flatten()?;
        vic(size).map(|_| size)
    })
}

/// uyvy_packet: a frame as the VK03 takes it: its header (the width and height in 12 bits
/// each), the pixels as UYVY (two pixels in U, Y0, V, Y1; BT.601's studio range, worked out in
/// whole numbers) and the trailer. An odd width's last pixel is left out.
pub fn uyvy_packet(native: &Image) -> Result<Vec<u8>> {
    let rgb;
    let native = if native.mode() == Mode::RGB {
        native
    } else {
        rgb = native.convert(Mode::RGB)?;
        &rgb
    };
    let (width, height) = native.size();
    let (w, h) = (width as u32, height as u32);
    let mut out = vec![
        0xFF,
        0x00,
        0x00,
        0x00,
        0x00,
        (w >> 4) as u8,
        (((w & 0xF) << 4) | ((h >> 8) & 0xF)) as u8,
        (h & 0xFF) as u8,
    ];
    out.reserve((width & !1) * height * 2 + TRAILER.len());
    let luma = |r: i32, g: i32, b: i32| (((66 * r + 129 * g + 25 * b + 128) >> 8) + 16) as u8;
    for row in native.as_bytes().chunks_exact(width * 3) {
        for pair in row.chunks_exact(6) {
            let [r0, g0, b0, r1, g1, b1] = [0, 1, 2, 3, 4, 5].map(|n| i32::from(pair[n]));
            let (r, g, b) = (r0 + r1, g0 + g1, b0 + b1);
            let u = (((-38 * r - 74 * g + 112 * b + 256) >> 9) + 128) as u8;
            let v = (((112 * r - 94 * g - 18 * b + 256) >> 9) + 128) as u8;
            out.extend_from_slice(&[u, luma(r0, g0, b0), v, luma(r1, g1, b1)]);
        }
    }
    out.extend_from_slice(&TRAILER);
    Ok(out)
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

/// frame_packet: a frame as the chip takes it: its header, the pixels as BGR and the trailer;
/// with `no_ff` (the DS339) no pixel byte FF.
pub fn frame_packet(native: &Image, no_ff: bool) -> Result<Vec<u8>> {
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
    if no_ff {
        for byte in &mut out[8..] {
            if *byte == 0xFF {
                *byte = 0xFE;
            }
        }
    }
    out.extend_from_slice(&TRAILER);
    Ok(out)
}

/// to_native: a frame turned and sized into the framebuffer (which stands on end, but a VK03's
/// family's of 320x240): turned where it lies the other way.
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

/// What the keeper guards: the chip, the frames in and the last frame's packet.
pub struct Link {
    io: Option<Box<dyn UsbIo>>,
    label: String,
    counters: Counters,
    frames: u64,
    packet: Vec<u8>,
    pauses: bool,
    /// the DS339's start and frames (issue #46)
    ds339: bool,
    /// when the DS339's state was asked last
    state_at: Option<Instant>,
    /// the VK03's start and frames (issue #75)
    vk03: bool,
    /// it runs: no pauses after the reports (the VK03's)
    running: bool,
    /// when the last frame went out
    sent_at: Option<Instant>,
}

impl Link {
    /// report: one feature report written and read back; the reply's data.
    fn report(&mut self, report: &[u8; 8]) -> UsbResult<Vec<u8>> {
        let reply = self.ask(report)?;
        Ok(reply.get(3..8.min(reply.len())).unwrap_or(&[]).to_vec())
    }

    /// ask: one feature report written and read back; the whole reply (eight bytes of none
    /// where it only writes: the DS339's and the VK03's A6, B6 and C6).
    fn ask(&mut self, report: &[u8; 8]) -> UsbResult<Vec<u8>> {
        let pause = self.pauses && !self.running;
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
        if (self.ds339 || self.vk03) && WRITE_ONLY.contains(&report[0]) {
            if pause {
                std::thread::sleep(Duration::from_secs_f64(pause_after(report)));
            }
            return Ok(vec![0; 8]);
        }
        let reply = io.control_in(0xA1, 0x01, 0x0300, u16::from(CONTROL), 8, REPORT_TIMEOUT_MS)?;
        if pause {
            std::thread::sleep(Duration::from_secs_f64(pause_after(report)));
        }
        Ok(reply)
    }

    /// the VK03's state asked (it wants that within 250 ms while it runs)
    fn alive(&mut self) -> UsbResult<()> {
        self.state_at = Some(Instant::now());
        self.report(&status()).map(|_| ())
    }

    /// fault: a transfer that went wrong, said as the panel's.
    fn fault(&mut self, error: &UsbError) -> DeviceError {
        self.counters.usb_errors += 1;
        DeviceError(tf(
            "the {panel} stopped answering: {error}",
            &[("panel", &self.label), ("error", &error.to_string())],
        ))
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
            if self.ds339 || self.vk03 {
                // (the transfer ended by a packet of no bytes)
                self.io_mut()?.write(ENDPOINT_OUT, &[], WRITE_TIMEOUT_MS)?;
            }
            self.frames += 1;
            let now = Instant::now();
            self.sent_at = Some(now);
            if self.vk03 {
                // the output on after the first frame; its state asked every 200 ms at most
                if self.frames == 1 {
                    for report in vk03_on() {
                        self.report(&report)?;
                    }
                    self.state_at = Some(now);
                } else if self.state_at.is_none_or(|at| {
                    now.duration_since(at) >= Duration::from_secs_f64(VK03_ALIVE_S)
                }) {
                    self.alive()?;
                }
                return Ok(());
            }
            let due = self
                .state_at
                .is_none_or(|at| now.duration_since(at) >= Duration::from_secs_f64(STATE_EVERY_S));
            if !self.ds339 || due {
                self.state_at = Some(now);
                // (the state is only asked, as the vendor's application does)
                let _ = self.report(&status());
            }
            if self.frames == 2 {
                for report in video_on() {
                    let report = if self.ds339 { ds339(report) } else { report };
                    self.report(&report)?;
                }
            }
            Ok(())
        })();
        if let Err(e) = sent {
            return Err(self.fault(&e));
        }
        if self.packet.as_slice() != packet {
            self.packet = packet.to_vec();
        }
        Ok(())
    }

    fn io_mut(&mut self) -> UsbResult<&mut Box<dyn UsbIo>> {
        self.io
            .as_mut()
            .ok_or_else(|| UsbError::Other("the device is let go".into()))
    }

    /// again: the last frame again, where nothing went out for a while; the VK03's state alone
    /// where its last frame went out within the second.
    fn again(link: &mut Link) -> Result<()> {
        let recent = link
            .sent_at
            .is_some_and(|at| at.elapsed() < Duration::from_secs_f64(AGAIN_S));
        if link.vk03 && link.io.is_some() && recent {
            return link.alive().map_err(|e| link.fault(&e));
        }
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
            ds339: model.id == MODEL_ID,
            state_at: None,
            vk03: model.id == VK03_ID,
            running: false,
            sent_at: None,
        };
        let every = if model.id == VK03_ID {
            VK03_KEEP_S
        } else {
            AGAIN_S
        };
        Ms9132 {
            model,
            size: (model.native_width, model.native_height),
            brightness: 100,
            picture: None,
            keeper: Keeper::new(every, link, Link::again, "ms9132-keep"),
        }
    }

    /// Without the chip's pauses (the tests').
    pub fn without_pauses(self) -> Ms9132 {
        self.keeper.with(|kept| kept.inner.pauses = false);
        self
    }

    pub fn open(&mut self) -> Result<()> {
        let (io, _) = usb_link::take(VENDOR_ID, &[PRODUCT_ID], &self.model.label())
            .map_err(|e| DeviceError(hinted(e.0, cfg!(windows))))?;
        self.open_on(Box::new(io))
    }

    pub fn open_on(&mut self, io: Box<dyn UsbIo>) -> Result<()> {
        let label = self.model.label();
        self.keeper.with(|kept| {
            kept.inner.io = Some(io);
            kept.inner.frames = 0;
            kept.inner.state_at = None;
            kept.inner.running = false;
            kept.inner.sent_at = None;
        });
        // the size its EDID says, where a mode is known for it
        let started = self.keeper.with(|kept| -> UsbResult<Option<(i64, i64)>> {
            let link = &mut kept.inner;
            let io = link.io.as_mut().expect("just put there");
            io.claim(&[CONTROL, VIDEO])?;
            if link.vk03 {
                return vk03_start(link);
            }
            let mut edid = [0u8; 128];
            for report in start() {
                let report = if link.ds339 { ds339(report) } else { report };
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
                    .with(|kept| {
                        if kept.inner.vk03 {
                            vk03_set_mode(&mut kept.inner, w, h, mode)
                        } else {
                            set_mode(&mut kept.inner, w, h, mode)
                        }
                    })
                    .err()
            }
            Err(e) => Some(e),
        };
        if let Some(e) = failed {
            self.close();
            self.keeper.with(|kept| kept.inner.counters.usb_errors += 1);
            return Err(DeviceError(tf(
                "the {panel} did not start: {error}",
                &[
                    ("panel", &label),
                    ("error", &hinted(e.to_string(), cfg!(windows))),
                ],
            )));
        }
        self.keeper.start();
        Ok(())
    }

    fn is_open(&self) -> bool {
        self.keeper.with(|kept| kept.inner.io.is_some())
    }

    /// The frame as the panel takes it, at the brightness set: the VK03's as UYVY, the others'
    /// as BGR (the DS339's without a byte FF).
    fn packet_of(&self, native: &Image) -> Result<Vec<u8>> {
        let picture = dimmed(native, self.brightness)?;
        match self.model.id {
            VK03_ID => uyvy_packet(&picture),
            id => frame_packet(&picture, id == MODEL_ID),
        }
    }
}

/// vk03_start: the VK03 started as its owner found it; the size its flash's serial number
/// names, where it names one the chip has a mode for (read twice, as the start reads it: the
/// second time's where the first said none).
fn vk03_start(link: &mut Link) -> UsbResult<Option<(i64, i64)>> {
    let read_serial = |link: &mut Link| -> UsbResult<String> {
        let mut blocks = Vec::new();
        for report in serial_reads() {
            blocks.push(link.ask(&report)?);
        }
        Ok(serial_of(&blocks))
    };
    for report in vk03_power() {
        link.report(&report)?;
    }
    let first = read_serial(link)?;
    for report in vk03_reads() {
        link.report(&report)?;
    }
    let second = read_serial(link)?;
    link.report(&status())?;
    Ok(size_of_serial(&first).or_else(|| size_of_serial(&second)))
}

/// vk03_set_mode: the VK03's size and timing; from here it runs (no pauses after its reports).
fn vk03_set_mode(link: &mut Link, width: i64, height: i64, vic: u8) -> UsbResult<()> {
    for report in vk03_mode(width, height, vic) {
        link.report(&report)?;
    }
    link.running = true;
    Ok(())
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
        let packet = self.packet_of(&native)?;
        let mut checked = false;
        let shown = self.keeper.with(|kept| {
            kept.check()?;
            checked = true;
            kept.inner.send(&packet)?;
            if kept.inner.frames == 1 && !kept.inner.vk03 {
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
                let packet = self.packet_of(picture)?;
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::usb_link::InterfaceInfo;
    use std::sync::{Arc, Mutex};

    /// What the made-up chip saw: a report written, a report read back, a bulk transfer.
    #[derive(Clone, Debug, PartialEq)]
    enum Seen {
        Report([u8; 8]),
        Read,
        Bulk(usize),
    }

    /// A VK03 the tests make up: what it saw, and the 32 bytes of its flash at 1FE0.
    #[derive(Clone)]
    struct Chip {
        seen: Arc<Mutex<Vec<Seen>>>,
        last: Arc<Mutex<[u8; 8]>>,
        flash: Vec<u8>,
    }

    impl Chip {
        fn with_serial(serial: &[u8]) -> Chip {
            let mut flash = vec![0x5A, 0xA5, 0x1A];
            flash.extend_from_slice(serial);
            flash.resize(32, 0x00);
            Chip {
                seen: Arc::default(),
                last: Arc::default(),
                flash,
            }
        }

        fn seen(&self) -> Vec<Seen> {
            std::mem::take(&mut self.seen.lock().unwrap())
        }
    }

    impl UsbIo for Chip {
        fn product(&mut self) -> String {
            "MS9132".into()
        }
        fn interfaces(&self) -> Vec<InterfaceInfo> {
            Vec::new()
        }
        fn claim(&mut self, interfaces: &[u8]) -> UsbResult<()> {
            assert_eq!(interfaces, [CONTROL, VIDEO]);
            Ok(())
        }
        fn write(&mut self, endpoint: u8, data: &[u8], _: u64) -> UsbResult<()> {
            assert_eq!(endpoint, ENDPOINT_OUT);
            self.seen.lock().unwrap().push(Seen::Bulk(data.len()));
            Ok(())
        }
        fn read(&mut self, _: u8, _: usize, _: u64) -> UsbResult<Vec<u8>> {
            Ok(Vec::new())
        }
        fn control_out(
            &mut self,
            kind: u8,
            ask: u8,
            value: u16,
            index: u16,
            data: &[u8],
            _: u64,
        ) -> UsbResult<()> {
            assert_eq!((kind, ask, value, index), (0x21, 0x09, 0x0300, 0));
            let report: [u8; 8] = data.try_into().expect("8 bytes");
            *self.last.lock().unwrap() = report;
            self.seen.lock().unwrap().push(Seen::Report(report));
            Ok(())
        }
        fn control_in(
            &mut self,
            kind: u8,
            ask: u8,
            value: u16,
            index: u16,
            length: u16,
            _: u64,
        ) -> UsbResult<Vec<u8>> {
            assert_eq!(
                (kind, ask, value, index, length),
                (0xA1, 0x01, 0x0300, 0, 8)
            );
            self.seen.lock().unwrap().push(Seen::Read);
            let last = *self.last.lock().unwrap();
            Ok(match last {
                [0xF5, 0x00, 0x1F, at, ..] if at >= 0xE0 => {
                    let at = usize::from(at - 0xE0);
                    self.flash[at..at + 8].to_vec()
                }
                _ => vec![0; 8],
            })
        }
        fn release(&mut self) {}
    }

    /// The reports among what the chip saw, each with whether it was read back.
    fn reports(seen: &[Seen]) -> Vec<([u8; 8], bool)> {
        let mut out = Vec::new();
        for (n, event) in seen.iter().enumerate() {
            if let Seen::Report(report) = event {
                out.push((*report, seen.get(n + 1) == Some(&Seen::Read)));
            }
        }
        out
    }

    fn vk03(chip: &Chip) -> Ms9132 {
        let mut driver = Ms9132::new(find_model(VK03_ID)).without_pauses();
        driver.open_on(Box::new(chip.clone())).expect("it starts");
        driver
    }

    /// A frame's transfers: 64 KB pieces, the rest, a packet of no bytes.
    fn frame_of(size: (usize, usize)) -> Vec<Seen> {
        let total = 8 + size.0 * size.1 * 2 + 8;
        let mut out: Vec<Seen> = (0..total / CHUNK).map(|_| Seen::Bulk(CHUNK)).collect();
        out.push(Seen::Bulk(total % CHUNK));
        out.push(Seen::Bulk(0));
        out
    }

    #[test]
    fn the_vk03_starts_as_its_owner_found_and_takes_uyvy_frames() {
        let chip = Chip::with_serial(b"MCVKC030360*0960S251901783");
        let mut driver = vk03(&chip);
        let started = reports(&chip.seen());
        // its power and source first, as its owner's start has them, A6 and B6 not read back
        assert_eq!(
            started[..9].iter().map(|(r, _)| *r).collect::<Vec<_>>(),
            vk03_power()
        );
        assert!(started
            .iter()
            .all(|(r, read)| *read != WRITE_ONLY.contains(&r[0])));
        // the serial number read twice, the EDID four bytes at a time; then size and timing
        let serials = started.iter().filter(|(r, _)| r[0] == 0xF5).count();
        let edid = started
            .iter()
            .filter(|(r, _)| r[..2] == [0xB5, 0xC0])
            .count();
        assert_eq!((serials, edid), (8, 32));
        let mode: Vec<[u8; 8]> = started
            .iter()
            .map(|(r, _)| *r)
            .rev()
            .take(18)
            .rev()
            .collect();
        assert_eq!(mode, vk03_mode(360, 960, 0xA0));
        assert!(mode.contains(&[0xA6, 0x01, 0x01, 0x68, 0x03, 0xC0, 0x22, 0x00]));
        assert!(mode.contains(&[0xA6, 0x02, 0xA0, 0x00, 0x01, 0x68, 0x03, 0xC0]));
        // a frame, the output on after it
        let frame = Image::new(Mode::RGB, 960, 360, &[10, 200, 30]);
        driver.show(&frame, None).unwrap();
        let seen = chip.seen();
        let frame_seen = frame_of((360, 960));
        assert_eq!(seen[..frame_seen.len()], frame_seen[..]);
        let on: Vec<[u8; 8]> = reports(&seen[frame_seen.len()..])
            .iter()
            .map(|(r, _)| *r)
            .collect();
        assert_eq!(on, vk03_on());
        // the next at once: the frame alone; a while later, its state asked after it
        driver.show(&frame, None).unwrap();
        assert_eq!(chip.seen(), frame_seen);
        std::thread::sleep(Duration::from_millis(60));
        driver.show(&frame, None).unwrap();
        std::thread::sleep(Duration::from_millis(250));
        driver.show(&frame, None).unwrap();
        let seen = chip.seen();
        let asked = reports(&seen);
        assert!(!asked.is_empty() && asked.iter().all(|(r, read)| *r == status() && *read));
        // with no frame, the keeper asks its state within 250 ms, the frame again after a second
        chip.seen();
        std::thread::sleep(Duration::from_millis(600));
        let idle = chip.seen();
        assert!(idle.iter().all(|s| !matches!(s, Seen::Bulk(_))), "{idle:?}");
        assert!(reports(&idle).len() >= 2, "{idle:?}");
        std::thread::sleep(Duration::from_millis(900));
        let later = chip.seen();
        assert!(later.iter().any(|s| matches!(s, Seen::Bulk(CHUNK))));
        driver.close();
    }

    #[test]
    fn the_size_its_serial_number_names_is_taken() {
        let chip = Chip::with_serial(b"MCVKC030320*0240S2519");
        let mut driver = vk03(&chip);
        let started = reports(&chip.seen());
        assert!(started
            .iter()
            .any(|(r, _)| *r == [0xA6, 0x01, 0x01, 0x40, 0x00, 0xF0, 0x22, 0x00]));
        assert!(started
            .iter()
            .any(|(r, _)| *r == [0xA6, 0x02, 0x94, 0x00, 0x01, 0x40, 0x00, 0xF0]));
        driver
            .show(&Image::new(Mode::RGB, 320, 240, &[0, 0, 0]), None)
            .unwrap();
        assert_eq!(chip.seen()[..4], frame_of((320, 240))[..]);
        driver.close();
        // none: the model's
        let chip = Chip::with_serial(&[0xFF; 29]);
        let mut driver = vk03(&chip);
        assert!(reports(&chip.seen())
            .iter()
            .any(|(r, _)| *r == [0xA6, 0x02, 0xA0, 0x00, 0x01, 0x68, 0x03, 0xC0]));
        driver.close();
    }

    #[test]
    fn a_serial_number_is_read_and_its_size_found() {
        let blocks = |bytes: &[u8]| -> Vec<Vec<u8>> {
            let mut flash = vec![0x5A, 0xA5, 0x1A];
            flash.extend_from_slice(bytes);
            flash.resize(32, 0xFF);
            flash.chunks(8).map(<[u8]>::to_vec).collect()
        };
        let serial = serial_of(&blocks(b"MCVKC030360*0960S251901783"));
        assert_eq!(serial, "MCVKC030360*0960S251901783");
        assert_eq!(size_of_serial(&serial), Some((360, 960)));
        assert_eq!(size_of_serial("XX0320*0240"), Some((320, 240)));
        assert_eq!(size_of_serial("XX0123*0456"), None); // (no mode for it)
        assert_eq!(size_of_serial("0360x0960"), None);
        assert_eq!(size_of_serial("*"), None);
        assert_eq!(serial_of(&[vec![0; 8]]), ""); // (no header)
        assert_eq!(serial_of(&blocks(b"AB\x00CD")), "AB");
    }

    #[test]
    fn uyvy_is_bt601s_studio_range_two_pixels_at_a_time() {
        let white = uyvy_packet(&Image::new(Mode::RGB, 360, 960, &[255, 255, 255])).unwrap();
        assert_eq!(white.len(), 691_216);
        assert_eq!(white[..8], [0xFF, 0, 0, 0, 0, 0x16, 0x83, 0xC0]);
        assert_eq!(white[8..12], [128, 235, 128, 235]);
        assert_eq!(white[white.len() - 8..], TRAILER);
        let black = uyvy_packet(&Image::new(Mode::RGB, 2, 1, &[0, 0, 0])).unwrap();
        assert_eq!(black[8..12], [128, 16, 128, 16]);
        let red = uyvy_packet(&Image::new(Mode::RGB, 2, 1, &[255, 0, 0])).unwrap();
        assert_eq!(red[8..12], [90, 82, 240, 82]);
        let header = uyvy_packet(&Image::new(Mode::RGB, 320, 240, &[0, 0, 0])).unwrap();
        assert_eq!(header[5..8], [0x14, 0x00, 0xF0]);
    }

    /// The hint for a wrong driver comes on Windows alone, for what the USB layer says of one.
    #[test]
    fn the_driver_hint_comes_where_windows_has_the_wrong_driver() {
        let wrong = "incompatible driver is installed for this interface";
        assert!(driver_hint(wrong, true).is_some_and(|hint| hint.contains("WinUSB")));
        assert!(driver_hint(wrong, false).is_none());
        let unset = "Could not find DeviceInterfaceGUIDs in registry. WinUSB driver may not be \
                     correctly installed for this interface.";
        assert!(driver_hint(unset, true).is_some());
        assert!(driver_hint("[Errno 110] Operation timed out", true).is_none());
        assert_eq!(hinted("no answer".to_string(), true), "no answer");
        assert!(hinted(wrong.to_string(), true).starts_with(&format!("{wrong}. ")));
    }
}
