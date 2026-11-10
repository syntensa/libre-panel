//! Game mode: the panel changes by itself when you play (services/game.py).
//!
//! The service looks every few seconds for a game: a program from the list in the settings,
//! or any program of a game installed through Steam, another launcher (launchers.rs) or in a
//! folder named in the settings whose window has been in front once (where Libre Panel
//! cannot tell which window is in front, any program of such a game that runs). Once it
//! finds one, it publishes the game's name (`game.name`), its logo from Steam's cache
//! (`game.logo`) and how long it has been played (`game.time`, in seconds, each second:
//! since its program started, where that can be told), and switches to the mode `game` with
//! an entrance that shows the logo. A few seconds after the game ends it switches back, with
//! the entrance for the end.
//!
//! The logo stays after the game, until the next one comes: the round report that follows
//! shows it beside the game's name. Without the entrance a message ("Game mode") says that
//! the panel changed.

use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_pixels::Image;
use lp_py::{Dict, Value};
use lp_render::overlays::Toast;

use crate::host::{Event, ServiceHost, Transition};
use crate::launchers::Catalog;
use crate::machine::{Computer, Machine, Program};
use crate::manager::{Part, Schema, Service};
use crate::paths::{self, HERE};
use crate::steam::{Library, Places};

pub const NAME: &str = "game.name";
pub const LOGO: &str = "game.logo";
pub const TIME: &str = "game.time";
pub const MODE: &str = "game";
/// at most so long the switch waits for a sample that has the logo
const SAMPLE_WAIT_S: f64 = 1.0;
/// how often the worker looks at that deadline while it waits
const TICK_S: f64 = 0.2;
/// how often it looks for a game
const POLL_RANGE_S: (f64, f64) = (0.25, 60.0);

/// A game that runs.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Found {
    /// the same key, the same game: its program, "steam:<number>", "epic:<app>" and so on
    /// for another launcher's, "folder:<its folder>", "" for `force`
    pub key: String,
    pub name: String,
    pub pid: i64,
    pub path: String,
    /// Steam's number for it, if it is a Steam game
    pub appid: String,
}

/// program_of: the file name of the game's program in lower case, which is how PresentMon
/// names the program whose frames it counts. None where there is no program (a forced game
/// mode).
pub fn program_of(found: &Found) -> Option<String> {
    if !found.path.is_empty() {
        let file = lp_py::lower(&paths::basename(HERE, &found.path));
        return (!file.is_empty()).then_some(file);
    }
    (!found.key.is_empty() && !found.key.contains(':')).then(|| found.key.clone())
}

/// parse_programs: the `programs` option: `"game.exe"` or `"game.exe = A Better Name"`, as
/// {program in lower case: name to show}.
pub fn parse_programs(raw: &[Value], report: &mut dyn FnMut(String)) -> IndexMap<String, String> {
    let mut programs = IndexMap::new();
    for entry in raw {
        let parts = entry
            .as_str()
            .map(|entry| entry.split_once('=').unwrap_or((entry, "")));
        let Some((program, label)) = parts.filter(|(program, _)| !lp_py::strip(program).is_empty())
        else {
            report(format!("programs: {} is no program name", entry.repr()));
            continue;
        };
        let program = lp_py::lower(lp_py::strip(program));
        let label = match lp_py::strip(label) {
            "" => program.strip_suffix(".exe").unwrap_or(&program).to_string(),
            label => label.to_string(),
        };
        programs.insert(program, label);
    }
    programs
}

/// The game a path belongs to: its key, its name and Steam's number for it.
type Matched = (String, String, String);

/// Tells whether a game runs. Stays on a game for `linger_s` after its program ended (games
/// start again while they are set up; launchers swap their process).
pub struct Detector {
    machine: Arc<dyn Machine>,
    pub library: Library,
    programs: IndexMap<String, String>,
    steam: bool,
    linger_s: f64,
    pub catalog: Option<Catalog>,
    game: Option<Found>,
    /// when its program was last seen
    seen: f64,
    /// programs whose window has been in front once
    front: HashSet<i64>,
}

impl Detector {
    pub fn new(
        machine: Arc<dyn Machine>,
        library: Library,
        programs: IndexMap<String, String>,
        steam: bool,
        linger_s: f64,
        catalog: Option<Catalog>,
    ) -> Detector {
        Detector {
            machine,
            library,
            programs,
            steam,
            linger_s,
            catalog: catalog.filter(Catalog::wanted),
            game: None,
            seen: 0.0,
            front: HashSet::new(),
        }
    }

