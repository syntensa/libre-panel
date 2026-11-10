//! The app's server (editor/server.py): the pages of the app and what they ask, on
//! 127.0.0.1 only, to a name of this server only and, but for the app's own files, with the
//! token of this run (access.rs). Each request on a thread of its own, HTTP/1.0 as Python's
//! http.server speaks it (http.rs).
//!
//! The routes this build has: the app's files, the fonts, the old editor's files and its
//! bookmark, the texts in the app's language, and the background app's state and controls.
//! A path of the Python app's that is not here yet answers "not found", after the token.

use std::net::{Ipv4Addr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, Weak};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use lp_i18n::t;
use lp_py::{Dict, Value};
use lp_render::Snapshot;

use crate::access;
use crate::background::BackgroundApp;
use crate::home::Home;
use crate::http::{urlparse, Exchange, Url};
use crate::settings::{Answer, Settings};

/// the port the app's server takes where it is free
pub const PORT: u16 = 8765;
const MAX_JSON: u64 = 2 * 1024 * 1024;
/// the tables of a language the app's pages read
const EDITOR_TABLES: [&str; 5] = ["messages", "fields", "widgets", "enums", "icons"];

fn say(message: &str) {
    eprintln!("libre-panel: {message}");
}

/// What the server's requests share (EditorState, as far as this build has it).
pub struct State {
    home: Home,
    config_file: PathBuf,
    /// the running background app (`start`); none for a plain `libre-panel editor`
    controls: Weak<BackgroundApp>,
    began: Instant,
    /// when a page of the app last asked something, in seconds since the server began
    last_request: Mutex<Option<f64>>,
    /// how the server's own hub of sensors is asked (a server without a background app)
    live: Mutex<Option<std::sync::mpsc::Sender<Reply>>>,
    /// settings the panel loop takes in when it restarts
    restart: crate::settings::Restart,
}

impl State {
    /// When a page of the app last asked something.
    #[allow(dead_code)] // (for light mode: what only the app needs goes while it is closed)
    pub fn last_request(&self) -> Option<f64> {
        self.last_request.lock().ok().and_then(|at| *at)
    }
}

/// A part of a route's template: text as it is, `{name}` one part of the path, `{name+}`
/// the rest of it (one character or more), `{name*}` the rest, or nothing.
#[derive(Clone, Copy)]
enum Part {
    Text(&'static str),
    One,
    Rest,
    RestOrNothing,
}

fn parts_of(template: &'static str) -> Vec<Part> {
    let (mut parts, mut rest) = (Vec::new(), template);
    while let Some(open) = rest.find('{') {
        let close = rest[open..]
            .find('}')
            .map_or(rest.len(), |close| open + close);
        if open > 0 {
            parts.push(Part::Text(&rest[..open]));
        }
        parts.push(match rest[open..close].chars().last() {
            Some('+') => Part::Rest,
            Some('*') => Part::RestOrNothing,
            _ => Part::One,
        });
        rest = &rest[(close + 1).min(rest.len())..];
    }
    if !rest.is_empty() {
        parts.push(Part::Text(rest));
    }
    parts
}

/// pattern.fullmatch(path): what the template's names stand for, in their order.
fn matched<'a>(parts: &[Part], path: &'a str) -> Option<Vec<&'a str>> {
    let Some((part, later)) = parts.split_first() else {
        return path.is_empty().then(Vec::new);
    };
    if let Part::Text(text) = part {
        return matched(later, path.strip_prefix(text)?);
    }
    // (the longest first, as a pattern takes it)
    let ends = (0..=path.len())
        .rev()
        .filter(|end| path.is_char_boundary(*end));
    for end in ends {
        let taken = &path[..end];
        let fits = match part {
            Part::One => !taken.is_empty() && !taken.contains('/'),
            Part::Rest => !taken.is_empty(),
            _ => true,
        };
        if fits && !taken.contains('\n') {
            if let Some(mut found) = matched(later, &path[end..]) {
                found.insert(0, taken);
                return Some(found);
            }
        }
    }
    None
}

type Handle = fn(&State, &mut Exchange, &[&str], &Url);

/// One way into the server: the method, the path as a template, what to do.
struct Route {
    method: &'static str,
    template: &'static str,
    handle: Handle,
    /// the app's own files (code, no data): no token needed
    public: bool,
    /// an <img> asks for it and cannot send the header: the token may be in the query
    picture: bool,
}

const fn public(method: &'static str, template: &'static str, handle: Handle) -> Route {
    Route {
        method,
        template,
        handle,
        public: true,
        picture: false,
    }
}

const fn private(method: &'static str, template: &'static str, handle: Handle) -> Route {
    Route {
        method,
        template,
        handle,
        public: false,
        picture: false,
    }
}

const fn picture(method: &'static str, template: &'static str, handle: Handle) -> Route {
    Route {
        method,
        template,
        handle,
        public: false,
        picture: true,
    }
}

