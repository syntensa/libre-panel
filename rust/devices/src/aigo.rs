//! AIGO's case screens: the Xingcanchen's of 1920x462 (USB 1d6b:0103, "Aigo USB Device" of
//! "Aigo Inc."), a HID device that takes JPEG pictures in reports of 1024 bytes, as
//! Thermaltake's LCD does. Written from what its owner captured of AIGO's program and from
//! LanScreen's notes (MIT, by Savathun), which drives the Xingcanlan of the same family:
//!
//! - A command is text like an HTTP request ("POST conn 1", then "SeqNumber=<n>",
//!   "Date=<ms>", for one with a body "ContentType=json" and "ContentLength=<bytes>", an empty
//!   line and the body; lines end with CR LF). Framed: the length (the text's + 5, two bytes
//!   big-endian), the text and a checksum (the sum of those bytes), each 5A in them written
//!   5B 01 and each 5B 5B 02, between two 5A. The screen answers "1 200" where it did it, with
//!   AckNumber the command's SeqNumber + 1.
//! - Opening: "POST power 1" {"event":"resume"} (a screen asleep takes nothing else first),
//!   "POST conn 1", "STATE all 1" {"heartbeat":1} and "POST realtimeDisplay 1"
//!   {"enable":true}; closing "POST realtimeDisplay 1" {"enable":false}, and the screen shows
//!   its own again.
//! - A picture: a JPEG in pieces of 1000 bytes, each after 24 bytes: 5C, the piece's length +
//!   21 (two bytes big-endian), 34, the count of pieces and the piece's number (two bytes each,
//!   big-endian, from 0), 01, and zeros.
//! - "STATE all 1" {"heartbeat":1} every 5 seconds keeps the link, pictures or not; a picture
//!   goes only when the frame changed.
//!
//! The vendor id is the Linux Foundation's, which many devices built on Linux carry: a device
//! with these ids is this screen only where its product names AIGO. On Windows the screen keeps
//! Windows' own HID driver: its reports go through it (win_hid.rs), no WinUSB needed.

use std::time::Duration;

use lp_pixels::Image;

use crate::base::{dimmed, tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::keep::Keeper;
use crate::models::{find_model, PanelModel};
use crate::turzx_packet::{wall_clock, Clock};
use crate::usb_link::{self, HidReports, UsbError, UsbIo};

pub const PROTOCOL: &str = "usb-aigo";
pub const VENDOR_ID: u16 = 0x1D6B;
pub const PRODUCT_ID: u16 = 0x0103;
pub const MODEL_ID: &str = "aigo-xingcanchen";
pub const REPORT: usize = 1024;
const PIECE_HEADER: usize = 24;
pub const PIECE: usize = REPORT - PIECE_HEADER;
pub const MAX_JPEG: usize = 512 * 1024;
/// as AIGO's program writes them first
pub const QUALITIES: [i64; 4] = [85, 75, 65, 50];
const START: u8 = 0x5A;
const ESCAPE: u8 = 0x5B;
const WRITE_TIMEOUT_MS: u64 = 5000;
const ANSWER_TIMEOUT_MS: u64 = 2000;
/// answers read for one command at most (a late one of before comes first)
const ANSWERS: usize = 8;
const RESUME_PAUSE_S: f64 = 0.1;
const HEARTBEAT_S: f64 = 5.0;

/// is_aigo: whether a device's product names AIGO (its vendor id is not its own).
pub fn is_aigo(product: &str) -> bool {
    product.to_lowercase().contains("aigo")
}

/// present_model: the screen on the bus (not opened): a device with its ids whose product
/// names AIGO.
pub fn present_model() -> Option<&'static PanelModel> {
    if crate::testbus::ids().is_some() {
        usb_link::present(VENDOR_ID, &[PRODUCT_ID])?;
        return find_model(MODEL_ID);
    }
    let found = usb_link::find(VENDOR_ID, &[PRODUCT_ID]).ok()?;
    if found.is_empty() {
        return None;
    }
    let named = found.iter().any(|device| is_aigo(&device.product()));
    // (on Windows the bus may not say a HID device's product: its HID driver does)
    #[cfg(windows)]
    let named = named
        || crate::win_hid::products(VENDOR_ID, PRODUCT_ID)
            .iter()
            .any(|name| is_aigo(name));
    named.then(|| find_model(MODEL_ID)).flatten()
}

