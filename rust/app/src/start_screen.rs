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
    /// range with `full`); the clip needs ffmpeg.
    pub fn file(
        &self,
        env: &lp_theme::Env,
        model: &PanelModel,
        what: &str,
        way: &str,
        design: &str,
        full: bool,
    ) -> Result<Vec<u8>, String> {
        let key = format!("{}|{what}|{way}|{design}|{full}", model.id);
        kept_or(&self.files, FILES_KEPT, key, || match what {
            "clip" => {
                let ffmpeg = lp_devices::h264::find_ffmpeg("")?;
                let frames =
                    boot::standby_frames(env, model, None, way, design, 1.0).map_err(|e| e.0)?;
                clip_h264(&ffmpeg, &frames, full)
            }
            _ => logo_jpeg(&boot::boot_logo(env, model, way, design, 1.0).map_err(|e| e.0)?),
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
        let mut child = std::process::Command::new(&command[0])
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
            let mut one = Dict::new();
            one.insert("id".into(), text(m.id));
            one.insert("label".into(), Value::Str(m.label()));
            one.insert(
                "size".into(),
                Value::List(vec![Value::Int(w.into()), Value::Int(h.into())]),
            );
            one.insert("measured".into(), Value::Bool(m.video));
            Value::Dict(one)
        })
        .collect();
    let mut names = Dict::new();
    names.insert("logo".into(), text(BOOT_LOGO));
    names.insert("clip".into(), text(STANDBY_CLIP));
    let mut answer = Dict::new();
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
