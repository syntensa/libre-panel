//! Turing rev. C serial panels (devices/turing_rev_c.py): 2.1" round, 5" and 8.8" (V0.x).
//!
//! - Every message is padded to a multiple of 250 bytes (0x00; the bitmap start 0x2C); long
//!   data is cut into 249 bytes, each but the last followed by 0x00.
//! - A sleeping panel shows up with other USB ids (1a86:ca21, "CT21INCH", "USB7INCH",
//!   "CT88INCH"); opening its port wakes it, and it comes back as 0525:a4a7 or 1d6b:0121/0106
//!   ("20080411").
//! - HELLO answers "chs_..." and the ROM version; the model comes from the config, the serial
//!   numbers "USB7INCH" and "CT88INCH", or the first frame's size; else the 5".
//! - A whole frame: PRE_UPDATE_BITMAP, START_DISPLAY_BITMAP, DISPLAY_BITMAP, the pixels as BGRA
//!   in the panel's own orientation, a status read. A box: UPDATE_BITMAP, its rows (offset,
//!   width, pixels as BGRA, or BGR on the 2.1" and ROM 88 and older), EF 69, then a status
//!   query. A box whose data would end where the panel loses it goes out as two; a panel that
//!   asks for it again ("needReSend:1") gets the whole next frame.

use std::time::Duration;

use lp_pixels::{Image, Mode, Transpose};

use crate::base::{leave_place, place_taken, take_place, tf, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::link::Link;
use crate::models::{find_model, PanelModel, MODELS};
use crate::ports::FoundPort;
use crate::serial::PortInfo;
use crate::serial_panel::{Ctx, Family, SerialPanel};

const HELLO: [u8; 12] = [
    0x01, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0xC5, 0xD3,
];
const SET_BRIGHTNESS: [u8; 10] = [0x7B, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00];
const STOP_VIDEO: [u8; 7] = [0x79, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01];
const STOP_MEDIA: [u8; 7] = [0x96, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01];
const QUERY_STATUS: [u8; 7] = [0xCF, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01];
const PRE_UPDATE_BITMAP: [u8; 7] = [0x86, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01];
const START_DISPLAY_BITMAP: [u8; 1] = [0x2C];
const UPDATE_BITMAP: [u8; 4] = [0xCC, 0xEF, 0x69, 0x00];
const STATUS_SIZE: usize = 1024;
const AWAKE_SERIAL: &str = "20080411";
const WAKE_TRIES: usize = 15;
const HELLO_TRIES: usize = 3;

fn display_bitmap(model: &str) -> Option<[u8; 6]> {
    Some(match model {
        "turing-2.1" => [0xC8, 0xEF, 0x69, 0x00, 0x0E, 0x10],
        "turing-5" => [0xC8, 0xEF, 0x69, 0x00, 0x17, 0x70],
        "turing-8.8" => [0xC8, 0xEF, 0x69, 0x00, 0x38, 0x40],
        _ => return None,
    })
}

/// padded: `message` padded to a multiple of 250 bytes.
pub fn padded(message: &[u8], fill: u8) -> Vec<u8> {
    let mut out = message.to_vec();
    let more = (250 - message.len() % 250) % 250;
    out.resize(message.len() + more, fill);
    out
}

/// cut: `data` in pieces of 249 bytes, each but the last followed by 0x00.
pub fn cut(data: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len() + data.len() / 249 + 1);
    for (n, piece) in data.chunks(249).enumerate() {
        if n > 0 {
            out.push(0);
        }
        out.extend_from_slice(piece);
    }
    out
}

pub fn bgra(image: &Image) -> Result<Vec<u8>> {
    let rgba;
    let image = if image.mode() == Mode::RGBA {
        image
    } else {
        rgba = image.convert(Mode::RGBA)?;
        &rgba
    };
    Ok(image
        .as_bytes()
        .chunks_exact(4)
        .flat_map(|p| [p[2], p[1], p[0], p[3]])
        .collect())
}

