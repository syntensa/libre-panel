//! Autopilot: the theme that fits what you are doing (services/autopilot.py).
//!
//! Rules in config.toml are checked from the top; the first whose conditions all hold names
//! the theme to show (music playing, the microphone in use, the load over a limit, a
//! program, the time of day). When none holds, the theme of config.toml shows. The service
//! never writes the configuration: it asks the host for a theme the way any service can
//! (`show_theme` with the lowest priority, for normal mode only), so a game mode or a
//! service with a higher priority goes first, and the choice is back by itself when they are
//! done.
//!
//! Nothing flickers: a new choice must hold for `hold_s`, and a theme then stays for `min_s`
//! (a rule can say `immediate`). Right after the system started the load is no signal, since
//! everything starts at once.

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;

use lp_py::regex::Regex;
use lp_py::{Dict, Value};
use lp_render::snapshot::Snapshot;

use crate::host::{Event, Scope, ServiceHost, Transition};
use crate::machine::{Computer, Machine};
use crate::manager::{Part, Schema, Service};
use crate::notices::is_playing;
use crate::power::number;

const CONDITIONS: [&str; 6] = [
    "music",
    "microphone",
    "cpu_over",
    "gpu_over",
    "apps",
    "time",
];
/// a mean over a window counts once this much of it has been seen
const FILL: f64 = 0.8;
/// load readings are kept this far apart
const LOAD_EVERY_S: f64 = 0.5;
/// the microphone and the window in front are asked this often
const SLOW_EVERY_S: f64 = 1.0;
/// and the list of programs (where no window in front is known)
const PROGRAMS_EVERY_S: f64 = 2.0;
/// whether a theme is installed
const THEME_EVERY_S: f64 = 10.0;
const LONGEST_S: f64 = 3600.0;

/// A condition of a rule: what a signal must be.
#[derive(Clone, Debug, PartialEq)]
pub enum Condition {
    Music(bool),
    Microphone(bool),
    CpuOver(f64),
    GpuOver(f64),
    Apps(HashSet<String>),
    /// from and to, minutes after midnight
    Time(i64, i64),
}

/// A theme and the conditions at which it shows.
#[derive(Clone, Debug, PartialEq)]
pub struct Rule {
    pub name: String,
    pub theme: String,
    pub when: Vec<Condition>,
    /// the window the load is averaged over
    pub for_s: f64,
    pub hold_s: Option<f64>,
    pub immediate: bool,
}

/// parse_time: `"22:00-06:30"` as minutes after midnight, (22 * 60, 6 * 60 + 30).
pub fn parse_time(text: &Value) -> Option<(i64, i64)> {
    static TIME: OnceLock<Option<Regex>> = OnceLock::new();
    const PATTERN: &str = r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$";
    let pattern = TIME.get_or_init(|| Regex::new(PATTERN, false).ok());
    let chars: Vec<char> = text.as_str()?.chars().collect();
    let found = pattern.as_ref()?.matches(&chars)?;
    let part = |n: usize| {
        let digits = found.group(&chars, n)?;
        lp_py::int_of_digits(&digits).and_then(|n| i64::try_from(n).ok())
    };
    let (start_h, start_m, end_h, end_m) = (part(1)?, part(2)?, part(3)?, part(4)?);
    if start_h.max(end_h) > 23 || start_m.max(end_m) > 59 {
        return None;
    }
    Some((start_h * 60 + start_m, end_h * 60 + end_m))
}

pub fn in_time(window: (i64, i64), minutes: i64) -> bool {
    let (start, end) = window;
    match start <= end {
        true => start <= minutes && minutes < end,
        false => minutes >= start || minutes < end,
    }
}