const ROUTES: &[Route] = &[
    // -- the app: its page, modules, fonts, styles -----------------------------------------------
    public("GET", "/", |s, x, _, _| app_file(s, x, "index.html")),
    public("GET", "/index.html", |s, x, _, _| {
        app_file(s, x, "index.html")
    }),
    // a bookmark of the old editor leads to the app
    public("GET", "/classic", |_, x, _, _| x.redirect("/")),
    public("GET", "/classic/", |_, x, _, _| x.redirect("/")),
    public("GET", "/classic/index.html", |_, x, _, _| x.redirect("/")),
    public("GET", "/app/fonts/{name*}", |s, x, m, _| font(s, x, m[0])),
    public("GET", "/app/{name*}", |s, x, m, _| app_file(s, x, m[0])),
    public("GET", "/static/{name*}", |s, x, m, _| {
        static_file(s, x, m[0])
    }),
    // -- reading -----------------------------------------------------------------------------------
    private("GET", "/api/themes", |s, x, _, _| {
        x.json(&theme_list(s), 200)
    }),
    private("GET", "/api/themes/{theme}/assets", |s, x, m, _| {
        assets(s, x, m[0])
    }),
    picture("GET", "/api/themes/{theme}/assets/{rel+}", |s, x, m, _| {
        asset_file(s, x, m[0], m[1])
    }),
    // (not in this build yet: kept from the route of a theme by its name)
    picture("GET", "/api/themes/{theme}/preview", |_, x, _, _| {
        not_found(x)
    }),
    private("GET", "/api/themes/{theme}/export", |_, x, _, _| {
        not_found(x)
    }),
    private("GET", "/api/themes/{theme}/sizes", |_, x, _, _| {
        not_found(x)
    }),
    private("GET", "/api/readings", |s, x, _, u| readings(s, x, u)),
    private("GET", "/api/settings", |s, x, _, _| {
        settings(s, x, |told| told.read())
    }),
    private("GET", "/api/pages", |s, x, _, u| {
        let query = crate::http::parse_qs(&u.query);
        let panel = query.get("panel").and_then(|asked| asked.first());
        match settings_of(s).pages(panel.map(String::as_str)) {
            Ok(told) => x.json(&told, 200),
            Err(refused) => x.error(&refused.message, refused.status, ""),
        }
    }),
    private("GET", "/api/sequence", |s, x, _, _| {
        settings(s, x, |told| told.sequence())
    }),
    private("GET", "/api/panel", |s, x, _, _| {
        settings(s, x, |told| told.panel())
    }),
    private("GET", "/api/clip", |s, x, _, _| {
        settings(s, x, |told| told.clip_question())
    }),
    private("GET", "/api/about", |s, x, _, _| {
        settings(s, x, |told| told.about())
    }),
    private("GET", "/api/scenes", |s, x, _, _| {
        settings(s, x, |told| told.scenes())
    }),
    private("GET", "/api/themes/{theme+}", |s, x, m, _| {
        get_theme(s, x, m[0])
    }),
    private("GET", "/api/sensors", |s, x, _, _| sensors(s, x)),
    private("GET", "/api/i18n", |s, x, _, _| {
        x.json(&Value::Dict(i18n(s)), 200)
    }),
    private("GET", "/api/app", |s, x, _, _| app_state(s, x)),
    // -- writing -----------------------------------------------------------------------------------
    private("POST", "/api/app", |s, x, _, _| app_action(s, x)),
    private("POST", "/api/settings", |s, x, _, _| {
        settings_with(s, x, |told, asked| told.write(asked))
    }),
    private("POST", "/api/pages", |s, x, _, _| {
        settings_with(s, x, |told, asked| told.set_page(asked))
    }),
    private("POST", "/api/sequence", |s, x, _, _| {
        settings_with(s, x, |told, asked| told.set_sequence(asked))
    }),
    private("POST", "/api/scenes", |s, x, _, _| {
        settings_with(s, x, |told, asked| told.set_scene(asked))
    }),
];

fn settings_of(state: &State) -> Settings<'_> {
    Settings {
        home: &state.home,
        config_file: &state.config_file,
        controls: state.controls.upgrade(),
        restart: &state.restart,
    }
}

/// _settings: a route of the Settings (settings.rs): its answer, or why it is refused.
fn settings(state: &State, exchange: &mut Exchange, method: fn(&Settings) -> Answer<Value>) {
    match method(&settings_of(state)) {
        Ok(told) => exchange.json(&told, 200),
        Err(refused) => exchange.error(&refused.message, refused.status, ""),
    }
}

/// A route of the Settings that takes what the request says.
fn settings_with(
    state: &State,
    exchange: &mut Exchange,
    method: fn(&Settings, &Value) -> Answer<Value>,
) {
    let Some(payload) = exchange.read_json(MAX_JSON) else {
        return;
    };
    match method(&settings_of(state), &payload) {
        Ok(told) => exchange.json(&told, 200),
        Err(refused) => exchange.error(&refused.message, refused.status, ""),
    }
}

fn not_found(exchange: &mut Exchange) {
    exchange.error("not found", 404, "");
}

/// Path(name).suffix
fn suffix(name: &str) -> &str {
    let last = name.rsplit('/').next().unwrap_or(name);
    match last.rfind('.') {
        Some(dot) if dot > 0 && dot + 1 < last.len() => &last[dot..],
        _ => "",
    }
}

