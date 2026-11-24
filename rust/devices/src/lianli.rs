//! Lian Li's USB screens with a TURZX-like protocol (devices/lianli_usb.py; VID 0x1CBE): the
//! Universal Screen 8.8" and 9.2" and the HydroShift II LCD and OLED Curve. The TURZX command
//! packet with the same commands, but a command and its picture go in one bulk write; the
//! picture is a JPEG to the background layer (101), the PNG layer (102) over it cleared once
//! when the panel is opened; the answer to a picture may not come, its byte 8 says how many
//! pictures wait in the panel, and over 3 the next waits until it is 2 (asked with 122);
//! opening: 10, 123, 52, the cleared PNG layer and 15 (30 a second); 14 sets the brightness.
//! The Universal Screen 8.8" is also told the time when it is opened (51, SetClock, between
//! 123 and 52, as L-Connect and the open drivers tell it): the clock its firmware draws by
//! itself once the PC has gone then shows the time, not 00:00 and on.

use std::time::{Duration, Instant};

use lp_pixels::{Filter, Image, Mode, Transpose};

use crate::base::{tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::models::{PanelModel, MODELS};
use crate::turzx_packet::{now_stamp, wall_clock, Stamp, Transport};
use crate::usb_link::{self, UsbIo};

pub const PROTOCOL: &str = "usb-lianli";
pub const VENDOR_ID: u16 = 0x1CBE;
const CMD_SYNC: u8 = 10;
const CMD_BRIGHTNESS: u8 = 14;
const CMD_FRAME_RATE: u8 = 15;
const CMD_SET_CLOCK: u8 = 51;
const CMD_STOP_CLOCK: u8 = 52;
const CMD_JPEG: u8 = 101;
const CMD_PNG: u8 = 102;
const CMD_LEVEL: u8 = 122;
const CMD_STOP_MEDIA: u8 = 123;
pub const MAX_PICTURE: usize = 512_000;
/// The screens told the time as they are opened: the Universal Screen 8.8", whose SetClock the
/// protocol reference has from L-Connect and from its firmware (lianli88_0001_0023).
pub const CLOCK_MODELS: [&str; 1] = ["lianli-universal-8.8"];
/// SetClock's mode 2: the clock set, and the panel's own choice whether it draws a clock once
/// the PC has gone (saved in it by modes 0 and 1, L-Connect's switch) left as it is.
const CLOCK_ONLY: u8 = 2;
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

/// The local time now as year, month, day, hour, minute and second (None where it cannot be
/// told).
pub type LocalNow = fn() -> Option<[u32; 6]>;

/// The time of this computer's zone now, as the panel is told it.
pub fn local_now() -> Option<[u32; 6]> {
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    let (t, _) = lp_py::zones::fromtimestamp(zone, wall_clock().floor())?;
    let year = u32::try_from(t.year).ok().filter(|year| *year <= 0xFFFF)?;
    Some([year, t.month, t.day, t.hour, t.minute, t.second])
}

/// SetClock's arguments (bytes 8 to 15 of its packet): the year big endian, the month, the
/// day, the hour, the minute, the second, and the mode.
pub fn clock_args(now: [u32; 6], mode: u8) -> [u8; 8] {
    let [year, month, day, hour, minute, second] = now;
    let byte = |value: u32| u8::try_from(value).unwrap_or(0);
    [
        byte(year >> 8),
        byte(year & 0xFF),
        byte(month),
        byte(day),
        byte(hour),
        byte(minute),
        byte(second),
        mode,
    ]
}

/// A Lian Li screen (LianLiDisplay).
pub struct LianLi {
    pub model: Option<&'static PanelModel>,
    transport: Option<Transport>,
    counters: Counters,
    pub stamp: Stamp,
    /// the time a screen of CLOCK_MODELS is told as it is opened (the tests' a fixed one)
    pub local_now: LocalNow,
}

impl LianLi {
    pub fn new(model: Option<&'static PanelModel>) -> LianLi {
        LianLi {
            model,
            transport: None,
            counters: Counters::default(),
            stamp: now_stamp,
            local_now,
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
            for (command, data) in [(CMD_SYNC, &[][..]), (CMD_STOP_MEDIA, &[][..])] {
                transport.ask(command, data, 2000, false)?;
            }
            if CLOCK_MODELS.contains(&model.id) {
                if let Some(now) = (self.local_now)() {
                    // (the time is a help, not a need: where it is not answered, the panel is
                    // opened as before, a late answer thrown away)
                    let told =
                        transport.ask(CMD_SET_CLOCK, &clock_args(now, CLOCK_ONLY), 2000, false);
                    if told.is_err() {
                        transport.drain(200, 4);
                    }
                }
            }
            transport.ask(CMD_STOP_CLOCK, &[0], 2000, false)?;
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

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use super::*;
    use crate::models::find_model;
    use crate::turzx_packet::packet;
    use crate::usb_link::{EndpointInfo, InterfaceInfo, UsbError, UsbResult};

    /// The moment the tests tell: 2026-10-30 05:19:47, the reference's own capture.
    fn at_capture() -> Option<[u32; 6]> {
        Some([2026, 10, 30, 5, 19, 47])
    }

    fn stamp() -> u32 {
        0x0102_0304
    }

    /// A screen the tests make up: what was written to it, the packets it answers (all but
    /// those `silent` names, by their place among the writes).
    struct Made {
        written: Arc<Mutex<Vec<Vec<u8>>>>,
        waiting: usize,
        silent: Vec<usize>,
    }

    impl UsbIo for Made {
        fn product(&mut self) -> String {
            "Lian Li".into()
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
                endpoints: vec![endpoint(0x01), endpoint(0x81)],
            }]
        }
        fn claim(&mut self, _: &[u8]) -> UsbResult<()> {
            Ok(())
        }
        fn write(&mut self, _: u8, data: &[u8], _: u64) -> UsbResult<()> {
            let mut written = self.written.lock().unwrap();
            if !self.silent.contains(&written.len()) {
                self.waiting += 1;
            }
            written.push(data.to_vec());
            Ok(())
        }
        fn read(&mut self, _: u8, _: usize, _: u64) -> UsbResult<Vec<u8>> {
            if self.waiting == 0 {
                return Err(UsbError::Timeout);
            }
            self.waiting -= 1;
            // (byte 8 of an answer to a picture: none waits in the screen)
            Ok(vec![0; 512])
        }
        fn control_out(&mut self, _: u8, _: u8, _: u16, _: u16, _: &[u8], _: u64) -> UsbResult<()> {
            panic!("a Lian Li screen is sent no control transfer");
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
            panic!("a Lian Li screen is sent no control transfer");
        }
        fn release(&mut self) {}
    }

    /// The packets written as `model` was opened, the screen answering all but `silent`.
    fn opened(model: &str, silent: Vec<usize>) -> (Result<()>, Vec<Vec<u8>>) {
        let written = Arc::new(Mutex::new(Vec::new()));
        let io = Made {
            written: Arc::clone(&written),
            waiting: 0,
            silent,
        };
        let mut screen = LianLi::new(find_model(model));
        screen.stamp = stamp;
        screen.local_now = at_capture;
        let done = screen.open_on(Box::new(io));
        let packets = written.lock().unwrap().clone();
        (done, packets)
    }

    fn command(cmd: u8, args: &[u8]) -> Vec<u8> {
        packet(cmd, args, stamp()).unwrap().to_vec()
    }

    /// The reference's capture of L-Connect's switch (on, 2026-10-30 05:19:47): the bytes after
    /// the stamp, 07 EA 0A 1E 05 13 2F 01; Libre Panel sends the same time with mode 2.
    #[test]
    fn the_time_is_laid_out_as_the_reference_captured_it() {
        let now = at_capture().unwrap();
        assert_eq!(
            clock_args(now, 1),
            [0x07, 0xEA, 0x0A, 0x1E, 0x05, 0x13, 0x2F, 0x01]
        );
        assert_eq!(clock_args(now, CLOCK_ONLY)[7], 2);
    }

    /// The 8.8" is told the time between StopPlay and StopClock, as L-Connect tells it, with
    /// the mode that leaves its own choice of an offline clock as it is.
    #[test]
    fn the_8_8_is_told_the_time_as_it_is_opened() {
        let (done, packets) = opened("lianli-universal-8.8", Vec::new());
        assert!(done.is_ok(), "{done:?}");
        let told = clock_args(at_capture().unwrap(), CLOCK_ONLY);
        assert_eq!(
            packets[..4],
            [
                command(CMD_SYNC, &[]),
                command(CMD_STOP_MEDIA, &[]),
                command(CMD_SET_CLOCK, &told),
                command(CMD_STOP_CLOCK, &[0]),
            ]
        );
        let last = packets.last().unwrap();
        assert_eq!(*last, command(CMD_FRAME_RATE, &[30]));
    }

    /// The other Lian Li screens are opened as before: nothing is known of their clock.
    #[test]
    fn the_other_screens_are_not_told_the_time() {
        for model in [
            "lianli-universal-9.2",
            "lianli-hydroshift2-lcd",
            "lianli-hydroshift2-oled",
        ] {
            let (done, packets) = opened(model, Vec::new());
            assert!(done.is_ok(), "{model}: {done:?}");
            assert_eq!(
                packets[..3],
                [
                    command(CMD_SYNC, &[]),
                    command(CMD_STOP_MEDIA, &[]),
                    command(CMD_STOP_CLOCK, &[0]),
                ],
                "{model}"
            );
        }
    }

    /// An 8.8" that does not answer SetClock is opened all the same.
    #[test]
    fn an_8_8_that_does_not_answer_the_time_is_opened_all_the_same() {
        let (done, packets) = opened("lianli-universal-8.8", vec![2]);
        assert!(done.is_ok(), "{done:?}");
        assert_eq!(packets[3], command(CMD_STOP_CLOCK, &[0]));
    }

    /// Where the time cannot be told, the 8.8" is opened without it.
    #[test]
    fn without_a_time_the_8_8_is_opened_without_it() {
        let written = Arc::new(Mutex::new(Vec::new()));
        let io = Made {
            written: Arc::clone(&written),
            waiting: 0,
            silent: Vec::new(),
        };
        let mut screen = LianLi::new(find_model("lianli-universal-8.8"));
        screen.stamp = stamp;
        screen.local_now = || None;
        assert!(screen.open_on(Box::new(io)).is_ok());
        let packets = written.lock().unwrap().clone();
        assert_eq!(packets[2], command(CMD_STOP_CLOCK, &[0]));
    }

    /// The zone's time now is a real date and time.
    #[test]
    fn the_local_time_is_a_date_and_a_time() {
        let [year, month, day, hour, minute, second] = local_now().expect("a time");
        assert!(year >= 2024 && (1..=12).contains(&month) && (1..=31).contains(&day));
        assert!(hour < 24 && minute < 60 && second < 61);
    }
}
