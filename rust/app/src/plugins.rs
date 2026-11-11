//! The plugins (plugins/loader.py, docs/PLUGINS.md): Python packages, which the Rust app runs
//! in a process of their own, the plugin host (src/libre_panel/plugin_host.py; the program
//! libre-panel-plugins in the downloads). It is started only when plugin folders are
//! installed (`<settings>/plugins/<name>/plugin.toml`), once per process, and asked over its
//! standard input and output: a message is a length (4 bytes, little-endian), that many bytes
//! of JSON and, where the JSON has `"blob": n`, n bytes of data after it. A request has an
//! `id` and its answer the same; what plugins do on their own comes as a message with `event`.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use lp_py::{Dict, Value};

pub const API_VERSION: i64 = 1;
pub const PLUGINS_DIRNAME: &str = "plugins";
pub const PLUGIN_FILENAME: &str = "plugin.toml";
pub const BUILT_IN: &str = "built in";
/// how long a request may take before the host is taken for hung
const ANSWER_S: u64 = 30;

/// loader.py's BUILT_IN_PARTS: what Libre Panel brings itself, by kind (a plugin cannot use
/// these names).
pub const BUILT_IN_PARTS: [(&str, &[&str]); 2] = [
    (
        "services",
        &[
            "agents",
            "autopilot",
            "cooling",
            "devices",
            "downloads",
            "game",
            "memory",
            "notices",
            "power",
            "timer",
            "waveshare",
        ],
    ),
    ("editor_pages", &["autopilot", "control"]),
];

/// The plugin folders installed in `settings` (each with its plugin.toml), by name.
pub fn folders(settings: &Path) -> Vec<PathBuf> {
    let root = settings.join(PLUGINS_DIRNAME);
    let mut found: Vec<PathBuf> = std::fs::read_dir(&root)
        .into_iter()
        .flatten()
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.join(PLUGIN_FILENAME).is_file())
        .collect();
    found.sort();
    found
}

/// The plugin host's program and the arguments before its own: LIBRE_PANEL_PLUGIN_HOST (and
/// LIBRE_PANEL_PLUGIN_HOST_ARGS, split at blanks: `-m libre_panel.plugin_host` for a Python),
/// else the downloads' libre-panel-plugins in "plugin-host" beside this program.
pub fn program() -> Option<(PathBuf, Vec<String>)> {
    if let Some(named) = std::env::var_os("LIBRE_PANEL_PLUGIN_HOST").filter(|v| !v.is_empty()) {
        let args = std::env::var("LIBRE_PANEL_PLUGIN_HOST_ARGS").unwrap_or_default();
        let args = args.split_whitespace().map(str::to_string).collect();
        return Some((PathBuf::from(named), args));
    }
    let exe = std::env::current_exe().ok()?;
    let name = if cfg!(windows) {
        "libre-panel-plugins.exe"
    } else {
        "libre-panel-plugins"
    };
    let beside = exe.parent()?.join("plugin-host").join(name);
    beside.is_file().then(|| (beside, Vec::new()))
}

type Answer = (Dict, Vec<u8>);

struct Inner {
    stdin: Mutex<ChildStdin>,
    child: Mutex<Child>,
    waiting: Mutex<HashMap<u64, Sender<Answer>>>,
    next: AtomicU64,
    events: Mutex<Option<Sender<Answer>>>,
    gone: Mutex<Option<String>>,
}

/// The plugin host, running.
pub struct PluginHost {
    inner: Arc<Inner>,
    /// what it said first: the plugin API, the parts found, held back and the errors
    pub hello: Dict,
}