fn content_type(suffix: &str) -> Option<&'static str> {
    Some(match suffix {
        ".html" => "text/html; charset=utf-8",
        ".js" => "text/javascript; charset=utf-8",
        ".css" => "text/css; charset=utf-8",
        ".svg" => "image/svg+xml; charset=utf-8",
        ".png" => "image/png",
        _ => return None,
    })
}

fn name_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-'
}

/// _APP_PATH: at most four folders and a file, each a name that does not begin with a dot.
fn app_path(name: &str) -> bool {
    let parts: Vec<&str> = name.split('/').collect();
    let named = |part: &&str| {
        let bytes = part.as_bytes();
        bytes.first().is_some_and(|first| name_byte(*first))
            && bytes.iter().all(|byte| name_byte(*byte) || *byte == b'.')
    };
    parts.len() <= 5 && parts.iter().all(named)
}

/// A file inside `base` and nowhere else, read.
fn file_in(base: &Path, name: &str) -> Option<Vec<u8>> {
    let base = std::fs::canonicalize(base).ok()?;
    let file = std::fs::canonicalize(base.join(name)).ok()?;
    (file.starts_with(&base) && file.is_file()).then(|| std::fs::read(&file).ok())?
}

/// _app_file: a file of the app: its page, modules, styles and vendored libraries.
fn app_file(state: &State, exchange: &mut Exchange, name: &str) {
    let kind = match suffix(name).to_ascii_lowercase().as_str() {
        ".txt" => Some("text/plain; charset=utf-8"),
        other => content_type(other),
    };
    let found = kind
        .filter(|_| app_path(name) && !name.contains(".."))
        .and_then(|kind| {
            let base = state.home.data.join("editor").join("app");
            Some((file_in(&base, name)?, kind))
        });
    match found {
        Some((body, kind)) => exchange.send(200, &body, kind, ""),
        None => not_found(exchange),
    }
}

/// _static: a file of the old editor.
fn static_file(state: &State, exchange: &mut Exchange, name: &str) {
    let plain = !name.is_empty()
        && !name.starts_with('.')
        && name.bytes().all(|byte| name_byte(byte) || byte == b'.');
    let file = state.home.data.join("editor").join("static").join(name);
    if !plain || !file.is_file() {
        return not_found(exchange);
    }
    match std::fs::read(&file) {
        Ok(body) => {
            let kind = content_type(suffix(name)).unwrap_or("application/octet-stream");
            exchange.send(200, &body, kind, "");
        }
        Err(_) => not_found(exchange),
    }
}

/// _font: the bundled fonts, for the app's own text (never from the network).
fn font(state: &State, exchange: &mut Exchange, name: &str) {
    let env = lp_theme::Env {
        fonts_dir: state.home.fonts(),
    };
    let path = name.strip_suffix(".ttf").and_then(|stem| {
        env.builtin_font_path(&format!("{}{stem}", lp_theme::tables::BUILTIN_PREFIX))
    });
    match path.and_then(|path| std::fs::read(path).ok()) {
        Some(body) => exchange.send(200, &body, "font/ttf", ""),
        None => not_found(exchange),
    }
}

/// The asset files' kinds: pictures, fonts and videos.
fn asset_type(suffix: &str) -> Option<&'static str> {
    Some(match suffix {
        ".png" => "image/png",
        ".jpg" | ".jpeg" => "image/jpeg",
        ".gif" => "image/gif",
        ".webp" => "image/webp",
        ".ttf" => "font/ttf",
        ".otf" => "font/otf",
        ".mp4" | ".m4v" => "video/mp4",
        ".mov" => "video/quicktime",
        ".webm" => "video/webm",
        ".mkv" => "video/x-matroska",
        _ => return None,
    })
}

