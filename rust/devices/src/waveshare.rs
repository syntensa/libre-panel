//! Waveshare's USB monitors (the 2.8" and its family): a small Linux computer behind a serial
//! port over USB, which draws the themes on its own card (PCMonitorLinux) and takes from the
//! PC only the values to show in them (devices/waveshare.py). Libre Panel sends those
//! (lp-services' waveshare), with the brightness and the screen turned upside down, and
//! switches between the themes on the card; it writes nothing on the card.
//!
//! The protocol, as Derik-for-real24 documented it in the wsmonitor project (NOTICE.md):
//! every message is a frame
//!
//! ```text
//! "AA551234 FIXEDCMDHEAD "  22 bytes
//! type, command, length, CRC-32 of the payload   four little-endian uint32
//! payload
//! ```
//!
//! with type 0 from the PC and 2 for the display's answer (it answers every command). Most
//! payloads are a `QMap<QString, QString>` as Qt's QDataStream writes it: the count, then
//! each key and value as a big-endian byte length and UTF-16BE text. Brightness takes the
//! number as plain text, 0 to 255.
//!
//! The display has commands that write files on its card and delete themes; this module has
//! none of them. A port is the display only once it answered the handshake (`FIND_DEVICE`)
//! with a frame of its own: its ids (`1d6b:0104`, or `1234:5678` that other devices use too)
//! only say where to ask.

use indexmap::IndexMap;

use crate::base::{place_taken, DeviceError, Result};
use crate::link::Link;

pub const MAGIC: &[u8; 22] = b"AA551234 FIXEDCMDHEAD ";
/// type, command, length, crc
const HEAD: usize = 16;
pub const TO_DISPLAY: u32 = 0;
pub const ANSWER: u32 = 2;
/// a longer length in a head is not the display's
const MAX_PAYLOAD: u32 = 1 << 20;

// the commands Libre Panel sends: none of them writes on the card (RELOAD_THEME shows a theme
// that is there already, GET_THEME lists them)
pub const FIND_DEVICE: u32 = 0;
pub const SYSTEM_DATA: u32 = 1;
pub const RELOAD_THEME: u32 = 2;
pub const GET_THEME: u32 = 3;
pub const SET_BRIGHTNESS: u32 = 4;
pub const GET_VERSION: u32 = 8;
pub const SET_CANVAS_FLIP: u32 = 9;
pub const GET_SCREEN_MESSAGE: u32 = 10;
const COMMANDS: [u32; 8] = [
    FIND_DEVICE,
    SYSTEM_DATA,
    RELOAD_THEME,
    GET_THEME,
    SET_BRIGHTNESS,
    GET_VERSION,
    SET_CANVAS_FLIP,
    GET_SCREEN_MESSAGE,
];
/// where the card keeps its themes (GET_THEME names each by path)
const THEMES_FOLDER: &str = "/data/theme/";
const THEME_SUFFIX: &str = ".Theme";

// where the display may be: the USB gadget's own ids (with its product's name), and the
// placeholder ids some units have, which other devices use too
const GADGET_IDS: (u16, u16) = (0x1D6B, 0x0104);
const GADGET_PRODUCT: &str = "Virtual Serial Port";
const PLACEHOLDER_IDS: (u16, u16) = (0x1234, 0x5678);

/// the longest the display takes to answer
pub const ANSWER_S: f64 = 3.0;
/// the display's brightest backlight (Waveshare's program sends 0 to 255)
const BRIGHTEST: f64 = 255.0;

fn crc32(data: &[u8]) -> u32 {
    let mut crc = flate2::Crc::new();
    crc.update(data);
    crc.sum()
}

/// frame: a whole frame of `command` with `payload`.
pub fn frame(command: u32, payload: &[u8], kind: u32) -> Vec<u8> {
    let mut out = MAGIC.to_vec();
    let length = u32::try_from(payload.len()).unwrap_or(u32::MAX);
    for word in [kind, command, length, crc32(payload)] {
        out.extend_from_slice(&word.to_le_bytes());
    }
    out.extend_from_slice(payload);
    out
}

fn utf16(text: &str) -> Vec<u8> {
    text.encode_utf16().flat_map(u16::to_be_bytes).collect()
}