impl PluginHost {
    /// The host for the plugin folders of `settings`; none where none is installed. An error
    /// where plugins are installed but there is no host to run them, or it does not start.
    pub fn start(settings: &Path) -> Result<Option<PluginHost>, String> {
        if folders(settings).is_empty() {
            return Ok(None);
        }
        let Some((program, mut args)) = program() else {
            return Err(
                "plugins are installed, but this Libre Panel has no plugin host \
                        (libre-panel-plugins) to run them"
                    .into(),
            );
        };
        args.push("--folder".into());
        args.push(
            settings
                .join(PLUGINS_DIRNAME)
                .to_string_lossy()
                .into_owned(),
        );
        let mut command = Command::new(&program);
        command
            .args(&args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        let mut child = command
            .spawn()
            .map_err(|why| format!("the plugin host {} did not start: {why}", program.display()))?;
        let stdin = child.stdin.take().ok_or("the plugin host has no input")?;
        let mut stdout = child.stdout.take().ok_or("the plugin host has no output")?;
        // what the plugins log goes into the app's log: "<level>\t<logger>\t<message>" a line,
        // and the lines of a traceback after it
        if let Some(stderr) = child.stderr.take() {
            let _ = std::thread::Builder::new()
                .name("plugin-log".into())
                .spawn(move || {
                    use std::io::BufRead;
                    let mut last = ("libre_panel.plugins".to_string(), "INFO".to_string());
                    for line in std::io::BufReader::new(stderr)
                        .lines()
                        .map_while(Result::ok)
                    {
                        let mut parts = line.splitn(3, '\t');
                        let message = match (parts.next(), parts.next(), parts.next()) {
                            (Some(level), Some(logger), Some(message)) => {
                                last = (logger.to_string(), level.to_string());
                                message.to_string()
                            }
                            _ => line,
                        };
                        crate::logs::plugin_line(&last.1, &last.0, &message);
                    }
                });
        }
        let inner = Arc::new(Inner {
            stdin: Mutex::new(stdin),
            child: Mutex::new(child),
            waiting: Mutex::new(HashMap::new()),
            next: AtomicU64::new(1),
            events: Mutex::new(None),
            gone: Mutex::new(None),
        });
        let reading = Arc::clone(&inner);
        std::thread::Builder::new()
            .name("plugin-host".into())
            .spawn(move || {
                let why = loop {
                    match receive(&mut stdout) {
                        Ok(Some(message)) => reading.deliver(message),
                        Ok(None) => break "the plugin host ended".to_string(),
                        Err(why) => break format!("the plugin host's answer is broken: {why}"),
                    }
                };
                if let Ok(mut gone) = reading.gone.lock() {
                    *gone = Some(why);
                }
                // (whoever waits hears that no answer comes)
                if let Ok(mut waiting) = reading.waiting.lock() {
                    waiting.clear();
                }
            })
            .map_err(|why| why.to_string())?;
        let mut host = PluginHost {
            inner,
            hello: Dict::new(),
        };
        host.hello = host.request("hello", Dict::new(), &[])?.0;
        Ok(Some(host))
    }

    /// `op` asked with `fields` and `blob`; its answer (an error where it says one).
    pub fn request(&self, op: &str, fields: Dict, blob: &[u8]) -> Result<Answer, String> {
        if let Some(why) = self.inner.gone.lock().ok().and_then(|gone| gone.clone()) {
            return Err(why);
        }
        let id = self.inner.next.fetch_add(1, Ordering::Relaxed);
        let (tell, answer) = channel();
        self.inner
            .waiting
            .lock()
            .map_err(|_| "the plugin host's lock is broken")?
            .insert(id, tell);
        let mut message = fields;
        message.insert("id".into(), Value::Int(id.into()));
        message.insert("op".into(), Value::from(op));
        self.inner.send(message, blob)?;
        let (told, data) = answer
            .recv_timeout(Duration::from_secs(ANSWER_S))
            .map_err(|_| {
                self.inner.waiting.lock().ok().map(|mut w| w.remove(&id));
                self.inner
                    .gone
                    .lock()
                    .ok()
                    .and_then(|gone| gone.clone())
                    .unwrap_or_else(|| format!("the plugin host did not answer {op} in time"))
            })?;
        match told.get("error") {
            Some(Value::Str(why)) => Err(why.clone()),
            _ => Ok((told, data)),
        }
    }

    /// What plugins do on their own (each message with its `event`), from now on.
    pub fn events(&self) -> Receiver<Answer> {
        let (tell, heard) = channel();
        if let Ok(mut events) = self.inner.events.lock() {
            *events = Some(tell);
        }
        heard
    }

    /// The answer to what a plugin asked (an event with its `call`).
    pub fn answer(&self, call: &Value, value: Value) {
        let mut message = Dict::new();
        message.insert("answer".into(), call.clone());
        message.insert("value".into(), value);
        let _ = self.inner.send(message, &[]);
    }

    /// The names of the parts of `kind` the plugins bring.
    pub fn names(&self, kind: &str) -> Vec<String> {
        self.parts()
            .iter()
            .filter(|part| text(part, "kind") == kind)
            .map(|part| text(part, "name"))
            .collect()
    }

    /// The parts found, as `(kind, name, source)`.
    pub fn parts(&self) -> Vec<Dict> {
        match self.hello.get("parts") {
            Some(Value::List(parts)) => parts
                .iter()
                .filter_map(|part| match part {
                    Value::Dict(part) => Some(part.clone()),
                    _ => None,
                })
                .collect(),
            _ => Vec::new(),
        }
    }

    /// The folders of the theme packages plugins bring.
    pub fn theme_folders(&self) -> Vec<PathBuf> {
        self.parts()
            .iter()
            .filter(|part| text(part, "kind") == "themes")
            .filter_map(|part| match part.get("folder") {
                Some(Value::Str(folder)) => Some(PathBuf::from(folder)),
                _ => None,
            })
            .collect()
    }
}

impl Inner {
    fn send(&self, message: Dict, blob: &[u8]) -> Result<(), String> {
        let mut message = message;
        if !blob.is_empty() {
            message.insert("blob".into(), Value::Int((blob.len() as u64).into()));
        }
        let body = lp_py::json::dumps(&Value::Dict(message), &lp_py::json::Dumps::default());
        let mut out = Vec::with_capacity(4 + body.len() + blob.len());
        out.extend_from_slice(&(body.len() as u32).to_le_bytes());
        out.extend_from_slice(body.as_bytes());
        out.extend_from_slice(blob);
        let mut stdin = self
            .stdin
            .lock()
            .map_err(|_| "the plugin host's lock is broken")?;
        stdin
            .write_all(&out)
            .and_then(|()| stdin.flush())
            .map_err(|why| format!("the plugin host does not listen: {why}"))
    }

