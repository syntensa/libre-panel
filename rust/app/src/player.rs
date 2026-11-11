//! The player (the app's *Player*, player.py): videos, animated pictures and pictures of a
//! folder of its own, played on panels instead of their themes, one after the other.
//!
//! - **The library** is a folder beside config.toml (`player`): what lies there is what can be
//!   played. The app copies a file into it, takes one out and opens the folder; what is put
//!   there by hand plays as well. What each one is, how long a round of it plays and its size,
//!   ffmpeg and the decoders tell once for each file as it is.
//! - **A playback**: the files in their order, the one playing, since when or where it stands
//!   paused, on which panels, how it fills them (`cover`, `contain`), how long a picture shows
//!   and what comes after the last (`all`, `one`, `off`). It lives in the app's process; every
//!   panel's loop asks it on every frame (`now`). Its times are on the wall clock. Nothing of
//!   it is written: it ends with the app.
//! - **On a panel** (`Screen`, one for each panel's loop): a video through ffmpeg, each frame at
//!   its moment of the playback, an animated picture frame by frame, a picture fitted; on
//!   black. Notices, the alarm and the rest still go over it; paused, the frame stays.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

use lp_pixels::{Filter, Image, Mode};
use lp_py::{Dict, Value};

pub const FOLDER: &str = "player";
pub const FITS: [&str; 2] = ["cover", "contain"];
pub const LOOPS: [&str; 3] = ["all", "one", "off"];
/// a picture's time, and an animated picture's at least (in whole rounds)
pub const PICTURE_S: f64 = 10.0;
const PICTURE_RANGE: (f64, f64) = (1.0, 3600.0);
/// further into an item than this, "previous" starts it again
const BACK_S: f64 = 3.0;
const VIDEOS: [&str; 5] = [".mp4", ".webm", ".mov", ".m4v", ".mkv"];
const STILLS: [&str; 6] = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"];
const LEAST_FRAME_S: f64 = 0.02;
const USUAL_FRAME_S: f64 = 0.1;
/// pictures of the library kept at most
const THUMBS: usize = 300;

/// Something the player cannot do, said for the person.
pub struct PlayerError(pub String);

fn error<T>(said: impl Into<String>) -> Result<T, PlayerError> {
    Err(PlayerError(said.into()))
}

/// The wall clock (a panel's loop and the app read the same).
fn clock() -> f64 {
    crate::share::now()
}

fn say(message: &str) {
    crate::logs::log(crate::logs::Level::Info, "libre_panel.player", message);
}

/// folder: the library: the folder beside config.toml whose files can be played.
pub fn folder() -> PathBuf {
    lp_config::dirs::config_dir().join(FOLDER)
}

/// The suffix of a name in lower case, with its dot ("" for none): Path.suffix.lower().
fn suffix(name: &str) -> String {
    match name.rfind('.') {
        Some(at) if at > 0 && at + 1 < name.len() => lp_py::lower(&name[at..]),
        _ => String::new(),
    }
}

fn is_video(name: &str) -> bool {
    let lower = lp_py::lower(name);
    VIDEOS.iter().any(|end| lower.ends_with(end))
}

/// valid_name: whether `name` is one of a file the library may hold: a name alone (no
/// folder), not hidden, of a video or a picture.
pub fn valid_name(name: &str) -> bool {
    let count = name.chars().count();
    let allowed = name
        .chars()
        .all(|c| !matches!(c, '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|') && c >= ' ');
    let ending = suffix(name);
    (1..=120).contains(&count)
        && allowed
        && name == lp_py::strip(name)
        && !name.starts_with('.')
        && !name.ends_with('.')
        && (VIDEOS.contains(&ending.as_str()) || STILLS.contains(&ending.as_str()))
}

// -- the library ----------------------------------------------------------------------------

/// A file of the library as ffmpeg or the decoders read it.
#[derive(Clone, Debug)]
pub struct Info {
    pub name: String,
    /// "video", "animation", "picture"
    pub kind: &'static str,
    pub bytes: u64,
    /// a round of it in seconds (0: a picture)
    pub length: f64,
    pub size: Option<(i64, i64)>,
    pub fps: Option<f64>,
}

