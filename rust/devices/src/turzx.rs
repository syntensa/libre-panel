//! TURZX USB panels, through Libre Panel's engine, which has the panel (devices/turzx.py,
//! devices/turzx_video.py and devices/turzx_usb.py with devices/engine.py): in their video
//! mode every frame goes into the panel's H.264 decoder; without it each frame goes as a
//! picture, a PNG to the panel's upper layer or a JPEG to the one under it.
//!
//! The video (docs/protocol/turzx-usb.md, "Video layer"):
//! - **Start:** 110 with the standby clip's name clears the framebuffer, 111 stops the
//!   playback 110 started, 112 asks whether it still plays, then the brightness, a
//!   transparent PNG layer, the player's rate (15) and the block size (17).
//! - **Frames:** the engine is given the parts of each frame that changed, with the time
//!   it was drawn for; it makes the picture and sends it a fixed lead later, with the
//!   panel's flow control.
//! - **The bit rate** "auto" follows the panel: a step down while it takes less than it is
//!   sent, a step up after a calm minute, into the running stream; where it got to is kept
//!   for the next run (video-state.json).
//! - **Stop:** 123 ends the stream, 15 = 30 gives the panel its power-on rate back, and the
//!   last frame stays on the panel as a sharp PNG.
//! - **A hung decoder** (the engine finds it: slow blocks, a queue stuck high) is cleared
//!   by a restart of the panel only: 11, wait until it leaves the bus (at most 20 s) and
//!   comes back (at most 90 s), 2 s more, then a full start. At most three times in a row;
//!   after 300 s without a hang the count starts again. After that: replug.
//!
//! Nothing here stores anything on the panel.

use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard, OnceLock};
use std::time::{Duration, Instant};

use lp_pixels::{Image, Mode, Transpose};

use crate::base::{leave_place, take_place, Counters, DeviceError, Result};
use crate::engine::{capabilities, frame_record, EngineLink, Failure};
use crate::models::{orientation_of, PanelModel, MODELS};
use crate::png;
use crate::rate::{auto_rate, mbit, rate_text, rates, RateControl, RateKey, VideoState};
use crate::usb;

pub const VENDOR_ID: u16 = 0x1CBE;
pub const CMD_SYNC: u8 = 10;
/// restarts the panel (clears a hung decoder)
pub const CMD_RESTART: u8 = 11;
pub const CMD_BRIGHTNESS: u8 = 14;
pub const CMD_FRAME_RATE: u8 = 15;
pub const CMD_CHUNK_SIZE: u8 = 17;
pub const CMD_UPLOAD_JPEG: u8 = 101;
pub const CMD_UPLOAD_PNG: u8 = 102;
/// clears the framebuffer; the name goes into the panel's settings in RAM
pub const CMD_PLAY_LOCAL: u8 = 110;
pub const CMD_STOP_LOCAL: u8 = 111;
pub const CMD_LOCAL_PLAYING: u8 = 112;
pub const CMD_STOP_STREAM: u8 = 123;
/// The most a command's payload may be: a picture of more is not sent.
pub const MAX_PAYLOAD: usize = 1024 * 1024;
/// The panel's own block size; it answers 17 with 0, meaning this default.
pub const DEFAULT_BLOCK: u32 = 202_752;
/// The player's rate after power-on. Set again on exit, because the panel's own standby clip
/// plays at the last rate set.
pub const POWER_ON_FPS: u8 = 30;
/// What x264 is told for drawn frames (h264.py's DRAWN_X264).
pub const DRAWN_X264: &str = "no-deblock=1:subme=0";
/// The models whose decoder shows the high profile (HIGH_PROFILE_MODELS).
pub const HIGH_PROFILE_MODELS: [&str; 2] = ["turing-9.2-usb", "turing-12.3-usb"];
/// 4:4:4 at 90 keeps coloured text crisp; busy frames step down.
pub const JPEG_QUALITY: i64 = 90;
pub const JPEG_FALLBACK: [i64; 3] = [75, 60, 45];
/// The models whose stills go as JPEG with `device.still = "auto"`: the 5.2" shows nothing
/// of a PNG.
pub const JPEG_STILLS: [&str; 1] = ["turing-5.2-usb"];
/// The bit rate "auto" got to goes into video-state.json at most this often.
const RATE_KEPT_S: f64 = 10.0;
const RESTARTING: &str = "the panel restarts to clear its hung video decoder";

/// The video's settings, as `[video]` of config.toml has them (VideoConfig), with the
/// panel's video range.
#[derive(Clone, Debug, PartialEq)]
pub struct Video {
    /// "auto", "on" or "off"
    pub mode: String,
    pub fps: i64,
    pub device_fps: i64,
    pub crf: i64,
    pub preset: String,
    pub keyframe_s: f64,
    pub maxrate: String,
    pub profile: String,
    pub engine_encoder: String,
    pub local_clip: String,
    /// `[device] video_range`: "auto" (the model's), "limited" or "full"
    pub video_range: String,
}

/// What a TURZX panel is opened with.
#[derive(Clone, Debug)]
pub struct Setup {
    /// Libre Panel's engine, the program
    pub engine: PathBuf,
    /// the configured model (None: "auto", a panel that is there)
    pub model: Option<&'static PanelModel>,
    /// the models the other panels name
    pub beside: Vec<String>,
    pub video: Video,
    /// `[device] still`: "auto", "png" or "jpeg"
    pub still: String,
    pub brightness: i64,
    /// where the bit rate "auto" got to is kept (None: nowhere)
    pub state: Option<VideoState>,
    /// the tests' simulated panel instead of USB: `tcp:HOST:PORT`, the engine's `--link`
    pub test_link: Option<String>,
    /// with it: a file that is there while the simulated panel is on the bus
    pub test_present: Option<PathBuf>,
    /// with it: more arguments for the engine
    pub test_args: Vec<String>,
}

/// The TURZX USB models by their product ids (USB_PIDS).
pub fn usb_models() -> impl Iterator<Item = (u16, &'static PanelModel)> {
    MODELS
        .iter()
        .filter(|model| model.protocol == "usb-turing")
        .flat_map(|model| model.usb_ids.iter().map(move |(_, pid)| (*pid, model)))
}

/// firmware_of: the firmware's name in the sync reply: the ASCII after `turzx_`; "" when
/// there is none.
pub fn firmware_of(reply: &[u8]) -> String {
    let head = &reply[..reply.len().min(64)];
    let Some(start) = head.windows(6).position(|part| part == b"turzx_") else {
        return String::new();
    };
    reply[start..]
        .iter()
        .take(32)
        .take_while(|&&byte| 0x20 < byte && byte < 0x7F)
        .map(|&byte| byte as char)
        .collect()
}

