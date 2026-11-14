//! USB displays that speak "udisp" (devices/udisp.py): Waveshare's USB monitors with the card
//! image that makes them a USB display (the 2.8" round one), and the ESP32 displays the
//! protocol was made for. They take whole pictures, as JPEG or raw pixels, as fast as USB 2.0
//! carries them: Libre Panel streams them, every frame at a steady pace.
//!
//! - The device is vendor-specific (`303a:2986`, `2987`), one interface with a bulk OUT
//!   endpoint; every picture is one transfer to it.
//! - Its product string says what it takes, fields between underscores:
//!   `t113udisp1_R480x480_Ejpg5_Ergb16_Ergb32` is 480x480, taking JPEG and pixels of 16 bits
//!   and 32; `Fps30` caps the frames a second, `Bl20` a JPEG at 20 KB. The kind named first
//!   is the one it is sent (Waveshare's 2.8" draws JPEG alone, #30).
//! - A picture is a header of 16 bytes, little-endian (CRC-16 sent as 0, type, command 0, x,
//!   y, width, height, then a frame number in the low 10 bits and the payload's length in the
//!   high 22) and its payload.
//! - A transfer of whole packets (512 bytes) is ended by a header of type 0xFF, a picture of
//!   1x1 with nothing in it.
//!
//! Waveshare's 2.8" (`t113udisp`) shows a picture turned by 180°: Libre Panel turns what it
//! sends it. The display has no known command for its backlight: the brightness goes into the
//! picture, and a change of it sends the last picture again at once.

use std::time::Instant;

use lp_pixels::{Filter, Image, Mode, Transpose};

use crate::base::{dimmed, tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::keep::Keeper;
use crate::models::{PanelModel, MODELS};
use crate::usb_link::{self, UsbIo};

pub const PROTOCOL: &str = "usb-udisp";
pub const VENDOR_ID: u16 = 0x303A;
pub const PRODUCT_IDS: [u16; 2] = [0x2986, 0x2987];
pub const RGB565: u8 = 0;
pub const RGB888: u8 = 1;
pub const YUV420: u8 = 2;
pub const JPEG: u8 = 3;
pub const MAX_PAYLOAD: usize = (1 << 22) - 1;
const FRAME_IDS: u32 = 1 << 10;
/// a JPEG's qualities: [device] jpeg_quality, then lower where it is too large
pub const QUALITIES: [i64; 5] = [90, 80, 70, 60, 50];
const WRITE_TIMEOUT_MS: u64 = 3000;
/// of the pictures a second the display came to: the most it is driven at
const REACHED: f64 = 0.9;
/// the first frames, timed for how many the display takes a second
const TRIAL: usize = 24;
/// the last picture again after this long without one
const AGAIN_S: f64 = 1.0;
/// displays that show a picture turned by 180° (the 2.8": #33)
const TURNED: [&str; 1] = ["t113udisp"];
pub const JPEG_LAYOUTS: [&str; 2] = ["4:4:4", "4:2:0"];

/// header: the 16 bytes before a picture of `size` at `at`, `payload` bytes of `kind`.
pub fn header(
    kind: u8,
    size: (u16, u16),
    payload: usize,
    frame: u32,
    at: (u16, u16),
) -> Result<Vec<u8>> {
    if payload > MAX_PAYLOAD {
        return Err(DeviceError(format!(
            "a picture of {payload} bytes is more than the display takes"
        )));
    }
    let packed = (frame % FRAME_IDS) | ((payload as u32) << 10);
    let mut out = Vec::with_capacity(16);
    out.extend_from_slice(&0u16.to_le_bytes());
    out.push(kind);
    out.push(0);
    for value in [at.0, at.1, size.0, size.1] {
        out.extend_from_slice(&value.to_le_bytes());
    }
    out.extend_from_slice(&packed.to_le_bytes());
    Ok(out)
}

/// END: after a picture of whole packets.
pub fn end() -> Vec<u8> {
    let mut out = vec![0, 0, 0xFF, 0, 0, 0, 0, 0];
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&0u32.to_le_bytes());
    out
}

/// What a product string says (described).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Said {
    /// the display's own name, its first field
    pub name: String,
    pub size: (i64, i64),
    /// the kind of picture named first
    pub kind: Option<u8>,
    pub jpeg: Option<i64>,
    pub rgb16: bool,
    pub rgb32: bool,
    pub fps: Option<i64>,
    /// a JPEG's most bytes
    pub limit: Option<i64>,
}

