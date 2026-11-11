//! What is playing: title, artist, album, position and the cover (sensors/media.py).
//!
//! - Windows: the system's media controls (what the volume pop-up shows). Windows tells when
//!   the session, the track or the playback changes, so a new track shows at once; a slow
//!   poll is the safety net. The cover is read when the track changes, not on every look.
//! - Linux: any MPRIS player (Spotify, browsers, VLC, ...) through `playerctl`, or where that
//!   is not installed, straight from the session's D-Bus with systemd's `busctl`.
//!
//! Only read while a theme shows `media.*` readings; a cover is fetched only when a theme
//! shows it. Nothing is sent anywhere.

#[cfg(not(windows))]
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_pixels::{Filter, Image, Mode};
use lp_py::Value;
use lp_render::Reading;

use crate::lhm::Readings;
use crate::service::{wanted, Wanted};

/// playerctl cannot tell what changes: it is asked this often
pub const POLL_SECONDS: f64 = 1.0;
/// Windows tells what changes; this only catches a notice that went missing
pub const SAFETY_SECONDS: f64 = 5.0;
/// after a notice, once more: players fill in the cover and the position later
pub const SETTLE_SECONDS: f64 = 1.0;
/// two looks are at least this far apart, whatever the notices
pub const MIN_GAP_SECONDS: f64 = 0.05;
/// a position further than this from the expected one is a seek
pub const SEEK_SECONDS: f64 = 1.0;
/// nobody looked for this long: the thread ends, and starts on demand
pub const IDLE_SECONDS: f64 = 60.0;
/// while nobody looks: how often to see whether somebody does
pub const IDLE_CHECK_SECONDS: f64 = 1.0;
/// a cover that could not be read is asked for again after this long
pub const COVER_RETRY_SECONDS: f64 = 10.0;
pub const MAX_COVER: usize = 4 * 1024 * 1024;
#[cfg(not(windows))]
/// a command that takes longer than this has failed
const COMMAND_SECONDS: f64 = 3.0;

/// A track's cover: a file or a web address, or the picture itself.
#[derive(Clone, Debug)]
pub enum Cover {
    Place(String),
    Picture(Arc<Vec<u8>>),
}