/// parse_conditions: the conditions of a rule with their values checked, or what is wrong.
pub fn parse_conditions(when: Option<&Value>) -> Result<Vec<Condition>, String> {
    let Some(Value::Dict(when)) = when else {
        return Err("'when' must be a table, e.g. { music = true }".into());
    };
    let mut found = Vec::new();
    for (key, value) in when {
        found.push(match key.as_str() {
            "for_s" => continue,
            "music" | "microphone" => {
                let Value::Bool(on) = value else {
                    return Err(format!("{key} must be true or false"));
                };
                match key.as_str() {
                    "music" => Condition::Music(*on),
                    _ => Condition::Microphone(*on),
                }
            }
            "cpu_over" | "gpu_over" => {
                let Some(load) = number(Some(value)) else {
                    return Err(format!("{key} must be a number (a load in percent)"));
                };
                match key.as_str() {
                    "cpu_over" => Condition::CpuOver(load),
                    _ => Condition::GpuOver(load),
                }
            }
            "apps" => {
                let names: HashSet<String> = (value.as_list().unwrap_or(&[]).iter())
                    .filter_map(Value::as_str)
                    .map(|name| lp_py::lower(lp_py::strip(name)))
                    .filter(|name| !name.is_empty())
                    .collect();
                if names.is_empty() {
                    return Err("apps must be a list of program names".into());
                }
                Condition::Apps(names)
            }
            "time" => match parse_time(value) {
                Some((start, end)) => Condition::Time(start, end),
                None => return Err("time must look like \"22:00-06:30\"".into()),
            },
            _ => {
                let known = CONDITIONS.join(", ");
                return Err(format!(
                    "unknown condition {} (known: {known})",
                    lp_py::repr(key)
                ));
            }
        });
    }
    if found.is_empty() {
        return Err("it has no conditions".into());
    }
    Ok(found)
}

/// parse_rules: the `rules` option; a rule that can never match is reported and left out.
pub fn parse_rules(raw: &[Value], report: &mut dyn FnMut(String)) -> Vec<Rule> {
    let mut rules = Vec::new();
    for (n, entry) in raw.iter().enumerate() {
        let table = entry.as_dict();
        let get = |key: &str| table.and_then(|table| table.get(key));
        if matches!(get("enabled"), Some(Value::Bool(false))) {
            continue;
        }
        let name = match get("name").filter(|name| name.truthy()) {
            Some(name) => name.str(),
            None => format!("rule {}", n + 1),
        };
        let shown = lp_py::repr(&name);
        let Some(Value::Str(theme)) = get("theme").filter(|theme| theme.truthy()) else {
            report(format!("rule {shown} skipped: it needs a theme"));
            continue;
        };
        let when = match parse_conditions(get("when")) {
            Ok(when) => when,
            Err(problem) => {
                report(format!("rule {shown} skipped: {problem}"));
                continue;
            }
        };
        let seconds = |given: Option<&Value>, usual: f64| match given {
            Some(given) => number(Some(given)),
            None => Some(usual),
        };
        let for_s = seconds(
            get("when")
                .and_then(Value::as_dict)
                .and_then(|when| when.get("for_s")),
            20.0,
        );
        let hold = seconds(get("hold_s"), 0.0);
        let (Some(for_s), Some(hold)) = (for_s.filter(|s| *s > 0.0), hold.filter(|s| *s >= 0.0))
        else {
            report(format!(
                "rule {shown} skipped: for_s and hold_s must be numbers of seconds"
            ));
            continue;
        };
        rules.push(Rule {
            name,
            theme: theme.clone(),
            when,
            for_s: for_s.min(LONGEST_S),
            hold_s: get("hold_s").map(|_| hold),
            immediate: matches!(get("immediate"), Some(Value::Bool(true))),
        });
    }
    rules
}

/// What the rules look at, as of the last sample.
#[derive(Clone, Debug, Default)]
pub struct Signals {
    pub music: bool,
    pub microphone: bool,
    /// the program in front (where that is known)
    pub app: Option<String>,
    /// else the programs that run
    pub running: HashSet<String>,
    /// the time of day
    pub minutes: i64,
    /// when, and the processor's and the graphics card's load then
    pub loads: VecDeque<(f64, Option<f64>, Option<f64>)>,
}