impl Info {
    fn value(&self) -> Value {
        let mut told = Dict::new();
        told.insert("name".into(), Value::Str(self.name.clone()));
        told.insert("kind".into(), Value::from(self.kind));
        told.insert("bytes".into(), Value::int(self.bytes as i64));
        told.insert("length".into(), Value::Float(self.length));
        told.insert(
            "size".into(),
            self.size.map_or(Value::None, |(w, h)| {
                Value::List(vec![Value::int(w), Value::int(h)])
            }),
        );
        told.insert("fps".into(), self.fps.map_or(Value::None, Value::Float));
        Value::Dict(told)
    }
}

type Stamp = (i128, u64);
type Infos = HashMap<PathBuf, (Stamp, Option<Info>)>;
/// a small picture by its file, the file's stamp and its width
type Thumbnail = ((PathBuf, i128, u64, usize), Vec<u8>);
static INFOS: Mutex<Option<Infos>> = Mutex::new(None);
static THUMBNAILS: Mutex<Vec<Thumbnail>> = Mutex::new(Vec::new());

fn stamp_of(path: &Path) -> Option<Stamp> {
    let meta = std::fs::metadata(path).ok()?;
    let modified = crate::arrangement::stamp_of(path)?;
    Some((modified, meta.len()))
}

/// ffmpeg, as the videos of themes find it; None where there is none.
fn ffmpeg() -> Option<PathBuf> {
    lp_devices::h264::find_ffmpeg("").ok()
}

/// has_ffmpeg: whether videos can play here.
pub fn has_ffmpeg() -> bool {
    ffmpeg().is_some()
}

/// info: what the file at `path` is; None for one that is no video or picture that can be
/// read. Read once for each file as it is (its time and size).
pub fn info(path: &Path) -> Option<Info> {
    let stamp = stamp_of(path)?;
    if let Ok(infos) = INFOS.lock() {
        if let Some((kept, found)) = infos.as_ref().and_then(|infos| infos.get(path)) {
            if *kept == stamp {
                return found.clone();
            }
        }
    }
    let found = read_info(path, stamp.1);
    if let Ok(mut infos) = INFOS.lock() {
        infos
            .get_or_insert_with(HashMap::new)
            .insert(path.to_path_buf(), (stamp, found.clone()));
    }
    found
}

fn read_info(path: &Path, bytes: u64) -> Option<Info> {
    let name = path.file_name()?.to_string_lossy().into_owned();
    if is_video(&name) {
        let Some(ffmpeg) = ffmpeg() else {
            // (it plays once ffmpeg is there)
            return Some(Info {
                name,
                kind: "video",
                bytes,
                length: 0.0,
                size: None,
                fps: None,
            });
        };
        let told = lp_render::video::video_info(&ffmpeg, path).ok()?;
        told.size?; // (ffmpeg cannot read it)
        return Some(Info {
            name,
            kind: "video",
            bytes,
            length: told.length.unwrap_or(0.0),
            size: told.size,
            fps: told.fps,
        });
    }
    let data = std::fs::read(path).ok()?;
    let picture = match lp_decode::decode(&data) {
        Ok(picture) => picture,
        Err(why) => {
            say(&format!(
                "player: {name} is no picture that can be read ({})",
                why.msg
            ));
            return None;
        }
    };
    let shape = (picture.width as i64, picture.height as i64);
    let count = lp_decode::n_frames(&data).unwrap_or(1);
    let mut length = 0.0;
    if count > 1 {
        let _ = lp_decode::each_frame(&data, &mut |_, frame| {
            let seconds = frame.duration.unwrap_or(0.0) / 1000.0;
            length += if seconds >= LEAST_FRAME_S {
                seconds
            } else {
                USUAL_FRAME_S
            };
            Ok(())
        });
    }
    Some(Info {
        name,
        kind: if count > 1 { "animation" } else { "picture" },
        bytes,
        length,
        size: Some(shape),
        fps: None,
    })
}

/// library: what can be played: the files of the folder that are videos or pictures, by name.
pub fn library(place: &Path) -> Vec<Info> {
    let Ok(entries) = std::fs::read_dir(place) else {
        return Vec::new();
    };
    let mut paths: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.is_file()
                && path
                    .file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(valid_name)
        })
        .collect();
    paths.sort_by_key(|path| {
        lp_py::casefold(&path.file_name().unwrap_or_default().to_string_lossy())
    });
    paths.iter().filter_map(|path| info(path)).collect()
}