/// brightness_arg: 0-100 % as the panel's 0-102 scale.
pub fn brightness_arg(percent: i64) -> u8 {
    (percent.clamp(0, 100) as f64 / 100.0 * 102.0) as u8
}

/// encode_png_rgba: the PNG of a picture as the panel decodes it: colour type 6 (RGBA)
/// only; an RGB one comes out shifted by a byte a pixel and tiled four times.
pub fn encode_png_rgba(image: &Image, level: png::Compression) -> Result<Vec<u8>> {
    let rgba;
    let image = if image.mode() == Mode::RGBA {
        image
    } else {
        rgba = image.convert(Mode::RGBA)?;
        &rgba
    };
    png::encode_with(image, level).map_err(DeviceError)
}

/// transparent_layer: a fully transparent PNG layer. Opaque black would hide the video, or
/// the JPEG stills, completely.
pub fn transparent_layer(size: (i64, i64)) -> Result<Vec<u8>> {
    let layer = Image::blank(Mode::RGBA, size.0 as usize, size.1 as usize);
    encode_png_rgba(&layer, png::Compression::best())
}

fn too_detailed(bytes: usize) -> DeviceError {
    DeviceError(format!(
        "{} ({} KB, limit {} KB); use fewer photos or gradients in the theme",
        crate::base::FRAME_ERROR,
        bytes / 1024,
        MAX_PAYLOAD / 1024
    ))
}

/// encode_frame: the PNG of one frame (as the panel lies), within the panel's payload limit:
/// packed fast, a busy frame as well as zlib can; one too detailed even so is refused.
pub fn encode_frame(native: &Image) -> Result<Vec<u8>> {
    let mut data = encode_png_rgba(native, png::Compression::new(2))?;
    if data.len() > MAX_PAYLOAD {
        data = encode_png_rgba(native, png::Compression::best())?;
    }
    if data.len() > MAX_PAYLOAD {
        return Err(too_detailed(data.len()));
    }
    Ok(data)
}

/// jpeg_width: how wide a JPEG still is for a framebuffer `width` columns wide: the columns
/// that show, rounded up to whole blocks of 16 (464 on the 9.2", not its framebuffer's 480:
/// a 480-wide JPEG wraps its last rows onto the first there).
pub fn jpeg_width(model: Option<&PanelModel>, width: i64) -> i64 {
    let shown = width - model.map_or(0, |model| model.hidden[1]);
    (shown + 15).div_euclid(16) * 16
}

/// encode_jpeg: a baseline JPEG (4:4:4) of a frame as the panel lies, for 101, within the
/// payload limit: cut to jpeg_width from the left, never stretched; a busy frame at a lower
/// quality.
pub fn encode_jpeg(native: &Image, quality: i64, model: Option<&PanelModel>) -> Result<Vec<u8>> {
    let width = jpeg_width(model, native.width() as i64);
    let cut;
    let native = if width != native.width() as i64 {
        cut = native.crop(0, 0, width, native.height() as i64);
        &cut
    } else {
        native
    };
    let rgb;
    let native = if native.mode() == Mode::RGB {
        native
    } else {
        rgb = native.convert(Mode::RGB)?;
        &rgb
    };
    let mut size = 0;
    let lower = JPEG_FALLBACK.iter().copied().filter(|step| *step < quality);
    for step in std::iter::once(quality).chain(lower) {
        let data = lp_decode::save_jpeg_with(native, step, true)?;
        if data.len() <= MAX_PAYLOAD {
            return Ok(data);
        }
        size = data.len();
    }
    Err(too_detailed(size))
}

/// still_format: how a panel takes its stills with `device.still`: "png" or "jpeg"; "auto"
/// is JPEG on the models of JPEG_STILLS, else PNG.
pub fn still_format(still: &str, model: Option<&PanelModel>) -> &'static str {
    match still {
        "jpeg" => "jpeg",
        "png" => "png",
        _ if model.is_some_and(|model| JPEG_STILLS.contains(&model.id)) => "jpeg",
        _ => "png",
    }
}

/// to_native: a theme's frame turned into the panel's portrait framebuffer.
pub fn to_native(frame: &Image) -> Image {
    if orientation_of(frame.width() as i64, frame.height() as i64) == "landscape" {
        frame.transpose(Transpose::Rotate270)
    } else {
        frame.transpose(Transpose::Rotate180)
    }
}

/// rotation_of: how a frame of `size` is turned into the panel's framebuffer: a landscape
/// frame a quarter turn clockwise, a portrait one half a turn.
pub fn rotation_of(size: (i64, i64)) -> i64 {
    if orientation_of(size.0, size.1) == "landscape" {
        270
    } else {
        180
    }
}

/// full_range: whether a panel gets its video in full range: `video_range` "full", or "auto"
/// on a model whose decoder shows every stream as full range.
pub fn full_range(video_range: &str, model: Option<&PanelModel>) -> bool {
    match video_range {
        "auto" => model.is_some_and(|model| model.video_range == "full"),
        given => given == "full",
    }
}

/// resolve_profile: the H.264 profile for a panel: "auto" is high where it was measured to
/// show, baseline elsewhere.
pub fn resolve_profile<'a>(profile: &'a str, model_id: &str) -> &'a str {
    match profile {
        "auto" if HIGH_PROFILE_MODELS.contains(&model_id) => "high",
        "auto" => "baseline",
        given => given,
    }
}

/// engine_lead: how long after the time a frame was drawn for the engine sends its picture:
/// two frames' time, between 30 and 100 ms.
pub fn engine_lead(fps: i64) -> f64 {
    (2.0 / fps.max(1) as f64).clamp(0.03, 0.1)
}

/// block_of: the blocks in which a frame of `size` is compared: 16 pixels, or 8 where the
/// frame is not made of whole blocks of 16; None where neither fits.
pub fn block_of(size: (i64, i64)) -> Option<i64> {
    [16, 8]
        .into_iter()
        .find(|block| size.0 % block == 0 && size.1 % block == 0)
}