pub fn bgr(image: &Image) -> Result<Vec<u8>> {
    let rgb;
    let image = if image.mode() == Mode::RGB {
        image
    } else {
        rgb = image.convert(Mode::RGB)?;
        &rgb
    };
    Ok(image
        .as_bytes()
        .chunks_exact(3)
        .flat_map(|p| [p[2], p[1], p[0]])
        .collect())
}

/// halves: `image` at (x, y) as two boxes.
fn halves(image: &Image, x: i64, y: i64) -> Vec<(Image, i64, i64)> {
    let (w, h) = (image.width() as i64, image.height() as i64);
    if h > 1 {
        return vec![
            (image.crop(0, 0, w, h - 1), x, y),
            (image.crop(0, h - 1, w, h), x, y + h - 1),
        ];
    }
    vec![
        (image.crop(0, 0, w - 1, 1), x, y),
        (image.crop(w - 1, 0, w, 1), x + w - 1, y),
    ]
}

/// image.rotate(degrees, expand=True) for the quarter turns: Pillow transposes then.
fn rotated(image: &Image, degrees: u32) -> Image {
    match degrees {
        90 => image.transpose(Transpose::Rotate90),
        180 => image.transpose(Transpose::Rotate180),
        270 => image.transpose(Transpose::Rotate270),
        _ => image.clone(),
    }
}

pub fn is_asleep(info: &PortInfo) -> bool {
    info.vid.zip(info.pid) == Some((0x1A86, 0xCA21))
        || matches!(
            info.serial_number.as_deref(),
            Some("CT21INCH" | "USB7INCH" | "CT88INCH")
        )
}

pub fn is_awake(info: &PortInfo) -> bool {
    matches!(
        info.vid.zip(info.pid),
        Some((0x0525, 0xA4A7) | (0x1D6B, 0x0121) | (0x1D6B, 0x0106))
    ) || info.serial_number.as_deref() == Some(AWAKE_SERIAL)
}

fn printable(answer: &[u8]) -> String {
    // decode("ascii", "ignore"), kept where string.printable has it
    answer
        .iter()
        .filter(|b| b.is_ascii() && (b.is_ascii_graphic() || b" \t\n\r\x0b\x0c".contains(b)))
        .map(|b| char::from(*b))
        .collect()
}

/// Turing rev. C (RevCDisplay's protocol).
pub struct RevC {
    /// whether the model's size is known (else: from the first frame)
    pub sized: bool,
    pub rom: u32,
    pub count: u32,
    /// the panel lost a box: the next frame goes out whole
    pub resync: bool,
    /// the pauses between the asks (the tests leave them out)
    pub pauses: bool,
}

impl RevC {
    pub fn new(configured: bool) -> RevC {
        RevC {
            sized: configured,
            rom: 87,
            count: 0,
            resync: false,
            pauses: true,
        }
    }

    fn pause(&self, seconds: f64) {
        if self.pauses {
            std::thread::sleep(Duration::from_secs_f64(seconds));
        }
    }

    fn command(ctx: &mut Ctx, message: &[u8], fill: u8, read: usize) -> Result<Vec<u8>> {
        ctx.write(&padded(message, fill))?;
        if read > 0 {
            ctx.read(read)
        } else {
            Ok(Vec::new())
        }
    }

    fn heard(&mut self, answer: &[u8]) {
        if printable(answer).contains("needReSend:1") && !self.resync {
            self.resync = true;
        }
    }

    fn tall(ctx: &Ctx) -> bool {
        ctx.model.is_some_and(|m| m.id == "turing-8.8")
    }