/// _theme_list: the installed themes with their names and panels, for lists and galleries:
/// read from each theme.json, without loading the theme.
fn theme_list(state: &State) -> Value {
    let mut themes = lp_theme::versions::list_themes(&state.home.places());
    for item in &mut themes {
        let folder = PathBuf::from(item.get("path").map(Value::str).unwrap_or_default());
        let text = std::fs::read(folder.join("theme.json")).ok();
        let text = text.and_then(|bytes| String::from_utf8(bytes).ok());
        let data = match text.and_then(|text| lp_py::json::loads(&text).ok()) {
            Some(Value::Dict(data)) => data,
            _ => Dict::new(),
        };
        let display = match data.get("display") {
            Some(Value::Dict(display)) => display.clone(),
            _ => Dict::new(),
        };
        let text = |value: Option<&Value>| match value {
            Some(Value::Str(text)) => Some(text.clone()),
            _ => None,
        };
        let name = text(data.get("name")).filter(|name| !lp_py::strip(name).is_empty());
        let id = item.get("id").cloned().unwrap_or(Value::None);
        item.insert("name".into(), name.map_or(id, Value::Str));
        let model = text(display.get("model")).map_or(Value::None, Value::Str);
        item.insert("model".into(), model);
        let collection = text(data.get("collection")).unwrap_or_default();
        item.insert("collection".into(), Value::Str(collection));
        // a card slot that leaves its cards to the person: Start offers the cards while the
        // theme is on the panel
        let slot = |widget: &Value| match widget {
            Value::Dict(w) => {
                let named = |key: &str, name: &str| matches!(w.get(key), Some(Value::Str(text)) if text == name);
                let items = w.get("items").filter(|items| items.truthy());
                let items = items.map(Value::str).unwrap_or_default();
                named("type", "module")
                    && named("module", "cards")
                    && lp_py::strip(&items).is_empty()
            }
            _ => false,
        };
        let cards = match data.get("widgets") {
            Some(Value::List(widgets)) => widgets.iter().any(slot),
            _ => false,
        };
        item.insert("cards".into(), Value::Bool(cards));
        let whole = |key: &str| {
            let value = display.get(key);
            value
                .filter(|v| matches!(v, Value::Int(_) | Value::Bool(_)))
                .cloned()
        };
        let size = match (whole("width"), whole("height")) {
            (Some(width), Some(height)) => Value::List(vec![width, height]),
            _ => Value::None,
        };
        item.insert("size".into(), size);
    }
    Value::List(themes.into_iter().map(Value::Dict).collect())
}

/// The files under `folder`, each as its path from there.
fn files_under(folder: &Path, inside: &str, found: &mut Vec<String>) {
    let Ok(entries) = std::fs::read_dir(folder) else {
        return;
    };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        let rel = match inside.is_empty() {
            true => name,
            false => format!("{inside}/{name}"),
        };
        let path = entry.path();
        if entry.file_type().is_ok_and(|kind| kind.is_dir()) {
            files_under(&path, &rel, found);
        } else if path.is_file() {
            found.push(rel);
        }
    }
}

/// _assets: the pictures, fonts and videos in a theme's folder.
fn assets(state: &State, exchange: &mut Exchange, theme_id: &str) {
    let folder = match lp_theme::versions::find_theme(theme_id, &state.home.places()) {
        Ok(folder) => folder,
        Err(why) => return exchange.error(&why.msg, 404, ""),
    };
    let mut files = Vec::new();
    files_under(&folder, "", &mut files);
    files.retain(|file| asset_type(&suffix(file).to_lowercase()).is_some());
    files.sort_by(|a, b| a.chars().cmp(b.chars()));
    let files = files.into_iter().map(Value::Str).collect();
    exchange.json(&Value::List(files), 200);
}

/// _asset_file: an image or font of a theme, for the library of pictures and their
/// previews.
fn asset_file(state: &State, exchange: &mut Exchange, theme_id: &str, rel: &str) {
    let rel = Value::Str(crate::http::unquote(rel));
    let file = lp_theme::versions::find_theme(theme_id, &state.home.places())
        .and_then(|folder| lp_theme::model::resolve_asset(&folder, &rel));
    let found = file.ok().and_then(|file| {
        let name = file.file_name()?.to_string_lossy().into_owned();
        let kind = asset_type(&suffix(&name).to_lowercase())?;
        let body = file.is_file().then(|| std::fs::read(&file).ok())??;
        Some((body, kind))
    });
    match found {
        Some((body, kind)) => exchange.send(200, &body, kind, ""),
        None => not_found(exchange),
    }
}

/// _get_theme: a theme as its file has it, with what is to be said of it.
fn get_theme(state: &State, exchange: &mut Exchange, theme_id: &str) {
    let places = state.home.places();
    let env = lp_theme::Env {
        fonts_dir: state.home.fonts(),
    };
    let loaded = lp_theme::versions::find_theme(theme_id, &places)
        .and_then(|folder| Ok((lp_theme::model::load_theme(&folder, &env)?, folder)));
    let (theme, folder) = match loaded {
        Ok(loaded) => loaded,
        Err(why) => return exchange.error(&why.msg, 404, ""),
    };
    let resolved = |path: &Path| std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let builtin = !resolved(&folder).starts_with(resolved(&places.user));
    let mut told = Dict::new();
    told.insert("id".into(), Value::Str(theme_id.to_string()));
    told.insert("builtin".into(), Value::Bool(builtin));
    told.insert("theme".into(), theme.to_dict());
    let warnings = theme.warnings.iter().cloned().map(Value::Str).collect();
    told.insert("warnings".into(), Value::List(warnings));
    exchange.json(&Value::Dict(told), 200);
}