/// engine_options: how the engine encodes frames of `size` into the panel's framebuffer, as
/// h264.py's engine_options says it, at `rate` (Mbit/s) and with the stream's lead.
/// `encoder`: "x264", "card" or "auto", as the engine at hand can.
pub fn engine_options(
    size: (i64, i64),
    video: &Video,
    model_id: &str,
    full: bool,
    rate: f64,
    encoder: &str,
) -> Vec<String> {
    let keyint = ((video.fps as f64 * video.keyframe_s).round_ties_even() as i64).max(1);
    let kbit = ((rate * 1000.0).round_ties_even() as i64).max(1);
    let mut options: Vec<String> = [
        "--size",
        &format!("{}x{}", size.0, size.1),
        "--turn",
        &rotation_of(size).to_string(),
        "--fps",
        &video.fps.to_string(),
        "--crf",
        &video.crf.to_string(),
        "--kbit",
        &kbit.to_string(),
        "--keyint",
        &keyint.to_string(),
        "--preset",
        &video.preset,
        "--profile",
        resolve_profile(&video.profile, model_id),
        "--x264",
        DRAWN_X264,
    ]
    .iter()
    .map(|option| option.to_string())
    .collect();
    if encoder == "card" || encoder == "auto" {
        options.extend(["--encoder".to_string(), encoder.to_string()]);
    }
    if full {
        options.push("--full".into());
    }
    let lead_ms = ((engine_lead(video.fps) * 1000.0).round_ties_even() as i64).max(1);
    options.extend(["--lead-ms".to_string(), lead_ms.to_string()]);
    options
}

/// video_mode: whether a TURZX USB panel runs in video mode, and what keeps "auto" from it.
/// "on" and "off" decide alone. "auto" needs a model the video mode is proven on and the
/// name its start command carries (`local_clip`: never made up, it lands in the panel's
/// settings).
pub fn video_mode(video: &Video, model: Option<&PanelModel>) -> (bool, Vec<String>) {
    if video.mode != "auto" {
        return (video.mode == "on", Vec::new());
    }
    let Some(model) = model else {
        return (false, Vec::new());
    };
    let gaps = video_gaps(&video.local_clip, Some(model), true);
    let texts = gaps.iter().map(|gap| match *gap {
        "model" => format!("the video mode is not proven on the {} yet", model.label()),
        _ => "video.local_clip is not set (see docs/CONFIGURATION.md, Video)".to_string(),
    });
    (gaps.is_empty(), texts.collect())
}

/// video_gaps: what the video mode lacks, as codes: "model" (not proven on this panel; it is
/// then the only one) and "clip" (`local_clip` is not set). `proven` false does not ask for
/// a proven model, as `mode = "on"` does not. (Libre Panel's engine encodes: no ffmpeg is
/// looked for, as Python looks for one.)
pub fn video_gaps(local_clip: &str, model: Option<&PanelModel>, proven: bool) -> Vec<&'static str> {
    if proven && model.is_some_and(|model| !model.video) {
        return vec!["model"];
    }
    match local_clip.is_empty() {
        true => vec!["clip"],
        false => Vec::new(),
    }
}

/// changed_boxes: where the frame `now` differs from `before` (both of `size`, three bytes
/// a pixel), as boxes grown to whole blocks of `block` pixels: a band of blocks for each
/// stretch of rows that changed between the same columns. None changed: none.
pub fn changed_boxes(
    before: &[u8],
    now: &[u8],
    size: (usize, usize),
    block: usize,
) -> Vec<[u16; 4]> {
    let (width, height) = size;
    let (row, block) = (width * 3, block.max(1));
    let mut boxes: Vec<[u16; 4]> = Vec::new();
    for top in (0..height).step_by(block) {
        let bottom = (top + block).min(height);
        let mut span: Option<(usize, usize)> = None;
        for y in top..bottom {
            let (old, new) = (
                &before[y * row..(y + 1) * row],
                &now[y * row..(y + 1) * row],
            );
            if old == new {
                continue;
            }
            let first = old.iter().zip(new).position(|(a, b)| a != b).unwrap_or(0) / 3;
            let last = old.iter().zip(new).rposition(|(a, b)| a != b).unwrap_or(0) / 3;
            span = Some(match span {
                Some((x0, x1)) => (x0.min(first), x1.max(last)),
                None => (first, last),
            });
        }
        let Some((first, last)) = span else { continue };
        let x0 = first - first % block;
        let x1 = (last + 1).div_ceil(block).saturating_mul(block).min(width);
        let band = [x0 as u16, top as u16, x1 as u16, bottom as u16];
        match boxes.last_mut() {
            // (the band below one of the same columns: one box)
            Some(above) if above[0] == band[0] && above[2] == band[2] && above[3] == band[1] => {
                above[3] = band[3];
            }
            _ => boxes.push(band),
        }
    }
    boxes
}

/// A restart of the panel under way, and how many came in a row (_Restart). Kept for the
/// process: a panel is opened anew each time the loop opens it again.
#[derive(Debug)]
struct Restart {
    began: Instant,
    count: u32,
    last: f64,
    /// set while a restart is under way
    pid: Option<u16>,
    sent: f64,
    gone: Option<f64>,
    back: Option<f64>,
}

impl Restart {
    /// then: replug
    const IN_A_ROW: u32 = 3;
    /// this long without a hang and the count starts again
    const RESET_S: f64 = 300.0;
    const LEAVE_S: f64 = 20.0;
    const RETURN_S: f64 = 90.0;
    const SETTLE_S: f64 = 2.0;

    fn new() -> Restart {
        Restart {
            began: Instant::now(),
            count: 0,
            last: -1e9,
            pid: None,
            sent: 0.0,
            gone: None,
            back: None,
        }
    }

    fn clock(&self) -> f64 {
        self.began.elapsed().as_secs_f64()
    }

    fn allowed(&mut self) -> bool {
        if self.clock() - self.last > Self::RESET_S {
            self.count = 0;
        }
        self.count < Self::IN_A_ROW
    }

    fn begin(&mut self, pid: u16) {
        self.count += 1;
        self.last = self.clock();
        self.sent = self.last;
        (self.pid, self.gone, self.back) = (Some(pid), None, None);
    }

    /// wait: an error until the panel has left the bus, is back and has settled: that it
    /// restarts, or that it did not come back.
    fn wait(&mut self, present: impl Fn(u16) -> bool) -> Result<()> {
        let Some(pid) = self.pid else {
            return Ok(());
        };
        let (now, here) = (self.clock(), present(pid));
        let restarting = || Err(DeviceError(RESTARTING.into()));
        let gone = match self.gone {
            Some(gone) => gone,
            None => {
                if here && now - self.sent < Self::LEAVE_S {
                    return restarting();
                }
                // (left the bus, or never seemed to: go on after 20 s)
                self.gone = Some(now);
                now
            }
        };
        let back = match self.back {
            Some(back) => back,
            None => {
                if !here {
                    if now - gone < Self::RETURN_S {
                        return restarting();
                    }
                    self.pid = None;
                    return Err(DeviceError(
                        "the panel did not come back after its restart; replug it, please".into(),
                    ));
                }
                self.back = Some(now);
                now
            }
        };
        if now - back < Self::SETTLE_S {
            return restarting();
        }
        self.pid = None; // back and settled: open it as usual
        Ok(())
    }
}

