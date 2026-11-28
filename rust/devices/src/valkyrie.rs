//! Valkyrie's B360-GT VK cooler screen of 480x480 (USB 374a:a005, its product "Vk-Copl-08"): a
//! device of the maker's own class (interface 0, bulk OUT 02 and IN 81, WinUSB on Windows) that
//! takes the pixels as they are. Written from what its owner captured of Myth.Cool, the maker's
//! program, and the BoWei "mode 2" notes of the same family (VID 374a):
//!
//! - A request is a command (u16), the length of what follows (u32) and that, little-endian. The
//!   screen answers the command with 0x8000 set, the length and what it says; a status of four
//!   bytes is 0 where it did it.
//! - Opening: 0x1003 with 0x00012345; 0x0001, the screen's info (28 bytes: the pixel format 0x0E
//!   at 0, the width at 12, the height at 16, the size of its pixels at 24); the backlight at
//!   full (0x0002, 100: its range is not proven, so the brightness goes into the pixels).
//! - A picture: 0x1001 with its size, answered, then its pixels as BGR565 (two bytes a pixel,
//!   little-endian, red in the low bits) in transfers of at most 128 KiB; one whose length is a
//!   multiple of 4 KiB is followed by an empty one, as Myth.Cool sends them.
//! - 0x000F with 0x12345678 once a second while no picture goes keeps the link.
//!
//! The maker's closing command blanks the screen and is never sent: when Libre Panel quits, the
//! screen goes back to its own picture by itself, as its owner saw (issue #69).

use lp_pixels::{Image, Mode};

use crate::base::{dimmed, tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::keep::Keeper;
use crate::models::{find_model, PanelModel};
use crate::usb_link::{self, UsbError, UsbIo};

pub const PROTOCOL: &str = "usb-valkyrie";
pub const VENDOR_ID: u16 = 0x374A;
pub const PRODUCT_ID: u16 = 0xA005;
pub const MODEL_ID: &str = "valkyrie-b360gt-vk";
const CMD_SCREEN_INFO: u16 = 0x0001;
const CMD_BRIGHTNESS: u16 = 0x0002;
const CMD_HEARTBEAT: u16 = 0x000F;
const CMD_FRAME: u16 = 0x1001;
const CMD_HELLO: u16 = 0x1003;
const HELLO: u32 = 0x0001_2345;
const HEARTBEAT: u32 = 0x1234_5678;
/// the screen's pixel format: BGR565
const PIXEL_FORMAT: u32 = 0x0E;
/// the pixels go in transfers of at most this many bytes; one of a multiple of ALIGN is followed
/// by an empty one
pub const CHUNK: usize = 0x20000;
const ALIGN: usize = 0x1000;
const ANSWER: usize = 512;
/// answers read for one request at most (one to another request of before comes first)
const ANSWERS: usize = 4;
const WRITE_TIMEOUT_MS: u64 = 3000;
const ANSWER_TIMEOUT_MS: u64 = 1000;
/// a heartbeat after so long without a picture
const AGAIN_S: f64 = 1.0;

/// present_model: the screen on the bus (not opened).
pub fn present_model() -> Option<&'static PanelModel> {
    usb_link::present(VENDOR_ID, &[PRODUCT_ID])?;
    find_model(MODEL_ID)
}

/// request: a command with what follows it, as the screen takes it.
pub fn request(command: u16, data: &[u8]) -> Vec<u8> {
    let mut out = command.to_le_bytes().to_vec();
    out.extend((data.len() as u32).to_le_bytes());
    out.extend_from_slice(data);
    out
}

/// answer_of: what the screen says to `command` in `answer`; an error where it answered
/// another command, its length does not hold, or its status says it did not do it.
pub fn answer_of(command: u16, answer: &[u8]) -> std::result::Result<Vec<u8>, String> {
    if answer.len() < 6 {
        return Err(format!("a short answer ({} bytes)", answer.len()));
    }
    let said = u16::from_le_bytes([answer[0], answer[1]]);
    if said != command | 0x8000 {
        return Err(format!("0x{said:04x} answered 0x{command:04x}"));
    }
    let length = u32::from_le_bytes([answer[2], answer[3], answer[4], answer[5]]) as usize;
    let Some(body) = answer.get(6..6 + length) else {
        return Err(format!(
            "an answer of {} bytes said {length}",
            answer.len() - 6
        ));
    };
    if length == 4 && body != [0, 0, 0, 0] {
        let status = u32::from_le_bytes([body[0], body[1], body[2], body[3]]);
        return Err(format!("0x{command:04x} refused ({status})"));
    }
    Ok(body.to_vec())
}

