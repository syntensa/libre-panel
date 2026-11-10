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

use crate::company::Company;
use crate::home::Home;
use crate::hotkeys::{self, Hotkeys, BRIGHTNESS_STEP};
use crate::mainloop::{Asked, MainLoop, RunStatus};

fn say(message: &str) {
    eprintln!("libre-panel: {message}");
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
    config_file: PathBuf,
    company: Arc<Company>,
    pub panel: PanelService,
    pub hotkeys: Arc<Hotkeys>,
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
                panel: PanelService::new(home, asked, Arc::clone(&company)),
                company,
                hotkeys,
            }
        })
    }

    fn config(&self) -> Result<Config, lp_config::Error> {
        lp_config::load_config(&self.config_file, lp_config::platform())
    }

    pub fn start(&self) {
        match self.config() {
            Ok(config) => self.company.follow(&config),
            // (they start once the config is fixed)
            Err(why) => say(&format!("services not started: {}", why.msg)),
        }
        self.panel.start();
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
        self.company.close();
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