fn restart() -> MutexGuard<'static, Restart> {
    static RESTART: OnceLock<Mutex<Restart>> = OnceLock::new();
    RESTART
        .get_or_init(|| Mutex::new(Restart::new()))
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

/// Whether a panel restarts to clear its hung decoder (PanelRestarting): the loop looks
/// again soon, not with its usual pause.
pub fn restart_under_way() -> bool {
    restart().pid.is_some()
}

/// The engine with a panel it opened: its link, which panel it is and where.
struct Connection {
    link: EngineLink,
    pid: u16,
    model: Option<&'static PanelModel>,
    firmware: String,
    /// where it is on the bus, while it is open (take_place)
    place: Option<String>,
}

impl Connection {
    /// EngineTransport.open and TurzxUsbDisplay.open: the panel the setup names (the same
    /// choice as devices/turzx_usb.py's _pick), opened by the engine, which is told its
    /// product id and where it is on the bus; then asked to sync.
    fn open(setup: &Setup) -> Result<Connection> {
        Connection::open_at(setup, 0)
    }

    /// As [`Connection::open`], passing over `skip` panels that would do first (a panel
    /// beside others of the same model).
    fn open_at(setup: &Setup, skip: usize) -> Result<Connection> {
        let wanted = setup
            .model
            .and_then(|model| model.usb_ids.first())
            .map(|(_, pid)| *pid);
        let mut command = vec![
            setup.engine.to_string_lossy().into_owned(),
            "panel".to_string(),
        ];
        let (pid, place) = match &setup.test_link {
            Some(spec) => {
                // (the simulated panel: no bus to look on; the 9.2" where none is named)
                let pid = wanted.unwrap_or(0x0092);
                command.extend(["--pid".to_string(), format!("{pid:04x}")]);
                command.extend(["--link".to_string(), spec.clone()]);
                command.extend(setup.test_args.iter().cloned());
                (pid, None)
            }
            None => {
                let devices = usb::list();
                let Some(device) = usb::pick(wanted, &setup.beside, &devices, true, skip) else {
                    return Err(DeviceError(
                        "no Turing/TURZX USB panel found. Is it plugged in, and is the TURZX \
                         app closed? (it holds the device exclusively)"
                            .into(),
                    ));
                };
                let place = device.place();
                if !take_place(&place) {
                    return Err(DeviceError(
                        "the panel is in use by another panel of Libre Panel".into(),
                    ));
                }
                command.extend(["--pid".to_string(), format!("{:04x}", device.pid)]);
                if !device.ports.is_empty() {
                    let ports: Vec<String> = device.ports.iter().map(u8::to_string).collect();
                    command.extend(["--ports".to_string(), ports.join(".")]);
                }
                if let Some(bus) = device.busnum {
                    command.extend(["--bus".to_string(), bus.to_string()]);
                }
                command.extend(["--address".to_string(), device.address.to_string()]);
                (device.pid, Some(place))
            }
        };
        let let_go = |place: &Option<String>| {
            if let Some(place) = place {
                leave_place(place);
            }
        };
        let mut link = match EngineLink::start(&command) {
            Ok(link) => link,
            Err(e) => {
                let_go(&place);
                return Err(e);
            }
        };
        let reply = match link.sync(CMD_SYNC, 3) {
            Ok(reply) => reply,
            Err(e) => {
                link.close(Duration::from_secs(5));
                let_go(&place);
                return Err(e);
            }
        };
        Ok(Connection {
            link,
            pid,
            model: setup.model.or_else(|| usb::model_of(pid)),
            firmware: firmware_of(&reply),
            place,
        })
    }

    /// The panel's size as it lies (the model's; 480x1920 where it is not known).
    fn native(&self) -> (i64, i64) {
        self.model
            .map_or((480, 1920), |m| (m.native_width, m.native_height))
    }

    fn describe(&self) -> String {
        let name = match self.model {
            Some(model) => model.label(),
            None => format!("TURZX USB panel {:04x}", self.pid),
        };
        if self.firmware.is_empty() {
            name
        } else {
            format!("{name} (firmware {})", self.firmware)
        }
    }

    fn close(&mut self) {
        self.link.close(Duration::from_secs(5));
        if let Some(place) = self.place.take() {
            leave_place(&place);
        }
    }
}

/// A TURZX USB panel opened by the engine for its file commands (panel_files.py's
/// open_panel: the start screen's boot logo and standby clip), in step; closed when dropped.
pub struct FileLink {
    conn: Connection,
}

impl FileLink {
    /// open_panel: the panel the setup names (its model if given), `skip` panels alike passed
    /// over, opened and in step.
    pub fn open(setup: &Setup, skip: usize) -> Result<FileLink> {
        Connection::open_at(setup, skip).map(|conn| FileLink { conn })
    }

    /// The panel's model: the one its product id is, else the one asked for.
    pub fn model(&self) -> Option<&'static PanelModel> {
        usb::model_of(self.conn.pid).or(self.conn.model)
    }

    /// A command and its reply, accepted (UsbTransport.command).
    pub fn command(
        &self,
        cmd: u8,
        args: &[u8],
        payload: &[u8],
        timeout_ms: u32,
    ) -> Result<Vec<u8>> {
        self.conn.link.command(cmd, args, payload, timeout_ms)
    }

    /// A question whose reply carries data (UsbTransport.ask; `echo`: it begins with the
    /// command's number).
    pub fn ask(&self, cmd: u8, args: &[u8], echo: bool) -> Result<Vec<u8>> {
        self.conn.link.ask(cmd, args, 2000, echo)
    }

    /// What the panel still had to say read away (UsbTransport.drain).
    pub fn drain(&self) {
        self.conn.link.drain(50, 64);
    }
}

impl Drop for FileLink {
    fn drop(&mut self) {
        self.conn.close();
    }
}

/// What stopped the video.
enum Trouble {
    /// the engine found the panel's decoder hung: the depth its queue stood at
    Hung(String),
    /// its link failed, or the engine ended
    Failed(String),
}

