//! Image widgets (render/renderer.py's _draw_image, _live_image, _asset_image, _slides, _slide,
//! _animation, _fitted, _blurred and _rounded; render/moving.py's read_animation): a picture a
//! sensor source or a service has (an album cover, a game's logo, the playing app's icon) or
//! one of the theme's files (turned as its EXIF says), fitted into the widget's box
//! (stretched, contained where it says, or covering it), blurred into a backdrop of its
//! colours, its corners rounded; a widget with no picture a clear piece of its size. A
//! slideshow of still pictures takes turns by the clock (the frame's time as a timestamp of the
//! computer's zone), a folder's pictures found as pathlib's glob finds them; an animated
//! picture (GIF, WebP, APNG) shows its frame of the moment from when it first showed. Videos,
//! and slideshows with a picture that moves: not drawn here yet.

use std::hash::{DefaultHasher, Hash, Hasher};
use std::rc::Rc;
use std::sync::Arc;

use lp_pixels::{chops, Draw, Filter, Image, Mode, ALL_CORNERS};
use lp_py::{Dict, Value};

use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Drawn, Renderer, MOST_PIXELS, UNREAD};
use crate::shapes::SUPERSAMPLE;
use crate::widget;

/// A picture `w` x `h` (ints of the theme), refused where no machine makes it.
fn sized(w: i64, h: i64) -> Result<(usize, usize)> {
    if w < 0 || h < 0 || w.saturating_mul(h) > MOST_PIXELS {
        return Err(Failed("a picture too large".into()));
    }
    Ok((w as usize, h as usize))
}

/// _fitted: `image` at w x h: stretched, inside it with room left (contain: at the left, in
/// the middle or at the right, by `align`) or filling it (cover), with LANCZOS.
fn fitted(image: &Image, w: usize, h: usize, fit: &str, align: &str) -> Result<Image> {
    match fit {
        "cover" => Ok(image.fit((w, h), Filter::Lanczos, 0.0, (0.5, 0.5))?),
        "contain" => {
            let inner = image.contain((w, h), Filter::Lanczos)?;
            let mut out = Image::blank(Mode::RGBA, w, h);
            let room = w as i64 - inner.width() as i64;
            let left = match align {
                "left" => 0,
                "right" => room,
                _ => room.div_euclid(2),
            };
            let top = (h as i64 - inner.height() as i64).div_euclid(2);
            out.paste(&inner, left, top, None)?;
            Ok(out)
        }
        _ => Ok(image.resize(w, h, Filter::Lanczos, None, None)?),
    }
}

/// _blurred: `image` at w x h (as fitted; 0: its own size) blurred by `radius` px: fitted and
/// blurred at a fraction of the size (premultiplied, so transparent edges stay clean), then
/// drawn up.
fn blurred(image: &Image, radius: f64, w: i64, h: i64, fit: &str, align: &str) -> Result<Image> {
    let (w, h) = if w <= 0 || h <= 0 {
        (image.width(), image.height())
    } else {
        sized(w, h)?
    };
    let scale = ((radius / 4.0).floor() as i64).clamp(1, 8);
    let part = |side: usize| (lp_py::round(side as f64 / scale as f64) as i64).max(1) as usize;
    let small = fitted(&image.convert(Mode::RGBA)?, part(w), part(h), fit, align)?;
    let small = small.convert(Mode::RGBa)?;
    let r = (radius / scale as f64) as f32;
    let small = small.gaussian_blur(r, r)?;
    Ok(small
        .resize(w, h, Filter::Bilinear, None, None)?
        .convert(Mode::RGBA)?)
}

/// _rounded: `image` with round corners of `radius` px, their mask drawn three times larger and
/// reduced (LANCZOS), multiplied into its alpha.
fn rounded(image: &Image, radius: i64) -> Result<Image> {
    let (w, h) = (image.width(), image.height());
    let s = SUPERSAMPLE;
    let (mw, mh) = sized(w as i64 * s as i64, h as i64 * s as i64)?;
    let mut mask = Image::blank(Mode::L, mw, mh);
    Draw::new(&mut mask).rounded_rectangle(
        (0.0, 0.0, mw as f64 - 1.0, mh as f64 - 1.0),
        radius.saturating_mul(s as i64) as f64,
        Some([255, 0, 0, 0]),
        None,
        1,
        ALL_CORNERS,
    )?;
    let mask = mask.resize(w, h, Filter::Lanczos, None, None)?;
    let mut out = image.clone();
    out.putalpha(&chops::multiply(&image.getchannel(3)?, &mask)?)?;
    Ok(out)
}

/// What a live picture is kept by: its name, mode, size and pixels (a new picture under the same
/// name is another).
pub(crate) fn picture_key(name: &str, image: &Image) -> Value {
    let mut hasher = DefaultHasher::new();
    image.as_bytes().hash(&mut hasher);
    Value::List(vec![
        name.into(),
        image.mode().name().into(),
        Value::int(image.width() as i64),
        Value::int(image.height() as i64),
        Value::Str(format!("{:016x}", hasher.finish())),
    ])
}

impl Renderer {
    /// _live_image's piece: the picture at (x, y), blurred into a backdrop (fitted as it says,
    /// filling the box), else fitted where the widget has a size, its corners rounded.
    pub(crate) fn live_image_piece(&self, w: &Dict, image: &Image) -> Result<Piece> {
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (iw, ih) = (widget::int(w, "w", 0), widget::int(w, "h", 0));
        let (fit, align) = (widget::text(w, "fit"), widget::text(w, "align"));
        let mut picture = image.convert(Mode::RGBA)?;
        let blur = widget::number(w, "blur", 0.0);
        if blur > 0.0 {
            picture = blurred(&picture, blur, iw, ih, fit, align)?;
        } else if iw > 0 && ih > 0 {
            let (fw, fh) = sized(iw, ih)?;
            picture = fitted(&picture, fw, fh, fit, align)?;
        }
        let radius = widget::int(w, "radius", 0);
        if radius > 0 {
            picture = rounded(&picture, radius)?;
        }
        Ok(Piece::new(picture, x, y))
    }
}