/// fitted: `image` across `size` (cover) or whole within it (contain), on black, RGB.
fn fitted(image: &Image, size: (usize, usize), fit: &str) -> Result<Image, String> {
    let image = image.convert(Mode::RGBA).map_err(|e| e.to_string())?;
    let image = if fit == "cover" {
        image.fit(size, Filter::Lanczos, 0.0, (0.5, 0.5))
    } else {
        image.contain(size, Filter::Lanczos)
    }
    .map_err(|e| e.to_string())?;
    let mut out = Image::new(Mode::RGB, size.0, size.1, &[0, 0, 0]);
    let x = (size.0 as i64 - image.width() as i64).div_euclid(2);
    let y = (size.1 as i64 - image.height() as i64).div_euclid(2);
    out.paste(&image, x, y, Some(&image))
        .map_err(|e| e.to_string())?;
    Ok(out)
}

/// thumbnail: a JPEG of the file `name` of the library, `width` wide: a video's picture a
/// little way in (its first is often black), an animated picture's first; None for none.
pub fn thumbnail(name: &str, width: i64, place: &Path) -> Option<Vec<u8>> {
    if !valid_name(name) {
        return None;
    }
    let path = place.join(name);
    let found = info(&path)?;
    let width = width.clamp(16, 1280) as usize;
    let (modified, bytes) = stamp_of(&path)?;
    let key = (path.clone(), modified, bytes, width);
    if let Ok(thumbs) = THUMBNAILS.lock() {
        if let Some((_, data)) = thumbs.iter().find(|(k, _)| *k == key) {
            return Some(data.clone());
        }
    }
    let picture = if found.kind == "video" {
        let ffmpeg = ffmpeg()?;
        let (w, h) = found.size?;
        let height =
            ((width as f64 * h as f64 / w.max(1) as f64).round_ties_even() as usize).max(2);
        let at = if found.length > 0.0 {
            (found.length * 0.1).min(1.0)
        } else {
            0.0
        };
        lp_render::video::first_frame(&ffmpeg, &path, width, height, "contain", at)?
    } else {
        let data = std::fs::read(&path).ok()?;
        let image = lp_decode::open(&data, Mode::RGBA).ok()?;
        image
            .thumbnail((width as f64, (width * 4) as f64), Filter::Lanczos, None)
            .ok()?
    };
    let mut flat = Image::new(Mode::RGB, picture.width(), picture.height(), &[0, 0, 0]);
    let mask = (picture.mode() == Mode::RGBA).then_some(&picture);
    flat.paste(&picture, 0, 0, mask).ok()?;
    let data = lp_decode::save_jpeg(&flat, 82).ok()?;
    if let Ok(mut thumbs) = THUMBNAILS.lock() {
        if thumbs.len() >= THUMBS {
            thumbs.remove(0);
        }
        thumbs.push((key, data.clone()));
    }
    Some(data)
}

/// remove: the file `name` out of the library (it plays no more).
pub fn remove(name: &Value, place: &Path) -> Result<(), PlayerError> {
    let Some(name) = name.as_str().filter(|name| valid_name(name)) else {
        return error("not a file of the library");
    };
    match std::fs::remove_file(place.join(name)) {
        Ok(()) => Ok(()),
        Err(why) if why.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(why) => error(format!("{name} could not be removed: {why}")),
    }
}

// -- the playback ---------------------------------------------------------------------------

/// A file in a playback and how long it plays.
#[derive(Clone, Debug, PartialEq)]
pub struct Item {
    pub name: String,
    pub kind: &'static str,
    pub seconds: f64,
    pub fps: Option<f64>,
}

fn item_of(found: &Info, picture_s: f64) -> Item {
    let item = |seconds, fps| Item {
        name: found.name.clone(),
        kind: found.kind,
        seconds,
        fps,
    };
    if found.kind == "video" {
        return item(found.length, found.fps);
    }
    if found.kind == "animation" && found.length > 0.0 {
        let rounds = ((picture_s / found.length - 1e-9).ceil()).max(1.0);
        return item(rounds * found.length, None);
    }
    item(picture_s, None)
}