/// A TURZX panel showing video, the engine having it (TurzxVideoDisplay with its
/// EngineTransport and EngineStream).
pub struct TurzxVideo {
    setup: Setup,
    conn: Connection,
    block: u32,
    /// the encoder the engine at hand is asked for ("x264", "card" or "auto")
    encoder: String,
    /// the size of the frames the running stream takes, and the next frame's number
    stream: Option<((i64, i64), u32)>,
    /// the frame sent last: what the next is compared with, and what stays on the panel
    last: Option<Image>,
    /// the panel the bit rate "auto" is followed for
    rate_key: Option<RateKey>,
    /// what video-state.json has of it, and when it was written
    rate_kept: (f64, f64),
    /// the bit rate's cap in force, Mbit/s
    bit_rate: f64,
    floor_told: bool,
    began: Instant,
    pub counters: Counters,
    notes: Vec<String>,
}

impl TurzxVideo {
    /// The panel opened by the engine and its video started: once a panel that restarts is
    /// back.
    pub fn open(setup: Setup) -> Result<TurzxVideo> {
        if setup.video.local_clip.is_empty() {
            // (an empty name would erase the panel's standby clip setting)
            return Err(DeviceError(
                "video.local_clip is not set (see docs/CONFIGURATION.md, Video)".into(),
            ));
        }
        let was_restarting = restart_under_way();
        let present = |pid: u16| match (&setup.test_link, &setup.test_present) {
            (Some(_), Some(file)) => file.exists(),
            (Some(_), None) => true,
            (None, _) => usb::present(pid),
        };
        restart().wait(present)?;
        let conn = Connection::open(&setup)?;
        let mut notes = Vec::new();
        if was_restarting {
            notes.push("the panel is back after its restart".to_string());
        }
        // (the graphics card's encoder where this engine has it; on Windows only)
        let can = capabilities(&setup.engine);
        let encoder = match setup.video.engine_encoder.as_str() {
            "card" if !can.contains("cards") => {
                notes.push(
                    "video: this engine has no graphics card's encoder (one of before); x264 \
                     in the engine encodes"
                        .to_string(),
                );
                "x264"
            }
            "card" => "card",
            "auto" if cfg!(windows) && can.contains("proven") => "auto",
            _ => "x264",
        }
        .to_string();
        let mut panel = TurzxVideo {
            conn,
            block: DEFAULT_BLOCK,
            encoder,
            stream: None,
            last: None,
            rate_key: None,
            rate_kept: (0.0, -1e9),
            bit_rate: 0.0,
            floor_told: false,
            began: Instant::now(),
            counters: Counters::default(),
            notes,
            setup,
        };
        if let Err(e) = panel.start() {
            panel.conn.close();
            return Err(e);
        }
        Ok(panel)
    }