/// framed: a command's text as the screen takes it.
pub fn framed(text: &str) -> Result<Vec<u8>> {
    let content = text.as_bytes();
    let length = content.len() + 5;
    if length > 0xFFFF {
        return Err(DeviceError("a command is too long".into()));
    }
    let mut inner = (length as u16).to_be_bytes().to_vec();
    inner.extend_from_slice(content);
    let sum = inner.iter().fold(0u8, |sum, b| sum.wrapping_add(*b));
    inner.push(sum);
    let mut out = vec![START];
    for byte in inner {
        match byte {
            START => out.extend([ESCAPE, 0x01]),
            ESCAPE => out.extend([ESCAPE, 0x02]),
            byte => out.push(byte),
        }
    }
    out.push(START);
    if out.len() > REPORT {
        return Err(DeviceError("a command is too long".into()));
    }
    Ok(out)
}

/// unframed: the text of an answer (its framing and escapes taken off); None for none.
pub fn unframed(report: &[u8]) -> Option<String> {
    let start = report.iter().position(|&b| b == START)?;
    let end = start + 1 + report[start + 1..].iter().position(|&b| b == START)?;
    let mut inner = Vec::new();
    let mut bytes = report[start + 1..end].iter();
    while let Some(&byte) = bytes.next() {
        inner.push(match (byte, bytes.as_slice().first()) {
            (ESCAPE, Some(0x01)) => {
                bytes.next();
                START
            }
            (ESCAPE, Some(0x02)) => {
                bytes.next();
                ESCAPE
            }
            (byte, _) => byte,
        });
    }
    // (the length's two bytes, the text, the checksum)
    let text = inner.get(2..inner.len().checked_sub(1)?)?;
    Some(String::from_utf8_lossy(text).into_owned())
}

/// command: `name` as the screen takes it, with its sequence number and the date `now`.
pub fn command(name: &str, number: i64, body: Option<&str>, now: f64) -> Result<Vec<u8>> {
    let mut lines = vec![
        name.to_string(),
        format!("SeqNumber={number}"),
        format!("Date={}", (now * 1000.0) as i64),
    ];
    if let Some(body) = body {
        lines.push("ContentType=json".into());
        lines.push(format!("ContentLength={}", body.len()));
    }
    framed(&format!(
        "{}\r\n\r\n{}",
        lines.join("\r\n"),
        body.unwrap_or("")
    ))
}

/// done: whether an answer's text says the command numbered `number` was done.
pub fn done(answer: &str, number: i64) -> bool {
    let first = answer.lines().next().unwrap_or("");
    let ack = answer
        .lines()
        .find_map(|line| line.trim().strip_prefix("AckNumber="));
    first.split_whitespace().nth(1) == Some("200")
        && ack.is_none_or(|ack| ack.trim() == (number + 1).to_string())
}

