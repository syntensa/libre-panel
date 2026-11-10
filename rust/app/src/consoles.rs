//! The built-in console pages (pages/base.py, pages/control.py, pages/autopilot.py): Control
//! (the panel's state, the services, the sources, test messages, the theme per mode, the log)
//! and Autopilot (the rules of the `autopilot` service and what it is doing), each a JSON API
//! of its own under `/api/plugins/<page>/`, and their files under `/plugins/<page>/`.

use std::path::Path;
use std::sync::Arc;

use indexmap::IndexMap;
use lp_config::edit::Change;
use lp_py::toml::Table;
use lp_py::{Dict, Value};

use crate::background::BackgroundApp;

/// A page: its id, its title by language, its icon.
pub struct PageInfo {
    pub id: &'static str,
    pub titles: &'static [(&'static str, &'static str)],
}

pub const PAGES: [PageInfo; 2] = [
    PageInfo {
        id: "autopilot",
        titles: &[
            ("en", "Autopilot"),
            ("de", "Autopilot"),
            ("fr", "Pilote automatique"),
            ("es", "Piloto automático"),
            ("pl", "Autopilot"),
            ("pt", "Piloto automático"),
            ("it", "Pilota automatico"),
            ("ru", "Автопилот"),
            ("ja", "オートパイロット"),
        ],
    },
    PageInfo {
        id: "control",
        titles: &[
            ("en", "Control"),
            ("de", "Steuerung"),
            ("fr", "Contrôle"),
            ("es", "Control"),
            ("pl", "Sterowanie"),
            ("pt", "Controle"),
            ("it", "Controllo"),
            ("ru", "Управление"),
            ("ja", "操作"),
        ],
    },
];

/// The folder of the pages' files (index.html and the scripts of every page).
pub fn folder(data: &Path) -> std::path::PathBuf {
    data.join("pages").join("web")
}

/// _pages: the pages there are, each `{id, title, icon}`, in the app's language.
pub fn listed(data: &Path) -> Value {
    if !folder(data).join("index.html").is_file() {
        return Value::List(Vec::new());
    }
    let language = lp_i18n::language();
    let code = language.code();
    Value::List(
        PAGES
            .iter()
            .map(|page| {
                let title = page
                    .titles
                    .iter()
                    .find(|(lang, _)| *lang == code)
                    .or_else(|| page.titles.iter().find(|(lang, _)| *lang == "en"))
                    .map_or(page.id, |(_, title)| title);
                let mut d = Dict::new();
                d.insert("id".into(), Value::from(page.id));
                d.insert("title".into(), Value::from(title));
                d.insert("icon".into(), Value::from(""));
                Value::Dict(d)
            })
            .collect(),
    )
}

/// What a page answers: data, sent as JSON, with the status.
pub enum Answer {
    Json(u16, Value),
    /// a picture: its bytes and their type
    Bytes(Vec<u8>, &'static str),
}

/// A request that cannot be met; the page shows the message (already translated).
struct Refused(String, u16);

fn refused(message: impl Into<String>) -> Refused {
    Refused(message.into(), 400)
}

fn t(text: &str) -> String {
    lp_i18n::t(text)
}

fn tf(text: &str, values: &[(&str, Value)]) -> String {
    lp_i18n::t_format(text, values).unwrap_or_else(|why| why.msg)
}

/// The query and the body of a request, with readers that refuse what is wrong.
struct Request<'a> {
    query: &'a IndexMap<String, Vec<String>>,
    body: Dict,
}

impl Request<'_> {
    fn param(&self, name: &str, default: &str) -> String {
        match self.query.get(name).and_then(|values| values.first()) {
            Some(value) => value.clone(),
            None => default.to_string(),
        }
    }

    /// A whole number from the query, kept between `low` and `high`.
    fn integer(&self, name: &str, default: i64, low: i64, high: i64) -> i64 {
        match lp_py::int_of_str(&self.param(name, &default.to_string())) {
            Some(n) => {
                let n = n.clamp(low.into(), high.into());
                i64::try_from(&n).unwrap_or(default)
            }
            None => default,
        }
    }

    fn text(&self, name: &str) -> Result<String, Refused> {
        match self.body.get(name) {
            Some(Value::Str(value)) if !value.is_empty() => Ok(value.clone()),
            _ => Err(refused(tf(
                "{name} is missing",
                &[("name", Value::from(name))],
            ))),
        }
    }
}

/// What a page gets: the running app (None in a plain editor) and config.toml.
pub struct Context<'a> {
    pub app: Option<Arc<BackgroundApp>>,
    pub config_file: &'a Path,
    pub places: lp_theme::versions::Places,
}

