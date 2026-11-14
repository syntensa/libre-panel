//! The panel's own start screen from the app (editor/server.py `_start_screen*`,
//! devices/panel_files.py): the TURZX USB panels whose boot logo and standby clip Libre Panel
//! makes, the app's pictures of them, and the files as the panel takes them.

use std::collections::VecDeque;
use std::sync::Mutex;

use lp_devices::models::PanelModel;
use lp_pixels::{Image, Mode};
use lp_py::{Dict, Value};
use lp_render::boot;

/// The boot logo's file on the panel, and the standby clip's (the vendor app's name).
pub const BOOT_LOGO: &str = "usr/data/boot.jpg";
pub const STANDBY_CLIP: &str = "usr/data/standby.h264";
/// the panels the boot picture and the standby clip were measured on (panel_files.py
/// MEASURED: SPUR II on the 9.2"); the other USB panels share its firmware, untried
pub const MEASURED: &[&str] = &["turing-9.2-usb"];
/// A file on the panel is at most this large.
pub const MAX_FILE: usize = 1024 * 1024;
/// The boot logo's JPEG: (quality, a colour every pixel) in turn until it fits.
const JPEG_STEPS: [(i64, bool); 6] = [
    (95, true),
    (92, true),
    (90, false),
    (85, false),
    (75, false),
    (60, false),
];
/// The standby clip's x264 crf: this first, coarser in steps of 2 up to the most where it does
/// not fit, finer where it fills less than 40 % of the room.
const CLIP_CRF: i64 = 20;
const CLIP_CRF_MIN: i64 = 17;
const CLIP_CRF_MAX: i64 = 30;
/// How many of the app's pictures and of the files are kept (lru_cache's maxsize).
const PICTURES_KEPT: usize = 16;
const FILES_KEPT: usize = 4;

/// models: the panels this works on: the TURZX USB ones.
pub fn models() -> impl Iterator<Item = &'static PanelModel> {
    lp_devices::models::MODELS
        .iter()
        .filter(|model| model.protocol == "usb-turing")
}

/// model_of: a TURZX USB panel by its id.
pub fn model_of(id: &str) -> Option<&'static PanelModel> {
    models().find(|model| model.id == id)
}

/// design_of: a look of the panel's own screens by its name (Libre Panel's own for none).
pub fn design_of(name: &str) -> Option<&'static str> {
    let name = if name.is_empty() { boot::DEFAULT } else { name };
    boot::DESIGNS.iter().copied().find(|design| *design == name)
}

/// The app's pictures and the files made, the newest last.
#[derive(Default)]
pub struct Kept {
    pictures: Mutex<VecDeque<(String, Vec<u8>)>>,
    files: Mutex<VecDeque<(String, Vec<u8>)>>,
}

fn kept_or(
    kept: &Mutex<VecDeque<(String, Vec<u8>)>>,
    most: usize,
    key: String,
    make: impl FnOnce() -> Result<Vec<u8>, String>,
) -> Result<Vec<u8>, String> {
    {
        let mut list = kept.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(at) = list.iter().position(|(k, _)| *k == key) {
            let found = list.remove(at).expect("(there)");
            list.push_back(found.clone());
            return Ok(found.1);
        }
    }
    let made = make()?;
    let mut list = kept.lock().unwrap_or_else(|e| e.into_inner());
    list.push_back((key, made.clone()));
    while list.len() > most {
        list.pop_front();
    }
    Ok(made)
}

impl Kept {
    /// Every picture and file kept let go (light mode: the app is closed).
    pub fn forget(&self) {
        for kept in [&self.pictures, &self.files] {
            *kept.lock().unwrap_or_else(|e| e.into_inner()) = VecDeque::new();
        }
    }

    /// cached_preview: the app's picture of the boot logo (PNG) or of the standby clip (an
    /// animated WebP), `width` px wide.
    pub fn preview(
        &self,
        env: &lp_theme::Env,
        model: &PanelModel,
        what: &str,
        width: i64,
        way: &str,
        design: &str,
    ) -> Result<Vec<u8>, String> {
        let key = format!("{}|{what}|{width}|{way}|{design}", model.id);
        kept_or(&self.pictures, PICTURES_KEPT, key, || match what {
            "clip" => preview_clip(env, model, width, way, design),
            _ => preview_png(env, model, width, way, design),
        })
    }