#[derive(Clone, Debug)]
struct Playback {
    items: Vec<Item>,
    panels: Vec<i64>,
    index: usize,
    began: f64,
    paused: Option<f64>,
    fit: String,
    looping: String,
    shuffle: bool,
    picture_s: f64,
    jump: u64,
    place: PathBuf,
}

impl Playback {
    fn item(&self) -> &Item {
        &self.items[self.index]
    }

    fn position(&self, at: f64) -> f64 {
        self.paused.unwrap_or_else(|| (at - self.began).max(0.0))
    }
}

/// What a panel plays at a moment.
#[derive(Clone, Debug)]
pub struct Showing {
    pub path: PathBuf,
    pub kind: &'static str,
    pub fit: String,
    pub position: f64,
    pub jump: u64,
    pub fps: Option<f64>,
}

static OWN: Mutex<Option<Playback>> = Mutex::new(None);
static JUMPS: AtomicU64 = AtomicU64::new(1);

fn next_jump() -> u64 {
    JUMPS.fetch_add(1, Ordering::Relaxed)
}

fn shuffled(items: &[Item]) -> Vec<Item> {
    let mut mixed = items.to_vec();
    // (random.shuffle: Fisher-Yates over the system's randomness)
    for i in (1..mixed.len()).rev() {
        let mut bytes = [0u8; 8];
        let _ = getrandom::getrandom(&mut bytes);
        let j = (u64::from_le_bytes(bytes) % (i as u64 + 1)) as usize;
        mixed.swap(i, j);
    }
    mixed
}

/// _advanced: the playback at `at`: the items that ended since moved on from, as its loop
/// says; None where the last ended and nothing comes after it.
fn advanced(playback: Option<Playback>, at: f64) -> Option<Playback> {
    let mut playback = playback?;
    for _ in 0..4 * playback.items.len() {
        let length = playback.item().seconds;
        if playback.paused.is_some() || length <= 0.0 || at - playback.began < length - 1e-6 {
            return Some(playback);
        }
        let began = playback.began + length; // (the next begins where it ended)
        if playback.looping == "one" {
            playback.began = began;
            playback.jump = next_jump();
        } else if playback.index + 1 < playback.items.len() {
            playback.index += 1;
            playback.began = began;
            playback.jump = next_jump();
        } else if playback.looping == "all" {
            if playback.shuffle {
                playback.items = shuffled(&playback.items);
            }
            playback.index = 0;
            playback.began = began;
            playback.jump = next_jump();
        } else {
            return None;
        }
    }
    if playback.paused.is_none() {
        // (away for long: on from now)
        playback.began = at - (at - playback.began).min(playback.item().seconds);
    }
    Some(playback)
}

fn moved(mut playback: Playback, step: i64, at: f64) -> Playback {
    let count = playback.items.len() as i64;
    playback.index = (playback.index as i64 + step).rem_euclid(count) as usize;
    playback.paused = playback.paused.map(|_| 0.0);
    playback.began = at;
    playback.jump = next_jump();
    playback
}

fn sought(mut playback: Playback, seconds: f64, at: f64) -> Playback {
    let length = playback.item().seconds;
    let seconds = if length > 0.0 {
        seconds.min(length - 0.05)
    } else {
        seconds
    }
    .max(0.0);
    playback.paused = playback.paused.map(|_| seconds);
    playback.began = at - seconds;
    playback.jump = next_jump();
    playback
}

fn lock() -> std::sync::MutexGuard<'static, Option<Playback>> {
    OWN.lock().unwrap_or_else(|e| e.into_inner())
}

fn change(how: impl FnOnce(Playback, f64) -> Playback) {
    let at = clock();
    let mut own = lock();
    *own = advanced(own.take(), at).map(|playback| how(playback, at));
}

fn picture_seconds(value: &Value) -> Result<f64, PlayerError> {
    let seconds = match value {
        Value::Int(_) | Value::Float(_) | Value::Bool(_) => value.to_f64(),
        Value::Str(text) => lp_py::strip(text).parse::<f64>().ok(),
        _ => None,
    };
    match seconds {
        Some(seconds) if !seconds.is_nan() => Ok(seconds.clamp(PICTURE_RANGE.0, PICTURE_RANGE.1)),
        _ => error("picture_s: seconds"),
    }
}