impl Context<'_> {
    fn background(&self) -> bool {
        self.app.is_some()
    }

    fn status(&self) -> Option<Dict> {
        self.app.as_ref().map(|app| app.panel.state())
    }

    fn services(&self) -> IndexMap<String, String> {
        self.app
            .as_ref()
            .map(|app| app.services_state())
            .unwrap_or_default()
    }

    fn config(&self) -> Result<lp_config::Config, String> {
        lp_config::load_config(self.config_file, lp_config::platform()).map_err(|why| why.msg)
    }

    fn follow_config(&self) {
        if let Some(app) = &self.app {
            app.follow_config();
        }
    }

    fn themes(&self) -> Vec<Value> {
        lp_theme::versions::list_themes(&self.places)
            .into_iter()
            .map(|theme| theme.get("id").cloned().unwrap_or(Value::None))
            .collect()
    }
}

/// The services this build has (the plugins' registry of Python's).
fn installed_services() -> Vec<&'static str> {
    crate::services::parts()
        .keys()
        .copied()
        .filter(|name| *name != "script")
        .collect()
}

/// handle: a request to page `id`; None for a page there is none of.
pub fn handle(
    id: &str,
    method: &str,
    path: &str,
    query: &IndexMap<String, Vec<String>>,
    body: Option<&Value>,
    context: &Context,
) -> Option<Answer> {
    let route = path.trim_matches('/');
    let body = match body {
        Some(Value::Dict(d)) => d.clone(),
        _ => Dict::new(),
    };
    let request = Request { query, body };
    let done = match id {
        "control" => control(method, route, &request, context),
        "autopilot" => autopilot(method, route, &request, context),
        _ => return None,
    };
    Some(match done {
        Some(Ok(answer)) => answer,
        Some(Err(Refused(message, status))) => Answer::Json(status, error_of(&message)),
        None => Answer::Json(404, error_of(&t("not found"))),
    })
}

fn error_of(message: &str) -> Value {
    let mut d = Dict::new();
    d.insert("error".into(), Value::from(message));
    Value::Dict(d)
}

