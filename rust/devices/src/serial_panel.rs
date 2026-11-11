//! What every serial panel driver shares (devices/serial_panel.py's SerialDisplay and
//! serial_link.py's SerialLink), and the families that need no more: Turing rev. A
//! (turing_rev_a.py), XuanFang rev. B (turing_rev_b.py), Kipye rev. D (kipye_rev_d.py) and WeAct
//! (weact.py). A family says how to ask the panel what it is, how to set the brightness and the
//! orientation and how a box of pixels goes out; finding the port, fitting frames of another
//! size, sending only the changed box and counting are the same for all.

use std::time::Duration;

use lp_pixels::{Filter, Image, Mode, Transpose};

use crate::base::{leave_place, take_place, tf, Counters, DeviceError, Result};
use crate::driver::{Driver, Region};
use crate::link::Link;
use crate::models::{find_model, orientation_of, PanelModel};
use crate::ports::FoundPort;

/// rgb565: pixels as 16-bit RGB565, two bytes each, little or big endian.
pub fn rgb565(image: &Image, little: bool) -> Result<Vec<u8>> {
    let rgb;
    let image = if image.mode() == Mode::RGB {
        image
    } else {
        rgb = image.convert(Mode::RGB)?;
        &rgb
    };
    let mut out = Vec::with_capacity(image.width() * image.height() * 2);
    for p in image.as_bytes().chunks_exact(3) {
        let high = (p[0] & 0xF8).wrapping_add(p[1] >> 5);
        let low = ((p[1] & 0x1C) << 3).wrapping_add(p[2] >> 3);
        if little {
            out.extend_from_slice(&[low, high]);
        } else {
            out.extend_from_slice(&[high, low]);
        }
    }
    Ok(out)
}

/// The panel's link as a driver uses it: what it wrote counted.
pub struct Ctx {
    link: Box<dyn Link>,
    pub written: u64,
    place: String,
    /// the model on the port (once it is known)
    pub model: Option<&'static PanelModel>,
    /// "portrait" or "landscape", set by the first frame
    pub orientation: Option<&'static str>,
}

impl Ctx {
    pub fn write(&mut self, data: &[u8]) -> Result<()> {
        self.link.write(data)?;
        self.written += data.len() as u64;
        Ok(())
    }

    pub fn read(&mut self, size: usize) -> Result<Vec<u8>> {
        self.link.read(size)
    }

    pub fn drain_input(&mut self) {
        self.link.drain_input();
    }

    /// write_chunks: `data` in pieces of `size`, each after `prefix`.
    pub fn write_chunks(&mut self, data: &[u8], size: usize, prefix: &[u8]) -> Result<()> {
        for piece in data.chunks(size.max(1)) {
            let mut out = prefix.to_vec();
            out.extend_from_slice(piece);
            self.write(&out)?;
        }
        Ok(())
    }

    /// The width of the panel in its orientation now.
    pub fn width(&self) -> i64 {
        self.model
            .map_or(0, |m| m.size(self.orientation.unwrap_or("portrait")).0)
    }

    fn close(&mut self) {
        self.link.close();
        if !self.place.is_empty() {
            leave_place(&std::mem::take(&mut self.place));
        }
    }
}

/// What a family says.
pub trait Family: Send {
    /// the family's name for messages ("Turing rev. A panel")
    fn family(&self) -> String;
    fn baud(&self) -> u32 {
        115_200
    }
    fn rtscts(&self) -> bool {
        true
    }
    /// identify: the model on the port; drivers whose panels say it ask here.
    fn identify(
        &mut self,
        _ctx: &mut Ctx,
        found: &FoundPort,
        _configured: bool,
    ) -> Result<&'static PanelModel> {
        Ok(found.model)
    }
    /// What goes to the panel once it is identified.
    fn opened(&mut self, _ctx: &mut Ctx) -> Result<()> {
        Ok(())
    }
    /// The panel's own command that turns its screen off, where it has one.
    fn screen_off(&mut self, _ctx: &mut Ctx) -> Result<bool> {
        Ok(false)
    }
    fn send_brightness(&mut self, ctx: &mut Ctx, percent: i64) -> Result<()>;
    /// Turn the panel to `orientation`; `width`x`height` is its size then.
    fn send_orientation(
        &mut self,
        ctx: &mut Ctx,
        orientation: &str,
        width: i64,
        height: i64,
    ) -> Result<()>;
    /// Before a frame is shown: the model where the frame says it (Turing rev. C's first frame),
    /// and the box to send (None: the whole frame).
    fn fit(
        &mut self,
        _model: &mut Option<&'static PanelModel>,
        _frame: &Image,
        region: Option<Region>,
    ) -> Option<Region> {
        region
    }
    /// Pixels for the box from (x0, y0) to (x1, y1), the last pixel included.
    fn send_bitmap(
        &mut self,
        ctx: &mut Ctx,
        x0: i64,
        y0: i64,
        x1: i64,
        y1: i64,
        image: &Image,
    ) -> Result<()>;
}