    /// cached_file: the boot logo's JPEG or the standby clip as the panel takes them (in full
    /// range with `full`; turned round for a panel mounted upside down, `turned`, as they are
    /// written); the clip needs ffmpeg.
    #[allow(clippy::too_many_arguments)]
    pub fn file(
        &self,
        env: &lp_theme::Env,
        model: &PanelModel,
        what: &str,
        way: &str,
        turned: bool,
        design: &str,
        full: bool,
    ) -> Result<Vec<u8>, String> {
        let key = format!("{}|{what}|{way}|{turned}|{design}|{full}", model.id);
        kept_or(&self.files, FILES_KEPT, key, || match what {
            "clip" => {
                let ffmpeg = lp_devices::h264::find_ffmpeg("")?;
                let frames =
                    boot::standby_frames(env, model, None, way, design, 1.0).map_err(|e| e.0)?;
                let frames: Vec<Image> = (frames.into_iter())
                    .map(|frame| as_mounted(frame, turned))
                    .collect();
                clip_h264(&ffmpeg, &frames, full)
            }
            _ => {
                let picture = boot::boot_logo(env, model, way, design, 1.0).map_err(|e| e.0)?;
                logo_jpeg(&as_mounted(picture, turned))
            }
        })
    }
}

/// _preview_scale: how much smaller the app's picture is drawn than the panel's (at most as
/// large).
fn preview_scale(model: &PanelModel, width: i64, way: &str) -> f64 {
    (width as f64 / model.size(way).0 as f64).min(1.0)
}

/// preview_png: the boot logo as a PNG for the app, drawn at that size.
fn preview_png(
    env: &lp_theme::Env,
    model: &PanelModel,
    width: i64,
    way: &str,
    design: &str,
) -> Result<Vec<u8>, String> {
    let scale = preview_scale(model, width, way);
    let picture = boot::boot_logo(env, model, way, design, scale).map_err(|e| e.0)?;
    lp_devices::png::encode_optimized(&picture)
}

/// preview_clip: the standby clip as an animated WebP for the app, at its rate, drawn at
/// that size (quality 90, method 4, looping).
fn preview_clip(
    env: &lp_theme::Env,
    model: &PanelModel,
    width: i64,
    way: &str,
    design: &str,
) -> Result<Vec<u8>, String> {
    let scale = preview_scale(model, width, way);
    let frames = boot::standby_frames(env, model, None, way, design, scale).map_err(|e| e.0)?;
    let duration = (1000.0 / f64::from(boot::CLIP_FPS)).round_ties_even() as i64;
    lp_decode::save_webp_animation(&frames, duration, 0, 90.0, 4).map_err(|e| e.to_string())
}

/// picture_type: the media type of a picture of [`Kept::preview`].
pub fn picture_type(data: &[u8]) -> &'static str {
    if data.starts_with(b"RIFF") && data.get(8..12) == Some(b"WEBP") {
        "image/webp"
    } else {
        "image/png"
    }
}

fn native_rgb(picture: &Image) -> Result<Image, String> {
    let native = lp_devices::turzx::to_native(picture);
    match native.mode() {
        Mode::RGB => Ok(native),
        _ => native.convert(Mode::RGB).map_err(|e| e.0),
    }
}

/// logo_jpeg: the boot logo as the JPEG the panel shows: turned into its framebuffer, at
/// quality 95 with every pixel's colour, less where it would not fit in 1 MiB.
fn logo_jpeg(picture: &Image) -> Result<Vec<u8>, String> {
    let native = native_rgb(picture)?;
    for (quality, full) in JPEG_STEPS {
        let data = lp_decode::save_jpeg_options(&native, quality, full, true)
            .map_err(|e| e.to_string())?;
        if data.len() <= MAX_FILE {
            return Ok(data);
        }
    }
    Err("the boot logo does not fit in 1 MiB".into())
}

