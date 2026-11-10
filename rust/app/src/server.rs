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

use crate::access;
use crate::background::BackgroundApp;
use crate::home::Home;
use crate::http::{urlparse, Exchange, Url};

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

const ROUTES: [Route; 11] = [
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
    private("GET", "/api/i18n", |s, x, _, _| {
        x.json(&Value::Dict(i18n(s)), 200)
    }),
    private("GET", "/api/app", |s, x, _, _| app_state(s, x)),
    // -- writing -----------------------------------------------------------------------------------
    private("POST", "/api/app", |s, x, _, _| app_action(s, x)),
];

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