/// A serial panel of a family (SerialDisplay).
pub struct SerialPanel<F: Family> {
    pub family: F,
    model: Option<&'static PanelModel>,
    configured: bool,
    ctx: Option<Ctx>,
    port: String,
    brightness: Option<i64>,
    counters: Counters,
}

impl<F: Family> SerialPanel<F> {
    /// `model` as config.toml names it (None: as the port says); `configured`: chosen, not
    /// found; `port`: device.port.
    pub fn new(
        family: F,
        model: Option<&'static PanelModel>,
        configured: bool,
        port: &str,
    ) -> Self {
        SerialPanel {
            family,
            model,
            configured: configured && model.is_some(),
            ctx: None,
            port: port.to_string(),
            brightness: None,
            counters: Counters::default(),
        }
    }

    pub fn open(&mut self) -> Result<()> {
        let Some(found) = crate::ports::find_port(self.model, &self.port)? else {
            return Err(DeviceError(tf(
                "no {panel} found on a serial port",
                &[("panel", &self.family.family())],
            )));
        };
        if !take_place(&found.device) {
            return Err(DeviceError(tf(
                "{port} is in use by another panel of Libre Panel",
                &[("port", &found.device)],
            )));
        }
        let link = crate::serial::SerialPort::open(
            &found.device,
            self.family.baud(),
            self.family.rtscts(),
        )
        .map_err(|e| {
            leave_place(&found.device);
            DeviceError(tf(
                "cannot open {port}: {error}",
                &[("port", &found.device), ("error", &e.to_string())],
            ))
        })?;
        let place = found.device.clone();
        self.open_on(Box::new(link), &found, place)
    }

    /// open on `link`, the port of `found`, taken as `place` ("" for none).
    pub fn open_on(&mut self, link: Box<dyn Link>, found: &FoundPort, place: String) -> Result<()> {
        let mut ctx = Ctx {
            link,
            written: 0,
            place,
            model: self.model,
            orientation: None,
        };
        let identified = self
            .family
            .identify(&mut ctx, found, self.configured)
            .and_then(|model| {
                ctx.model = Some(model);
                self.family.opened(&mut ctx).map(|()| model)
            });
        match identified {
            Ok(model) => {
                self.model = Some(model);
                self.ctx = Some(ctx);
                Ok(())
            }
            Err(e) => {
                ctx.close();
                Err(e)
            }
        }
    }
}