// -- the theme's own pictures -------------------------------------------------------------------

/// moving.VIDEO_SUFFIXES: what is a video (not drawn here yet).
const VIDEO_SUFFIXES: [&str; 5] = [".mp4", ".webm", ".mov", ".m4v", ".mkv"];
/// What _slides takes of a folder: pictures and videos.
const SLIDE_SUFFIXES: [&str; 10] = [
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mov", ".m4v", ".mkv",
];
/// The pictures that may move: GIF, WebP and PNG (APNG).
const MOVING_SUFFIXES: [&str; 3] = [".gif", ".webp", ".png"];
/// Renderer.IMAGE_BYTES and ANIMATION_BYTES: the pictures kept fitted, the animations' frames.
pub(crate) const IMAGE_BYTES: usize = 64 << 20;
pub(crate) const ANIMATION_BYTES: usize = 192 << 20;
/// moving.FRAMES_BYTES: the frames of one animated picture, at most.
const FRAMES_BYTES: u128 = 96 << 20;
/// moving.LEAST_FRAME_S, USUAL_FRAME_S: a frame said to show for less (or 0) shows 0.1 s.
const LEAST_FRAME_S: f64 = 0.02;
const USUAL_FRAME_S: f64 = 0.1;
/// How long a slideshow's folders are not read again (_slides: half a minute).
const SLIDES_KEPT: std::time::Duration = std::time::Duration::from_secs(30);

/// What a widget's picture is kept fitted by: the picture, the widget's size, fit and align.
pub(crate) type PictureKey = (String, i64, i64, String, String);
/// What an animation's frames are kept by: that and the widget's blur and radius.
pub(crate) type AnimationKey = (String, i64, i64, String, String, i64, i64);
/// Why Rust leaves a frame with a video to Python: it was told of no ffmpeg (the app finds it
/// as devices/h264.find_ffmpeg does; without it, Python's warns and draws none).
const NO_FFMPEG: &str = "videos without ffmpeg";
/// What a video is played by (a feed of its own): the video, the widget's size and fit, its
/// speed (a double's bits), its frames a second and whether its keyframes alone.
pub(crate) type VideoKey = (String, usize, usize, String, u64, u32, bool);

fn ends_with_any(rel: &str, suffixes: &[&str]) -> bool {
    let lower = lp_py::lower(rel);
    suffixes.iter().any(|s| lower.ends_with(s))
}

/// moving.is_video.
pub(crate) fn is_video(rel: &str) -> bool {
    ends_with_any(rel, &VIDEO_SUFFIXES)
}

/// Python's float % float (the sign of the divisor).
fn py_mod(x: f64, m: f64) -> f64 {
    let r = x % m;
    if r == 0.0 {
        0.0f64.copysign(m)
    } else if (m < 0.0) != (r < 0.0) {
        r + m
    } else {
        r
    }
}

/// Python's float // float (float_floor_div: the quotient snapped to the nearest whole).
fn py_floordiv(x: f64, w: f64) -> f64 {
    let m = x % w;
    let mut div = (x - m) / w;
    if m != 0.0 && (w < 0.0) != (m < 0.0) {
        div -= 1.0;
    }
    if div != 0.0 {
        let floor = div.floor();
        if div - floor > 0.5 {
            floor + 1.0
        } else {
            floor
        }
    } else {
        0.0f64.copysign(x / w)
    }
}

/// float() of a widget's number as Python takes it (an int past a float's range: its error).
fn number_of(value: &Value) -> Result<Option<f64>> {
    Ok(match value {
        Value::Int(n) => Some(lp_py::num::int_to_float(n).map_err(|e| Failed(e.msg))?),
        Value::Float(f) => Some(*f),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::Str(s) => lp_py::float_of_str(s),
        _ => None,
    })
}

/// moving.speed_of: the widget's speed within 0.25 and 4 (1: as made; where float() fails, 1).
pub(crate) fn speed_of(value: &Value) -> Result<f64> {
    Ok(match number_of(value)? {
        Some(speed) if !speed.is_nan() => speed.clamp(0.25, 4.0),
        _ => 1.0,
    })
}

/// PurePath.suffix of a path's last part: from its last dot, where that is neither its first
/// nor its last character.
fn suffix(name: &str) -> &str {
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => &name[i..],
        _ => "",
    }
}

/// The parts of a pattern as pathlib's _parse_path takes them: by slashes (on Windows
/// backslashes too), without empty parts and dots; "" after a closing slash. A part with "**"
/// that is not "**" alone fails as _make_selector fails at it: all its selectors are made
/// first, so before any folder is read.
fn pattern_parts(pattern: &str) -> Result<Vec<String>> {
    let sep = |c: char| c == '/' || (cfg!(windows) && c == '\\');
    let drive = cfg!(windows) && pattern.chars().nth(1) == Some(':');
    if pattern.starts_with(sep) || drive {
        return Err(Failed("Non-relative patterns are unsupported".into()));
    }
    let mut parts: Vec<String> = pattern
        .split(sep)
        .filter(|p| !p.is_empty() && *p != ".")
        .map(str::to_string)
        .collect();
    if pattern.ends_with(sep) {
        parts.push(String::new());
    }
    if parts.iter().any(|p| p != "**" && p.contains("**")) {
        return Err(Failed(
            "Invalid pattern: '**' can only be an entire path component".into(),
        ));
    }
    Ok(parts)
}