    /// look: the game that runs at `now`, or None.
    pub fn look(&mut self, now: f64) -> Option<Found> {
        let looking = !self.programs.is_empty() || self.steam || self.catalog.is_some();
        let running = match looking {
            true => self.machine.programs(),
            false => Vec::new(),
        };
        let mut hit = self.listed(&running);
        if hit.is_none() && self.steam {
            hit = self.steam(&running);
        }
        if hit.is_none() {
            hit = self.launchers(&running);
        }
        if hit.is_some() {
            (self.game, self.seen) = (hit, now);
        } else if self.game.is_some() && now - self.seen > self.linger_s {
            self.game = None;
            let there: HashSet<i64> = running.iter().map(|program| program.pid).collect();
            self.front.retain(|pid| there.contains(pid));
        }
        self.game.clone()
    }

    fn listed(&mut self, running: &[Program]) -> Option<Found> {
        for (program, label) in &self.programs {
            if let Some(found) = running.iter().find(|found| found.name == *program) {
                let steam = self.library.match_path(&found.path);
                return Some(Found {
                    key: program.clone(),
                    name: label.clone(),
                    pid: found.pid,
                    path: found.path.clone(),
                    appid: steam.map(|game| game.appid).unwrap_or_default(),
                });
            }
        }
        None
    }

    /// _steam: a program of a game installed through Steam.
    fn steam(&mut self, running: &[Program]) -> Option<Found> {
        if self.library.games().is_empty() {
            return None;
        }
        let mut library = std::mem::replace(&mut self.library, Library::none());
        let found = self.installed(running, &mut |path| {
            // (one game, whatever the program of it that is in front)
            let game = library.match_path(path)?;
            Some((format!("steam:{}", game.appid), game.name, game.appid))
        });
        self.library = library;
        found
    }

    /// _launchers: a program of a game of another launcher, or in a folder named in the
    /// settings.
    fn launchers(&mut self, running: &[Program]) -> Option<Found> {
        let mut catalog = self.catalog.take()?;
        let found = match catalog.games().is_empty() {
            true => None,
            false => self.installed(running, &mut |path| {
                let game = catalog.match_path(path)?;
                Some((
                    format!("{}:{}", game.source, game.key),
                    game.name,
                    String::new(),
                ))
            }),
        };
        self.catalog = Some(catalog);
        found
    }

    /// _installed: the program of an installed game (`match_of` tells its key, name and Steam
    /// number). Where we know which window is in front, it must have been in front once: a
    /// library also holds programs that run all day (a wallpaper, a launcher's helper).
    fn installed(
        &mut self,
        running: &[Program],
        match_of: &mut dyn FnMut(&str) -> Option<Matched>,
    ) -> Option<Found> {
        let front = match self.machine.has_foreground() {
            true => self.machine.foreground_pid(),
            false => None,
        };
        let current = self.game.as_ref().map_or(0, |game| game.pid);
        // the game's program in front first (a launcher or an anti-cheat's starter that stays
        // in the background is not the one that draws), then the one it is: keep the game
        let mut order: Vec<&Program> = running.iter().collect();
        order.sort_by_key(|program| (Some(program.pid) != front, program.pid != current));
        for program in order {
            let Some((key, name, appid)) = match_of(&program.path) else {
                continue;
            };
            if let Some(front) = front {
                if program.pid == front {
                    self.front.insert(program.pid);
                }
                if !self.front.contains(&program.pid) {
                    continue;
                }
            }
            return Some(Found {
                key,
                name,
                pid: program.pid,
                path: program.path.clone(),
                appid,
            });
        }
        None
    }
}

/// What the service's options say beside what the detector takes.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Options {
    pub automatic: bool,
    pub force: bool,
    pub entrance: bool,
    pub logo: bool,
    pub poll_s: f64,
}

/// What a step of the service asks of the host.
#[derive(Clone, Debug)]
pub enum Action {
    Publish(&'static str, Value, &'static str, String),
    /// the game's logo, None to take it away
    Logo(Option<Image>),
    Mode(Option<&'static str>, Option<Transition>),
    Tell(Box<Toast>),
    /// a line for the log
    Note(String),
    /// the program whose frames count (PresentMon's)
    Target(Option<String>),
}

/// The panel's state for the game that runs: what it is set up for.
pub struct Core {
    pub detector: Detector,
    options: Options,
    /// the game the panel is set up for
    found: Option<Found>,
    logo: Option<Image>,
    in_mode: bool,
    /// since when the switch waits for a sample with the logo
    waiting: Option<f64>,
    /// since when the game has been played
    since: Option<f64>,
    /// the seconds `game.time` said last
    played: i64,
}

impl Core {
    pub fn new(detector: Detector, options: Options) -> Core {
        Core {
            detector,
            options: Options {
                poll_s: options.poll_s.max(POLL_RANGE_S.0).min(POLL_RANGE_S.1),
                ..options
            },
            found: None,
            logo: None,
            in_mode: false,
            waiting: None,
            since: None,
            played: -1,
        }
    }

