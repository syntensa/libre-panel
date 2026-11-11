//! NZXT Kraken coolers with an LCD on the pump (devices/nzxt_kraken.py): the Kraken Z53, Z63 and
//! Z73 (1e71:3008, 320x320), the Kraken 2023 (300e, 240x240), the Elite RGB 2024 (3012, 640x640)
//! and the Plus 2024 (3014, 240x240). Only the screen's reports are written.
//!
//! - Control over the HID interface in reports of 64 bytes, an id and a sub-command first; the
//!   answer has the id + 1 and the same sub-command, and 1 at byte 14 where it was taken; the
//!   cooler's own status reports in between are passed by. 10 01 asks the firmware's version,
//!   30 01 the screen's state (its turn at byte 26); 30 02 01 <brightness> 00 00 01 <turn> sets
//!   the brightness; 38 01 02 00 gives the screen back to the liquid's temperature, 38 01 04
//!   <bucket> shows a bucket.
//! - A picture goes into one of two buckets in turn (36 03; 32 02 deleted; 32 01 set up; 36 01;
//!   on bulk endpoint 0x02 a header of 20 bytes by itself and the pixels as R G B 0; 36 02;
//!   38 01 04). A Kraken 2023 with firmware 2 takes a picture without buckets, as RGB565 (big
//!   endian), the first one twice.
//! - On Linux the HID interface stays with the kernel: the reports go through its hidraw node.

use std::time::{Duration, Instant};

use lp_pixels::{Filter, Image, Mode, Transpose};

use crate::base::{tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::models::{PanelModel, MODELS};
use crate::usb_link::{self, HidReports, UsbError, UsbIo, UsbResult};

pub const PROTOCOL: &str = "usb-nzxt";
pub const VENDOR_ID: u16 = 0x1E71;
pub const Z3: u16 = 0x3008;
pub const KRAKEN_2023: u16 = 0x300E;
pub const PRODUCTS: [u16; 4] = [Z3, KRAKEN_2023, 0x3012, 0x3014];
pub const REPORT: usize = 64;
const BULK_INTERFACE: u8 = 0;
const BULK_OUT: u8 = 0x02;
const BIG_CHUNK: usize = 2 * 1024 * 1024;
const MAGIC: [u8; 12] = [
    0x12, 0xFA, 0x01, 0xE8, 0xAB, 0xCD, 0xEF, 0x98, 0x76, 0x54, 0x32, 0x10,
];
const RGBA: u8 = 0x02;
const RGB16: u8 = 0x06;
const BUCKETS: [u8; 2] = [0, 1];
const MEMORY_KIB: usize = 24320;
const TAKEN: u8 = 0x01;
const ANSWER_S: f64 = 3.0;
const WRITE_TIMEOUT_MS: u64 = 5000;
/// HidReports.write's own time where nzxt_kraken.py names none
const REPORT_TIMEOUT_MS: u64 = 3000;

fn chunk(product: u16) -> usize {
    if product == Z3 {
        512
    } else {
        BIG_CHUNK
    }
}

pub fn models() -> Vec<&'static PanelModel> {
    MODELS.iter().filter(|m| m.protocol == PROTOCOL).collect()
}

pub fn model_for(product: u16) -> Option<&'static PanelModel> {
    models()
        .into_iter()
        .find(|m| m.usb_ids.contains(&(VENDOR_ID, product)))
}

/// present_model: a Kraken with an LCD on the bus (none opened).
pub fn present_model() -> Option<&'static PanelModel> {
    model_for(usb_link::present(VENDOR_ID, &PRODUCTS)?)
}

pub fn header(kind: u8, length: usize) -> Vec<u8> {
    let mut out = MAGIC.to_vec();
    out.extend_from_slice(&[kind, 0, 0, 0]);
    out.extend_from_slice(&(length as u32).to_le_bytes());
    out
}

fn rgb(picture: &Image) -> Result<std::borrow::Cow<'_, Image>> {
    Ok(if picture.mode() == Mode::RGB {
        std::borrow::Cow::Borrowed(picture)
    } else {
        std::borrow::Cow::Owned(picture.convert(Mode::RGB)?)
    })
}

/// rgba: the pixels as R G B 0.
pub fn rgba(picture: &Image) -> Result<Vec<u8>> {
    let picture = rgb(picture)?;
    let mut out = Vec::with_capacity(picture.width() * picture.height() * 4);
    for pixel in picture.as_bytes().chunks_exact(3) {
        out.extend_from_slice(&[pixel[0], pixel[1], pixel[2], 0]);
    }
    Ok(out)
}