/// A number of ASCII digits, as Python's int() of `\d+` reads it (re's \d takes other
/// decimal digits too: those are read by their value).
fn digits(text: &str) -> Option<i64> {
    if text.is_empty() {
        return None;
    }
    let mut value: i64 = 0;
    for c in text.chars() {
        let digit = lp_py::decimal(c)?;
        value = value.checked_mul(10)?.checked_add(i64::from(digit))?;
    }
    Some(value)
}

/// described: what a product string says; None where it is no udisp display's (no
/// `R<w>x<h>`).
pub fn described(product: &str) -> Option<Said> {
    let fields: Vec<&str> = product.split('_').collect();
    let mut said = Said {
        name: fields[0].to_string(),
        ..Said::default()
    };
    let mut size = None;
    let mut named = Vec::new();
    for field in &fields {
        if let Some(rest) = field.strip_prefix('R') {
            if let Some((w, h)) = rest.split_once('x') {
                if let (Some(w), Some(h)) = (digits(w), digits(h)) {
                    size = Some((w, h));
                    continue;
                }
            }
        }
        if let Some(quality) = field.strip_prefix("Ejpg").and_then(digits) {
            said.jpeg = Some(quality);
            named.push(JPEG);
        } else if *field == "Ergb16" {
            said.rgb16 = true;
            named.push(RGB565);
        } else if *field == "Ergb32" || *field == "Ergb24" {
            said.rgb32 = true;
            named.push(RGB888);
        } else if let Some(fps) = field.strip_prefix("Fps").and_then(digits) {
            said.fps = Some(fps);
        } else if let Some(kb) = field.strip_prefix("Bl").and_then(digits) {
            said.limit = Some(kb.saturating_mul(1024));
        }
    }
    said.kind = named.first().copied();
    said.size = size?;
    Some(said)
}

/// model_for: the model of a udisp display of `size` (its own way round).
pub fn model_for(size: (i64, i64)) -> Option<&'static PanelModel> {
    MODELS
        .iter()
        .find(|m| m.protocol == PROTOCOL && (m.native_width, m.native_height) == size)
}

/// present_model: the display on the bus (not opened), by what its product string says.
pub fn present_model() -> Option<&'static PanelModel> {
    let found = usb_link::find(VENDOR_ID, &PRODUCT_IDS).ok()?;
    found
        .iter()
        .filter_map(|device| described(&device.product()))
        .find_map(|said| model_for(said.size))
}

/// jpeg: `picture` as a baseline JPEG within `limit` bytes, its colours at every pixel
/// ("4:4:4") or at every second pixel each way ("4:2:0"), at `quality`, lower only where it
/// would be more than `limit`.
pub fn jpeg(picture: &Image, limit: Option<i64>, layout: &str, quality: i64) -> Result<Vec<u8>> {
    let most = limit
        .filter(|limit| *limit != 0)
        .map_or(MAX_PAYLOAD as i64, |limit| limit.min(MAX_PAYLOAD as i64));
    let full = layout != "4:2:0";
    let lower = QUALITIES.iter().copied().filter(|lower| *lower < quality);
    let mut size = 0;
    for step in std::iter::once(quality).chain(lower) {
        let data = lp_decode::save_jpeg_with(picture, step, full)?;
        if data.len() as i64 <= most {
            return Ok(data);
        }
        size = data.len();
    }
    Err(DeviceError(format!(
        "frame too detailed for the display ({} KB)",
        size / 1024
    )))
}

/// rgb565: the pixels as 16 bits each, little-endian.
pub fn rgb565(picture: &Image) -> Result<Vec<u8>> {
    let rgb;
    let picture = if picture.mode() == Mode::RGB {
        picture
    } else {
        rgb = picture.convert(Mode::RGB)?;
        &rgb
    };
    let mut out = Vec::with_capacity(picture.width() * picture.height() * 2);
    for pixel in picture.as_bytes().chunks_exact(3) {
        let (r, g, b) = (
            u16::from(pixel[0]),
            u16::from(pixel[1]),
            u16::from(pixel[2]),
        );
        out.extend_from_slice(&(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)).to_le_bytes());
    }
    Ok(out)
}

/// takes: the kind of picture a display is sent: the one it names first; JPEG where none.
pub fn takes(said: &Said) -> u8 {
    said.kind.unwrap_or(JPEG)
}

/// to_native: a frame turned and sized into the display's picture.
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

