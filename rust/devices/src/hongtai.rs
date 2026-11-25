//! Hongtai / Jungle Leopard cooler screens (devices/hongtai.py): the JL Chill Arc 360 (480x960),
//! the strip of the family (1920x462) and the white-label screens on the same firmware (MSI EZ
//! Display, Thermaltake, ZOTAC and more), a USB serial port (33c3:7788) that takes JPEG
//! pictures; and the MAJESTY 9.16" (33c3:7792, issue #40), the strip on firmware of its own,
//! which this crate drives first: the Python app has no driver for it.
//!
//! The protocol as InfoPanel (GPL-3.0, habibrehmansg/infopanel, JL-PROTOCOL.md; from the
//! vendor's "Jungle Leopard Display" application) documents it:
//!
//! - 2 000 000 baud, no flow control. A command is 55 AA, its length (16 bit, little endian,
//!   the whole frame), the command, its data and the sum of all bytes before (16 bit, little
//!   endian). The answers have the same frame; 06 is answered with JSON (its size, its model).
//! - Opening: FF D9 FF D9 00 00 00 00 clears the panel's reader, then 06 asks what it is.
//! - A picture: 11 (the live picture goes on), then its size (32 bit, little endian), the JPEG
//!   of the panel's size, the sum of both (16 bit), and FF D9 FF D9. The firmware takes up to
//!   about 80 KB.
//! - 11 keeps the live picture: without it for 1.5 s the panel shows its own animation again;
//!   it goes out every second while no picture does. 03 sets the backlight (0-100), 21 lets the
//!   panel go.
//!
//! The MAJESTY as its owner found it on the panel (the vendor's MAJESTY AIO MONITOR 1.0.53 and
//! a driver of their own, issue #40): after 06 it is set up with 15 and 00 03, its answer
//! waited for up to about 3 s; its backlight goes to 95, where the vendor's application keeps
//! it; then the live picture. Its brightness goes into the pictures, as that application puts
//! it there: each is dimmed before it is made a JPEG.
//!
//! The MAJESTY goes dark by itself now and then while it is shown pictures, its backlight off
//! (issue #54): it says FF (55 AA 08 00 FF FF 05 03) the moment it does, and again for each
//! picture while it stays so. Setting it up again brings it back, so it is opened again then (as
//! a change of the brightness in the app opens it), on a port at most once a minute.
//!
//! Never sent: 01 (restart), 0C (firmware update), 14, 15 (but the MAJESTY's setting up), 20
//! (the region preset), 23 (the serial number), 25 (the pump's motor), 26.

use std::collections::BTreeMap;
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use lp_pixels::{Filter, Image, Mode, Transpose};
use lp_py::json::ErrorKind;
use lp_py::{Dict, Value};

use crate::base::{dimmed, Counters, DeviceError, Result};
use crate::link::Link;

pub const CMD_LIGHT: u8 = 0x03;
pub const CMD_INFO: u8 = 0x06;
pub const CMD_LIVE: u8 = 0x11;
/// The MAJESTY's setting up (with MAJESTY_SETUP), never sent to another of the family.
pub const CMD_SETUP: u8 = 0x15;
pub const CMD_CLOSE: u8 = 0x21;
pub const NEVER: [u8; 8] = [0x01, 0x0C, 0x14, 0x15, 0x20, 0x23, 0x25, 0x26];
pub const RESET: [u8; 8] = [0xFF, 0xD9, 0xFF, 0xD9, 0, 0, 0, 0];
pub const END: [u8; 4] = [0xFF, 0xD9, 0xFF, 0xD9];
pub const MAX_JPEG: usize = 80 * 1024;
pub const QUALITIES: [i64; 7] = [90, 80, 70, 60, 50, 40, 30];
pub const BAUD: u32 = 2_000_000;
/// The models the family's size names (the shorter side first), where the panel says it.
pub const BY_SIZE: [((i64, i64), &str); 2] = [
    ((480, 960), "hongtai-480x960"),
    ((462, 1920), "hongtai-1920x462"),
];
/// The family's own USB ids, and the MAJESTY's.
pub const FAMILY_USB: (u16, u16) = (0x33C3, 0x7788);
pub const MAJESTY_USB: (u16, u16) = (0x33C3, 0x7792);
pub const MAJESTY_MODEL: &str = "majesty-9.16";
/// What the MAJESTY is set up with (after 15), and the backlight it is kept at.
pub const MAJESTY_SETUP: [u8; 2] = [0x00, 0x03];
pub const MAJESTY_LIGHT: u8 = 95;
/// What the MAJESTY says when it has gone dark by itself (issue #54: 55 AA 08 00 FF FF 05 03,
/// the moment its backlight went off, then again for each picture while it stays dark).
pub const CMD_STOPPED: u8 = 0xFF;
/// The MAJESTY opened again because it went dark: at most this often on a port.
const REVIVE_EVERY: Duration = Duration::from_secs(60);