/// How a playback is asked for.
pub struct Asked<'a> {
    pub names: &'a [String],
    pub panels: &'a [i64],
    pub start: Option<&'a str>,
    pub fit: &'a Value,
    pub looping: &'a Value,
    pub shuffle: bool,
    pub picture_s: &'a Value,
}

/// play: the files `names` of the library in their order on `panels`, from `start` (the
/// first where it is none of them), instead of their themes; with `shuffle` in another order,
/// `start` first.
pub fn play(asked: &Asked, place: &Path) -> Result<(), PlayerError> {
    let fit = asked.fit.as_str().filter(|fit| FITS.contains(fit));
    let Some(fit) = fit else {
        return error(format!("fit: one of {}", FITS.join(", ")));
    };
    let looping = asked
        .looping
        .as_str()
        .filter(|looping| LOOPS.contains(looping));
    let Some(looping) = looping else {
        return error(format!("loop: one of {}", LOOPS.join(", ")));
    };
    let mut panels: Vec<i64> = asked.panels.to_vec();
    panels.sort_unstable();
    panels.dedup();
    if panels.is_empty() || panels.iter().any(|n| *n < 1) {
        return error("panels: the numbers of the panels to play on, 1 the first");
    }
    let picture_s = picture_seconds(asked.picture_s)?;
    let items: Vec<Item> = asked
        .names
        .iter()
        .filter(|name| valid_name(name))
        .filter_map(|name| info(&place.join(name)))
        .map(|found| item_of(&found, picture_s))
        .collect();
    if items.is_empty() {
        return error("nothing to play: no video or picture of the library");
    }
    let first = items
        .iter()
        .position(|item| Some(item.name.as_str()) == asked.start)
        .unwrap_or(0);
    let (items, index) = if asked.shuffle {
        let lead = items[first].clone();
        let rest: Vec<Item> = items
            .iter()
            .enumerate()
            .filter(|(i, _)| *i != first)
            .map(|(_, item)| item.clone())
            .collect();
        let mut all = vec![lead];
        all.extend(shuffled(&rest));
        (all, 0)
    } else {
        (items, first)
    };
    crate::mirror::stop(); // (one or the other on a panel: the Player, or a monitor)
    let shown: Vec<String> = panels.iter().map(i64::to_string).collect();
    say(&format!(
        "player: {} to play on panel {}, from {}",
        items.len(),
        shown.join(", "),
        items[index].name
    ));
    *lock() = Some(Playback {
        items,
        panels,
        index,
        began: clock(),
        paused: None,
        fit: fit.to_string(),
        looping: looping.to_string(),
        shuffle: asked.shuffle,
        picture_s,
        jump: next_jump(),
        place: place.to_path_buf(),
    });
    Ok(())
}

pub fn pause() {
    change(|mut p, at| {
        if p.paused.is_none() {
            p.paused = Some(p.position(at));
        }
        p
    });
}

pub fn resume() {
    change(|mut p, at| {
        if let Some(paused) = p.paused.take() {
            p.began = at - paused;
        }
        p
    });
}

/// toggle: pause, or play on where it stands paused.
pub fn toggle() {
    let paused = lock().as_ref().is_some_and(|p| p.paused.is_some());
    if paused {
        resume();
    } else {
        pause();
    }
}

pub fn next_item() {
    change(|p, at| moved(p, 1, at));
}

/// previous_item: the item before; one that has played a few seconds from its start.
pub fn previous_item() {
    change(|p, at| {
        if p.position(at) > BACK_S {
            sought(p, 0.0, at)
        } else {
            moved(p, -1, at)
        }
    });
}

/// seek: to `seconds` into the item on show.
pub fn seek(seconds: f64) {
    change(|p, at| sought(p, seconds, at));
}