impl<F: Family> Driver for SerialPanel<F> {
    fn model(&self) -> Option<&'static PanelModel> {
        self.model
    }

    fn describe(&self) -> String {
        self.model
            .map_or_else(|| self.family.family(), |m| m.label())
    }

    fn show(&mut self, frame: &Image, region: Option<Region>) -> Result<()> {
        let Some(ctx) = self.ctx.as_mut() else {
            return Err(DeviceError("panel not open".into()));
        };
        let region = self.family.fit(&mut self.model, frame, region);
        ctx.model = self.model;
        let orientation = orientation_of(frame.width() as i64, frame.height() as i64);
        let size = self
            .model
            .map_or((frame.width() as i64, frame.height() as i64), |m| {
                m.size(orientation)
            });
        let mut region = region;
        let padded;
        let mut frame = frame;
        if (frame.width() as i64, frame.height() as i64) != size {
            // a found panel of another size than the theme
            let corner = frame.as_bytes()[..frame.bands()].to_vec();
            padded = frame.pad(
                (size.0 as usize, size.1 as usize),
                Filter::Lanczos,
                Some(&corner),
                (0.5, 0.5),
            )?;
            frame = &padded;
            region = None;
        }
        if ctx.orientation != Some(orientation) {
            self.family
                .send_orientation(ctx, orientation, size.0, size.1)?;
            ctx.orientation = Some(orientation);
            region = None;
        }
        let (x0, y0, x1, y1) = region.unwrap_or((0, 0, size.0, size.1));
        if x1 <= x0 || y1 <= y0 {
            return Ok(());
        }
        let before = ctx.written;
        let sent =
            self.family
                .send_bitmap(ctx, x0, y0, x1 - 1, y1 - 1, &frame.crop(x0, y0, x1, y1));
        self.counters.bytes += ctx.written - before;
        if sent.is_err() {
            self.counters.usb_errors += 1;
        }
        sent
    }

    fn set_brightness(&mut self, percent: i64) -> Result<()> {
        self.brightness = Some(percent);
        match self.ctx.as_mut() {
            Some(ctx) => self.family.send_brightness(ctx, percent.clamp(0, 100)),
            None => Ok(()),
        }
    }

    fn counters(&self) -> Counters {
        self.counters
    }

    fn slow(&self) -> bool {
        true
    }

    fn screen_off(&mut self) -> bool {
        match self.ctx.as_mut() {
            Some(ctx) => self.family.screen_off(ctx).unwrap_or(false),
            None => false,
        }
    }

    fn close(&mut self) {
        if let Some(mut ctx) = self.ctx.take() {
            ctx.close();
        }
    }
}

impl<F: Family> Drop for SerialPanel<F> {
    fn drop(&mut self) {
        self.close();
    }
}

// -- Turing rev. A ------------------------------------------------------------------------------

/// Turing rev. A (turing_rev_a.py): the Turing 3.5" and UsbPCMonitor.
pub struct RevA;

const A_HELLO: u8 = 69;
const A_SCREEN_OFF: u8 = 108;
const A_SCREEN_ON: u8 = 109;
const A_SET_BRIGHTNESS: u8 = 110;
const A_SET_ORIENTATION: u8 = 121;
const A_DISPLAY_BITMAP: u8 = 197;

/// The six bytes of a rev. A command: the rectangle packed ten bits per number.
pub fn rev_a_header(command: u8, x: i64, y: i64, ex: i64, ey: i64) -> [u8; 6] {
    [
        (x >> 2) as u8,
        (((x & 3) << 6) + (y >> 4)) as u8,
        (((y & 15) << 4) + (ex >> 6)) as u8,
        (((ex & 63) << 2) + (ey >> 8)) as u8,
        (ey & 255) as u8,
        command,
    ]
}

/// 0-100 % to the panel's 0 (brightest) - 255 (darkest).
pub fn rev_a_level(percent: i64) -> i64 {
    (255.0 - percent.clamp(0, 100) as f64 / 100.0 * 255.0) as i64
}

impl Family for RevA {
    fn family(&self) -> String {
        lp_i18n::t("Turing rev. A panel")
    }

    fn identify(
        &mut self,
        ctx: &mut Ctx,
        found: &FoundPort,
        configured: bool,
    ) -> Result<&'static PanelModel> {
        ctx.drain_input();
        ctx.write(&[A_HELLO; 6])?;
        let answer = ctx.read(6)?;
        ctx.drain_input();
        let named = match answer.as_slice() {
            [1, 1, 1, 1, 1, 1] => find_model("usbpcmonitor-3.5"),
            [2, 2, 2, 2, 2, 2] => find_model("usbpcmonitor-5"),
            [3, 3, 3, 3, 3, 3] => find_model("usbpcmonitor-7"),
            _ => None,
        };
        if let Some(named) = named {
            return Ok(named);
        }
        if configured || !answer.is_empty() {
            return Ok(found.model);
        }
        Ok(find_model("turing-3.5").unwrap_or(found.model))
    }

    fn opened(&mut self, ctx: &mut Ctx) -> Result<()> {
        // (off if Libre Panel turned it off quitting)
        ctx.write(&rev_a_header(A_SCREEN_ON, 0, 0, 0, 0))
    }

    fn screen_off(&mut self, ctx: &mut Ctx) -> Result<bool> {
        ctx.write(&rev_a_header(A_SCREEN_OFF, 0, 0, 0, 0))?;
        Ok(true)
    }

    fn send_brightness(&mut self, ctx: &mut Ctx, percent: i64) -> Result<()> {
        ctx.write(&rev_a_header(
            A_SET_BRIGHTNESS,
            rev_a_level(percent),
            0,
            0,
            0,
        ))
    }

    fn send_orientation(
        &mut self,
        ctx: &mut Ctx,
        orientation: &str,
        width: i64,
        height: i64,
    ) -> Result<()> {
        let mut command = [0u8; 16];
        command[..6].copy_from_slice(&rev_a_header(A_SET_ORIENTATION, 0, 0, 0, 0));
        command[6] = if orientation == "landscape" { 2 } else { 0 } + 100;
        command[7..11].copy_from_slice(&[
            (width >> 8) as u8,
            (width & 255) as u8,
            (height >> 8) as u8,
            (height & 255) as u8,
        ]);
        ctx.write(&command)
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
        ctx.write(&rev_a_header(A_DISPLAY_BITMAP, x0, y0, x1, y1))?;
        let width = ctx.width();
        ctx.write_chunks(&rgb565(image, true)?, (width * 8) as usize, &[])
    }
}