/// clip_command: ffmpeg for the standby clip as the panel's decoder plays it: one slice a
/// picture, a sequence every 11 pictures, baseline, no B pictures; in full range with `full`;
/// the dark parts as smooth as the light ones (aq-mode 3).
pub fn clip_command(
    ffmpeg: &std::path::Path,
    size: (usize, usize),
    crf: i64,
    full: bool,
) -> Vec<String> {
    use lp_devices::h264::{colour_tags, stamp, FULL_RANGE};
    let convert = if full {
        format!("{FULL_RANGE},format=yuv420p")
    } else {
        "format=yuv420p".into()
    };
    let mut command: Vec<String> = vec![ffmpeg.display().to_string()];
    command.extend(
        [
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
        ]
        .map(String::from),
    );
    command.push(format!("{}x{}", size.0, size.1));
    command.push("-framerate".into());
    command.push(boot::CLIP_FPS.to_string());
    command.extend(["-i", "-", "-an", "-vf"].map(String::from));
    command.push(convert);
    command.extend(
        [
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-preset",
            "slow",
            "-threads",
            "1",
            "-crf",
        ]
        .map(String::from),
    );
    command.push(crf.to_string());
    command.extend(colour_tags(full));
    command.push("-x264-params".into());
    command.push(
        "sliced-threads=0:bframes=0:scenecut=0:keyint=11:min-keyint=11:repeat-headers=1\
         :aq-mode=3"
            .into(),
    );
    command.extend(stamp(full));
    command.extend(["-f", "h264", "-"].map(String::from));
    command
}

/// clip_h264: the standby clip's pictures turned into the framebuffer and encoded as finely
/// as fits in 1 MiB.
fn clip_h264(ffmpeg: &std::path::Path, frames: &[Image], full: bool) -> Result<Vec<u8>, String> {
    let native: Vec<Image> = frames.iter().map(native_rgb).collect::<Result<_, _>>()?;
    let Some(first) = native.first() else {
        return Err("the standby clip has no pictures".into());
    };
    let size = first.size();
    let raw: Vec<u8> = native
        .iter()
        .flat_map(|f| f.as_bytes().iter().copied())
        .collect();
    let encoded = |crf: i64| -> Result<Vec<u8>, String> {
        let command = clip_command(ffmpeg, size, crf, full);
        let failed = |why: String| format!("ffmpeg could not make the standby clip: {why}");
        let mut child = crate::capture::quiet(&mut std::process::Command::new(&command[0]))
            .args(&command[1..])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| failed(e.to_string()))?;
        let mut stdin = child.stdin.take().expect("(piped)");
        let input = raw.clone();
        let writer = std::thread::spawn(move || {
            use std::io::Write;
            let _ = stdin.write_all(&input);
        });
        let done = child
            .wait_with_output()
            .map_err(|e| failed(e.to_string()))?;
        let _ = writer.join();
        if !done.status.success() || done.stdout.is_empty() {
            let tail = &done.stderr[done.stderr.len().saturating_sub(300)..];
            return Err(failed(bytes_repr(tail)));
        }
        Ok(done.stdout)
    };
    let mut crf = CLIP_CRF;
    let mut data = encoded(crf)?;
    while data.len() > MAX_FILE && crf < CLIP_CRF_MAX {
        crf += 2;
        data = encoded(crf)?;
    }
    if data.len() > MAX_FILE {
        return Err("the standby clip does not fit in 1 MiB".into());
    }
    if (data.len() as f64) < MAX_FILE as f64 * 0.4 && crf == CLIP_CRF {
        let finer = encoded(CLIP_CRF_MIN)?;
        if finer.len() <= MAX_FILE {
            data = finer;
        }
    }
    if lp_devices::h264::slice_count(&data) != native.len() {
        return Err("the standby clip came out with more than one slice a picture".into());
    }
    Ok(data)
}

// -- on the panel -------------------------------------------------------------------------------

