//! AI activity: what Claude Code and Codex do on this PC, for a screen that shows it
//! (services/agents.py).
//!
//! The readings come from the board (agents.rs): the agents' hooks and Claude Code's status
//! line, and in the background the plan's usage, asked of the programs themselves while a
//! theme shows `ai.*`:
//!
//! ```toml
//! [services.agents]
//! claude_usage = true   # ask Claude Code every three minutes (2.1.293 and later)
//! codex_usage = true    # ask Codex's app-server every minute
//! task = true           # what the session is called
//! notice = false        # a notice on the panel when a session begins to wait for you
//! ```

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Instant;

use lp_py::datetime::DateTime;
use lp_py::Value;
use lp_render::overlays::Toast;

use crate::agents::{board, Board, AGENTS};
use crate::agents_usage::{self as usage, UsageError};
use crate::host::ServiceHost;
use crate::manager::{Part, Schema, Service};

const TICK_S: f64 = 1.0;
/// a program that did not answer is asked again after this long
const RETRY_S: f64 = 15.0 * 60.0;

fn ai_name(agent: &str) -> &'static str {
    match agent {
        "claude" => "Claude Code",
        _ => "Codex",
    }
}

/// What the service's options say.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Options {
    pub claude_usage: bool,
    pub codex_usage: bool,
    pub task: bool,
    pub notice: bool,
}

/// A notice that a session began to wait for you.
#[derive(Clone, Debug, PartialEq)]
pub struct Waiting {
    pub agent: &'static str,
    /// what the session is called, where it has a name and names are shown
    pub task: Option<String>,
}

/// The readings as the service publishes them (what a session is called stays off the panels
/// where `task` is off), and the agents that began to wait since the look before (`states`:
/// what each was then).
pub fn look(
    readings: Vec<(String, Value)>,
    options: &Options,
    states: &mut HashMap<&'static str, String>,
) -> (Vec<(String, Value)>, Vec<Waiting>) {
    let find = |key: String| readings.iter().find(|(name, _)| *name == key);
    let mut waiting = Vec::new();
    for agent in AGENTS {
        let state = match find(format!("ai.{agent}.state")) {
            Some((_, state)) if state.truthy() => state.str(),
            _ => "off".to_string(),
        };
        let before = states.insert(agent, state.clone());
        let began = before.is_some_and(|before| before != "waiting");
        if state == "waiting" && began && options.notice {
            let task = find(format!("ai.{agent}.task"))
                .filter(|(_, task)| options.task && task.truthy())
                .map(|(_, task)| task.str());
            waiting.push(Waiting { agent, task });
        }
    }
    let shown = readings
        .into_iter()
        .map(
            |(key, value)| match !options.task && key.ends_with(".task") {
                true => (key, Value::None),
                false => (key, value),
            },
        )
        .collect();
    (shown, waiting)
}

/// Today and the Monday of its week, as ISO dates.
fn days() -> Option<(String, String)> {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs_f64();
    let today = lp_py::zones::fromtimestamp(lp_py::zones::local()?, now)?.0;
    let monday = DateTime::from_ordinal(today.toordinal() - i64::from(today.weekday()))?;
    let iso = |day: &DateTime| format!("{:04}-{:02}-{:02}", day.year, day.month, day.day);
    Some((iso(&today), iso(&monday)))
}

struct Shared {
    host: ServiceHost,
    options: Options,
    board: Arc<Board>,
    began: Instant,
    /// when each agent's program is asked next (the service's clock)
    next: Mutex<[f64; 2]>,
    states: Mutex<HashMap<&'static str, String>>,
    stop: AtomicBool,
}

impl Shared {
    fn clock(&self) -> f64 {
        self.began.elapsed().as_secs_f64()
    }

    fn set_next(&self, agent: usize, after: f64) {
        if let Ok(mut next) = self.next.lock() {
            next[agent] = self.clock() + after;
        }
    }

    fn publish(&self) {
        for line in self.board.notes() {
            self.host.note(&line);
        }
        let Ok(mut states) = self.states.lock() else {
            return;
        };
        let (readings, waiting) = look(self.board.readings(), &self.options, &mut states);
        drop(states);
        for (key, value) in readings {
            self.host.publish(&key, value, "", "");
        }
        for Waiting { agent, task } in waiting {
            let words = lp_i18n::t("Waiting for you");
            let mut toast = Toast {
                icon: Some("agent".into()),
                seconds: Some(6.0),
                kind: "agent".into(),
                rank: 2,
                key: format!("ai-{agent}"),
                ..Toast::new(task.unwrap_or_else(|| words.clone()))
            };
            let label = format!("{} · {words}", ai_name(agent));
            toast.payload.insert("label".into(), Value::Str(label));
            self.host.notify(toast);
        }
    }