    fn full(&mut self, ctx: &mut Ctx, image: &Image) -> Result<()> {
        let model = ctx.model.ok_or_else(|| DeviceError("no model".into()))?;
        let landscape = ctx.orientation == Some("landscape");
        let image = if Self::tall(ctx) {
            rotated(image, if landscape { 270 } else { 180 })
        } else if !landscape {
            rotated(image, 90)
        } else {
            image.clone()
        };
        let size = ((model.native_width * model.native_width / 64) as u16).to_be_bytes();
        Self::command(ctx, &PRE_UPDATE_BITMAP, 0, 0)?;
        Self::command(ctx, &START_DISPLAY_BITMAP, 0x2C, 0)?;
        // (a model of another family has no DISPLAY_BITMAP: Python's KeyError)
        let mut display = display_bitmap(model.id)
            .ok_or_else(|| DeviceError(format!("'{}'", model.id)))?
            .to_vec();
        display.extend_from_slice(&size);
        Self::command(ctx, &display, 0, 0)?;
        Self::command(ctx, &cut(&bgra(&image)?), 0, STATUS_SIZE)?;
        let answer = Self::command(ctx, &QUERY_STATUS, 0, STATUS_SIZE)?;
        self.heard(&answer);
        Ok(())
    }

    fn rows(&self, ctx: &Ctx, image: &Image, x: i64, y: i64) -> Result<(Vec<u8>, Image)> {
        let model = ctx.model.ok_or_else(|| DeviceError("no model".into()))?;
        let (native_w, native_h) = (model.native_width, model.native_height);
        let orientation = ctx.orientation.unwrap_or("portrait");
        let (width, height) = model.size(orientation);
        let (mut x0, mut y0) = (x, y);
        let image = if Self::tall(ctx) {
            if orientation == "landscape" {
                let turned = rotated(image, 270);
                y0 = height - y - turned.width() as i64;
                turned
            } else {
                let turned = rotated(image, 180);
                x0 = height - y - turned.height() as i64;
                y0 = width - x - turned.width() as i64;
                turned
            }
        } else if orientation == "portrait" {
            let turned = rotated(image, 90);
            x0 = width - x - turned.height() as i64;
            turned
        } else {
            (x0, y0) = (y, x);
            image.clone()
        };
        let stride = if Self::tall(ctx) { native_w } else { native_h };
        let four = model.id != "turing-2.1" && self.rom > 88;
        let (pixels, size) = if four {
            (bgra(&image)?, 4)
        } else {
            (bgr(&image)?, 3)
        };
        let line = image.width() * size;
        let mut rows = Vec::with_capacity(image.height() * (line + 5));
        for h in 0..image.height() {
            let offset = (x0 + h as i64) * stride + y0;
            rows.extend_from_slice(&(offset as u32).to_be_bytes()[1..]);
            rows.extend_from_slice(&(image.width() as u16).to_be_bytes());
            rows.extend_from_slice(&pixels[h * line..(h + 1) * line]);
        }
        Ok((rows, image))
    }

    fn send_box(&mut self, ctx: &mut Ctx, image: &Image, x: i64, y: i64) -> Result<()> {
        let (rows, _) = self.rows(ctx, image, x, y)?;
        let data = if rows.len() > 250 {
            cut(&rows)
        } else {
            rows.clone()
        };
        if matches!(data.len() % 250, 0 | 248 | 249) {
            for (part, px, py) in halves(image, x, y) {
                self.send_box(ctx, &part, px, py)?;
            }
            return Ok(());
        }
        let mut head = UPDATE_BITMAP.to_vec();
        head.extend_from_slice(&((rows.len() + 2) as u32).to_be_bytes()[1..]);
        head.extend_from_slice(&[0, 0, 0]);
        head.extend_from_slice(&self.count.to_be_bytes());
        Self::command(ctx, &head, 0, 0)?;
        let mut body = data;
        body.extend_from_slice(&[0xEF, 0x69]);
        Self::command(ctx, &body, 0, 0)?;
        let answer = Self::command(ctx, &QUERY_STATUS, 0, STATUS_SIZE)?;
        self.heard(&answer);
        self.count += 1;
        Ok(())
    }
}

impl Family for RevC {
    fn family(&self) -> String {
        lp_i18n::t("Turing rev. C panel")
    }