    pub fn poll_s(&self) -> f64 {
        self.options.poll_s
    }

    /// How long the worker sleeps at `now` before its next look, when `due` is the next look
    /// for a game.
    pub fn wait(&self, now: f64, due: f64) -> f64 {
        let mut wait = match self.waiting {
            Some(_) => TICK_S,
            None => (due - now).max(0.01),
        };
        if let Some(since) = self.since {
            // the time played goes on second by second
            wait = wait.min((1.0 - (now - since).rem_euclid(1.0)).max(0.01));
        }
        wait
    }

    /// detect: look for a game once and set the panel up for what is found.
    pub fn detect(&mut self, now: f64) -> Vec<Action> {
        let mut found = match self.options.automatic {
            true => self.detector.look(now),
            false => None,
        };
        if found.is_none() && self.options.force {
            found = Some(Found {
                key: String::new(),
                name: lp_i18n::t("Game mode"),
                pid: 0,
                path: String::new(),
                appid: String::new(),
            });
        }
        let mut out = Vec::new();
        self.apply(found, now, &mut out);
        out
    }

    fn apply(&mut self, found: Option<Found>, now: f64, out: &mut Vec<Action>) {
        let Some(found) = found else {
            if self.found.is_some() {
                self.end(out);
            }
            return;
        };
        if let Some(before) = self.found.as_ref().filter(|before| before.key == found.key) {
            if program_of(&found) != program_of(before) {
                // another program of the game
                out.push(Action::Target(program_of(&found)));
                self.found = Some(found);
            }
            return; // still the same game
        }
        self.logo = match self.options.logo && !found.appid.is_empty() {
            true => self.detector.library.logo(&found.appid),
            false => None,
        };
        let running = match found.pid {
            0 => None,
            pid => self.detector.machine.running_s(pid),
        };
        self.since = Some(now - running.unwrap_or(0.0));
        self.played = -1;
        self.found = Some(found.clone());
        self.tick(now, out);
        out.push(Action::Note(format!("game: {}", found.name)));
        // (its frames are the ones that count)
        out.push(Action::Target(program_of(&found)));
        out.push(Action::Publish(
            NAME,
            Value::Str(found.name),
            "",
            "Game".into(),
        ));
        out.push(Action::Logo(self.logo.clone()));
        if self.in_mode {
            return; // one game after the other: the screens learn the new name and logo
        }
        match self.logo {
            None => self.enter(out),
            // the first game screen should have the logo: wait for a sample that has it
            Some(_) => self.waiting = Some(now),
        }
    }

    /// tick: say how long the game has been played (`game.time`), once a second.
    pub fn tick(&mut self, now: f64, out: &mut Vec<Action>) {
        let Some(since) = self.since else {
            return;
        };
        let played = (now - since).max(0.0) as i64;
        if played != self.played {
            self.played = played;
            out.push(Action::Publish(
                TIME,
                Value::int(played),
                "s",
                lp_i18n::t("Played"),
            ));
        }
    }

    // -- switching ---------------------------------------------------------------------------

    /// on_sample: a sample came; `there` says whether it has the logo.
    pub fn sample(&mut self, there: bool, out: &mut Vec<Action>) {
        if self.waiting.is_some() && there {
            self.enter(out);
        }
    }

    /// advance: the switch no longer waits for a sample once a second has passed.
    pub fn advance(&mut self, now: f64, out: &mut Vec<Action>) {
        if self
            .waiting
            .is_some_and(|since| now - since >= SAMPLE_WAIT_S)
        {
            self.enter(out);
        }
    }

    fn enter(&mut self, out: &mut Vec<Action>) {
        self.waiting = None;
        out.push(Action::Mode(Some(MODE), self.transition("start")));
        self.in_mode = true;
        if !self.options.entrance {
            self.announce("Game mode", out);
        }
    }

    fn end(&mut self, out: &mut Vec<Action>) {
        let was_in_mode = self.in_mode;
        if was_in_mode {
            out.push(Action::Mode(None, self.transition("end")));
            if !self.options.entrance {
                self.announce("Game ended", out);
            }
        }
        out.push(Action::Note(
            match was_in_mode {
                true => "game over",
                false => "game gone before the mode began",
            }
            .into(),
        ));
        (self.waiting, self.in_mode, self.found, self.logo) = (None, false, None, None);
        (self.since, self.played) = (None, -1);
        out.push(Action::Target(None));
        out.push(Action::Publish(NAME, Value::None, "", "Game".into()));
        out.push(Action::Publish(
            TIME,
            Value::None,
            "s",
            lp_i18n::t("Played"),
        ));
        if !was_in_mode {
            // (a game that was played leaves its logo for the round report)
            out.push(Action::Logo(None));
        }
    }