/// What finding a slideshow's pictures comes to where it finds none: Python's error, or what
/// Rust does not read yet.
type Found<T> = std::result::Result<T, Drawn>;

/// A part's match of a name: fnmatch's. On Windows pathlib matches without case
/// (re.IGNORECASE): as lower() takes it where both are ASCII and the part has no class of
/// characters; Rust says it does not match others there yet.
fn part_matches(name: &str, part: &str) -> Found<bool> {
    let matched = if cfg!(windows) {
        if !(name.is_ascii() && part.is_ascii() && !part.contains('[')) {
            return Err(Drawn::Unported(
                "slideshow patterns of other than ASCII on Windows".into(),
            ));
        }
        lp_py::fnmatch::fnmatchcase(&name.to_ascii_lowercase(), &part.to_ascii_lowercase())
    } else {
        lp_py::fnmatch::fnmatchcase(name, part)
    };
    matched.map_err(|e| Drawn::Failed(Failed(e.0)))
}

/// What Rust does not read of a folder yet: a name that is no Unicode (Python's are its
/// surrogates).
fn not_unicode() -> Drawn {
    Drawn::Unported("folders of names that are no Unicode".into())
}

/// The folders below `path`, itself the first (Path.walk's, top down: no folder a link leads
/// to).
fn folders_below(path: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
    out.push(path.to_path_buf());
    let Ok(entries) = std::fs::read_dir(path) else {
        return;
    };
    for entry in entries.flatten() {
        if entry.file_type().is_ok_and(|t| t.is_dir()) {
            folders_below(&entry.path(), out);
        }
    }
}

/// pathlib's selectors (Python 3.12): the paths below `path` that the pattern's `parts`
/// match, each part against the names of a folder (hidden ones too; a part before others
/// matches folders only), "**" any folders below, ".." the folder above.
fn select(
    path: &std::path::Path,
    parts: &[String],
    out: &mut Vec<std::path::PathBuf>,
) -> Found<()> {
    let Some((part, rest)) = parts.split_first() else {
        out.push(path.to_path_buf());
        return Ok(());
    };
    if part.is_empty() {
        out.push(path.to_path_buf());
        return Ok(());
    }
    if part == "**" {
        let skip = parts.iter().take_while(|p| *p == "**").count();
        let child = &parts[skip..];
        let mut starts = Vec::new();
        folders_below(path, &mut starts);
        let mut found = Vec::new();
        for start in &starts {
            select(start, child, &mut found)?;
        }
        if child.iter().any(|p| p == "**") {
            // (_DoubleRecursiveWildcardSelector: each path once)
            let mut seen = std::collections::HashSet::new();
            found.retain(|p| seen.insert(p.clone()));
        }
        out.extend(found);
        return Ok(());
    }
    if part == ".." {
        return select(&path.join(".."), rest, out);
    }
    let Ok(entries) = std::fs::read_dir(path) else {
        return Ok(());
    };
    for entry in entries.flatten() {
        if !rest.is_empty() && !std::fs::metadata(entry.path()).is_ok_and(|m| m.is_dir()) {
            continue;
        }
        let Some(name) = entry.file_name().to_str().map(str::to_string) else {
            return Err(not_unicode());
        };
        if part_matches(&name, part)? {
            select(&path.join(&name), rest, out)?;
        }
    }
    Ok(())
}

/// The relative parts of `path` below `root`, as text (None for a name that is no Unicode).
fn parts_below(root: &std::path::Path, path: &std::path::Path) -> Option<Vec<String>> {
    let rel = path.strip_prefix(root).ok()?;
    rel.components()
        .map(|c| c.as_os_str().to_str().map(str::to_string))
        .collect()
}

/// What reading an animated picture came to.
pub(crate) enum Read {
    /// its frames, finished
    Frames(Animation),
    /// a picture of one frame: drawn as a still
    Still,
    /// its finish failed (Python's raises)
    Failed(Failed),
    /// what Pillow raises reading it (or that Rust cannot say: Raised::Unported)
    Raised(lp_decode::Error),
}

/// The exceptions _animation and _moving go on from (a picture they take as not moving):
/// OSError and ValueError.
fn passed_over(e: &lp_decode::Error) -> bool {
    e.raised.is_os_error() || e.raised == lp_decode::Raised::ValueError
}

/// moving.Animation: the frames of an animated picture, finished, and when each one ends.
pub(crate) struct Animation {
    pub(crate) frames: Vec<Image>,
    ends: Vec<f64>,
}

impl Animation {
    /// The frame on show `seconds` into the animation, round and round.
    pub(crate) fn index(&self, seconds: f64) -> usize {
        let at = py_mod(seconds, *self.ends.last().expect("frames"));
        self.ends
            .iter()
            .position(|&end| at < end)
            .unwrap_or(self.ends.len() - 1)
    }

    pub(crate) fn size(&self) -> usize {
        self.frames.iter().map(|f| f.width() * f.height() * 4).sum()
    }

    /// The seconds of one round (Animation.length).
    pub(crate) fn length(&self) -> f64 {
        *self.ends.last().expect("frames")
    }
}

