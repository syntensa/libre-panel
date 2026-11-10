//! What the AI coding agents on this PC do: Claude Code and Codex (libre_panel/agents.py).
//!
//! Three ways in, all on this computer; no account and none of their keys is read:
//!
//! - **Hooks**: each event of theirs (a prompt sent, a tool used, a question to you, the
//!   answer done) runs a command that posts it to `/api/ai/hook`.
//! - **Claude Code's status line**: what it would show below the prompt is posted to
//!   `/api/ai/status`, which answers with the line it then shows.
//! - **In the background**: the plan's usage asked of the programs themselves
//!   (agents_usage.rs).
//!
//! This is the board all of them write to and the readings `ai.<agent>.*` it gives.

use std::sync::{Arc, Condvar, Mutex, OnceLock};
use std::time::Duration;

use indexmap::IndexMap;
use lp_py::datetime::{DateTime, Iso};
use lp_py::{Dict, Value};
use sha2::{Digest, Sha256};

pub const AGENTS: [&str; 2] = ["claude", "codex"];
pub const STATES: [&str; 4] = ["working", "waiting", "idle", "off"];
/// of what a session is called, on the one line a theme has for it
pub const MAX_TASK: usize = 120;

/// Hook events and what they say of a session (Claude Code's names; Codex uses the same).
const WORKING: [&str; 10] = [
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PostToolBatch",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "PostCompact",
    "TaskCreated",
];
const IDLE: [&str; 3] = ["SessionStart", "Stop", "StopFailure"];
const WAITING_NOTICES: [&str; 4] = [
    "permission_prompt",
    "elicitation_dialog",
    "elicitation_url_dialog",
    "agent_needs_input",
];
pub const HOOK_EVENTS: [&str; 10] = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "Notification",
    "PreCompact",
    "Stop",
    "StopFailure",
    "SessionEnd",
];
pub const CODEX_HOOK_EVENTS: [&str; 7] = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "PreCompact",
    "Stop",
];

/// working without a word for so long: taken as idle (a closed window)
const WORKING_STALE_S: f64 = 15.0 * 60.0;
const WAITING_STALE_S: f64 = 2.0 * 3600.0;
/// a session that said nothing for so long is over
const SESSION_GONE_S: f64 = 6.0 * 3600.0;
pub const FIVE_HOUR: &str = "five_hour";
pub const WEEK: &str = "week";
/// sessions a theme can list one by one (ai.<agent>.s1 ... .s3)
const LISTED: usize = 3;

fn rank(state: &str) -> i64 {
    match state {
        "waiting" => 3,
        "working" => 2,
        _ => 1,
    }
}

/// The first `count` characters of a text.
fn head(text: &str, count: usize) -> String {
    text.chars().take(count).collect()
}

/// `str(value or "")`.
fn text_of(value: Option<&Value>) -> String {
    match value {
        Some(value) if value.truthy() => value.str(),
        _ => String::new(),
    }
}

/// A table's entry where it is a table itself, else an empty one.
fn table<'a>(data: &'a Dict, key: &str) -> Option<&'a Dict> {
    data.get(key).and_then(Value::as_dict)
}

fn entry<'a>(data: Option<&'a Dict>, key: &str) -> Option<&'a Value> {
    data.and_then(|data| data.get(key))
}