const FOLDER: &str = "usr/data";
const LIST_PIECES: usize = 20;
const PIECE: usize = 512;
const ACK: u8 = 0xC8;
const CMD_WRITE_FILE: u8 = 40;
const CMD_DELETE_FILE: u8 = 42;
const CMD_FILE_SIZE: u8 = 98;
const CMD_LIST: u8 = 99;
const CMD_PLAY_LOCAL: u8 = 110;
const CMD_SHOW_JPEG: u8 = 113;
const CMD_SAVE_SETTINGS: u8 = 125;
const CMD_FRAME_RATE: u8 = 15;
/// the settings' start mode: play the clip they name
const START_WITH_CLIP: u8 = 2;
/// restart, halt, and the other save of the settings: never sent
const NEVER: [u8; 3] = [11, 12, 13];

fn note(message: &str) {
    crate::logs::log(
        crate::logs::Level::Info,
        "libre_panel.devices.panel_files",
        message,
    );
}

fn warn(message: &str) {
    crate::logs::log(
        crate::logs::Level::Warning,
        "libre_panel.devices.panel_files",
        message,
    );
}

/// checked_name: a file name as the panel takes it: relative, below `usr/data/`, no `..`.
pub fn checked_name(name: &str) -> Result<Vec<u8>, String> {
    let shown = lp_py::repr(name);
    if !name.starts_with(&format!("{FOLDER}/")) || name.starts_with('/') {
        return Err(format!("{shown}: only files below {FOLDER}/ are written"));
    }
    let plain =
        name.split('/').all(|part| !matches!(part, "" | "." | "..")) && !name.contains('\\');
    let raw = name.as_bytes();
    if !plain || !name.is_ascii() || raw.len() > 200 || !raw.iter().all(|b| (33..127).contains(b)) {
        return Err(format!("{shown}: not a plain file name"));
    }
    Ok(raw.to_vec())
}

fn file_args(name: &[u8], size: usize) -> Vec<u8> {
    let mut args = (name.len() as u32).to_be_bytes().to_vec();
    args.extend_from_slice(&(size as u32).to_be_bytes());
    args.extend_from_slice(name);
    args
}

/// names_in: the file names in a folder's listing as the panel gives it: the plain names in
/// the text, in order, once each (re.findall of `[A-Za-z0-9_][A-Za-z0-9_.\-]*\.[A-Za-z0-9]+`).
pub fn names_in(listing: &[u8]) -> Vec<String> {
    let text: Vec<u8> = listing
        .iter()
        .map(|&b| if b == 0 { b'\n' } else { b })
        .collect();
    let first = |b: u8| b.is_ascii_alphanumeric() || b == b'_';
    let inner = |b: u8| first(b) || b == b'.' || b == b'-';
    let mut found: Vec<String> = Vec::new();
    let mut i = 0;
    while i < text.len() {
        if !first(text[i]) {
            i += 1;
            continue;
        }
        let run_end = (i + 1..text.len())
            .find(|&k| !inner(text[k]))
            .unwrap_or(text.len());
        // the last dot of the run followed by a letter or digit: the greedy match's end
        let matched = (i + 1..run_end)
            .rev()
            .find(|&j| text[j] == b'.' && text.get(j + 1).is_some_and(u8::is_ascii_alphanumeric))
            .map(|j| {
                (j + 1..text.len())
                    .find(|&k| !text[k].is_ascii_alphanumeric())
                    .unwrap_or(text.len())
            });
        match matched {
            Some(end) => {
                let token: String = text[i..end].iter().map(|&b| char::from(b)).collect();
                if !found.contains(&token) {
                    found.push(token);
                }
                i = end;
            }
            None => i += 1,
        }
    }
    found
}

/// PanelFiles: the file commands on an open panel.
struct PanelFiles<'a> {
    link: &'a lp_devices::turzx::FileLink,
}

