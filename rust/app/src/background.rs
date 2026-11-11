//! Libre Panel as a background app (service.py): the panel's loop in a thread that can be
//! paused and resumed, the services and the keyboard shortcuts beside it, and the controls
//! of them (`libre-panel start`, in a terminal).

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
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
use crate::mainloop::{Asked, Ending, MainLoop, RunStatus};
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
    /// the panel test has the panel (hold): the loop does not start until it gives it back
    held: AtomicBool,
    /// whether the loop was paused when the panel test took the panel
    before: AtomicBool,
    /// why the loop ends (Ending's number): Libre Panel quits (stop()) or the computer sleeps
    /// (sleep()), and the panel may go dark; a pause or a restart leaves it
    ending: Arc<AtomicU8>,
    /// the loop stopped for the computer's sleep: it starts again as it wakes
    asleep: AtomicBool,
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
            held: AtomicBool::new(false),
            before: AtomicBool::new(false),
            ending: Arc::default(),
            asleep: AtomicBool::new(false),
            error: Arc::default(),
            status: Arc::default(),
        }
    }

    /// The first panel's status (its `more` has the panels beside it).
    pub fn status(&self) -> Arc<Mutex<RunStatus>> {
        Arc::clone(&self.status)
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
        if self.held.load(Ordering::Relaxed) {
            say("the panel test has the panel: the panel starts after it");
            return;
        }
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

    /// hold: give the panel to the panel test (doctor.rs): the loop stops, releases the
    /// panel and does not start again, from the tray or a shortcut either, until `release`.
    /// Whether it ran; an error where the loop does not let the panel go.
    pub fn hold(&self) -> Result<bool, String> {
        let ran = self.running() && !self.paused();
        self.before.store(self.paused(), Ordering::Relaxed);
        self.held.store(true, Ordering::Relaxed);
        self.paused.store(true, Ordering::Relaxed);
        if !self.halt() {
            // it hangs on the panel: the test would talk to it at the same time
            self.held.store(false, Ordering::Relaxed);
            self.paused
                .store(self.before.load(Ordering::Relaxed), Ordering::Relaxed);
            return Err("the panel loop did not let the panel go; restart Libre Panel".into());
        }
        Ok(ran)
    }

    /// release: the panel test is done: the loop starts again where it ran before, and stays
    /// paused where it was.
    pub fn release(&self, resume: bool) {
        self.held.store(false, Ordering::Relaxed);
        self.paused
            .store(self.before.load(Ordering::Relaxed), Ordering::Relaxed);
        if resume {
            self.start();
        }
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
        self.ending.store(Ending::Quit.number(), Ordering::Relaxed);
        self.halt();
    }

    /// sleep: the computer goes to sleep: the loop ends, the panel goes dark where config.toml
    /// says so (`[device] dark_on_sleep`), and `wake` starts it again. A paused panel stays as
    /// it is. Whether it stopped.
    pub fn sleep(&self) -> bool {
        if !self.running() || self.paused() {
            return false;
        }
        self.ending.store(Ending::Sleep.number(), Ordering::Relaxed);
        self.asleep.store(true, Ordering::Relaxed);
        self.halt();
        self.ending.store(Ending::No.number(), Ordering::Relaxed);
        true
    }

    /// wake: the computer woke: the loop that stopped for its sleep starts again (whether).
    pub fn wake(&self) -> bool {
        if !self.asleep.swap(false, Ordering::Relaxed) {
            return false;
        }
        self.start();
        true
    }

    /// shown_frame: the frame on panel `n` now (1: `[device]`'s, 2 and on: those of
    /// `[[panels]]`), after transitions and messages, in the theme's orientation; None before
    /// its first frame and while it is paused. The loop keeps it only while it is asked for:
    /// the first time a frame is waited for (a loop at rest draws twice a second at least).
    pub fn shown_frame(&self, n: i64) -> Option<Arc<lp_pixels::Image>> {
        let status = match n {
            1 => Some(Arc::clone(&self.status)),
            _ => {
                let first = self.status.lock().ok()?;
                usize::try_from(n - 2)
                    .ok()
                    .and_then(|n| first.more.get(&n).cloned())
            }
        }?;
        let until = Instant::now() + Duration::from_millis(1500);
        loop {
            {
                let mut status = status.lock().ok()?;
                status.live_asked = Some(Instant::now());
                if status.shown.is_some() || status.state != "showing" || Instant::now() >= until {
                    return status.shown.clone();
                }
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    /// beside_theme: the theme the loop of the panel `[[panels]]` at `n` says it shows (""
    /// where it does not run, or says none).
    pub fn beside_theme(&self, n: usize) -> String {
        let beside = self
            .status
            .lock()
            .ok()
            .and_then(|first| first.more.get(&n).cloned());
        beside
            .and_then(|beside| beside.lock().ok().map(|status| status.theme.clone()))
            .unwrap_or_default()
    }

    /// told: what each panel's loop says of it (its model and the size of its frames): the
    /// first panel's, then those of the first `count` of `[[panels]]`; None for one whose
    /// loop does not run.
    pub fn told(&self, count: usize) -> Vec<Option<crate::arrangement::Told>> {
        let told = |status: &RunStatus| crate::arrangement::Told {
            model: status.model.clone(),
            size: status.size,
        };
        let Ok(first) = self.status.lock() else {
            return vec![None; count + 1];
        };
        let more: Vec<Option<Arc<Mutex<RunStatus>>>> =
            (0..count).map(|n| first.more.get(&n).cloned()).collect();
        let mut found = vec![Some(told(&first))];
        drop(first);
        for each in more {
            found.push(each.and_then(|each| each.lock().ok().map(|status| told(&status))));
        }
        found
    }

    /// sources: the sensor sources the panel reads, the first that has a reading winning: each
    /// one's name, how many readings of the last snapshot came from it and what it says of
    /// itself. None before the loop runs.
    pub fn sources(&self) -> Vec<(&'static str, usize, Option<Dict>)> {
        let hub = self
            .status
            .lock()
            .ok()
            .and_then(|status| status.hub.clone());
        let Some(hub) = hub else {
            return Vec::new();
        };
        let hub = hub
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        hub.sources()
    }

    /// How LibreHardwareMonitor is read, where a source of the loop reads it (load.rs).
    pub fn lhm_status(&self) -> Option<lp_sensors::service::LhmStatus> {
        let hub = self
            .status
            .lock()
            .ok()
            .and_then(|status| status.hub.clone())?;
        let hub = hub
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        hub.lhm_status()
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
        let detail = match (error.is_empty(), state) {
            (true, "waiting") => status.detail.clone(),
            _ => error,
        };
        told.insert("detail".into(), Value::Str(detail));
        let running = matches!(state, "showing" | "waiting");
        // what to do when temperatures and power show "--" (Windows), in the user's language
        let hint = match (running, &status.hub) {
            (true, Some(hub)) => {
                let latest = self.company.host.latest();
                let readings = latest
                    .map(|latest| latest.readings.clone())
                    .unwrap_or_default();
                let mut hub = hub
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                hub.hint(&readings, crate::unlock_offered())
            }
            _ => String::new(),
        };
        told.insert("hint".into(), Value::Str(hint));
        // a theme config.toml names that cannot be shown: the panel shows its own instead
        told.insert(
            "missing_theme".into(),
            Value::Str(status.missing_theme.clone()),
        );
        let notice = match (running, status.missing_theme.is_empty()) {
            (true, false) => {
                let shown = match status.theme.is_empty() {
                    true => status.default_theme.clone(),
                    false => status.theme.clone(),
                };
                lp_i18n::t_format(
                    "The theme “{name}” is no longer installed, so the panel shows “{shown}”. \
                     Choose a theme in the tray menu or on Start.",
                    &[
                        ("name", Value::Str(status.missing_theme.clone())),
                        ("shown", Value::Str(shown)),
                    ],
                )
                .unwrap_or_else(|why| why.msg)
            }
            _ => String::new(),
        };
        told.insert("notice".into(), Value::Str(notice));
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
        told.insert("target".into(), Value::Str(status.target));
        told.insert("default_theme".into(), Value::Str(status.default_theme));
        told.insert("mode".into(), status.mode.map_or(Value::None, Value::Str));
        // numbers of the last seconds (meter.rs); none while it does not run
        let stats = status.stats.filter(|_| running).unwrap_or_default();
        told.insert("stats".into(), Value::Dict(stats));
        // the panels beside this one ([[panels]]), by their place there, while it runs
        let mut more: Vec<(usize, RunStatus)> = (status.more.iter())
            .map(|(n, each)| (*n, each.lock().map(|s| s.clone()).unwrap_or_default()))
            .collect();
        more.sort_by_key(|(n, _)| *n);
        let more = (more.into_iter().filter(|_| running))
            .map(|(n, each)| {
                let mut entry = Dict::new();
                entry.insert("index".into(), Value::int(n as i64));
                entry.insert("state".into(), Value::Str(each.state.into()));
                let detail = match each.state {
                    "waiting" | "error" => each.detail,
                    _ => String::new(),
                };
                entry.insert("detail".into(), Value::Str(detail));
                entry.insert("target".into(), Value::Str(each.target));
                entry.insert("theme".into(), Value::Str(each.theme));
                let fps = each.stats.and_then(|stats| stats.get("fps").cloned());
                entry.insert("fps".into(), fps.unwrap_or(Value::None));
                entry.insert("model".into(), Value::Str(each.model));
                let size = each.size.map_or(Value::None, |(width, height)| {
                    Value::List(vec![Value::int(width), Value::int(height)])
                });
                entry.insert("size".into(), size);
                Value::Dict(entry)
            })
            .collect();
        told.insert("more".into(), Value::List(more));
        // (in the order service.py gives them)
        let order = [
            "state",
            "testing",
            "detail",
            "hint",
            "missing_theme",
            "notice",
            "target",
            "theme",
            "default_theme",
            "frames",
            "mode",
            "sequence",
            "size",
            "stats",
            "more",
        ];
        told.insert("testing".into(), Value::Bool(false)); // (no panel test here yet)
        let mut sorted = Dict::new();
        for key in order {
            if let Some(value) = told.shift_remove(key) {
                sorted.insert(key.into(), value);
            }
        }
        sorted.extend(told);
        sorted
    }
}

/// What a loop's thread has of its service.
struct Work {
    home: Home,
    asked: Asked,
    company: Arc<Company>,
    ending: Arc<AtomicU8>,
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
                    made.run(&self.stop, &|| {
                        Ending::of(self.ending.load(Ordering::Relaxed))
                    });
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
    /// what the start at login runs: this program's `tray --background`, with the --config asked
    autostart_command: Vec<String>,
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
                autostart_command: crate::autostart::launch_command(asked.config.as_deref()),
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
        crate::colour_show::recover(); // (a colour check the app ended during: said, gone)
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
            // (the window would show nothing)
            let url = format!("http://127.0.0.1:{}/", server.port);
            crate::window::close_window(&self.home.settings, Some(&url));
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

    /// The pictures the services published (the album cover, the game's logo, ...).
    pub fn images(&self) -> indexmap::IndexMap<String, lp_pixels::Image> {
        self.company.host.images()
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
            "autostart" => match value {
                Value::Bool(on) => self.set_autostart(*on)?,
                _ => return Err("autostart must be true or false".into()),
            },
            // which panel is which: each shows its number
            "identify" => crate::identify::show(),
            "show_transition" => {
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

    pub fn home(&self) -> &Home {
        &self.home
    }

    pub fn config_file(&self) -> &std::path::Path {
        &self.config_file
    }

    /// quit: Libre Panel asked to end (the tray's Quit).
    pub fn quit(&self) {
        self.quit_requested.store(true, Ordering::Relaxed);
    }

    /// The log file, where the app writes one (the tray's "Open log").
    pub fn log_file(&self) -> Option<PathBuf> {
        crate::logs::file()
    }

    /// How the timer stands, while it runs (the timer service's summary).
    pub fn timer(&self) -> Option<Value> {
        self.company.summary("timer")
    }

    /// active_theme: the theme of the normal page: config.toml's, else the panel's own default.
    pub fn active_theme(&self) -> Option<String> {
        let config = self.config().ok()?;
        if config.theme_given {
            return Some(config.theme);
        }
        let running = match self.panel.state().get("default_theme") {
            Some(Value::Str(theme)) if !theme.is_empty() => Some(theme.clone()),
            _ => None,
        };
        let places = self.home.places();
        let model = crate::settings::panel_model(&config);
        Some(
            running
                .unwrap_or_else(|| lp_theme::defaults::default_theme(model, &places).to_string()),
        )
    }

    /// clip_question: whether the person is still to be asked for the panel's standby clip
    /// (Start asks; the tray points to it): on a panel it is proven on, the one config.toml
    /// names, else the one the loop sends to. Nothing here looks at the USB bus.
    pub fn clip_question(&self) -> bool {
        let Ok(config) = self.config() else {
            return false;
        };
        let device = &config.device;
        let video = &device.video;
        if !["auto", "turzx"].contains(&device.driver.as_str()) || video.mode != "auto" {
            return false;
        }
        if !video.local_clip.is_empty() || video.clip_asked {
            return false;
        }
        let target = match self.panel.state().get("target") {
            Some(Value::Str(target)) => target.clone(),
            _ => String::new(),
        };
        let model = lp_devices::models::find_model(&device.model).or_else(|| {
            (lp_devices::models::MODELS.iter())
                .find(|model| model.video && target.starts_with(&model.label()))
        });
        model.is_some_and(|model| {
            lp_devices::turzx::video_gaps(&video.local_clip, Some(model), true) == ["clip"]
        })
    }

    fn autostart(&self) -> crate::autostart::Autostart {
        crate::autostart::Autostart::new(&self.autostart_command[0])
    }

    /// set_autostart: Libre Panel started at login (its background app), or no longer.
    pub fn set_autostart(&self, enabled: bool) -> Result<(), String> {
        let autostart = self.autostart();
        if enabled {
            let icon = self.home.data.join("assets").join("libre-panel.png");
            let at = (autostart.enable(&self.autostart_command, false, &icon)).map_err(|e| e.0)?;
            crate::logs::log(
                crate::logs::Level::Info,
                "libre_panel.service",
                &format!("autostart enabled: {at}"),
            );
        } else if autostart.disable().map_err(|e| e.0)? {
            crate::logs::log(
                crate::logs::Level::Info,
                "libre_panel.service",
                "autostart disabled",
            );
        }
        Ok(())
    }

    /// open: the app shown on `view`, in its window (opened or brought to the front), or in
    /// the browser where there can be no window; its address (None while it has no server).
    pub fn open(&self, view: &str) -> Option<String> {
        let address = self.start_editor()?;
        crate::window::open_window(&self.home.settings, &address, Some(view), false, None);
        Some(address)
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
            "open" => drop(self.open("home")),
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
    /// Each service config.toml enables and its state, as the service manager has them.
    pub fn services_state(&self) -> indexmap::IndexMap<String, String> {
        self.company.states()
    }

    /// What a running service is doing, for its card; None where it says nothing.
    pub fn service_summary(&self, name: &str) -> Option<Value> {
        self.company.summary(name)
    }

    /// A running service's status for its console page; None where it does not run.
    pub fn service_status(&self, name: &str) -> Option<Value> {
        self.company.status(name)
    }

    /// Stops an enabled service and starts it again; its new state.
    pub fn restart_service(&self, name: &str) -> Result<String, String> {
        self.company.restart(name)
    }

    /// A short message on the panel from the app's server (a page, another program's
    /// notice): whether it will show, or why not.
    pub fn notify(&self, toast: lp_render::overlays::Toast) -> lp_services::host::Delivery {
        lp_services::host::ServiceHost::new(&self.company.host, "editor").notify(toast)
    }

    pub fn snapshot(&self) -> Dict {
        let mut told = Dict::new();
        told.insert("panel".into(), Value::Dict(self.panel.state()));
        let brightness = self.brightness().map_or(Value::None, Value::int);
        told.insert("brightness".into(), brightness);
        let turned = self.upside_down().map_or(Value::None, Value::Bool);
        told.insert("upside_down".into(), turned);
        let autostart = self.autostart();
        let enabled = autostart.is_enabled().map_or(Value::None, Value::Bool);
        told.insert("autostart".into(), enabled);
        told.insert(
            "autostart_location".into(),
            Value::Str(autostart.location()),
        );
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