    pub fn model(&self) -> Option<&'static PanelModel> {
        self.conn.model
    }

    pub fn describe(&self) -> String {
        format!(
            "{}, video {} fps through the engine ({})",
            self.conn.describe(),
            self.setup.video.fps,
            self.conn.link.place()
        )
    }

    /// VideoSession.start: the video layer made ready.
    fn start(&mut self) -> Result<()> {
        let name = self.setup.video.local_clip.as_bytes();
        let mut play = (name.len() as u32).to_be_bytes().to_vec();
        play.extend_from_slice(&[0; 4]);
        play.extend_from_slice(name);
        let link = &self.conn.link;
        link.command(CMD_PLAY_LOCAL, &play, &[], 2000)?;
        link.command(CMD_STOP_LOCAL, &[], &[], 2000)?;
        link.command(CMD_LOCAL_PLAYING, &[], &[], 2000)?;
        let level = brightness_arg(self.setup.brightness);
        link.command(CMD_BRIGHTNESS, &[level], &[], 2000)?;
        let layer = transparent_layer(self.conn.native())?;
        let length = (layer.len() as u32).to_be_bytes();
        link.command(CMD_UPLOAD_PNG, &length, &layer, 5000)?;
        let rate = self.setup.video.device_fps as u8;
        link.command(CMD_FRAME_RATE, &[rate], &[], 2000)?;
        let reply = link.command(CMD_CHUNK_SIZE, &[], &[], 2000)?;
        if let Some(size) = reply.get(8..12) {
            let size = u32::from_be_bytes([size[0], size[1], size[2], size[3]]);
            if 0 < size && size <= 1024 * 1024 {
                self.block = size;
            }
        }
        Ok(())
    }

    pub fn set_brightness(&mut self, percent: i64) -> Result<()> {
        self.setup.brightness = percent;
        self.conn
            .link
            .command(CMD_BRIGHTNESS, &[brightness_arg(percent)], &[], 2000)
            .map(|_| ())
    }

    fn trouble(&self) -> Option<Trouble> {
        if let Some(Failure { hung, text }) = self.conn.link.failed() {
            return Some(if hung {
                Trouble::Hung(text)
            } else {
                Trouble::Failed(text)
            });
        }
        (!self.conn.link.alive()).then(|| {
            Trouble::Failed(format!(
                "Libre Panel's engine stopped ({})",
                self.conn.link.reason()
            ))
        })
    }

    /// What the engine counted so far, in the run's counters.
    fn count(&mut self) -> (u64, u64, u64) {
        let counted = self.conn.link.counts();
        let of = |name: &str| counted.get(name).copied().unwrap_or(0.0).max(0.0) as u64;
        let counters = &mut self.counters;
        counters.blocks = counters.blocks.max(of("blocks"));
        counters.bytes = counters.bytes.max(of("bytes"));
        counters.waits = counters.waits.max(of("waits"));
        counters.slow_blocks = counters.slow_blocks.max(of("slow_blocks"));
        counters.drops = counters.drops.max(of("drops"));
        counters.max_depth = counters.max_depth.max(of("max_depth"));
        (counters.waits, counters.blocks, counters.slow_blocks)
    }

    /// _restart_panel: the panel restarted (11) to clear its hung decoder, as SPUR II does;
    /// the loop opens it again once it is back. The error says that it restarts, or, where
    /// restarts did not help, that it hangs (then: replug).
    fn restart_panel(&mut self, depth: &str) -> DeviceError {
        let hung = DeviceError(format!(
            "the panel's video decoder hangs (queue stuck at {depth}). Unplug the panel and \
             plug it in again."
        ));
        let mut restart = restart();
        if !restart.allowed() {
            return hung;
        }
        restart.begin(self.conn.pid);
        self.counters.panel_restarts += 1;
        self.notes.push(format!(
            "the panel's video decoder hangs; restarting the panel ({} of {} in a row)",
            restart.count,
            Restart::IN_A_ROW
        ));
        drop(restart);
        if self.stream.take().is_some() {
            let _ = self.conn.link.end();
        }
        // (it may be gone before it answers)
        let _ = self.conn.link.query(CMD_RESTART, 200);
        self.last = None; // (nothing more is sent to a hung decoder: no stop, no last frame)
        self.conn.close();
        DeviceError(RESTARTING.into())
    }

    /// _follow_the_panel: `maxrate` "auto": a step down while the panel takes less than it
    /// is sent, a step up again after a calm minute, into the running stream; and where it
    /// got to kept for the next run.
    fn follow_the_panel(&mut self) {
        let counts = self.count();
        let now = self.began.elapsed().as_secs_f64();
        let Some(key) = self.rate_key.clone() else {
            return;
        };
        let Some((before, moved, rate, floored)) = rates().get_mut(&key).map(|control| {
            let before = control.rate;
            let moved = control.judge(counts, now);
            (before, moved, control.rate, control.floored)
        }) else {
            return;
        };
        match moved {
            Some(rate) => {
                let why = if rate < before {
                    "the panel takes less than it is sent"
                } else {
                    "calm for a minute"
                };
                self.notes.push(format!(
                    "video: bit rate {} -> {} ({why})",
                    rate_text(before),
                    rate_text(rate)
                ));
            }
            None if floored && !self.floor_told => {
                self.floor_told = true;
                self.notes.push(format!(
                    "video: the panel takes fewer pictures than it is sent at {} already; \
                     fewer frames a second (video.fps) would help, fewer bits would not",
                    rate_text(rate)
                ));
            }
            None => {}
        }
        self.keep_rate(now, false);
        if rate != self.bit_rate && self.stream.is_some() {
            // (into the running stream: x264 goes on with the new cap, no new encoder and no
            // keyframe, nothing for the panel to wait for)
            let kbit = ((rate * 1000.0).round_ties_even() as u32).max(1);
            if self.conn.link.rate(kbit).is_ok() {
                self.bit_rate = rate;
            }
        }
    }

    /// _keep_rate: the bit rate "auto" got to, for the next run: written when it changed, at
    /// most every RATE_KEPT_S, and when the panel is closed (`at_once`).
    fn keep_rate(&mut self, now: f64, at_once: bool) {
        let (Some(state), Some(key)) = (&self.setup.state, &self.rate_key) else {
            return;
        };
        let Some(rate) = rates().get(key).map(|control| control.rate) else {
            return;
        };
        if rate == self.rate_kept.0 || (!at_once && now - self.rate_kept.1 < RATE_KEPT_S) {
            return;
        }
        state.keep_rate(key, rate);
        self.rate_kept = (rate, now);
    }

    /// A new stream for frames of `size`: the engine's encoder with its options, at the bit
    /// rate config.toml names or "auto" has got to.
    fn begin_stream(&mut self, size: (i64, i64)) -> Result<()> {
        if self.stream.take().is_some() {
            self.conn.link.end()?;
        }
        let video = &self.setup.video;
        let model_id = self.conn.model.map_or("", |model| model.id);
        let rate = if video.maxrate == "auto" {
            let place = match &self.conn.place {
                Some(place) => place.clone(),
                None => self.conn.pid.to_string(),
            };
            let key: RateKey = (model_id.to_string(), place, size);
            let rate = rates()
                .entry(key.clone())
                .or_insert_with(|| {
                    // (where the last run got to, if it is known)
                    let remembered = self.setup.state.as_ref().map_or(0.0, |s| s.rate(&key));
                    RateControl::new(auto_rate(video.fps, size), remembered)
                })
                .rate;
            if self.rate_key.as_ref() != Some(&key) {
                self.rate_key = Some(key);
                self.rate_kept.0 = rate;
            }
            rate
        } else {
            self.rate_key = None;
            mbit(&video.maxrate)
        };
        let full = full_range(&video.video_range, self.conn.model);
        let options = engine_options(size, video, model_id, full, rate, &self.encoder);
        self.conn.link.stream(&options, self.block)?;
        self.bit_rate = rate;
        let feed = match self.encoder.as_str() {
            "card" => ", the graphics card's encoder where it opens",
            "auto" => ", the graphics card's encoder where it is AMD's or NVIDIA's",
            _ => "",
        };
        self.notes.push(format!(
            "video: encoding with the engine, profile {}{feed}, {} range, {}",
            resolve_profile(&video.profile, model_id),
            if full { "full" } else { "limited" },
            rate_text(rate)
        ));
        self.stream = Some((size, 0));
        self.last = None; // (its first frame whole)
        Ok(())
    }

    /// show: a frame into the stream, drawn for the time `planned` (seconds on the loop's
    /// clock): the parts of it that changed since the one before, of which the engine makes
    /// the picture and sends it a fixed lead later. A frame of another size begins a new
    /// stream; a hung decoder restarts the panel.
    pub fn show(&mut self, frame: Image, planned: Option<f64>) -> Result<()> {
        match self.trouble() {
            Some(Trouble::Hung(depth)) => return Err(self.restart_panel(&depth)),
            Some(Trouble::Failed(why)) => {
                self.counters.usb_errors += 1;
                return Err(DeviceError(why));
            }
            None => {}
        }
        let frame = if frame.mode() == Mode::RGB {
            frame
        } else {
            frame.convert(Mode::RGB)?
        };
        let size = (frame.width() as i64, frame.height() as i64);
        if size.0 > i64::from(u16::MAX) || size.1 > i64::from(u16::MAX) {
            return Err(DeviceError(format!(
                "a frame of {}x{} is too large for the panel",
                size.0, size.1
            )));
        }
        if !matches!(self.stream, Some((running, _)) if running == size) {
            self.begin_stream(size)?;
        }
        self.follow_the_panel();
        let number = self.stream.map_or(0, |(_, number)| number);
        let whole = [[0, 0, size.0 as u16, size.1 as u16]];
        let changed;
        let boxes: &[[u16; 4]] = match &self.last {
            Some(last) if last.size() == frame.size() => {
                let block = block_of(size).unwrap_or(1) as usize;
                changed = changed_boxes(last.as_bytes(), frame.as_bytes(), frame.size(), block);
                &changed
            }
            _ => &whole,
        };
        let record = frame_record(number, planned, frame.width(), frame.as_bytes(), boxes);
        self.conn.link.write(&record)?;
        self.stream = Some((size, number.wrapping_add(1)));
        self.last = Some(frame);
        self.notes.extend(self.conn.link.notes());
        Ok(())
    }

    /// What the driver and the engine noted for the log since this was last asked.
    pub fn notes(&mut self) -> Vec<String> {
        std::mem::take(&mut self.notes)
    }

    /// What the engine counted (pictures, blocks, bytes, waits, late ...).
    pub fn counts(&self) -> std::collections::HashMap<String, f64> {
        self.conn.link.counts()
    }

    /// close: the stream ended and the video stopped (VideoSession.stop): 123, the power-on
    /// rate, and the last frame left on the panel as a PNG (a frame too detailed for one:
    /// the video's last picture stays); then the panel let go. What the engine sent is said.
    pub fn close(&mut self) {
        let now = self.began.elapsed().as_secs_f64();
        self.keep_rate(now, true);
        if self.stream.take().is_some() {
            let _ = self.conn.link.end();
        }
        if self.trouble().is_none() {
            let link = &self.conn.link;
            let _ = link.command(CMD_STOP_STREAM, &[], &[], 2000);
            let _ = link.command(CMD_FRAME_RATE, &[POWER_ON_FPS], &[], 2000);
            let last = self.last.as_ref().map(to_native);
            if let Some(png) = last.and_then(|native| encode_frame(&native).ok()) {
                let length = (png.len() as u32).to_be_bytes();
                let _ = link.command(CMD_UPLOAD_PNG, &length, &png, 5000);
            }
        }
        self.last = None;
        self.conn.close();
        let counted = self.conn.link.counts();
        let of = |name: &str| counted.get(name).copied().unwrap_or(0.0);
        if of("blocks") > 0.0 {
            self.notes.push(format!(
                "video: {} blocks, {:.1} MB, waited {} times, deepest queue {}",
                of("blocks") as u64,
                of("bytes") / 1e6,
                of("waits") as u64,
                of("max_depth") as u64
            ));
        }
        if of("pictures") > 0.0 {
            let late = of("late") as u64;
            let mut said = format!(
                "video: the engine sent {} pictures, {late} of them late",
                of("pictures") as u64
            );
            if late > 0 && counted.contains_key("came_late") {
                said += &format!(
                    " ({} made after their time; the latest {:.0} ms past it)",
                    of("came_late") as u64,
                    of("late_most_ms")
                );
            }
            self.notes.push(said);
        }
        if counted.contains_key("x264_ms") && of("frames") > 0.0 {
            let encoder = if of("card") != 0.0 {
                "the graphics card's encoder"
            } else {
                "x264"
            };
            self.notes.push(format!(
                "video: the engine took {:.1} ms a frame: {:.1} composing it, {:.1} its YUV, \
                 {:.1} {encoder}",
                of("encode_ms"),
                of("compose_ms"),
                of("yuv_ms"),
                of("x264_ms")
            ));
        }
    }
}

