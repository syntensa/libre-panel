//! Thermalright's Trofeo Vision screens (devices/thermalright.py): the 6.86" of 1280x480 (USB
//! 0416:5302, a HID device) and the bars of 9.16" (1920x462) and 11.3" (1920x440) (0416:5408,
//! bulk transfers).
//!
//! - The 6.86" takes reports of 512 bytes: DA DB DC DD with 1 at byte 12 asks what it is; the
//!   answer has its kind at byte 5 and a variant at byte 4. A picture is a JPEG after a header
//!   of 20 bytes, 512 bytes a report; no answer comes.
//! - The bars: 2048 bytes, 02 FF with 1 at byte 8, ask what it is; the answer, 03 FF with 1 at
//!   byte 8, has the kind at byte 20 and the variant at byte 22. A picture is a JPEG in packets
//!   of 512 bytes (a header of 16 and 496 of the JPEG), filled up to a multiple of four, 4096
//!   bytes a write; the bar answers 03 FF with 1 at byte 8.
//! - Pictures turned as TRCC turns them, the JPEG 4:2:0, the brightness in the pixels; the last
//!   picture again every half second (a bar) or second (the 6.86").

use lp_pixels::{Image, Transpose};

use crate::base::{dimmed, tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::keep::Keeper;
use crate::models::{find_model, PanelModel, MODELS};
use crate::usb_link::{self, HidReports, UsbError, UsbIo};

pub const PROTOCOL: &str = "usb-thermalright";
pub const VENDOR_ID: u16 = 0x0416;
pub const HID_PRODUCT: u16 = 0x5302;
pub const BULK_PRODUCT: u16 = 0x5408;
pub const PRODUCTS: [u16; 2] = [BULK_PRODUCT, HID_PRODUCT];
const MAGIC: [u8; 4] = [0xDA, 0xDB, 0xDC, 0xDD];
pub const REPORT: usize = 512;
const HEADER: usize = 20;
const HELLO: usize = 2048;
pub const PACKET: usize = 512;
const PACKET_HEADER: usize = 16;
pub const PACKET_DATA: usize = PACKET - PACKET_HEADER;
const WRITE: usize = 4096;
pub const MAX_JPEG: usize = 230_000;
pub const QUALITIES: [i64; 5] = [90, 80, 70, 60, 50];
const WRITE_TIMEOUT_MS: u64 = 3000;
const ANSWER_TIMEOUT_MS: u64 = 1000;
const MISSED: u32 = 5;
const SIX_INCH: &str = "thermalright-trofeo-6.86";
const NINE_INCH: &str = "thermalright-trofeo-9.16";
const ELEVEN_INCH: &str = "thermalright-trofeo-11.3";

fn again_s(product: u16) -> f64 {
    if product == HID_PRODUCT {
        1.0
    } else {
        0.5
    }
}

fn hid_kind(kind: u8) -> Option<&'static str> {
    (kind == 0x80).then_some(SIX_INCH)
}

fn bulk_kind(kind: u32) -> Option<&'static str> {
    match kind {
        65 => Some(NINE_INCH),
        68 => Some(SIX_INCH),
        69 => Some(ELEVEN_INCH),
        _ => None,
    }
}

fn size_of(id: &str) -> (i64, i64) {
    match id {
        SIX_INCH => (1280, 480),
        NINE_INCH => (1920, 462),
        _ => (1920, 440),
    }
}

pub fn models() -> Vec<&'static PanelModel> {
    MODELS.iter().filter(|m| m.protocol == PROTOCOL).collect()
}

/// present_model: a Trofeo Vision on the bus (none opened): the model its USB id is first of.
pub fn present_model() -> Option<&'static PanelModel> {
    let product = usb_link::present(VENDOR_ID, &PRODUCTS)?;
    models()
        .into_iter()
        .find(|m| m.usb_ids.contains(&(VENDOR_ID, product)))
}

pub fn hid_hello() -> Vec<u8> {
    let mut report = vec![0u8; REPORT];
    report[0..4].copy_from_slice(&MAGIC);
    report[12] = 1;
    report
}

/// hid_answer: the kind and the variant the 6.86" says.
pub fn hid_answer(answer: &[u8]) -> Option<(u32, u32)> {
    (answer.len() > HEADER && answer[..4] == MAGIC && answer[12] == 1)
        .then(|| (u32::from(answer[5]), u32::from(answer[4])))
}

