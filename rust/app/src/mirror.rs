//! A panel as a monitor (mirror.py, the app's *Screen*): a monitor of this computer, a part of
//! one or a window on a panel instead of its theme, as it changes, until it is stopped.
//!
//! - **What is shown** ([`Source`]): a monitor (`monitor`, by its id: capture.rs), a part of
//!   one (`region`), a window (`window`), or the monitor of its own that the computer extends
//!   its desktop to for the panel (`virtual`: extend.rs makes it); fitted into the panel's
//!   frame whole (`contain`), to its edges (`cover`) or stretched; the mouse pointer with it or
//!   not.
//! - **Where** ([`show`], [`now`]): on which panels. It lives in the app's process, as the
//!   Player's playback does: every panel's loop asks it each frame. Nothing of it is written:
//!   it ends with the app.
//! - **On a panel** ([`Screen`]): ffmpeg captures and fits the pictures, the newest is drawn;
//!   notices and the alarm still go over them. (Python's app had a TURZX panel in video mode
//!   encode the screen straight into its stream; here every panel gets frames, which the
//!   engine encodes.)

use std::io::Read;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use lp_pixels::{Image, Mode};
use lp_py::{Dict, Value};

use crate::capture::{self, Capture};
use crate::extend;

pub const KINDS: [&str; 3] = ["monitor", "window", "virtual"];
pub const FITS: [&str; 3] = ["contain", "cover", "stretch"];
/// a capture nobody asked a frame of for this long: ffmpeg stops
const IDLE_S: f64 = 5.0;
/// after ffmpeg stopped by itself (a window closed, a monitor gone)
const RETRY_S: f64 = 3.0;

/// Something that cannot be shown, said for the person.
#[derive(Debug)]
pub struct MirrorError(pub String);

/// What a panel shows as a monitor.
#[derive(Clone, Debug, PartialEq)]
pub struct Source {
    pub kind: String,
    pub id: String,
    pub name: String,
    pub region: Option<(i64, i64, i64, i64)>,
    pub fit: String,
    pub cursor: bool,
}

impl Source {
    fn value(&self) -> Value {
        let mut told = Dict::default();
        told.insert("kind".into(), Value::Str(self.kind.clone()));
        told.insert("id".into(), Value::Str(self.id.clone()));
        told.insert("name".into(), Value::Str(self.name.clone()));
        told.insert(
            "region".into(),
            self.region.map_or(Value::None, |(x, y, w, h)| {
                Value::List(vec![
                    Value::int(x),
                    Value::int(y),
                    Value::int(w),
                    Value::int(h),
                ])
            }),
        );
        told.insert("fit".into(), Value::Str(self.fit.clone()));
        told.insert("cursor".into(), Value::Bool(self.cursor));
        Value::Dict(told)
    }
}

struct Mirroring {
    source: Source,
    panels: Vec<i64>,
    since: f64,
}

static OWN: Mutex<Option<Mirroring>> = Mutex::new(None);

fn own() -> std::sync::MutexGuard<'static, Option<Mirroring>> {
    OWN.lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn say(message: &str) {
    crate::logs::log(crate::logs::Level::Info, "libre_panel.mirror", message);
}

fn warn(message: &str) {
    crate::logs::log(crate::logs::Level::Warning, "libre_panel.mirror", message);
}

fn whole(value: &Value) -> Option<i64> {
    match value {
        Value::Int(n) => i64::try_from(n).ok(),
        _ => None,
    }
}

/// source_of: a Source from the app's JSON (MirrorError: what is wrong with it).
pub fn source_of(raw: Option<&Value>) -> Result<Source, MirrorError> {
    let Some(Value::Dict(raw)) = raw else {
        return Err(MirrorError("source: expected {kind, id, ...}".into()));
    };
    let kind = match raw.get("kind") {
        Some(Value::Str(kind)) if KINDS.contains(&kind.as_str()) => kind.clone(),
        _ => return Err(MirrorError(format!("kind: one of {}", KINDS.join(", ")))),
    };
    let id = match raw.get("id") {
        None => String::new(),
        Some(Value::Str(id)) => id.clone(),
        Some(_) => return Err(MirrorError("id: which monitor or window".into())),
    };
    if kind != "virtual" && id.is_empty() {
        return Err(MirrorError("id: which monitor or window".into()));
    }
    let region = match raw.get("region") {
        None | Some(Value::None) => None,
        Some(Value::List(parts)) if parts.len() == 4 => {
            let numbers: Option<Vec<i64>> = parts.iter().map(whole).collect();
            match numbers {
                Some(n) if n[2] >= 16 && n[3] >= 16 => Some((n[0], n[1], n[2], n[3])),
                _ => {
                    return Err(MirrorError(
                        "region: [x, y, width, height] in pixels".into(),
                    ))
                }
            }
        }
        Some(_) => {
            return Err(MirrorError(
                "region: [x, y, width, height] in pixels".into(),
            ))
        }
    };
    let fit = match raw.get("fit") {
        None => "contain".to_string(),
        Some(Value::Str(fit)) if FITS.contains(&fit.as_str()) => fit.clone(),
        Some(_) => return Err(MirrorError(format!("fit: one of {}", FITS.join(", ")))),
    };
    let name = match raw.get("name") {
        Some(Value::Str(name)) => name.clone(),
        _ => String::new(),
    };
    let cursor = raw.get("cursor") != Some(&Value::Bool(false));
    Ok(Source {
        kind,
        id,
        name,
        region,
        fit,
        cursor,
    })
}