impl PanelFiles<'_> {
    fn command(&self, cmd: u8, args: &[u8], payload: &[u8], timeout_ms: u32) -> Result<(), String> {
        if NEVER.contains(&cmd) {
            return Err(format!("command {cmd} is never sent"));
        }
        self.link
            .command(cmd, args, payload, timeout_ms)
            .map(drop)
            .map_err(|e| e.to_string())
    }

    /// listing: the names in usr/data, as the panel lists them (all 20 pieces are fetched).
    fn listing(&self) -> Result<Vec<String>, String> {
        let mut pieces = Vec::new();
        for n in 0..LIST_PIECES {
            let reply = self
                .link
                .ask(CMD_LIST, &file_args(FOLDER.as_bytes(), 0), false)
                .map_err(|e| e.to_string())?;
            if n == 0 {
                let head: Vec<String> = reply.iter().take(32).map(|b| format!("{b:02x}")).collect();
                crate::logs::log(
                    crate::logs::Level::Debug,
                    "libre_panel.devices.panel_files",
                    &format!("start screen: listing {FOLDER} begins {}", head.join(" ")),
                );
            }
            let header = reply.first() == Some(&CMD_LIST) && reply.get(1) == Some(&ACK);
            let piece = if header {
                reply.get(8..).unwrap_or_default()
            } else {
                &reply[..]
            };
            pieces.extend_from_slice(&piece[..piece.len().min(PIECE)]);
        }
        Ok(names_in(&pieces))
    }

    /// noted_listing: the listing before a file is written, for the log; one that the panel
    /// does not give does not stop the writing.
    fn noted_listing(&self) -> Option<Vec<String>> {
        match self.listing() {
            Ok(names) => {
                let said = if names.is_empty() {
                    "nothing listed".to_string()
                } else {
                    names.join(", ")
                };
                note(&format!("start screen: {FOLDER} holds {said}"));
                Some(names)
            }
            Err(why) => {
                warn(&format!(
                    "start screen: the panel's files could not be listed: {why}"
                ));
                self.link.drain();
                None
            }
        }
    }

    /// size: the size of a file of the panel's (0: none).
    fn size(&self, name: &str) -> Result<i64, String> {
        let reply = self
            .link
            .ask(CMD_FILE_SIZE, &file_args(&checked_name(name)?, 0), true)
            .map_err(|e| e.to_string())?;
        let Some(bytes) = reply.get(8..12) else {
            return Err("unpack requires a buffer of 4 bytes".into());
        };
        let size = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        Ok(if size == u32::MAX { 0 } else { i64::from(size) })
    }

    /// write: a file written, and checked that it has the size it should. Where the panel
    /// kept the file it had (a 9.2" kept its standby clip when a larger one came, it seems
    /// where there is no room for both at once), that one is taken away and the new one
    /// written again.
    fn write(&self, name: &str, data: &[u8]) -> Result<(), String> {
        let raw = checked_name(name)?;
        if data.is_empty() || data.len() > MAX_FILE {
            return Err(format!(
                "{name}: a file is 1 byte to 1 MiB, not {}",
                data.len()
            ));
        }
        self.command(CMD_WRITE_FILE, &file_args(&raw, data.len()), data, 20000)?;
        let mut written = self.size(name)?;
        if written != data.len() as i64 && written != 0 {
            note(&format!(
                "start screen: the panel kept the {name} it had ({written} bytes, not {}): \
                 taken away, written again",
                data.len()
            ));
            self.delete(name)?;
            self.command(CMD_WRITE_FILE, &file_args(&raw, data.len()), data, 20000)?;
            written = self.size(name)?;
        }
        if written != data.len() as i64 {
            return Err(format!(
                "{name}: the panel has {written} bytes of it, not {}",
                data.len()
            ));
        }
        Ok(())
    }

    fn delete(&self, name: &str) -> Result<(), String> {
        self.command(
            CMD_DELETE_FILE,
            &file_args(&checked_name(name)?, 0),
            &[],
            4000,
        )
    }

    fn show_jpeg(&self, name: &str) -> Result<(), String> {
        self.command(
            CMD_SHOW_JPEG,
            &file_args(&checked_name(name)?, 0),
            &[],
            4000,
        )
    }

    /// play: a clip of the panel's played at the rate of a cold start; its name goes into the
    /// panel's settings (in memory, until save_start).
    fn play(&self, name: &str) -> Result<(), String> {
        let raw = checked_name(name)?;
        self.command(CMD_FRAME_RATE, &[boot::CLIP_FPS as u8], &[], 4000)?;
        self.command(CMD_PLAY_LOCAL, &file_args(&raw, 0), &[], 4000)
    }

    /// save_start: the panel's settings saved: its brightness, to start with the clip `play`
    /// named, unturned, and to dim when the PC has been gone for 10 s.
    fn save_start(&self, brightness: i64) -> Result<(), String> {
        let arg = lp_devices::turzx::brightness_arg(brightness);
        self.command(
            CMD_SAVE_SETTINGS,
            &[arg, START_WITH_CLIP, 0, 0, 0, 0],
            &[],
            4000,
        )
    }
}

