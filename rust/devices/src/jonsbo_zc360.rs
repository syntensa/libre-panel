//! JONSBO ZC-360 (devices/jonsbo_zc360.py): three 3.38" screens of 640x180, one under each fan,
//! each a USB device of its own (43a8:0e61) with a serial number of its own; driven as one panel
//! of 1920x180, the screens side by side, or each as a panel of its own (`jonsbo-zc360-screen`).
//!
//! - A command is 32 bytes: AA 55, the command and its parameter and six zero bytes in DES (ECB,
//!   a fixed key), 0x01 at byte 10 and BB at the end; the answer (32 bytes from 0x82) has its
//!   state in byte 2 of the part decrypted the same way.
//! - 56 starts the controller, 84 sets the brightness, 86 the rotation. A controller just
//!   powered answers its first frame with 0x60 and wants 88 then; every frame after answers
//!   0x62.
//! - A frame is the screen's framebuffer, 180x640 BGR, in 720 records of a 32-byte header and
//!   480 bytes of pixels, eight records a bulk write of 4096 bytes, then the answer is read.
//! - The screens are written one after the other (here a lock of the process: the Rust app's
//!   panels are threads of it); a screen whose part did not change is not written again.

use std::sync::Mutex;
use std::time::Duration;

use des::cipher::{BlockDecrypt, BlockEncrypt, KeyInit};
use lp_pixels::{Filter, Image, Mode, Transpose};

