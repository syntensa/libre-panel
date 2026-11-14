//! Lian Li's USB screens with a TURZX-like protocol (devices/lianli_usb.py; VID 0x1CBE): the
//! Universal Screen 8.8" and 9.2" and the HydroShift II LCD and OLED Curve. The TURZX command
//! packet with the same commands, but a command and its picture go in one bulk write; the
//! picture is a JPEG to the background layer (101), the PNG layer (102) over it cleared once
//! when the panel is opened; the answer to a picture may not come, its byte 8 says how many
//! pictures wait in the panel, and over 3 the next waits until it is 2 (asked with 122);
//! opening: 10, 123, 52, the cleared PNG layer and 15 (30 a second); 14 sets the brightness.

use std::time::{Duration, Instant};

use lp_pixels::{Filter, Image, Mode, Transpose};

use crate::base::{tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::models::{PanelModel, MODELS};
use crate::turzx_packet::{now_stamp, Stamp, Transport};
use crate::usb_link::{self, UsbIo};

pub const PROTOCOL: &str = "usb-lianli";
pub const VENDOR_ID: u16 = 0x1CBE;
const CMD_SYNC: u8 = 10;
const CMD_BRIGHTNESS: u8 = 14;
const CMD_FRAME_RATE: u8 = 15;
const CMD_STOP_CLOCK: u8 = 52;
const CMD_JPEG: u8 = 101;
const CMD_PNG: u8 = 102;
const CMD_LEVEL: u8 = 122;
const CMD_STOP_MEDIA: u8 = 123;
pub const MAX_PICTURE: usize = 512_000;
pub const QUALITIES: [i64; 5] = [92, 85, 75, 60, 45];

pub fn models() -> Vec<&'static PanelModel> {
    MODELS.iter().filter(|m| m.protocol == PROTOCOL).collect()
}

/// present_model: a Lian Li screen on the bus (none opened).
pub fn present_model() -> Option<&'static PanelModel> {
    models()
        .into_iter()
        .find(|m| usb_link::present(VENDOR_ID, &[m.usb_ids[0].1]).is_some())
}

/// jpeg: the picture within MAX_PICTURE, at the best quality that keeps within it.
pub fn jpeg(native: &Image) -> Result<Vec<u8>> {
    let rgb;
    let native = if native.mode() == Mode::RGB {
        native
    } else {
        rgb = native.convert(Mode::RGB)?;
        &rgb
    };
    let mut size = 0;
    for quality in QUALITIES {
        let data = lp_decode::save_jpeg(native, quality)?;
        if data.len() <= MAX_PICTURE {
            return Ok(data);
        }
        size = data.len();
    }
    Err(DeviceError(format!(
        "frame too detailed for the panel ({} KB)",
        size / 1024
    )))
}

/// A Lian Li screen (LianLiDisplay).
pub struct LianLi {
    pub model: Option<&'static PanelModel>,
    transport: Option<Transport>,
    counters: Counters,
    pub stamp: Stamp,
}

impl LianLi {
    pub fn new(model: Option<&'static PanelModel>) -> LianLi {
        LianLi {
            model,
            transport: None,
            counters: Counters::default(),
            stamp: now_stamp,
        }
    }

    /// open on the screen of the model's product id on the bus.
    pub fn open(&mut self) -> Result<()> {
        let Some(model) = self.model.or_else(present_model) else {
            return Err(DeviceError(lp_i18n::t("no Lian Li screen on the USB bus")));
        };
        self.model = Some(model);
        let pid = model.usb_ids[0].1;
        let found = usb_link::find(VENDOR_ID, &[pid])?;
        if found.is_empty() {
            return Err(DeviceError(lp_i18n::t(
                "no Turing/TURZX USB panel found. Is it plugged in, and is the TURZX app closed? \
                 (it holds the device exclusively)",
            )));
        }
        let (io, _) = usb_link::take(VENDOR_ID, &[pid], &model.label()).map_err(|e| {
            if e.0.starts_with("another panel") {
                DeviceError(lp_i18n::t(
                    "the panel is in use by another panel of Libre Panel",
                ))
            } else {
                e
            }
        })?;
        self.open_on(Box::new(io))
    }