    fn identify(
        &mut self,
        ctx: &mut Ctx,
        found: &FoundPort,
        _configured: bool,
    ) -> Result<&'static PanelModel> {
        let mut said = None;
        for attempt in 1..=HELLO_TRIES {
            ctx.drain_input();
            let mut answer = Self::command(ctx, &HELLO, 0, 23)?;
            if !answer.windows(4).any(|w| w == b"chs_") {
                answer.extend(ctx.read(64)?);
            }
            ctx.drain_input();
            let text = printable(&answer);
            if let Some(at) = text.find("chs_") {
                said = Some(text[at..].to_string());
                break;
            }
            if attempt < HELLO_TRIES {
                self.pause(0.5);
            }
        }
        let Some(text) = said else {
            return Err(DeviceError(tf(
                "the panel on {port} did not answer",
                &[("port", &found.device)],
            )));
        };
        // int(text.split(".")[2]), as Python's int() takes it
        self.rom = text
            .split('.')
            .nth(2)
            .and_then(|part| lp_py::int_of_str(part.trim()))
            .and_then(|rom| i64::try_from(&rom).ok())
            .filter(|rom| (80..=100).contains(rom))
            .map_or(87, |rom| rom as u32);
        Ok(found.model)
    }

    fn opened(&mut self, ctx: &mut Ctx) -> Result<()> {
        Self::command(ctx, &STOP_VIDEO, 0, 0)?;
        Self::command(ctx, &STOP_MEDIA, 0, STATUS_SIZE)?;
        Ok(())
    }

    fn send_brightness(&mut self, ctx: &mut Ctx, percent: i64) -> Result<()> {
        let mut message = SET_BRIGHTNESS.to_vec();
        message.push((percent as f64 / 100.0 * 255.0) as u8);
        Self::command(ctx, &message, 0, 0)?;
        Ok(())
    }

    fn send_orientation(
        &mut self,
        _ctx: &mut Ctx,
        _orientation: &str,
        _width: i64,
        _height: i64,
    ) -> Result<()> {
        Ok(()) // turned in software (OPTIONS would store settings in the panel)
    }

    fn fit(
        &mut self,
        model: &mut Option<&'static PanelModel>,
        frame: &Image,
        region: Option<Region>,
    ) -> Option<Region> {
        if !self.sized {
            let size = (frame.width() as i64, frame.height() as i64);
            if let Some(fits) = MODELS.iter().find(|m| {
                m.protocol == "serial-c"
                    && (m.size("landscape") == size || m.size("portrait") == size)
            }) {
                *model = Some(fits);
            }
            self.sized = true;
        }
        if self.resync {
            None
        } else {
            region
        }
    }

    fn send_bitmap(
        &mut self,
        ctx: &mut Ctx,
        x0: i64,
        y0: i64,
        x1: i64,
        y1: i64,
        image: &Image,
    ) -> Result<()> {
        let model = ctx.model.ok_or_else(|| DeviceError("no model".into()))?;
        let (width, height) = model.size(ctx.orientation.unwrap_or("portrait"));
        if (x0, y0, x1, y1) == (0, 0, width - 1, height - 1) {
            self.resync = false;
            self.full(ctx, image)
        } else {
            self.send_box(ctx, image, x0, y0)
        }
    }
}

/// A Turing rev. C panel (RevCDisplay): `model` as config.toml names it, else from its serial
/// number or the first frame.
pub fn panel(
    model: Option<&'static PanelModel>,
    configured: bool,
    port: &str,
) -> SerialPanel<RevC> {
    let configured = configured && model.is_some();
    SerialPanel::new(
        RevC::new(configured),
        model.filter(|_| configured),
        configured,
        port,
    )
}