/// moving.read_animation: the frames of an animated picture one after the other, each passed
/// through `finish`, every second one (third ...) kept where all would take more than
/// FRAMES_BYTES, each showing as long as those it stands for.
pub(crate) fn read_animation(data: &[u8], finish: &dyn Fn(&Image) -> Result<Image>) -> Read {
    const ENOUGH: &str = "(enough)";
    // (Image.open, n_frames: a picture of one frame is a still, its pixels not read)
    match lp_decode::n_frames(data) {
        Err(e) => return Read::Raised(e),
        Ok(count) if count <= 1 => return Read::Still,
        Ok(_) => {}
    }
    let (mut frames, mut ends) = (Vec::new(), Vec::new());
    let (mut first, mut every, mut took, mut n) = (None, 1u128, 0.0f64, 0usize);
    let mut failed = None;
    let read = lp_decode::each_frame(data, &mut |count, frame| {
        let stop = || Err(lp_decode::Error::new(lp_decode::Raised::Unported, ENOUGH));
        if n == 0 {
            if count <= 1 {
                return stop();
            }
            match finish(&frame.image) {
                Ok(image) => {
                    let bytes = count as u128 * image.width() as u128 * image.height() as u128 * 4;
                    every = bytes.div_ceil(FRAMES_BYTES).max(1);
                    first = Some(image);
                }
                Err(e) => {
                    failed = Some(e);
                    return stop();
                }
            }
        }
        let seconds = frame.duration.unwrap_or(0.0) / 1000.0;
        took += if seconds >= LEAST_FRAME_S {
            seconds
        } else {
            USUAL_FRAME_S
        };
        if n as u128 % every == every - 1 || n + 1 == count {
            let image = match first.take() {
                Some(image) if n == 0 => image,
                _ => match finish(&frame.image) {
                    Ok(image) => image,
                    Err(e) => {
                        failed = Some(e);
                        return stop();
                    }
                },
            };
            frames.push(image);
            ends.push(took);
        }
        n += 1;
        Ok(())
    });
    if let Some(e) = failed {
        return Read::Failed(e);
    }
    match read {
        Err(e) if e.msg != ENOUGH => Read::Raised(e),
        _ if frames.len() <= 1 => Read::Still,
        _ => Read::Frames(Animation { frames, ends }),
    }
}

/// moving.read_animation of a file's bytes, the frames as they are: how many it keeps (None
/// for a still), or what Pillow raises (Raised::Unported where Rust cannot say).
pub fn animation_read(data: &[u8]) -> std::result::Result<Option<usize>, lp_decode::Error> {
    match read_animation(data, &|image| Ok(image.clone())) {
        Read::Frames(animation) => Ok(Some(animation.frames.len())),
        Read::Still => Ok(None),
        Read::Failed(e) => Err(lp_decode::Error::new(lp_decode::Raised::Unported, e.0)),
        Read::Raised(e) => Err(e),
    }
}

impl Renderer {
    /// _slides: the widget's picture and its slideshow's others (one a line: a picture's name,
    /// "@" a live one, a pattern a folder's pictures and videos, sorted), folders read every
    /// half minute.
    pub(crate) fn slides(&mut self, w: &Dict) -> Found<Vec<String>> {
        let id = widget::text(w, "id").to_string();
        if let Some((at, found)) = self.slide_lists.get(&id) {
            if at.elapsed() < SLIDES_KEPT {
                return Ok(found.clone());
            }
        }
        let src = widget::text(w, "src");
        let mut found: Vec<String> = if src.is_empty() {
            Vec::new()
        } else {
            vec![src.to_string()]
        };
        let root = self.theme.root.clone();
        for line in lp_py::splitlines(widget::text(w, "slides")) {
            let line = lp_py::strip(line);
            if line.starts_with('@') {
                found.push(line.to_string());
                continue;
            }
            let Some(root) = root.as_ref().filter(|_| !line.is_empty()) else {
                continue;
            };
            if !line.contains(['*', '?', '[']) {
                found.push(line.to_string());
                continue;
            }
            let mut matched = Vec::new();
            let parts = pattern_parts(line).map_err(Drawn::Failed)?;
            select(root, &parts, &mut matched)?;
            let mut files: Vec<Vec<String>> = matched
                .iter()
                .filter(|p| std::fs::metadata(p).is_ok_and(|m| m.is_file()))
                .map(|p| parts_below(root, p).ok_or_else(not_unicode))
                .collect::<Found<_>>()?;
            // (sorted as paths are: part by part, on Windows without case)
            if cfg!(windows) {
                files.sort_by_cached_key(|parts| {
                    parts.iter().map(|p| lp_py::lower(p)).collect::<Vec<_>>()
                });
            } else {
                files.sort();
            }
            for parts in files {
                let rel = parts.join("/");
                let name = parts.last().map_or("", String::as_str);
                if lp_theme::model::resolve_asset(root, &Value::from(rel.as_str())).is_err() {
                    continue; // (nothing outside the theme folder)
                }
                if SLIDE_SUFFIXES.contains(&lp_py::lower(suffix(name)).as_str()) {
                    found.push(rel);
                }
            }
        }
        self.slide_lists
            .insert(id, (std::time::Instant::now(), found.clone()));
        Ok(found)
    }

