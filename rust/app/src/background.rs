//! Libre Panel as a background app (service.py): the panel's loop in a thread that can be
//! paused and resumed, the services and the keyboard shortcuts beside it, and the controls
//! of them (`libre-panel start`, in a terminal).

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, Weak};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use lp_config::Config;
use lp_i18n::t;
use lp_py::{Dict, Value};
use lp_services::host::Event;

use crate::access;
use crate::company::Company;
use crate::home::Home;
use crate::hotkeys::{self, Hotkeys, BRIGHTNESS_STEP};
use crate::mainloop::{Asked, MainLoop, RunStatus};
use crate::server::EditorServer;

/// in the settings folder: what the running app says of itself (its pid, its address)
const INSTANCE_FILENAME: &str = "instance.json";

fn say(message: &str) {
    crate::logs::say("libre_panel.service", message);
}

fn stamp_of(path: &std::path::Path) -> Option<std::time::SystemTime> {
    std::fs::metadata(path).and_then(|m| m.modified()).ok()
}

/// A loop's thread: how it is told to stop, and whether it has ended.
struct Running {
    thread: JoinHandle<()>,
    stop: Arc<AtomicBool>,
    done: Arc<AtomicBool>,
}

#[derive(Default)]
struct Loops {
    running: Option<Running>,
    /// a loop that did not stop in time: the next one waits for it, never two loops on one
    /// panel
    lingering: Option<Running>,
}

/// Runs the main loop in a thread that can be paused and resumed (PanelService).
///
/// A broken config does not end the app: it waits until the file is fixed and starts again.
pub struct PanelService {
    home: Home,
    asked: Asked,
    company: Arc<Company>,
    loops: Mutex<Loops>,
    paused: AtomicBool,
    /// Libre Panel quits (stop()): the panel may go dark; a pause or a restart leaves it
    ending: Arc<AtomicBool>,
    error: Arc<Mutex<String>>,
    status: Arc<Mutex<RunStatus>>,
}

/// how long a broken config.toml is waited for, where it does not change
const RETRY_S: u32 = 30;
/// how long a stop waits for the loop to let the panel go
const HALT_S: f64 = 10.0;
/// how long a new loop waits for an old one that did not stop in time
const LINGER_S: f64 = 30.0;

fn wait_for(done: &AtomicBool, seconds: f64) -> bool {
    let until = Instant::now() + Duration::from_secs_f64(seconds);
    while !done.load(Ordering::Relaxed) && Instant::now() < until {
        std::thread::sleep(Duration::from_millis(10));
    }
    done.load(Ordering::Relaxed)
}

impl PanelService {
    pub fn new(home: Home, asked: Asked, company: Arc<Company>) -> PanelService {
        PanelService {
            home,
            asked,
            company,
            loops: Mutex::default(),
            paused: AtomicBool::new(false),
            ending: Arc::default(),
            error: Arc::default(),
            status: Arc::default(),
        }
    }

    pub fn paused(&self) -> bool {
        self.paused.load(Ordering::Relaxed)
    }

    pub fn running(&self) -> bool {
        let loops = self.loops.lock();
        loops.is_ok_and(|loops| {
            (loops.running.as_ref()).is_some_and(|r| !r.done.load(Ordering::Relaxed))
        })
    }

    pub fn start(&self) {
        let Ok(mut loops) = self.loops.lock() else {
            return;
        };
        if (loops.running.as_ref()).is_some_and(|r| !r.done.load(Ordering::Relaxed)) {
            return;
        }
        if let Some(lingering) = loops.lingering.take() {
            // (it still has the panel's port)
            if !wait_for(&lingering.done, LINGER_S) {
                say("the old panel loop still runs; the panel starts after it");
                loops.lingering = Some(lingering);
                return;
            }
            let _ = lingering.thread.join();
        }
        self.paused.store(false, Ordering::Relaxed);
        let (stop, done) = (
            Arc::new(AtomicBool::new(false)),
            Arc::new(AtomicBool::new(false)),
        );
        let work = Work {
            home: self.home.clone(),
            asked: self.asked.clone(),
            company: Arc::clone(&self.company),
            ending: Arc::clone(&self.ending),
            error: Arc::clone(&self.error),
            status: Arc::clone(&self.status),
            stop: Arc::clone(&stop),
        };
        let ended = Arc::clone(&done);
        let thread = std::thread::Builder::new().name("panel".into());
        let thread = thread.spawn(move || {
            work.run();
            ended.store(true, Ordering::Relaxed);
        });
        match thread {
            Ok(thread) => loops.running = Some(Running { thread, stop, done }),
            Err(why) => say(&format!("the panel's thread did not start: {why}")),
        }
    }