impl PartialEq for Cover {
    fn eq(&self, other: &Cover) -> bool {
        match (self, other) {
            (Cover::Place(a), Cover::Place(b)) => a == b,
            // (the same bytes held twice are the common case, and need no comparing)
            (Cover::Picture(a), Cover::Picture(b)) => Arc::ptr_eq(a, b) || a == b,
            _ => false,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Track {
    /// "playing", "paused" or "stopped"
    pub state: String,
    pub title: String,
    pub artist: String,
    pub album: String,
    /// seconds, at `at`
    pub position: Option<f64>,
    pub duration: Option<f64>,
    pub cover: Option<Cover>,
    pub source: String,
    /// the time of the position, on the source's clock
    pub at: f64,
    /// what finds the player's icon: the AppUserModelID or the MPRIS name
    pub app_id: String,
}

impl Track {
    pub fn readings(&self, now: f64) -> Readings {
        let mut out = Readings::new();
        let mut put = |key: &str, value: Value, unit: &str, label: &str| {
            if matches!(&value, Value::Str(text) if text.is_empty()) {
                return;
            }
            let reading = Reading {
                unit: unit.into(),
                label: label.into(),
                ..Reading::of(value)
            };
            out.insert(key.to_string(), reading);
        };
        let text = |text: &str| Value::Str(text.to_string());
        put("media.title", text(&self.title), "", "Title");
        put("media.artist", text(&self.artist), "", "Artist");
        put("media.album", text(&self.album), "", "Album");
        put("media.source", text(&self.source), "", "Player");
        put(
            "media.state",
            Value::Str(lp_py::capitalize(&self.state)),
            "",
            "Media",
        );
        let playing = self.state == "playing";
        put(
            "media.playing",
            Value::int(i64::from(playing)),
            "",
            "Playing",
        );
        let mut position = self.position;
        if let (Some(at), true) = (&mut position, playing) {
            *at += 0.0f64.max(now - self.at); // it moved on since it was asked
        }
        let duration = self.duration.filter(|duration| *duration != 0.0);
        if let Some(duration) = duration {
            position = position.map(|at| at.min(duration));
            put("media.duration", Value::Float(duration), "s", "Length");
        }
        if let Some(at) = position {
            put("media.position", Value::Float(at), "s", "Position");
            if let Some(duration) = duration {
                put(
                    "media.progress",
                    Value::Float(at / duration),
                    "",
                    "Progress",
                );
                put(
                    "media.remaining",
                    Value::Float(duration - at),
                    "s",
                    "Remaining",
                );
            }
        }
        out
    }
}

/// "Spotify.exe" -> "Spotify"; "Microsoft.ZuneMusic_8wekyb3d8bbwe!App" -> "ZuneMusic".
pub fn app_name(model_id: &str) -> String {
    let name = model_id.split('!').next().unwrap_or("");
    let name = name.split('_').next().unwrap_or("");
    let mut name = name.rsplit('\\').next().unwrap_or("");
    if lp_py::lower(name).ends_with(".exe") {
        let end = name.char_indices().rev().nth(3).map_or(0, |(at, _)| at);
        name = &name[..end];
    }
    name.rsplit('.').next().unwrap_or("").to_string()
}

// -- Linux -------------------------------------------------------------------------------

const SEP: char = '\x1f';
/// What playerctl is asked to print: the fields, a unit separator between them.
pub const FORMAT: &str = "{{status}}\x1f{{artist}}\x1f{{title}}\x1f{{album}}\x1f{{position}}\
\x1f{{mpris:length}}\x1f{{mpris:artUrl}}\x1f{{playerName}}";

fn state_of(status: &str) -> String {
    let status = lp_py::lower(status);
    match status.as_str() {
        "playing" | "paused" => status,
        _ => "stopped".into(),
    }
}

/// Seconds of a text of microseconds; none where it is no whole number.
fn micro(text: &str) -> Option<f64> {
    let n = lp_py::int_of_str(text)?;
    lp_py::num::int_true_div(&n, &num_bigint::BigInt::from(1_000_000)).ok()
}

pub fn parse_playerctl(text: &str, at: f64) -> Option<Track> {
    let parts: Vec<&str> = text.trim_end_matches('\n').split(SEP).collect();
    let [status, artist, title, album, position, length, art, player] = parts[..] else {
        return None;
    };
    if title.is_empty() {
        return None;
    }
    Some(Track {
        state: state_of(status),
        title: title.into(),
        artist: artist.into(),
        album: album.into(),
        position: micro(position),
        duration: micro(length).filter(|length| *length != 0.0),
        cover: (!art.is_empty()).then(|| Cover::Place(art.into())),
        source: lp_py::capitalize(player),
        at,
        app_id: lp_py::lower(player),
    })
}

pub const MPRIS: &str = "org.mpris.MediaPlayer2.";

/// A value of busctl's JSON without its D-Bus type (`{"type": "s", "data": "x"}`).
fn plain(value: &Value) -> &Value {
    match value {
        Value::Dict(dict) if dict.len() == 2 && dict.contains_key("type") => {
            dict.get("data").map_or(value, plain)
        }
        _ => value,
    }
}

/// A player's `org.mpris.MediaPlayer2.Player` properties, as `busctl --json` gives GetAll's
/// answer, as a track; None without a title.
pub fn parse_mpris(reply: &Value, name: &str, at: f64) -> Option<Track> {
    let properties = match reply.as_dict()?.get("data")? {
        Value::List(data) => data.first()?,
        _ => return None,
    };
    let properties = properties.as_dict()?;
    let none = Value::None;
    let empty = IndexMap::new();
    let metadata = properties.get("Metadata").map_or(&none, plain);
    let metadata = metadata.as_dict().unwrap_or(&empty);
    let text = |key: &str| -> String {
        match metadata.get(key).map_or(&none, plain) {
            Value::List(items) => {
                let shown: Vec<String> = items
                    .iter()
                    .filter(|item| item.truthy())
                    .map(Value::str)
                    .collect();
                shown.join(", ")
            }
            Value::None => String::new(),
            Value::Str(text) if text.is_empty() => String::new(),
            other => other.str(),
        }
    };
    let seconds = |value: Option<&Value>| -> Option<f64> {
        match plain(value?) {
            Value::Int(n) => lp_py::num::int_true_div(n, &num_bigint::BigInt::from(1_000_000)).ok(),
            Value::Float(f) => Some(f / 1_000_000.0),
            _ => None,
        }
    };
    let title = text("xesam:title");
    if title.is_empty() {
        return None;
    }
    let status = match properties.get("PlaybackStatus").map_or(&none, plain) {
        status if status.truthy() => status.str(),
        _ => String::new(),
    };
    let player = match name.strip_prefix(MPRIS) {
        Some(rest) => rest.split('.').next().unwrap_or(""),
        None => name,
    };
    let art = text("mpris:artUrl");
    Some(Track {
        state: state_of(&status),
        title,
        artist: text("xesam:artist"),
        album: text("xesam:album"),
        position: seconds(properties.get("Position")),
        duration: seconds(metadata.get("mpris:length")).filter(|length| *length != 0.0),
        cover: (!art.is_empty()).then_some(Cover::Place(art)),
        source: lp_py::capitalize(player),
        at,
        app_id: lp_py::lower(player),
    })
}

#[cfg(not(windows))]
/// shutil.which: the program of this name on the PATH.
fn which(name: &str) -> Option<PathBuf> {
    let paths = std::env::var_os("PATH")?;
    std::env::split_paths(&paths)
        .map(|folder| folder.join(name))
        .find(|path| runs(path))
}

#[cfg(unix)]
fn runs(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

#[cfg(not(any(unix, windows)))]
fn runs(path: &Path) -> bool {
    path.is_file()
}

#[cfg(not(windows))]
/// What a command prints, if it ends well in time; an error where it cannot be started or
/// takes too long.
fn run(program: &Path, arguments: &[&str]) -> Result<Option<String>, String> {
    use std::io::Read;
    use std::process::{Command, Stdio};
    let mut child = Command::new(program)
        .args(arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| e.to_string())?;
    let mut pipe = child.stdout.take().ok_or("no output")?;
    let reader = std::thread::spawn(move || {
        let mut out = Vec::new();
        let _ = pipe.read_to_end(&mut out);
        out
    });
    let end = Instant::now() + Duration::from_secs_f64(COMMAND_SECONDS);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < end => std::thread::sleep(Duration::from_millis(5)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                let name = program.file_name().unwrap_or_default().to_string_lossy();
                return Err(format!("{name} timed out after {COMMAND_SECONDS} seconds"));
            }
        }
    };
    let out = reader.join().unwrap_or_default();
    Ok(status
        .success()
        .then(|| String::from_utf8_lossy(&out).into_owned()))
}

/// How what plays is asked for.
pub trait Backend {
    /// how it reads, for the source's status
    fn via(&self) -> String;
    /// What plays at `now` (the source's clock); an error where the asking failed.
    fn poll(&mut self, want_cover: bool, now: &dyn Fn() -> f64) -> Result<Option<Track>, String>;
    /// Seconds until the next look when nothing is reported.
    fn interval(&self) -> f64 {
        POLL_SECONDS
    }
    /// the last look read the timeline alone (no settle look)
    fn timeline_only(&self) -> bool {
        false
    }
    /// `wake` is called, from any thread, when the player has news.
    fn bind(&mut self, _wake: Arc<dyn Fn() + Send + Sync>) {}
    fn close(&mut self) {}
}

#[cfg(not(windows))]
struct Playerctl(PathBuf);

#[cfg(not(windows))]
impl Backend for Playerctl {
    fn via(&self) -> String {
        "playerctl".into()
    }

    fn poll(&mut self, _cover: bool, now: &dyn Fn() -> f64) -> Result<Option<Track>, String> {
        // (no player: it fails)
        let printed = run(&self.0, &["metadata", "--format", FORMAT])?;
        Ok(printed.and_then(|text| parse_playerctl(&text, now())))
    }
}

#[cfg(not(windows))]
/// MPRIS players straight from the session's D-Bus, with systemd's `busctl`: for a Linux
/// without `playerctl`. The one that plays, else one that is paused.
struct Busctl(PathBuf);

#[cfg(not(windows))]
impl Busctl {
    fn call(&self, arguments: &[&str]) -> Result<Option<Value>, String> {
        let mut all = vec!["--user", "--json=short", "call"];
        all.extend(arguments);
        let printed = run(&self.0, &all)?;
        Ok(printed.and_then(|text| lp_py::json::loads(&text).ok()))
    }

    fn players(&self) -> Result<Vec<String>, String> {
        let bus = "org.freedesktop.DBus";
        let reply = self.call(&[bus, "/org/freedesktop/DBus", bus, "ListNames"])?;
        let names = reply
            .as_ref()
            .and_then(|reply| match reply.as_dict()?.get("data")? {
                Value::List(data) => data.first()?.as_list(),
                _ => None,
            });
        Ok(names
            .unwrap_or_default()
            .iter()
            .filter_map(Value::as_str)
            .filter(|name| name.starts_with(MPRIS))
            .map(str::to_string)
            .collect())
    }
}

/// The track that counts of several players': one that plays, else one that is paused (the
/// first of equals).
pub fn first_playing(found: Vec<Track>) -> Option<Track> {
    let rank = |track: &Track| match track.state.as_str() {
        "playing" => 0,
        "paused" => 1,
        _ => 2,
    };
    let mut best: Option<Track> = None;
    for track in found {
        if best.as_ref().is_none_or(|b| rank(&track) < rank(b)) {
            best = Some(track);
        }
    }
    best
}

#[cfg(not(windows))]
impl Backend for Busctl {
    fn via(&self) -> String {
        "busctl (MPRIS on D-Bus; playerctl is not installed)".into()
    }

    fn poll(&mut self, _cover: bool, now: &dyn Fn() -> f64) -> Result<Option<Track>, String> {
        let mut found = Vec::new();
        for name in self.players()? {
            let reply = self.call(&[
                &name,
                "/org/mpris/MediaPlayer2",
                "org.freedesktop.DBus.Properties",
                "GetAll",
                "s",
                "org.mpris.MediaPlayer2.Player",
            ])?;
            found.extend(reply.and_then(|reply| parse_mpris(&reply, &name, now())));
        }
        Ok(first_playing(found))
    }
}

/// The way what plays is asked for on this computer; an error where there is none.
fn backend() -> Result<Box<dyn Backend>, String> {
    #[cfg(windows)]
    {
        Ok(Box::new(crate::media_windows::Controls::new()?))
    }
    #[cfg(not(windows))]
    {
        if let Some(command) = which("playerctl") {
            return Ok(Box::new(Playerctl(command)));
        }
        match which("busctl") {
            Some(command) => Ok(Box::new(Busctl(command))),
            None => Err("neither playerctl nor busctl is installed".into()),
        }
    }
}

// -- the source --------------------------------------------------------------------------

/// The path of a `file://` address (urllib's url2pathname of its path): its percent escapes
/// read, on Windows the drive's slash dropped and the others turned.
fn file_of(address: &str) -> String {
    let rest = &address["file://".len()..];
    // (urlparse: the host up to the next slash, then the path up to ? or #)
    let path = rest.find('/').map_or("", |at| &rest[at..]);
    let path = path.split(['?', '#']).next().unwrap_or("");
    let mut bytes = Vec::new();
    let raw = path.as_bytes();
    let mut at = 0;
    while at < raw.len() {
        let hex = |byte: u8| (byte as char).to_digit(16);
        match (
            raw[at],
            raw.get(at + 1).copied().and_then(hex),
            raw.get(at + 2).copied().and_then(hex),
        ) {
            (b'%', Some(high), Some(low)) => {
                bytes.push((high * 16 + low) as u8);
                at += 3;
            }
            (byte, ..) => {
                bytes.push(byte);
                at += 1;
            }
        }
    }
    let path = String::from_utf8_lossy(&bytes).into_owned();
    if cfg!(windows) {
        let path = path.replace('/', "\\");
        let drive = path.as_bytes().get(2) == Some(&b':') || path.as_bytes().get(2) == Some(&b'|');
        return match path.strip_prefix('\\') {
            Some(rest) if drive => rest.replacen('|', ":", 1),
            _ => path,
        };
    }
    path
}

/// The bytes of a cover from a file (path or file://) or the web; none where it is not
/// there, too big, or cannot be had.
fn cover_bytes(place: &str) -> Option<Vec<u8>> {
    if place.starts_with("http://") || place.starts_with("https://") {
        let named = [("User-Agent", "libre-panel")];
        let data = lp_net::get(place, &named, 5.0, MAX_COVER + 1).ok()?;
        return (data.len() <= MAX_COVER).then_some(data);
    }
    let path = match place.starts_with("file://") {
        true => file_of(place),
        false => place.to_string(),
    };
    let found = std::fs::metadata(&path).ok()?;
    if !found.is_file() || found.len() > MAX_COVER as u64 {
        return None;
    }
    std::fs::read(&path).ok()
}

/// A cover from its bytes, a file (path or file://) or the web: no larger than 512 pixels,
/// in RGBA; none where it cannot be read (a broken cover is no error).
pub fn load_cover(cover: &Cover) -> Option<Image> {
    let fetched;
    let data: &[u8] = match cover {
        Cover::Picture(data) => data,
        Cover::Place(place) if place.is_empty() => return None,
        Cover::Place(place) => {
            fetched = cover_bytes(place)?;
            &fetched
        }
    };
    if data.is_empty() {
        return None;
    }
    let small = lp_decode::thumbnail(data, (512.0, 512.0), Filter::Bicubic, Some(2.0)).ok()?;
    small.convert(Mode::RGBA).ok()
}

/// How what plays is read and what plays, or why it cannot be read.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Status {
    pub read_with: Option<String>,
    pub now: Option<String>,
    pub error: Option<String>,
}

#[derive(Default)]
struct Flags {
    stop: bool,
    runs: bool,
    /// the player has news
    wake: bool,
}

struct Shared {
    track: Mutex<Option<Track>>,
    /// the cover that was loaded last, its picture, and which one it is of those loaded
    cover: Mutex<(Option<Cover>, Option<Image>, u64)>,
    flags: Mutex<Flags>,
    changed: Condvar,
    notes: Mutex<Vec<String>>,
    /// how what plays is read, once it is; why it cannot be read
    via: Mutex<String>,
    problem: Mutex<String>,
    warned: Mutex<bool>,
    wanted: Wanted,
    began: Instant,
}

impl Shared {
    fn now(&self) -> f64 {
        self.began.elapsed().as_secs_f64()
    }