/// A TURZX panel shown single pictures (TurzxUsbDisplay), the engine having it: every frame
/// as a PNG to the panel's upper layer (about 9 a second on the 9.2"), or as a JPEG to the
/// layer under it (`device.still`; the 5.2" shows no PNG).
pub struct TurzxStills {
    setup: Setup,
    conn: Connection,
    /// the PNG layer was made transparent on this connection
    layer_clear: bool,
    brightness: Option<i64>,
    pub counters: Counters,
    notes: Vec<String>,
}

impl TurzxStills {
    pub fn open(setup: Setup) -> Result<TurzxStills> {
        let conn = Connection::open(&setup)?;
        Ok(TurzxStills {
            setup,
            conn,
            layer_clear: false,
            brightness: None,
            counters: Counters::default(),
            notes: Vec::new(),
        })
    }

    pub fn model(&self) -> Option<&'static PanelModel> {
        self.conn.model
    }

    pub fn describe(&self) -> String {
        format!(
            "{}, single pictures through the engine ({})",
            self.conn.describe(),
            self.conn.link.place()
        )
    }

    pub fn set_brightness(&mut self, percent: i64) -> Result<()> {
        self.brightness = Some(percent);
        self.conn
            .link
            .command(CMD_BRIGHTNESS, &[brightness_arg(percent)], &[], 2000)
            .map(|_| ())
    }

    fn send_png(&mut self, png: &[u8]) -> Result<()> {
        let length = (png.len() as u32).to_be_bytes();
        self.conn.link.command(CMD_UPLOAD_PNG, &length, png, 5000)?;
        self.counters.bytes += png.len() as u64;
        Ok(())
    }

    fn send_jpeg(&mut self, jpeg: &[u8]) -> Result<()> {
        if !self.layer_clear {
            // The PNG layer lies over the JPEG one and keeps what it showed last, across
            // reconnects too: an opaque picture there would hide every JPEG. So it is made
            // transparent once a connection, as the video mode does.
            let layer = transparent_layer(self.conn.native())?;
            self.send_png(&layer)?;
            self.layer_clear = true;
        }
        let length = (jpeg.len() as u32).to_be_bytes();
        self.conn
            .link
            .command(CMD_UPLOAD_JPEG, &length, jpeg, 5000)?;
        self.counters.bytes += jpeg.len() as u64;
        Ok(())
    }

    fn send(&mut self, data: &[u8], jpeg: bool) -> Result<()> {
        if jpeg {
            self.send_jpeg(data)
        } else {
            self.send_png(data)
        }
    }

    /// show: the frame as the panel lies, as a picture. A panel that stopped answering (a
    /// replug, standby or a session that crashed leaves the link out of step) is opened once
    /// more, which usually fixes it.
    pub fn show(&mut self, frame: &Image) -> Result<()> {
        let native = to_native(frame);
        let jpeg = still_format(&self.setup.still, self.conn.model) == "jpeg";
        let data = if jpeg {
            encode_jpeg(&native, JPEG_QUALITY, self.conn.model)?
        } else {
            encode_frame(&native)?
        };
        let Err(why) = self.send(&data, jpeg) else {
            return Ok(());
        };
        self.notes
            .push(format!("panel stopped answering ({why}); reconnecting"));
        self.counters.usb_errors += 1;
        self.conn.close();
        self.conn = Connection::open(&self.setup)?;
        self.layer_clear = false;
        if let Some(percent) = self.brightness {
            self.set_brightness(percent)?;
        }
        self.send(&data, jpeg)
    }

    pub fn notes(&mut self) -> Vec<String> {
        std::mem::take(&mut self.notes)
    }

    pub fn close(&mut self) {
        self.conn.close();
    }
}

/// A TURZX USB panel, in the mode config.toml and the panel allow (TurzxDisplay._driver).
pub enum Turzx {
    Video(Box<TurzxVideo>),
    Stills(Box<TurzxStills>),
}