/// qmap: `values` as Qt 5's QDataStream writes a `QMap<QString, QString>`: from the last key
/// to the first, in the order of their UTF-16 code units, as the themes Waveshare's program
/// saves have them (the display does not mind; the frames are then the vendor's to the
/// byte).
pub fn qmap(values: &IndexMap<String, String>) -> Vec<u8> {
    let mut keys: Vec<&String> = values.keys().collect();
    keys.sort_by_cached_key(|key| utf16(key));
    let count = u32::try_from(values.len()).unwrap_or(u32::MAX);
    let mut out = count.to_be_bytes().to_vec();
    for key in keys.into_iter().rev() {
        for text in [key, &values[key]] {
            let data = utf16(text);
            out.extend_from_slice(&u32::try_from(data.len()).unwrap_or(u32::MAX).to_be_bytes());
            out.extend_from_slice(&data);
        }
    }
    out
}

/// A map as the display tells one: a null string is None.
pub type Told = IndexMap<String, Option<String>>;

/// read_qmap: a `QMap<QString, QString>` read back (a null string is None); an error (what
/// Python's ValueError says) if it is not one.
pub fn read_qmap(payload: &[u8]) -> std::result::Result<Told, String> {
    let short = |at: usize| format!("unpack_from requires a buffer of at least {} bytes", at + 4);
    let word = |at: usize| -> std::result::Result<u32, String> {
        let bytes = payload.get(at..at + 4).ok_or_else(|| short(at))?;
        Ok(u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
    };
    let count = word(0)?;
    let (mut at, mut out) = (4, Told::new());
    for _ in 0..count {
        let mut pair: Vec<Option<String>> = Vec::with_capacity(2);
        for _ in 0..2 {
            let size = word(at)? as usize;
            at += 4;
            if size == 0xFFFF_FFFF {
                pair.push(None);
                continue;
            }
            let bytes = payload
                .get(at..at + size)
                .filter(|_| size.is_multiple_of(2));
            let bytes = bytes.ok_or("a string runs past the payload")?;
            let units: Vec<u16> = bytes
                .chunks_exact(2)
                .map(|pair| u16::from_be_bytes([pair[0], pair[1]]))
                .collect();
            let text = String::from_utf16(&units).map_err(|_| "illegal UTF-16 surrogate")?;
            pair.push(Some(text));
            at += size;
        }
        let value = pair.pop().flatten();
        out.insert(pair.pop().flatten().unwrap_or_default(), value);
    }
    if at != payload.len() {
        return Err("bytes after the map".into());
    }
    Ok(out)
}

/// What an answer says.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Said {
    Map(Told),
    /// (brightness echoes its number)
    Text(String),
    Nothing,
}

/// answer_of: what an answer says: a map, a text, or nothing (empty).
pub fn answer_of(payload: &[u8]) -> Said {
    if payload.is_empty() {
        return Said::Nothing;
    }
    match read_qmap(payload) {
        Ok(map) => Said::Map(map),
        Err(_) => Said::Text(String::from_utf8_lossy(payload).into_owned()),
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Frame {
    pub kind: u32,
    pub command: u32,
    pub payload: Vec<u8>,
}

/// Frames out of what the port gives, a piece at a time: bytes before a frame's head, and a
/// frame whose CRC does not fit, are passed over (counted in `skipped`).
#[derive(Default)]
pub struct Frames {
    buffer: Vec<u8>,
    pub skipped: usize,
}

impl Frames {
    pub fn feed(&mut self, data: &[u8]) -> Vec<Frame> {
        self.buffer.extend_from_slice(data);
        let mut found = Vec::new();
        loop {
            let at = (self.buffer.windows(MAGIC.len())).position(|window| window == MAGIC);
            let Some(at) = at else {
                // (the head may begin at the end)
                let over = self.buffer.len().saturating_sub(MAGIC.len() - 1);
                self.skipped += over;
                self.buffer.drain(..over);
                return found;
            };
            self.skipped += at;
            self.buffer.drain(..at);
            if self.buffer.len() < MAGIC.len() + HEAD {
                return found;
            }
            let word = |n: usize| {
                let at = MAGIC.len() + 4 * n;
                let bytes = &self.buffer[at..at + 4];
                u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]])
            };
            let (kind, command, length, crc) = (word(0), word(1), word(2), word(3));
            let end = MAGIC.len() + HEAD + length as usize;
            if length <= MAX_PAYLOAD && self.buffer.len() < end {
                return found;
            }
            let fits = length <= MAX_PAYLOAD && crc32(&self.buffer[MAGIC.len() + HEAD..end]) == crc;
            if !fits {
                self.skipped += 1;
                self.buffer.drain(..1);
                continue;
            }
            let payload = self.buffer[MAGIC.len() + HEAD..end].to_vec();
            self.buffer.drain(..end);
            found.push(Frame {
                kind,
                command,
                payload,
            });
        }
    }
}