    /// _transition: the entrance with the game's name and logo (the picture published under
    /// the logo's key), or the theme's own transition.
    fn transition(&self, kind: &str) -> Option<Transition> {
        if !self.options.entrance {
            return None;
        }
        let mut params = Dict::new();
        params.insert("kind".into(), Value::Str(kind.to_string()));
        // (a forced mode has no game to name)
        if let Some(found) = self.found.as_ref().filter(|found| !found.key.is_empty()) {
            params.insert("title".into(), Value::Str(found.name.clone()));
        }
        if self.logo.is_some() {
            params.insert("logo".into(), Value::Str(LOGO.to_string()));
        }
        Some(Transition {
            name: "entrance".into(),
            params: Some(params),
        })
    }

    /// status: what the service is doing, for a console page.
    pub fn status(&self) -> Dict {
        let text =
            |text: Option<&String>| text.map_or(Value::None, |text| Value::Str(text.clone()));
        let found = self.found.as_ref();
        let mut status = Dict::new();
        status.insert("game".into(), text(found.map(|found| &found.name)));
        let appid = found
            .map(|found| &found.appid)
            .filter(|appid| !appid.is_empty());
        status.insert("steam_id".into(), text(appid));
        let mode = self.in_mode.then(|| MODE.to_string());
        status.insert("mode".into(), text(mode.as_ref()));
        status.insert("waiting".into(), Value::Bool(self.waiting.is_some()));
        status
    }

    fn announce(&self, label: &str, out: &mut Vec<Action>) {
        let Some(found) = &self.found else {
            return;
        };
        let mut payload = Dict::new();
        payload.insert("label".into(), Value::Str(lp_i18n::t(label)));
        out.push(Action::Tell(Box::new(Toast {
            icon: Some("gamepad".into()),
            seconds: Some(3.0),
            kind: "game".into(),
            rank: 1,
            payload,
            key: "game".into(),
            ..Toast::new(found.name.clone())
        })));
    }
}

struct Shared {
    core: Mutex<Core>,
    host: ServiceHost,
    began: Instant,
    ended: AtomicBool,
    wake: (Mutex<()>, Condvar),
}

impl Shared {
    fn clock(&self) -> f64 {
        self.began.elapsed().as_secs_f64()
    }

    /// Does what a step asks, unless the service stopped meanwhile.
    fn act(&self, step: impl FnOnce(&mut Core, &mut Vec<Action>)) {
        let mut out = Vec::new();
        {
            let Ok(mut core) = self.core.lock() else {
                return;
            };
            if self.ended.load(Ordering::Relaxed) {
                return; // (a look that was under way when the service stopped)
            }
            step(&mut core, &mut out);
            for note in std::mem::take(&mut core.detector.library.notes) {
                out.push(Action::Note(note));
            }
            if let Some(catalog) = &mut core.detector.catalog {
                out.extend(
                    std::mem::take(&mut catalog.notes)
                        .into_iter()
                        .map(Action::Note),
                );
            }
        }
        for action in out {
            match action {
                Action::Publish(key, value, unit, label) => {
                    self.host.publish(key, value, unit, &label);
                }
                Action::Logo(logo) => self.host.publish_image(LOGO, logo),
                Action::Mode(mode, transition) => self.host.set_mode(mode, transition),
                Action::Tell(toast) => {
                    self.host.notify(*toast);
                }
                Action::Note(line) => self.host.note(&line),
                Action::Target(program) => lp_sensors::presentmon::set_target(program.as_deref()),
            }
        }
    }