impl Turzx {
    /// The panel opened: showing video where `[video] mode` says so ("auto": on a model it
    /// is proven on, with the standby clip's name set), else single pictures. What keeps
    /// "auto" from the video is noted.
    pub fn open(setup: Setup) -> Result<Turzx> {
        let model = setup.model.or_else(|| match &setup.test_link {
            None if setup.video.mode == "auto" => usb::present_model(&setup.beside, 0),
            _ => None,
        });
        let (video, missing) = video_mode(&setup.video, model);
        if video {
            return TurzxVideo::open(setup).map(|panel| Turzx::Video(Box::new(panel)));
        }
        let mut panel = TurzxStills::open(setup)?;
        if !missing.is_empty() {
            panel
                .notes
                .push(format!("video mode off: {}", missing.join("; ")));
        }
        Ok(Turzx::Stills(Box::new(panel)))
    }

    pub fn model(&self) -> Option<&'static PanelModel> {
        match self {
            Turzx::Video(panel) => panel.model(),
            Turzx::Stills(panel) => panel.model(),
        }
    }

    pub fn describe(&self) -> String {
        match self {
            Turzx::Video(panel) => panel.describe(),
            Turzx::Stills(panel) => panel.describe(),
        }
    }

    /// Whether the panel shows video: it is drawn for at `[video] fps`.
    pub fn streams(&self) -> bool {
        matches!(self, Turzx::Video(_))
    }

    pub fn show(&mut self, frame: Image, planned: Option<f64>) -> Result<()> {
        match self {
            Turzx::Video(panel) => panel.show(frame, planned),
            Turzx::Stills(panel) => panel.show(&frame),
        }
    }

    pub fn set_brightness(&mut self, percent: i64) -> Result<()> {
        match self {
            Turzx::Video(panel) => panel.set_brightness(percent),
            Turzx::Stills(panel) => panel.set_brightness(percent),
        }
    }

    pub fn notes(&mut self) -> Vec<String> {
        match self {
            Turzx::Video(panel) => panel.notes(),
            Turzx::Stills(panel) => panel.notes(),
        }
    }

    pub fn counters(&self) -> Counters {
        match self {
            Turzx::Video(panel) => panel.counters,
            Turzx::Stills(panel) => panel.counters,
        }
    }

    /// The video's bit rate cap in force, Mbit/s (0: none, or no video).
    pub fn bit_rate(&self) -> f64 {
        match self {
            Turzx::Video(panel) => panel.bit_rate,
            Turzx::Stills(_) => 0.0,
        }
    }

    pub fn close(&mut self) {
        match self {
            Turzx::Video(panel) => panel.close(),
            Turzx::Stills(panel) => panel.close(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_parts_of_a_frame_that_changed() {
        let (w, h) = (64usize, 48usize);
        let before = vec![0u8; w * h * 3];
        assert!(changed_boxes(&before, &before, (w, h), 16).is_empty());
        let mut now = before.clone();
        now[(20 * w + 33) * 3 + 1] = 9; // (a pixel at 33, 20)
        assert_eq!(changed_boxes(&before, &now, (w, h), 16), [[32, 16, 48, 32]]);
        now[(35 * w + 40) * 3] = 1; // (and one below it, in the next band: one box)
        assert_eq!(changed_boxes(&before, &now, (w, h), 16), [[32, 16, 48, 48]]);
        now[2] = 7; // (and the first pixel: a box of its own)
        assert_eq!(
            changed_boxes(&before, &now, (w, h), 16),
            [[0, 0, 16, 16], [32, 16, 48, 48]]
        );
        now[(47 * w + 63) * 3 + 2] = 1; // (the last pixel widens the last band alone)
        assert_eq!(
            changed_boxes(&before, &now, (w, h), 16),
            [[0, 0, 16, 16], [32, 16, 48, 32], [32, 32, 64, 48]]
        );
        // (a frame not made of whole blocks: compared pixel by pixel)
        let (before, mut now) = (vec![0u8; 5 * 3 * 3], vec![0u8; 5 * 3 * 3]);
        now[(5 + 4) * 3] = 1;
        assert_eq!(changed_boxes(&before, &now, (5, 3), 1), [[4, 1, 5, 2]]);
    }

    #[test]
    fn a_restart_waits_for_the_panel_to_leave_and_to_come_back() {
        let mut restart = Restart::new();
        assert!(restart.wait(|_| true).is_ok()); // (none under way)
        assert!(restart.allowed());
        restart.begin(0x92);
        assert_eq!(restart.wait(|_| true).unwrap_err().0, RESTARTING); // still there
        assert_eq!(restart.wait(|_| false).unwrap_err().0, RESTARTING); // gone
        assert_eq!(restart.wait(|_| true).unwrap_err().0, RESTARTING); // back, settling
        restart.back = Some(restart.clock() - Restart::SETTLE_S);
        assert!(restart.wait(|_| true).is_ok());
        assert!(restart.pid.is_none());
        // (three in a row, then no more until five minutes without one)
        restart.begin(0x92);
        restart.begin(0x92);
        assert!(!restart.allowed());
        restart.last = restart.clock() - Restart::RESET_S - 1.0;
        assert!(restart.allowed());
        // (one that never comes back)
        restart.begin(0x92);
        restart.gone = Some(restart.clock() - Restart::RETURN_S);
        assert!(restart.wait(|_| false).unwrap_err().0.contains("replug"));
    }

    #[test]
    fn what_the_engine_is_asked_for() {
        let video = Video {
            mode: "on".into(),
            fps: 25,
            device_fps: 30,
            crf: 20,
            preset: "superfast".into(),
            keyframe_s: 2.0,
            maxrate: "auto".into(),
            profile: "auto".into(),
            engine_encoder: "auto".into(),
            local_clip: "clip".into(),
            video_range: "auto".into(),
        };
        let options = engine_options((1920, 480), &video, "turing-9.2-usb", false, 2.0, "x264");
        assert_eq!(
            options.join(" "),
            "--size 1920x480 --turn 270 --fps 25 --crf 20 --kbit 2000 --keyint 50 --preset \
             superfast --profile high --x264 no-deblock=1:subme=0 --lead-ms 80"
        );
        let options = engine_options((480, 1920), &video, "", true, 1.5, "auto");
        assert!(options.join(" ").ends_with(
            "--profile baseline --x264 no-deblock=1:subme=0 --encoder auto --full --lead-ms 80"
        ));
        assert_eq!(
            jpeg_width(crate::models::find_model("turing-9.2-usb"), 480),
            464
        );
        assert_eq!(jpeg_width(None, 480), 480);
        assert_eq!(brightness_arg(70), 71);
        assert_eq!(
            firmware_of(b"\x00\x00turzx_0001_0015\x00rest"),
            "turzx_0001_0015"
        );
    }
}