use crate::base::{leave_place, place_taken, take_place, tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::models::{find_model, PanelModel};
use crate::usb_link::{UsbError, UsbIo};

pub const VENDOR_ID: u16 = 0x43A8;
pub const PRODUCT_ID: u16 = 0x0E61;
pub const MODEL_ID: &str = "jonsbo-zc360";
pub const SCREEN_MODEL_ID: &str = "jonsbo-zc360-screen";
pub const SCREENS: usize = 3;
pub const WIDTH: usize = 640;
pub const HEIGHT: usize = 180;
const ENDPOINT_OUT: u8 = 0x02;
const ENDPOINT_IN: u8 = 0x82;
const DES_KEY: [u8; 8] = [65, 95, 217, 250, 19, 66, 88, 183];
const COMMAND: u8 = 0x01;
const FRAME: u8 = 0x02;
const CMD_INIT: u8 = 56;
const CMD_BRIGHTNESS: u8 = 84;
const CMD_ROTATION: u8 = 86;
const CMD_COMMIT: u8 = 88;
const FIRST_FRAME: u8 = 0x60;
const SHOWING: u8 = 0x62;
const COMMIT_SETTLE_S: f64 = 0.3;
const CLAIM_SETTLE_S: f64 = 0.3;
const ANSWER_GAP_S: f64 = 0.001;
const PAYLOAD: usize = 480;
const RECORDS: usize = WIDTH * HEIGHT * 3 / PAYLOAD;
const PER_WRITE: usize = 8;
const WRITE_TIMEOUT_MS: u64 = 3000;
const READ_TIMEOUT_MS: u64 = 100;

/// one screen written at a time
static TURN: Mutex<()> = Mutex::new(());

fn cipher() -> des::Des {
    des::Des::new(&DES_KEY.into())
}

pub fn command_packet(command: u8, parameter: u8) -> Vec<u8> {
    let mut packet = vec![0u8; 32];
    packet[0..2].copy_from_slice(&[0xaa, 0x55]);
    let mut block = [command, parameter, 0, 0, 0, 0, 0, 0].into();
    cipher().encrypt_block(&mut block);
    packet[2..10].copy_from_slice(&block);
    packet[10] = COMMAND;
    packet[31] = 0xBB;
    packet
}

/// answer_state: the state a 32-byte answer says; None for no answer.
pub fn answer_state(answer: &[u8]) -> Option<u8> {
    if answer.len() != 32 {
        return None;
    }
    let mut block: [u8; 8] = answer[2..10].try_into().ok()?;
    let mut cell = block.into();
    cipher().decrypt_block(&mut cell);
    block.copy_from_slice(&cell);
    Some(block[0])
}

fn record_header(number: usize) -> [u8; 32] {
    let mut header = [0u8; 32];
    header[0..2].copy_from_slice(&[0xaa, 0x55]);
    header[10] = FRAME;
    header[11..13].copy_from_slice(&(number as u16).to_be_bytes());
    header[31] = 0xBB;
    header
}

/// frame_writes: the 90 bulk writes of one screen's landscape picture (640x180).
pub fn frame_writes(screen: &Image) -> Result<Vec<Vec<u8>>> {
    let mut screen = if screen.size() != (WIDTH, HEIGHT) {
        screen.resize(WIDTH, HEIGHT, Filter::Bicubic, None, None)?
    } else {
        screen.clone()
    };
    if screen.mode() != Mode::RGB {
        screen = screen.convert(Mode::RGB)?;
    }
    let native = screen.transpose(Transpose::Rotate270);
    let bgr: Vec<u8> = native
        .as_bytes()
        .chunks_exact(3)
        .flat_map(|p| [p[2], p[1], p[0]])
        .collect();
    let mut writes = Vec::with_capacity(RECORDS / PER_WRITE);
    for first in (0..RECORDS).step_by(PER_WRITE) {
        let mut write = Vec::with_capacity(PER_WRITE * (32 + PAYLOAD));
        for record in first..first + PER_WRITE {
            write.extend_from_slice(&record_header(record + 1));
            write.extend_from_slice(&bgr[record * PAYLOAD..(record + 1) * PAYLOAD]);
        }
        writes.push(write);
    }
    Ok(writes)
}

/// screen_order: which screen shows each third of the picture, from the left: `device.port`
/// as "2,1,3"; in their order where it says nothing usable.
pub fn screen_order(port: &str) -> Vec<usize> {
    let digits: Vec<i64> = port
        .chars()
        .filter_map(|c| lp_py::decimal(c).map(|d| i64::from(d) - 1))
        .collect();
    let mut sorted = digits.clone();
    sorted.sort_unstable();
    if sorted == [0, 1, 2] {
        return digits.into_iter().map(|d| d as usize).collect();
    }
    (0..SCREENS).collect()
}

/// screen_number: which screen a panel of one of them shows on (from 0): `device.port` "1" to
/// "3"; None for the first free one.
pub fn screen_number(port: &str) -> Option<usize> {
    match port.trim() {
        "1" => Some(0),
        "2" => Some(1),
        "3" => Some(2),
        _ => None,
    }
}

/// A screen on the bus, not opened: where it is and its serial number.
pub struct Candidate {
    pub io: Box<dyn UsbIo>,
    pub serial: String,
    pub place: String,
    /// its bus, the ports on the way and its address (the order of the screens alone)
    pub position: (String, Vec<u8>, u8),
}

/// The ZC-360's screens on the bus, opened (not claimed).
fn candidates() -> Result<Vec<Candidate>> {
    let found = crate::usb_link::find(VENDOR_ID, &[PRODUCT_ID])?;
    let mut out = Vec::new();
    for device in found {
        let place = device.place();
        let serial = device.info.serial_number().unwrap_or("").to_string();
        let ports = device.info.port_chain().to_vec();
        let address = device.info.device_address();
        let bus = place
            .trim_start_matches("usb:")
            .rsplit_once('.')
            .map_or(String::new(), |(bus, _)| bus.to_string());
        // (opened without its place: the panel takes it, and leaves it, itself)
        if let Ok(io) = crate::usb_link::NusbIo::open_found(&device, String::new()) {
            out.push(Candidate {
                io: Box::new(io),
                serial,
                place,
                position: (bus, ports, address),
            });
        }
    }
    Ok(out)
}

/// present: whether a ZC-360 is on the bus (none of its screens is opened).
pub fn present() -> bool {
    crate::usb_link::find(VENDOR_ID, &[PRODUCT_ID]).is_ok_and(|found| !found.is_empty())
}

/// all_free: a ZC-360 on the bus with its three screens free.
pub fn all_free() -> bool {
    crate::usb_link::find(VENDOR_ID, &[PRODUCT_ID])
        .is_ok_and(|found| found.iter().filter(|d| !place_taken(&d.place())).count() >= SCREENS)
}

/// One of the three, claimed from open to close.
struct Screen {
    io: Box<dyn UsbIo>,
    place: String,
    /// the last picture it showed (unchanged: not again)
    sent: Option<Vec<u8>>,
}

impl Screen {
    fn claim(&mut self) -> Result<()> {
        self.io.claim(&[0]).map_err(|e| {
            DeviceError(tf(
                "cannot open the ZC-360 screen: {error}",
                &[("error", &e.to_string())],
            ))
        })
    }

    fn command(
        &mut self,
        command: u8,
        parameter: u8,
        pauses: bool,
    ) -> std::result::Result<Option<u8>, UsbError> {
        self.io.write(
            ENDPOINT_OUT,
            &command_packet(command, parameter),
            WRITE_TIMEOUT_MS,
        )?;
        Ok(self.answer(pauses))
    }

    fn answer(&mut self, pauses: bool) -> Option<u8> {
        if pauses {
            std::thread::sleep(Duration::from_secs_f64(ANSWER_GAP_S));
        }
        self.io
            .read(ENDPOINT_IN, 32, READ_TIMEOUT_MS)
            .ok()
            .and_then(|answer| answer_state(&answer))
    }

    /// Its picture where it changed: the bytes sent.
    fn show(&mut self, picture: &Image, pauses: bool) -> std::result::Result<usize, Fault> {
        let writes = frame_writes(picture)?;
        let body: Vec<u8> = writes.concat();
        if self.sent.as_ref() == Some(&body) {
            return Ok(0);
        }
        let state = {
            let _turn = TURN.lock().unwrap_or_else(|e| e.into_inner());
            for chunk in &writes {
                self.io.write(ENDPOINT_OUT, chunk, WRITE_TIMEOUT_MS)?;
            }
            self.answer(pauses)
        };
        let length = body.len();
        self.sent = (state == Some(SHOWING)).then_some(body);
        if state == Some(FIRST_FRAME) {
            self.command(CMD_COMMIT, 0, pauses)?;
            if pauses {
                std::thread::sleep(Duration::from_secs_f64(COMMIT_SETTLE_S));
            }
        }
        Ok(length)
    }
}

enum Fault {
    Usb(UsbError),
    Device(DeviceError),
}

impl From<UsbError> for Fault {
    fn from(e: UsbError) -> Fault {
        Fault::Usb(e)
    }
}

impl From<DeviceError> for Fault {
    fn from(e: DeviceError) -> Fault {
        Fault::Device(e)
    }
}

/// The pictures "Which is which?" shows on the three, while it shows (the app's numbers).
pub type Numbers = Box<dyn Fn((usize, usize)) -> Option<Vec<Image>> + Send>;

/// The ZC-360, or one of its screens (Zc360Display).
pub struct Zc360 {
    pub model: &'static PanelModel,
    screens: Vec<Screen>,
    single: bool,
    which: Option<usize>,
    order: Vec<usize>,
    brightness: i64,
    counters: Counters,
    /// each screen its number while "Which is which?" shows
    pub numbers: Option<Numbers>,
    /// the controllers' pauses (the tests leave them out)
    pub pauses: bool,
}

impl Zc360 {
    pub fn new(model: Option<&'static PanelModel>, port: &str, brightness: i64) -> Zc360 {
        let model = model
            .or_else(|| find_model(MODEL_ID))
            .expect("the ZC-360 in the catalog");
        let single = model.id == SCREEN_MODEL_ID;
        Zc360 {
            model,
            screens: Vec::new(),
            single,
            which: if single { screen_number(port) } else { None },
            order: if single { vec![0] } else { screen_order(port) },
            brightness,
            counters: Counters::default(),
            numbers: None,
            pauses: true,
        }
    }

    pub fn open(&mut self) -> Result<()> {
        let found = candidates()?;
        self.open_among(found)
    }

    /// open among the screens `found` on the bus.
    pub fn open_among(&mut self, found: Vec<Candidate>) -> Result<()> {
        let chosen = if self.single {
            self.one_of(found)?
        } else {
            let mut free: Vec<Candidate> = found
                .into_iter()
                .filter(|c| !place_taken(&c.place))
                .collect();
            if free.len() < SCREENS {
                let count = free.len().to_string();
                return Err(DeviceError(tf(
                    "the ZC-360 has {count} of its 3 screens on the USB bus",
                    &[("count", &count)],
                )));
            }
            free.sort_by(|a, b| (&a.serial, &a.place).cmp(&(&b.serial, &b.place)));
            free.truncate(SCREENS);
            free
        };
        let mut screens: Vec<Screen> = chosen
            .into_iter()
            .map(|c| Screen {
                io: c.io,
                place: c.place,
                sent: None,
            })
            .collect();
        let mut taken = 0;
        let started = (|| -> Result<()> {
            for screen in &mut screens {
                if !take_place(&screen.place) {
                    return Err(DeviceError(lp_i18n::t(
                        "another panel has a screen of the ZC-360 open",
                    )));
                }
                taken += 1;
                screen.claim()?;
            }
            if self.pauses {
                std::thread::sleep(Duration::from_secs_f64(CLAIM_SETTLE_S));
            }
            let level = self.brightness.clamp(0, 100) as u8;
            for screen in &mut screens {
                for (command, parameter) in
                    [(CMD_INIT, 0), (CMD_BRIGHTNESS, level), (CMD_ROTATION, 0)]
                {
                    if let Err(e) = screen.command(command, parameter, self.pauses) {
                        self.counters.usb_errors += 1;
                        return Err(DeviceError(tf(
                            "the ZC-360 stopped answering: {error}",
                            &[("error", &e.to_string())],
                        )));
                    }
                }
            }
            Ok(())
        })();
        if let Err(e) = started {
            for screen in screens.iter_mut().take(taken) {
                screen.io.release();
                leave_place(&screen.place);
            }
            return Err(e);
        }
        self.screens = screens;
        Ok(())
    }

    /// One screen, a panel of its own: the one `device.port` names, else the first free.
    fn one_of(&self, mut found: Vec<Candidate>) -> Result<Vec<Candidate>> {
        found.sort_by(|a, b| a.position.cmp(&b.position));
        let index = match self.which {
            Some(which) => {
                let n = (which + 1).to_string();
                if which >= found.len() {
                    return Err(DeviceError(tf(
                        "the ZC-360's screen {n} is not on the USB bus",
                        &[("n", &n)],
                    )));
                }
                if place_taken(&found[which].place) {
                    return Err(DeviceError(tf(
                        "another panel has the ZC-360's screen {n} open",
                        &[("n", &n)],
                    )));
                }
                which
            }
            None => match found.iter().position(|c| !place_taken(&c.place)) {
                Some(index) => index,
                None => {
                    return Err(DeviceError(lp_i18n::t(
                        "no screen of the ZC-360 is free on the USB bus",
                    )));
                }
            },
        };
        Ok(vec![found.swap_remove(index)])
    }

    fn sent(&mut self, fault: Fault) -> DeviceError {
        match fault {
            Fault::Device(e) => e,
            Fault::Usb(e) => {
                self.counters.usb_errors += 1;
                DeviceError(tf(
                    "the ZC-360 stopped answering: {error}",
                    &[("error", &e.to_string())],
                ))
            }
        }
    }
}

impl Driver for Zc360 {
    fn model(&self) -> Option<&'static PanelModel> {
        Some(self.model)
    }

    fn describe(&self) -> String {
        self.model.label()
    }

    fn show(&mut self, frame: &Image, _region: Option<Region>) -> Result<()> {
        if self.screens.is_empty() {
            return Err(DeviceError(lp_i18n::t("the ZC-360 is not open")));
        }
        let mut frame = if frame.width() < frame.height() {
            frame.transpose(Transpose::Rotate270)
        } else {
            frame.clone()
        };
        let pauses = self.pauses;
        if self.single {
            let shown = self.screens[0].show(&frame, pauses);
            return match shown {
                Ok(bytes) => {
                    self.counters.bytes += bytes as u64;
                    Ok(())
                }
                Err(fault) => Err(self.sent(fault)),
            };
        }
        if frame.size() != (WIDTH * SCREENS, HEIGHT) {
            frame = frame.resize(WIDTH * SCREENS, HEIGHT, Filter::Bicubic, None, None)?;
        }
        let numbered = self
            .numbers
            .as_ref()
            .and_then(|numbers| numbers((WIDTH, HEIGHT)));
        let mut parts: Vec<(usize, Image)> = match numbered {
            Some(pictures) => pictures.into_iter().enumerate().take(SCREENS).collect(),
            None => self
                .order
                .iter()
                .enumerate()
                .map(|(third, screen)| {
                    let x = (third * WIDTH) as i64;
                    (*screen, frame.crop(x, 0, x + WIDTH as i64, HEIGHT as i64))
                })
                .collect(),
        };
        parts.sort_by_key(|(index, _)| *index);
        for (index, picture) in parts {
            match self.screens[index].show(&picture, pauses) {
                Ok(bytes) => self.counters.bytes += bytes as u64,
                Err(fault) => return Err(self.sent(fault)),
            }
        }
        Ok(())
    }

    fn set_brightness(&mut self, percent: i64) -> Result<()> {
        let level = percent.clamp(0, 100) as u8;
        let pauses = self.pauses;
        for n in 0..self.screens.len() {
            if let Err(e) = self.screens[n].command(CMD_BRIGHTNESS, level, pauses) {
                return Err(self.sent(Fault::Usb(e)));
            }
        }
        Ok(())
    }

    fn counters(&self) -> Counters {
        self.counters
    }

    fn screen_off(&mut self) -> bool {
        if self.screens.is_empty() {
            return false;
        }
        let _ = self.set_brightness(0);
        true
    }

    fn close(&mut self) {
        for mut screen in self.screens.drain(..) {
            screen.io.release();
            leave_place(&screen.place);
        }
    }
}

impl Drop for Zc360 {
    fn drop(&mut self) {
        self.close();
    }
}