/// Readings of sources that work on demand: a running panel has none of them until a theme
/// (or a service) shows one, so a sensor field offers them from here, whatever the live
/// readings say. (key, unit, label), as the sources give them.
const ON_DEMAND: [(&str, &str, &str); 44] = [
    ("audio.volume", "%", "Volume"),
    ("audio.muted", "", "Muted"),
    ("cpu.load", "%", "CPU load"),
    ("gpu.load", "%", "GPU load"),
    ("media.title", "", "Title"),
    ("media.artist", "", "Artist"),
    ("media.album", "", "Album"),
    ("media.source", "", "Player"),
    ("media.state", "", "Media"),
    ("media.playing", "", "Playing"),
    ("media.duration", "s", "Length"),
    ("media.position", "s", "Position"),
    ("media.progress", "", "Progress"),
    ("media.remaining", "s", "Remaining"),
    ("game.fps", "fps", "Frames per second"),
    ("game.frametime", "ms", "Frame time"),
    ("game.low", "fps", "1% low"),
    ("game.frametime_peak", "ms", "Frame time peak"),
    ("game.fps_avg", "fps", "Average FPS of the round"),
    ("game.fps_1low", "fps", "1% low of the round"),
    ("game.fps_01low", "fps", "0.1% low of the round"),
    (
        "game.frametime_avg",
        "ms",
        "Average frame time of the round",
    ),
    ("game.frametime_max", "ms", "Longest frame of the round"),
    ("game.limiter", "", "Limited by"),
    ("game.app", "", "Game"),
    ("game.name", "", "Game"),
    ("weather.rain.minutes", "min", "Rain starts or stops"),
    ("weather.rain.text", "", "Rain"),
    ("net.ping", "ms", "Ping"),
    ("net.ping.lost", "%", "Ping lost"),
    ("net.ping.host", "", "Ping host"),
    ("audio.mic.muted", "", "Microphone muted"),
    ("audio.mic.volume", "%", "Microphone level"),
    ("power.watts", "W", "Power"),
    ("power.today", "kWh", "Energy today"),
    ("power.month", "kWh", "Energy this month"),
    // (the currency of [services.power])
    ("power.today.cost", "€", "Cost today"),
    ("power.month.cost", "€", "Cost this month"),
    ("timer.left", "s", "Time left"),
    ("timer.text", "", "Time left"),
    ("timer.running", "", "Timer runs"),
    ("timer.share", "", "Time passed"),
    ("timer.phase", "", "Timer"),
    ("timer.rounds", "", "Rounds"),
];
/// Pictures a source or service has, for an image widget (`"src": "@media.cover"`). They are
/// no readings: they come marked `picture`, and a sensor field leaves them out.
const PICTURES: [(&str, &str); 3] = [
    ("media.cover", "Album cover"),
    ("media.app_icon", "Player icon"),
    ("game.logo", "Game logo"),
];

/// The local time of now, and its fold.
fn moment() -> (lp_render::Moment, bool) {
    let since = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH);
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    lp_py::zones::fromtimestamp(zone, since.map_or(0.0, |since| since.as_secs_f64()))
        .unwrap_or((lp_render::Moment::date(1970, 1, 1), false))
}

type Reply = std::sync::mpsc::Sender<Option<Snapshot>>;

/// The readings of a server without a background app: a hub of its own, on a thread of its
/// own (the sources stay on one thread), asked for a snapshot. None where config.toml
/// cannot be read.
fn own_snapshot(state: &State) -> Option<Snapshot> {
    let mut asks = state.live.lock().ok()?;
    if asks.is_none() {
        let (ask, asked) = std::sync::mpsc::channel::<Reply>();
        let file = state.config_file.clone();
        let thread = std::thread::Builder::new().name("editor-sensors".into());
        let made = thread.spawn(move || {
            let mut hub: Option<crate::sensors::Hub> = None;
            for reply in asked {
                if hub.is_none() {
                    let config = lp_config::load_config(&file, lp_config::platform()).ok();
                    hub = config.map(|config| crate::sensors::Hub::of(&config, false).0);
                    if let Some(hub) = &mut hub {
                        let (now, fold) = moment();
                        hub.snapshot(now, fold); // (rates need a previous sample)
                    }
                }
                let (now, fold) = moment();
                let _ = reply.send(hub.as_mut().map(|hub| hub.snapshot(now, fold)));
            }
        });
        made.ok()?;
        *asks = Some(ask);
    }
    let (reply, answer) = std::sync::mpsc::channel();
    asks.as_ref()?.send(reply).ok()?;
    answer.recv().ok()?
}

/// live_snapshot: the readings for previews. In the background app they are the panel's own:
/// sources that drive hardware must not run twice in one process.
fn live_snapshot(state: &State) -> Option<Snapshot> {
    match state.controls.upgrade() {
        Some(app) => Some(
            app.latest()
                .map_or_else(Snapshot::default, |latest| (*latest).clone()),
        ),
        None => own_snapshot(state),
    }
}

/// The demo's snapshot of now.
fn demo_snapshot() -> Option<Snapshot> {
    let since = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH);
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    let t = since.map_or(0.0, |since| since.as_secs_f64());
    lp_services::demo::demo_snapshot(t, lp_services::demo::HISTORY_SAMPLES, zone).ok()
}

/// JSON has no NaN.
fn plain(value: &Value) -> Value {
    match value {
        Value::Float(number) if !number.is_finite() => Value::None,
        other => other.clone(),
    }
}