/// configure: how the playback goes on: how it fills the panels, what comes after the last,
/// in another order, how long a picture shows (from the next one on).
pub fn configure(
    fit: &Value,
    looping: &Value,
    shuffle: &Value,
    picture_s: &Value,
) -> Result<(), PlayerError> {
    let fit = match fit {
        Value::None => None,
        Value::Str(fit) if FITS.contains(&fit.as_str()) => Some(fit.clone()),
        _ => return error(format!("fit: one of {}", FITS.join(", "))),
    };
    let looping = match looping {
        Value::None => None,
        Value::Str(looping) if LOOPS.contains(&looping.as_str()) => Some(looping.clone()),
        _ => return error(format!("loop: one of {}", LOOPS.join(", "))),
    };
    let shuffle = (!matches!(shuffle, Value::None)).then(|| shuffle.truthy());
    let picture_s = match picture_s {
        Value::None => None,
        value => Some(picture_seconds(value)?),
    };
    let mut remade: HashMap<String, Item> = HashMap::new();
    if let Some(seconds) = picture_s {
        let current = lock().clone();
        for item in current.iter().flat_map(|p| p.items.iter()) {
            if item.kind == "video" {
                continue;
            }
            let place = current
                .as_ref()
                .map(|p| p.place.clone())
                .unwrap_or_default();
            if let Some(found) = info(&place.join(&item.name)) {
                remade.insert(item.name.clone(), item_of(&found, seconds));
            }
        }
    }
    change(|mut p, _| {
        if let Some(fit) = fit {
            p.fit = fit;
        }
        if let Some(looping) = looping {
            p.looping = looping;
        }
        if let Some(shuffle) = shuffle {
            p.shuffle = shuffle;
        }
        if let Some(seconds) = picture_s {
            p.picture_s = seconds;
        }
        p.items = p
            .items
            .iter()
            .map(|item| {
                remade
                    .get(&item.name)
                    .cloned()
                    .unwrap_or_else(|| item.clone())
            })
            .collect();
        p
    });
    Ok(())
}

/// stop: the themes again, on every panel the player played on.
pub fn stop() {
    if lock().take().is_some() {
        say("player: stopped");
    }
}

/// now: what panel `number` plays now; None: its theme.
pub fn now(number: i64) -> Option<Showing> {
    let at = clock();
    let mut own = lock();
    *own = advanced(own.take(), at);
    let playback = own.as_ref()?;
    if !playback.panels.contains(&number) {
        return None;
    }
    let item = playback.item();
    Some(Showing {
        path: playback.place.join(&item.name),
        kind: item.kind,
        fit: playback.fit.clone(),
        position: playback.position(at),
        jump: playback.jump,
        fps: item.fps,
    })
}

/// status: the playback for the app: its items, the one on show, where it stands; None.
pub fn status() -> Value {
    let at = clock();
    let mut own = lock();
    *own = advanced(own.take(), at);
    let Some(playback) = own.as_ref() else {
        return Value::None;
    };
    let item = playback.item();
    let items = playback
        .items
        .iter()
        .map(|item| {
            let mut told = Dict::new();
            told.insert("name".into(), Value::Str(item.name.clone()));
            told.insert("kind".into(), Value::from(item.kind));
            told.insert("seconds".into(), Value::Float(item.seconds));
            told.insert("fps".into(), item.fps.map_or(Value::None, Value::Float));
            Value::Dict(told)
        })
        .collect();
    let position = (playback.position(at) * 1000.0).round_ties_even() / 1000.0;
    let mut told = Dict::new();
    told.insert("items".into(), Value::List(items));
    told.insert("index".into(), Value::int(playback.index as i64));
    told.insert("name".into(), Value::Str(item.name.clone()));
    told.insert("kind".into(), Value::from(item.kind));
    told.insert("position".into(), Value::Float(position));
    told.insert("seconds".into(), Value::Float(item.seconds));
    told.insert("paused".into(), Value::Bool(playback.paused.is_some()));
    told.insert(
        "panels".into(),
        Value::List(playback.panels.iter().map(|n| Value::int(*n)).collect()),
    );
    told.insert("fit".into(), Value::Str(playback.fit.clone()));
    told.insert("loop".into(), Value::Str(playback.looping.clone()));
    told.insert("shuffle".into(), Value::Bool(playback.shuffle));
    told.insert("picture_s".into(), Value::Float(playback.picture_s));
    Value::Dict(told)
}

/// The library's files as the app shows them.
pub fn library_value(place: &Path) -> Value {
    Value::List(library(place).iter().map(Info::value).collect())
}

// -- on a panel -----------------------------------------------------------------------------