    fn deliver(&self, (message, blob): Answer) {
        match message.get("id") {
            Some(Value::Int(id)) => {
                let id = u64::try_from(id.clone()).unwrap_or(0);
                let tell = self.waiting.lock().ok().and_then(|mut w| w.remove(&id));
                if let Some(tell) = tell {
                    let _ = tell.send((message, blob));
                }
            }
            _ => {
                if let Some(tell) = self.events.lock().ok().and_then(|e| e.clone()) {
                    let _ = tell.send((message, blob));
                }
            }
        }
    }
}

impl Drop for PluginHost {
    fn drop(&mut self) {
        if let Ok(mut child) = self.inner.child.lock() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// One message from `from`: none at its end.
fn receive(from: &mut impl Read) -> Result<Option<Answer>, String> {
    let mut head = [0u8; 4];
    match from.read_exact(&mut head) {
        Ok(()) => {}
        Err(why) if why.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(why) => return Err(why.to_string()),
    }
    let mut body = vec![0u8; u32::from_le_bytes(head) as usize];
    from.read_exact(&mut body).map_err(|why| why.to_string())?;
    let text = String::from_utf8(body).map_err(|why| why.to_string())?;
    let Value::Dict(message) = lp_py::json::loads(&text).map_err(|why| why.msg)? else {
        return Err("not an object".into());
    };
    let size = match message.get("blob") {
        Some(Value::Int(size)) => usize::try_from(size.clone()).unwrap_or(0),
        _ => 0,
    };
    let mut blob = vec![0u8; size];
    from.read_exact(&mut blob).map_err(|why| why.to_string())?;
    Ok(Some((message, blob)))
}

/// `part[key]` as text ("" for none).
pub fn text(part: &Dict, key: &str) -> String {
    match part.get(key) {
        Some(Value::Str(text)) => text.clone(),
        _ => String::new(),
    }
}

/// The plugin host of this process, or why it is not there.
pub type Shared = Result<Arc<PluginHost>, String>;

/// The plugin host of this process (started on first use), or why there is none; none at all
/// where no plugin is installed.
pub fn shared(settings: &Path) -> Option<Shared> {
    static HOST: OnceLock<Mutex<Option<Shared>>> = OnceLock::new();
    let held = HOST.get_or_init(|| Mutex::new(None));
    let mut held = held.lock().ok()?;
    if held.is_none() {
        match PluginHost::start(settings) {
            Ok(None) => return None,
            Ok(Some(host)) => *held = Some(Ok(Arc::new(host))),
            Err(why) => {
                crate::logs::say("libre_panel.plugins", &format!("plugins: {why}"));
                *held = Some(Err(why));
            }
        }
    }
    held.clone()
}

/// The theme folders the installed plugins bring (none without plugins).
pub fn theme_folders(settings: &Path) -> Vec<PathBuf> {
    match shared(settings) {
        Some(Ok(host)) => host.theme_folders(),
        _ => Vec::new(),
    }
}

/// `libre-panel plugins` (cli.py's _cmd_plugins): what is installed, where it came from and
/// whether it loads; false where something failed.
pub fn list(settings: &Path) -> bool {
    let root = settings.join(PLUGINS_DIRNAME);
    println!(
        "plugin API {API_VERSION}; plugin folders go into {}",
        root.display()
    );
    let mut rows: Vec<(String, String, String, String)> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    for (kind, names) in BUILT_IN_PARTS {
        for name in names {
            rows.push((kind.into(), (*name).into(), String::new(), BUILT_IN.into()));
        }
    }
    match shared(settings) {
        None => {}
        Some(Ok(host)) => match host.request("check", Dict::new(), &[]) {
            Ok((told, _)) => {
                if let Some(Value::List(parts)) = told.get("parts") {
                    for part in parts {
                        if let Value::Dict(part) = part {
                            let (kind, name) = (text(part, "kind"), text(part, "name"));
                            rows.push((kind, name, text(part, "error"), text(part, "source")));
                        }
                    }
                }
                if let Some(Value::List(said)) = told.get("errors") {
                    errors.extend(said.iter().filter_map(|e| e.as_str().map(str::to_string)));
                }
            }
            Err(why) => errors.push(why),
        },
        Some(Err(why)) => errors.push(why),
    }
    if rows.iter().all(|row| row.3 == BUILT_IN) {
        println!("no plugins installed");
    }
    // (registry.all(): the kinds in order, each kind's parts as they were found)
    let mut kinds: Vec<String> = rows.iter().map(|row| row.0.clone()).collect();
    kinds.sort_by(|a, b| a.chars().cmp(b.chars()));
    kinds.dedup();
    for kind in &kinds {
        for (_, name, error, source) in rows.iter().filter(|row| &row.0 == kind) {
            let state = if error.is_empty() {
                "ok".to_string()
            } else {
                format!("FAILED: {error}")
            };
            println!("{kind:<10} {name:<24} {state:<6} {source}");
        }
    }
    for error in &errors {
        println!("error: {error}");
    }
    errors.is_empty() && rows.iter().all(|row| row.2.is_empty())
}

// -- what passes between the app and the plugins ------------------------------------------------

/// A field of the fields of a message.
fn field<'a>(message: &'a Dict, key: &str) -> &'a Value {
    static NONE: Value = Value::None;
    message.get(key).unwrap_or(&NONE)
}

fn opt_text(value: &Value) -> Option<String> {
    value.as_str().map(str::to_string)
}

/// A reading as the plugin host sends one.
fn reading_of(told: &Dict) -> (String, lp_render::Reading) {
    let mut reading = lp_render::Reading::of(field(told, "value").clone());
    reading.unit = text(told, "unit");
    reading.label = text(told, "label");
    reading.origin = text(told, "origin");
    reading.device = text(told, "device");
    (text(told, "key"), reading)
}

/// A reading as the plugin host takes one.
fn reading_dict(key: &str, reading: &lp_render::Reading) -> Value {
    let mut told = Dict::new();
    told.insert("key".into(), Value::from(key));
    let value = match &reading.value {
        Value::Float(f) if !f.is_finite() => Value::None, // (JSON has no NaN)
        other => other.clone(),
    };
    told.insert("value".into(), value);
    told.insert("unit".into(), Value::from(reading.unit.as_str()));
    told.insert("label".into(), Value::from(reading.label.as_str()));
    told.insert("origin".into(), Value::from(reading.origin.as_str()));
    told.insert(
        "stamp".into(),
        reading.stamp.map_or(Value::None, Value::Float),
    );
    told.insert("device".into(), Value::from(reading.device.as_str()));
    Value::Dict(told)
}

/// A snapshot as the plugin host takes one: its readings, history and moment (its pictures stay).
fn snapshot_dict(snapshot: &lp_render::Snapshot) -> Value {
    let mut told = Dict::new();
    let readings = snapshot
        .readings
        .iter()
        .map(|(key, reading)| reading_dict(key, reading))
        .collect();
    told.insert("readings".into(), Value::List(readings));
    let history = snapshot
        .history
        .iter()
        .map(|(key, values)| {
            let values = values
                .iter()
                .map(|v| {
                    if v.is_finite() {
                        Value::Float(*v)
                    } else {
                        Value::None
                    }
                })
                .collect();
            (key.clone(), Value::List(values))
        })
        .collect();
    told.insert("history".into(), Value::Dict(history));
    let now = &snapshot.now;
    told.insert(
        "now".into(),
        Value::Str(format!(
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:06}",
            now.year, now.month, now.day, now.hour, now.minute, now.second, now.microsecond
        )),
    );
    Value::Dict(told)
}

