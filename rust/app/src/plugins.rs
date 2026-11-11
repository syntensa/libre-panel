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
use std::sync::mpsc::{channel, Sender};
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
            .stderr(Stdio::inherit());
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