fn wall() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64())
}

/// show: `source` on `panels` from now on, instead of their themes (and of the Player). The
/// panel's own monitor (`virtual`) goes on the desktop first, `size` large, and off again
/// with stop.
pub fn show(source: Source, panels: &[i64], size: Option<(i64, i64)>) -> Result<(), MirrorError> {
    let mut chosen: Vec<i64> = panels.iter().copied().filter(|n| *n >= 1).collect();
    chosen.sort_unstable();
    chosen.dedup();
    if chosen.is_empty() {
        return Err(MirrorError("panels: at least one".into()));
    }
    let place = capture::platform();
    let before_virtual = own()
        .as_ref()
        .is_some_and(|before| before.source.kind == "virtual");
    let own_monitor = source.kind == "virtual" && place != "wayland";
    if own_monitor {
        let size = size.ok_or_else(|| {
            MirrorError("the panel's size is not known: choose its model in Settings".into())
        })?;
        extend::attach(size, place).map_err(|extend::ExtendError(why)| MirrorError(why))?;
    } else if before_virtual {
        extend::detach(place);
    }
    crate::player::stop(); // (one or the other on a panel)
    let named = if source.name.is_empty() {
        source.id.clone()
    } else {
        source.name.clone()
    };
    let list: Vec<String> = chosen.iter().map(i64::to_string).collect();
    say(&format!(
        "monitor: {} {named} on panel {}",
        source.kind,
        list.join(", ")
    ));
    *own() = Some(Mirroring {
        source,
        panels: chosen,
        since: wall(),
    });
    Ok(())
}

/// stop: the themes again (the panel's own monitor off the desktop).
pub fn stop() {
    let stopped = own().take();
    if let Some(stopped) = stopped {
        if stopped.source.kind == "virtual" {
            extend::detach(capture::platform());
        }
        say("monitor: stopped");
    }
}

/// now: what panel `number` shows as a monitor now; None: its theme (or the Player).
pub fn now(number: i64) -> Option<Source> {
    let own = own();
    let mirroring = own.as_ref()?;
    mirroring
        .panels
        .contains(&number)
        .then(|| mirroring.source.clone())
}

/// status: what is shown, where and since when; None: nothing.
pub fn status() -> Value {
    let own = own();
    let Some(mirroring) = own.as_ref() else {
        return Value::None;
    };
    let mut told = Dict::default();
    told.insert("source".into(), mirroring.source.value());
    told.insert(
        "panels".into(),
        Value::List(mirroring.panels.iter().map(|n| Value::int(*n)).collect()),
    );
    told.insert("since".into(), Value::Float(mirroring.since));
    Value::Dict(told)
}

// -- capturing ------------------------------------------------------------------------------

/// capture_of: ffmpeg's input for `source` here, now (a monitor's place and a window's handle
/// are looked up as it starts: they change); `size`: the panel's frame (Wayland's capture
/// program fits the pictures to it).
pub fn capture_of(
    ffmpeg: &str,
    source: &Source,
    fps: i64,
    size: Option<(usize, usize)>,
) -> Result<Capture, MirrorError> {
    let place = capture::platform();
    if place == "wayland" {
        return crate::wayland::capture_of(source, fps, size);
    }
    let named = |what: &str| {
        if source.name.is_empty() {
            format!("{what} {}", source.id)
        } else {
            format!("{what} {}", source.name)
        }
    };
    let found = if source.kind == "window" {
        let window = capture::find_window(&source.id, place)
            .ok_or_else(|| MirrorError(format!("the {} is gone", named("window"))))?;
        capture::window_capture(ffmpeg, &window, fps, source.cursor, place)
    } else {
        let monitor = if source.kind == "virtual" {
            extend::virtual_monitor(place).ok_or_else(|| {
                MirrorError("no monitor of the panel's own: set it up first".into())
            })?
        } else {
            capture::find_monitor(&source.id, place)
                .ok_or_else(|| MirrorError(format!("the {} is gone", named("monitor"))))?
        };
        capture::monitor_capture(ffmpeg, &monitor, fps, source.cursor, source.region, place)
    };
    found.map_err(|capture::CaptureError(why)| MirrorError(why))
}