pub fn hid_reports(jpeg: &[u8], width: i64, height: i64) -> Vec<Vec<u8>> {
    let mut data = vec![0u8; HEADER];
    data[0..4].copy_from_slice(&MAGIC);
    data[4] = 2;
    data[8..10].copy_from_slice(&(width as u16).to_le_bytes());
    data[10..12].copy_from_slice(&(height as u16).to_le_bytes());
    data[12] = 2;
    data[16..20].copy_from_slice(&(jpeg.len() as u32).to_le_bytes());
    data.extend_from_slice(jpeg);
    data.chunks(REPORT)
        .map(|chunk| {
            let mut report = chunk.to_vec();
            report.resize(REPORT, 0);
            report
        })
        .collect()
}

pub fn bulk_hello() -> Vec<u8> {
    let mut hello = vec![0u8; HELLO];
    hello[0..2].copy_from_slice(&[0x02, 0xff]);
    hello[8] = 1;
    hello
}

/// bulk_answer: the kind and the variant a bar says, as usausa counts them.
pub fn bulk_answer(answer: &[u8]) -> Option<(u32, u32)> {
    (answer.len() > 22 && answer[0..2] == [0x03, 0xff] && answer[8] == 1).then(|| {
        let kind = if answer[20] <= 3 {
            65
        } else {
            64 + u32::from(answer[20])
        };
        (kind, u32::from(answer[22]) + 1)
    })
}

/// answered: whether a bar's answer to a picture says it took it.
pub fn answered(answer: Option<&[u8]>) -> bool {
    answer.is_some_and(|a| a.len() > 8 && a[0..2] == [0x03, 0xff] && a[8] == 1)
}

pub fn bulk_writes(jpeg: &[u8]) -> Vec<Vec<u8>> {
    let count = jpeg.len().div_ceil(PACKET_DATA).max(1);
    let mut data = Vec::with_capacity((count + 3) * PACKET);
    for number in 0..count {
        let part = &jpeg
            [(number * PACKET_DATA).min(jpeg.len())..((number + 1) * PACKET_DATA).min(jpeg.len())];
        let mut header = [0u8; PACKET_HEADER];
        header[0..2].copy_from_slice(&[0x01, 0xff]);
        header[2..6].copy_from_slice(&(jpeg.len() as u32).to_le_bytes());
        header[6..8].copy_from_slice(&(part.len() as u16).to_le_bytes());
        header[8] = 1;
        header[9..11].copy_from_slice(&(count as u16).to_le_bytes());
        header[11..13].copy_from_slice(&(number as u16).to_le_bytes());
        data.extend_from_slice(&header);
        data.extend_from_slice(part);
        data.resize(data.len() + PACKET_DATA - part.len(), 0);
    }
    let fill = (4 - count % 4) % 4;
    data.resize(data.len() + fill * PACKET, 0);
    data.chunks(WRITE).map(<[u8]>::to_vec).collect()
}

/// turn: how far TRCC turns the pictures of this screen, clockwise (degrees).
pub fn turn(model_id: &str, variant: u32) -> u32 {
    if model_id == SIX_INCH {
        return if variant == 2 { 90 } else { 0 };
    }
    if variant == 2 || variant == 4 {
        0
    } else {
        180
    }
}

