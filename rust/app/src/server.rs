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
    crate::logs::say("libre_panel.editor.server", message);
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
    /// whom Start tells what is new (news.rs)
    news: crate::news::News,
    /// whether a newer version is out (updates.rs)
    updates: crate::updates::Updates,
    /// the pictures of the themes kept (previews.rs)
    previews: crate::previews::Previews,
    /// the port the server listens on (what the AI hooks call)
    port: u16,
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
    /// another program's notice: its own key stands for the token, checked by the route
    notice: bool,
}

const fn public(method: &'static str, template: &'static str, handle: Handle) -> Route {
    Route {
        method,
        template,
        handle,
        public: true,
        picture: false,
        notice: false,
    }
}

const fn private(method: &'static str, template: &'static str, handle: Handle) -> Route {
    Route {
        method,
        template,
        handle,
        public: false,
        picture: false,
        notice: false,
    }
}

const fn picture(method: &'static str, template: &'static str, handle: Handle) -> Route {
    Route {
        method,
        template,
        handle,
        public: false,
        picture: true,
        notice: false,
    }
}

const fn notice(method: &'static str, template: &'static str, handle: Handle) -> Route {
    Route {
        method,
        template,
        handle,
        public: false,
        picture: false,
        notice: true,
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
    public("GET", "/plugins/{page}/{name*}", |s, x, m, _| {
        let name = if m[1].is_empty() { "index.html" } else { m[1] };
        page_file(s, x, m[0], name);
    }),
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
    picture("GET", "/api/themes/{theme}/preview", |s, x, m, u| {
        theme_preview(s, x, m[0], u)
    }),
    private("GET", "/api/themes/{theme}/export", |s, x, m, _| {
        export_theme(s, x, m[0])
    }),
    private("GET", "/api/themes/{theme}/sizes", |s, x, m, _| {
        let env = lp_theme::Env {
            fonts_dir: s.home.fonts(),
        };
        match crate::previews::theme_sizes(m[0], &env, &s.home.places()) {
            Ok(told) => x.json(&told, 200),
            Err(crate::previews::Refused::Theme(_)) => not_found(x),
            Err(crate::previews::Refused::Drawing(why)) => unanswered(&why),
        }
    }),
    picture("GET", "/api/fan-look", |s, x, _, u| {
        let query = crate::http::parse_qs(&u.query);
        let first = |name: &str, default: &str| {
            query
                .get(name)
                .and_then(|v| v.first())
                .map_or(default.to_string(), String::clone)
        };
        let (look, paint) = (first("look", "noctua-g2"), first("paint", "theme"));
        picture_answer(
            x,
            crate::drawings::fan_look(&look, &paint, &s.home.fonts()),
            "unknown look or colours",
        )
    }),
    picture("GET", "/api/symbols-look", |s, x, _, u| {
        let query = crate::http::parse_qs(&u.query);
        let pack = query
            .get("pack")
            .and_then(|v| v.first())
            .map_or(lp_config::DEFAULT_SYMBOLS.to_string(), String::clone);
        let env = lp_theme::Env {
            fonts_dir: s.home.fonts(),
        };
        picture_answer(
            x,
            crate::drawings::symbols_look(&pack, &env),
            "unknown look",
        )
    }),
    private("GET", "/api/specs", |s, x, _, _| {
        let env = lp_theme::Env {
            fonts_dir: s.home.fonts(),
        };
        x.json(&crate::specs::specs(&env, &s.home.places()), 200)
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
    private("GET", "/api/news", |s, x, _, _| {
        x.json(&s.news.state(), 200)
    }),
    private("GET", "/api/update", |s, x, _, u| {
        let at_once = crate::http::parse_qs(&u.query).contains_key("now");
        x.json(&s.updates.state(check_updates(s), at_once), 200)
    }),
    private("GET", "/api/system", |s, x, _, _| {
        settings(s, x, |told| told.system())
    }),
    private("GET", "/api/report", |s, x, _, _| {
        settings(s, x, |told| told.report())
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
    // (the tray icon's program and the app: its menu, what is chosen in it, its picture;
    // rust/desktop, tray.rs. The Python app has its icon in its own process)
    private("GET", "/api/tray", |s, x, _, _| {
        match s.controls.upgrade() {
            Some(app) => x.json(&crate::tray::state(&app), 200),
            None => x.error("this editor does not run the panel", 404, ""),
        }
    }),
    private("GET", "/api/tray/icon", |s, x, _, u| {
        tray_icon(s, x, &u.query)
    }),
    private("GET", "/api/hardware", |s, x, _, u| {
        // (`?refresh=1`, and `?refresh`: ask the computer for its parts' names again)
        let query = crate::http::parse_qs_blank(&u.query);
        let refresh = query
            .get("refresh")
            .and_then(|asked| asked.first())
            .is_some_and(|asked| asked != "0" && asked != "false");
        hardware_answer(s, x, refresh);
    }),
    private("GET", "/api/history", |s, x, _, u| history(s, x, u)),
    private("GET", "/api/plugins/pages", |s, x, _, _| {
        x.json(&crate::consoles::listed(&s.home.data), 200)
    }),
    // (a page may answer with a picture)
    picture("GET", "/api/plugins/{page}/{rest*}", |s, x, m, u| {
        page_api(s, x, "GET", m[0], m[1], u, None);
    }),
    // a notice from another program: with its own key instead of the token
    notice("POST", "/api/ai/hook", |s, x, _, u| ai_in(s, x, u, "hook")),
    notice("POST", "/api/ai/status", |s, x, _, u| {
        ai_in(s, x, u, "status")
    }),
    notice("GET", "/api/notify", |s, x, _, u| {
        outside_notice(s, x, u, false)
    }),
    notice("POST", "/api/notify", |s, x, _, u| {
        outside_notice(s, x, u, true)
    }),
    private("GET", "/api/notify-key", |s, x, _, _| {
        notify_key(s, x, false)
    }),
    private("POST", "/api/notify-key", |s, x, _, _| {
        notify_key(s, x, true)
    }),
    private(
        "GET",
        "/api/ai/setup",
        |s, x, _, _| match lp_config::load_config(&s.config_file, lp_config::platform()) {
            Ok(config) => x.json(&crate::ai::setup_state(&config, s.port), 200),
            Err(why) => x.error(&why.msg, 409, ""),
        },
    ),
    private("POST", "/api/ai/setup", |s, x, _, _| {
        let config = match lp_config::load_config(&s.config_file, lp_config::platform()) {
            Ok(config) => config,
            Err(why) => return x.error(&why.msg, 409, ""),
        };
        let Some(payload) = x.read_json(MAX_JSON) else {
            return;
        };
        match crate::ai::set_up(&config, &payload, s.port) {
            Ok(told) => x.json(&told, 200),
            Err(crate::ai::Refused::Said(why, status)) => x.error(&why, status, ""),
            Err(crate::ai::Refused::Failed(why)) => unanswered(&why),
        }
    }),
    picture("GET", "/api/backup/file", |s, x, _, _| {
        // (the person's settings and own themes in one zip, to keep)
        let themes = lp_config::dirs::user_themes_dir();
        match crate::backup::make_backup(&s.config_file, &themes) {
            Ok(data) => {
                let at = crate::share::local_time(crate::share::now());
                let name = format!(
                    "libre-panel-backup-{:04}-{:02}-{:02}.zip",
                    at[0], at[1], at[2]
                );
                x.send(200, &data, "application/zip", &name);
            }
            Err(crate::backup::Refused::Os(why) | crate::backup::Refused::Backup(why)) => {
                x.error(&why, 409, "");
            }
            Err(crate::backup::Refused::Failed(why)) => unanswered(&why),
        }
    }),
    private("GET", "/api/logs", |_, x, _, u| {
        let query = crate::http::parse_qs(&u.query);
        let level = query.get("level").and_then(|asked| asked.first());
        x.json(
            &crate::logs::state(level.map_or("DEBUG", String::as_str)),
            200,
        )
    }),
    picture("GET", "/api/logs/file", |_, x, _, _| {
        // (the whole log to keep, without the person's folders)
        let body = crate::logs::text(&lp_config::dirs::log_file(), None);
        x.send(
            200,
            body.as_bytes(),
            "text/plain; charset=utf-8",
            "libre-panel-log.txt",
        );
    }),
    // -- writing -----------------------------------------------------------------------------------
    private("POST", "/api/app", |s, x, _, _| app_action(s, x)),
    private("POST", "/api/tray", |s, x, _, _| tray_action(s, x)),
    private("POST", "/api/settings", |s, x, _, _| {
        settings_with(s, x, |told, asked| told.write(asked))
    }),
    private("POST", "/api/pages", |s, x, _, _| {
        settings_with(s, x, |told, asked| told.set_page(asked))
    }),
    private("POST", "/api/sequence", |s, x, _, _| {
        settings_with(s, x, |told, asked| told.set_sequence(asked))
    }),
    private("POST", "/api/news", |s, x, _, _| see_news(s, x)),
    private("POST", "/api/logs", |_, x, _, _| set_verbose(x)),
    private("POST", "/api/plugins/{page}/{rest*}", |s, x, m, u| {
        // (a page gets what it was sent, a JSON object, and the answer is its own)
        let Some(body) = x.read_json(MAX_JSON) else {
            return;
        };
        page_api(s, x, "POST", m[0], m[1], u, Some(&body));
    }),
    private("POST", "/api/backup", |s, x, _, _| restore_backup(s, x)),
    private("POST", "/api/hardware", |s, x, _, _| {
        let Some(payload) = x.read_json(MAX_JSON) else {
            return;
        };
        match crate::hardware::set_hardware(&payload, &s.config_file) {
            Ok(()) => hardware_answer(s, x, false),
            Err(why) => x.error(&why, 400, ""),
        }
    }),
    private("POST", "/api/language", |s, x, _, _| set_language(s, x)),
    private("POST", "/api/update", |s, x, _, _| update_later(s, x)),
    private("POST", "/api/system", |s, x, _, _| {
        settings_with(s, x, |told, asked| told.set_system(asked))
    }),
    private("POST", "/api/location", |s, x, _, _| {
        settings_with(s, x, |told, asked| told.search_place(asked))
    }),
    private("POST", "/api/open", |s, x, _, _| {
        settings_with(s, x, |told, asked| told.open_folder(asked))
    }),
    private("POST", "/api/scenes", |s, x, _, _| {
        settings_with(s, x, |told, asked| told.set_scene(asked))
    }),
    private("POST", "/api/themes/import", |s, x, _, u| {
        // (a theme named "import" saved: its body is JSON, an import's a zip)
        let json = x
            .header("Content-Type")
            .unwrap_or("")
            .starts_with("application/json");
        match json {
            true => save_theme(s, x, "import"),
            false => import_theme(s, x, u),
        }
    }),
    private("POST", "/api/render", |s, x, _, _| render(s, x)),
    private("POST", "/api/preset-previews", |s, x, _, _| {
        drawing(s, x, |payload, _, env, config| {
            let palette = match payload {
                Value::Dict(d) => d.get("palette").cloned().unwrap_or(Value::None),
                _ => Value::None,
            };
            let previews = crate::specs::preset_previews(&palette, &config.symbols, env)?;
            let mut told = Dict::new();
            told.insert("previews".into(), previews);
            Ok(Value::Dict(told))
        })
    }),
    private("POST", "/api/module-previews", |s, x, _, _| {
        drawing(s, x, |payload, _, env, config| {
            let get = |key: &str| match payload {
                Value::Dict(d) => Ok(d.get(key).cloned().unwrap_or(Value::None)),
                other => Err(crate::drawings::Refused::Failed(format!(
                    "AttributeError: '{}' object has no attribute 'get'",
                    other.type_name()
                ))),
            };
            let previews = crate::drawings::module_previews(
                &get("palette")?,
                &get("style")?,
                &config.symbols,
                env,
            )?;
            let mut told = Dict::new();
            told.insert("previews".into(), previews);
            Ok(Value::Dict(told))
        })
    }),
    private("POST", "/api/templates", |s, x, _, _| {
        drawing(s, x, |payload, _, _, _| crate::drawings::templates(payload))
    }),
    private("POST", "/api/detach", |s, x, _, _| {
        drawing(s, x, |payload, state, env, _| {
            let base = match payload {
                Value::Dict(d) => d.get("base").cloned(),
                _ => None,
            };
            let root = base_folder(state, base.as_ref());
            crate::drawings::detach(payload, root.as_deref(), env)
        })
    }),
    private("POST", "/api/adapt", |s, x, _, _| {
        drawing(s, x, |payload, _, env, _| {
            crate::drawings::adapt(payload, env)
        })
    }),
    private("POST", "/api/themes/{theme}/version", |s, x, m, _| {
        edit(s, x, |payload, places, env| {
            crate::editing::make_version(m[0], payload, places, env)
        })
    }),
    private("POST", "/api/themes/{theme*}", |s, x, m, _| {
        save_theme(s, x, m[0])
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

/// config.toml's check_updates; None where it cannot be read (nothing is asked).
fn check_updates(state: &State) -> Option<bool> {
    let config = lp_config::load_config(&state.config_file, lp_config::platform()).ok()?;
    config.check_updates
}

/// _update_later: `{"later": "0.7.0"}`: the card of that version was closed (updates.rs).
fn update_later(state: &State, exchange: &mut Exchange) {
    let Some(payload) = exchange.read_json(MAX_JSON) else {
        return;
    };
    let later = match &payload {
        Value::Dict(payload) => payload.get("later").and_then(Value::as_str).unwrap_or(""),
        _ => "",
    };
    match state.updates.later(later, check_updates(state)) {
        Ok(told) => exchange.json(&told, 200),
        Err(why) => exchange.error(&why, 400, ""),
    }
}

/// _see_news: `{"seen": "0.6"}`: the card of a release's news was closed (news.rs).
fn see_news(state: &State, exchange: &mut Exchange) {
    let Some(payload) = exchange.read_json(MAX_JSON) else {
        return;
    };
    let seen = match &payload {
        Value::Dict(payload) => payload.get("seen").and_then(Value::as_str).unwrap_or(""),
        _ => "",
    };
    match state.news.see(seen) {
        Ok(told) => exchange.json(&told, 200),
        Err(why) => exchange.error(&why, 400, ""),
    }
}

/// `/api/notify`: a notice from another program, only where `allow_notify` is on and with the
/// notify key (the header, or `?key=`); `text`, `title`, `seconds`, `icon` and `level` in the
/// address or, posted, as JSON.
fn outside_notice(state: &State, exchange: &mut Exchange, url: &Url, posted: bool) {
    let config = match lp_config::load_config(&state.config_file, lp_config::platform()) {
        Ok(config) => config,
        Err(why) => return exchange.error(&why.msg, 409, ""),
    };
    if !config.allow_notify {
        let why =
            t("Notices from other programs are switched off (Settings → Game mode and more).");
        return exchange.error(&why, 403, "");
    }
    let mut params: Dict = crate::http::parse_qs(&url.query)
        .into_iter()
        .filter_map(|(name, values)| Some((name, Value::Str(values.into_iter().next()?))))
        .collect();
    let given = match exchange.header(crate::notify::HEADER) {
        Some(header) if !header.is_empty() => header.to_string(),
        _ => params.get("key").map(Value::str).unwrap_or_default(),
    };
    params.shift_remove("key");
    if !access::matches(Some(&given), &crate::notify::read_key()) {
        return exchange.error(&t("The notify key is missing or wrong."), 403, "");
    }
    if posted && exchange.header("Content-Length").unwrap_or("0") != "0" {
        let Some(body) = exchange.read_json(MAX_JSON) else {
            return;
        };
        let Value::Dict(body) = body else {
            return exchange.error("expected {\"text\": \"...\"}", 400, "");
        };
        params.extend(body);
    }
    let toast = match crate::notify::notice(&params) {
        Ok(toast) => toast,
        Err(why) => return exchange.error(&why, 400, ""),
    };
    let delivery = match state.controls.upgrade() {
        Some(app) => app.notify(toast),
        None => lp_services::host::Delivery {
            shown: false,
            reason: lp_services::host::NO_PANEL.into(),
        },
    };
    let mut told = Dict::new();
    told.insert("shown".into(), Value::Bool(delivery.shown));
    told.insert("reason".into(), Value::Str(delivery.reason));
    exchange.json(&Value::Dict(told), 200);
}

/// `/api/notify-key`: for Settings, the notify key (`POST`: a new one), the address that shows
/// a notice and the command that does.
fn notify_key(state: &State, exchange: &mut Exchange, renew: bool) {
    let config = match lp_config::load_config(&state.config_file, lp_config::platform()) {
        Ok(config) => config,
        Err(why) => return exchange.error(&why.msg, 409, ""),
    };
    match crate::notify::key_state(&config, state.port, renew) {
        Ok(told) => exchange.json(&told, 200),
        Err(why) => unanswered(&why),
    }
}

/// `POST /api/ai/hook` and `/api/ai/status`: a hook event of Claude Code or Codex, or Claude
/// Code's status line, only while AI activity is on and with the AI key (`?key=` or the
/// header). The status line is answered with the line it shows.
fn ai_in(state: &State, exchange: &mut Exchange, url: &Url, kind: &str) {
    let query = crate::http::parse_qs(&url.query);
    let config = match lp_config::load_config(&state.config_file, lp_config::platform()) {
        Ok(config) => config,
        Err(why) => return exchange.error(&why.msg, 409, ""),
    };
    let given = match exchange.header(lp_services::agents_setup::HEADER) {
        Some(header) if !header.is_empty() => header.to_string(),
        _ => crate::http::first(&query, "key").to_string(),
    };
    let agent = crate::http::first(&query, "agent");
    let agent = match crate::ai::admitted(&config, Some(&given), agent) {
        Ok(agent) => agent,
        Err(crate::ai::Refused::Said(why, status)) => return exchange.error(&why, status, ""),
        Err(crate::ai::Refused::Failed(why)) => return unanswered(&why),
    };
    let max = lp_services::agents_setup::MAX_BODY as u64;
    let Some(body) = exchange.read_body(max, "request too large") else {
        return;
    };
    let data = match crate::ai::event(&body) {
        Ok(data) => data,
        Err(crate::ai::Refused::Said(why, status)) => return exchange.error(&why, status, ""),
        Err(crate::ai::Refused::Failed(why)) => return unanswered(&why),
    };
    let board = lp_services::agents::board();
    if kind == "hook" {
        board.hook(&agent, &data);
        return exchange.send(204, b"", "text/plain", "");
    }
    let line = board.status(&agent, &data);
    exchange.send(200, line.as_bytes(), "text/plain; charset=utf-8", "");
}

/// A file of a built-in page (its index.html, scripts and styles).
fn page_file(state: &State, exchange: &mut Exchange, page: &str, name: &str) {
    let known = crate::consoles::PAGES.iter().any(|p| p.id == page);
    let kind = match suffix(name).to_ascii_lowercase().as_str() {
        ".json" => Some("application/json"),
        ".jpg" => Some("image/jpeg"),
        ".webp" => Some("image/webp"),
        ".woff2" => Some("font/woff2"),
        ".ttf" => Some("font/ttf"),
        other => content_type(other),
    };
    let base = crate::consoles::folder(&state.home.data);
    match kind
        .filter(|_| known)
        .and_then(|kind| Some((file_in(&base, name)?, kind)))
    {
        Some((body, kind)) => exchange.send(200, &body, kind, ""),
        None => not_found(exchange),
    }
}

/// A request to a built-in page's own API.
fn page_api(
    state: &State,
    exchange: &mut Exchange,
    method: &str,
    page: &str,
    path: &str,
    url: &Url,
    body: Option<&Value>,
) {
    let query = crate::http::parse_qs(&url.query);
    let context = crate::consoles::Context {
        app: state.controls.upgrade(),
        config_file: &state.config_file,
        places: state.home.places(),
    };
    match crate::consoles::handle(page, method, path, &query, body, &context) {
        Some(crate::consoles::Answer::Json(status, data)) => exchange.json(&data, status),
        Some(crate::consoles::Answer::Bytes(data, kind)) => exchange.send(200, &data, kind, ""),
        None => not_found(exchange),
    }
}

/// `POST /api/backup`: a backup (the zip's bytes) put in place: its config.toml and its
/// themes. Refused while Libre Panel runs with administrator rights: config.toml may name
/// programs it would start with them.
fn restore_backup(state: &State, exchange: &mut Exchange) {
    if crate::access::elevated() {
        let why = lp_i18n::t(
            "While Libre Panel runs with administrator rights, a backup is not restored here: \
             its config.toml may name programs that would be started with those rights.",
        );
        return exchange.error(&why, 403, "");
    }
    let Some(data) = exchange.read_body(crate::backup::MAX_TOTAL, "request too large") else {
        return;
    };
    let themes = lp_config::dirs::user_themes_dir();
    match crate::backup::restore_backup(&data, &state.config_file, &themes) {
        Ok(done) => {
            if let Some(app) = state.controls.upgrade() {
                app.follow_config(); // (the services take the restored settings)
            }
            exchange.json(&done, 200);
        }
        Err(crate::backup::Refused::Backup(why)) => exchange.error(&why, 400, ""),
        Err(crate::backup::Refused::Os(why)) => exchange.error(&why, 409, ""),
        Err(crate::backup::Refused::Failed(why)) => unanswered(&why),
    }
}

/// `GET /api/history?span=day` (week, month): what the memory kept, for History, whether the
/// memory service is switched on, and the span's energy and its cost where the power service
/// prices it.
fn history(state: &State, exchange: &mut Exchange, url: &Url) {
    let query = crate::http::parse_qs(&url.query);
    let span = crate::http::first(&query, "span");
    let span = if span.is_empty() { "day" } else { span };
    let config = match lp_config::load_config(&state.config_file, lp_config::platform()) {
        Ok(config) => config,
        Err(why) => return exchange.error(&why.msg, 409, ""),
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64());
    let path = crate::history::database(&config);
    let mut found = match crate::history::history(&path, span, now) {
        Ok(found) => found,
        Err(crate::history::Refused::Span(why)) => return exchange.error(why, 400, ""),
        Err(crate::history::Refused::Failed(why)) => return unanswered(&why),
    };
    let enabled = |name: &str| config.services.enabled.iter().any(|on| on == name);
    let power = match enabled("power") {
        true => {
            let summary = match found.get("summary") {
                Some(Value::Dict(d)) => d.clone(),
                _ => Dict::new(),
            };
            let none = lp_py::toml::Table::new();
            let options = config.services.options.get("power").unwrap_or(&none);
            crate::history::energy(&summary, options)
        }
        false => Value::None,
    };
    found.insert("memory".into(), Value::Bool(enabled("memory")));
    found.insert("power".into(), power);
    exchange.json(&Value::Dict(found), 200);
}

/// The Settings' hardware: the parts' names, the fans this computer reads now, their looks.
fn hardware_answer(state: &State, exchange: &mut Exchange, refresh: bool) {
    let readings = live_snapshot(state).map(|snapshot| snapshot.readings);
    let readings = readings.unwrap_or_default();
    match crate::hardware::hardware(&state.config_file, &readings, refresh) {
        Ok(told) => exchange.json(&told, 200),
        Err(why) => exchange.error(&why, 400, ""),
    }
}

/// `POST /api/logs {"verbose": true}`: every detail into the log until the app starts again,
/// or only what matters.
fn set_verbose(exchange: &mut Exchange) {
    let Some(payload) = exchange.read_json(MAX_JSON) else {
        return;
    };
    let on = match &payload {
        Value::Dict(d) => d.get("verbose"),
        _ => None,
    };
    let Some(Value::Bool(on)) = on else {
        return exchange.error("expected {\"verbose\": true or false}", 400, "");
    };
    crate::logs::set_verbose(*on);
    let mut told = Dict::new();
    told.insert("verbose".into(), Value::Bool(crate::logs::verbose()));
    exchange.json(&Value::Dict(told), 200);
}

/// `POST /api/language {"language": "de"}` (or "auto"): written into config.toml, and the
/// app speaks it at once.
fn set_language(state: &State, exchange: &mut Exchange) {
    let Some(payload) = exchange.read_json(MAX_JSON) else {
        return;
    };
    let setting = match &payload {
        Value::Dict(d) => match d.get("language") {
            Some(Value::Str(s)) => Some(s.clone()),
            _ => None,
        },
        _ => None,
    };
    let known = |code: &str| code == "auto" || lp_i18n::LANGUAGES.iter().any(|(c, _)| *c == code);
    let Some(setting) = setting.filter(|code| known(code)) else {
        let codes: Vec<&str> = lp_i18n::LANGUAGES.iter().map(|(c, _)| *c).collect();
        let why = format!("language must be auto or one of {}", codes.join(", "));
        return exchange.error(&why, 400, "");
    };
    let written = lp_config::edit::set_config_value(
        "language",
        lp_py::toml::Toml::Str(setting.clone()),
        &state.config_file,
        lp_config::platform(),
    );
    if let Err(why) = written {
        return exchange.error(&why.msg, 400, "");
    }
    // (the panel and the pages follow at once)
    let env = |name: &str| std::env::var(name).unwrap_or_default();
    let code = lp_i18n::resolve(Some(&setting), || lp_i18n::system_language(&env, None));
    match lp_i18n::Language::load(&state.home.locale(), &code) {
        Ok(language) => lp_i18n::set_language(language),
        Err(why) => say(&format!("language {code}: {why}")),
    }
    exchange.json(&Value::Dict(i18n(state)), 200);
}

fn not_found(exchange: &mut Exchange) {
    exchange.error("not found", 404, "");
}

/// Path(name).suffix
pub(crate) fn suffix(name: &str) -> &str {
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

/// A writing route of the editor's (editing.rs) with the request's JSON.
fn edit(
    state: &State,
    exchange: &mut Exchange,
    route: impl FnOnce(
        &Value,
        &lp_theme::versions::Places,
        &lp_theme::Env,
    ) -> Result<Value, crate::editing::Refused>,
) {
    let Some(payload) = exchange.read_json(MAX_JSON) else {
        return;
    };
    let env = lp_theme::Env {
        fonts_dir: state.home.fonts(),
    };
    match route(&payload, &state.home.places(), &env) {
        Ok(told) => exchange.json(&told, 200),
        Err(why) if why.status == 500 => unanswered(&why.message),
        Err(why) => exchange.error(&why.message, why.status, ""),
    }
}

/// handle_error: what Python's server does not catch: logged, the request left unanswered
/// (its connection closed with nothing).
fn unanswered(why: &str) {
    say(&format!("editor: request failed: {why}"));
}

/// _save: a theme saved into the person's themes folder.
fn save_theme(state: &State, exchange: &mut Exchange, theme_id: &str) {
    edit(state, exchange, |payload, places, env| {
        crate::editing::save(theme_id, payload, places, env)
    })
}

/// _base_folder: the folder of the theme a page names as the one whose pictures and fonts an
/// unsaved theme takes (None for a name that is none or a theme that is not there).
fn base_folder(state: &State, base: Option<&Value>) -> Option<PathBuf> {
    let Some(Value::Str(name)) = base else {
        return None;
    };
    if !lp_theme::model::valid_theme_name(name) {
        return None;
    }
    lp_theme::versions::find_theme(name, &state.home.places()).ok()
}

/// The config.toml's choices the editor's pictures show, as the panel does (its defaults where
/// it does not read).
fn chosen(state: &State) -> lp_config::Config {
    lp_config::load_config(&state.config_file, lp_config::platform())
        .unwrap_or_else(|_| lp_config::Config::new(None, lp_config::platform()))
}

/// A drawing route of the editor's (drawings.rs) with the request's JSON.
fn drawing(
    state: &State,
    exchange: &mut Exchange,
    route: impl FnOnce(
        &Value,
        &State,
        &lp_theme::Env,
        &lp_config::Config,
    ) -> Result<Value, crate::drawings::Refused>,
) {
    let Some(payload) = exchange.read_json(MAX_JSON) else {
        return;
    };
    let env = lp_theme::Env {
        fonts_dir: state.home.fonts(),
    };
    match route(&payload, state, &env, &chosen(state)) {
        Ok(told) => exchange.json(&told, 200),
        Err(crate::drawings::Refused::Said(why)) => exchange.error(&why, 400, ""),
        Err(crate::drawings::Refused::Failed(why)) => unanswered(&why),
    }
}

/// _render: Design's and Look's picture of the theme it is given (drawings.rs), live readings
/// over the demo's where it asks for them.
fn render(state: &State, exchange: &mut Exchange) {
    drawing(state, exchange, |payload, state, env, config| {
        let get = |key: &str| match payload {
            Value::Dict(d) => Ok(d.get(key).cloned().unwrap_or(Value::None)),
            other => Err(crate::drawings::Refused::Failed(format!(
                "AttributeError: '{}' object has no attribute 'get'",
                other.type_name()
            ))),
        };
        let root = base_folder(state, Some(&get("base")?));
        let live = match get("live")?.truthy() {
            true => live_snapshot(state).map(|current| {
                let history = current
                    .history
                    .into_iter()
                    .filter(|(_, values)| values.len() > 1)
                    .collect();
                (current.readings, history)
            }),
            false => None,
        };
        crate::drawings::render(
            &get("theme")?,
            root.as_deref(),
            config,
            live,
            &get("notice")?,
            &get("look")?,
            env,
        )
    })
}

/// A picture of the app's own (Settings' tiles): the PNG, "not found" for what there is not.
fn picture_answer(
    exchange: &mut Exchange,
    drawn: Result<Option<Vec<u8>>, crate::drawings::Refused>,
    unknown: &str,
) {
    match drawn {
        Ok(Some(png)) => exchange.send(200, &png, "image/png", ""),
        Ok(None) => exchange.error(unknown, 404, ""),
        Err(crate::drawings::Refused::Said(why)) => exchange.error(&why, 400, ""),
        Err(crate::drawings::Refused::Failed(why)) => unanswered(&why),
    }
}

/// What sharing a theme came to, as the server answers it.
fn refused(exchange: &mut Exchange, refused: crate::share::Refused) {
    use crate::share::Refused;
    match refused {
        Refused::Share(why) => exchange.error(&why, 400, ""),
        Refused::Theme(why) => exchange.error(&why, 404, ""),
        Refused::Os(why) => exchange.error(&why, 409, ""),
        Refused::Failed(why) => unanswered(&why),
    }
}

/// _export_theme: a theme as one .zip to give to someone (share.rs).
fn export_theme(state: &State, exchange: &mut Exchange, theme_id: &str) {
    let env = lp_theme::Env {
        fonts_dir: state.home.fonts(),
    };
    match crate::share::export_theme(theme_id, &state.home.places(), &env) {
        Ok((data, filename)) => exchange.send(200, &data, "application/zip", &filename),
        Err(why) => refused(exchange, why),
    }
}

/// _import_theme: `POST /api/themes/import` with a shared theme's .zip as the body (`?name=`
/// the file's name): put in the person's themes folder under a name of its own, answered
/// with `{id, name, files}`.
fn import_theme(state: &State, exchange: &mut Exchange, url: &Url) {
    let too_large = lp_i18n::t_format(
        "The file is larger than {size} MB: a shared theme may be at most that.",
        &[(
            "size",
            Value::int((crate::share::MAX_ZIP / (1024 * 1024)) as i64),
        )],
    )
    .unwrap_or_else(|why| why.msg);
    let Some(data) = exchange.read_body(crate::share::MAX_ZIP, &too_large) else {
        return;
    };
    let query = crate::http::parse_qs(&url.query);
    let name = query
        .get("name")
        .and_then(|v| v.first())
        .cloned()
        .unwrap_or_default();
    let env = lp_theme::Env {
        fonts_dir: state.home.fonts(),
    };
    let places = state.home.places();
    match crate::share::import_theme(&data, &places.user, &places, &env, &name, "") {
        Ok(done) => {
            let mut told = Dict::new();
            told.insert("id".into(), Value::Str(done.id));
            told.insert("name".into(), Value::Str(done.name));
            told.insert("files".into(), Value::int(done.files as i64));
            exchange.json(&Value::Dict(told), 200);
        }
        Err(why) => refused(exchange, why),
    }
}

/// _theme_preview: a saved theme as a picture (`?w=` its width), for lists of themes; with
/// `?size=WxH` as a panel of that size shows it (Design's sizes). The pictures are kept until
/// the theme or a choice changes.
fn theme_preview(state: &State, exchange: &mut Exchange, theme_id: &str, url: &Url) {
    let query = crate::http::parse_qs(&url.query);
    let first = |name: &str| query.get(name).and_then(|values| values.first()).cloned();
    let width = first("w").map_or(Some(480.into()), |w| lp_py::int_of_str(&w));
    let width = width.map_or(480, |w| {
        let w = w.clamp(64.into(), 1920.into());
        usize::try_from(w).unwrap_or(480)
    });
    let size = crate::previews::size_of(&first("size").unwrap_or_default());
    let places = state.home.places();
    let found = lp_theme::versions::find_theme(theme_id, &places)
        .ok()
        .and_then(|folder| {
            let file = folder.join(lp_theme::tables::THEME_FILENAME);
            let stamp = std::fs::metadata(&file).and_then(|m| m.modified()).ok()?;
            Some((folder, stamp))
        });
    let Some((folder, stamp)) = found else {
        return not_found(exchange);
    };
    let config = lp_config::load_config(&state.config_file, lp_config::platform())
        .unwrap_or_else(|_| lp_config::Config::new(None, lp_config::platform()));
    let asked = crate::previews::Asked {
        folder: &folder,
        size,
        width,
        accent: crate::mainloop::accent_of(&config.accent),
        symbols: config.symbols.clone(),
        chosen: crate::previews::chosen_switches(&config, theme_id),
        cards: config.cards.clone(),
        card_opacity: config.card_opacity,
        light: config.light_colours,
    };
    let key = format!(
        "{theme_id}\0{stamp:?}\0{width}\0{}\0{}\0{:?}\0{:?}\0{:?}\0{}\0{size:?}\0{}",
        lp_i18n::language().code(),
        asked.symbols,
        asked.chosen,
        asked.accent,
        asked.cards,
        asked.card_opacity,
        asked.light,
    );
    let env = lp_theme::Env {
        fonts_dir: state.home.fonts(),
    };
    let gone = || exchange.client_gone();
    let drawn = state.previews.picture(&key, gone, || {
        crate::previews::theme_preview(&asked, &env, &places)
    });
    match drawn {
        None => {}
        Some(Ok(png)) => exchange.send(200, &png, "image/png", ""),
        Some(Err(crate::previews::Refused::Theme(why))) => exchange.error(&why, 404, ""),
        Some(Err(crate::previews::Refused::Drawing(why))) => unanswered(&why),
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

/// `GET /api/tray/icon?state=showing&size=32`: the app's icon with the dot for how the panel is
/// doing, as a PNG (branding.logo).
fn tray_icon(state: &State, exchange: &mut Exchange, query: &str) {
    if state.controls.upgrade().is_none() {
        return exchange.error("this editor does not run the panel", 404, "");
    }
    let asked = crate::http::parse_qs_blank(query);
    let first = |key: &str| asked.get(key).and_then(|values| values.first()).cloned();
    let size = first("size").and_then(|size| size.parse::<usize>().ok());
    let size = size.filter(|size| (16..=256).contains(size)).unwrap_or(64);
    let status = first("state").unwrap_or_default();
    let png = lp_render::branding::logo(size, Some(&status))
        .map_err(|why| why.0)
        .and_then(|picture| {
            lp_devices::png::encode_with(&picture, lp_devices::png::Compression::new(6))
                .map_err(|why| why.to_string())
        });
    match png {
        Ok(png) => exchange.send(200, &png, "image/png", ""),
        Err(why) => exchange.error(&why, 500, ""),
    }
}

/// `POST /api/tray {"id": ...}`: an item of the tray's menu chosen; done in the background.
fn tray_action(state: &State, exchange: &mut Exchange) {
    let Some(payload) = exchange.read_json(MAX_JSON) else {
        return;
    };
    let Some(app) = state.controls.upgrade() else {
        return exchange.error("this editor does not run the panel", 404, "");
    };
    match payload {
        Value::Dict(payload) => match payload.get("id") {
            Some(Value::Str(id)) => {
                crate::tray::act(app, id.clone());
                exchange.json(&Value::Dict(Dict::new()), 200);
            }
            _ => exchange.error("expected {\"id\": ...}", 400, ""),
        },
        _ => exchange.error("expected {\"id\": ...}", 400, ""),
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
    let open = found
        .as_ref()
        .is_some_and(|(route, _)| route.public || route.notice);
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
    let software = format!("LibrePanelEditor/{}", lp_py::VERSION);
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
        let news = crate::news::News::new(&config_file);
        let updates = crate::updates::Updates::new(&config_file);
        let port = listener.local_addr()?.port();
        Ok(EditorServer {
            port,
            token,
            state: Arc::new(State {
                port,
                home,
                config_file,
                controls,
                began: Instant::now(),
                last_request: Mutex::new(None),
                live: Mutex::new(None),
                restart: crate::settings::Restart::default(),
                news,
                updates,
                previews: crate::previews::Previews::default(),
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