/// A frame as the panel takes it: 55 AA, length, command, data, sum. A command never sent is
/// refused, as is data too long for the length.
pub fn frame(command: u8, data: &[u8]) -> Result<Vec<u8>> {
    if NEVER.contains(&command) {
        return Err(DeviceError(format!("command {command:#04x} is never sent")));
    }
    framed(command, data)
}

fn framed(command: u8, data: &[u8]) -> Result<Vec<u8>> {
    let length = u16::try_from(data.len() + 7)
        .map_err(|_| DeviceError("'H' format requires 0 <= number <= 65535".into()))?;
    let mut body = vec![0x55, 0xAA];
    body.extend_from_slice(&length.to_le_bytes());
    body.push(command);
    body.extend_from_slice(data);
    let sum = body.iter().map(|&b| u32::from(b)).sum::<u32>() as u16;
    body.extend_from_slice(&sum.to_le_bytes());
    Ok(body)
}

/// The MAJESTY's setting up: 15 with 00 03 (the one 15 that is sent).
pub fn majesty_setup() -> Vec<u8> {
    framed(CMD_SETUP, &MAJESTY_SETUP).expect("(two bytes of data)")
}

/// A JPEG in its envelope, with the end marker.
pub fn picture(jpeg: &[u8]) -> Result<Vec<u8>> {
    let size = u32::try_from(jpeg.len())
        .map_err(|_| DeviceError("'I' format requires 0 <= number <= 4294967295".into()))?;
    let mut sized = size.to_le_bytes().to_vec();
    sized.extend_from_slice(jpeg);
    let sum = sized.iter().map(|&b| u64::from(b)).sum::<u64>() as u16;
    sized.extend_from_slice(&sum.to_le_bytes());
    sized.extend_from_slice(&END);
    Ok(sized)
}

/// The picture as a JPEG the firmware takes: the best quality that keeps within MAX_JPEG (the
/// lowest where none does).
pub fn jpeg(native: &Image) -> Result<Vec<u8>> {
    let mut data = Vec::new();
    for quality in QUALITIES {
        data = lp_decode::save_jpeg(native, quality)?;
        if data.len() <= MAX_JPEG {
            break;
        }
    }
    Ok(data)
}

/// _answer: the data of the panel's answer to 06 as JSON, an object; empty for none (no frame
/// within 64 bytes, one cut short, no JSON or JSON of another kind). JSON too deep fails, as
/// Python's RecursionError does. (A half of a surrogate pair, which Python's json reads into a
/// str, is no JSON here: Rust's str cannot hold it.)
pub fn answer(link: &mut dyn Link) -> Result<Dict> {
    let mut found = false;
    for _ in 0..64 {
        let byte = link.read(1)?;
        if byte.is_empty() {
            return Ok(Dict::default());
        }
        if byte[0] == 0x55 && link.read(1)? == [0xAA] {
            found = true;
            break;
        }
    }
    if !found {
        return Ok(Dict::default());
    }
    let head = link.read(3)?;
    if head.len() < 3 {
        return Ok(Dict::default());
    }
    let length = usize::from(u16::from_le_bytes([head[0], head[1]]));
    let data = link.read(length.saturating_sub(7))?;
    link.read(2)?; // (the sum)
    let text = String::from_utf8_lossy(&data);
    match lp_py::json::loads(text.trim_matches('\0')) {
        Ok(Value::Dict(info)) => Ok(info),
        Ok(_) => Ok(Dict::default()),
        Err(e) if e.kind == ErrorKind::Recursion => Err(DeviceError(e.msg)),
        Err(_) => Ok(Dict::default()),
    }
}