    fn run(&self) {
        let poll = self.core.lock().map_or(2.0, |core| core.poll_s());
        // (not at once: the panel is still starting)
        let mut due = self.clock() + poll;
        loop {
            let wait = match self.core.lock() {
                Ok(core) => core.wait(self.clock(), due),
                Err(_) => return,
            };
            if let Ok(asleep) = self.wake.0.lock() {
                let ended = |_: &mut ()| !self.ended.load(Ordering::Relaxed);
                let _ =
                    (self.wake.1).wait_timeout_while(asleep, Duration::from_secs_f64(wait), ended);
            }
            if self.ended.load(Ordering::Relaxed) {
                return;
            }
            self.act(|core, out| {
                let now = self.clock();
                core.advance(now, out);
                core.tick(now, out);
            });
            if self.clock() >= due {
                due = self.clock() + poll;
                self.act(|core, out| *out = core.detect(self.clock()));
            }
        }
    }
}

/// Switches to game mode while a game runs.
pub struct Game {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

fn options() -> Schema {
    let list = || Value::List(Vec::new());
    vec![
        ("programs", "list", list()),
        ("steam", "bool", Value::Bool(true)),
        ("steam_path", "string", Value::Str(String::new())),
        ("launchers", "bool", Value::Bool(true)),
        ("folders", "list", list()),
        ("ignore", "list", list()),
        ("automatic", "bool", Value::Bool(true)),
        ("force", "bool", Value::Bool(false)),
        ("entrance", "bool", Value::Bool(true)),
        ("logo", "bool", Value::Bool(true)),
        ("linger_s", "number", Value::Float(6.0)),
        ("poll_s", "number", Value::Float(2.0)),
    ]
}

type Clock = Box<dyn Fn() -> f64 + Send + Sync>;

/// The service's core of its options, on `machine`, with the launchers' games of `places`;
/// what is wrong with the options is reported.
pub fn core_of(
    options: &Dict,
    machine: Arc<dyn Machine>,
    clock: &dyn Fn() -> Clock,
    places: &dyn Fn() -> Places,
    report: &mut dyn FnMut(String),
) -> Core {
    let on = |key: &str, default: bool| options.get(key).map_or(default, Value::truthy);
    let list = |key: &str| options.get(key).and_then(Value::as_list).unwrap_or(&[]);
    let number = |key: &str, default: f64| {
        let given = options.get(key).and_then(Value::to_f64);
        given.unwrap_or(default)
    };
    let steam_path = options
        .get("steam_path")
        .map(Value::str)
        .unwrap_or_default();
    let library = Library::new(&steam_path, list("ignore"), clock(), places());
    let programs = parse_programs(list("programs"), report);
    let launchers = on("launchers", true);
    let catalog = Catalog::new(
        list("folders"),
        list("ignore"),
        launchers,
        clock(),
        None,
        places(),
    );
    let steam = on("steam", true);
    let detector = Detector::new(
        machine,
        library,
        programs,
        steam,
        number("linger_s", 6.0),
        Some(catalog),
    );
    Core::new(
        detector,
        Options {
            automatic: on("automatic", true),
            force: on("force", false),
            entrance: on("entrance", true),
            logo: on("logo", true),
            poll_s: number("poll_s", 2.0),
        },
    )
}

/// Game mode as a kind of service.
pub fn part() -> Part {
    Part {
        options,
        make: |host, options| {
            let began = Instant::now();
            let clock = move || -> Clock { Box::new(move || began.elapsed().as_secs_f64()) };
            let mut report = |line: String| host.note(&line);
            let core = core_of(
                &options,
                Arc::new(Computer),
                &clock,
                &Places::here,
                &mut report,
            );
            Ok(Box::new(Game {
                shared: Arc::new(Shared {
                    core: Mutex::new(core),
                    host,
                    began,
                    ended: AtomicBool::new(false),
                    wake: (Mutex::new(()), Condvar::new()),
                }),
                thread: None,
            }))
        },
    }
}

impl Service for Game {
    fn start(&mut self) -> Result<(), String> {
        // The frame rates come from PresentMon, whose recording session must exist before the
        // game starts (an anti-cheat refuses a new one): the service asks for them, so that
        // the source runs for as long as the service does, whether a theme shows them or not.
        self.shared.host.want(&["game.*"]);
        let shared = Arc::clone(&self.shared);
        self.shared.host.on(
            "sample",
            Box::new(move |event| {
                if let Event::Sample { snapshot } = event {
                    let there = snapshot.images.contains_key(LOGO);
                    shared.act(|core, out| core.sample(there, out));
                }
            }),
        )?;
        let shared = Arc::clone(&self.shared);
        let thread = std::thread::Builder::new()
            .name("game-mode".into())
            .spawn(move || shared.run());
        self.thread = Some(thread.map_err(|why| why.to_string())?);
        Ok(())
    }

    fn stop(&mut self) {
        self.shared.ended.store(true, Ordering::Relaxed);
        if let Ok(_asleep) = self.shared.wake.0.lock() {
            self.shared.wake.1.notify_all();
        }
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
        lp_sensors::presentmon::set_target(None);
    }

    fn summary(&self) -> Option<Value> {
        let core = self.shared.core.lock().ok()?;
        Some(Value::Dict(core.status()))
    }
}