fn dict(pairs: Vec<(&str, Value)>) -> Value {
    Value::Dict(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

fn ok(value: Value) -> Result<Answer, Refused> {
    Ok(Answer::Json(200, value))
}

// -- what Control and Autopilot share -------------------------------------------------------------

/// service_state: what a screen says about a service, as (code, detail), from what the
/// service manager says (`state`; None when it does not know the service) and from
/// config.toml (`enabled`; None when the file cannot be read).
fn service_state(state: Option<&str>, enabled: Option<bool>) -> (String, String) {
    let enabled = enabled.unwrap_or(state.is_some());
    let Some(state) = state else {
        let code = if enabled { "pending" } else { "off" };
        return (code.into(), String::new());
    };
    if !enabled {
        return ("pending".into(), String::new()); // (it still runs; the app is about to stop it)
    }
    if let Some(why) = state.strip_prefix("failed: ") {
        return ("failed".into(), why.to_string());
    }
    (state.replace(' ', "-"), String::new())
}

/// with_service: `services.enabled` with `name` in it (`on`) or without it; the order of the
/// others stays.
fn with_service(enabled: &[String], name: &str, on: bool) -> Vec<String> {
    if on {
        let mut names = enabled.to_vec();
        if !names.iter().any(|n| n == name) {
            names.push(name.to_string());
        }
        return names;
    }
    enabled.iter().filter(|n| *n != name).cloned().collect()
}

fn names_toml(names: &[String]) -> lp_py::toml::Toml {
    lp_py::toml::Toml::Array(
        names
            .iter()
            .map(|n| lp_py::toml::Toml::Str(n.clone()))
            .collect(),
    )
}

// -- Control ------------------------------------------------------------------------------

const LIVE_WIDTH: i64 = 960;
const JPEG_QUALITY: i64 = 78;
/// the formats of the live picture
const PICTURES: [(&str, &str); 2] = [("jpeg", "image/jpeg"), ("png", "image/png")];
const BUTTONS: [&str; 3] = ["timer", "timer_reset", "mic_mute"];
const RESTARTABLE: [&str; 3] = ["running", "stopped", "failed"];
const GAME_MODE: &str = "game";

fn why_not(reason: &str) -> String {
    match reason {
        lp_services::host::OFF_IN_THEME => t("the theme switches this kind off (toast.off)"),
        lp_services::host::LEFT_OUT => t("the screen shows this kind itself"),
        _ => t("no panel is connected"),
    }
}

/// probes: a test message of each kind: the name the page gives it and its toast, made as the
/// services make theirs.
fn probes(look: &str) -> Vec<(&'static str, String, lp_render::overlays::Toast)> {
    use lp_render::overlays::Toast;
    let test = t("Test message");
    let probe = |kind: &str,
                 name: &str,
                 text: &str,
                 label: &str,
                 rank: i64,
                 seconds: Option<f64>,
                 icon: Option<&str>,
                 level: &str,
                 more: Vec<(&str, Value)>| {
        let mut payload = Dict::new();
        let label = if label.is_empty() { name } else { label };
        payload.insert("label".into(), Value::from(label));
        for (key, value) in more {
            payload.insert(key.into(), value);
        }
        Toast {
            icon: icon.map(str::to_string),
            level: level.into(),
            seconds,
            kind: kind.into(),
            rank,
            payload,
            ..Toast::new(text)
        }
    };
    let time = {
        let at = crate::share::local_time(crate::share::now());
        format!("{:02}:{:02}", at[3], at[4])
    };
    let mut volume = vec![("progress", Value::Float(0.42))];
    if !look.is_empty() {
        volume.push(("look", Value::from(look)));
    }
    let names = [
        ("volume", t("Volume")),
        ("music", t("Music")),
        ("warning", t("Warning")),
        ("panel", t("Panel")),
        ("device", t("Device")),
        ("game", t("Game")),
    ];
    let restarted = tf("Panel restarted {time}", &[("time", Value::Str(time))]);
    let toasts = [
        probe(
            "volume",
            &names[0].1,
            "42",
            "",
            0,
            None,
            None,
            "info",
            volume,
        ),
        probe(
            "music",
            &names[1].1,
            &test,
            &t("Now playing"),
            1,
            Some(5.0),
            Some("music"),
            "info",
            vec![("progress", Value::Float(0.4))],
        ),
        probe(
            "warning",
            &names[2].1,
            &test,
            "",
            2,
            Some(5.0),
            Some("temperature"),
            "warning",
            vec![("progress", Value::Float(0.9))],
        ),
        probe(
            "panel",
            &names[3].1,
            &restarted,
            "",
            2,
            Some(8.0),
            None,
            "info",
            Vec::new(),
        ),
        probe(
            "device",
            &names[4].1,
            &test,
            "",
            1,
            Some(4.0),
            Some("usb"),
            "info",
            Vec::new(),
        ),
        probe(
            "game",
            &names[5].1,
            &test,
            &t("Game mode"),
            1,
            Some(3.0),
            Some("gamepad"),
            "info",
            Vec::new(),
        ),
    ];
    names
        .into_iter()
        .zip(toasts)
        .map(|((kind, name), toast)| (kind, name, toast))
        .collect()
}

fn log_file() -> Value {
    let path = lp_config::dirs::log_file();
    dict(vec![
        ("path", Value::Str(path.to_string_lossy().into_owned())),
        ("exists", Value::Bool(path.is_file())),
    ])
}

/// tail: the last `lines` lines of a text file, read from its end; empty where it cannot be
/// read.
fn tail(path: &Path, lines: i64) -> Vec<String> {
    let lines = lines.clamp(1, 1000) as usize;
    let Ok(data) = std::fs::read(path) else {
        return Vec::new();
    };
    let size = data.len();
    let mut window = lines * 200;
    loop {
        let start = size.saturating_sub(window);
        let text = String::from_utf8_lossy(&data[start..]).into_owned();
        let mut rows: Vec<String> = lp_py::splitlines(&text)
            .into_iter()
            .map(str::to_string)
            .collect();
        if start == 0 || rows.len() > lines {
            if start > 0 {
                rows.remove(0); // (the first line is cut off)
            }
            let from = rows.len().saturating_sub(lines);
            return rows[from..].to_vec();
        }
        window *= 2;
    }
}

fn probes_listed() -> Value {
    Value::List(
        probes("")
            .into_iter()
            .map(|(kind, name, _)| {
                dict(vec![
                    ("kind", Value::from(kind)),
                    ("label", Value::Str(name)),
                ])
            })
            .collect(),
    )
}

fn control_themes(config: Option<&lp_config::Config>, context: &Context) -> Value {
    let panel = context.status();
    let of = |key: &str| panel.as_ref().and_then(|p| p.get(key)).cloned();
    let normal = config.map_or(Value::None, |config| {
        if config.theme_given {
            return Value::Str(config.theme.clone());
        }
        match of("default_theme") {
            Some(Value::Str(running)) if !running.is_empty() => Value::Str(running),
            _ => {
                let model = crate::settings::panel_model(config);
                Value::from(lp_theme::defaults::default_theme(model, &context.places))
            }
        }
    });
    let game = config
        .and_then(|config| config.modes.get(GAME_MODE))
        .map_or(String::new(), |mode| mode.theme.clone().unwrap_or_default());
    dict(vec![
        ("available", Value::List(context.themes())),
        ("normal", normal),
        ("game", Value::Str(game)),
        ("shown", of("theme").unwrap_or(Value::None)),
        ("mode", of("mode").unwrap_or(Value::None)),
    ])
}

fn service_row(
    name: &str,
    enabled: bool,
    applied: &IndexMap<String, String>,
    context: &Context,
) -> Dict {
    let (code, detail) = match context.background() {
        true => service_state(applied.get(name).map(String::as_str), Some(enabled)),
        false => ("unknown".into(), String::new()), // (this editor does not run services)
    };
    let summary = match (code.as_str(), &context.app) {
        ("running", Some(app)) => match app.service_summary(name) {
            Some(Value::Dict(d)) => Value::Dict(d),
            _ => Value::None,
        },
        _ => Value::None,
    };
    let mut row = Dict::new();
    row.insert("name".into(), Value::from(name));
    row.insert("state".into(), Value::Str(code.clone()));
    row.insert("detail".into(), Value::Str(detail));
    row.insert("enabled".into(), Value::Bool(enabled));
    row.insert(
        "installed".into(),
        Value::Bool(installed_services().contains(&name)),
    );
    row.insert(
        "restartable".into(),
        Value::Bool(RESTARTABLE.contains(&code.as_str())),
    );
    row.insert("summary".into(), summary);
    row
}

fn control_services(config: Option<&lp_config::Config>, context: &Context) -> Value {
    let applied = context.services();
    let enabled: Vec<String> = match config {
        Some(config) => config.services.enabled.clone(),
        None => applied.keys().cloned().collect(),
    };
    let mut names: Vec<String> = installed_services()
        .into_iter()
        .map(str::to_string)
        .chain(applied.keys().cloned())
        .chain(enabled.iter().cloned())
        .collect();
    names.sort();
    names.dedup();
    Value::List(
        names
            .iter()
            .map(|name| {
                let on = enabled.contains(name);
                Value::Dict(service_row(name, on, &applied, context))
            })
            .collect(),
    )
}

fn yes_no(value: Option<bool>) -> String {
    match value {
        None => t("unknown"),
        Some(true) => t("yes"),
        Some(false) => t("no"),
    }
}

/// lhm_rows: how LibreHardwareMonitor is read and what keeps the CPU's sensors out of reach.
fn lhm_rows(status: &Dict) -> Vec<Value> {
    let get = |key: &str| status.get(key).cloned().unwrap_or(Value::None);
    let flag = |key: &str| match get(key) {
        Value::Bool(flag) => Some(flag),
        _ => None,
    };
    let row =
        |label: String, value: String| Value::List(vec![Value::Str(label), Value::Str(value)]);
    let mode = get("mode");
    let mode = mode.as_str().unwrap_or("");
    let names = [
        ("service", "sensor service"),
        ("inprocess", "inside Libre Panel"),
    ];
    let named = names.iter().find(|(key, _)| *key == mode);
    let shown = named.map_or_else(|| t("web server"), |(_, name)| t(name));
    let mut rows = vec![row(t("Mode"), shown)];
    if named.is_some() {
        if mode == "inprocess" {
            rows.push(row(t("Administrator"), yes_no(flag("elevated"))));
        }
        let reach = match flag("ring0") {
            None => t("unknown"),
            Some(true) => t("reachable"),
            Some(false) => t("out of reach"),
        };
        let driver = match get("pawnio") {
            Value::Str(version) if !version.is_empty() => version,
            Value::Str(_) => t("unknown"),
            _ => t("not installed"),
        };
        rows.push(row(t("CPU sensors"), reach));
        rows.push(row(t("PawnIO driver"), driver));
    }
    for (key, label) in [("service", "Sensor service"), ("error", "Error")] {
        let value = get(key);
        if value.truthy() {
            rows.push(row(
                t(label),
                value.as_str().unwrap_or_default().to_string(),
            ));
        }
    }
    rows
}

/// str() of a value a source says of itself.
fn as_text(value: &Value) -> String {
    match value {
        Value::Str(text) => text.clone(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Int(n) => n.to_string(),
        other => lp_py::json::dumps(other, &lp_py::json::Dumps::default()),
    }
}

/// _sources: the sensor sources the panel reads, the first that has a reading winning: each
/// with its readings, what it says of itself, as rows, the hint the tray shows for
/// LibreHardwareMonitor and whether something is the matter.
fn control_sources(context: &Context) -> Value {
    let Some(app) = &context.app else {
        return Value::List(Vec::new());
    };
    let panel = app.panel.state();
    let found = app
        .panel
        .sources()
        .into_iter()
        .map(|(name, readings, status)| {
            let (mut rows, mut hint) = (Vec::new(), Value::None);
            match &status {
                Some(status) if name == "librehardwaremonitor" => {
                    rows = lhm_rows(status);
                    hint = panel
                        .get("hint")
                        .filter(|hint| hint.truthy())
                        .cloned()
                        .unwrap_or(Value::None);
                }
                Some(status) => {
                    rows = (status.iter())
                        .filter(|(_, value)| !matches!(value, Value::None))
                        .map(|(key, value)| {
                            let label = Value::Str(key.replace('_', " "));
                            Value::List(vec![label, Value::Str(as_text(value))])
                        })
                        .collect();
                }
                None => {}
            }
            let error = status.as_ref().and_then(|status| status.get("error"));
            let problem = hint.truthy() || error.is_some_and(Value::truthy);
            Value::Dict(
                [
                    ("name", Value::Str(name.into())),
                    ("readings", Value::int(readings as i64)),
                    ("status", status.map_or(Value::None, Value::Dict)),
                    ("details", Value::List(rows)),
                    ("hint", hint),
                    ("problem", Value::Bool(problem)),
                ]
                .into_iter()
                .map(|(key, value)| (key.to_string(), value))
                .collect(),
            )
        });
    Value::List(found.collect())
}

fn control(
    method: &str,
    route: &str,
    request: &Request,
    context: &Context,
) -> Option<Result<Answer, Refused>> {
    let config = || context.config();
    let panel = || context.status().map_or(Value::None, Value::Dict);
    let background = Value::Bool(context.background());
    Some(match (method, route) {
        ("GET", "status") => {
            let found = config();
            let problem = found.as_ref().err().cloned().unwrap_or_default();
            let config = found.ok();
            ok(dict(vec![
                ("background", background),
                ("panel", panel()),
                ("config_problem", Value::Str(problem)),
                ("services", control_services(config.as_ref(), context)),
                ("sources", control_sources(context)),
                ("probes", probes_listed()),
                ("themes", control_themes(config.as_ref(), context)),
                ("log", log_file()),
            ]))
        }
        ("GET", "panel") => ok(dict(vec![("background", background), ("panel", panel())])),
        ("GET", "sources") => ok(dict(vec![
            ("background", background),
            ("sources", control_sources(context)),
        ])),
        ("GET", "services") => {
            let found = config();
            let problem = found.as_ref().err().cloned().unwrap_or_default();
            ok(dict(vec![
                ("background", background),
                ("services", control_services(found.ok().as_ref(), context)),
                ("config_problem", Value::Str(problem)),
            ]))
        }
        ("GET", "probes") => ok(dict(vec![("probes", probes_listed())])),
        ("GET", "themes") => {
            let found = config();
            let problem = found.as_ref().err().cloned().unwrap_or_default();
            let Value::Dict(mut themes) = control_themes(found.ok().as_ref(), context) else {
                return None;
            };
            themes.insert("config_problem".into(), Value::Str(problem));
            ok(Value::Dict(themes))
        }
        ("GET", "live") => live(request, context),
        ("GET", "log") => {
            let lines = tail(
                &lp_config::dirs::log_file(),
                request.integer("lines", 60, 1, 1000),
            );
            let Value::Dict(mut told) = log_file() else {
                return None;
            };
            let mut out = Dict::new();
            out.insert(
                "lines".into(),
                Value::List(lines.into_iter().map(Value::Str).collect()),
            );
            out.append(&mut told);
            ok(Value::Dict(out))
        }
        ("POST", "probe") => probe(request, context),
        ("POST", "services/enable") => enable(request, context),
        ("POST", "services/restart") => restart(request, context),
        ("POST", "theme") => set_theme(request, context),
        ("POST", "shortcut") => shortcut(request, context),
        _ => return None,
    })
}

fn live(request: &Request, context: &Context) -> Result<Answer, Refused> {
    let panel = request.integer("panel", 1, 1, 4); // (which is which)
    let frame = (context.app.as_ref()).and_then(|app| app.panel.shown_frame(panel));
    let Some(frame) = frame else {
        return Ok(Answer::Json(404, error_of(&t("no picture yet"))));
    };
    let kind = request.param("format", "jpeg").to_lowercase();
    let Some((_, content)) = PICTURES.iter().find(|(name, _)| *name == kind) else {
        return Err(refused(t("format is jpeg or png")));
    };
    let width = request.integer("w", LIVE_WIDTH, 160, 1920);
    let picture = encode(&frame, width, &kind).map_err(|why| Refused(why, 500))?;
    Ok(Answer::Bytes(picture, content))
}

/// encode: `frame` as a JPEG or PNG `width` pixels wide (a smaller frame stays as it is).
fn encode(frame: &lp_pixels::Image, width: i64, kind: &str) -> Result<Vec<u8>, String> {
    let (w, h) = frame.size();
    let resized;
    let mut frame = frame;
    if width < w as i64 {
        let height = (h as f64 * width as f64 / w as f64)
            .round_ties_even()
            .max(1.0);
        let size = (width as usize, height as usize);
        resized = (frame.resize(size.0, size.1, lp_pixels::Filter::Bilinear, None, None))
            .map_err(|why| why.to_string())?;
        frame = &resized;
    }
    let rgb;
    if frame.mode() != lp_pixels::Mode::RGB {
        rgb = frame
            .convert(lp_pixels::Mode::RGB)
            .map_err(|why| why.to_string())?;
        frame = &rgb;
    }
    match kind {
        "png" => lp_devices::png::encode_with(frame, lp_devices::png::Compression::new(3)),
        _ => lp_decode::save_jpeg(frame, JPEG_QUALITY).map_err(|why| why.to_string()),
    }
}

fn probe(request: &Request, context: &Context) -> Result<Answer, Refused> {
    let kind = request.text("kind")?;
    let look = context
        .config()
        .ok()
        .and_then(
            |config| match config.services.options.get("notices")?.get("volume_look")? {
                lp_py::toml::Toml::Str(look) => Some(look.clone()),
                _ => None,
            },
        )
        .unwrap_or_default();
    let Some((_, name, toast)) = probes(&look).into_iter().find(|(k, _, _)| *k == kind) else {
        return Err(refused(tf(
            "unknown kind of message: {kind}",
            &[("kind", Value::Str(kind))],
        )));
    };
    let delivery = match &context.app {
        Some(app) => app.notify(toast),
        None => lp_services::host::Delivery {
            shown: false,
            reason: lp_services::host::NO_PANEL.into(),
        },
    };
    let message = match delivery.shown {
        true => t("Shown on the panel."),
        false => tf(
            "Not shown: {why}.",
            &[("why", Value::Str(why_not(&delivery.reason)))],
        ),
    };
    ok(dict(vec![
        ("kind", Value::Str(kind)),
        ("name", Value::Str(name)),
        ("shown", Value::Bool(delivery.shown)),
        ("reason", Value::Str(delivery.reason)),
        ("message", Value::Str(message)),
    ]))
}

fn enable(request: &Request, context: &Context) -> Result<Answer, Refused> {
    let name = request.text("service")?;
    let Some(Value::Bool(on)) = request.body.get("enabled") else {
        return Err(refused(t("enabled is on or off")));
    };
    let on = *on;
    let config = context.config().map_err(|problem| Refused(problem, 409))?;
    if on && !installed_services().contains(&name.as_str()) {
        let said = tf(
            "{name} is not an installed service",
            &[("name", Value::Str(name))],
        );
        return Err(Refused(said, 404));
    }
    let names = config.services.enabled.clone();
    if on != names.contains(&name) {
        let names = with_service(&names, &name, on);
        let written = lp_config::edit::set_config_value(
            "services.enabled",
            names_toml(&names),
            context.config_file,
            lp_config::platform(),
        );
        written.map_err(|why| Refused(why.msg, 409))?;
        context.follow_config();
    }
    let row = service_row(&name, on, &context.services(), context);
    ok(dict(vec![
        ("service", Value::Str(name)),
        ("enabled", Value::Bool(on)),
        ("state", row.get("state").cloned().unwrap_or(Value::None)),
        ("detail", row.get("detail").cloned().unwrap_or(Value::None)),
    ]))
}

fn restart(request: &Request, context: &Context) -> Result<Answer, Refused> {
    let name = request.text("service")?;
    let Some(app) = &context.app else {
        return Err(Refused(t("This editor does not run the services."), 404));
    };
    let state = app.restart_service(&name).map_err(|_| {
        let said = tf(
            "{name} is not enabled in config.toml",
            &[("name", Value::Str(name.clone()))],
        );
        Refused(said, 404)
    })?;
    let (code, detail) = service_state(Some(&state), Some(true));
    ok(dict(vec![
        ("service", Value::Str(name)),
        ("state", Value::Str(code)),
        ("detail", Value::Str(detail)),
    ]))
}

fn shortcut(request: &Request, context: &Context) -> Result<Answer, Refused> {
    let action = request.text("action")?;
    if !BUTTONS.contains(&action.as_str()) {
        return Err(refused(tf(
            "action is one of {actions}",
            &[("actions", Value::Str(BUTTONS.join(", ")))],
        )));
    }
    let Some(app) = &context.app else {
        return Err(Refused(t("This editor does not run the services."), 404));
    };
    app.shortcut(&action);
    ok(dict(vec![
        ("action", Value::Str(action)),
        ("done", Value::Bool(true)),
    ]))
}

fn set_theme(request: &Request, context: &Context) -> Result<Answer, Refused> {
    let mode = request.text("mode")?;
    let theme = match request.body.get("theme") {
        Some(Value::Str(theme)) if mode == "normal" || mode == "game" => theme.clone(),
        _ => return Err(refused(t("mode is normal or game, and theme a name"))),
    };
    // (the game mode may name none: it shows the normal one)
    if (!theme.is_empty() || mode == "normal")
        && lp_theme::versions::find_theme(&theme, &context.places).is_err()
    {
        return Err(refused(tf(
            "Theme {theme} not found",
            &[("theme", Value::Str(theme))],
        )));
    }
    let key = match mode.as_str() {
        "normal" => "theme".to_string(),
        _ => format!("modes.{GAME_MODE}.theme"),
    };
    lp_config::edit::set_config_value(
        &key,
        lp_py::toml::Toml::Str(theme.clone()),
        context.config_file,
        lp_config::platform(),
    )
    .map_err(|why| Refused(why.msg, 409))?;
    ok(dict(vec![
        ("mode", Value::Str(mode)),
        ("theme", Value::Str(theme)),
    ]))
}

// -- Autopilot ----------------------------------------------------------------------------

const SERVICE: &str = "autopilot";
const LOADS: [&str; 2] = ["cpu_over", "gpu_over"];
/// no setting of the autopilot is longer than a day
const LONGEST_S: f64 = 86400.0;

fn is_number(value: &Value) -> bool {
    matches!(value, Value::Int(_) | Value::Float(_))
}

fn number(value: &Value) -> f64 {
    value.to_f64().unwrap_or(f64::NAN)
}

fn condition_problem(key: &str, value: &Value) -> String {
    let say = |text: &str| tf(text, &[("key", Value::from(key))]);
    match key {
        "music" | "microphone" => match value {
            Value::Bool(_) => String::new(),
            _ => say("{key} is on or off"),
        },
        k if LOADS.contains(&k) => {
            match is_number(value) && (0.0..=100.0).contains(&number(value)) {
                true => String::new(),
                false => say("{key} is a load between 0 and 100"),
            }
        }
        "for_s" => match is_number(value) && number(value) > 0.0 {
            true => String::new(),
            false => t("for_s is a number of seconds"),
        },
        "apps" => {
            let names = value.as_list().unwrap_or(&[]);
            let fine = !names.is_empty()
                && names
                    .iter()
                    .all(|n| matches!(n, Value::Str(s) if !lp_py::strip(s).is_empty()));
            match fine {
                true => String::new(),
                false => t("apps is a list of program names"),
            }
        }
        "time" => {
            let fine = matches!(value, Value::Str(_))
                && lp_services::autopilot::parse_time(value).is_some();
            match fine {
                true => String::new(),
                false => t("time looks like \"22:00-06:30\""),
            }
        }
        _ => say("unknown condition {key}"),
    }
}

fn rule_problem(rule: &Value) -> String {
    let Value::Dict(rule) = rule else {
        return t("a rule is a table");
    };
    match rule.get("name") {
        Some(Value::Str(name)) if !lp_py::strip(name).is_empty() => {}
        _ => return t("every rule needs a name"),
    }
    match rule.get("theme") {
        Some(Value::Str(theme)) if !theme.is_empty() => {}
        _ => return t("every rule needs a theme"),
    }
    let when = rule.get("when");
    let Some(Value::Dict(conditions)) = when else {
        return t("a rule needs at least one condition");
    };
    if conditions.is_empty() {
        return t("a rule needs at least one condition");
    }
    for (key, value) in conditions {
        let problem = condition_problem(key, value);
        if !problem.is_empty() {
            return problem;
        }
    }
    for key in ["enabled", "immediate"] {
        if rule.get(key).is_some_and(|v| !matches!(v, Value::Bool(_))) {
            return tf("{key} is on or off", &[("key", Value::from(key))]);
        }
    }
    if let Some(hold) = rule.get("hold_s") {
        if !(is_number(hold) && (0.0..=LONGEST_S).contains(&number(hold))) {
            return t("hold_s is a number of seconds");
        }
    }
    // (the service's own word, if it finds more)
    lp_services::autopilot::parse_conditions(when)
        .err()
        .unwrap_or_default()
}

/// The autopilot's options beside its rules: (name, kind, default).
fn option_specs() -> Vec<(&'static str, &'static str, Value)> {
    (lp_services::autopilot::part().options)()
}

fn settings_problem(options: &Value) -> String {
    let Value::Dict(options) = options else {
        return t("options is a table");
    };
    for (key, kind, _) in option_specs() {
        let Some(value) = options.get(key).filter(|_| key != "rules") else {
            continue;
        };
        let say = |text: &str| tf(text, &[("key", Value::from(key))]);
        if kind == "string" {
            if !matches!(value, Value::Str(_)) {
                return say("{key} is a name");
            }
        } else if !(is_number(value) && (0.0..=LONGEST_S).contains(&number(value))) {
            return say("{key} is a number of seconds");
        }
    }
    String::new()
}

fn autopilot_status(config: Option<&lp_config::Config>, context: &Context) -> Dict {
    let enabled = config.map(|config| config.services.enabled.iter().any(|n| n == SERVICE));
    let (code, detail) = match context.background() {
        true => service_state(context.services().get(SERVICE).map(String::as_str), enabled),
        false => ("unknown".into(), String::new()),
    };
    let status = context
        .app
        .as_ref()
        .and_then(|app| app.service_status(SERVICE))
        .unwrap_or(Value::None);
    let mut told = Dict::new();
    told.insert("background".into(), Value::Bool(context.background()));
    told.insert("enabled".into(), enabled.map_or(Value::None, Value::Bool));
    told.insert("state".into(), Value::Str(code));
    told.insert("detail".into(), Value::Str(detail));
    told.insert("status".into(), status);
    told
}

fn given_options(config: &lp_config::Config) -> Dict {
    crate::services::options_of(config)
        .shift_remove(SERVICE)
        .unwrap_or_default()
}

fn autopilot_read(context: &Context) -> Result<Answer, Refused> {
    let config = context.config().map_err(|problem| Refused(problem, 409))?;
    let given = given_options(&config);
    let rules = match given.get("rules") {
        Some(Value::List(rules)) => rules.clone(),
        _ => Vec::new(),
    };
    let themes = context.themes();
    let problems: Vec<Value> = rules
        .iter()
        .map(|rule| {
            let mut problem = rule_problem(rule);
            let theme = match rule {
                Value::Dict(d) => d.get("theme").cloned().unwrap_or(Value::None),
                _ => Value::None,
            };
            if problem.is_empty() && !themes.contains(&theme) {
                problem = tf(
                    "theme {theme} is not installed: the autopilot skips this rule",
                    &[("theme", theme)],
                );
            }
            Value::Str(problem)
        })
        .collect();
    let options: Dict = option_specs()
        .into_iter()
        .filter(|(key, _, _)| *key != "rules")
        .map(|(key, _, default)| (key.to_string(), given.get(key).cloned().unwrap_or(default)))
        .collect();
    let transitions: Vec<Value> = std::iter::once("")
        .chain(lp_render::transitions::BUILT_IN)
        .map(Value::from)
        .collect();
    let mut told = autopilot_status(Some(&config), context);
    told.insert("options".into(), Value::Dict(options));
    told.insert("rules".into(), Value::List(rules));
    told.insert("problems".into(), Value::List(problems));
    told.insert("themes".into(), Value::List(themes));
    told.insert("transitions".into(), Value::List(transitions));
    ok(Value::Dict(told))
}

fn autopilot_save(request: &Request, context: &Context) -> Result<Answer, Refused> {
    let body = &request.body;
    let (rules, options, enabled) = (body.get("rules"), body.get("options"), body.get("enabled"));
    let rules = rules.filter(|r| !matches!(r, Value::None));
    let options = options.filter(|o| !matches!(o, Value::None));
    let enabled = enabled.filter(|e| !matches!(e, Value::None));
    if let Some(rules) = rules {
        let Value::List(list) = rules else {
            return Err(refused(t("rules is a list")));
        };
        for (number, rule) in list.iter().enumerate() {
            let problem = rule_problem(rule);
            if !problem.is_empty() {
                let name = match rule {
                    Value::Dict(d) => d.get("name").filter(|n| n.truthy()).cloned(),
                    _ => None,
                };
                let name = name.unwrap_or(Value::int((number + 1) as i64));
                return Err(refused(tf(
                    "Rule {name}: {problem}",
                    &[("name", name), ("problem", Value::Str(problem))],
                )));
            }
        }
    }
    if let Some(options) = options {
        let problem = settings_problem(options);
        if !problem.is_empty() {
            return Err(refused(problem));
        }
    }
    if enabled.is_some_and(|e| !matches!(e, Value::Bool(_))) {
        return Err(refused(t("enabled is on or off")));
    }
    let config = context.config().map_err(|problem| Refused(problem, 409))?;
    let given = given_options(&config);
    let specs = option_specs();
    let mut changes = Vec::new();
    if let Some(Value::Dict(options)) = options {
        for (key, value) in options {
            let Some((_, _, default)) = specs.iter().find(|(k, _, _)| k == key) else {
                continue;
            };
            if key == "rules" {
                continue;
            }
            if *value != given.get(key).cloned().unwrap_or_else(|| default.clone()) {
                let toml = crate::settings::toml_of(value)
                    .ok_or_else(|| refused(format!("{key}: not a value config.toml holds")))?;
                changes.push(Change::Value(format!("services.{SERVICE}.{key}"), toml));
            }
        }
    }
    if let Some(Value::Bool(on)) = enabled {
        let names = &config.services.enabled;
        if *on != names.iter().any(|n| n == SERVICE) {
            let names = with_service(names, SERVICE, *on);
            changes.push(Change::Value("services.enabled".into(), names_toml(&names)));
        }
    }
    if let Some(Value::List(list)) = rules {
        let before = given
            .get("rules")
            .cloned()
            .unwrap_or(Value::List(Vec::new()));
        if Value::List(list.clone()) != before {
            let mut tables: Vec<Table> = Vec::new();
            for rule in list {
                match crate::settings::toml_of(rule) {
                    Some(lp_py::toml::Toml::Table(table)) => tables.push(table),
                    _ => return Err(refused("a rule config.toml cannot hold")),
                }
            }
            changes.push(Change::Tables(format!("services.{SERVICE}.rules"), tables));
        }
    }
    if !changes.is_empty() {
        // (one write: one restart)
        lp_config::edit::edit_config(&changes, context.config_file, lp_config::platform())
            .map_err(|why| Refused(why.msg, 409))?;
        context.follow_config();
    }
    autopilot_read(context)
}

fn autopilot(
    method: &str,
    route: &str,
    request: &Request,
    context: &Context,
) -> Option<Result<Answer, Refused>> {
    Some(match (method, route) {
        ("GET", "settings") => autopilot_read(context),
        ("GET", "status") => {
            let config = context.config().ok();
            ok(Value::Dict(autopilot_status(config.as_ref(), context)))
        }
        ("POST", "settings") => autopilot_save(request, context),
        _ => return None,
    })
}