/// What the display is told by config.toml.
#[derive(Clone, Debug)]
pub struct Options {
    /// [device] jpeg
    pub jpeg: String,
    /// [device] jpeg_quality
    pub jpeg_quality: i64,
    /// [video] fps
    pub fps: i64,
}

/// What the keeper guards: the link and the last message.
pub struct Link {
    io: Option<Box<dyn UsbIo>>,
    endpoint: u8,
    packet: usize,
    last: Vec<u8>,
    counters: Counters,
    label: String,
}

impl Link {
    fn send(&mut self, message: &[u8]) -> Result<()> {
        let Some(io) = self.io.as_mut() else {
            return Err(DeviceError(tf(
                "the {panel} is not open",
                &[("panel", &self.label)],
            )));
        };
        let ended = message.len().is_multiple_of(self.packet);
        let mut sent = io.write(self.endpoint, message, WRITE_TIMEOUT_MS);
        if sent.is_ok() && ended {
            sent = io.write(self.endpoint, &end(), WRITE_TIMEOUT_MS);
        }
        if let Err(e) = sent {
            self.counters.usb_errors += 1;
            return Err(DeviceError(tf(
                "the {panel} stopped answering: {error}",
                &[("panel", &self.label), ("error", &e.to_string())],
            )));
        }
        self.counters.bytes += (message.len() + if ended { 16 } else { 0 }) as u64;
        self.last = message.to_vec();
        Ok(())
    }

    fn again(link: &mut Link) -> Result<()> {
        if link.io.is_some() && !link.last.is_empty() {
            let last = link.last.clone();
            return link.send(&last);
        }
        Ok(())
    }
}

/// A udisp display (UdispDisplay).
pub struct Udisp {
    pub model: &'static PanelModel,
    options: Options,
    pub kind: u8,
    pub layout: String,
    pub rate: Option<f64>,
    timed: Vec<f64>,
    said: Said,
    brightness: i64,
    turned: i64,
    frame: u32,
    shown: Option<Image>,
    keeper: Keeper<Link>,
}

impl Udisp {
    pub fn new(options: Options, model: Option<&'static PanelModel>) -> Udisp {
        let model = model.unwrap_or_else(|| {
            MODELS
                .iter()
                .find(|m| m.protocol == PROTOCOL)
                .expect("a udisp model in the catalog")
        });
        let layout = if JPEG_LAYOUTS.contains(&options.jpeg.as_str()) {
            options.jpeg.clone()
        } else {
            JPEG_LAYOUTS[0].to_string()
        };
        let link = Link {
            io: None,
            endpoint: 0,
            packet: 512,
            last: Vec::new(),
            counters: Counters::default(),
            label: model.label(),
        };
        Udisp {
            model,
            options,
            kind: JPEG,
            layout,
            rate: None,
            timed: Vec::new(),
            said: Said::default(),
            brightness: 100,
            turned: 0,
            frame: 0,
            shown: None,
            keeper: Keeper::new(AGAIN_S, link, Link::again, "udisp-keep"),
        }
    }

    fn size(&self) -> (i64, i64) {
        (self.model.native_width, self.model.native_height)
    }

    fn wanted(&self) -> i64 {
        let fps = self.options.fps;
        fps.min(self.said.fps.filter(|f| *f != 0).unwrap_or(fps))
    }

    /// open on the first free display on the bus.
    pub fn open(&mut self) -> Result<()> {
        let (io, _pid) = usb_link::take(VENDOR_ID, &PRODUCT_IDS, &self.model.label())?;
        self.open_on(Box::new(io))
    }

    /// open on `io`, a display taken for this panel.
    pub fn open_on(&mut self, mut io: Box<dyn UsbIo>) -> Result<()> {
        let label = self.model.label();
        let said = described(&io.product());
        let fail = |io: &mut Box<dyn UsbIo>, why: String| {
            io.release();
            Err(DeviceError(why))
        };
        let Some(said) = said.filter(|said| said.size == self.size()) else {
            let got = described(&io.product())
                .map_or("?".to_string(), |s| format!("{}×{}", s.size.0, s.size.1));
            return fail(
                &mut io,
                tf(
                    "the USB display shows {size}, not the {panel}'s size",
                    &[("size", &got), ("panel", &label)],
                ),
            );
        };
        self.kind = takes(&said);
        self.rate = None;
        self.timed.clear();
        self.turned = if TURNED.iter().any(|t| said.name.starts_with(t)) {
            180
        } else {
            0
        };
        self.said = said;
        if let Err(e) = usb_link::claim(io.as_mut(), &[0]) {
            io.release();
            return Err(e);
        }
        let Some(endpoint) = usb_link::endpoints(io.as_ref(), 0).0 else {
            return fail(
                &mut io,
                tf(
                    "the {panel} has no endpoint for pictures",
                    &[("panel", &label)],
                ),
            );
        };
        let packet = usb_link::packet_size(io.as_ref(), endpoint, 512);
        self.keeper.with(|kept| {
            kept.inner.io = Some(io);
            kept.inner.endpoint = endpoint;
            kept.inner.packet = packet;
            kept.inner.last.clear();
        });
        self.keeper.start();
        Ok(())
    }