/// Pictures as the plugin host sends them: their keys and sizes, and the RGBA pixels of each
/// after the one before.
fn pictures_of(told: &Dict, blob: &[u8]) -> Vec<(String, lp_pixels::Image)> {
    let mut found = Vec::new();
    let mut at = 0;
    let Value::List(listed) = field(told, "images") else {
        return found;
    };
    for item in listed {
        let Value::Dict(item) = item else { continue };
        let Some((w, h)) = size_of(field(item, "size")) else {
            continue;
        };
        let end = at + w * h * 4;
        let Some(data) = blob.get(at..end) else { break };
        at = end;
        if let Ok(image) = lp_pixels::Image::from_bytes(lp_pixels::Mode::RGBA, w, h, data.to_vec())
        {
            found.push((text(item, "key"), image));
        }
    }
    found
}

fn size_of(value: &Value) -> Option<(usize, usize)> {
    let Value::List(size) = value else {
        return None;
    };
    let number = |v: &Value| match v {
        Value::Int(n) => usize::try_from(n.clone()).ok(),
        _ => None,
    };
    Some((number(size.first()?)?, number(size.get(1)?)?))
}

/// A transition as the plugin host sends one: a name, [name, parameters] or none.
fn transition_of(value: &Value) -> Option<lp_services::host::Transition> {
    match value {
        Value::Str(name) => Some(lp_services::host::Transition::named(name)),
        Value::List(both) => {
            let name = both.first()?.as_str()?;
            let mut transition = lp_services::host::Transition::named(name);
            if let Some(Value::Dict(params)) = both.get(1) {
                transition.params = Some(params.clone());
            }
            Some(transition)
        }
        _ => None,
    }
}