// -- XuanFang rev. B ----------------------------------------------------------------------------

/// XuanFang rev. B and "flagship" 3.5" (turing_rev_b.py).
pub struct RevB {
    /// brightness 0-255, else only on and off (HELLO tells)
    pub full_range: bool,
    /// the pause after each bitmap (the tests leave it out)
    pub cooldown: Duration,
}

impl Default for RevB {
    fn default() -> RevB {
        RevB {
            full_range: false,
            cooldown: Duration::from_millis(50),
        }
    }
}

const B_HELLO: u8 = 0xCA;
const B_SET_ORIENTATION: u8 = 0xCB;
const B_DISPLAY_BITMAP: u8 = 0xCC;
const B_SET_BRIGHTNESS: u8 = 0xCE;

pub fn rev_b_packet(command: u8, data: &[u8]) -> Vec<u8> {
    let mut out = vec![command];
    let mut body = data.to_vec();
    body.resize(body.len().max(8), 0);
    out.extend_from_slice(&body);
    out.push(command);
    out
}

impl Family for RevB {
    fn family(&self) -> String {
        lp_i18n::t("XuanFang rev. B panel")
    }

    fn identify(
        &mut self,
        ctx: &mut Ctx,
        found: &FoundPort,
        _configured: bool,
    ) -> Result<&'static PanelModel> {
        ctx.drain_input();
        ctx.write(&rev_b_packet(B_HELLO, b"HELLO"))?;
        let answer = ctx.read(10)?;
        ctx.drain_input();
        if answer.is_empty() {
            return Err(DeviceError(tf(
                "the panel on {port} did not answer",
                &[("port", &found.device)],
            )));
        }
        let framed = answer.len() == 10 && answer[0] == B_HELLO && answer[9] == B_HELLO;
        self.full_range = framed && matches!(answer[7], 0x11 | 0x12);
        Ok(found.model)
    }

    fn send_brightness(&mut self, ctx: &mut Ctx, percent: i64) -> Result<()> {
        let level = if self.full_range {
            (percent as f64 / 100.0 * 255.0) as i64
        } else if percent == 0 {
            1
        } else {
            0
        };
        ctx.write(&rev_b_packet(B_SET_BRIGHTNESS, &[level as u8]))
    }

    fn send_orientation(
        &mut self,
        ctx: &mut Ctx,
        orientation: &str,
        _width: i64,
        _height: i64,
    ) -> Result<()> {
        ctx.write(&rev_b_packet(
            B_SET_ORIENTATION,
            &[u8::from(orientation == "landscape")],
        ))
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
        let mut box_ = Vec::with_capacity(8);
        for v in [x0, y0, x1, y1] {
            box_.extend_from_slice(&(v as u16).to_be_bytes());
        }
        ctx.write(&rev_b_packet(B_DISPLAY_BITMAP, &box_))?;
        let width = ctx.width();
        ctx.write_chunks(&rgb565(image, false)?, (width * 8) as usize, &[])?;
        if !self.cooldown.is_zero() {
            std::thread::sleep(self.cooldown);
        }
        Ok(())
    }
}

// -- Kipye rev. D -------------------------------------------------------------------------------

/// Kipye Qiye 3.5" (kipye_rev_d.py).
pub struct RevD;