/// theme_name: a theme's name as the person knows it: its file's name without `.Theme`.
pub fn theme_name(path: &str) -> &str {
    let name = path.rsplit('/').next().unwrap_or(path);
    name.strip_suffix(THEME_SUFFIX).unwrap_or(name)
}

type Clock = Box<dyn FnMut() -> f64 + Send>;

/// The display on an open port (a serial port, or what a test brings): a command and its
/// answer at a time.
pub struct Display {
    port: Box<dyn Link>,
    clock: Clock,
    frames: Frames,
}

impl Display {
    pub fn new(port: Box<dyn Link>) -> Display {
        let began = std::time::Instant::now();
        Display::with_clock(port, Box::new(move || began.elapsed().as_secs_f64()))
    }

    pub fn with_clock(port: Box<dyn Link>, clock: Clock) -> Display {
        Display {
            port,
            clock,
            frames: Frames::default(),
        }
    }

    /// ask: send `command` and wait for the display's answer to it; an error when none comes
    /// in time. Answers to earlier commands that come late are passed over.
    pub fn ask(&mut self, command: u32, payload: &[u8], timeout: f64) -> Result<Frame> {
        if !COMMANDS.contains(&command) {
            return Err(DeviceError(format!(
                "command {command} is not one Libre Panel sends"
            )));
        }
        (self.port.write(&frame(command, payload, TO_DISPLAY)))
            .map_err(|why| DeviceError(format!("cannot write to the display: {}", why.0)))?;
        let end = (self.clock)() + timeout;
        while (self.clock)() < end {
            let data = (self.port.read(4096))
                .map_err(|why| DeviceError(format!("cannot read from the display: {}", why.0)))?;
            let answers = self.frames.feed(&data).into_iter();
            let mut ours = answers.filter(|f| f.kind == ANSWER && f.command == command);
            if let Some(found) = ours.next() {
                return Ok(found);
            }
        }
        Err(DeviceError("the display did not answer".into()))
    }

    /// hello: the handshake: what the display says of itself (some say nothing: an empty
    /// map); an error when nothing answers as the display does.
    pub fn hello(&mut self) -> Result<Told> {
        Ok(
            match answer_of(&self.ask(FIND_DEVICE, b"", ANSWER_S)?.payload) {
                Said::Map(said) => said,
                _ => Told::new(),
            },
        )
    }

    pub fn version(&mut self) -> Result<String> {
        Ok(
            match answer_of(&self.ask(GET_VERSION, b"", ANSWER_S)?.payload) {
                Said::Map(said) => said.get("versionV2").cloned().flatten().unwrap_or_default(),
                Said::Text(said) => said,
                Said::Nothing => String::new(),
            },
        )
    }

    pub fn send_values(&mut self, values: &IndexMap<String, String>) -> Result<()> {
        self.ask(SYSTEM_DATA, &qmap(values), ANSWER_S).map(|_| ())
    }

    /// set_brightness: `percent` of the backlight: the display counts 0 to 255 (Waveshare's
    /// program sends that range; 100, as other tools send at most, is only two fifths of it).
    pub fn set_brightness(&mut self, percent: i64) -> Result<()> {
        let level = lp_py::round(percent.clamp(0, 100) as f64 * BRIGHTEST / 100.0);
        let text = format!("{level:.0}");
        self.ask(SET_BRIGHTNESS, text.as_bytes(), ANSWER_S)
            .map(|_| ())
    }

    pub fn set_flipped(&mut self, flipped: bool) -> Result<()> {
        let mut values = IndexMap::new();
        let turned = if flipped { "1" } else { "0" };
        values.insert("canvas_flip".to_string(), turned.to_string());
        self.ask(SET_CANVAS_FLIP, &qmap(&values), ANSWER_S)
            .map(|_| ())
    }

    /// themes: the themes on the card, by their path (`/data/theme/480x480/Space
    /// Suit.Theme`), in the order of their names; GET_THEME's answer names them with their
    /// checksums, beside what else the display tells of itself.
    pub fn themes(&mut self) -> Result<Vec<String>> {
        let Said::Map(said) = answer_of(&self.ask(GET_THEME, b"", ANSWER_S)?.payload) else {
            return Ok(Vec::new());
        };
        let theme =
            |path: &&String| path.starts_with(THEMES_FOLDER) && path.ends_with(THEME_SUFFIX);
        let mut found: Vec<String> = said.keys().filter(theme).cloned().collect();
        found.sort_by_cached_key(|path| lp_py::casefold(theme_name(path)));
        Ok(found)
    }