/// The awake rev. C ports (on `port`, if set), the panels' serial number first.
fn awake(ports: &[PortInfo], port: &str, configured: bool) -> Vec<PortInfo> {
    let mut found: Vec<PortInfo> = ports
        .iter()
        .filter(|p| {
            is_awake(p)
                && !(!port.is_empty() && p.device != port)
                && (configured
                    || !port.is_empty()
                    || p.serial_number.as_deref() == Some(AWAKE_SERIAL))
        })
        .cloned()
        .collect();
    found.sort_by_key(|p| p.serial_number.as_deref() != Some(AWAKE_SERIAL));
    found
}

/// The ports no panel of this process has open.
fn free(ports: Vec<PortInfo>) -> Vec<PortInfo> {
    ports
        .into_iter()
        .filter(|p| !place_taken(&p.device))
        .collect()
}

/// RevCDisplay.open: an awake panel taken as it is, a sleeping one woken first (its serial
/// number may say the size), then asked what it is. `list` lists the ports, `opening` opens
/// one (pyserial's: the tests' simulated ones), `pause` waits.
pub fn open(
    panel: &mut SerialPanel<RevC>,
    port: &str,
    list: &dyn Fn() -> Vec<PortInfo>,
    opening: &mut dyn FnMut(&str) -> Result<Box<dyn Link>>,
) -> Result<()> {
    let configured = panel.family.sized;
    let mut ports = free(list());
    let mut model = panel.model();
    for info in &ports {
        let hint = match info.serial_number.as_deref() {
            Some("USB7INCH") => find_model("turing-5"),
            Some("CT88INCH") => find_model("turing-8.8"),
            _ => None,
        };
        if is_asleep(info) && hint.is_some() && !configured {
            model = hint;
            panel.family.sized = true;
        }
    }
    let mut port = port.to_string();
    let mut found = awake(&ports, &port, configured);
    if found.is_empty() {
        if let Some(info) = ports
            .iter()
            .find(|info| is_asleep(info) && !(!port.is_empty() && info.device != port))
            .cloned()
        {
            ports = wake(&info, list, opening, panel.family.pauses)?;
            port.clear(); // (the port named the sleeping panel; awake, it has another)
        }
        found = awake(&ports, &port, configured);
    }
    if found.is_empty() && port.is_empty() {
        return Err(DeviceError(tf(
            "no {panel} found on a serial port",
            &[("panel", &panel.family.family())],
        )));
    }
    let device = found.first().map_or(port.clone(), |p| p.device.clone());
    let model = model
        .or_else(|| find_model("turing-5"))
        .expect("the 5\" in the catalog");
    if !take_place(&device) {
        return Err(DeviceError(tf(
            "{port} is in use by another panel of Libre Panel",
            &[("port", &device)],
        )));
    }
    let link = match opening(&device) {
        Ok(link) => link,
        Err(e) => {
            leave_place(&device);
            return Err(DeviceError(tf(
                "cannot open {port}: {error}",
                &[("port", &device), ("error", &e.0)],
            )));
        }
    };
    let found = FoundPort {
        device: device.clone(),
        model,
        serial_number: Some(AWAKE_SERIAL.into()),
        candidates: Vec::new(),
    };
    panel.open_on(link, &found, device)
}

/// _wake: a sleeping panel's port opened until it comes back awake (up to 15 s).
fn wake(
    info: &PortInfo,
    list: &dyn Fn() -> Vec<PortInfo>,
    opening: &mut dyn FnMut(&str) -> Result<Box<dyn Link>>,
    pauses: bool,
) -> Result<Vec<PortInfo>> {
    let second = || {
        if pauses {
            std::thread::sleep(Duration::from_secs(1));
        }
    };
    for _ in 0..WAKE_TRIES {
        if let Ok(mut link) = opening(&info.device) {
            link.close();
        }
        let ports = free(list());
        if ports.iter().any(is_awake) {
            second(); // it answers a moment after it shows up
            return Ok(ports);
        }
        second();
    }
    Err(DeviceError(tf(
        "the panel on {port} did not wake up",
        &[("port", &info.device)],
    )))
}