/// fitted: ffmpeg's filter fitting the captured pictures into a frame of width x height.
pub fn fitted(fit: &str, width: usize, height: usize) -> String {
    match fit {
        "cover" => format!(
            "scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
        ),
        "contain" => format!(
            "scale={width}:{height}:force_original_aspect_ratio=decrease,\
             pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        ),
        _ => format!("scale={width}:{height}"),
    }
}

/// frames_command: ffmpeg capturing `found` into raw RGB frames of `size` on its output,
/// `fps` a second (the newest is read: CaptureFeed).
pub fn frames_command(
    ffmpeg: &str,
    found: &Capture,
    size: (usize, usize),
    fit: &str,
    fps: i64,
) -> Vec<String> {
    let filters: Vec<String> = [
        found.prefilter.clone(),
        fitted(fit, size.0, size.1),
        format!("fps={fps}"),
    ]
    .into_iter()
    .filter(|part| !part.is_empty())
    .collect();
    let mut command = vec![
        ffmpeg.to_string(),
        "-v".into(),
        "error".into(),
        "-nostdin".into(),
    ];
    command.extend(found.inputs.iter().cloned());
    command.extend([
        "-an".into(),
        "-vf".into(),
        filters.join(","),
        "-pix_fmt".into(),
        "rgb24".into(),
        "-f".into(),
        "rawvideo".into(),
        "pipe:1".into(),
    ]);
    command
}

#[derive(Default)]
struct FeedState {
    newest: Option<Image>,
    count: u64,
    asked: f64,
    failed: Option<f64>,
    /// the run reading now (a new one replaces it)
    run: u64,
    children: Vec<Arc<Mutex<Child>>>,
    reading: bool,
}

/// CaptureFeed: the screen as ffmpeg captures it, into frames of `size`: a thread reads them
/// as they come and keeps the newest. ffmpeg starts with the first ask, stops IDLE_S after the
/// last, and starts again RETRY_S after it stopped by itself.
pub struct CaptureFeed {
    command: Vec<String>,
    size: (usize, usize),
    feeder: Vec<String>,
    began: Instant,
    state: Arc<Mutex<FeedState>>,
}

impl CaptureFeed {
    pub fn new(command: Vec<String>, size: (usize, usize), feeder: Vec<String>) -> CaptureFeed {
        CaptureFeed {
            command,
            size,
            feeder,
            began: Instant::now(),
            state: Arc::new(Mutex::new(FeedState::default())),
        }
    }

    fn clock(&self) -> f64 {
        self.began.elapsed().as_secs_f64()
    }

    /// frame: the number of the newest frame (a new one is a new picture) and the frame.
    pub fn frame(&self) -> (u64, Option<Image>) {
        let now = self.clock();
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        state.asked = now;
        let waited = state.failed.is_none_or(|at| now - at >= RETRY_S);
        if !state.reading && waited {
            self.start(&mut state);
        }
        (state.count, state.newest.clone())
    }

    fn start(&self, state: &mut FeedState) {
        let mut children = Vec::new();
        let mut stdin = Stdio::null();
        if !self.feeder.is_empty() {
            match capture::quiet(Command::new(&self.feeder[0]).args(&self.feeder[1..]))
                .stdout(Stdio::piped())
                .stderr(Stdio::null())
                .spawn()
            {
                Ok(mut feeder) => {
                    if let Some(out) = feeder.stdout.take() {
                        stdin = Stdio::from(out);
                    }
                    children.push(feeder);
                }
                Err(why) => {
                    warn(&format!("monitor: the capture did not start ({why})"));
                    state.failed = Some(self.clock());
                    return;
                }
            }
        }
        let started = capture::quiet(Command::new(&self.command[0]).args(&self.command[1..]))
            .stdin(stdin)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn();
        let mut ffmpeg = match started {
            Ok(ffmpeg) => ffmpeg,
            Err(why) => {
                warn(&format!("monitor: the capture did not start ({why})"));
                for mut child in children {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                state.failed = Some(self.clock());
                return;
            }
        };
        let Some(stream) = ffmpeg.stdout.take() else {
            return;
        };
        children.push(ffmpeg);
        let children: Vec<Arc<Mutex<Child>>> = children
            .into_iter()
            .map(|c| Arc::new(Mutex::new(c)))
            .collect();
        state.run += 1;
        state.children = children.clone();
        state.reading = true;
        let (run, size, shared, began) =
            (state.run, self.size, Arc::clone(&self.state), self.began);
        let reader = move || read(stream, run, size, &shared, began, &children);
        if std::thread::Builder::new()
            .name("monitor-feed".into())
            .spawn(reader)
            .is_err()
        {
            state.reading = false;
        }
    }

    pub fn close(&self) {
        let children = {
            let mut state = self
                .state
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            state.newest = None;
            state.asked = -IDLE_S * 2.0;
            state.run += 1; // (the reader's run is over)
            state.reading = false;
            std::mem::take(&mut state.children)
        };
        for child in children {
            let mut child = child
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            let _ = child.kill();
        }
    }
}

impl Drop for CaptureFeed {
    fn drop(&mut self) {
        self.close();
    }
}

fn read(
    mut stream: std::process::ChildStdout,
    run: u64,
    size: (usize, usize),
    shared: &Mutex<FeedState>,
    began: Instant,
    children: &[Arc<Mutex<Child>>],
) {
    let length = size.0 * size.1 * 3;
    let mut data = vec![0u8; length];
    loop {
        if stream.read_exact(&mut data).is_err() {
            break;
        }
        let Ok(picture) = Image::from_bytes(Mode::RGB, size.0, size.1, data.clone()) else {
            break;
        };
        let mut state = shared
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if state.run != run {
            break; // closed
        }
        state.newest = Some(picture);
        state.count += 1;
        if began.elapsed().as_secs_f64() - state.asked > IDLE_S {
            break; // nobody looks: the theme again
        }
    }
    for child in children {
        let mut child = child
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let _ = child.kill();
        let _ = child.wait();
    }
    let mut state = shared
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if state.run == run {
        state.reading = false;
        state.children.clear();
        let now = began.elapsed().as_secs_f64();
        if now - state.asked <= IDLE_S {
            state.failed = Some(now);
            warn("monitor: the capture stopped");
        }
    }
}

type ScreenKey = (Source, (usize, usize), i64);

/// Screen: what one panel's loop draws while it is a monitor: the newest picture of the
/// source at the panel's size, on black (the loop draws on it).
#[derive(Default)]
pub struct Screen {
    key: Option<ScreenKey>,
    feed: Option<CaptureFeed>,
    last: Option<Image>,
    ffmpeg: Option<String>,
    failed: Option<Instant>,
    /// why it shows nothing (for the app)
    pub error: String,
}

impl Screen {
    /// frame: the panel's frame of `source`, `size` large, `fps` a second.
    pub fn frame(&mut self, source: &Source, size: (usize, usize), fps: f64) -> Image {
        let rate = (fps as i64).max(1);
        let key = (source.clone(), size, rate);
        let waited = self
            .failed
            .is_none_or(|at| at.elapsed().as_secs_f64() >= RETRY_S);
        if self.key.as_ref() != Some(&key) && waited {
            self.open(source, size, rate);
            self.key = self.feed.is_some().then_some(key);
        }
        if let Some(feed) = &self.feed {
            if let (_, Some(picture)) = feed.frame() {
                self.last = Some(picture.clone());
                return picture;
            }
        }
        match &self.last {
            Some(last) if last.size() == size => last.clone(),
            _ => Image::new(Mode::RGB, size.0, size.1, &[0, 0, 0]),
        }
    }

    fn open(&mut self, source: &Source, size: (usize, usize), rate: i64) {
        self.close();
        if self.ffmpeg.is_none() {
            match lp_devices::h264::find_ffmpeg("") {
                Ok(found) => self.ffmpeg = Some(found.to_string_lossy().into_owned()),
                Err(why) => {
                    self.error = why;
                    self.failed = Some(Instant::now());
                    return;
                }
            }
        }
        let ffmpeg = self.ffmpeg.clone().unwrap_or_default();
        match capture_of(&ffmpeg, source, rate, Some(size)) {
            Ok(found) => {
                self.error = String::new();
                let command = frames_command(&ffmpeg, &found, size, &source.fit, rate);
                self.feed = Some(CaptureFeed::new(command, size, found.feeder));
            }
            Err(MirrorError(why)) => {
                if why != self.error {
                    warn(&format!("monitor: {why}"));
                }
                self.error = why;
                self.failed = Some(Instant::now());
            }
        }
    }

    pub fn close(&mut self) {
        if let Some(feed) = self.feed.take() {
            feed.close();
        }
        self.key = None;
    }
}