impl Signals {
    /// mean: the load (the processor's, else the graphics card's) averaged over `window_s`;
    /// None while less than four fifths of the window has been seen (a peak right after the
    /// start must not count as a load).
    pub fn mean(&self, cpu: bool, window_s: f64, now: f64) -> Option<f64> {
        let column = |load: &(f64, Option<f64>, Option<f64>)| if cpu { load.1 } else { load.2 };
        let seen: Vec<(f64, f64)> = (self.loads.iter())
            .filter(|load| now - load.0 <= window_s)
            .filter_map(|load| Some((load.0, column(load)?)))
            .collect();
        if now - seen.first()?.0 < FILL * window_s {
            return None;
        }
        Some(seen.iter().fold(0.0, |sum, (_, load)| sum + load) / seen.len() as f64)
    }
}

/// What the service's options say beside its rules.
#[derive(Clone, Debug, PartialEq)]
pub struct Options {
    pub hold_s: f64,
    pub min_s: f64,
    pub quiet_after_boot_s: f64,
    pub transition: String,
}

/// What a sample asks of the host.
#[derive(Clone, Debug, PartialEq)]
pub enum Action {
    /// a line for the log
    Note(String),
    Show(String, Option<Transition>),
    Restore(Option<Transition>),
}

/// The rules and what they said last.
pub struct Core {
    machine: Arc<dyn Machine>,
    rules: Vec<Rule>,
    options: Options,
    /// how far back the load is kept
    longest: f64,
    pub signals: Signals,
    /// the rule whose theme is shown
    rule: Option<usize>,
    /// that theme (None: the user's own)
    theme: Option<String>,
    /// what the rules say, since when
    candidate: (Option<String>, f64),
    changed: f64,
    /// slow signals: when each was last asked
    asked: HashMap<&'static str, f64>,
    installed: HashMap<String, (bool, f64)>,
    past_boot: bool,
}

impl Core {
    pub fn new(rules: Vec<Rule>, options: Options, machine: Arc<dyn Machine>) -> Core {
        let loaded = |rule: &&Rule| {
            let over =
                |when: &Condition| matches!(when, Condition::CpuOver(_) | Condition::GpuOver(_));
            rule.when.iter().any(over)
        };
        let longest =
            (rules.iter().filter(loaded)).fold(0.0, |most: f64, rule| most.max(rule.for_s));
        Core {
            machine,
            rules,
            options,
            longest,
            signals: Signals::default(),
            rule: None,
            theme: None,
            candidate: (None, 0.0),
            changed: f64::NEG_INFINITY,
            asked: HashMap::new(),
            installed: HashMap::new(),
            past_boot: false,
        }
    }

    pub fn rules(&self) -> &[Rule] {
        &self.rules
    }

    fn reads(&self, fits: impl Fn(&Condition) -> bool) -> bool {
        self.rules.iter().any(|rule| rule.when.iter().any(&fits))
    }