    /// show_theme: show the theme at `path` (as `themes` lists it), one that is on the card:
    /// nothing is written. The theme brings its own orientation (`canvas_flip` in its file),
    /// which the display takes on as it loads it.
    pub fn show_theme(&mut self, path: &str) -> Result<()> {
        (self.ask(RELOAD_THEME, path.as_bytes(), ANSWER_S * 2.0)).map(|_| ())
    }

    pub fn close(&mut self) {
        self.port.close();
    }
}

/// is_display: whether the display may be on a port of these ids: the gadget's ids with its
/// product's name, where the system tells it (Windows does not: there the handshake decides),
/// or the placeholder ids.
pub fn is_display(vid: Option<u16>, pid: Option<u16>, product: Option<&str>) -> bool {
    match (vid, pid) {
        (Some(vid), Some(pid)) if (vid, pid) == GADGET_IDS => {
            product.is_none_or(|product| lp_py::strip(product) == GADGET_PRODUCT)
        }
        (Some(vid), Some(pid)) => (vid, pid) == PLACEHOLDER_IDS,
        _ => false,
    }
}

/// candidates: the ports the display may be on, by their ids (`port`: only that one,
/// whatever its ids), without those a panel of Libre Panel has open.
pub fn candidates(port: &str) -> Vec<String> {
    if !port.is_empty() {
        return match place_taken(port) {
            true => Vec::new(),
            false => vec![port.to_string()],
        };
    }
    let ports = crate::serial::ports().into_iter();
    let may = ports.filter(|info| {
        let product = crate::serial::product_of(&info.device);
        is_display(info.vid, info.pid, product.as_deref()) && !place_taken(&info.device)
    });
    may.map(|info| info.device).collect()
}

/// holders: Linux: the names of the other processes of this user that have `device` open,
/// such as wsmonitor's service, which takes the port without the lock (each would write over
/// the other's frames); none where it cannot be told.
pub fn holders(device: &str, proc: &std::path::Path) -> Vec<String> {
    let (Ok(target), Ok(entries)) = (std::fs::canonicalize(device), std::fs::read_dir(proc)) else {
        return Vec::new();
    };
    let own = std::process::id().to_string();
    let mut found = Vec::new();
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.is_empty() || !name.bytes().all(|b| b.is_ascii_digit()) || name == own {
            continue;
        }
        // (another user's process, or one that ended: passed over)
        let Ok(open) = std::fs::read_dir(entry.path().join("fd")) else {
            continue;
        };
        let has = open
            .flatten()
            .any(|fd| std::fs::read_link(fd.path()).is_ok_and(|link| link == target));
        if has {
            if let Ok(comm) = std::fs::read_to_string(entry.path().join("comm")) {
                found.push(lp_py::strip(&comm).to_string());
            }
        }
    }
    found
}

/// open_port: `device` opened for the display: raw, with DTR set (some firmware is silent
/// without); the speed means nothing to a USB serial port. An error ("busy") where another
/// program has it open on Linux, which the lock does not keep out.
pub fn open_port(device: &str) -> Result<Box<dyn Link>> {
    if cfg!(target_os = "linux") {
        let mut others = holders(device, std::path::Path::new("/proc"));
        others.sort();
        others.dedup();
        if !others.is_empty() {
            let names = others.join(", ");
            return Err(DeviceError(format!(
                "{device} is busy: {names} has it open"
            )));
        }
    }
    let mut port = crate::serial::SerialPort::open(device, 115_200, false)?;
    // (Windows opens a port for one program anyway; elsewhere the lock keeps out a second
    // Libre Panel and every program that asks for it)
    port.lock()?;
    let wait = std::time::Duration::from_secs_f64;
    port.set_waits(wait(0.1), wait(ANSWER_S))?;
    Ok(Box::new(port))
}

/// How a port is opened for the display.
pub type Opener<'a> = &'a dyn Fn(&str) -> Result<Box<dyn Link>>;

/// connect: the display: the first port that answers the handshake, as (the display, its
/// port, what it said); an error (the last port's reason) when none does.
pub fn connect(port: &str, opener: Opener) -> Result<(Display, String, Told)> {
    let mut reason = "no Waveshare USB monitor is connected".to_string();
    for device in candidates(port) {
        let mut display = match opener(&device) {
            Ok(port) => Display::new(port),
            Err(why) => {
                reason = format!("{device}: {}", why.0);
                continue;
            }
        };
        match display.hello() {
            Ok(said) => return Ok((display, device, said)),
            Err(why) => {
                reason = format!("{device}: {}", why.0);
                display.close();
            }
        }
    }
    Err(DeviceError(reason))
}