    fn wants(&self, prefix: &str) -> bool {
        wanted(&self.wanted.keys(), prefix)
    }

    fn stopped(&self) -> bool {
        self.flags.lock().map(|flags| flags.stop).unwrap_or(true)
    }

    /// Ask the backend and keep the answer; true if the backend failed.
    fn look(&self, backend: &mut dyn Backend) -> bool {
        let now = || self.now();
        let (track, failed) = match backend.poll(self.wants("media.cover"), &now) {
            Ok(track) => (track, false),
            Err(_) => (None, true), // (a player must not stop the panel)
        };
        if let Some(cover) = track.as_ref().and_then(|track| track.cover.as_ref()) {
            let known = self
                .cover
                .lock()
                .is_ok_and(|kept| kept.0.as_ref() == Some(cover));
            if !known {
                let picture = load_cover(cover);
                if let Ok(mut kept) = self.cover.lock() {
                    *kept = (Some(cover.clone()), picture, kept.2 + 1);
                }
            }
        }
        if let Ok(mut kept) = self.track.lock() {
            *kept = track;
        }
        failed
    }

    /// Look when the player has news (a backend that has events), and every `interval`
    /// otherwise. After news, once more a moment later (`SETTLE_SECONDS`): the cover and the
    /// position come in after the title.
    fn run(self: &Arc<Shared>, backend: &mut dyn Backend) {
        let waker = self.clone();
        backend.bind(Arc::new(move || {
            if let Ok(mut flags) = waker.flags.lock() {
                flags.wake = true;
            }
            waker.changed.notify_all();
        }));
        let mut idle_since: Option<Instant> = None;
        let mut news = false;
        while !self.stopped() {
            // before looking: news that comes meanwhile brings the next look
            if let Ok(mut flags) = self.flags.lock() {
                flags.wake = false;
            }
            let pause = if self.wants("media.") {
                idle_since = None;
                let failed = self.look(backend);
                std::thread::sleep(Duration::from_secs_f64(MIN_GAP_SECONDS));
                let settle = news && !backend.timeline_only();
                let wait = if settle {
                    SETTLE_SECONDS
                } else {
                    backend.interval()
                };
                // (a backend that failed starts anew and has no events yet: look soon)
                if failed {
                    wait.min(POLL_SECONDS)
                } else {
                    wait
                }
            } else {
                let since = *idle_since.get_or_insert_with(Instant::now);
                if since.elapsed().as_secs_f64() > IDLE_SECONDS {
                    // nobody looked for a minute: end, start again on demand
                    if let Ok(mut track) = self.track.lock() {
                        *track = None;
                    }
                    break;
                }
                IDLE_CHECK_SECONDS
            };
            let Ok(flags) = self.flags.lock() else {
                break;
            };
            let woken =
                self.changed
                    .wait_timeout_while(flags, Duration::from_secs_f64(pause), |flags| {
                        !flags.wake && !flags.stop
                    });
            news = woken.is_ok_and(|(flags, _)| flags.wake);
        }
        backend.close();
    }