    /// The readings the rules read.
    pub fn wants(&self) -> Vec<&'static str> {
        let mut keys = Vec::new();
        if self.reads(|when| matches!(when, Condition::Music(_))) {
            keys.push("media.state");
        }
        if self.reads(|when| matches!(when, Condition::CpuOver(_))) {
            keys.push("cpu.load");
        }
        if self.reads(|when| matches!(when, Condition::GpuOver(_))) {
            keys.push("gpu.load");
        }
        keys
    }

    // -- every sample ------------------------------------------------------------------------

    /// on_sample: the signals of a sample at `now` (`minutes`: the time of day), and what
    /// the rules then ask for. `resting` says that a mode is on; `installed` whether a theme
    /// is there.
    pub fn sample(
        &mut self,
        snapshot: &Snapshot,
        now: f64,
        minutes: i64,
        resting: bool,
        installed: &dyn Fn(&str) -> bool,
    ) -> Vec<Action> {
        self.read(snapshot, now, minutes);
        self.decide(now, resting, installed)
    }

    fn read(&mut self, snapshot: &Snapshot, now: f64, minutes: i64) {
        if self.reads(|when| matches!(when, Condition::Music(_))) {
            self.signals.music = is_playing(snapshot);
        }
        if self.longest != 0.0 {
            let loads = &mut self.signals.loads;
            if loads.back().is_none_or(|last| now - last.0 >= LOAD_EVERY_S) {
                let load = |key: &str| number(Some(&snapshot.value(key)));
                loads.push_back((now, load("cpu.load"), load("gpu.load")));
            }
            while loads
                .front()
                .is_some_and(|first| now - first.0 > self.longest)
            {
                loads.pop_front();
            }
        }
        let microphone = self.reads(|when| matches!(when, Condition::Microphone(_)));
        if microphone && self.due("microphone", now, SLOW_EVERY_S) {
            self.signals.microphone = self.machine.microphone_in_use();
        }
        if self.reads(|when| matches!(when, Condition::Apps(_))) {
            if self.machine.has_foreground() {
                if self.due("app", now, SLOW_EVERY_S) {
                    self.signals.app = self.machine.foreground_name();
                }
            } else if self.due("running", now, PROGRAMS_EVERY_S) {
                let programs = self.machine.programs().into_iter();
                self.signals.running = programs.map(|program| program.name).collect();
            }
        }
        if self.reads(|when| matches!(when, Condition::Time(..))) {
            self.signals.minutes = minutes;
        }
    }

    /// _due: whether a signal that costs something is asked for again.
    fn due(&mut self, what: &'static str, now: f64, every: f64) -> bool {
        if now - self.asked.get(what).copied().unwrap_or(f64::NEG_INFINITY) < every {
            return false;
        }
        self.asked.insert(what, now);
        true
    }

    // -- the rules ---------------------------------------------------------------------------

    /// holds: whether all the conditions of a rule hold. Anything unknown does not.
    fn holds(&self, rule: &Rule, now: f64, quiet: bool) -> bool {
        let signals = &self.signals;
        let over = |cpu: bool, limit: f64| {
            let load = match quiet {
                true => None,
                false => signals.mean(cpu, rule.for_s, now),
            };
            load.is_some_and(|load| load >= limit)
        };
        rule.when.iter().all(|when| match when {
            Condition::Music(on) => signals.music == *on,
            Condition::Microphone(on) => signals.microphone == *on,
            Condition::CpuOver(limit) => over(true, *limit),
            Condition::GpuOver(limit) => over(false, *limit),
            Condition::Apps(names) => match self.machine.has_foreground() {
                true => signals.app.as_ref().is_some_and(|app| names.contains(app)),
                false => names.iter().any(|name| signals.running.contains(name)),
            },
            Condition::Time(start, end) => in_time((*start, *end), signals.minutes),
        })
    }

    /// _usable: whether the rule's theme is installed (asked now and then; said once if not).
    fn usable(
        &mut self,
        rule: usize,
        now: f64,
        installed: &dyn Fn(&str) -> bool,
        out: &mut Vec<Action>,
    ) -> bool {
        let rule = &self.rules[rule];
        let known = self.installed.get(&rule.theme).copied();
        if let Some((there, _)) = known.filter(|(_, asked)| now - asked < THEME_EVERY_S) {
            return there;
        }
        let there = installed(&rule.theme);
        if !there && known.is_none_or(|(before, _)| before) {
            out.push(Action::Note(format!(
                "rule {} skipped: theme {} is not installed",
                lp_py::repr(&rule.name),
                lp_py::repr(&rule.theme)
            )));
        }
        self.installed.insert(rule.theme.clone(), (there, now));
        there
    }

    /// _after_boot_quiet: right after the system started the load says nothing about what
    /// you do.
    fn after_boot_quiet(&mut self) -> bool {
        if self.past_boot {
            return false;
        }
        let quiet = self.options.quiet_after_boot_s;
        if self.machine.uptime_s().is_none_or(|uptime| uptime >= quiet) {
            self.past_boot = true;
            return false;
        }
        true
    }

    /// decide: pick the rule that holds, and follow it if the choice has been stable long
    /// enough and the theme before has stayed long enough.
    pub fn decide(
        &mut self,
        now: f64,
        resting: bool,
        installed: &dyn Fn(&str) -> bool,
    ) -> Vec<Action> {
        let mut out = Vec::new();
        if resting {
            // A game: the request stays where it is, resting there, and applies again at
            // once after it. A new choice must hold afresh then.
            self.candidate = (self.theme.clone(), now);
            return out;
        }
        let quiet = self.longest != 0.0 && self.after_boot_quiet();
        let usable: Vec<usize> = (0..self.rules.len())
            .filter(|rule| self.usable(*rule, now, installed, &mut out))
            .collect();
        let rule = usable
            .into_iter()
            .find(|rule| self.holds(&self.rules[*rule], now, quiet));
        let theme = rule.map(|rule| self.rules[rule].theme.clone());
        if theme == self.theme {
            self.candidate = (theme, now);
            return out;
        }
        if self.candidate.0 != theme {
            self.candidate = (theme, now);
        }
        let chosen = rule.map(|rule| &self.rules[rule]);
        let hold = chosen
            .and_then(|rule| rule.hold_s)
            .unwrap_or(self.options.hold_s);
        let stable = now - self.candidate.1 >= hold;
        let free =
            now - self.changed >= self.options.min_s || chosen.is_some_and(|rule| rule.immediate);
        if stable && free {
            self.follow(rule, now, &mut out);
        }
        out
    }

    fn follow(&mut self, rule: Option<usize>, now: f64, out: &mut Vec<Action>) {
        let transition = Some(&self.options.transition).filter(|name| !name.is_empty());
        let transition = transition.map(|name| Transition::named(name));
        (self.rule, self.changed) = (rule, now);
        self.theme = rule.map(|rule| self.rules[rule].theme.clone());
        match rule.map(|rule| &self.rules[rule]) {
            None => {
                out.push(Action::Note(
                    "no rule holds: the theme of config.toml".into(),
                ));
                out.push(Action::Restore(transition));
            }
            Some(rule) => {
                let (name, theme) = (lp_py::repr(&rule.name), lp_py::repr(&rule.theme));
                out.push(Action::Note(format!("rule {name}: theme {theme}")));
                out.push(Action::Show(rule.theme.clone(), transition));
            }
        }
    }

    /// status: what the autopilot is doing, for a console page.
    pub fn status(&self, now: f64, resting: bool) -> Dict {
        let text =
            |text: Option<&String>| text.map_or(Value::None, |text| Value::Str(text.clone()));
        let float = |number: Option<f64>| number.map_or(Value::None, Value::Float);
        let mut signals = Dict::new();
        signals.insert("music".into(), Value::Bool(self.signals.music));
        signals.insert("microphone".into(), Value::Bool(self.signals.microphone));
        signals.insert("app".into(), text(self.signals.app.as_ref()));
        signals.insert("cpu".into(), float(self.signals.mean(true, 20.0, now)));
        signals.insert("gpu".into(), float(self.signals.mean(false, 20.0, now)));
        let since =
            (self.changed > f64::NEG_INFINITY).then(|| lp_py::round_digits(now - self.changed, 1));
        let candidate = self
            .candidate
            .0
            .as_ref()
            .filter(|_| self.candidate.0 != self.theme);
        let mut status = Dict::new();
        status.insert(
            "rule".into(),
            text(self.rule.map(|rule| &self.rules[rule].name)),
        );
        status.insert("theme".into(), text(self.theme.as_ref()));
        status.insert("since_s".into(), float(since));
        status.insert("candidate".into(), text(candidate));
        status.insert("resting".into(), Value::Bool(resting));
        status.insert("signals".into(), Value::Dict(signals));
        status
    }
}