/// _readings: sensor values for the app's pickers (`?keys=a,b`; all without it) and the
/// recent history of some (`?history=a`): live where there are live ones, sample values
/// otherwise, each marked `sample`.
fn readings(state: &State, exchange: &mut Exchange, url: &Url) {
    let query = crate::http::parse_qs(&url.query);
    let Some(mut snapshot) = demo_snapshot() else {
        return exchange.error("no readings", 500, "");
    };
    let mut live = false;
    // the keys this computer gives; the rest are samples
    let mut measured = std::collections::HashSet::new();
    if let Some(current) = live_snapshot(state) {
        live = !current.readings.is_empty();
        measured = current.readings.keys().cloned().collect();
        let longer = current
            .history
            .into_iter()
            .filter(|(_, values)| values.len() > 1);
        snapshot.history.extend(longer);
        snapshot.readings.extend(current.readings);
    }
    let listed = |name: &str| -> Vec<String> {
        let joined = query
            .get(name)
            .map(|values| values.join(","))
            .unwrap_or_default();
        joined.split(',').map(str::to_string).collect()
    };
    let wanted: Vec<String> = listed("keys")
        .into_iter()
        .filter(|key| !key.is_empty())
        .collect();
    let keys = match wanted.is_empty() {
        true => {
            let mut all: Vec<String> = snapshot.readings.keys().cloned().collect();
            all.sort_by(|a, b| a.chars().cmp(b.chars()));
            all
        }
        false => wanted.clone(),
    };
    let entry = |value: Value, unit: &str, label: &str, sample: bool| {
        let mut told = Dict::new();
        told.insert("value".into(), value);
        told.insert("unit".into(), Value::Str(unit.to_string()));
        told.insert("label".into(), Value::Str(label.to_string()));
        // a sample value: this computer gives none for the key (a fan it does not have), so
        // the panel shows nothing there
        told.insert("sample".into(), Value::Bool(sample));
        Value::Dict(told)
    };
    let mut found = Dict::new();
    for key in keys {
        if let Some(reading) = snapshot
            .readings
            .get(&key)
            .filter(|_| !key.starts_with('_'))
        {
            let sample = !measured.contains(&key);
            let told = entry(plain(&reading.value), &reading.unit, &reading.label, sample);
            found.insert(key, told);
        }
    }
    // the clock's own (a seconds run): always live
    let second = lp_render::snapshot::TIME_SECOND;
    if wanted.is_empty() || wanted.iter().any(|key| key == second) {
        let told = entry(snapshot.value(second), "s", "Seconds of the minute", false);
        found.insert(second.to_string(), told);
    }
    let mut history = Dict::new();
    for key in listed("history") {
        let values = snapshot
            .history
            .get(&key)
            .filter(|values| !key.is_empty() && !values.is_empty());
        if let Some(values) = values {
            let last = &values[values.len().saturating_sub(60)..];
            let rounded = last
                .iter()
                .map(|v| plain(&Value::Float(lp_py::round_digits(*v, 3))));
            history.insert(key, Value::List(rounded.collect()));
        }
    }
    let mut told = Dict::new();
    told.insert("live".into(), Value::Bool(live));
    told.insert("readings".into(), Value::Dict(found));
    told.insert("history".into(), Value::Dict(history));
    exchange.json(&Value::Dict(told), 200);
}

/// _sensors: every key a theme can show: those that work on demand, the demo's and the
/// panel's own (a source's unit and label come last, so they win), and the pictures.
fn sensors(state: &State, exchange: &mut Exchange) {
    let entry = |key: &str, unit: &str, label: &str| {
        let mut told = Dict::new();
        told.insert("key".into(), Value::Str(key.to_string()));
        told.insert("unit".into(), Value::Str(unit.to_string()));
        told.insert("label".into(), Value::Str(label.to_string()));
        told
    };
    let mut found: indexmap::IndexMap<String, Dict> = ON_DEMAND
        .iter()
        .map(|(key, unit, label)| ((*key).to_string(), entry(key, unit, label)))
        .collect();
    let mut readings = demo_snapshot()
        .map(|snapshot| snapshot.readings)
        .unwrap_or_default();
    // (live sensors are optional for the app)
    if let Some(current) = live_snapshot(state) {
        readings.extend(current.readings);
    }
    for (key, reading) in &readings {
        let mut told = entry(key, &reading.unit, &reading.label);
        // (the app lists a source's raw readings under it)
        if !reading.device.is_empty() {
            told.insert("device".into(), Value::Str(reading.device.clone()));
        }
        found.insert(key.clone(), told);
    }
    for (key, label) in PICTURES {
        let mut told = entry(key, "", label);
        told.insert("picture".into(), Value::Bool(true));
        found.insert(key.to_string(), told);
    }
    let mut keys: Vec<&String> = found.keys().collect();
    keys.sort_by(|a, b| a.chars().cmp(b.chars()));
    let listed = keys.into_iter().map(|key| Value::Dict(found[key].clone()));
    exchange.json(&Value::List(listed.collect()), 200);
}