    /// _halt: stop the loop; whether it stopped (it lets the panel go as it does).
    fn halt(&self) -> bool {
        let running = match self.loops.lock() {
            Ok(mut loops) => loops.running.take(),
            Err(_) => None,
        };
        let Some(running) = running else {
            return true;
        };
        running.stop.store(true, Ordering::Relaxed);
        if wait_for(&running.done, HALT_S) {
            let _ = running.thread.join();
            return true;
        }
        say("the panel loop did not stop in time");
        if let Ok(mut loops) = self.loops.lock() {
            loops.lingering = Some(running);
        }
        false
    }

    /// restart: open the panels anew without restarting Libre Panel, for one that hangs: the
    /// loop lets every panel go and starts again, and a paused panel runs again. An error
    /// where the loop does not let a panel go (then only a new start of Libre Panel helps).
    pub fn restart(&self) -> Result<(), String> {
        let stuck = || t("A panel does not let go: quit Libre Panel and start it again.");
        if !self.halt() {
            return Err(stuck());
        }
        say("the panels start anew");
        self.start();
        // (the loop of an earlier try still hangs on a panel)
        self.running().then_some(()).ok_or_else(stuck)
    }

    /// pause: stop sending frames and release the panel (another program may use it).
    pub fn pause(&self) {
        self.paused.store(true, Ordering::Relaxed);
        self.halt();
    }

    pub fn resume(&self) {
        self.start();
    }

    /// stop: Libre Panel quits: the loop ends, and the panel goes dark where config.toml
    /// says so (`[device] dark_on_quit`); pausing leaves it as it is.
    pub fn stop(&self) {
        self.ending.store(true, Ordering::Relaxed);
        self.halt();
    }

    /// Which run of the loop this is (0: none runs): a new start makes the sensors anew.
    pub fn run(&self) -> u64 {
        self.status.lock().map_or(0, |status| status.run)
    }

    /// state: what the panel does: `state` ("paused", "error", "stopped", or the loop's:
    /// "starting", "waiting", "showing"), the error's `detail`, the `theme` on show and the
    /// `frames` shown.
    pub fn state(&self) -> Dict {
        let status = self.status.lock().map(|s| s.clone()).unwrap_or_default();
        let error = self.error.lock().map(|e| e.clone()).unwrap_or_default();
        let state = if self.paused() {
            "paused"
        } else if !error.is_empty() {
            "error"
        } else if !self.running() || status.state.is_empty() {
            "stopped"
        } else {
            status.state
        };
        let mut told = Dict::new();
        told.insert("state".into(), Value::Str(state.into()));
        told.insert("detail".into(), Value::Str(error));
        told.insert("theme".into(), Value::Str(status.theme));
        told.insert("frames".into(), Value::int(status.frames as i64));
        // the size of the panel's frames: the app shows each theme in its version for it
        let size = status.size.map_or(Value::None, |(width, height)| {
            Value::List(vec![Value::int(width), Value::int(height)])
        });
        told.insert("size".into(), size);
        // where the screens in turn stand: the step, its theme and the seconds left of it
        let sequence = match (state, status.sequence) {
            ("showing", Some((index, theme, left_s))) => {
                let mut at = Dict::new();
                at.insert("index".into(), Value::int(index as i64));
                at.insert("theme".into(), Value::Str(theme));
                at.insert(
                    "left_s".into(),
                    Value::Float(lp_py::round_digits(left_s, 1)),
                );
                Value::Dict(at)
            }
            _ => Value::None,
        };
        told.insert("sequence".into(), sequence);
        told
    }
}

/// What a loop's thread has of its service.
struct Work {
    home: Home,
    asked: Asked,
    company: Arc<Company>,
    ending: Arc<AtomicBool>,
    error: Arc<Mutex<String>>,
    status: Arc<Mutex<RunStatus>>,
    stop: Arc<AtomicBool>,
}

impl Work {
    fn error(&self, text: &str) {
        if let Ok(mut error) = self.error.lock() {
            *error = text.to_string();
        }
    }