struct Shared {
    core: Mutex<Core>,
    host: ServiceHost,
    began: Instant,
    /// (a sample that is under way then asks for nothing)
    stopped: AtomicBool,
}

/// The time of day on this computer, in minutes after midnight.
fn minutes_now() -> i64 {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |since| since.as_secs_f64());
    lp_py::zones::local()
        .and_then(|zone| lp_py::zones::fromtimestamp(zone, now))
        .map_or(0, |(wall, _)| {
            i64::from(wall.hour) * 60 + i64::from(wall.minute)
        })
}

/// Shows the theme of the first rule that holds.
pub struct Autopilot {
    shared: Arc<Shared>,
}

fn options() -> Schema {
    vec![
        ("rules", "list", Value::List(Vec::new())),
        ("hold_s", "number", Value::Float(8.0)),
        ("min_s", "number", Value::Float(20.0)),
        ("quiet_after_boot_s", "number", Value::Float(240.0)),
        ("transition", "string", Value::Str("tube".into())),
    ]
}

/// The service's options beside its rules.
pub fn options_of(options: &Dict) -> Options {
    let number = |key: &str, usual: f64| {
        let given = options.get(key).and_then(Value::to_f64);
        given.unwrap_or(usual)
    };
    Options {
        hold_s: number("hold_s", 8.0),
        min_s: number("min_s", 20.0),
        quiet_after_boot_s: number("quiet_after_boot_s", 240.0),
        transition: options
            .get("transition")
            .map_or("tube".to_string(), Value::str),
    }
}