/// _i18n: the app's language, config.toml's setting, the languages there are and the
/// tables of the language the pages read.
fn i18n(state: &State) -> Dict {
    let setting = lp_config::load_config(&state.config_file, lp_config::platform())
        .map_or_else(|_| "auto".to_string(), |config| config.language);
    let language = lp_i18n::language();
    let mut told = Dict::new();
    told.insert("language".into(), Value::Str(language.code().to_string()));
    told.insert("setting".into(), Value::Str(setting));
    let languages = lp_i18n::LANGUAGES.iter();
    let languages: Dict = languages
        .map(|(code, name)| ((*code).to_string(), Value::Str((*name).to_string())))
        .collect();
    told.insert("languages".into(), Value::Dict(languages));
    for (name, table) in language.catalog() {
        if EDITOR_TABLES.contains(&name.as_str()) {
            told.insert(name.clone(), table.clone());
        }
    }
    told
}

fn available(snapshot: Dict) -> Value {
    let mut told = Dict::new();
    told.insert("available".into(), Value::Bool(true));
    told.extend(snapshot);
    Value::Dict(told)
}

/// `GET /api/app`: the background app's state, if this server belongs to one.
fn app_state(state: &State, exchange: &mut Exchange) {
    match state.controls.upgrade() {
        Some(app) => exchange.json(&available(app.snapshot()), 200),
        None => {
            let mut told = Dict::new();
            told.insert("available".into(), Value::Bool(false));
            exchange.json(&Value::Dict(told), 200);
        }
    }
}

/// `POST /api/app`: `{"action": ..., "value": ...}`, one of the background app's controls;
/// its new state.
fn app_action(state: &State, exchange: &mut Exchange) {
    let Some(payload) = exchange.read_json(MAX_JSON) else {
        return;
    };
    let Some(app) = state.controls.upgrade() else {
        return exchange.error("this editor does not run the panel", 404, "");
    };
    let action = match &payload {
        Value::Dict(payload) => match payload.get("action") {
            Some(Value::Str(action)) => Some((action.clone(), payload.get("value").cloned())),
            _ => None,
        },
        _ => None,
    };
    let Some((action, value)) = action else {
        return exchange.error("expected {\"action\": ...}", 400, "");
    };
    match app.act(&action, &value.unwrap_or(Value::None)) {
        Ok(snapshot) => exchange.json(&available(snapshot), 200),
        Err(why) => exchange.error(&why, 400, ""),
    }
}

/// _serve: answer a request: only to a name of this server and only with the token, but for
/// the app's own files. The token is asked for first, also for a path that is no route: only
/// those who may use the server learn what is there.
fn serve(state: &State, token: &str, hosts: &[String; 2], exchange: &mut Exchange) {
    // only the names this server listens under are answered (a web page that reaches it
    // through a name of its own gets nothing), and a browser's request only from a page of
    // the app itself
    let host = exchange.header("Host").unwrap_or("").to_string();
    if !hosts.contains(&host) {
        return exchange.error("forbidden host", 403, "");
    }
    if let Some(origin) = exchange.header("Origin") {
        if !hosts.iter().any(|host| origin == format!("http://{host}")) {
            return exchange.error("forbidden origin", 403, "");
        }
    }
    let url = urlparse(&exchange.target);
    let found = ROUTES.iter().find_map(|route| {
        (route.method == exchange.method).then_some(())?;
        Some((route, matched(&parts_of(route.template), &url.path)?))
    });
    let open = found.as_ref().is_some_and(|(route, _)| route.public);
    if !open {
        let mut given = exchange.header(access::HEADER).map(str::to_string);
        if given.is_none() && found.as_ref().is_some_and(|(route, _)| route.picture) {
            let query = crate::http::parse_qs(&url.query);
            given = Some(crate::http::first(&query, access::NAME).to_string());
        }
        if !access::matches(given.as_deref(), token) {
            let said = t(
                "Access token missing or wrong. Open Libre Panel from its window or its \
                          tray icon, or with the address that libre-panel editor prints.",
            );
            return exchange.error(&said, 403, "token");
        }
    }
    let Some((route, names)) = found else {
        return not_found(exchange);
    };
    if !open {
        // a page of this run of the app asks
        if let Ok(mut last) = state.last_request.lock() {
            *last = Some(state.began.elapsed().as_secs_f64());
        }
    }
    (route.handle)(state, exchange, &names, &url);
    exchange.discard_body(); // (a route that answers without asking for its body)
}

fn handle(stream: TcpStream, state: &State, token: &str, hosts: &[String; 2]) {
    let _ = stream.set_nonblocking(false);
    let software = format!("LibrePanelEditor/{}", env!("CARGO_PKG_VERSION"));
    let Some(mut exchange) = Exchange::read(stream, &software) else {
        return;
    };
    match exchange.method.as_str() {
        "GET" | "POST" => serve(state, token, hosts, &mut exchange),
        other => {
            let said = format!("Unsupported method ({})", lp_py::repr(other));
            exchange.send_error(501, Some(&said), None);
        }
    }
}