/// What a plugin did, done in the app's services host; a toast's delivery answered.
fn act(host: &PluginHost, hub: &lp_services::host::PluginHost, message: &Dict, blob: &[u8]) {
    let owner = opt_text(field(message, "owner"));
    let owner = owner.as_deref();
    match text(message, "event").as_str() {
        "publish" => {
            if let Value::Dict(told) = field(message, "reading") {
                let (key, reading) = reading_of(told);
                hub.publish(&key, reading, owner);
            }
        }
        "state" => {
            let key = text(message, "key");
            let _ = hub.publish_state(
                &key,
                field(message, "value"),
                owner,
                &text(message, "label"),
            );
        }
        "unpublish" => hub.unpublish(&text(message, "key")),
        "want" => {
            let keys = match field(message, "keys") {
                Value::List(keys) => keys.iter().filter_map(opt_text).collect(),
                _ => Vec::new(),
            };
            hub.want(owner.unwrap_or_default(), keys);
        }
        "image" => {
            let key = text(message, "key");
            let image = size_of(field(message, "size")).and_then(|(w, h)| {
                lp_pixels::Image::from_bytes(lp_pixels::Mode::RGBA, w, h, blob.to_vec()).ok()
            });
            hub.publish_image(&key, image, owner);
        }
        "theme" => {
            use lp_services::host::Scope;
            let scope = match field(message, "mode") {
                Value::Str(mode) if mode == "*" => Scope::Any, // (ANY_MODE)
                Value::Str(mode) => Scope::Mode(Some(mode.clone())),
                _ => Scope::Mode(None),
            };
            let priority = match field(message, "priority") {
                Value::Int(n) => i64::try_from(n.clone()).unwrap_or(0),
                _ => 0,
            };
            hub.request_theme(
                opt_text(field(message, "name")).as_deref(),
                &text(message, "by"),
                transition_of(field(message, "transition")),
                priority,
                scope,
            );
        }
        "mode" => hub.set_mode(
            opt_text(field(message, "name")).as_deref(),
            opt_text(field(message, "by")).as_deref(),
            transition_of(field(message, "transition")),
        ),
        "forget" => hub.forget(owner.unwrap_or_default()),
        "notify" => {
            let Value::Dict(told) = field(message, "toast") else {
                return;
            };
            let mut toast = lp_render::overlays::Toast::new(text(told, "text"));
            toast.icon = opt_text(field(told, "icon"));
            toast.level = text(told, "level");
            toast.seconds = match field(told, "seconds") {
                Value::Float(f) => Some(*f),
                Value::Int(n) => lp_py::num::int_to_float(n).ok(),
                _ => None,
            };
            toast.kind = text(told, "kind");
            toast.rank = match field(told, "rank") {
                Value::Int(n) => i64::try_from(n.clone()).unwrap_or(0),
                _ => 0,
            };
            if let Value::Dict(payload) = field(told, "payload") {
                toast.payload = payload.clone();
            }
            toast.service = text(told, "service");
            toast.key = text(told, "key");
            let delivery = hub.notify(toast);
            let mut said = Dict::new();
            said.insert("shown".into(), Value::Bool(delivery.shown));
            said.insert("reason".into(), Value::from(delivery.reason.as_str()));
            host.answer(field(message, "call"), Value::Dict(said));
        }
        _ => {}
    }
}