/// Which panel: the models the other panels name, and how many alike to pass over.
pub struct Where<'a> {
    pub beside: &'a [String],
    pub skip: usize,
}

/// _with_panel: `job` on the panel opened (closed again afterwards).
fn with_panel(
    model: Option<&'static PanelModel>,
    place: &Where,
    job: impl FnOnce(&PanelFiles, &'static PanelModel) -> Result<Dict, String>,
) -> Result<Dict, String> {
    let setup = crate::display::files_setup(model, place.beside)?;
    let link = lp_devices::turzx::FileLink::open(&setup, place.skip).map_err(|e| e.to_string())?;
    let Some(found) = link.model() else {
        return Err(lp_i18n::t("No TURZX USB panel found"));
    };
    job(&PanelFiles { link: &link }, found)
}

fn told(pairs: Vec<(&str, Value)>) -> Dict {
    pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect()
}

fn whole(n: i64) -> Value {
    Value::Int(n.into())
}

/// StartScreen.state: what is on the panel.
pub fn panel_state(model: Option<&'static PanelModel>, place: &Where) -> Result<Dict, String> {
    with_panel(model, place, |files, found| {
        let names = files.noted_listing().map_or(Value::None, |names| {
            Value::List(names.into_iter().map(Value::Str).collect())
        });
        Ok(told(vec![
            ("model", Value::Str(found.id.into())),
            ("files", names),
            ("boot_logo", whole(files.size(BOOT_LOGO)?)),
            ("standby_clip", whole(files.size(STANDBY_CLIP)?)),
        ]))
    })
}

/// The picture as a panel mounted upside down (`turned`: rotate = 180) shows it upright: turned
/// round, as its frames go to it.
fn as_mounted(picture: Image, turned: bool) -> Image {
    match turned {
        true => picture.transpose(lp_pixels::Transpose::Rotate180),
        false => picture,
    }
}

/// StartScreen.write_logo: the boot logo written and shown (it shows at the next cold start);
/// turned round for a panel mounted upside down (`turned`), as its frames are.
pub fn write_logo(
    env: &lp_theme::Env,
    model: Option<&'static PanelModel>,
    way: &str,
    turned: bool,
    place: &Where,
    design: &str,
) -> Result<Dict, String> {
    with_panel(model, place, |files, found| {
        let picture = boot::boot_logo(env, found, way, design, 1.0).map_err(|e| e.0)?;
        let data = logo_jpeg(&as_mounted(picture, turned))?;
        files.noted_listing();
        files.write(BOOT_LOGO, &data)?;
        files.show_jpeg(BOOT_LOGO)?;
        Ok(told(vec![
            ("model", Value::Str(found.id.into())),
            ("boot_logo", whole(data.len() as i64)),
            ("design", Value::Str(design.into())),
        ]))
    })
}

/// StartScreen.remove_logo: the boot logo taken away (the factory logo shows again).
pub fn remove_logo(model: Option<&'static PanelModel>, place: &Where) -> Result<Dict, String> {
    with_panel(model, place, |files, found| {
        files.delete(BOOT_LOGO)?;
        Ok(told(vec![
            ("model", Value::Str(found.id.into())),
            ("boot_logo", whole(files.size(BOOT_LOGO)?)),
        ]))
    })
}

/// StartScreen.write_clip: the standby clip written, played and saved as what the panel
/// starts with; in the range of values its decoder shows; turned round for a panel mounted
/// upside down (`turned`), as its frames are.
#[allow(clippy::too_many_arguments)]
pub fn write_clip(
    env: &lp_theme::Env,
    brightness: i64,
    model: Option<&'static PanelModel>,
    way: &str,
    turned: bool,
    place: &Where,
    design: &str,
    video_range: &str,
) -> Result<Dict, String> {
    let ffmpeg = lp_devices::h264::find_ffmpeg("")?;
    with_panel(model, place, |files, found| {
        let frames = boot::standby_frames(env, found, None, way, design, 1.0).map_err(|e| e.0)?;
        let frames: Vec<Image> = (frames.into_iter())
            .map(|frame| as_mounted(frame, turned))
            .collect();
        let full = lp_devices::turzx::full_range(video_range, Some(found));
        let data = clip_h264(&ffmpeg, &frames, full)?;
        files.noted_listing();
        files.write(STANDBY_CLIP, &data)?;
        files.play(STANDBY_CLIP)?;
        files.save_start(brightness)?;
        Ok(told(vec![
            ("model", Value::Str(found.id.into())),
            ("standby_clip", whole(data.len() as i64)),
            ("name", Value::Str(STANDBY_CLIP.into())),
            ("design", Value::Str(design.into())),
        ]))
    })
}

/// repr() of Python bytes.
fn bytes_repr(data: &[u8]) -> String {
    let quote = if data.contains(&b'\'') && !data.contains(&b'"') {
        '"'
    } else {
        '\''
    };
    let mut out = format!("b{quote}");
    for &b in data {
        match b {
            b'\\' => out.push_str("\\\\"),
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            _ if char::from(b) == quote => {
                out.push('\\');
                out.push(quote);
            }
            32..=126 => out.push(char::from(b)),
            _ => out.push_str(&format!("\\x{b:02x}")),
        }
    }
    out.push(quote);
    out
}

/// What `GET /api/start-screen` answers (`_start_screen`).
pub struct Told<'a> {
    pub panel: i64,
    pub orientation: &'a str,
    pub model: Option<&'static str>,
    pub runs_panel: bool,
}