/// A short name for a session id (the id itself is not kept).
pub fn session(value: Option<&Value>) -> String {
    let text = text_of(value);
    if text.is_empty() {
        return "-".into();
    }
    let digest = Sha256::digest(text.as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    hex[..12].to_string()
}

/// What a session is called, where it carries a name; never what was asked of it.
pub fn task(data: &Dict) -> String {
    let Some(Value::Str(value)) = data.get("session_title") else {
        return String::new();
    };
    let words: Vec<&str> = value
        .split(lp_py::isspace)
        .filter(|word| !word.is_empty())
        .collect();
    head(&words.join(" "), MAX_TASK) // one line: a theme has no more room
}

pub fn number(value: Option<&Value>) -> Option<f64> {
    match value? {
        Value::Int(_) => value?.to_f64(),
        Value::Float(f) => Some(*f),
        Value::Str(text) => lp_py::float_of_str(text),
        _ => None,
    }
}

pub fn percent(value: Option<&Value>) -> Option<f64> {
    number(value).filter(|found| (0.0..=100.0).contains(found))
}

/// A moment as seconds since 1970: a number, or an ISO 8601 text.
pub fn epoch(value: Option<&Value>) -> Option<f64> {
    if let Some(found) = number(value) {
        return Some(if found > 1e11 { found / 1000.0 } else { found }); // (milliseconds)
    }
    let Some(Value::Str(text)) = value else {
        return None;
    };
    if text.is_empty() {
        return None;
    }
    match lp_py::datetime::fromisoformat(&text.replace('Z', "+00:00")) {
        Iso::Naive(moment) => lp_py::zones::timestamp(lp_py::zones::local()?, &moment, false),
        Iso::Aware(moment, seconds, micros) => {
            let since = moment.since(&DateTime::date(1970, 1, 1)) - seconds * 1_000_000 - micros;
            Some(lp_py::true_div(i128::from(since), 1_000_000))
        }
        Iso::Invalid => None,
    }
}

/// The day of a moment on this computer (ISO), "" where Python would not know it.
fn day_of(now: f64) -> String {
    lp_py::zones::local()
        .and_then(|zone| lp_py::zones::fromtimestamp(zone, now))
        .map(|(wall, _)| format!("{:04}-{:02}-{:02}", wall.year, wall.month, wall.day))
        .unwrap_or_default()
}

#[derive(Clone, Debug, PartialEq)]
pub struct Session {
    pub state: String,
    pub changed: f64,
    pub seen: f64,
    pub tool: String,
    pub task: String,
    /// what its status line last said it cost so far ($)
    pub cost: Option<f64>,
}

impl Session {
    fn new(changed: f64) -> Session {
        Session {
            state: "idle".into(),
            changed,
            seen: 0.0,
            tool: String::new(),
            task: String::new(),
            cost: None,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Quota {
    /// percent
    pub used: f64,
    /// when, seconds since 1970
    pub resets: Option<f64>,
    /// when it was read
    pub seen: f64,
    pub source: String,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Agent {
    pub sessions: IndexMap<String, Session>,
    pub model: String,
    pub context: Option<f64>,
    pub cost: Option<f64>,
    pub quotas: IndexMap<String, Quota>,
    pub tokens_today: Option<f64>,
    pub tokens_week: Option<f64>,
    pub usage_error: String,
    /// why, for Settings: UsageError's (missing, old, signed_out ...)
    pub usage_reason: String,
    /// the program's version where it is too old ("old")
    pub usage_version: String,
    /// when it last said something (a hook, its status line); 0: never
    pub heard: f64,
    /// what that was (the hook's event, "statusLine")
    pub heard_what: String,
    /// $ of all its sessions today, by their status lines
    pub cost_today: Option<f64>,
    /// the day cost_today is of (ISO date)
    pub cost_day: String,
}

/// Where the plan's usage last came from, for Settings.
#[derive(Clone, Debug, PartialEq)]
pub struct Usage {
    /// "status": its status line; "background": asked in the background; "": none yet
    pub source: String,
    /// how many seconds ago (None: never)
    pub ago: Option<f64>,
    pub error: String,
    pub reason: String,
    pub version: String,
}

#[derive(Default)]
struct Known {
    agents: IndexMap<&'static str, Agent>,
    /// lines for the log: the first word of an agent
    notes: Vec<String>,
}

type Clock = Box<dyn Fn() -> f64 + Send + Sync>;

/// What is known of each agent, from every way in; thread-safe.
pub struct Board {
    clock: Clock,
    known: Mutex<Known>,
    changed: (Mutex<bool>, Condvar),
}

fn system_time() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |since| since.as_secs_f64())
}

impl Default for Board {
    fn default() -> Board {
        Board::new(Box::new(system_time))
    }
}

/// The agent's sessions, each with its state as it stands (one that went stale taken as
/// idle): waiting for you before working before idle, and of the same state the one that
/// came to it last first. Sessions long gone are dropped on the way.
fn ranked(agent: &mut Agent, now: f64) -> Vec<Session> {
    agent
        .sessions
        .retain(|_, session| now - session.seen <= SESSION_GONE_S || (now - session.seen).is_nan());
    let mut found: Vec<Session> = agent
        .sessions
        .values()
        .map(|session| {
            let quiet = now - session.seen;
            let stale = (session.state == "working" && quiet > WORKING_STALE_S)
                || (session.state == "waiting" && quiet > WAITING_STALE_S);
            Session {
                state: if stale {
                    "idle".into()
                } else {
                    session.state.clone()
                },
                ..session.clone()
            }
        })
        .collect();
    found.sort_by(|a, b| {
        (rank(&b.state).cmp(&rank(&a.state))).then(
            (-a.changed)
                .partial_cmp(&-b.changed)
                .unwrap_or(std::cmp::Ordering::Equal),
        )
    });
    found
}

/// What a session cost since its last status line, added to the day's: all of it the first
/// time, and from midnight on what it costs after it.
fn spent(known: &mut Agent, key: &str, cost: f64, now: f64) {
    let day = day_of(now);
    if known.cost_day != day {
        known.cost_day = day;
        known.cost_today = Some(0.0);
    }
    let session = known
        .sessions
        .entry(key.to_string())
        .or_insert_with(|| Session::new(now));
    let grew = match session.cost {
        Some(before) if cost >= before => cost - before,
        _ => cost,
    };
    session.cost = Some(cost);
    known.cost_today = Some(known.cost_today.unwrap_or(0.0) + grew);
}

fn fixed(value: f64) -> String {
    lp_py::format::format(&Value::Float(value), ".0f").unwrap_or_default()
}

/// The line Claude Code shows: the model, the context, the plan's usage.
fn line(known: &Agent, now: f64) -> String {
    let mut parts = Vec::new();
    if !known.model.is_empty() {
        parts.push(known.model.clone());
    }
    if let Some(context) = known.context {
        parts.push(format!("ctx {}%", fixed(context)));
    }
    for (window, label) in [(FIVE_HOUR, "5h"), (WEEK, "7d")] {
        if let Some(quota) = known.quotas.get(window) {
            if !quota.resets.is_some_and(|resets| resets < now) {
                parts.push(format!("{label} {}%", fixed(quota.used)));
            }
        }
    }
    parts.join(" · ")
}

impl Board {
    pub fn new(clock: Clock) -> Board {
        let mut known = Known::default();
        for name in AGENTS {
            known.agents.insert(name, Agent::default());
        }
        Board {
            clock,
            known: Mutex::new(known),
            changed: (Mutex::new(false), Condvar::new()),
        }
    }

    fn with<T>(
        &self,
        agent: &str,
        act: impl FnOnce(&mut Agent, &mut Vec<String>) -> T,
    ) -> Option<T> {
        let mut known = self.known.lock().ok()?;
        let Known { agents, notes } = &mut *known;
        Some(act(agents.get_mut(agent)?, notes))
    }

    /// Ends a wait at once (the service stops).
    pub fn wake(&self) {
        self.set_changed();
    }

    fn set_changed(&self) {
        if let Ok(mut changed) = self.changed.0.lock() {
            *changed = true;
            self.changed.1.notify_all();
        }
    }

    /// Forgets that something changed, then waits until something does, `seconds` at most.
    pub fn wait_changed(&self, seconds: f64) {
        let Ok(mut changed) = self.changed.0.lock() else {
            return;
        };
        *changed = false;
        let wait = Duration::from_secs_f64(seconds.max(0.0));
        let _ = self
            .changed
            .1
            .wait_timeout_while(changed, wait, |changed| !*changed);
    }

    /// Lines for the log since the last call.
    pub fn notes(&self) -> Vec<String> {
        self.known
            .lock()
            .map(|mut known| std::mem::take(&mut known.notes))
            .unwrap_or_default()
    }

    // -- in ----------------------------------------------------------------------------------

    /// One hook event (its JSON as the agent sends it).
    pub fn hook(&self, agent: &str, data: &Value) {
        let Some(data) = data.as_dict() else {
            return;
        };
        let event = text_of(data.get("hook_event_name"));
        let (key, now) = (session(data.get("session_id")), (self.clock)());
        let done = self.with(agent, |known, notes| {
            let what = head(&event, 40);
            heard(
                agent,
                known,
                notes,
                now,
                if what.is_empty() { "?" } else { &what },
            );
            if event == "SessionEnd" {
                known.sessions.shift_remove(&key);
                return;
            }
            let session = known
                .sessions
                .entry(key)
                .or_insert_with(|| Session::new(now));
            session.seen = now;
            let mut state = None;
            if WORKING.contains(&event.as_str()) {
                state = Some("working");
                if event == "PreToolUse" {
                    session.tool = head(&text_of(data.get("tool_name")), 40);
                } else if event == "UserPromptSubmit" {
                    session.tool.clear();
                    session.task = task(data);
                }
            } else if event == "PermissionRequest" {
                state = Some("waiting");
            } else if event == "Notification" {
                let kind = text_of(data.get("notification_type"));
                if WAITING_NOTICES.contains(&kind.as_str()) {
                    state = Some("waiting");
                } else if kind == "idle_prompt" {
                    state = Some("idle");
                }
            } else if IDLE.contains(&event.as_str()) {
                state = Some("idle");
                session.tool.clear();
                if event == "SessionStart" {
                    session.task.clear(); // a session taken up again begins without one
                }
            }
            if let Some(state) = state.filter(|state| *state != session.state) {
                session.state = state.to_string();
                session.changed = now;
            }
        });
        if done.is_some() {
            self.set_changed();
        }
    }

    /// Claude Code's status line data; the line it shows (the model, the context, the plan's
    /// usage), "" where nothing is known.
    pub fn status(&self, agent: &str, data: &Value) -> String {
        let Some(data) = data.as_dict() else {
            return String::new();
        };
        let now = (self.clock)();
        let (model, context) = (table(data, "model"), table(data, "context_window"));
        let (cost, limits) = (table(data, "cost"), table(data, "rate_limits"));
        let shown = self.with(agent, |known, notes| {
            heard(agent, known, notes, now, "statusLine");
            if let Some(name) = entry(model, "display_name").filter(|name| name.truthy()) {
                known.model = head(&name.str(), 40);
            }
            known.context = percent(entry(context, "used_percentage"));
            known.cost = number(entry(cost, "total_cost_usd")).filter(|spent| *spent >= 0.0);
            let key = session(data.get("session_id"));
            if let (Some(cost), true) = (known.cost, key != "-") {
                spent(known, &key, cost, now);
            }
            for (window, name) in [(FIVE_HOUR, "five_hour"), (WEEK, "seven_day")] {
                let value = limits.and_then(|limits| table(limits, name));
                if let Some(used) = percent(entry(value, "used_percentage")) {
                    let quota = Quota {
                        used,
                        resets: epoch(entry(value, "resets_at")),
                        seen: now,
                        source: "status".into(),
                    };
                    known.quotas.insert(window.to_string(), quota);
                }
            }
            if key != "-" {
                let session = known
                    .sessions
                    .entry(key)
                    .or_insert_with(|| Session::new(now));
                session.seen = now;
            }
            line(known, now)
        });
        if shown.is_some() {
            self.set_changed();
        }
        shown.unwrap_or_default()
    }

    /// How many seconds ago the agent last said something, and what (None: never).
    pub fn last_heard(&self, agent: &str) -> (Option<f64>, String) {
        let now = (self.clock)();
        self.with(agent, |known, _| {
            let ago = (known.heard != 0.0).then(|| (now - known.heard).max(0.0));
            (ago, known.heard_what.clone())
        })
        .unwrap_or_default()
    }

    pub fn quota(&self, agent: &str, window: &str, used: f64, resets: Option<f64>, source: &str) {
        let seen = (self.clock)();
        self.with(agent, |known, _| {
            let quota = Quota {
                used,
                resets,
                seen,
                source: source.to_string(),
            };
            known.quotas.insert(window.to_string(), quota);
            known.usage_error.clear();
            known.usage_reason.clear();
            known.usage_version.clear();
        });
        self.set_changed();
    }

    pub fn tokens(&self, agent: &str, today: Option<f64>, week: Option<f64>) {
        self.with(agent, |known, _| {
            known.tokens_today = today;
            known.tokens_week = week;
        });
        self.set_changed();
    }

    pub fn usage_failed(&self, agent: &str, why: &str, reason: &str, version: &str) {
        self.with(agent, |known, _| {
            known.usage_error = why.to_string();
            known.usage_reason = reason.to_string();
            known.usage_version = version.to_string();
        });
    }

    /// Where the plan's usage last came from, and why the last request in the background
    /// brought none.
    pub fn usage(&self, agent: &str) -> Option<Usage> {
        let now = (self.clock)();
        self.with(agent, |known, _| {
            let mut newest: Option<&Quota> = None;
            for quota in known.quotas.values() {
                if newest.is_none_or(|newest| quota.seen > newest.seen) {
                    newest = Some(quota);
                }
            }
            Usage {
                source: newest.map(|quota| quota.source.clone()).unwrap_or_default(),
                ago: newest.map(|quota| (now - quota.seen).max(0.0)),
                error: known.usage_error.clone(),
                reason: known.usage_reason.clone(),
                version: known.usage_version.clone(),
            }
        })
    }

    // -- out ---------------------------------------------------------------------------------

    /// The agent's state (`STATES`), the tool it uses and since when it is so: waiting for
    /// you before working before idle, over its sessions; "off" without any.
    pub fn state(&self, agent: &str) -> (String, String, f64) {
        let now = (self.clock)();
        let best = self
            .with(agent, |known, _| ranked(known, now).into_iter().next())
            .flatten();
        match best {
            Some(best) => (best.state, best.tool, best.changed),
            None => ("off".into(), String::new(), now),
        }
    }

    /// `ai.<agent>.*` as numbers and texts (None: not known), in their order.
    pub fn readings(&self) -> Vec<(String, Value)> {
        let now = (self.clock)();
        let float = |value: Option<f64>| value.map_or(Value::None, Value::Float);
        let text = |value: &str| match value {
            "" => Value::None,
            value => Value::Str(value.to_string()),
        };
        let mut out = Vec::new();
        for agent in AGENTS {
            let values = self.with(agent, |known, _| {
                let ranked = ranked(known, now);
                let best = ranked.first();
                let state = best.map_or("off", |best| best.state.as_str());
                let today = day_of(now);
                let since = best.map_or(now, |best| best.changed);
                let active = known
                    .sessions
                    .values()
                    .filter(|session| now - session.seen <= SESSION_GONE_S)
                    .count();
                let busy = match state {
                    "waiting" => 2,
                    "working" => 1,
                    "idle" => 0,
                    _ => -1,
                };
                let cost_today = known.cost_today.map(|cost| match known.cost_day == today {
                    true => cost,
                    false => 0.0,
                });
                let mut values: Vec<(String, Value)> = vec![
                    ("state".into(), Value::Str(state.to_string())),
                    ("busy".into(), Value::int(busy)),
                    (
                        "tool".into(),
                        Value::Str(best.map(|best| best.tool.clone()).unwrap_or_default()),
                    ),
                    ("task".into(), text(best.map_or("", |best| &best.task))),
                    ("since".into(), Value::Float((now - since).max(0.0))),
                    ("sessions".into(), Value::int(active as i64)),
                    ("model".into(), text(&known.model)),
                    ("context".into(), float(known.context)),
                    ("cost".into(), float(known.cost)),
                    ("tokens_today".into(), float(known.tokens_today)),
                    ("tokens_week".into(), float(known.tokens_week)),
                    // what its sessions cost today, by their status lines (an estimate)
                    ("cost_today".into(), float(cost_today)),
                ];
                // the sessions one by one, as ranked (s1 is the one the state above is of)
                for n in 0..LISTED {
                    let session = ranked.get(n);
                    let name = format!("s{}", n + 1);
                    values.push((
                        format!("{name}.state"),
                        session.map_or(Value::None, |s| Value::Str(s.state.clone())),
                    ));
                    values.push((
                        format!("{name}.task"),
                        session.map_or(Value::None, |s| text(&s.task)),
                    ));
                    values.push((
                        format!("{name}.since"),
                        float(session.map(|s| (now - s.changed).max(0.0))),
                    ));
                }
                for (window, name) in [(FIVE_HOUR, "five_hour"), (WEEK, "week")] {
                    // (a window that began anew: what it said is over)
                    let quota = known
                        .quotas
                        .get(window)
                        .filter(|quota| !quota.resets.is_some_and(|resets| resets < now));
                    let left = quota
                        .and_then(|quota| quota.resets)
                        .filter(|resets| *resets != 0.0)
                        .map(|resets| resets - now);
                    values.push((name.to_string(), float(quota.map(|quota| quota.used))));
                    values.push((format!("{name}_reset"), float(left))); // seconds
                    values.push((
                        format!("{name}_age"),
                        float(quota.map(|quota| now - quota.seen)),
                    ));
                }
                values
            });
            for (name, value) in values.unwrap_or_default() {
                out.push((format!("ai.{agent}.{name}"), value));
            }
        }
        out
    }
}

fn heard(agent: &str, known: &mut Agent, notes: &mut Vec<String>, now: f64, what: &str) {
    if known.heard == 0.0 {
        notes.push(format!("AI activity: the first word from {agent} ({what})"));
    }
    known.heard = now;
    known.heard_what = what.to_string();
}

/// The board every way in writes to (the hooks' address, the service).
pub fn board() -> Arc<Board> {
    static BOARD: OnceLock<Arc<Board>> = OnceLock::new();
    Arc::clone(BOARD.get_or_init(|| Arc::new(Board::default())))
}