/// An event of the app as the plugin host takes it.
fn event_message(event: &lp_services::host::Event) -> Dict {
    use lp_services::host::Event;
    let mut data = Dict::new();
    match event {
        Event::ThemeChanged { theme } => {
            data.insert("theme".into(), Value::from(theme.as_str()));
        }
        Event::ModeChanged { mode } => {
            data.insert("mode".into(), mode.clone().map_or(Value::None, Value::Str));
        }
        Event::Sample { snapshot } => {
            data.insert("snapshot".into(), snapshot_dict(snapshot));
        }
        Event::Fresh { snapshot, keys } => {
            data.insert("snapshot".into(), snapshot_dict(snapshot));
            let keys = keys.iter().map(|k| Value::from(k.as_str())).collect();
            data.insert("keys".into(), Value::List(keys));
        }
        Event::Shortcut { action } => {
            data.insert("action".into(), Value::from(action.as_str()));
        }
        _ => {}
    }
    let mut message = Dict::new();
    message.insert("event".into(), Value::from(event.name()));
    message.insert("data".into(), Value::Dict(data));
    message
}

/// The plugins' services, run in the plugin host (lp-services' Remote).
pub struct RemoteServices {
    host: Arc<PluginHost>,
    names: Vec<String>,
}

impl RemoteServices {
    /// The plugins' services of `host`, which act on `hub`: what they do is done there, and
    /// what happens there they hear. `config` is config.toml, which their sources follow.
    pub fn new(
        host: Arc<PluginHost>,
        hub: &Arc<lp_services::host::PluginHost>,
        config: Option<&Path>,
    ) -> RemoteServices {
        if let Some(path) = config {
            let mut fields = Dict::new();
            fields.insert(
                "path".into(),
                Value::Str(path.to_string_lossy().into_owned()),
            );
            let _ = host.request("config", fields, &[]);
        }
        let heard = host.events();
        let acting = (Arc::clone(&host), Arc::clone(hub));
        let _ = std::thread::Builder::new()
            .name("plugin-events".into())
            .spawn(move || {
                for (message, blob) in heard {
                    act(&acting.0, &acting.1, &message, &blob);
                }
            });
        for event in lp_services::host::EVENTS {
            let telling = Arc::clone(&host);
            let tellings = Arc::clone(hub);
            let told = Box::new(move |event: &lp_services::host::Event| {
                let mut fields = event_message(event);
                fields.insert(
                    "mode".into(),
                    tellings.mode().map_or(Value::None, Value::Str),
                );
                let _ = telling.request("emit", fields, &[]);
            });
            let _ = hub.listen(event, told, Some(PLUGINS_OWNER));
        }
        let names = host.names("services");
        RemoteServices { host, names }
    }
}