pub fn jpeg(picture: &Image) -> Result<Vec<u8>> {
    let mut size = 0;
    for quality in QUALITIES {
        let data = lp_decode::save_jpeg_with(picture, quality, false)?;
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

pub fn turned(picture: &Image, degrees: u32) -> Image {
    match degrees {
        90 => picture.transpose(Transpose::Rotate270),
        180 => picture.transpose(Transpose::Rotate180),
        _ => picture.clone(),
    }
}

/// What the keeper guards: the link and the last picture's writes.
pub struct Link {
    io: Option<Box<dyn UsbIo>>,
    label: String,
    counters: Counters,
    hid: bool,
    reports: Option<HidReports>,
    out: u8,
    into: u8,
    missed: u32,
    last: Vec<Vec<u8>>,
}

impl Link {
    fn read(&mut self) -> std::result::Result<Option<Vec<u8>>, UsbError> {
        let Some(io) = self.io.as_mut() else {
            return Ok(None);
        };
        match io.read(self.into, PACKET, ANSWER_TIMEOUT_MS) {
            Ok(answer) => Ok(Some(answer)),
            Err(UsbError::Timeout) => Ok(None),
            Err(e) => Err(e),
        }
    }

    fn stopped(&mut self, error: &str) -> DeviceError {
        self.counters.usb_errors += 1;
        DeviceError(tf(
            "the {panel} stopped answering: {error}",
            &[("panel", &self.label), ("error", error)],
        ))
    }

    fn send(&mut self, writes: Vec<Vec<u8>>) -> Result<()> {
        let sent = (|| -> std::result::Result<(), UsbError> {
            let Some(io) = self.io.as_mut() else {
                return Err(UsbError::Other("the device is let go".into()));
            };
            if self.hid {
                let reports = self.reports.clone().expect("the 6.86\"'s reports");
                for report in &writes {
                    reports.write(io.as_mut(), report, WRITE_TIMEOUT_MS)?;
                }
            } else {
                for chunk in &writes {
                    io.write(self.out, chunk, WRITE_TIMEOUT_MS)?;
                }
                let answer = self.read()?;
                self.missed = if answered(answer.as_deref()) {
                    0
                } else {
                    self.missed + 1
                };
            }
            Ok(())
        })();
        if let Err(e) = sent {
            return Err(self.stopped(&e.to_string()));
        }
        self.counters.bytes += writes.iter().map(|w| w.len() as u64).sum::<u64>();
        self.last = writes;
        if self.missed >= MISSED {
            let missed = format!("no answer to {} pictures", self.missed);
            return Err(self.stopped(&missed));
        }
        Ok(())
    }

    fn again(link: &mut Link) -> Result<()> {
        if link.io.is_some() && !link.last.is_empty() {
            let last = link.last.clone();
            return link.send(last);
        }
        Ok(())
    }
}

/// A Trofeo Vision (TrofeoDisplay).
pub struct Trofeo {
    pub model: Option<&'static PanelModel>,
    product: u16,
    interface: u8,
    size: (i64, i64),
    turn: u32,
    brightness: i64,
    picture: Option<Image>,
    keeper: Keeper<Link>,
}

impl Trofeo {
    pub fn new(model: Option<&'static PanelModel>) -> Trofeo {
        let link = Link {
            io: None,
            label: String::new(),
            counters: Counters::default(),
            hid: false,
            reports: None,
            out: 0,
            into: 0,
            missed: 0,
            last: Vec::new(),
        };
        Trofeo {
            model,
            product: 0,
            interface: 0,
            size: (1280, 480),
            turn: 0,
            brightness: 100,
            picture: None,
            keeper: Keeper::new(again_s(BULK_PRODUCT), link, Link::again, "trofeo-keep"),
        }
    }

    pub fn open(&mut self) -> Result<()> {
        let own: Vec<u16> = self
            .model
            .map_or(Vec::new(), |m| m.usb_ids.iter().map(|ids| ids.1).collect());
        let products: Vec<u16> = own
            .iter()
            .copied()
            .chain(PRODUCTS.iter().copied().filter(|p| !own.contains(p)))
            .collect();
        let (io, product) = usb_link::take(VENDOR_ID, &products, &self.describe())?;
        self.open_on(Box::new(io), product)
    }

    /// open on `io`, a screen of the product id `product` taken for this panel.
    pub fn open_on(&mut self, mut io: Box<dyn UsbIo>, product: u16) -> Result<()> {
        self.product = product;
        let asked = if product == HID_PRODUCT {
            self.ask_hid(io.as_mut())
                .map(|said| said.map(|(k, v)| (hid_kind(k as u8), k, v)))
        } else {
            self.ask_bulk(io.as_mut())
                .map(|said| said.map(|(k, v)| (bulk_kind(k), k, v)))
        };
        let failed = match asked {
            Ok(Ok((Some(found), _, variant))) => {
                self.model = find_model(found);
                self.size = size_of(found);
                self.turn = turn(found, variant);
                None
            }
            Ok(Ok((None, kind, _))) => Some(DeviceError(tf(
                "the {panel} is a kind Libre Panel does not know yet ({kind})",
                &[
                    ("panel", &self.describe()),
                    ("kind", &format!("0x{kind:02x}")),
                ],
            ))),
            Ok(Err(e)) => Some(e),
            Err(e) => {
                self.keeper.with(|kept| kept.inner.counters.usb_errors += 1);
                Some(DeviceError(tf(
                    "the {panel} did not start: {error}",
                    &[("panel", &self.describe()), ("error", &e.to_string())],
                )))
            }
        };
        if let Some(e) = failed {
            io.release();
            return Err(e);
        }
        let label = self.describe();
        let hid = product == HID_PRODUCT;
        self.keeper.with(|kept| {
            kept.inner.io = Some(io);
            kept.inner.label = label;
            kept.inner.hid = hid;
            kept.inner.missed = 0;
            kept.inner.last.clear();
        });
        self.keeper.set_every(again_s(product));
        self.keeper.start();
        Ok(())
    }

    /// The 6.86" asked what it is.
    fn ask_hid(&mut self, io: &mut dyn UsbIo) -> std::result::Result<Result<(u32, u32)>, UsbError> {
        self.interface = usb_link::hid_interface(io).unwrap_or(0);
        if let Err(e) = usb_link::claim(io, &[self.interface]) {
            return Ok(Err(e));
        }
        let reports = HidReports::new(io, self.interface, REPORT, false);
        self.keeper
            .with(|kept| kept.inner.reports = Some(reports.clone()));
        for _ in 0..2 {
            reports.write(io, &hid_hello(), WRITE_TIMEOUT_MS)?;
            for _ in 0..8 {
                let Some(answer) = reports.read(io, ANSWER_TIMEOUT_MS)? else {
                    break;
                };
                if let Some(said) = hid_answer(&answer) {
                    return Ok(Ok(said));
                }
            }
        }
        Ok(Err(DeviceError(tf(
            "the {panel} does not say what it is",
            &[("panel", &self.describe())],
        ))))
    }

    /// A bar asked what it is.
    fn ask_bulk(
        &mut self,
        io: &mut dyn UsbIo,
    ) -> std::result::Result<Result<(u32, u32)>, UsbError> {
        self.interface = 0;
        if let Err(e) = usb_link::claim(io, &[0]) {
            return Ok(Err(e));
        }
        let (Some(out), Some(into)) = usb_link::endpoints(io, 0) else {
            return Ok(Err(DeviceError("bulk endpoints not found".into())));
        };
        self.keeper.with(|kept| {
            kept.inner.out = out;
            kept.inner.into = into;
        });
        io.write(out, &bulk_hello(), WRITE_TIMEOUT_MS)?;
        let answer = match io.read(into, PACKET, ANSWER_TIMEOUT_MS) {
            Ok(answer) => answer,
            Err(UsbError::Timeout) => Vec::new(),
            Err(e) => return Err(e),
        };
        Ok(bulk_answer(&answer).ok_or_else(|| {
            DeviceError(tf(
                "the {panel} does not say what it is",
                &[("panel", &self.describe())],
            ))
        }))
    }

    fn encode(&self, native: &Image) -> Result<Vec<Vec<u8>>> {
        let data = jpeg(&turned(&dimmed(native, self.brightness)?, self.turn))?;
        Ok(if self.product == HID_PRODUCT {
            hid_reports(&data, self.size.0, self.size.1)
        } else {
            bulk_writes(&data)
        })
    }

    fn is_open(&self) -> bool {
        self.keeper.with(|kept| kept.inner.io.is_some())
    }
}

impl Driver for Trofeo {
    fn model(&self) -> Option<&'static PanelModel> {
        self.model
    }

    fn describe(&self) -> String {
        self.model
            .map_or("Thermalright Trofeo Vision".into(), |m| m.label())
    }

    fn show(&mut self, frame: &Image, _region: Option<Region>) -> Result<()> {
        if !self.is_open() {
            return Err(DeviceError(tf(
                "the {panel} is not open",
                &[("panel", &self.describe())],
            )));
        }
        let native = crate::beada::to_native(frame, self.size)?;
        let writes = self.encode(&native)?;
        let mut checked = false;
        let shown = self.keeper.with(|kept| {
            kept.check()?;
            checked = true;
            kept.inner.send(writes)?;
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
            if let Some(picture) = self.picture.clone() {
                let writes = self.encode(&picture)?;
                self.keeper.with(|kept| {
                    kept.inner.send(writes)?;
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
            if let Some(mut io) = kept.inner.io.take() {
                io.release();
            }
            kept.inner.reports = None;
            kept.inner.last.clear();
            kept.inner.missed = 0;
        });
        self.picture = None;
    }
}

impl Drop for Trofeo {
    fn drop(&mut self) {
        self.close();
    }
}