    fn thread(self: &Arc<Shared>) {
        match backend() {
            Ok(mut backend) => {
                if let Ok(mut via) = self.via.lock() {
                    *via = backend.via();
                }
                if let Ok(mut problem) = self.problem.lock() {
                    problem.clear();
                }
                self.run(backend.as_mut());
            }
            Err(why) => {
                if let Ok(mut problem) = self.problem.lock() {
                    *problem = why.clone();
                }
                let first = self
                    .warned
                    .lock()
                    .is_ok_and(|mut warned| !std::mem::replace(&mut *warned, true));
                if let (true, Ok(mut notes)) = (first, self.notes.lock()) {
                    notes.push(format!("media: what is playing cannot be read: {why}"));
                }
            }
        }
        if let Ok(mut flags) = self.flags.lock() {
            flags.runs = false;
        }
    }
}

/// media.* readings and the `media.cover` picture, while a theme or a service reads them.
pub struct MediaSource {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl MediaSource {
    pub fn new(wanted: Wanted) -> MediaSource {
        MediaSource {
            shared: Arc::new(Shared {
                track: Mutex::new(None),
                cover: Mutex::new((None, None, 0)),
                flags: Mutex::new(Flags::default()),
                changed: Condvar::new(),
                notes: Mutex::new(Vec::new()),
                via: Mutex::new(String::new()),
                problem: Mutex::new(String::new()),
                warned: Mutex::new(false),
                wanted,
                began: Instant::now(),
            }),
            thread: None,
        }
    }