/// _start_screen's answer: the models, the one at hand, the panel, how it is mounted, whether
/// this app runs the panel, the files' names and the looks (Libre Panel's own first).
pub fn state(told: &Told) -> Value {
    let text = |t: &str| Value::Str(t.to_string());
    let listed: Vec<Value> = models()
        .map(|m| {
            let (w, h) = m.size(told.orientation);
            let mut one = Dict::default();
            one.insert("id".into(), text(m.id));
            one.insert("label".into(), Value::Str(m.label()));
            one.insert(
                "size".into(),
                Value::List(vec![Value::Int(w.into()), Value::Int(h.into())]),
            );
            one.insert("measured".into(), Value::Bool(MEASURED.contains(&m.id)));
            Value::Dict(one)
        })
        .collect();
    let mut names = Dict::default();
    names.insert("logo".into(), text(BOOT_LOGO));
    names.insert("clip".into(), text(STANDBY_CLIP));
    let mut answer = Dict::default();
    answer.insert("models".into(), Value::List(listed));
    answer.insert("model".into(), told.model.map_or(Value::None, text));
    answer.insert("panel".into(), Value::Int(told.panel.into()));
    answer.insert("orientation".into(), text(told.orientation));
    answer.insert("runs_panel".into(), Value::Bool(told.runs_panel));
    answer.insert("names".into(), Value::Dict(names));
    answer.insert(
        "designs".into(),
        Value::List(boot::DESIGNS.iter().map(|d| text(d)).collect()),
    );
    Value::Dict(answer)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bytes_are_said_as_python_says_them() {
        assert_eq!(bytes_repr(b"a'b\n\x01\\"), "b\"a'b\\n\\x01\\\\\"");
        assert_eq!(bytes_repr(b"x"), "b'x'");
    }

    #[test]
    fn the_clips_command_is_panel_files_pys() {
        let command = clip_command(std::path::Path::new("ffmpeg"), (480, 1920), 20, true);
        let joined = command.join(" ");
        assert!(joined.starts_with("ffmpeg -hide_banner -loglevel error -f rawvideo"));
        assert!(joined.contains("-s 480x1920 -framerate 30 -i - -an -vf scale=out_range=full"));
        assert!(joined.contains("-crf 20 -colorspace smpte170m"));
        assert!(joined.ends_with("matrix_coefficients=6 -f h264 -"));
    }
}