impl RevD {
    fn send(ctx: &mut Ctx, data: &[u8]) -> Result<()> {
        ctx.write(data)?;
        ctx.drain_input(); // the acknowledgements: not needed, must not pile up
        Ok(())
    }
}

impl Family for RevD {
    fn family(&self) -> String {
        lp_i18n::t("Kipye rev. D panel")
    }

    fn send_brightness(&mut self, ctx: &mut Ctx, percent: i64) -> Result<()> {
        let mut level = vec![67, 67];
        level.extend_from_slice(&((percent * 5) as u16).to_be_bytes());
        Self::send(ctx, &level)?;
        Self::send(ctx, &level) // the panel sometimes misses the first one
    }

    fn send_orientation(
        &mut self,
        ctx: &mut Ctx,
        _orientation: &str,
        _width: i64,
        _height: i64,
    ) -> Result<()> {
        Self::send(ctx, &[67, 72, 0, 0]) // landscape is turned in software
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
        let (mut x0, mut y0, mut x1, mut y1) = (x0, y0, x1, y1);
        let turned;
        let mut image = image;
        if ctx.orientation == Some("landscape") {
            let portrait_width = ctx.model.map_or(0, |m| m.native_width);
            turned = image.transpose(Transpose::Rotate270);
            image = &turned;
            (x0, y0, x1, y1) = (portrait_width - 1 - y1, x0, portrait_width - 1 - y0, x1);
        }
        let mut block = vec![67, 65];
        for v in [x0, x1, y0, y1] {
            block.extend_from_slice(&(v as u16).to_be_bytes());
        }
        Self::send(ctx, &block)?;
        Self::send(ctx, &[68, 0, 0, 0])?;
        let pixels = rgb565(image, false)?;
        for piece in pixels.chunks(63) {
            let mut out = vec![0x50];
            out.extend_from_slice(piece);
            Self::send(ctx, &out)?;
        }
        Self::send(ctx, &[65, 0, 0, 0])
    }
}

// -- WeAct ---------------------------------------------------------------------------------------

/// WeAct Studio Display FS V1, 3.5" and 0.96" (weact.py).
pub struct WeAct;

const W_END: u8 = 0x0A;
const W_SET_ORIENTATION: u8 = 0x02;
const W_SET_BRIGHTNESS: u8 = 0x03;
const W_SET_BITMAP: u8 = 0x05;
const W_SYSTEM_VERSION: u8 = 0x42;
const W_READ: u8 = 0x80;
const W_FADE_MS: u16 = 1000;

impl Family for WeAct {
    fn family(&self) -> String {
        lp_i18n::t("WeAct panel")
    }

    fn identify(
        &mut self,
        ctx: &mut Ctx,
        found: &FoundPort,
        configured: bool,
    ) -> Result<&'static PanelModel> {
        ctx.drain_input();
        ctx.write(&[W_SYSTEM_VERSION | W_READ, W_END])?;
        let _version = ctx.read(19)?;
        ctx.drain_input();
        let serial = found.serial_number.as_deref().unwrap_or("");
        let named = match serial.get(..2) {
            Some("AB") => find_model("weact-3.5"),
            Some("AD") => find_model("weact-0.96"),
            _ => None,
        };
        match named {
            Some(named) if !configured => Ok(named),
            _ => Ok(found.model),
        }
    }

    fn send_brightness(&mut self, ctx: &mut Ctx, percent: i64) -> Result<()> {
        let level = (percent as f64 / 100.0 * 255.0) as i64;
        let mut out = vec![W_SET_BRIGHTNESS, level as u8];
        out.extend_from_slice(&W_FADE_MS.to_le_bytes());
        out.push(b'\n');
        ctx.write(&out)
    }

    fn send_orientation(
        &mut self,
        ctx: &mut Ctx,
        orientation: &str,
        _width: i64,
        _height: i64,
    ) -> Result<()> {
        let turn = if orientation == "landscape" { 2 } else { 0 };
        ctx.write(&[W_SET_ORIENTATION, turn, W_END])
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
        let mut out = vec![W_SET_BITMAP];
        for v in [x0, y0, x1, y1] {
            out.extend_from_slice(&(v as u16).to_le_bytes());
        }
        out.push(b'\n');
        ctx.write(&out)?;
        let width = ctx.width();
        ctx.write_chunks(&rgb565(image, true)?, (width * 4) as usize, &[])
    }
}