    fn track(&self) -> Option<Track> {
        self.shared
            .track
            .lock()
            .ok()
            .and_then(|track| track.clone())
    }

    /// What plays now: news shows in the next frame (a new song, play or pause), and the
    /// position moves on at each frame's time. The thread is started where a theme reads
    /// media.* and it does not run.
    pub fn read(&mut self) -> Readings {
        let (stopped, runs) = match self.shared.flags.lock() {
            Ok(flags) => (flags.stop, flags.runs),
            Err(_) => (true, false),
        };
        if stopped || !self.shared.wants("media.") {
            return Readings::new();
        }
        if !runs {
            if let Some(ended) = self.thread.take() {
                let _ = ended.join();
            }
            if let Ok(mut flags) = self.shared.flags.lock() {
                flags.runs = true;
            }
            let shared = self.shared.clone();
            self.thread = std::thread::Builder::new()
                .name("sensors-media".into())
                .spawn(move || shared.thread())
                .ok();
        }
        let now = self.shared.now();
        self.track()
            .map_or(Readings::new(), |track| track.readings(now))
    }

    /// How what plays is read and what plays, or why it cannot be read; nothing before a
    /// theme or a service asks.
    pub fn status(&self) -> Status {
        let text = |held: &Mutex<String>| {
            let text = held.lock().map(|text| text.clone()).unwrap_or_default();
            (!text.is_empty()).then_some(text)
        };
        let track = self.track().filter(|track| !track.title.is_empty());
        Status {
            read_with: text(&self.shared.via),
            now: track.map(|track| format!("{}: {}", track.state, track.title)),
            error: text(&self.shared.problem),
        }
    }