    fn run(&self) {
        let config_file = (self.asked.config.clone()).unwrap_or_else(|| self.home.config_file());
        while !self.stop.load(Ordering::Relaxed) {
            // what the file is like before it is read: a fix written while the error is said
            // is a change to it
            let stamp = stamp_of(&config_file);
            let (home, asked) = (self.home.clone(), self.asked.clone());
            let company = Arc::clone(&self.company);
            match MainLoop::with(home, asked, company, false, Arc::clone(&self.status)) {
                Ok(mut made) => {
                    self.error("");
                    made.run(&self.stop, &|| self.ending.load(Ordering::Relaxed));
                    if self.asked.frames.is_some() {
                        return; // (the frames asked for are shown)
                    }
                }
                Err(why) => {
                    self.error(&why);
                    say(&format!(
                        "panel stopped: {why} (waiting for the config or theme to change)"
                    ));
                    for _ in 0..RETRY_S * 10 {
                        if self.stop.load(Ordering::Relaxed) || stamp_of(&config_file) != stamp {
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(100));
                    }
                }
            }
        }
    }
}

/// _held: the shortcuts to hold: those of config.toml, none while they are switched off.
fn held(config: &Config) -> indexmap::IndexMap<String, String> {
    match config.hotkeys_enabled {
        true => config.hotkeys.clone(),
        false => indexmap::IndexMap::new(),
    }
}

/// The panel loop with the services and the shortcuts, and their controls (BackgroundApp).
pub struct BackgroundApp {
    home: Home,
    config_file: PathBuf,
    /// the port asked for the app's server (another is used if it is taken)
    port: u16,
    company: Arc<Company>,
    pub panel: PanelService,
    pub hotkeys: Arc<Hotkeys>,
    /// the app's server, while it serves
    editor: Mutex<Option<EditorServer>>,
    own: Weak<BackgroundApp>,
    /// set when the app is asked to quit
    pub quit_requested: AtomicBool,
}

impl BackgroundApp {
    pub fn new(home: Home, asked: Asked) -> Arc<BackgroundApp> {
        let config_file = asked.config.clone().unwrap_or_else(|| home.config_file());
        let company = Company::new();
        Arc::new_cyclic(|app: &Weak<BackgroundApp>| {
            let pressed = app.clone();
            let act: hotkeys::Fire = Arc::new(move |action: &str| {
                if let Some(app) = pressed.upgrade() {
                    app.shortcut(action);
                }
            });
            let hotkeys = Arc::new(Hotkeys::new(act, hotkeys::backend(), hotkeys::support));
            // (in the background, so that taking the keys never stalls the panel)
            let applies = Arc::clone(&hotkeys);
            company.set_also(Some(Box::new(move |config| {
                let (hotkeys, named) = (Arc::clone(&applies), held(config));
                let thread = std::thread::Builder::new().name("hotkeys-apply".into());
                let _ = thread.spawn(move || hotkeys.apply(&named));
            })));
            BackgroundApp {
                config_file,
                port: asked.port.unwrap_or(crate::server::PORT),
                panel: PanelService::new(home.clone(), asked, Arc::clone(&company)),
                home,
                company,
                hotkeys,
                editor: Mutex::new(None),
                own: app.clone(),
                quit_requested: AtomicBool::new(false),
            }
        })
    }

    fn config(&self) -> Result<Config, lp_config::Error> {
        lp_config::load_config(&self.config_file, lp_config::platform())
    }

    /// _check_folders: with the highest rights Libre Panel runs what config.toml and the
    /// plugins name: the log says so when other users can change them.
    fn check_folders(&self) {
        if !access::elevated() {
            return;
        }
        let mut folders = vec![self.home.settings.clone()];
        let beside = std::fs::canonicalize(&self.config_file).ok();
        if let Some(folder) = beside.as_deref().and_then(std::path::Path::parent) {
            if !folders.iter().any(|known| known == folder) {
                folders.push(folder.to_path_buf());
            }
        }
        for folder in folders {
            let why = access::folder_risk(&folder, None);
            if !why.is_empty() {
                say(&format!(
                    "Libre Panel runs with administrator rights and runs what the settings and \
                     plugins in {} name, but {why}: any user who can change them can run a \
                     program with those rights",
                    folder.display()
                ));
            }
        }
    }

    /// start: the services, the shortcuts, the panel and the app's server; the server's
    /// address with its token (None where it did not start).
    pub fn start(&self) -> Option<String> {
        self.check_folders();
        match self.config() {
            Ok(config) => self.company.follow(&config),
            // (they start once the config is fixed)
            Err(why) => say(&format!("services not started: {}", why.msg)),
        }
        self.panel.start();
        self.start_editor()
    }