/// int(info.get(key) or 0): Python's int() of what the panel said, its errors where it raises.
fn int_of(info: &Dict, key: &str) -> Result<i64> {
    let value = match info.get(key) {
        Some(v) if v.truthy() => v,
        _ => return Ok(0),
    };
    let too_large = || DeviceError("Python int too large to convert to C ssize_t".into());
    match value {
        Value::Bool(b) => Ok(i64::from(*b)),
        Value::Int(n) => i64::try_from(n).map_err(|_| too_large()),
        Value::Float(x) if x.is_nan() => {
            Err(DeviceError("cannot convert float NaN to integer".into()))
        }
        Value::Float(x) if x.is_infinite() => Err(DeviceError(
            "cannot convert float infinity to integer".into(),
        )),
        Value::Float(x) => {
            let whole = x.trunc();
            if !(i64::MIN as f64..i64::MAX as f64).contains(&whole) {
                return Err(too_large()); // (beyond -2^63 .. 2^63)
            }
            Ok(whole as i64)
        }
        Value::Str(s) => match lp_py::int_of_str(s) {
            Some(n) => i64::try_from(n).map_err(|_| too_large()),
            None => Err(DeviceError(format!(
                "invalid literal for int() with base 10: {}",
                lp_py::repr(s)
            ))),
        },
        Value::List(_) => Err(DeviceError(
            "int() argument must be a string, a bytes-like object or a real number, not 'list'"
                .into(),
        )),
        Value::Dict(_) => Err(DeviceError(
            "int() argument must be a string, a bytes-like object or a real number, not 'dict'"
                .into(),
        )),
        Value::None => Ok(0),
    }
}

/// str() of what the panel calls itself, its first 40 characters (for the log).
fn name_of(info: &Dict) -> String {
    let name = match info.get("model") {
        None => "?".to_string(),
        Some(Value::Str(s)) => s.clone(),
        Some(other) => other.repr(),
    };
    name.chars().take(40).collect()
}

/// Which panel of the family is on the port.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// on the family's own firmware (33c3:7788)
    Family,
    /// the MAJESTY 9.16" (33c3:7792): set up first, its brightness in its pictures
    Majesty,
}

impl Kind {
    /// The kind of panel a port's USB ids name.
    pub fn of_usb(ids: (u16, u16)) -> Option<Kind> {
        match ids {
            FAMILY_USB => Some(Kind::Family),
            MAJESTY_USB => Some(Kind::Majesty),
            _ => None,
        }
    }
}

/// How long the driver waits: the panel's own times (PANEL), the tests' shorter ones.
#[derive(Clone, Copy, Debug)]
pub struct Timing {
    /// before the opening's FF D9 FF D9 and after it (time.sleep(0.2))
    pub settle: Duration,
    /// 11 again while nothing went out (LIVE_EVERY_S)
    pub live_every: Duration,
    /// the MAJESTY's answer to its setting up, waited for
    pub setup_answer: Duration,
}

impl Timing {
    pub const PANEL: Timing = Timing {
        settle: Duration::from_millis(200),
        live_every: Duration::from_secs(1),
        setup_answer: Duration::from_secs(3),
    };
}

/// What the panel said when it was opened.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Identity {
    /// its size as it lies (the strip's portrait), as it said it, else the model's
    pub native: (i64, i64),
    /// what it calls itself, for the log
    pub name: String,
    /// the model of the family its size names, where it names one (BY_SIZE; the MAJESTY is
    /// its own)
    pub named: Option<&'static str>,
    /// the command of the MAJESTY's answer to its setting up, None where none came in time
    pub set_up: Option<u8>,
}

struct State<L> {
    link: Option<L>,
    last_sent: Instant,
    fault: Option<DeviceError>,
    stop: bool,
    counters: Counters,
    heard: Heard,
}

/// The longest frame of the panel's that is waited for (and the most read at once).
const HEARD_KEPT: usize = 16 * 1024;
/// How much of a frame the log shows.
const HEARD_SHOWN: usize = 24;