/// What one panel's loop draws while the player plays on it: the item of the moment at the
/// panel's size, on black.
/// what a screen shows: the file, its kind, the fit, the size, the rate and the jump
type ScreenKey = (PathBuf, &'static str, String, (usize, usize), u32, u64);

#[derive(Default)]
pub struct Screen {
    key: Option<ScreenKey>,
    feed: Option<lp_render::video::VideoFeed>,
    animation: Option<lp_render::Animation>,
    still: Option<Image>,
    /// shown until the next one's first frame
    last: Option<Image>,
}

impl Screen {
    /// frame: the panel's frame of `showing`, `size` large; `fps`: the panel's frames a
    /// second (a video is decoded at as many, no more than its own).
    pub fn frame(&mut self, showing: &Showing, size: (usize, usize), fps: f64) -> Image {
        let mut rate = (fps as i64).max(1) as u32;
        if showing.kind == "video" {
            if let Some(own) = showing.fps {
                rate = rate.min(((own - 0.05).ceil() as i64).max(1) as u32);
            }
        }
        // (a picture's time says where it is)
        let jump = if showing.kind == "video" {
            showing.jump
        } else {
            0
        };
        let key = (
            showing.path.clone(),
            showing.kind,
            showing.fit.clone(),
            size,
            rate,
            jump,
        );
        if self.key.as_ref() != Some(&key) {
            self.open(showing, size, rate);
            self.key = Some(key);
        }
        if let Some(feed) = &self.feed {
            let (_count, picture) = feed.frame(Some(showing.position));
            if let Some(picture) = picture {
                let mut frame = Image::new(Mode::RGB, size.0, size.1, &[0, 0, 0]);
                // (opaque, or black where contain left room)
                let _ = frame.paste(&picture.picture, 0, 0, None);
                self.last = Some(frame.clone());
                return frame;
            }
        } else if let Some(animation) = &self.animation {
            let frame = animation.frames[animation.index(showing.position)].clone();
            self.last = Some(frame.clone());
            return frame;
        } else if let Some(still) = &self.still {
            self.last = Some(still.clone());
            return still.clone();
        }
        match &self.last {
            Some(last) if last.size() == size => last.clone(),
            _ => Image::new(Mode::RGB, size.0, size.1, &[0, 0, 0]),
        }
    }

    fn open(&mut self, showing: &Showing, size: (usize, usize), rate: u32) {
        self.close_feed();
        self.animation = None;
        self.still = None;
        let name = showing
            .path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        if showing.kind == "video" {
            let Some(ffmpeg) = ffmpeg() else {
                crate::logs::log(
                    crate::logs::Level::Warning,
                    "libre_panel.player",
                    &format!("player: {name} needs ffmpeg"),
                );
                return;
            };
            let start = if showing.position > 0.05 {
                showing.position
            } else {
                0.0
            };
            let reading = lp_render::video::Reading {
                w: size.0,
                h: size.1,
                fit: showing.fit.clone(),
                fps: rate,
                once: false,
                speed: 1.0,
                start,
                rounds: false,
                keys: false,
            };
            let feed = lp_render::video::VideoFeed::new(&ffmpeg, &showing.path, reading);
            feed.begin_at(start);
            self.feed = Some(feed);
            return;
        }
        let data = match std::fs::read(&showing.path) {
            Ok(data) => data,
            Err(why) => {
                crate::logs::log(
                    crate::logs::Level::Warning,
                    "libre_panel.player",
                    &format!("player: {name} cannot be shown ({why})"),
                );
                return;
            }
        };
        let fit = showing.fit.clone();
        if showing.kind == "animation" {
            let finish =
                |image: &Image| fitted(image, size, &fit).map_err(lp_render::piece::Failed);
            if let lp_render::AnimationRead::Frames(animation) =
                lp_render::read_animation(&data, &finish)
            {
                self.animation = Some(animation);
                return;
            }
        }
        match lp_decode::open(&data, Mode::RGBA)
            .map_err(|e| e.msg)
            .and_then(|image| fitted(&image, size, &fit))
        {
            Ok(still) => self.still = Some(still),
            Err(why) => crate::logs::log(
                crate::logs::Level::Warning,
                "libre_panel.player",
                &format!("player: {name} cannot be shown ({why})"),
            ),
        }
    }

    fn close_feed(&mut self) {
        if let Some(feed) = self.feed.take() {
            feed.close();
        }
    }

    /// close: nothing plays here any more: ffmpeg stops, the pictures go.
    pub fn close(&mut self) {
        self.close_feed();
        self.key = None;
        self.animation = None;
        self.still = None;
        self.last = None;
    }
}