/// who hears the app's events for the plugins' services
const PLUGINS_OWNER: &str = "plugin-host";

impl lp_services::manager::Remote for RemoteServices {
    fn names(&self) -> Vec<String> {
        self.names.clone()
    }

    fn apply(
        &mut self,
        enabled: &[String],
        options: &indexmap::IndexMap<String, Dict>,
    ) -> indexmap::IndexMap<String, String> {
        let mut fields = Dict::new();
        let enabled = enabled.iter().map(|n| Value::from(n.as_str())).collect();
        fields.insert("enabled".into(), Value::List(enabled));
        let options = options
            .iter()
            .map(|(name, table)| (name.clone(), Value::Dict(table.clone())))
            .collect();
        fields.insert("options".into(), Value::Dict(options));
        match self.host.request("services.apply", fields, &[]) {
            Ok((told, _)) => match field(&told, "state") {
                Value::Dict(states) => states
                    .iter()
                    .map(|(name, state)| (name.clone(), state.as_str().unwrap_or("").into()))
                    .collect(),
                _ => indexmap::IndexMap::new(),
            },
            Err(why) => {
                crate::logs::say("libre_panel.plugins", &format!("plugin services: {why}"));
                indexmap::IndexMap::new()
            }
        }
    }

    fn restart(&mut self, name: &str) -> Result<String, String> {
        let mut fields = Dict::new();
        fields.insert("name".into(), Value::from(name));
        let (told, _) = self.host.request("services.restart", fields, &[])?;
        Ok(text(&told, "state"))
    }

    fn summary(&self, name: &str) -> Option<Value> {
        let mut fields = Dict::new();
        fields.insert("name".into(), Value::from(name));
        let (told, _) = self.host.request("services.summary", fields, &[]).ok()?;
        match field(&told, "summary") {
            Value::None => None,
            said => Some(said.clone()),
        }
    }

    fn stop(&mut self) {
        let _ = self.host.request("services.stop", Dict::new(), &[]);
    }
}

/// A sensor source a plugin brings (`[sensors] providers`), read in the plugin host.
pub struct PluginSource {
    host: Arc<PluginHost>,
    name: &'static str,
    every: crate::sensors::Every,
    overrides: &'static [&'static str],
    wanted: lp_sensors::service::Wanted,
    notes: Vec<String>,
    /// the pictures as the source last held them, and the number that says which they are
    images: (u64, indexmap::IndexMap<String, lp_pixels::Image>),
}