/// pieces: a JPEG as the screen's reports.
pub fn pieces(jpeg: &[u8]) -> Vec<Vec<u8>> {
    let count = jpeg.len().div_ceil(PIECE).max(1);
    (0..count)
        .map(|number| {
            let piece =
                &jpeg[(number * PIECE).min(jpeg.len())..((number + 1) * PIECE).min(jpeg.len())];
            let mut report = vec![0u8; REPORT];
            report[0] = 0x5C;
            report[1..3].copy_from_slice(&((piece.len() + 21) as u16).to_be_bytes());
            report[3] = 0x34;
            report[4..6].copy_from_slice(&(count as u16).to_be_bytes());
            report[6..8].copy_from_slice(&(number as u16).to_be_bytes());
            report[8] = 0x01;
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

/// What the keeper guards: the link to the screen and the number of its next command.
struct Link {
    io: Option<Box<dyn UsbIo>>,
    reports: Option<HidReports>,
    number: i64,
    clock: Clock,
    pause_s: f64,
    label: String,
    counters: Counters,
}

impl Link {
    /// ask: a command, and what the screen answered to it (None where it said nothing of it).
    fn ask(
        &mut self,
        name: &str,
        body: Option<&str>,
    ) -> std::result::Result<Option<String>, Asked> {
        let (Some(io), Some(reports)) = (self.io.as_mut(), self.reports.as_ref()) else {
            return Err(Asked::Usb(UsbError::Other("the device is let go".into())));
        };
        let number = self.number;
        let message = command(name, number, body, (self.clock)()).map_err(Asked::Said)?;
        reports
            .write(io.as_mut(), &message, WRITE_TIMEOUT_MS)
            .map_err(Asked::Usb)?;
        self.number += 1;
        for _ in 0..ANSWERS {
            let Some(report) = reports
                .read(io.as_mut(), ANSWER_TIMEOUT_MS)
                .map_err(Asked::Usb)?
            else {
                return Ok(None);
            };
            if let Some(text) = unframed(&report) {
                let ack = text
                    .lines()
                    .find_map(|line| line.trim().strip_prefix("AckNumber="))
                    .map(|ack| ack.trim().to_string());
                // (an answer to a command of before: the next)
                if ack.is_none_or(|ack| ack == (number + 1).to_string()) {
                    return Ok(Some(text));
                }
            }
        }
        Ok(None)
    }

    /// asked: whether the screen did the command.
    fn asked(&mut self, name: &str, body: Option<&str>) -> std::result::Result<bool, Asked> {
        let number = self.number;
        Ok(self
            .ask(name, body)?
            .is_some_and(|text| done(&text, number)))
    }

    /// A picture's reports, in order.
    fn picture(&mut self, reports_of: &[Vec<u8>]) -> Result<()> {
        let (Some(io), Some(reports)) = (self.io.as_mut(), self.reports.as_ref()) else {
            return Err(self.stopped(&Asked::Usb(UsbError::Other("the device is let go".into()))));
        };
        for report in reports_of {
            if let Err(e) = reports.write(io.as_mut(), report, WRITE_TIMEOUT_MS) {
                return Err(self.stopped(&Asked::Usb(e)));
            }
        }
        self.counters.bytes += reports_of.iter().map(|r| r.len() as u64).sum::<u64>();
        Ok(())
    }

    /// The keeper's: the heartbeat, every 5 seconds.
    fn heartbeat(link: &mut Link) -> Result<()> {
        if link.io.is_none() {
            return Ok(());
        }
        match link.ask("STATE all 1", Some(r#"{"heartbeat":1}"#)) {
            Ok(_) => Ok(()),
            Err(why) => Err(link.stopped(&why)),
        }
    }

    fn stopped(&mut self, why: &Asked) -> DeviceError {
        self.counters.usb_errors += 1;
        DeviceError(tf(
            "the {panel} stopped answering: {error}",
            &[("panel", &self.label), ("error", &why.to_string())],
        ))
    }
}

/// Why a command went wrong: on the bus, or in making it.
#[derive(Debug)]
enum Asked {
    Usb(UsbError),
    Said(DeviceError),
}

impl std::fmt::Display for Asked {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Asked::Usb(e) => e.fmt(f),
            Asked::Said(why) => f.write_str(&why.0),
        }
    }
}

/// An AIGO case screen.
pub struct Aigo {
    pub model: &'static PanelModel,
    brightness: i64,
    picture: Option<Image>,
    keeper: Keeper<Link>,
}

impl Aigo {
    pub fn new(model: Option<&'static PanelModel>) -> Aigo {
        let model = model
            .or_else(|| find_model(MODEL_ID))
            .expect("AIGO's screen in the catalog");
        Aigo {
            model,
            brightness: 100,
            picture: None,
            keeper: Keeper::new(
                HEARTBEAT_S,
                Link {
                    io: None,
                    reports: None,
                    number: 0,
                    clock: wall_clock,
                    pause_s: RESUME_PAUSE_S,
                    label: model.label(),
                    counters: Counters::default(),
                },
                Link::heartbeat,
                "aigo-keep",
            ),
        }
    }

    /// The date the commands carry and the pause after waking the screen (the tests' own).
    pub fn set_clock(&mut self, clock: Clock, pause_s: f64) {
        self.keeper.with(|kept| {
            kept.inner.clock = clock;
            kept.inner.pause_s = pause_s;
        });
    }

    fn size(&self) -> (i64, i64) {
        let (w, h) = (self.model.native_width, self.model.native_height);
        (w.max(h), w.min(h))
    }

    pub fn open(&mut self) -> Result<()> {
        let label = self.model.label();
        let io = take(&label)?;
        self.open_on(io)
    }

    /// open on `io`, the screen taken for this panel.
    pub fn open_on(&mut self, mut io: Box<dyn UsbIo>) -> Result<()> {
        let label = self.model.label();
        let interface = usb_link::hid_interface(io.as_ref()).unwrap_or(0);
        if let Err(e) = usb_link::claim(io.as_mut(), &[interface]) {
            io.release();
            return Err(e);
        }
        let reports = HidReports::new(io.as_ref(), interface, REPORT, false);
        let started = self.keeper.with(|kept| {
            let link = &mut kept.inner;
            link.io = Some(io);
            link.reports = Some(reports);
            link.number = 0;
            // (answers left from before: thrown away)
            if let (Some(io), Some(reports)) = (link.io.as_mut(), link.reports.as_ref()) {
                for _ in 0..16 {
                    match reports.read(io.as_mut(), 10) {
                        Ok(Some(_)) => {}
                        Ok(None) => break,
                        Err(e) => return Err(Asked::Usb(e)),
                    }
                }
            }
            // (a screen asleep takes nothing before it is woken: what it says to it, whatever)
            link.ask("POST power 1", Some(r#"{"event":"resume"}"#))?;
            if link.pause_s > 0.0 {
                std::thread::sleep(Duration::from_secs_f64(link.pause_s));
            }
            let number = link.number;
            let connected = link.ask("POST conn 1", None)?;
            let Some(connected) = connected.filter(|text| done(text, number)) else {
                return Err(Asked::Said(DeviceError("POST conn: no 200".into())));
            };
            // (where it shows the pictures it is sent already, it may refuse being told again)
            let showing = connected
                .replace([' ', '\t'], "")
                .contains(r#""realtimeDisplay":1"#)
                || connected
                    .replace([' ', '\t'], "")
                    .contains(r#""realtimeDisplay":true"#);
            link.ask("STATE all 1", Some(r#"{"heartbeat":1}"#))?;
            if !link.asked("POST realtimeDisplay 1", Some(r#"{"enable":true}"#))? && !showing {
                return Err(Asked::Said(DeviceError(
                    "POST realtimeDisplay: no 200".into(),
                )));
            }
            Ok(())
        });
        if let Err(why) = started {
            self.keeper.with(|kept| {
                let link = &mut kept.inner;
                if let Some(mut io) = link.io.take() {
                    io.release();
                }
                link.reports = None;
                if matches!(why, Asked::Usb(_)) {
                    link.counters.usb_errors += 1;
                }
            });
            return Err(DeviceError(tf(
                "the {panel} did not start: {error}",
                &[("panel", &label), ("error", &why.to_string())],
            )));
        }
        self.keeper.start();
        Ok(())
    }

    fn is_open(&self) -> bool {
        self.keeper.with(|kept| kept.inner.io.is_some())
    }

    /// The picture, dimmed, to the screen.
    fn send(&mut self, picture: &Image) -> Result<()> {
        let reports = pieces(&jpeg(&dimmed(picture, self.brightness)?)?);
        self.keeper.with(|kept| {
            kept.check()?;
            kept.inner.picture(&reports)
        })
    }
}

/// take: the screen, opened for this panel: on Windows through Windows' own HID driver, else
/// on the bus (a device with its ids whose product does not name AIGO is left alone).
fn take(label: &str) -> Result<Box<dyn UsbIo>> {
    #[cfg(windows)]
    {
        let (device, _) = crate::win_hid::take(VENDOR_ID, &[PRODUCT_ID], REPORT, label)?;
        let mut device: Box<dyn UsbIo> = Box::new(device);
        if !is_aigo(&device.product()) {
            device.release();
            return Err(DeviceError(tf(
                "no {panel} on the USB bus",
                &[("panel", label)],
            )));
        }
        Ok(device)
    }
    #[cfg(not(windows))]
    {
        let found = usb_link::find(VENDOR_ID, &[PRODUCT_ID])?;
        if !found.iter().any(|device| is_aigo(&device.product())) {
            return Err(DeviceError(tf(
                "no {panel} on the USB bus",
                &[("panel", label)],
            )));
        }
        let (mut io, _) = usb_link::take(VENDOR_ID, &[PRODUCT_ID], label).map_err(|e| {
            if e.0.starts_with("another panel") {
                DeviceError(tf(
                    "another panel of Libre Panel has the {panel} open",
                    &[("panel", label)],
                ))
            } else {
                e
            }
        })?;
        if !is_aigo(&io.product()) {
            io.release();
            return Err(DeviceError(tf(
                "no {panel} on the USB bus",
                &[("panel", label)],
            )));
        }
        Ok(Box::new(io))
    }
}

impl Driver for Aigo {
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
        let native = crate::beada::to_native(frame, self.size())?;
        self.send(&native)?;
        self.picture = Some(native);
        Ok(())
    }

    fn set_brightness(&mut self, percent: i64) -> Result<()> {
        self.brightness = percent.clamp(0, 100);
        if self.is_open() {
            if let Some(picture) = self.picture.clone() {
                self.send(&picture)?;
            }
        }
        Ok(())
    }

    fn counters(&self) -> Counters {
        self.keeper.with(|kept| kept.inner.counters)
    }

    fn picture(&self) -> String {
        "JPEG".into()
    }

    fn close(&mut self) {
        self.keeper.stop();
        let io = self.keeper.with(|kept| {
            let link = &mut kept.inner;
            if link.io.is_some() {
                // (the screen back to its own)
                let _ = link.ask("POST realtimeDisplay 1", Some(r#"{"enable":false}"#));
            }
            link.reports = None;
            link.io.take()
        });
        if let Some(mut io) = io {
            io.release();
        }
        self.picture = None;
    }
}

impl Drop for Aigo {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;
    use std::sync::{Arc, Mutex};

    use super::*;
    use crate::usb_link::{EndpointInfo, InterfaceInfo, UsbResult};

    fn at() -> f64 {
        1_795_600_000.123
    }

    /// A screen the tests make up: what was written to it, and its answers ("1 200" with the
    /// AckNumber, unless `refuse` names the command; nothing to a picture's reports).
    struct Made {
        written: Arc<Mutex<Vec<Vec<u8>>>>,
        answers: VecDeque<Vec<u8>>,
        refuse: Option<&'static str>,
        /// what "conn" says of the screen
        conn_body: &'static str,
        /// an answer of before, waiting when it is opened
        stale: bool,
    }

    impl Made {
        fn new(written: &Arc<Mutex<Vec<Vec<u8>>>>) -> Made {
            Made {
                written: Arc::clone(written),
                answers: VecDeque::new(),
                refuse: None,
                conn_body: r#"{"sn":"0123","realtimeDisplay":0}"#,
                stale: false,
            }
        }
    }

    fn text_of(message: &[u8]) -> String {
        unframed(message).expect("a framed command")
    }

    impl UsbIo for Made {
        fn product(&mut self) -> String {
            "Aigo USB Device".into()
        }
        fn interfaces(&self) -> Vec<InterfaceInfo> {
            let endpoint = |address| EndpointInfo {
                address,
                max_packet: 1024,
                interrupt: true,
            };
            vec![InterfaceInfo {
                number: 0,
                class: 3,
                endpoints: vec![endpoint(0x01), endpoint(0x81)],
            }]
        }
        fn claim(&mut self, _: &[u8]) -> UsbResult<()> {
            if self.stale {
                self.stale = false;
                self.answers
                    .push_back(framed("1 200\r\nAckNumber=77\r\n\r\n").unwrap());
            }
            Ok(())
        }
        fn write(&mut self, endpoint: u8, data: &[u8], _: u64) -> UsbResult<()> {
            assert_eq!((endpoint, data.len()), (0x01, REPORT));
            self.written.lock().unwrap().push(data.to_vec());
            if data[0] == 0x5C {
                return Ok(()); // (a picture's piece)
            }
            let text = text_of(data);
            let first = text.lines().next().unwrap().to_string();
            let number: i64 = text
                .lines()
                .find_map(|line| line.strip_prefix("SeqNumber="))
                .unwrap()
                .parse()
                .unwrap();
            let status = if self.refuse == Some(first.split(' ').nth(1).unwrap()) {
                "1 400"
            } else {
                "1 200"
            };
            let body = if first == "POST conn 1" {
                self.conn_body
            } else {
                ""
            };
            let answer = format!("{status}\r\nAckNumber={}\r\n\r\n{body}", number + 1);
            self.answers.push_back(framed(&answer).unwrap());
            Ok(())
        }
        fn read(&mut self, endpoint: u8, _: usize, _: u64) -> UsbResult<Vec<u8>> {
            assert_eq!(endpoint, 0x81);
            let mut report = self.answers.pop_front().ok_or(UsbError::Timeout)?;
            report.resize(REPORT, 0);
            Ok(report)
        }
        fn control_out(&mut self, _: u8, _: u8, _: u16, _: u16, _: &[u8], _: u64) -> UsbResult<()> {
            panic!("the screen is sent no control transfer");
        }
        fn control_in(
            &mut self,
            _: u8,
            _: u8,
            _: u16,
            _: u16,
            _: u16,
            _: u64,
        ) -> UsbResult<Vec<u8>> {
            panic!("the screen is asked no control transfer");
        }
        fn release(&mut self) {}
    }

    fn opened(made: Made) -> Result<Aigo> {
        let mut screen = Aigo::new(find_model(MODEL_ID));
        screen.set_clock(at, 0.0);
        screen.open_on(Box::new(made))?;
        Ok(screen)
    }

    fn commands(written: &Arc<Mutex<Vec<Vec<u8>>>>) -> Vec<String> {
        written
            .lock()
            .unwrap()
            .iter()
            .filter(|m| m[0] == START)
            .map(|m| text_of(m))
            .collect()
    }

    #[test]
    fn a_command_is_framed_and_escaped_as_the_screen_takes_it() {
        // (the length two bytes big-endian, the sum of it and the text, 5A and 5B escaped)
        assert_eq!(
            framed("ab").unwrap(),
            [0x5A, 0x00, 0x07, b'a', b'b', 0xCA, 0x5A]
        );
        let text = "Z[";
        let out = framed(text).unwrap();
        // (the sum: 0x00 + 0x07 + 0x5A + 0x5B = 0xBC)
        assert_eq!(out, [0x5A, 0x00, 0x07, 0x5B, 0x01, 0x5B, 0x02, 0xBC, 0x5A]);
        assert_eq!(unframed(&out).as_deref(), Some(text));
        // a length or a sum that is 5A is escaped as well
        let long = "x".repeat(0x5A - 5);
        let out = framed(&long).unwrap();
        assert_eq!(&out[..4], [0x5A, 0x00, 0x5B, 0x01]);
        assert_eq!(unframed(&out).as_deref(), Some(long.as_str()));
        assert!(framed(&"x".repeat(2000)).is_err());
        assert_eq!(unframed(&[0, 0, 0]), None);
    }

    #[test]
    fn a_command_is_written_as_aigo_s_program_writes_it() {
        let message = command(
            "POST realtimeDisplay 1",
            3,
            Some(r#"{"enable":true}"#),
            at(),
        )
        .unwrap();
        assert_eq!(
            text_of(&message),
            "POST realtimeDisplay 1\r\nSeqNumber=3\r\nDate=1795600000123\r\nContentType=json\r\n\
             ContentLength=15\r\n\r\n{\"enable\":true}"
        );
        assert_eq!(
            text_of(&command("POST conn 1", 0, None, at()).unwrap()),
            "POST conn 1\r\nSeqNumber=0\r\nDate=1795600000123\r\n\r\n"
        );
        assert!(done("1 200\r\nAckNumber=4\r\n\r\n", 3));
        assert!(!done("1 200\r\nAckNumber=9\r\n\r\n", 3));
        assert!(!done("1 400\r\nAckNumber=4\r\n\r\n", 3));
        assert!(done("1 200\r\n\r\n", 3));
    }

    #[test]
    fn a_picture_goes_in_pieces_of_1000_bytes_after_their_headers() {
        let jpeg: Vec<u8> = (0..2500u32).map(|n| n as u8).collect();
        let reports = pieces(&jpeg);
        assert_eq!(reports.len(), 3);
        assert_eq!(
            &reports[0][..9],
            [0x5C, 0x03, 0xFD, 0x34, 0x00, 0x03, 0x00, 0x00, 0x01]
        );
        assert!(reports[0][9..24].iter().all(|&b| b == 0));
        assert_eq!(&reports[0][24..], &jpeg[..1000]);
        // the last: its own length + 21, its number, filled up with zeros
        assert_eq!(
            &reports[2][..9],
            [0x5C, 0x02, 0x09, 0x34, 0x00, 0x03, 0x00, 0x02, 0x01]
        );
        assert_eq!(&reports[2][24..524], &jpeg[2000..]);
        assert!(reports[2][524..].iter().all(|&b| b == 0));
        assert!(reports.iter().all(|r| r.len() == REPORT));
    }

    #[test]
    fn the_screen_is_woken_connected_and_shown_a_picture() {
        let written = Arc::new(Mutex::new(Vec::new()));
        let mut made = Made::new(&written);
        made.stale = true; // (an answer of before waits: thrown away)
        let mut screen = opened(made).unwrap();
        let firsts: Vec<String> = commands(&written)
            .iter()
            .map(|t| t.lines().next().unwrap().to_string())
            .collect();
        assert_eq!(
            firsts,
            [
                "POST power 1",
                "POST conn 1",
                "STATE all 1",
                "POST realtimeDisplay 1"
            ]
        );
        assert!(commands(&written)[0].ends_with(r#"{"event":"resume"}"#));
        assert!(commands(&written)[2].ends_with(r#"{"heartbeat":1}"#));
        let frame = Image::new(lp_pixels::Mode::RGB, 1920, 462, &[200, 30, 30]);
        // (a picture where the JPEGs can be made: libjpeg, LP_LIBJPEG names it)
        if lp_decode::save_jpeg(&frame, 85).is_ok() {
            written.lock().unwrap().clear();
            screen.show(&frame, None).unwrap();
            let sent = written.lock().unwrap().clone();
            assert!(!sent.is_empty() && sent.iter().all(|r| r[0] == 0x5C));
            let count = u16::from_be_bytes([sent[0][4], sent[0][5]]) as usize;
            assert_eq!(sent.len(), count);
            let mut jpeg = Vec::new();
            for report in &sent {
                let length = u16::from_be_bytes([report[1], report[2]]) as usize - 21;
                jpeg.extend_from_slice(&report[24..24 + length]);
            }
            let shown = lp_decode::open(&jpeg, lp_pixels::Mode::RGB).unwrap();
            assert_eq!((shown.width(), shown.height()), (1920, 462));
        }
        // the heartbeat, and the screen given back when it closes
        written.lock().unwrap().clear();
        screen
            .keeper
            .with(|kept| Link::heartbeat(&mut kept.inner))
            .unwrap();
        screen.close();
        let said = commands(&written);
        assert_eq!(said.len(), 2);
        assert!(said[0].starts_with("STATE all 1") && said[0].ends_with(r#"{"heartbeat":1}"#));
        assert!(said[1].starts_with("POST realtimeDisplay 1"));
        assert!(said[1].ends_with(r#"{"enable":false}"#));
        assert!(screen.show(&frame, None).is_err());
    }

    #[test]
    fn a_screen_that_refuses_is_not_opened_unless_it_shows_already() {
        let written = Arc::new(Mutex::new(Vec::new()));
        let mut refusing = Made::new(&written);
        refusing.refuse = Some("conn");
        let why = opened(refusing).err().unwrap().0;
        assert!(why.contains("POST conn: no 200"), "{why}");
        let mut refusing = Made::new(&written);
        refusing.refuse = Some("realtimeDisplay");
        let why = opened(refusing).err().unwrap().0;
        assert!(why.contains("POST realtimeDisplay: no 200"), "{why}");
        // (one that shows the pictures it is sent already may refuse being told again)
        let mut showing = Made::new(&written);
        showing.refuse = Some("realtimeDisplay");
        showing.conn_body = r#"{"sn":"0123", "realtimeDisplay": 1}"#;
        opened(showing).unwrap().close();
        // (being woken is no need: a screen awake may refuse it)
        let mut awake = Made::new(&written);
        awake.refuse = Some("power");
        opened(awake).unwrap().close();
    }

    #[test]
    fn only_a_device_whose_product_names_aigo_is_the_screen() {
        assert!(is_aigo("Aigo USB Device"));
        assert!(is_aigo("AIGO screen"));
        assert!(!is_aigo("Multifunction Composite Gadget"));
        assert!(!is_aigo(""));
    }
}