    // -- the plan's usage ----------------------------------------------------------------------

    fn failed(&self, n: usize, why: &UsageError, after: f64) {
        self.board
            .usage_failed(AGENTS[n], &why.text, &why.reason, &why.version);
        self.set_next(n, after);
    }

    fn ask(&self, n: usize) {
        let agent = AGENTS[n];
        let every = match agent {
            "claude" => usage::CLAUDE_EVERY_S,
            _ => usage::CODEX_EVERY_S,
        };
        let mut passed = Vec::new();
        let path = lp_devices::rights::which(agent, &mut passed);
        for other in passed {
            self.host.note(&format!(
                "{agent} in {} is not started: Libre Panel runs with the highest rights",
                other.parent().unwrap_or(&other).display()
            ));
        }
        let Some(path) = path else {
            let missing = UsageError {
                text: "not installed".into(),
                reason: "missing".into(),
                version: String::new(),
            };
            return self.failed(n, &missing, RETRY_S);
        };
        let quotas = match agent {
            "claude" => usage::ask_claude(&path),
            _ => usage::ask_codex(&path).map(|(quotas, used)| {
                if let (Some(used), Some((today, monday))) = (used, days()) {
                    let (day, week) = usage::codex_tokens(&used, &today, &monday);
                    self.board.tokens(agent, Some(day), Some(week));
                }
                quotas
            }),
        };
        match quotas {
            Ok(quotas) => {
                for (window, (used, resets)) in quotas {
                    self.board.quota(agent, window, used, resets, "background");
                }
                self.set_next(n, every);
            }
            Err(why) => {
                self.host.note(&format!("{agent}: no usage ({})", why.text));
                self.failed(n, &why, every.max(RETRY_S));
            }
        }
    }

    /// The agent whose program is asked now, if one is due.
    fn due(&self) -> Option<usize> {
        let now = self.clock();
        let next = self.next.lock().ok()?;
        let enabled = [self.options.claude_usage, self.options.codex_usage];
        (0..AGENTS.len()).find(|n| enabled[*n] && now >= next[*n])
    }
}

fn run(shared: &Arc<Shared>) {
    let mut asking: Option<JoinHandle<()>> = None;
    while !shared.stop.load(Ordering::Relaxed) {
        shared.publish();
        // (one program at a time, beside the panel)
        let free = asking.as_ref().is_none_or(JoinHandle::is_finished);
        if free && shared.host.hub().shows("ai.") {
            if let Some(agent) = shared.due() {
                let asked = Arc::clone(shared);
                asking = std::thread::Builder::new()
                    .name(format!("agents-{}", AGENTS[agent]))
                    .spawn(move || asked.ask(agent))
                    .ok();
            }
        }
        shared.board.wait_changed(TICK_S); // (a hook comes at once)
    }
}

/// Publishes `ai.*` from the board; asks the agents' programs for the plan's usage.
pub struct Agents {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

fn options() -> Schema {
    vec![
        ("claude_usage", "bool", Value::Bool(true)),
        ("codex_usage", "bool", Value::Bool(true)),
        ("task", "bool", Value::Bool(true)),
        ("notice", "bool", Value::Bool(false)),
    ]
}

/// AI activity as a kind of service.
pub fn part() -> Part {
    Part {
        options,
        make: |host, options| {
            let on = |key: &str, default: bool| options.get(key).map_or(default, Value::truthy);
            Ok(Box::new(Agents {
                shared: Arc::new(Shared {
                    host,
                    options: Options {
                        claude_usage: on("claude_usage", true),
                        codex_usage: on("codex_usage", true),
                        task: on("task", true),
                        notice: on("notice", false),
                    },
                    board: board(),
                    began: Instant::now(),
                    next: Mutex::new([0.0; 2]),
                    states: Mutex::default(),
                    stop: AtomicBool::new(false),
                }),
                thread: None,
            }))
        },
    }
}

impl Service for Agents {
    fn start(&mut self) -> Result<(), String> {
        self.shared.stop.store(false, Ordering::Relaxed);
        let shared = Arc::clone(&self.shared);
        let thread = std::thread::Builder::new()
            .name("service-agents".into())
            .spawn(move || run(&shared));
        self.thread = Some(thread.map_err(|why| why.to_string())?);
        Ok(())
    }

    fn stop(&mut self) {
        self.shared.stop.store(true, Ordering::Relaxed);
        self.shared.board.wake();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