impl PluginSource {
    /// The plugin's source `name` with its `[sensors.<name>]` `options`; why it is not there
    /// where it is not.
    pub fn open(
        settings: &Path,
        name: &str,
        options: Dict,
        config: Option<&Path>,
        wanted: lp_sensors::service::Wanted,
    ) -> Option<Result<PluginSource, String>> {
        let host = match shared(settings)? {
            Ok(host) => host,
            Err(why) => return Some(Err(why)),
        };
        if !host.names("sensors").iter().any(|known| known == name) {
            return None;
        }
        if let Some(path) = config {
            let mut fields = Dict::new();
            fields.insert(
                "path".into(),
                Value::Str(path.to_string_lossy().into_owned()),
            );
            let _ = host.request("config", fields, &[]);
        }
        let mut fields = Dict::new();
        fields.insert("name".into(), Value::from(name));
        fields.insert("options".into(), Value::Dict(options));
        let opened = host.request("sensors.open", fields, &[]).map(|(told, _)| {
            let every = match field(&told, "every_frame") {
                Value::Bool(true) => crate::sensors::Every::Frame,
                Value::Str(how) if how == "video" => crate::sensors::Every::Video,
                _ => crate::sensors::Every::No,
            };
            let overrides: Vec<&'static str> = match field(&told, "overrides") {
                Value::List(keys) => keys
                    .iter()
                    .filter_map(Value::as_str)
                    .map(|key| &*Box::leak(key.to_string().into_boxed_str()))
                    .collect(),
                _ => Vec::new(),
            };
            PluginSource {
                host: Arc::clone(&host),
                name: Box::leak(name.to_string().into_boxed_str()),
                every,
                overrides: Box::leak(overrides.into_boxed_slice()),
                wanted,
                notes: Vec::new(),
                images: (0, indexmap::IndexMap::new()),
            }
        });
        Some(opened.map_err(|why| format!("sensor provider {name:?} did not load: {why}")))
    }

    fn named(&self) -> Dict {
        let mut fields = Dict::new();
        fields.insert("name".into(), Value::from(self.name));
        fields
    }
}

impl crate::sensors::Source for PluginSource {
    fn name(&self) -> &'static str {
        self.name
    }

    fn read(&mut self, out: &mut crate::sensors::Readings) {
        let mut fields = self.named();
        let wanted = self
            .wanted
            .keys()
            .iter()
            .map(|k| Value::from(k.as_str()))
            .collect();
        fields.insert("wanted".into(), Value::List(wanted));
        match self.host.request("sensors.read", fields, &[]) {
            Ok((told, _)) => {
                if let Value::List(readings) = field(&told, "readings") {
                    for reading in readings {
                        if let Value::Dict(reading) = reading {
                            let (key, reading) = reading_of(reading);
                            out.insert(key, reading);
                        }
                    }
                }
                let held = match field(&told, "images") {
                    Value::Int(n) => u64::try_from(n.clone()).unwrap_or(0),
                    _ => 0,
                };
                if held != self.images.0 {
                    let pictures = match self.host.request("sensors.images", self.named(), &[]) {
                        Ok((told, blob)) => pictures_of(&told, &blob).into_iter().collect(),
                        Err(_) => indexmap::IndexMap::new(),
                    };
                    self.images = (held, pictures);
                }
            }
            Err(why) => self
                .notes
                .push(format!("sensor source {}: {why}", self.name)),
        }
    }

    fn notes(&mut self) -> Vec<String> {
        std::mem::take(&mut self.notes)
    }

    fn every_frame(&self) -> crate::sensors::Every {
        self.every
    }

    fn overrides(&self) -> &'static [&'static str] {
        self.overrides
    }

    fn images(&self) -> indexmap::IndexMap<String, lp_pixels::Image> {
        self.images.1.clone()
    }

    fn images_state(&self) -> u64 {
        self.images.0
    }
}

impl Drop for PluginSource {
    fn drop(&mut self) {
        let _ = self.host.request("sensors.close", self.named(), &[]);
    }
}