/// What the panel sends while it is shown pictures, when it is asked nothing (issue #54: the
/// MAJESTY went dark now and then with nothing in the log): read as it comes, so that it
/// never fills the port's buffer; its frames counted by their command, each new one noted for
/// the log with its first bytes, and bytes that are no frame noted once.
#[derive(Default)]
struct Heard {
    /// the start of a frame not yet whole
    pending: Vec<u8>,
    /// the frames by their command, and bytes that were no frame
    counts: BTreeMap<u8, u64>,
    stray: u64,
    /// for the log, not yet said
    notes: Vec<String>,
}

fn hex(data: &[u8]) -> String {
    let shown: Vec<String> = data
        .iter()
        .take(HEARD_SHOWN)
        .map(|b| format!("{b:02X}"))
        .collect();
    let more = if data.len() > HEARD_SHOWN { " …" } else { "" };
    format!("{}{more}", shown.join(" "))
}

impl Heard {
    /// `data`, as it came from the panel on `port`, taken in.
    fn take_in(&mut self, data: &[u8], port: &str) {
        self.pending.extend_from_slice(data);
        loop {
            let start = (self.pending.windows(2)).position(|two| two == [0x55, 0xAA]);
            // (with no start, a last 55 may be the first byte of one)
            let kept = usize::from(self.pending.last() == Some(&0x55));
            let skipped = start.unwrap_or(self.pending.len() - kept);
            if skipped > 0 {
                self.stray(skipped, port);
            }
            if start.is_none() || self.pending.len() < 5 {
                break;
            }
            let length = usize::from(u16::from_le_bytes([self.pending[2], self.pending[3]]));
            if !(7..=HEARD_KEPT).contains(&length) {
                self.stray(1, port); // (no frame's length: its 55 is no frame's start)
                continue;
            }
            if self.pending.len() < length {
                break;
            }
            let command = self.pending[4];
            let count = self.counts.entry(command).or_default();
            *count += 1;
            if *count == 1 {
                self.notes.push(format!(
                    "the panel on {port} said {command:02X} unasked ({length} bytes: {})",
                    hex(&self.pending[..length])
                ));
            }
            self.pending.drain(..length);
        }
    }

    /// How many frames of `command` came.
    fn count(&self, command: u8) -> u64 {
        self.counts.get(&command).copied().unwrap_or(0)
    }

    fn stray(&mut self, n: usize, port: &str) {
        if self.stray == 0 {
            let said = hex(&self.pending[..n]);
            self.notes.push(format!(
                "the panel on {port} sent bytes that are no frame: {said}"
            ));
        }
        self.stray += n as u64;
        self.pending.drain(..n);
    }

    /// What was heard in all, for the log when the panel is let go; None for nothing.
    fn summary(&self, port: &str) -> Option<String> {
        let mut parts: Vec<String> = (self.counts.iter())
            .map(|(command, n)| format!("{command:02X} ×{n}"))
            .collect();
        if self.stray > 0 {
            parts.push(format!("{} bytes that were no frame", self.stray));
        }
        (!parts.is_empty())
            .then(|| format!("the panel on {port} said unasked: {}", parts.join(", ")))
    }
}

/// Whether the panel on `port` may be opened again now because it went dark: not where it was
/// within REVIVE_EVERY (a panel that stays dark is not opened again and again).
fn revive(port: &str) -> bool {
    static REVIVED: Mutex<BTreeMap<String, Instant>> = Mutex::new(BTreeMap::new());
    let mut revived = REVIVED.lock().unwrap_or_else(|e| e.into_inner());
    let now = Instant::now();
    if revived
        .get(port)
        .is_some_and(|at| now.duration_since(*at) < REVIVE_EVERY)
    {
        return false;
    }
    revived.insert(port.to_string(), now);
    true
}

struct Shared<L> {
    state: Mutex<State<L>>,
    wake: Condvar,
}

impl<L: Link> Shared<L> {
    fn lock(&self) -> MutexGuard<'_, State<L>> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }
}

impl<L: Link> State<L> {
    /// _write: `data` to the panel, counted; an error counted too (the main loop connects
    /// again).
    fn write(&mut self, data: &[u8]) -> Result<()> {
        let Some(link) = self.link.as_mut() else {
            return Err(DeviceError("panel not open".into()));
        };
        match link.write(data) {
            Ok(()) => {
                self.counters.bytes += data.len() as u64;
                self.last_sent = Instant::now();
                Ok(())
            }
            Err(e) => {
                self.counters.usb_errors += 1;
                Err(e)
            }
        }
    }
}