/// The autopilot as a kind of service.
pub fn part() -> Part {
    Part {
        options,
        make: |host, options| {
            let raw = options.get("rules").and_then(Value::as_list).unwrap_or(&[]);
            let rules = parse_rules(raw, &mut |line| host.note(&line));
            let core = Core::new(rules, options_of(&options), Arc::new(Computer));
            Ok(Box::new(Autopilot {
                shared: Arc::new(Shared {
                    core: Mutex::new(core),
                    host,
                    began: Instant::now(),
                    stopped: AtomicBool::new(false),
                }),
            }))
        },
    }
}

impl Service for Autopilot {
    fn start(&mut self) -> Result<(), String> {
        let host = &self.shared.host;
        let wants = match self.shared.core.lock() {
            Ok(core) if core.rules().is_empty() => {
                host.note("no usable rules in [services.autopilot]: nothing to do");
                return Ok(());
            }
            Ok(core) => core.wants(),
            Err(_) => return Err("the autopilot cannot be started".into()),
        };
        host.want(&wants);
        let shared = Arc::clone(&self.shared);
        host.on(
            "sample",
            Box::new(move |event| {
                let Event::Sample { snapshot } = event else {
                    return;
                };
                if shared.stopped.load(Ordering::Relaxed) {
                    return;
                }
                let now = shared.began.elapsed().as_secs_f64();
                let resting = shared.host.mode().is_some();
                let installed = |theme: &str| shared.host.theme_installed(theme);
                let actions = match shared.core.lock() {
                    Ok(mut core) => core.sample(snapshot, now, minutes_now(), resting, &installed),
                    Err(_) => return,
                };
                for action in actions {
                    match action {
                        Action::Note(line) => shared.host.note(&line),
                        Action::Show(theme, transition) => {
                            shared
                                .host
                                .show_theme(&theme, transition, 0, Scope::Mode(None));
                        }
                        Action::Restore(transition) => shared.host.restore_theme(transition),
                    }
                }
            }),
        )
    }

    fn stop(&mut self) {
        self.shared.stopped.store(true, Ordering::Relaxed);
    }

    fn status(&self) -> Option<Value> {
        let core = self.shared.core.lock().ok()?;
        let now = self.shared.began.elapsed().as_secs_f64();
        Some(Value::Dict(
            core.status(now, self.shared.host.mode().is_some()),
        ))
    }
}