    fn instance_file(&self) -> PathBuf {
        self.home.settings.join(INSTANCE_FILENAME)
    }

    /// EditorService.start and _write_instance: the app's server on its port (any free one
    /// where that is taken), its token where the programs of this user find it, and its
    /// address in the settings folder for those that start later.
    fn start_editor(&self) -> Option<String> {
        let mut editor = self.editor.lock().ok()?;
        if editor.is_none() {
            let made = EditorServer::make_near(self.port, &self.home, &self.config_file, &self.own);
            let mut server = match made.and_then(|mut server| server.serve().map(|()| server)) {
                Ok(server) => server,
                Err(why) => {
                    say(&format!("the app's server did not start: {why}"));
                    return None;
                }
            };
            access::publish(&self.home.settings, &server.token, server.port);
            say(&format!("theme editor at {}", server.url()));
            let mut told = Dict::new();
            told.insert("pid".into(), Value::int(i64::from(std::process::id())));
            told.insert("editor".into(), Value::Str(server.url()));
            let text = lp_py::json::dumps(&Value::Dict(told), &lp_py::json::Dumps::default());
            let written = std::fs::create_dir_all(&self.home.settings)
                .and_then(|()| std::fs::write(self.instance_file(), text));
            if let Err(why) = written {
                server.shutdown();
                say(&format!("the app's address could not be written: {why}"));
                return None;
            }
            *editor = Some(server);
        }
        editor.as_ref().map(EditorServer::address)
    }

    fn stop_editor(&self) {
        let server = self.editor.lock().ok().and_then(|mut editor| editor.take());
        if let Some(mut server) = server {
            access::forget(&self.home.settings, &server.token);
            server.shutdown();
        }
        // (the address of this run only: a later run may have written its own)
        let written = std::fs::read_to_string(self.instance_file()).ok();
        let ours = written.and_then(|text| match lp_py::json::loads(&text).ok()? {
            Value::Dict(told) => told.get("pid").and_then(Value::to_f64),
            _ => None,
        });
        if ours == Some(f64::from(std::process::id())) {
            let _ = std::fs::remove_file(self.instance_file());
        }
    }

    /// follow_config: bring the services and the shortcuts in line with config.toml now. The
    /// panel loop does it when it sees a change; this is for who has just written one, so
    /// that they follow also while the panel is paused. The file is left as it is if it
    /// cannot be read.
    pub fn follow_config(&self) {
        if let Ok(config) = self.config() {
            self.company.follow(&config);
        }
    }

    pub fn shutdown(&self) {
        self.hotkeys.close();
        self.company.set_also(None);
        self.company.stop_services();
        self.panel.stop();
        self.stop_editor();
        self.company.close();
    }

    /// The readings the panel was last drawn from.
    pub fn latest(&self) -> Option<Arc<lp_render::Snapshot>> {
        self.company.host.latest()
    }

    /// upside_down: whether the panel's picture is turned round; None where config.toml
    /// cannot be read.
    pub fn upside_down(&self) -> Option<bool> {
        self.config().ok().map(|config| config.device.rotate == 180)
    }

    /// act: one control action from the app; the new snapshot.
    pub fn act(&self, action: &str, value: &Value) -> Result<Dict, String> {
        let platform = lp_config::platform();
        match action {
            "pause" => self.panel.pause(),
            "resume" => self.panel.resume(),
            // the panels opened anew, for one that hangs
            "restart" => self.panel.restart()?,
            "brightness" => match value {
                Value::Int(_) => {
                    let percent = value.to_f64().unwrap_or(-1.0);
                    let whole = (0.0..=100.0).contains(&percent).then_some(percent as i64);
                    self.set_brightness(whole.unwrap_or(-1))?;
                }
                _ => return Err("brightness must be a whole number".into()),
            },
            "upside_down" => match value {
                Value::Bool(on) => {
                    let turned = lp_py::toml::Toml::Int(if *on { 180 } else { 0 }.into());
                    lp_config::edit::set_config_value(
                        "device.rotate",
                        turned,
                        &self.config_file,
                        platform,
                    )
                    .map_err(|why| why.msg)?;
                }
                _ => return Err("upside_down must be true or false".into()),
            },
            "autostart" | "identify" | "show_transition" => {
                return Err(format!("{action} is not in this build yet"));
            }
            "quit" => {
                // (the request is answered first; the server stops as part of quitting)
                let app = self.own.clone();
                let thread = std::thread::Builder::new().name("quit".into());
                let _ = thread.spawn(move || {
                    std::thread::sleep(Duration::from_millis(300));
                    if let Some(app) = app.upgrade() {
                        app.quit_requested.store(true, Ordering::Relaxed);
                    }
                });
            }
            other => return Err(format!("unknown action {}", lp_py::repr(other))),
        }
        Ok(self.snapshot())
    }