/// A Hongtai screen or the MAJESTY on its link (HongtaiDisplay): opened, it keeps its live
/// picture (11 every second while no picture goes out, from a thread of its own); it shows
/// pictures, sets the brightness and lets the panel go.
pub struct HongtaiDisplay<L: Link + 'static> {
    shared: Arc<Shared<L>>,
    kind: Kind,
    timing: Timing,
    /// the panel's size as it lies
    pub native: (i64, i64),
    /// the brightness last set (None: never)
    pub brightness: Option<i64>,
    /// the MAJESTY's last picture as it went out before it was dimmed
    last: Option<Image>,
    keeper: Option<thread::JoinHandle<()>>,
    /// its port, for the log
    port: String,
}

impl<L: Link + 'static> HongtaiDisplay<L> {
    /// The panel on `link` opened: asked what it is, the MAJESTY set up, its live picture kept
    /// from now on. `native` is its model's size (480x960 where there is none); `port` names
    /// it in messages. On an error the link is let go.
    pub fn open(
        link: L,
        kind: Kind,
        native: Option<(i64, i64)>,
        port: &str,
        timing: Timing,
    ) -> Result<(HongtaiDisplay<L>, Identity)> {
        let shared = Arc::new(Shared {
            state: Mutex::new(State {
                link: Some(link),
                last_sent: Instant::now(),
                fault: None,
                stop: false,
                counters: Counters::default(),
                heard: Heard::default(),
            }),
            wake: Condvar::new(),
        });
        let mut display = HongtaiDisplay {
            shared,
            kind,
            timing,
            native: native.unwrap_or((480, 960)),
            brightness: None,
            last: None,
            keeper: None,
            port: port.to_string(),
        };
        let identity = match display.identify(port) {
            Ok(identity) => identity,
            Err(e) => {
                display.let_go();
                return Err(e);
            }
        };
        // (for the log: what it calls itself and its size, as it said them)
        let (w, h) = identity.native;
        display.shared.lock().heard.notes.push(format!(
            "the panel on {port} calls itself {:?}, {w}x{h}",
            identity.name
        ));
        display.start_keeper();
        Ok((display, identity))
    }

    /// identify: FF D9 FF D9 to clear the panel's reader, then 06 and its answer; the
    /// MAJESTY's setting up after it.
    fn identify(&mut self, port: &str) -> Result<Identity> {
        thread::sleep(self.timing.settle);
        let mut state = self.shared.lock();
        let link = state.link.as_mut().expect("(open)");
        link.write(&RESET)?;
        drop(state);
        thread::sleep(self.timing.settle);
        let mut state = self.shared.lock();
        let link = state.link.as_mut().expect("(open)");
        link.drain_input();
        link.write(&frame(CMD_INFO, &[])?)?;
        let info = answer(link)?;
        if info.is_empty() {
            let message = lp_i18n::t("the panel on {port} does not say what it is");
            return Err(DeviceError(message.replace("{port}", port)));
        }
        let (width, height) = (int_of(&info, "width")?, int_of(&info, "height")?);
        if width != 0 && height != 0 {
            self.native = (width, height);
        }
        let (w, h) = self.native;
        let named = match self.kind {
            Kind::Family => BY_SIZE
                .iter()
                .find(|(size, _)| *size == (w.min(h), w.max(h)))
                .map(|&(_, id)| id),
            Kind::Majesty => None, // (its own model, whatever its size)
        };
        let mut identity = Identity {
            native: self.native,
            name: name_of(&info),
            named,
            set_up: None,
        };
        if self.kind == Kind::Majesty {
            identity.set_up = self.set_up(&mut state)?;
        }
        Ok(identity)
    }

    /// The MAJESTY set up: 15 with 00 03, its answer waited for (up to Timing.setup_answer),
    /// the backlight at 95, the live picture on. (Part of opening, as the asking is: not
    /// counted.)
    fn set_up(&self, state: &mut State<L>) -> Result<Option<u8>> {
        let link = state.link.as_mut().expect("(open)");
        link.write(&majesty_setup())?;
        let answered = Self::answer_frame(link, Instant::now() + self.timing.setup_answer)?;
        link.write(&frame(CMD_LIGHT, &[MAJESTY_LIGHT])?)?;
        link.write(&frame(CMD_LIVE, &[])?)?;
        Ok(answered)
    }

    /// A frame of the panel's (55 AA, length, command, data, sum): its command; None where
    /// none came before `deadline`.
    fn answer_frame(link: &mut L, deadline: Instant) -> Result<Option<u8>> {
        while Instant::now() < deadline {
            let byte = link.read(1)?;
            if byte != [0x55] || link.read(1)? != [0xAA] {
                continue;
            }
            let head = link.read(3)?;
            if head.len() < 3 {
                return Ok(None);
            }
            let length = usize::from(u16::from_le_bytes([head[0], head[1]]));
            link.read(length.saturating_sub(5))?; // (its data and sum)
            return Ok(Some(head[2]));
        }
        Ok(None)
    }

    fn start_keeper(&mut self) {
        let shared = Arc::clone(&self.shared);
        let port = self.port.clone();
        let every = self.timing.live_every;
        let tick = (every / 2).min(Duration::from_millis(250));
        let live = frame(CMD_LIVE, &[]).expect("(11 is sent)");
        let kind = self.kind;
        let mut state = self.shared.lock();
        state.stop = false;
        state.last_sent = Instant::now();
        drop(state);
        let spawned = thread::Builder::new()
            .name("hongtai-live".into())
            .spawn(move || loop {
                let state = shared.lock();
                let (mut state, _) = shared
                    .wake
                    .wait_timeout_while(state, tick, |s| !s.stop)
                    .unwrap_or_else(|e| e.into_inner());
                if state.stop {
                    return;
                }
                // what the panel sent, read (the read takes what came: it does not wait)
                let State { link, heard, .. } = &mut *state;
                let stopped = heard.count(CMD_STOPPED);
                if let Some(link) = link.as_mut() {
                    if let Some(n) = link.waiting().filter(|n| *n > 0) {
                        match link.read(n.min(HEARD_KEPT)) {
                            Ok(data) => heard.take_in(&data, &port),
                            Err(e) => {
                                state.fault = Some(e);
                                return;
                            }
                        }
                    }
                }
                // the MAJESTY gone dark by itself: opened again, as a change of the brightness
                // in the app does, which brings it back (the next picture says so)
                if kind == Kind::Majesty && heard.count(CMD_STOPPED) > stopped && revive(&port) {
                    state.fault = Some(DeviceError(format!(
                        "the panel on {port} went dark (it said FF): it is opened again"
                    )));
                    return;
                }
                if state.last_sent.elapsed() < every || state.link.is_none() {
                    continue;
                }
                if let Err(e) = state.write(&live) {
                    state.fault = Some(e);
                    return;
                }
            });
        self.keeper = spawned.ok();
    }

    /// set_brightness: 0 to 100. The family's backlight is set at once; the MAJESTY's
    /// picture on show goes out again at the new brightness.
    pub fn set_brightness(&mut self, percent: i64) -> Result<()> {
        let percent = percent.clamp(0, 100);
        self.brightness = Some(percent);
        let mut state = self.shared.lock();
        if state.link.is_none() {
            return Ok(());
        }
        match self.kind {
            Kind::Family => state.write(&frame(CMD_LIGHT, &[percent as u8])?),
            Kind::Majesty => match &self.last {
                Some(native) => {
                    let data = Self::picture_of(native, percent)?;
                    state.write(&data)
                }
                None => Ok(()),
            },
        }
    }

    /// 11 and the picture at `percent` of its brightness.
    fn picture_of(native: &Image, percent: i64) -> Result<Vec<u8>> {
        let mut data = frame(CMD_LIVE, &[])?;
        data.extend(picture(&jpeg(&dimmed(native, percent)?)?)?);
        Ok(data)
    }

    /// show: `image` turned into the panel's way round where it lies the other way, made the
    /// panel's size (Lanczos), in RGB, as a JPEG (the MAJESTY's dimmed first). A fault of the
    /// keeping thread comes out here.
    pub fn show(&mut self, image: &Image) -> Result<()> {
        {
            let mut state = self.shared.lock();
            if state.link.is_none() {
                return Err(DeviceError("panel not open".into()));
            }
            // (a fault known already: before the picture is made for nothing)
            if let Some(fault) = state.fault.take() {
                return Err(fault);
            }
        }
        let (width, height) = self.native;
        let mut image = std::borrow::Cow::Borrowed(image);
        if (image.width() > image.height()) != (width > height) {
            image = std::borrow::Cow::Owned(image.transpose(Transpose::Rotate270));
        }
        if (image.width() as i64, image.height() as i64) != self.native {
            if width <= 0 || height <= 0 {
                return Err(DeviceError("height and width must be > 0".into()));
            }
            let resized =
                image.resize(width as usize, height as usize, Filter::Lanczos, None, None)?;
            image = std::borrow::Cow::Owned(resized);
        }
        let native = image.convert(Mode::RGB)?;
        let data = match self.kind {
            Kind::Family => {
                let mut data = frame(CMD_LIVE, &[])?;
                data.extend(picture(&jpeg(&native)?)?);
                data
            }
            Kind::Majesty => Self::picture_of(&native, self.brightness.unwrap_or(100))?,
        };
        let mut state = self.shared.lock();
        if let Some(fault) = state.fault.take() {
            return Err(fault);
        }
        if self.kind == Kind::Majesty {
            self.last = Some(native);
        }
        state.write(&data)
    }

    /// What it counted.
    pub fn counters(&self) -> Counters {
        self.shared.lock().counters
    }

    /// What went wrong while no picture went out (the keeping thread found it: the panel gone,
    /// the MAJESTY gone dark), taken; None for nothing.
    pub fn take_fault(&mut self) -> Option<DeviceError> {
        self.shared.lock().fault.take()
    }

    /// What it noted for the log since it was last asked (what the panel sent unasked).
    pub fn notes(&mut self) -> Vec<String> {
        std::mem::take(&mut self.shared.lock().heard.notes)
    }

    fn stop_keeper(&mut self) {
        self.shared.lock().stop = true;
        self.shared.wake.notify_all();
        if let Some(keeper) = self.keeper.take() {
            let _ = keeper.join();
        }
    }

    fn let_go(&mut self) {
        if let Some(mut link) = self.shared.lock().link.take() {
            link.close();
        }
    }

    /// close: the keeping thread stopped, 21 (the panel shows its own animation again), the
    /// link let go.
    pub fn close(&mut self) {
        self.stop_keeper();
        {
            let mut state = self.shared.lock();
            if let Some(link) = state.link.as_mut() {
                let _ = link.write(&frame(CMD_CLOSE, &[]).expect("(21 is sent)"));
            }
            if let Some(summary) = state.heard.summary(&self.port) {
                state.heard.notes.push(summary);
                state.heard.counts.clear();
                state.heard.stray = 0;
            }
        }
        self.let_go();
        self.last = None;
    }
}