    fn encoded(&mut self, picture: &Image) -> Result<Vec<u8>> {
        let data = match self.kind {
            JPEG => jpeg(
                picture,
                self.said.limit,
                &self.layout,
                self.options.jpeg_quality,
            )?,
            RGB888 => picture.as_bytes().to_vec(),
            _ => rgb565(picture)?,
        };
        self.frame += 1;
        let size = (picture.width() as u16, picture.height() as u16);
        let mut message = header(self.kind, size, data.len(), self.frame, (0, 0))?;
        message.extend_from_slice(&data);
        Ok(message)
    }

    fn time(&mut self, seconds: f64) {
        if self.rate.is_some() {
            return;
        }
        self.timed.push(seconds);
        if self.timed.len() < TRIAL {
            return;
        }
        let sum: f64 = self.timed.iter().sum();
        self.rate = Some(self.timed.len() as f64 / sum.max(1e-6));
        self.timed.clear();
    }

    fn is_open(&self) -> bool {
        self.keeper.with(|kept| kept.inner.io.is_some())
    }
}

impl Driver for Udisp {
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
                &[("panel", &self.model.label())],
            )));
        }
        let began = Instant::now();
        let mut picture = to_native(frame, self.size())?;
        if self.turned != 0 {
            picture = picture.transpose(Transpose::Rotate180);
        }
        self.shown = Some(picture.clone());
        let message = self.encoded(&dimmed(&picture, self.brightness)?)?;
        self.keeper.with(|kept| {
            kept.check()?;
            kept.inner.send(&message)?;
            kept.sent();
            Ok::<(), DeviceError>(())
        })?;
        self.time(began.elapsed().as_secs_f64());
        Ok(())
    }

    fn set_brightness(&mut self, percent: i64) -> Result<()> {
        let brightness = percent.clamp(0, 100);
        let changed = brightness != self.brightness;
        self.brightness = brightness;
        if changed && self.is_open() {
            if let Some(shown) = self.shown.clone() {
                let message = self.encoded(&dimmed(&shown, brightness)?)?;
                // (the next frame finds an error out too and connects again)
                let _ = self.keeper.with(|kept| {
                    let sent = kept.inner.send(&message);
                    if sent.is_ok() {
                        kept.sent();
                    }
                    sent
                });
            }
        }
        Ok(())
    }

    fn counters(&self) -> Counters {
        self.keeper.with(|kept| kept.inner.counters)
    }

    fn streaming(&self) -> bool {
        self.is_open()
    }

    fn stream_fps(&self) -> i64 {
        let wanted = self.wanted();
        match self.rate {
            Some(rate) => wanted.min((rate * REACHED).round_ties_even() as i64).max(1),
            None => wanted,
        }
    }

    fn picture(&self) -> String {
        match self.kind {
            JPEG => format!("JPEG {}", self.layout),
            RGB888 => "RGB888".into(),
            YUV420 => "YUV420".into(),
            _ => "RGB565".into(),
        }
    }

    fn jpeg_layout(&self) -> Option<String> {
        (self.kind == JPEG).then(|| self.layout.clone())
    }

    fn set_jpeg(&mut self, layout: &str) -> Result<()> {
        if !JPEG_LAYOUTS.contains(&layout) {
            return Err(DeviceError(format!(
                "a JPEG's layout is one of {}",
                JPEG_LAYOUTS.join(", ")
            )));
        }
        self.layout = layout.to_string();
        self.options.jpeg = layout.to_string();
        Ok(())
    }

    fn close(&mut self) {
        self.keeper.stop();
        self.keeper.with(|kept| {
            if let Some(mut io) = kept.inner.io.take() {
                io.release();
            }
            kept.inner.last.clear();
        });
        self.shown = None;
    }
}

impl Drop for Udisp {
    fn drop(&mut self) {
        self.close();
    }
}