    /// open on `io`, the screen taken for this panel.
    pub fn open_on(&mut self, mut io: Box<dyn UsbIo>) -> Result<()> {
        let model = match self.model {
            Some(model) => model,
            None => {
                io.release();
                return Err(DeviceError(lp_i18n::t("no Lian Li screen on the USB bus")));
            }
        };
        if let Err(e) = usb_link::claim(io.as_mut(), &[0]) {
            io.release();
            return Err(e);
        }
        if !io.interfaces().iter().any(|i| i.number == 0) {
            io.release();
            return Err(DeviceError("USB interface 0 not found".into()));
        }
        let (Some(out), Some(into)) = usb_link::endpoints(io.as_ref(), 0) else {
            io.release();
            return Err(DeviceError("bulk endpoints not found".into()));
        };
        self.transport = Some(Transport::new(io, out, into, self.stamp));
        let opened = (|| -> Result<()> {
            let transport = self.transport.as_mut().expect("just made");
            transport.drain(50, 64);
            for (command, data) in [
                (CMD_SYNC, &[][..]),
                (CMD_STOP_MEDIA, &[][..]),
                (CMD_STOP_CLOCK, &[0][..]),
            ] {
                transport.ask(command, data, 2000, false)?;
            }
            let clear = Image::blank(
                Mode::RGBA,
                model.native_width as usize,
                model.native_height as usize,
            );
            let png = crate::turzx::encode_png_rgba(&clear, crate::png::Compression::new(2))?;
            self.push(CMD_PNG, &png)?;
            let transport = self.transport.as_mut().expect("open");
            transport.ask(CMD_FRAME_RATE, &[30], 2000, false)?;
            Ok(())
        })();
        if let Err(e) = opened {
            self.close();
            return Err(e);
        }
        Ok(())
    }

    fn push(&mut self, command: u8, data: &[u8]) -> Result<()> {
        let transport = self
            .transport
            .as_mut()
            .ok_or_else(|| DeviceError("not open".into()))?;
        let reply = transport.push(command, &(data.len() as u32).to_be_bytes(), data, 5000, 200)?;
        self.counters.bytes += data.len() as u64;
        if reply.is_some_and(|reply| reply.len() > 8 && reply[8] > 3) {
            self.wait_for_room()?;
        }
        Ok(())
    }

    /// Until two pictures or fewer wait in the panel (2 s at most).
    fn wait_for_room(&mut self) -> Result<()> {
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            let transport = self
                .transport
                .as_mut()
                .ok_or_else(|| DeviceError("not open".into()))?;
            match transport.query(CMD_LEVEL, 200)? {
                Some(reply) if reply.len() > 8 && reply[8] > 2 => {
                    std::thread::sleep(Duration::from_millis(2));
                }
                _ => return Ok(()),
            }
        }
        Ok(())
    }
}

impl Driver for LianLi {
    fn model(&self) -> Option<&'static PanelModel> {
        self.model
    }

    fn describe(&self) -> String {
        self.model.map_or("Lian Li".into(), |m| m.label())
    }

    fn show(&mut self, frame: &Image, _region: Option<Region>) -> Result<()> {
        let Some(model) = self.model.filter(|_| self.transport.is_some()) else {
            return Err(DeviceError(tf(
                "the {panel} is not open",
                &[("panel", &self.describe())],
            )));
        };
        let size = (model.native_width, model.native_height);
        let mut native = if (frame.width() > frame.height()) != (size.0 > size.1) {
            frame.transpose(Transpose::Rotate270)
        } else {
            frame.clone()
        };
        if (native.width() as i64, native.height() as i64) != size {
            native = native.resize(
                size.0 as usize,
                size.1 as usize,
                Filter::Lanczos,
                None,
                None,
            )?;
        }
        let data = jpeg(&native)?;
        self.push(CMD_JPEG, &data)
            .inspect_err(|_| self.counters.usb_errors += 1)
    }

    fn set_brightness(&mut self, percent: i64) -> Result<()> {
        if let Some(transport) = self.transport.as_mut() {
            transport.ask(CMD_BRIGHTNESS, &[percent.clamp(0, 100) as u8], 2000, false)?;
        }
        Ok(())
    }

    fn counters(&self) -> Counters {
        self.counters
    }

    fn close(&mut self) {
        if let Some(mut transport) = self.transport.take() {
            transport.io.release();
        }
    }
}

impl Drop for LianLi {
    fn drop(&mut self) {
        self.close();
    }
}