impl<L: Link + 'static> Drop for HongtaiDisplay<L> {
    fn drop(&mut self) {
        if self.keeper.is_some() || self.shared.lock().link.is_some() {
            self.close();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Heard;

    #[test]
    fn what_the_panel_sends_unasked_is_taken_in_frame_by_frame() {
        let mut heard = Heard::default();
        let live = [0x55, 0xAA, 0x07, 0x00, 0x11, 0x17, 0x01];
        // (a frame in two parts, after bytes that are no frame)
        heard.take_in(&[0x01, 0x02], "COM6");
        heard.take_in(&live[..3], "COM6");
        assert_eq!(heard.counts.len(), 0);
        heard.take_in(&live[3..], "COM6");
        heard.take_in(&live, "COM6");
        assert_eq!(heard.counts.get(&0x11), Some(&2));
        assert_eq!(heard.stray, 2);
        assert_eq!(
            heard.notes,
            [
                "the panel on COM6 sent bytes that are no frame: 01 02",
                "the panel on COM6 said 11 unasked (7 bytes: 55 AA 07 00 11 17 01)",
            ]
        );
        // (a length no frame has: its 55 AA is no frame's start)
        heard.take_in(&[0x55, 0xAA, 0x02, 0x00, 0x11], "COM6");
        assert_eq!(heard.stray, 7);
        assert!(heard.pending.is_empty());
        assert_eq!(
            heard.summary("COM6").as_deref(),
            Some("the panel on COM6 said unasked: 11 ×2, 7 bytes that were no frame")
        );
        assert_eq!(Heard::default().summary("COM6"), None);
    }
}

#[cfg(test)]
mod gone_dark {
    use std::collections::VecDeque;

    use super::*;

    /// A panel the tests make up: what was written to it, and what it says (its answer to 06,
    /// to the MAJESTY's setting up, and what a test puts in).
    #[derive(Clone, Default)]
    struct Made {
        input: Arc<Mutex<VecDeque<u8>>>,
    }

    impl Link for Made {
        fn write(&mut self, data: &[u8]) -> Result<()> {
            let mut input = self.input.lock().unwrap();
            if data == frame(CMD_INFO, &[]).unwrap() {
                let said = br#"{"width": 462, "height": 1920, "model": "made up"}"#;
                input.extend(framed(CMD_INFO, said).unwrap());
            }
            if data == majesty_setup() {
                input.extend([0x55, 0xAA, 0x07, 0x00, 0x15, 0x1B, 0x01]);
            }
            Ok(())
        }
        fn read(&mut self, size: usize) -> Result<Vec<u8>> {
            let mut input = self.input.lock().unwrap();
            let n = size.min(input.len());
            Ok(input.drain(..n).collect())
        }
        fn drain_input(&mut self) {
            self.input.lock().unwrap().clear();
        }
        fn waiting(&mut self) -> Option<usize> {
            Some(self.input.lock().unwrap().len())
        }
        fn close(&mut self) {}
    }

    /// What the MAJESTY of issue #54 said the moment it went dark.
    const DARK: [u8; 8] = [0x55, 0xAA, 0x08, 0x00, 0xFF, 0xFF, 0x05, 0x03];

    fn opened(kind: Kind, port: &str) -> (HongtaiDisplay<Made>, Made) {
        let made = Made::default();
        let timing = Timing {
            settle: Duration::from_millis(1),
            live_every: Duration::from_millis(100),
            setup_answer: Duration::from_millis(200),
        };
        let (mut display, _) =
            HongtaiDisplay::open(made.clone(), kind, None, port, timing).unwrap();
        // (what it said it is, for the log)
        let said = format!("the panel on {port} calls itself \"made up\", 462x1920");
        assert_eq!(display.notes(), [said]);
        (display, made)
    }

    /// The panel's words, once its keeper has read what it said (2 s at most).
    fn heard(display: &mut HongtaiDisplay<Made>) -> Vec<String> {
        let until = Instant::now() + Duration::from_secs(2);
        let mut notes = Vec::new();
        while notes.is_empty() && Instant::now() < until {
            thread::sleep(Duration::from_millis(20));
            notes = display.notes();
        }
        notes
    }

    #[test]
    fn a_majesty_that_went_dark_is_opened_again_at_most_once_a_minute() {
        let port = "a port of this test";
        let frame = Image::new(Mode::RGB, 1920, 462, &[200, 40, 40]);
        let (mut display, made) = opened(Kind::Majesty, port);
        made.input.lock().unwrap().extend(DARK);
        assert_eq!(
            heard(&mut display),
            [format!(
                "the panel on {port} said FF unasked (8 bytes: 55 AA 08 00 FF FF 05 03)"
            )]
        );
        // (the next picture says so, and the app opens it again)
        let why = display.show(&frame).unwrap_err();
        assert_eq!(
            why.0,
            format!("the panel on {port} went dark (it said FF): it is opened again")
        );
        display.close();
        // dark again at once: not opened again and again
        let (mut again, made) = opened(Kind::Majesty, port);
        made.input.lock().unwrap().extend(DARK);
        assert_eq!(heard(&mut again).len(), 1);
        assert!(again.shared.lock().fault.is_none());
        again.close();
    }

    #[test]
    fn a_panel_of_the_family_is_not_opened_again_for_what_it_says() {
        let port = "a port of the family's test";
        let (mut display, made) = opened(Kind::Family, port);
        made.input.lock().unwrap().extend(DARK);
        assert_eq!(heard(&mut display).len(), 1);
        assert!(display.shared.lock().fault.is_none());
        display.close();
    }
}