/// The app's server: made (its port taken), then serving on a thread of its own until it
/// is shut down.
pub struct EditorServer {
    pub port: u16,
    /// what a request must carry (access.rs); made with the server
    pub token: String,
    pub state: Arc<State>,
    listener: Option<TcpListener>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl EditorServer {
    /// make_server: the server on `port` (0: any free one), not yet serving. Its token is
    /// new, unless given.
    pub fn make(
        port: u16,
        home: Home,
        config_file: PathBuf,
        controls: Weak<BackgroundApp>,
        token: Option<String>,
    ) -> std::io::Result<EditorServer> {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, port))?;
        let token = match token {
            Some(token) => token,
            None => access::new_token()?,
        };
        Ok(EditorServer {
            port: listener.local_addr()?.port(),
            token,
            state: Arc::new(State {
                home,
                config_file,
                controls,
                began: Instant::now(),
                last_request: Mutex::new(None),
                live: Mutex::new(None),
                restart: crate::settings::Restart::default(),
            }),
            listener: Some(listener),
            stop: Arc::default(),
            thread: None,
        })
    }

    /// The server on `port`, or on any free one where that is taken.
    pub fn make_near(
        port: u16,
        home: &Home,
        config_file: &Path,
        controls: &Weak<BackgroundApp>,
    ) -> std::io::Result<EditorServer> {
        let make = |port| {
            EditorServer::make(
                port,
                home.clone(),
                config_file.to_path_buf(),
                controls.clone(),
                None,
            )
        };
        make(port).or_else(|_| make(0))
    }

    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}/", self.port)
    }

    /// `url` with the token: the way into the app for the person who started Libre Panel (a
    /// terminal may show it; a log never does).
    pub fn address(&self) -> String {
        access::with_token(&self.url(), Some(&self.token))
    }

    /// serve_forever, on a thread of the server's own.
    pub fn serve(&mut self) -> std::io::Result<()> {
        let Some(listener) = self.listener.take() else {
            return Ok(());
        };
        listener.set_nonblocking(true)?;
        let (state, token, stop) = (
            Arc::clone(&self.state),
            self.token.clone(),
            Arc::clone(&self.stop),
        );
        let hosts = [
            format!("127.0.0.1:{}", self.port),
            format!("localhost:{}", self.port),
        ];
        let thread = std::thread::Builder::new().name("editor".into());
        self.thread = Some(thread.spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                match listener.accept() {
                    Ok((stream, _)) => {
                        let (state, token, hosts) =
                            (Arc::clone(&state), token.clone(), hosts.clone());
                        let request = std::thread::Builder::new().name("editor-request".into());
                        let _ = request.spawn(move || handle(stream, &state, &token, &hosts));
                    }
                    Err(_) => std::thread::sleep(Duration::from_millis(15)),
                }
            }
        })?);
        Ok(())
    }

    pub fn shutdown(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// `libre-panel editor`: the server until `stop` is set; its address, with the token, on
/// the standard output.
pub fn serve_until(
    port: u16,
    home: &Home,
    config_file: &Path,
    stop: &AtomicBool,
) -> Result<(), String> {
    let mut server = EditorServer::make_near(port, home, config_file, &Weak::new())
        .map_err(|why| format!("the app's server did not start: {why}"))?;
    println!(
        "Libre Panel theme editor running at {}  (Ctrl+C to stop)",
        server.address()
    );
    (server.serve()).map_err(|why| format!("the app's server did not start: {why}"))?;
    while !stop.load(Ordering::Relaxed) {
        std::thread::sleep(Duration::from_millis(50));
    }
    server.shutdown();
    say("the app's server stopped");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_template_takes_what_its_pattern_takes() {
        let of = |template: &'static str, path: &str| {
            matched(&parts_of(template), path).map(|found| found.join("|"))
        };
        assert_eq!(of("/", "/").as_deref(), Some(""));
        assert_eq!(of("/", "/x"), None);
        assert_eq!(of("/app/{name*}", "/app/").as_deref(), Some(""));
        assert_eq!(of("/app/{name*}", "/app/a/b.js").as_deref(), Some("a/b.js"));
        assert_eq!(of("/app/{name*}", "/app"), None);
        assert_eq!(
            of(
                "/api/themes/{theme}/assets/{rel+}",
                "/api/themes/a/assets/b/c"
            )
            .as_deref(),
            Some("a|b/c")
        );
        assert_eq!(
            of("/api/themes/{theme}/assets/{rel+}", "/api/themes/a/assets/"),
            None
        );
        assert_eq!(
            of("/api/themes/{theme}/sizes", "/api/themes/a/b/sizes"),
            None
        );
        assert_eq!(
            of("/api/themes/{theme+}", "/api/themes/a/b").as_deref(),
            Some("a/b")
        );
        assert_eq!(
            of("/api/plugins/{page}/{rest*}", "/api/plugins/p/").as_deref(),
            Some("p|")
        );
        assert!(app_path("views/design/main.js") && app_path("a/b/c/d/e.js"));
        assert!(
            !app_path("a/b/c/d/e/f.js")
                && !app_path(".hidden")
                && !app_path("a//b")
                && !app_path("")
        );
        assert_eq!(
            (
                suffix("a/b.tar.GZ"),
                suffix(".x"),
                suffix("a."),
                suffix("a.b/c")
            ),
            (".GZ", "", "", "")
        );
    }
}