    /// The pictures of each image widget's slideshow as _slides finds them, by widget id; where
    /// it finds none, ("error", Python's error) or ("unported", what Rust does not read yet).
    #[allow(clippy::type_complexity)]
    pub fn image_slides(
        &mut self,
    ) -> Vec<(
        String,
        std::result::Result<Vec<String>, (&'static str, String)>,
    )> {
        let widgets: Vec<Dict> = self
            .widgets
            .iter()
            .filter(|w| widget::text(w, "type") == "image")
            .cloned()
            .collect();
        widgets
            .iter()
            .map(|w| {
                let id = widget::text(w, "id").to_string();
                let found = self.slides(w).map_err(|drawn| match drawn {
                    Drawn::Failed(e) => ("error", e.0),
                    Drawn::Unported(what) => ("unported", what),
                    Drawn::Piece(_) | Drawn::Nothing => ("error", "no list".into()),
                });
                (id, found)
            })
            .collect()
    }

    /// _moving: whether a slideshow's picture moves (a video, a picture of more than one
    /// frame: Image.open's n_frames), kept; one that is not there, outside the theme, or one
    /// Pillow fails at with an OSError or a ValueError does not. What else Pillow raises fails
    /// the widget; of one Rust cannot say Pillow's answer to: UNREAD.
    fn moves(&mut self, rel: &str) -> Found<bool> {
        if let Some(&found) = self.moves.get(rel) {
            return Ok(found);
        }
        let mut found = is_video(rel);
        if !found && ends_with_any(rel, &MOVING_SUFFIXES) {
            // (a name with a NUL: resolve_asset's ValueError, passed over)
            let root = self.theme.root.clone().filter(|_| !rel.contains('\0'));
            let data = root
                .and_then(|root| lp_theme::model::resolve_asset(&root, &Value::from(rel)).ok())
                .and_then(|path| std::fs::read(path).ok());
            if let Some(data) = data {
                found = match lp_decode::n_frames(&data) {
                    Ok(count) => count > 1,
                    Err(e) if passed_over(&e) => false,
                    Err(e) if e.raised == lp_decode::Raised::Unported => {
                        return Err(Drawn::Unported(UNREAD.into()));
                    }
                    Err(e) => return Err(Drawn::Failed(Failed(e.msg))),
                };
            }
        }
        self.moves.insert(rel.to_string(), found);
        Ok(found)
    }

    /// _slide: still pictures take turns by the clock, `seconds` each, the frame's time as a
    /// timestamp of the computer's zone (one picture alone shows, though Python takes the
    /// timestamp all the same: where that fails, so does the widget); a slideshow with one that
    /// moves: slide_moving.
    pub(crate) fn slide(
        &mut self,
        w: &Dict,
        pictures: &[String],
    ) -> std::result::Result<String, Drawn> {
        let seconds = match w.get("seconds") {
            Some(Value::Int(n)) => {
                let n = n.max(&num_bigint::BigInt::from(1)).clone();
                lp_py::num::int_to_float(&n).map_err(|e| Drawn::Failed(Failed(e.msg)))?
            }
            Some(Value::Float(f)) => f.max(1.0),
            _ => 10.0,
        };
        if pictures.len() == 1 {
            let now = self.now();
            return match lp_py::zones::local() {
                Some(local) => match lp_py::zones::timestamp(local, &now, self.fold()) {
                    Some(_) => Ok(pictures[0].clone()),
                    None => Err(Drawn::Failed(Failed(
                        "the frame's time has no timestamp".into(),
                    ))),
                },
                // (where Python cannot fail at it: datetime's years but their edges, on
                // Windows the C runtime's localtime from 1970 to 3000)
                None if (if cfg!(windows) { 1971..=2999 } else { 2..=9998 })
                    .contains(&now.year) =>
                {
                    Ok(pictures[0].clone())
                }
                None => Err(Drawn::Unported(
                    "pictures at the edges of the years without the computer's own time zone"
                        .into(),
                )),
            };
        }
        let mut moving = false;
        for rel in pictures {
            // (as any() asks: up to the first that moves)
            if self.moves(rel)? {
                moving = true;
                break;
            }
        }
        if moving {
            return self.slide_moving(w, pictures, seconds);
        }
        let Some(local) = lp_py::zones::local() else {
            return Err(Drawn::Unported(
                "slideshows without the computer's own time zone".into(),
            ));
        };
        let Some(stamp) = lp_py::zones::timestamp(local, &self.now(), self.fold()) else {
            return Err(Drawn::Unported(
                "a slideshow at a time that Python fails at".into(),
            ));
        };
        let turn = py_floordiv(stamp, seconds) as i128;
        Ok(pictures[turn.rem_euclid(pictures.len() as i128) as usize].clone())
    }

    /// _slide of a slideshow with a picture that moves (a video, an animated picture): each
    /// that moves plays whole rounds, as many as take `seconds` at least, and begins at its
    /// first frame; a still one shows `seconds`; each in turn, by the frame's time (render's
    /// `now`). A video's: Rust does not draw videos.
    fn slide_moving(
        &mut self,
        w: &Dict,
        pictures: &[String],
        seconds: f64,
    ) -> std::result::Result<String, Drawn> {
        let id = widget::text(w, "id").to_string();
        let now = self.at;
        let (mut index, began, ends) = self.shows.get(&id).copied().unwrap_or((-1, now, now));
        let count = pictures.len() as i64;
        if index < 0 || now >= ends || now < began || index >= count {
            index = (index + 1).rem_euclid(count);
            let rel = pictures[index as usize].clone();
            let lasts = self.slide_seconds(w, &rel, seconds)?;
            self.shows.insert(id.clone(), (index, now, now + lasts));
            // (an animated picture: from its first frame; a video: from its start)
            for (key, feed) in &self.feeds {
                if key.0 == rel {
                    feed.from_start();
                }
            }
            self.began.insert((id, rel), now);
        }
        Ok(pictures[index as usize].clone())
    }

    /// _slide_seconds: how long a picture of a slideshow shows: `seconds`; one that moves
    /// whole rounds, as many as take that long at least (at its speed).
    fn slide_seconds(&mut self, w: &Dict, rel: &str, seconds: f64) -> Found<f64> {
        if !self.moves(rel)? || self.theme.root.is_none() {
            return Ok(seconds);
        }
        let speed = speed_of(w.get("speed").unwrap_or(&Value::int(1))).map_err(Drawn::Failed)?;
        let length = if is_video(rel) {
            // (a video's length as ffmpeg reads it from the file; none where it cannot tell)
            let Some(ffmpeg) = self.ffmpeg.clone() else {
                return Err(Drawn::Unported(NO_FFMPEG.into()));
            };
            if rel.contains('\0') {
                // (resolve_asset's ValueError, which _slide_seconds does not catch)
                return Err(Drawn::Failed(Failed("embedded null byte".into())));
            }
            let root = self.theme.root.clone().unwrap_or_default();
            match lp_theme::model::resolve_asset(&root, &Value::from(rel)) {
                Ok(path) => self.video_info(rel, &ffmpeg, &path)?.length.unwrap_or(0.0),
                Err(_) => 0.0,
            }
        } else {
            match self.animation_of(w, rel)? {
                Some(animation) => animation.length(),
                None => 0.0,
            }
        };
        if length == 0.0 {
            return Ok(seconds);
        }
        let length = length / speed;
        Ok(if length < seconds {
            (seconds / length - 1e-6).ceil() * length
        } else {
            length
        })
    }

    /// _asset_image: the theme's picture `rel` fitted to w x h (0: as it is), kept (one that is
    /// not there too); None where it cannot be read (warned); one Rust does not read: UNREAD.
    fn asset_image(
        &self,
        rel: &str,
        w: i64,
        h: i64,
        fit: &str,
        align: &str,
    ) -> Found<Option<Rc<Image>>> {
        let key: PictureKey = (rel.into(), w, h, fit.into(), align.into());
        if let Some(found) = self.pictures.borrow_mut().get(&key) {
            return Ok(found);
        }
        let mut image = self.read_picture(rel).map_err(Drawn::from)?;
        if let Some(read) = &image {
            if w > 0 && h > 0 {
                let (fw, fh) = sized(w, h).map_err(Drawn::Failed)?;
                image = Some(fitted(read, fw, fh, fit, align).map_err(Drawn::Failed)?);
            }
        }
        let size = image
            .as_ref()
            .map_or(4096, |i| (i.width() * i.height() * 4).max(4096));
        let image = image.map(Rc::new);
        self.pictures.borrow_mut().put(key, image.clone(), size);
        Ok(image)
    }

    /// _animation: the frames of an animated picture (GIF, WebP, PNG) as the widget shows
    /// them, kept; None for a still picture or one that cannot be read (drawn as a still).
    fn animation_of(&mut self, w: &Dict, rel: &str) -> Found<Option<Rc<Animation>>> {
        let Some(root) = self.theme.root.clone() else {
            return Ok(None);
        };
        if !ends_with_any(rel, &MOVING_SUFFIXES) {
            return Ok(None);
        }
        let (iw, ih) = (widget::int(w, "w", 0), widget::int(w, "h", 0));
        let (fit, align) = (
            widget::text(w, "fit").to_string(),
            widget::text(w, "align").to_string(),
        );
        let (blur, radius) = (widget::int(w, "blur", 0), widget::int(w, "radius", 0));
        let key: AnimationKey = (rel.into(), iw, ih, fit.clone(), align.clone(), blur, radius);
        if let Some(found) = self.animations.get(&key) {
            return Ok(found);
        }
        // (_finish: blurred into the box, else fitted to it; rounded)
        let finish = |image: &Image| -> Result<Image> {
            let mut image = if blur > 0 {
                blurred(image, blur as f64, iw, ih, &fit, &align)?
            } else if iw > 0 && ih > 0 {
                let (fw, fh) = sized(iw, ih)?;
                fitted(image, fw, fh, &fit, &align)?
            } else {
                image.clone()
            };
            if radius > 0 {
                image = rounded(&image, radius)?;
            }
            Ok(image)
        };
        // (a name with a NUL: resolve_asset's ValueError, passed over: read as a still)
        let data = (!rel.contains('\0'))
            .then_some(&root)
            .and_then(|root| lp_theme::model::resolve_asset(root, &Value::from(rel)).ok())
            .and_then(|path| std::fs::read(path).ok());
        let found = match data.map(|data| read_animation(&data, &finish)) {
            Some(Read::Frames(animation)) => Some(Rc::new(animation)),
            Some(Read::Failed(e)) => return Err(Drawn::Failed(e)),
            Some(Read::Raised(e)) if passed_over(&e) => None,
            Some(Read::Raised(e)) if e.raised == lp_decode::Raised::Unported => {
                return Err(Drawn::Unported(UNREAD.into()));
            }
            Some(Read::Raised(e)) => return Err(Drawn::Failed(Failed(e.msg))),
            Some(Read::Still) | None => None,
        };
        let size = found.as_ref().map_or(4096, |a| a.size());
        self.animations.put(key, found.clone(), size);
        Ok(found)
    }

    /// _draw_image: the picture on show (the widget's, or its slideshow's turn): a live one, a
    /// video (Rust says it does not draw it yet), an animated picture's frame of the moment
    /// (from when it first showed, at its speed), else the theme's picture fitted, blurred and
    /// rounded (a clear piece of the widget's size where there is none).
    pub(crate) fn image_widget(&mut self, w: &Dict) -> Drawn {
        let pictures = match self.slides(w) {
            Ok(pictures) => pictures,
            Err(drawn) => return drawn,
        };
        let rel = if pictures.is_empty() {
            String::new()
        } else {
            match self.slide(w, &pictures) {
                Ok(rel) => rel,
                Err(drawn) => return drawn,
            }
        };
        let made = if let Some(name) = rel.strip_prefix('@') {
            let Some(content) = self.live_picture(name).map(|p| picture_key(name, p)) else {
                return Drawn::Nothing;
            };
            self.cached(w, content, |r| match r.live_picture(name) {
                Some(p) => r.live_image_piece(w, p),
                None => Err(Failed("a picture gone".into())),
            })
        } else if is_video(&rel) {
            return self.draw_video(w, &rel);
        } else {
            let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
            match self.animation_of(w, &rel) {
                Ok(Some(animation)) => {
                    // (the frame of the moment, from its first; a moving frame moves on)
                    let id = widget::text(w, "id").to_string();
                    let at = self.at;
                    let began = *self.began.entry((id, rel.clone())).or_insert(at);
                    let speed = match speed_of(w.get("speed").unwrap_or(&Value::int(1))) {
                        Ok(speed) => speed,
                        Err(e) => return Drawn::Failed(e),
                    };
                    let n = animation.index((at - began) * speed);
                    if self.options.animate {
                        self.moving = true;
                    }
                    let content = Value::List(vec![
                        rel.as_str().into(),
                        Value::int(Rc::as_ptr(&animation) as i64),
                        Value::int(n as i64),
                    ]);
                    self.cached(w, content, |_| {
                        Ok(Piece::new(animation.frames[n].clone(), x, y))
                    })
                }
                Ok(None) => match self.still_image(w, &rel, x, y) {
                    Ok(piece) => Ok(piece),
                    Err(drawn) => return drawn,
                },
                Err(drawn) => return drawn,
            }
        };
        match made {
            Ok(piece) => Drawn::Piece(piece),
            Err(e) => Drawn::Failed(e),
        }
    }

    /// _video_info: what ffmpeg says of a video, kept by its name; Python's error where its
    /// reading raises (a turn too large).
    fn video_info(
        &mut self,
        rel: &str,
        ffmpeg: &std::path::Path,
        path: &std::path::Path,
    ) -> Found<crate::video::VideoInfo> {
        if let Some(&found) = self.video_infos.get(rel) {
            return Ok(found);
        }
        let found = crate::video::video_info(ffmpeg, path).map_err(|e| Drawn::Failed(Failed(e)))?;
        self.video_infos.insert(rel.to_string(), found);
        Ok(found)
    }

    /// _draw_video: a video, round and round: its frame of the moment (video::VideoFeed); in a
    /// preview or until the first one comes, its first frame. Without a size of its own (0, as
    /// a picture added in Design starts) at the video's, within the theme's.
    fn draw_video(&mut self, w: &Dict, rel: &str) -> Drawn {
        let Some(root) = self.theme.root.clone() else {
            return Drawn::Nothing;
        };
        let Some(ffmpeg) = self.ffmpeg.clone() else {
            return Drawn::Unported(NO_FFMPEG.into());
        };
        if rel.contains('\0') {
            // (resolve_asset's ValueError, which _draw_video does not catch)
            return Drawn::Failed(Failed("embedded null byte".into()));
        }
        let path = match lp_theme::model::resolve_asset(&root, &Value::from(rel)) {
            Ok(path) => path,
            Err(e) => {
                self.warn(format!("video {}: {}", lp_py::repr(rel), e.msg));
                return Drawn::Nothing;
            }
        };
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (mut iw, mut ih) = (widget::int(w, "w", 0), widget::int(w, "h", 0));
        let mut w = w.clone();
        if iw <= 0 || ih <= 0 {
            let own = match self.video_info(rel, &ffmpeg, &path) {
                Ok(info) => info.size,
                Err(drawn) => return drawn,
            };
            let Some((ow, oh)) = own else {
                self.warn(format!("video {}: ffmpeg cannot read it", lp_py::repr(rel)));
                return Drawn::Nothing;
            };
            let (tw, th) = (self.theme.width as f64, self.theme.height as f64);
            let room = 1.0f64.min(tw / ow as f64).min(th / oh as f64);
            iw = 1.max(lp_py::round(ow as f64 * room) as i64);
            ih = 1.max(lp_py::round(oh as f64 * room) as i64);
            w.insert("w".into(), Value::int(iw));
            w.insert("h".into(), Value::int(ih));
        }
        let (vw, vh) = match sized(iw, ih) {
            Ok(size) => size,
            Err(e) => return Drawn::Failed(e),
        };
        let fit = widget::text(&w, "fit").to_string();
        let speed = match speed_of(w.get("speed").unwrap_or(&Value::int(1))) {
            Ok(speed) => speed,
            Err(e) => return Drawn::Failed(e),
        };
        let id = widget::text(&w, "id").to_string();
        self.video_drawn = Some((id, path.clone(), w.clone(), [x, y, iw, ih]));
        let mut rate = self
            .video_fps
            .filter(|&f| f != 0)
            .unwrap_or(crate::video::VIDEO_FPS);
        if !self.options.preview {
            let own = match self.video_info(rel, &ffmpeg, &path) {
                Ok(info) => info.fps,
                Err(drawn) => return drawn,
            };
            if let Some(own) = own {
                // no faster than the video's own: no frame twice (29.97 is 30)
                if !own.is_finite() {
                    return Drawn::Failed(Failed(
                        "cannot convert float infinity to integer".into(),
                    ));
                }
                let most = 1.0f64.max((own - 0.05).ceil());
                if most < f64::from(rate) {
                    rate = most as u32;
                }
            }
        }
        let keys = self.video_keys;
        let key: VideoKey = (
            rel.to_string(),
            vw,
            vh,
            fit.clone(),
            speed.to_bits(),
            rate,
            keys,
        );
        let mut shown: Option<(Arc<Image>, i64)> = None;
        if !self.options.preview {
            let feed = self.feeds.entry(key.clone()).or_insert_with(|| {
                let reading = crate::video::Reading {
                    w: vw,
                    h: vh,
                    fit: fit.clone(),
                    fps: rate,
                    once: false,
                    speed,
                    start: 0.0,
                    rounds: true,
                    keys,
                };
                crate::video::VideoFeed::new(&ffmpeg, &path, reading)
            });
            let (count, frame) = feed.frame(Some(self.at));
            shown = frame.map(|f| (f.picture, count));
            if self.options.animate {
                self.moving = true;
            }
        }
        if shown.is_none() {
            // (a preview, or the video has not begun yet: its first frame, kept)
            let pkey: PictureKey = (rel.to_string(), iw, ih, fit.clone(), "first".into());
            let kept = self.pictures.borrow_mut().get(&pkey);
            let poster = match kept {
                Some(poster) => poster,
                None => {
                    let poster =
                        crate::video::first_frame(&ffmpeg, &path, vw, vh, &fit, 0.0).map(Rc::new);
                    self.pictures
                        .borrow_mut()
                        .put(pkey, poster.clone(), vw * vh * 4);
                    poster
                }
            };
            shown = poster.map(|p| (Arc::new((*p).clone()), -1));
        }
        let Some((frame, count)) = shown else {
            return Drawn::Nothing;
        };
        // (ffmpeg fitted it already: as it is, unless blurred or rounded)
        let (blur, radius) = (widget::int(&w, "blur", 0), widget::int(&w, "radius", 0));
        let same = frame.size() == (vw, vh) && blur == 0 && radius == 0;
        let content = Value::List(vec![
            rel.into(),
            Value::int(vw as i64),
            Value::int(vh as i64),
            fit.as_str().into(),
            Value::Float(speed),
            Value::int(i64::from(rate)),
            Value::Bool(keys),
            Value::int(count),
            Value::int(Arc::as_ptr(&frame) as i64),
        ]);
        let align = widget::text(&w, "align").to_string();
        let made = self.cached(&w, content, |_| {
            if same {
                return Ok(Piece::new((*frame).clone(), x, y));
            }
            // (_finish of the widget fitted "stretch": blurred into the box, else stretched to
            // it; rounded)
            let mut image = if blur > 0 {
                blurred(&frame, blur as f64, iw, ih, "stretch", &align)?
            } else {
                fitted(&frame, vw, vh, "stretch", &align)?
            };
            if radius > 0 {
                image = rounded(&image, radius)?;
            }
            Ok(Piece::new(image, x, y))
        });
        match made {
            Ok(piece) => Drawn::Piece(piece),
            Err(e) => Drawn::Failed(e),
        }
    }

    /// _draw_image's still picture: the theme's picture fitted (_asset_image), blurred and
    /// rounded; none: a clear piece of the widget's size (a pixel at least). The picture is
    /// read (kept) before its piece is asked for: one Rust does not read leaves the frame to
    /// Python.
    fn still_image(&mut self, w: &Dict, rel: &str, x: i64, y: i64) -> Found<Rc<Piece>> {
        let (iw, ih) = (widget::int(w, "w", 0), widget::int(w, "h", 0));
        let (fit, align) = (
            widget::text(w, "fit").to_string(),
            widget::text(w, "align").to_string(),
        );
        let found = self.asset_image(rel, iw, ih, &fit, &align)?;
        self.cached(w, Value::Str(rel.to_string()), |_| {
            let Some(image) = found.clone() else {
                let (cw, ch) = sized(iw.max(1), ih.max(1))?;
                return Ok(Piece::new(Image::blank(Mode::RGBA, cw, ch), x, y));
            };
            let mut image = (*image).clone();
            let blur = widget::int(w, "blur", 0);
            if blur > 0 {
                image = blurred(&image, blur as f64, 0, 0, "stretch", "center")?;
            }
            let radius = widget::int(w, "radius", 0);
            if radius > 0 {
                image = rounded(&image, radius)?;
            }
            Ok(Piece::new(image, x, y))
        })
        .map_err(Drawn::Failed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_live_picture_is_kept_by_its_name_mode_size_and_pixels() {
        let picture = |mode, w, h, data: &[u8]| Image::from_bytes(mode, w, h, data.to_vec());
        let one = picture(Mode::RGBA, 1, 1, &[1, 2, 3, 4]).unwrap();
        let key = picture_key("media.cover", &one);
        assert_eq!(key, picture_key("media.cover", &one.clone()));
        let others = [
            ("game.logo", picture(Mode::RGBA, 1, 1, &[1, 2, 3, 4])),
            ("media.cover", picture(Mode::RGBa, 1, 1, &[1, 2, 3, 4])),
            ("media.cover", picture(Mode::RGBA, 1, 1, &[1, 2, 3, 5])),
            ("media.cover", picture(Mode::L, 2, 2, &[1, 2, 3, 4])),
            ("media.cover", picture(Mode::L, 4, 1, &[1, 2, 3, 4])),
            ("media.cover", picture(Mode::L, 1, 4, &[1, 2, 3, 4])),
        ];
        for (name, other) in others {
            assert_ne!(key, picture_key(name, &other.unwrap()));
        }
    }
}