/// The screen's info: its pixel format, width, height and the size of its pixels.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Info {
    pub format: u32,
    pub width: u32,
    pub height: u32,
    pub size: u32,
}

pub fn info_of(body: &[u8]) -> Option<Info> {
    if body.len() < 28 {
        return None;
    }
    let at = |i: usize| u32::from_le_bytes([body[i], body[i + 1], body[i + 2], body[i + 3]]);
    Some(Info {
        format: at(0),
        width: at(12),
        height: at(16),
        size: at(24),
    })
}

/// bgr565: an RGB picture's pixels as the screen takes them: two bytes each, little-endian,
/// red in the low five bits, green in the six above, blue in the high five.
pub fn bgr565(picture: &Image) -> Vec<u8> {
    let bytes = picture.as_bytes();
    let mut out = Vec::with_capacity(bytes.len() / 3 * 2);
    for pixel in bytes.chunks_exact(3) {
        let (r, g, b) = (
            u16::from(pixel[0]),
            u16::from(pixel[1]),
            u16::from(pixel[2]),
        );
        let value = (r >> 3) | ((g >> 2) << 5) | ((b >> 3) << 11);
        out.extend(value.to_le_bytes());
    }
    out
}

/// transfers: the pixels in the transfers they go in: at most CHUNK bytes each, and an empty one
/// after each whose length is a multiple of ALIGN.
pub fn transfers(pixels: &[u8]) -> Vec<&[u8]> {
    let mut out = Vec::new();
    for chunk in pixels.chunks(CHUNK) {
        out.push(chunk);
        if chunk.len() % ALIGN == 0 {
            out.push(&[][..]);
        }
    }
    out
}

/// What the keeper guards: the link to the screen.
struct Link {
    io: Option<Box<dyn UsbIo>>,
    out: u8,
    into: u8,
    label: String,
    counters: Counters,
}

impl Link {
    /// ask: a request, and what the screen answers to it (an answer to another request of
    /// before passed over).
    fn ask(&mut self, command: u16, data: &[u8]) -> std::result::Result<Vec<u8>, Asked> {
        let Some(io) = self.io.as_mut() else {
            return Err(Asked::Usb(UsbError::Other("the device is let go".into())));
        };
        io.write(self.out, &request(command, data), WRITE_TIMEOUT_MS)
            .map_err(Asked::Usb)?;
        let mut last = Vec::new();
        for _ in 0..ANSWERS {
            last = io
                .read(self.into, ANSWER, ANSWER_TIMEOUT_MS)
                .map_err(Asked::Usb)?;
            let said = last.get(..2).map(|c| u16::from_le_bytes([c[0], c[1]]));
            if said == Some(command | 0x8000) {
                break;
            }
        }
        answer_of(command, &last).map_err(Asked::Said)
    }

    /// The pixels of a picture, announced and sent.
    fn picture(&mut self, pixels: &[u8]) -> Result<()> {
        if let Err(why) = self.ask(CMD_FRAME, &(pixels.len() as u32).to_le_bytes()) {
            return Err(self.stopped(&why));
        }
        let Some(io) = self.io.as_mut() else {
            return Err(self.stopped(&Asked::Usb(UsbError::Other("the device is let go".into()))));
        };
        for transfer in transfers(pixels) {
            if let Err(e) = io.write(self.out, transfer, WRITE_TIMEOUT_MS) {
                return Err(self.stopped(&Asked::Usb(e)));
            }
        }
        self.counters.bytes += pixels.len() as u64;
        Ok(())
    }

    /// The keeper's: a heartbeat while no picture goes.
    fn heartbeat(link: &mut Link) -> Result<()> {
        if link.io.is_none() {
            return Ok(());
        }
        match link.ask(CMD_HEARTBEAT, &HEARTBEAT.to_le_bytes()) {
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

/// Why a request went wrong: on the bus, or in what the screen said.
#[derive(Debug)]
enum Asked {
    Usb(UsbError),
    Said(String),
}

impl std::fmt::Display for Asked {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Asked::Usb(e) => e.fmt(f),
            Asked::Said(why) => f.write_str(why),
        }
    }
}

/// Valkyrie's cooler screen.
pub struct Valkyrie {
    pub model: &'static PanelModel,
    brightness: i64,
    picture: Option<Image>,
    keeper: Keeper<Link>,
}

impl Valkyrie {
    pub fn new(model: Option<&'static PanelModel>) -> Valkyrie {
        let model = model
            .or_else(|| find_model(MODEL_ID))
            .expect("Valkyrie's screen in the catalog");
        Valkyrie {
            model,
            brightness: 100,
            picture: None,
            keeper: Keeper::new(
                AGAIN_S,
                Link {
                    io: None,
                    out: 0x02,
                    into: 0x81,
                    label: model.label(),
                    counters: Counters::default(),
                },
                Link::heartbeat,
                "valkyrie-keep",
            ),
        }
    }