    /// The pictures of what plays: `media.cover`, where the track has one that was read.
    pub fn images(&self) -> IndexMap<String, Image> {
        let mut found = IndexMap::new();
        let cover = self.track().and_then(|track| track.cover);
        if let (Some(cover), Ok(kept)) = (cover, self.shared.cover.lock()) {
            if let (true, Some(picture)) = (kept.0.as_ref() == Some(&cover), &kept.1) {
                found.insert("media.cover".to_string(), picture.clone());
            }
        }
        found
    }

    /// Which pictures `images` gives now: 0 for none, else a number that changes with them
    /// (asked on every frame, where the pictures themselves are only taken when it changed).
    pub fn images_state(&self) -> u64 {
        let cover = self.track().and_then(|track| track.cover);
        match (cover, self.shared.cover.lock()) {
            (Some(cover), Ok(kept)) if kept.0.as_ref() == Some(&cover) && kept.1.is_some() => {
                kept.2
            }
            _ => 0,
        }
    }

    /// What the source noted for the log since it was last asked.
    pub fn notes(&self) -> Vec<String> {
        let notes = self.shared.notes.lock();
        notes
            .map(|mut notes| std::mem::take(&mut *notes))
            .unwrap_or_default()
    }

    pub fn close(&mut self) {
        if let Ok(mut flags) = self.shared.flags.lock() {
            flags.stop = true;
        }
        self.shared.changed.notify_all(); // the thread ends at once
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for MediaSource {
    fn drop(&mut self) {
        self.close();
    }
}