pub fn rgb565_big(picture: &Image) -> Result<Vec<u8>> {
    let picture = rgb(picture)?;
    let mut out = Vec::with_capacity(picture.width() * picture.height() * 2);
    for pixel in picture.as_bytes().chunks_exact(3) {
        let (r, g, b) = (
            u16::from(pixel[0]),
            u16::from(pixel[1]),
            u16::from(pixel[2]),
        );
        out.extend_from_slice(&(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)).to_be_bytes());
    }
    Ok(out)
}

/// turned: `picture` turned clockwise by `quarters`.
pub fn turned(picture: &Image, quarters: u8) -> Image {
    match quarters % 4 {
        1 => picture.transpose(Transpose::Rotate270),
        2 => picture.transpose(Transpose::Rotate180),
        3 => picture.transpose(Transpose::Rotate90),
        _ => picture.clone(),
    }
}

/// The HID interface's reports through its hidraw node (Linux).
#[cfg(target_os = "linux")]
pub struct Hidraw {
    file: std::fs::File,
}

#[cfg(target_os = "linux")]
impl Hidraw {
    pub fn open(path: &std::path::Path) -> Result<Hidraw> {
        use std::os::unix::fs::OpenOptionsExt;
        std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_NONBLOCK)
            .open(path)
            .map(|file| Hidraw { file })
            .map_err(|e| {
                let error = match e.kind() {
                    std::io::ErrorKind::PermissionDenied => UsbError::Denied,
                    _ if e.raw_os_error() == Some(libc::EBUSY) => UsbError::Busy,
                    _ => UsbError::Other(e.to_string()),
                };
                DeviceError(
                    usb_link::access_hint(&error)
                        .unwrap_or_else(|| format!("cannot open {}: {e}", path.display())),
                )
            })
    }

    fn write(&mut self, report: &[u8]) -> UsbResult<()> {
        use std::io::Write;
        let mut data = report.to_vec();
        data.resize(data.len().max(REPORT), 0);
        self.file
            .write_all(&data)
            .map_err(|e| UsbError::Other(e.to_string()))
    }

    fn read(&mut self, timeout_ms: u64) -> UsbResult<Option<Vec<u8>>> {
        use std::io::Read;
        use std::os::fd::AsRawFd;
        let mut poll = libc::pollfd {
            fd: self.file.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        let timeout = i32::try_from(timeout_ms).unwrap_or(i32::MAX);
        // SAFETY: one pollfd of an open file, for the time given.
        let ready = unsafe { libc::poll(&mut poll, 1, timeout) };
        if ready <= 0 {
            return Ok(None);
        }
        let mut data = vec![0u8; REPORT];
        match self.file.read(&mut data) {
            Ok(0) => Ok(None),
            Ok(n) => {
                data.truncate(n);
                Ok(Some(data))
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => Ok(None),
            Err(e) => Err(UsbError::Other(e.to_string())),
        }
    }
}

/// hidraw_node: the hidraw node of the HID interface of the device at `bus` and `address`,
/// from sysfs.
#[cfg(target_os = "linux")]
pub fn hidraw_node(bus: &str, address: u8, root: &std::path::Path) -> Option<std::path::PathBuf> {
    let read = |path: std::path::PathBuf| std::fs::read_to_string(path).ok();
    let sorted = |dir: &std::path::Path, keep: &dyn Fn(&str) -> bool| -> Vec<std::path::PathBuf> {
        let mut found: Vec<std::path::PathBuf> = std::fs::read_dir(dir)
            .into_iter()
            .flatten()
            .flatten()
            .map(|entry| entry.path())
            .filter(|path| path.file_name().and_then(|n| n.to_str()).is_some_and(keep))
            .collect();
        found.sort();
        found
    };
    for folder in sorted(&root.join("sys/bus/usb/devices"), &|_| true) {
        let (Some(busnum), Some(devnum)) =
            (read(folder.join("busnum")), read(folder.join("devnum")))
        else {
            continue;
        };
        if (busnum.trim(), devnum.trim()) != (bus, address.to_string().as_str()) {
            continue;
        }
        let name = folder.file_name()?.to_str()?.to_string();
        let prefix = format!("{name}:");
        for interface in sorted(&folder, &|n| n.starts_with(&prefix)) {
            if read(interface.join("bInterfaceClass")).is_none_or(|class| class.trim() != "03") {
                continue;
            }
            for child in sorted(&interface, &|_| true) {
                let nodes = sorted(&child.join("hidraw"), &|n| n.starts_with("hidraw"));
                if let Some(node) = nodes.first() {
                    return Some(root.join("dev").join(node.file_name()?));
                }
            }
        }
    }
    None
}

/// The way to the HID interface: its hidraw node (Linux) or the interface itself.
enum Control {
    Hid(HidReports),
    #[cfg(target_os = "linux")]
    Raw(Hidraw),
}

impl Control {
    fn write(&mut self, io: &mut dyn UsbIo, report: &[u8], timeout_ms: u64) -> UsbResult<()> {
        match self {
            Control::Hid(reports) => reports.write(io, report, timeout_ms),
            #[cfg(target_os = "linux")]
            Control::Raw(raw) => raw.write(report),
        }
    }

    fn read(&mut self, io: &mut dyn UsbIo, timeout_ms: u64) -> UsbResult<Option<Vec<u8>>> {
        match self {
            Control::Hid(reports) => reports.read(io, timeout_ms),
            #[cfg(target_os = "linux")]
            Control::Raw(raw) => raw.read(timeout_ms),
        }
    }
}

/// What failed: the link (a USB or hidraw error) or the cooler (a DeviceError).
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

/// An answer's byte `i`; an answer too short fails as Python's IndexError says.
fn at(answer: &[u8], i: usize) -> std::result::Result<u8, Fault> {
    answer
        .get(i)
        .copied()
        .ok_or_else(|| Fault::Device(DeviceError("index out of range".into())))
}

/// A Kraken's LCD (KrakenDisplay).
pub struct Kraken {
    pub model: Option<&'static PanelModel>,
    io: Option<Box<dyn UsbIo>>,
    product: u16,
    control: Option<Control>,
    claimed: Vec<u8>,
    pub firmware: (u8, u8, u8),
    turn: u8,
    next: usize,
    first: bool,
    brightness: i64,
    counters: Counters,
    /// the reports through the kernel's hidraw node (Linux), else the HID interface itself
    pub hidraw: bool,
    /// the cooler's pauses after some commands (the tests leave them out)
    pub pauses: bool,
}

impl Kraken {
    /// `brightness`: [device] brightness, set as the screen opens.
    pub fn new(model: Option<&'static PanelModel>, brightness: i64) -> Kraken {
        Kraken {
            model,
            io: None,
            product: 0,
            control: None,
            claimed: Vec::new(),
            firmware: (0, 0, 0),
            turn: 0,
            next: 0,
            first: true,
            brightness,
            counters: Counters::default(),
            hidraw: cfg!(target_os = "linux"),
            pauses: true,
        }
    }

    /// Whether the pictures go without buckets (a Kraken 2023 with firmware 2).
    fn direct(&self) -> bool {
        self.product == KRAKEN_2023 && self.firmware.0 == 2
    }

    fn pause(&self, seconds: f64) {
        if self.pauses {
            std::thread::sleep(Duration::from_secs_f64(seconds));
        }
    }

    pub fn open(&mut self) -> Result<()> {
        let own: Vec<u16> = self
            .model
            .map_or(Vec::new(), |m| m.usb_ids.iter().map(|i| i.1).collect());
        let products: Vec<u16> = own
            .iter()
            .copied()
            .chain(PRODUCTS.iter().copied().filter(|p| !own.contains(p)))
            .collect();
        let (io, product) = usb_link::take(VENDOR_ID, &products, &self.describe())?;
        let location = (io.bus.clone(), io.address);
        self.open_on(Box::new(io), product, location)
    }

    /// open on `io`, a cooler of `product` at `location` (bus, address) taken for this panel.
    pub fn open_on(
        &mut self,
        io: Box<dyn UsbIo>,
        product: u16,
        location: (String, u8),
    ) -> Result<()> {
        self.product = product;
        self.model = model_for(product);
        self.io = Some(io);
        match self.start(&location) {
            Ok(()) => {
                self.next = 0;
                self.first = true;
                Ok(())
            }
            Err(Fault::Device(e)) => {
                self.close();
                Err(e)
            }
            Err(Fault::Usb(e)) => {
                self.close();
                self.counters.usb_errors += 1;
                Err(DeviceError(tf(
                    "the {panel} did not start: {error}",
                    &[("panel", &self.describe()), ("error", &e.to_string())],
                )))
            }
        }
    }

    fn start(&mut self, location: &(String, u8)) -> std::result::Result<(), Fault> {
        let io = self.io.as_mut().expect("just put there").as_mut();
        let mut hid = Vec::new();
        let control = if self.hidraw {
            #[cfg(target_os = "linux")]
            {
                let Some(node) = hidraw_node(&location.0, location.1, std::path::Path::new("/"))
                else {
                    return Err(DeviceError(
                        "the Kraken's HID interface has no hidraw node (is usbhid loaded?)".into(),
                    )
                    .into());
                };
                Control::Raw(Hidraw::open(&node)?)
            }
            #[cfg(not(target_os = "linux"))]
            {
                let _ = location;
                return Err(DeviceError("no hidraw here".into()).into());
            }
        } else {
            let Some(interface) = usb_link::hid_interface(io) else {
                return Err(DeviceError("the Kraken has no HID interface".into()).into());
            };
            usb_link::claim(io, &[interface])?;
            hid.push(interface);
            Control::Hid(HidReports::new(io, interface, REPORT, true))
        };
        self.control = Some(control);
        usb_link::claim(io, &[BULK_INTERFACE])?;
        self.claimed = std::iter::once(BULK_INTERFACE).chain(hid).collect();
        self.drain()?;
        let version = self.ask(&[0x10, 0x01])?;
        self.firmware = (at(&version, 17)?, at(&version, 18)?, at(&version, 19)?);
        self.turn = at(&self.ask(&[0x30, 0x01])?, 26)? & 3;
        self.set_level(self.brightness)?;
        if !self.direct() {
            self.ask(&[0x38, 0x01, 0x02, 0x00])?;
            self.pause(0.2);
            for bucket in BUCKETS {
                self.ask(&[0x32, 0x02, bucket])?;
            }
        }
        Ok(())
    }

    fn parts(&mut self) -> (&mut dyn UsbIo, &mut Control) {
        (
            self.io.as_mut().expect("open").as_mut(),
            self.control.as_mut().expect("open"),
        )
    }

    fn drain(&mut self) -> UsbResult<()> {
        let (io, control) = self.parts();
        for _ in 0..64 {
            if control.read(io, 1)?.is_none() {
                break;
            }
        }
        Ok(())
    }

    /// _ask: `report` written, and its answer (the id + 1, the same sub-command).
    fn ask(&mut self, report: &[u8]) -> std::result::Result<Vec<u8>, Fault> {
        let label = self.describe();
        let (io, control) = self.parts();
        control.write(io, report, WRITE_TIMEOUT_MS)?;
        let deadline = Instant::now() + Duration::from_secs_f64(ANSWER_S);
        loop {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            let wait = (left.as_secs_f64() * 1000.0) as u64;
            let answer = control.read(io, wait.max(1))?;
            if let Some(answer) = answer {
                if answer.len() > 1
                    && answer[0] == report[0].wrapping_add(1)
                    && answer[1] == report[1]
                {
                    return Ok(answer);
                }
            }
        }
        let asked = report
            .iter()
            .take(2)
            .map(|b| format!("{b:02x}"))
            .collect::<Vec<_>>()
            .join(" ");
        Err(DeviceError(tf(
            "the {panel} stopped answering: {error}",
            &[
                ("panel", &label),
                ("error", &format!("no answer to {asked}")),
            ],
        ))
        .into())
    }

    /// _exchange: `report` written and the next report read, whatever it is.
    fn exchange(&mut self, report: &[u8]) -> UsbResult<()> {
        let (io, control) = self.parts();
        control.write(io, report, WRITE_TIMEOUT_MS)?;
        control.read(io, 1000)?;
        Ok(())
    }

    /// _taken: `report` asked; a refusal goes on (liquidctl logs it and goes on).
    fn taken(&mut self, report: &[u8]) -> std::result::Result<(), Fault> {
        // (a refusal, byte 14 not TAKEN, is logged by liquidctl, which goes on)
        at(&self.ask(report)?, 14)?;
        Ok(())
    }

    fn set_level(&mut self, percent: i64) -> UsbResult<()> {
        let level = percent.clamp(0, 100) as u8;
        let turn = self.turn;
        let (io, control) = self.parts();
        control.write(
            io,
            &[0x30, 0x02, 0x01, level, 0, 0, 0x01, turn],
            REPORT_TIMEOUT_MS,
        )?;
        self.pause(0.1);
        Ok(())
    }

    fn bulk(&mut self, data: &[u8]) -> UsbResult<()> {
        let size = chunk(self.product);
        let io = self.io.as_mut().expect("open");
        for part in data.chunks(size) {
            io.write(BULK_OUT, part, WRITE_TIMEOUT_MS)?;
        }
        Ok(())
    }

    fn bucket_picture(&mut self, pixels: &[u8]) -> std::result::Result<(), Fault> {
        let bucket = BUCKETS[self.next];
        let slot = MEMORY_KIB / BUCKETS.len();
        let start = self.next * slot;
        let size = (20 + pixels.len()).div_ceil(1024);
        self.exchange(&[0x36, 0x03])?;
        for _ in 0..2 {
            if at(&self.ask(&[0x32, 0x02, bucket])?, 14)? != TAKEN {
                break;
            }
        }
        self.taken(&[
            0x32,
            0x01,
            bucket,
            bucket + 1,
            (start & 0xFF) as u8,
            (start >> 8) as u8,
            (size & 0xFF) as u8,
            (size >> 8) as u8,
            0x01,
        ])?;
        self.exchange(&[0x36, 0x01, bucket])?;
        let head = header(RGBA, pixels.len());
        self.io
            .as_mut()
            .expect("open")
            .write(BULK_OUT, &head, WRITE_TIMEOUT_MS)?;
        self.bulk(pixels)?;
        {
            let (io, control) = self.parts();
            control.write(io, &[0x36, 0x02], REPORT_TIMEOUT_MS)?;
        }
        self.pause(0.05);
        self.taken(&[0x38, 0x01, 0x04, bucket])?;
        self.next = (self.next + 1) % BUCKETS.len();
        self.counters.bytes += (20 + pixels.len()) as u64;
        Ok(())
    }

    fn direct_picture(&mut self, pixels: &[u8]) -> UsbResult<()> {
        let times = if self.first { 2 } else { 1 };
        for _ in 0..times {
            self.exchange(&[0x36, 0x01, 0x00, 0x01, 0x06])?;
            let head = header(RGB16, pixels.len());
            self.io
                .as_mut()
                .expect("open")
                .write(BULK_OUT, &head, WRITE_TIMEOUT_MS)?;
            self.bulk(pixels)?;
            self.exchange(&[0x36, 0x02])?;
            self.counters.bytes += (20 + pixels.len()) as u64;
        }
        self.first = false;
        Ok(())
    }

    fn stopped(&mut self, fault: Fault) -> DeviceError {
        match fault {
            Fault::Device(e) => e,
            Fault::Usb(e) => {
                self.counters.usb_errors += 1;
                DeviceError(tf(
                    "the {panel} stopped answering: {error}",
                    &[("panel", &self.describe()), ("error", &e.to_string())],
                ))
            }
        }
    }
}

impl Driver for Kraken {
    fn model(&self) -> Option<&'static PanelModel> {
        self.model
    }

    fn describe(&self) -> String {
        self.model.map_or("NZXT Kraken".into(), |m| m.label())
    }

    fn show(&mut self, frame: &Image, _region: Option<Region>) -> Result<()> {
        let Some(model) = self.model.filter(|_| self.io.is_some()) else {
            return Err(DeviceError(tf(
                "the {panel} is not open",
                &[("panel", &self.describe())],
            )));
        };
        let size = (model.native_width as usize, model.native_height as usize);
        let mut picture = rgb(frame)?.into_owned();
        if picture.size() != size {
            picture = picture.resize(size.0, size.1, Filter::Lanczos, None, None)?;
        }
        let picture = turned(&picture, self.turn);
        let sent = if self.direct() {
            let pixels = rgb565_big(&picture)?;
            self.direct_picture(&pixels).map_err(Fault::Usb)
        } else {
            let pixels = rgba(&picture)?;
            self.bucket_picture(&pixels)
        };
        sent.map_err(|fault| self.stopped(fault))
    }

    fn set_brightness(&mut self, percent: i64) -> Result<()> {
        if self.io.is_some() {
            if let Err(e) = self.set_level(percent) {
                return Err(self.stopped(Fault::Usb(e)));
            }
        }
        Ok(())
    }

    fn counters(&self) -> Counters {
        self.counters
    }

    fn screen_off(&mut self) -> bool {
        self.io.is_some() && self.set_level(0).is_ok()
    }

    fn close(&mut self) {
        if self.io.is_some() && self.control.is_some() && !self.claimed.is_empty() {
            // the screen back to the firmware's liquid temperature
            let (io, control) = self.parts();
            let _ = control.write(io, &[0x38, 0x01, 0x02, 0x00], REPORT_TIMEOUT_MS);
        }
        self.control = None;
        if let Some(mut io) = self.io.take() {
            io.release();
        }
        self.claimed.clear();
    }
}

impl Drop for Kraken {
    fn drop(&mut self) {
        self.close();
    }
}