    /// The panel's brightness as config.toml has it; None where the file cannot be read.
    pub fn brightness(&self) -> Option<i64> {
        self.config().ok().map(|config| config.device.brightness)
    }

    pub fn set_brightness(&self, percent: i64) -> Result<(), String> {
        if !(0..=100).contains(&percent) {
            return Err("brightness must be between 0 and 100".into());
        }
        // (the loop picks it up from the file)
        lp_config::edit::set_brightness(percent, &self.config_file, lp_config::platform())
            .map_err(|why| why.msg)
    }

    /// shortcut: what a keyboard shortcut does (hotkeys.rs): pause or resume the panel, turn
    /// its brightness up or down a step, open the app, put the next scene on, or mute the
    /// microphone; and the services hear of it (the timer starts, pauses or goes back).
    pub fn shortcut(&self, action: &str) {
        let event = Event::Shortcut {
            action: action.to_string(),
        };
        self.company.host.emit(event, false);
        let failed = |why: String| say(&format!("the shortcut for {action} failed: {why}"));
        match action {
            "pause" => match self.panel.paused() {
                true => self.panel.resume(),
                false => self.panel.pause(),
            },
            "brightness_up" | "brightness_down" => {
                let step = match action {
                    "brightness_up" => BRIGHTNESS_STEP,
                    _ => -BRIGHTNESS_STEP,
                };
                let now = self.brightness().unwrap_or(50);
                if let Err(why) = self.set_brightness((now + step).clamp(0, 100)) {
                    failed(why);
                }
            }
            "open" => say("the app's window is not in this build yet"),
            // the scene after the one that is on
            "next_scene" => {
                match lp_config::scenes::next_one(&self.config_file, lp_config::platform()) {
                    Ok(name) if name.is_empty() => {}
                    Ok(name) => {
                        say(&format!("scene: {name}"));
                        self.follow_config();
                    }
                    Err(why) if why.class == "ConfigError" => {
                        say(&format!("no next scene: {}", why.msg));
                    }
                    Err(why) => failed(why.msg),
                }
            }
            "mic_mute" => {
                if let Err(why) = lp_sensors::audio::toggle_microphone() {
                    say(&format!("audio: the microphone cannot be muted: {why}"));
                }
            }
            _ => {}
        }
    }

    /// snapshot: what the app shows of the panel and its controls.
    pub fn snapshot(&self) -> Dict {
        let mut told = Dict::new();
        told.insert("panel".into(), Value::Dict(self.panel.state()));
        let brightness = self.brightness().map_or(Value::None, Value::int);
        told.insert("brightness".into(), brightness);
        let turned = self.upside_down().map_or(Value::None, Value::Bool);
        told.insert("upside_down".into(), turned);
        told.insert("can_quit".into(), Value::Bool(true));
        told.insert("hotkeys".into(), Value::Dict(self.hotkeys.state()));
        told
    }

    /// The tests' way to the controls, until the app's server is here: lines on the standard
    /// input (`shortcut ACTION`, `press ACTION` as if its keys were pressed, `pause`,
    /// `resume`, `restart`, `state`: the snapshot as a line of JSON on the standard output).
    pub fn follow_input(self: &Arc<BackgroundApp>) {
        let app = Arc::clone(self);
        let thread = std::thread::Builder::new().name("control".into());
        let _ = thread.spawn(move || {
            for line in std::io::stdin().lines().map_while(Result::ok) {
                let mut words = line.split_whitespace();
                match (words.next().unwrap_or(""), words.next().unwrap_or("")) {
                    ("shortcut", action) => app.shortcut(action),
                    ("press", action) => app.hotkeys.press(action),
                    ("pause", _) => app.panel.pause(),
                    ("resume", _) => app.panel.resume(),
                    ("restart", _) => {
                        if let Err(why) = app.panel.restart() {
                            say(&why);
                        }
                    }
                    ("state", _) => {
                        let how = lp_py::json::Dumps::default();
                        println!("{}", lp_py::json::dumps(&Value::Dict(app.snapshot()), &how));
                    }
                    _ => {}
                }
            }
        });
    }
}