    fn size(&self) -> (i64, i64) {
        (self.model.native_width, self.model.native_height)
    }

    pub fn open(&mut self) -> Result<()> {
        let label = self.model.label();
        if usb_link::find(VENDOR_ID, &[PRODUCT_ID])?.is_empty() {
            return Err(DeviceError(tf(
                "no {panel} on the USB bus",
                &[("panel", &label)],
            )));
        }
        let (io, _) = usb_link::take(VENDOR_ID, &[PRODUCT_ID], &label).map_err(|e| {
            if e.0.starts_with("another panel") {
                DeviceError(tf(
                    "another panel of Libre Panel has the {panel} open",
                    &[("panel", &label)],
                ))
            } else {
                e
            }
        })?;
        self.open_on(Box::new(io))
    }

    /// open on `io`, the screen taken for this panel.
    pub fn open_on(&mut self, mut io: Box<dyn UsbIo>) -> Result<()> {
        let label = self.model.label();
        if let Err(e) = usb_link::claim(io.as_mut(), &[0]) {
            io.release();
            return Err(e);
        }
        let (Some(out), Some(into)) = usb_link::endpoints(io.as_ref(), 0) else {
            io.release();
            return Err(DeviceError(tf(
                "the {panel} did not start: {error}",
                &[("panel", &label), ("error", "its endpoints are not there")],
            )));
        };
        let (width, height) = self.size();
        let started = self.keeper.with(|kept| {
            let link = &mut kept.inner;
            link.io = Some(io);
            link.out = out;
            link.into = into;
            // (an answer left from before: thrown away)
            if let Some(io) = link.io.as_mut() {
                for _ in 0..16 {
                    if io.read(into, ANSWER, 20).is_err() {
                        break;
                    }
                }
            }
            link.ask(CMD_HELLO, &HELLO.to_le_bytes())?;
            let body = link.ask(CMD_SCREEN_INFO, &[])?;
            let Some(info) = info_of(&body) else {
                return Err(Asked::Said(format!("its info is {} bytes", body.len())));
            };
            let size = (width * height * 2) as u32;
            if info.format != PIXEL_FORMAT
                || (i64::from(info.width), i64::from(info.height)) != (width, height)
                || info.size != size
            {
                return Err(Asked::Said(format!(
                    "it is {}x{} (pixel format 0x{:x}, {} bytes), not {width}x{height}",
                    info.width, info.height, info.format, info.size
                )));
            }
            link.ask(CMD_BRIGHTNESS, &[100])?;
            Ok(())
        });
        if let Err(why) = started {
            self.keeper.with(|kept| {
                if let Some(mut io) = kept.inner.io.take() {
                    io.release();
                }
                if matches!(why, Asked::Usb(_)) {
                    kept.inner.counters.usb_errors += 1;
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
        let pixels = bgr565(&dimmed(picture, self.brightness)?);
        self.keeper.with(|kept| {
            kept.check()?;
            kept.inner.picture(&pixels)?;
            kept.sent();
            Ok(())
        })
    }
}

impl Driver for Valkyrie {
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
        let mut native = crate::beada::to_native(frame, self.size())?;
        if native.mode() != Mode::RGB {
            native = native.convert(Mode::RGB)?;
        }
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
        "BGR565".into()
    }

    fn close(&mut self) {
        self.keeper.stop();
        let io = self.keeper.with(|kept| kept.inner.io.take());
        if let Some(mut io) = io {
            io.release();
        }
        self.picture = None;
    }
}

impl Drop for Valkyrie {
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

    /// A screen the tests make up: what was written to it, and its answer to each request (the
    /// pixels are answered by nothing; `info` is what it says of itself).
    struct Made {
        written: Arc<Mutex<Vec<Vec<u8>>>>,
        answers: VecDeque<Vec<u8>>,
        info: Info,
        refuse: Option<u16>,
        expect_pixels: usize,
    }

    impl Made {
        fn new(written: &Arc<Mutex<Vec<Vec<u8>>>>) -> Made {
            Made {
                written: Arc::clone(written),
                answers: VecDeque::new(),
                info: Info {
                    format: 0x0E,
                    width: 480,
                    height: 480,
                    size: 460_800,
                },
                refuse: None,
                expect_pixels: 0,
            }
        }
    }

    fn answer(command: u16, body: &[u8]) -> Vec<u8> {
        request(command | 0x8000, body)
    }

    impl UsbIo for Made {
        fn product(&mut self) -> String {
            "Vk-Copl-08".into()
        }
        fn interfaces(&self) -> Vec<InterfaceInfo> {
            let endpoint = |address| EndpointInfo {
                address,
                max_packet: 512,
                interrupt: false,
            };
            vec![InterfaceInfo {
                number: 0,
                class: 0xFF,
                endpoints: vec![endpoint(0x02), endpoint(0x81)],
            }]
        }
        fn claim(&mut self, _: &[u8]) -> UsbResult<()> {
            Ok(())
        }
        fn write(&mut self, endpoint: u8, data: &[u8], _: u64) -> UsbResult<()> {
            assert_eq!(endpoint, 0x02);
            self.written.lock().unwrap().push(data.to_vec());
            if self.expect_pixels > 0 {
                self.expect_pixels = self.expect_pixels.saturating_sub(data.len());
                return Ok(());
            }
            let command = u16::from_le_bytes([data[0], data[1]]);
            let status: &[u8] = if self.refuse == Some(command) {
                &[1, 0, 0, 0]
            } else {
                &[0, 0, 0, 0]
            };
            let reply = match command {
                CMD_SCREEN_INFO => {
                    let mut body = vec![0u8; 28];
                    body[0..4].copy_from_slice(&self.info.format.to_le_bytes());
                    body[4..8].copy_from_slice(&16u32.to_le_bytes());
                    body[8..12].copy_from_slice(&960u32.to_le_bytes());
                    body[12..16].copy_from_slice(&self.info.width.to_le_bytes());
                    body[16..20].copy_from_slice(&self.info.height.to_le_bytes());
                    body[20..24].copy_from_slice(&0x4009_0420u32.to_le_bytes());
                    body[24..28].copy_from_slice(&self.info.size.to_le_bytes());
                    answer(command, &body)
                }
                _ => answer(command, status),
            };
            if command == CMD_FRAME && self.refuse != Some(command) {
                self.expect_pixels =
                    u32::from_le_bytes([data[6], data[7], data[8], data[9]]) as usize;
            }
            self.answers.push_back(reply);
            Ok(())
        }
        fn read(&mut self, endpoint: u8, _: usize, _: u64) -> UsbResult<Vec<u8>> {
            assert_eq!(endpoint, 0x81);
            self.answers.pop_front().ok_or(UsbError::Timeout)
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

    fn opened(made: Made) -> Result<Valkyrie> {
        let mut screen = Valkyrie::new(find_model(MODEL_ID));
        screen.open_on(Box::new(made))?;
        Ok(screen)
    }

    #[test]
    fn requests_are_laid_out_as_the_owner_captured_them() {
        // (the capture's: the hello, the backlight, a picture of 460800 bytes, a heartbeat)
        assert_eq!(
            request(CMD_HELLO, &HELLO.to_le_bytes()),
            [0x03, 0x10, 0x04, 0, 0, 0, 0x45, 0x23, 0x01, 0x00]
        );
        assert_eq!(
            request(CMD_BRIGHTNESS, &[80]),
            [0x02, 0x00, 0x01, 0, 0, 0, 0x50]
        );
        assert_eq!(
            request(CMD_FRAME, &460_800u32.to_le_bytes()),
            [0x01, 0x10, 0x04, 0, 0, 0, 0x00, 0x08, 0x07, 0x00]
        );
        assert_eq!(
            request(CMD_HEARTBEAT, &HEARTBEAT.to_le_bytes()),
            [0x0F, 0x00, 0x04, 0, 0, 0, 0x78, 0x56, 0x34, 0x12]
        );
        let done = [0x03, 0x90, 0x04, 0, 0, 0, 0, 0, 0, 0];
        assert_eq!(answer_of(CMD_HELLO, &done), Ok(vec![0, 0, 0, 0]));
        assert!(answer_of(CMD_HEARTBEAT, &done).is_err()); // (another command's answer)
        assert!(answer_of(CMD_HELLO, &[0x03, 0x90, 0x04, 0, 0, 0, 1, 0, 0, 0]).is_err());
        assert!(answer_of(CMD_HELLO, &[0x03, 0x90, 0x08, 0, 0, 0, 0, 0]).is_err());
        assert!(answer_of(CMD_HELLO, &[0x03, 0x90]).is_err());
    }

    #[test]
    fn a_pixel_is_bgr565_red_in_the_low_bits() {
        let data = [255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255].to_vec();
        let picture = Image::from_bytes(Mode::RGB, 4, 1, data).unwrap();
        assert_eq!(
            bgr565(&picture),
            [0x1F, 0x00, 0xE0, 0x07, 0x00, 0xF8, 0xFF, 0xFF]
        );
    }

    #[test]
    fn a_picture_goes_in_the_transfers_myth_cool_sends() {
        let pixels = vec![0u8; 460_800];
        let sizes: Vec<usize> = transfers(&pixels).iter().map(|t| t.len()).collect();
        assert_eq!(sizes, [131_072, 0, 131_072, 0, 131_072, 0, 67_584]);
        assert_eq!(
            transfers(&[0u8; 8192])
                .iter()
                .map(|t| t.len())
                .collect::<Vec<_>>(),
            [8192, 0]
        );
    }

    #[test]
    fn the_screen_is_opened_and_shown_a_picture() {
        let written = Arc::new(Mutex::new(Vec::new()));
        let mut screen = opened(Made::new(&written)).unwrap();
        let commands: Vec<Vec<u8>> = written.lock().unwrap().clone();
        assert_eq!(
            commands,
            [
                request(CMD_HELLO, &HELLO.to_le_bytes()),
                request(CMD_SCREEN_INFO, &[]),
                request(CMD_BRIGHTNESS, &[100]),
            ]
        );
        written.lock().unwrap().clear();
        let frame = Image::new(Mode::RGB, 480, 480, &[255, 0, 0]);
        screen.show(&frame, None).unwrap();
        let sent = written.lock().unwrap().clone();
        assert_eq!(sent[0], request(CMD_FRAME, &460_800u32.to_le_bytes()));
        let lengths: Vec<usize> = sent[1..].iter().map(Vec::len).collect();
        assert_eq!(lengths, [131_072, 0, 131_072, 0, 131_072, 0, 67_584]);
        assert_eq!(&sent[1][..4], [0x1F, 0x00, 0x1F, 0x00]);
        assert_eq!(screen.counters().bytes, 460_800);
        // the brightness in the pixels: half as bright, the picture again
        written.lock().unwrap().clear();
        screen.set_brightness(50).unwrap();
        let sent = written.lock().unwrap().clone();
        assert_eq!(sent[0], request(CMD_FRAME, &460_800u32.to_le_bytes()));
        assert_eq!(&sent[1][..2], [0x0F, 0x00]);
        screen.close();
        assert!(screen.show(&frame, None).is_err());
    }

    #[test]
    fn a_screen_of_another_size_or_that_refuses_is_not_opened() {
        let written = Arc::new(Mutex::new(Vec::new()));
        let mut other = Made::new(&written);
        other.info.width = 640;
        other.info.size = 614_400;
        let why = opened(other).err().unwrap().0;
        assert!(why.contains("640x480"), "{why}");
        let mut refusing = Made::new(&written);
        refusing.refuse = Some(CMD_HELLO);
        let why = opened(refusing).err().unwrap().0;
        assert!(why.contains("0x1003 refused"), "{why}");
        let mut format = Made::new(&written);
        format.info.format = 0xF001;
        assert!(opened(format).is_err());
    }

    #[test]
    fn an_answer_to_another_request_of_before_is_passed_over() {
        let written = Arc::new(Mutex::new(Vec::new()));
        let mut screen = opened(Made::new(&written)).unwrap();
        screen.keeper.with(|kept| {
            let link = &mut kept.inner;
            let io = link.io.as_mut().unwrap();
            // (what a screen might say after a picture's pixels)
            io.write(0x02, &request(CMD_FRAME, &0u32.to_le_bytes()), 0)
                .unwrap();
        });
        screen
            .keeper
            .with(|kept| Link::heartbeat(&mut kept.inner))
            .unwrap();
        screen.close();
    }

    #[test]
    fn a_heartbeat_keeps_the_link_while_no_picture_goes() {
        let written = Arc::new(Mutex::new(Vec::new()));
        let mut screen = opened(Made::new(&written)).unwrap();
        written.lock().unwrap().clear();
        screen
            .keeper
            .with(|kept| Link::heartbeat(&mut kept.inner))
            .unwrap();
        assert_eq!(
            written.lock().unwrap().clone(),
            [request(CMD_HEARTBEAT, &HEARTBEAT.to_le_bytes())]
        );
        screen.close();
    }
}
