//! In the background: the plan's usage, asked of the agents' own programs
//! (libre_panel/agents.py). Claude Code (2.1.293 and later) answers a headless usage request
//! without a message to the model; Codex's app-server reads its account's limits.

use std::io::{BufRead, BufReader, Read, Write};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, RecvTimeoutError};
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_py::json::Dumps;
use lp_py::{Dict, Value};
use num_bigint::BigInt;

use crate::agents::{epoch, number, percent, FIVE_HOUR, WEEK};

pub const CLAUDE_MIN_VERSION: [u64; 3] = [2, 1, 293];
pub const CLAUDE_EVERY_S: f64 = 180.0;
pub const CODEX_EVERY_S: f64 = 60.0;
const RUN_TIMEOUT_S: f64 = 30.0;
const REQUEST_ID: &str = "libre-panel-usage";

/// `{five_hour|week: (percent used, resets at)}`.
pub type Quotas = IndexMap<&'static str, (f64, Option<f64>)>;

/// No usage from the program; `reason` says why, for Settings: `missing` (not on this PC),
/// `old` (too old to be asked; `version` the one found), `signed_out`, `no_limits` (the
/// account has no plan's limits), "" (anything else).
#[derive(Clone, Debug, PartialEq)]
pub struct UsageError {
    pub text: String,
    pub reason: String,
    pub version: String,
}

impl UsageError {
    fn plain(text: impl Into<String>) -> UsageError {
        UsageError::because(text, "")
    }

    fn because(text: impl Into<String>, reason: &str) -> UsageError {
        UsageError {
            text: text.into(),
            reason: reason.to_string(),
            version: String::new(),
        }
    }
}

fn table<'a>(data: &'a Dict, key: &str) -> Option<&'a Dict> {
    data.get(key).and_then(Value::as_dict)
}

fn entry<'a>(data: Option<&'a Dict>, key: &str) -> Option<&'a Value> {
    data.and_then(|data| data.get(key))
}

fn is_text(value: Option<&Value>, text: &str) -> bool {
    matches!(value, Some(Value::Str(found)) if found == text)
}

fn one_line(value: &Value) -> String {
    lp_py::json::dumps(value, &Dumps::default())
}

/// Claude Code's request for its usage, as one line of JSON.
pub fn claude_request() -> String {
    let mut request = Dict::new();
    request.insert("subtype".into(), Value::Str("get_usage".into()));
    request.insert("skip_behaviors".into(), Value::Bool(true));
    let mut message = Dict::new();
    message.insert("type".into(), Value::Str("control_request".into()));
    message.insert("request_id".into(), Value::Str(REQUEST_ID.into()));
    message.insert("request".into(), Value::Dict(request));
    one_line(&Value::Dict(message))
}

/// The usage in Claude Code's answer (its lines of JSON).
pub fn claude_usage_reply<'a>(
    lines: impl IntoIterator<Item = &'a str>,
) -> Result<Dict, UsageError> {
    for line in lines {
        let message =
            lp_py::json::loads(line).map_err(|_| UsageError::plain("an answer that is no JSON"))?;
        let Some(message) = message.as_dict() else {
            continue;
        };
        let kind = message.get("type");
        if is_text(kind, "system") {
            continue;
        }
        if !is_text(kind, "control_response") {
            let kind = kind.map_or("None".to_string(), Value::str);
            return Err(UsageError::plain(format!("an unexpected answer ({kind})")));
        }
        let response = table(message, "response");
        if !is_text(entry(response, "request_id"), REQUEST_ID) {
            continue;
        }
        return match (entry(response, "subtype"), entry(response, "response")) {
            (subtype, Some(Value::Dict(data))) if is_text(subtype, "success") => Ok(data.clone()),
            _ => Err(UsageError::because(
                "no usage: sign in to Claude Code again, or update it",
                "signed_out",
            )),
        };
    }
    Err(UsageError::plain("no answer"))
}

/// `{five_hour|week: (percent used, resets at)}` of Claude Code's usage answer.
pub fn claude_quotas(data: &Dict) -> Result<Quotas, UsageError> {
    let none = || UsageError::because("the plan reports no limits", "no_limits");
    let limits = table(data, "rate_limits").filter(|limits| !limits.is_empty());
    let available = data.get("rate_limits_available").is_some_and(Value::truthy);
    let (Some(limits), true) = (limits, available) else {
        return Err(none());
    };
    let mut out = Quotas::new();
    for (window, name) in [(FIVE_HOUR, "five_hour"), (WEEK, "seven_day")] {
        let value = table(limits, name);
        if let Some(used) = percent(entry(value, "utilization")) {
            out.insert(window, (used, epoch(entry(value, "resets_at"))));
        }
    }
    if out.is_empty() {
        return Err(none());
    }
    Ok(out)
}

/// `{five_hour|week: (percent, resets at)}` of Codex's `account/rateLimits/read`: its
/// windows by their length (up to 6 hours: the short one; a day and more: the week).
pub fn codex_quotas(data: &Dict) -> Quotas {
    let mut limits = table(data, "rateLimits").filter(|limits| !limits.is_empty());
    if limits.is_none() {
        limits = table(data, "rateLimitsByLimitId").and_then(|by_id| table(by_id, "codex"));
    }
    let mut out = Quotas::new();
    for name in ["primary", "secondary"] {
        let value = limits.and_then(|limits| table(limits, name));
        let used = percent(entry(value, "usedPercent"));
        let minutes = number(entry(value, "windowDurationMins"));
        let (Some(used), Some(minutes)) = (used, minutes) else {
            continue;
        };
        let window = if minutes <= 360.0 {
            FIVE_HOUR
        } else if minutes >= 1440.0 {
            WEEK
        } else {
            continue;
        };
        out.insert(window, (used, epoch(entry(value, "resetsAt"))));
    }
    out
}

/// Today's and this week's tokens of Codex's `account/usage/read` (days as "YYYY-MM-DD").
pub fn codex_tokens(data: &Dict, today: &str, week_start: &str) -> (f64, f64) {
    let (mut day, mut week) = (0.0, 0.0);
    let buckets = data.get("dailyUsageBuckets").and_then(Value::as_list);
    for bucket in buckets.unwrap_or(&[]) {
        let Some(bucket) = bucket.as_dict() else {
            continue;
        };
        let date = match bucket.get("startDate") {
            Some(date) if date.truthy() => date.str(),
            _ => String::new(),
        };
        let tokens = number(bucket.get("tokens"))
            .filter(|tokens| *tokens != 0.0)
            .unwrap_or(0.0);
        if date == today {
            day += tokens;
        }
        if week_start <= date.as_str() && date.as_str() <= today {
            week += tokens;
        }
    }
    (day, week)
}

/// The version a program names first in its `--version` (digits of any script).
pub fn version_of(text: &str) -> Option<Vec<u64>> {
    let mut rest = text.trim_start_matches(lp_py::isspace).chars().peekable();
    let mut parts = Vec::new();
    for n in 0..3 {
        if n > 0 && rest.next() != Some('.') {
            return None;
        }
        let mut part: Option<u64> = None;
        while let Some(digit) = rest.peek().and_then(|ch| lp_py::decimal(*ch)) {
            let so_far = part.unwrap_or(0);
            part = Some(so_far.saturating_mul(10).saturating_add(u64::from(digit)));
            rest.next();
        }
        parts.push(part?);
    }
    Some(parts)
}

fn hidden(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let _ = command;
}

fn end(mut process: Child) {
    let _ = process.kill();
    let _ = process.wait();
}

pub fn claude_version(path: &Path) -> Option<Vec<u64>> {
    let mut command = Command::new(path);
    command
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    hidden(&mut command);
    let mut process = command.spawn().ok()?;
    let mut output = process.stdout.take()?;
    let (done, wait) = channel();
    let reader = std::thread::Builder::new()
        .name("agents-read".into())
        .spawn(move || {
            let mut said = Vec::new();
            let _ = output.read_to_end(&mut said);
            let _ = done.send(said);
        });
    if reader.is_err() {
        end(process);
        return None;
    }
    let said = wait.recv_timeout(Duration::from_secs_f64(RUN_TIMEOUT_S));
    end(process);
    version_of(&String::from_utf8_lossy(&said.ok()?))
}

/// Claude Code's environment for the usage request: without the switches that would keep it
/// from asking (it counts the request as optional traffic), with its own reports off.
pub fn claude_environment(
    environment: impl IntoIterator<Item = (String, String)>,
) -> Vec<(String, String)> {
    const BLOCKED: [&str; 5] = [
        "DISABLE_TELEMETRY",
        "DISABLE_ERROR_REPORTING",
        "DISABLE_AUTOUPDATER",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "CLAUDE_CODE_ENABLE_TELEMETRY",
    ];
    let mut out: Vec<(String, String)> = environment
        .into_iter()
        .filter(|(name, _)| !BLOCKED.contains(&lp_py::upper(name).as_str()))
        .collect();
    for (name, value) in [
        ("DISABLE_TELEMETRY", "1"),
        ("DISABLE_ERROR_REPORTING", "1"),
        ("DISABLE_AUTOUPDATER", "1"),
        ("CLAUDE_CODE_ENABLE_TELEMETRY", "0"),
    ] {
        out.push((name.to_string(), value.to_string()));
    }
    out
}

/// An answer's `id` where it is a whole number.
fn id_of(message: &Dict) -> Option<BigInt> {
    match message.get("id")? {
        Value::Int(id) => Some(id.clone()),
        Value::Bool(id) => Some(BigInt::from(u8::from(*id))),
        _ => None,
    }
}

/// Whether a line is one of the `wanted` answers: with `ids` a reply with an `id`, else any
/// message that is not a system one.
fn answers(line: &str, ids: bool) -> bool {
    let Ok(Value::Dict(message)) = lp_py::json::loads(line) else {
        return false;
    };
    if ids {
        return id_of(&message).is_some();
    }
    match message.get("type") {
        None | Some(Value::None) => false,
        kind => !is_text(kind, "system"),
    }
}

/// Why a program could not be started, as Python words an OSError.
fn os_error(why: &std::io::Error, path: &Path) -> String {
    let text = why.to_string();
    let plain = text.split(" (os error ").next().unwrap_or(&text);
    let named = lp_py::repr(&path.to_string_lossy());
    match why.raw_os_error() {
        Some(code) if cfg!(windows) => format!("[WinError {code}] {plain}: {named}"),
        Some(code) => format!("[Errno {code}] {plain}: {named}"),
        None => text,
    }
}

/// Starts `command`, writes `requests` (a line each) and gathers the lines it answers until
/// `wanted` answers came (with `ids`: replies with an `id`; else any line that is not a
/// system message), or the time is up; then ends it.
pub fn exchange(
    command: &[String],
    requests: &[String],
    environment: Option<Vec<(String, String)>>,
    wanted: usize,
    ids: bool,
) -> Result<Vec<String>, UsageError> {
    let path = Path::new(&command[0]);
    let mut start = Command::new(path);
    start
        .args(&command[1..])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    if let Some(environment) = environment {
        start.env_clear().envs(environment);
    }
    hidden(&mut start);
    let mut process = start.spawn().map_err(|why| {
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        UsageError::plain(format!("cannot start {name}: {}", os_error(&why, path)))
    })?;
    let (Some(mut input), Some(output)) = (process.stdin.take(), process.stdout.take()) else {
        end(process);
        return Ok(Vec::new());
    };
    let (say, lines) = channel::<Option<String>>();
    let reader = std::thread::Builder::new()
        .name("agents-read".into())
        .spawn(move || {
            let mut output = BufReader::new(output);
            loop {
                let mut line = Vec::new();
                match output.read_until(b'\n', &mut line) {
                    Ok(0) | Err(_) => break,
                    Ok(_) => {}
                }
                if line.ends_with(b"\r\n") {
                    line.truncate(line.len() - 2);
                    line.push(b'\n');
                }
                let Ok(line) = String::from_utf8(line) else {
                    return;
                };
                if say.send(Some(line)).is_err() {
                    return;
                }
            }
            let _ = say.send(None);
        });
    let mut got = Vec::new();
    let written = requests
        .iter()
        .try_for_each(|request| input.write_all(format!("{request}\n").as_bytes()))
        .and_then(|()| input.flush());
    if reader.is_ok() && written.is_ok() {
        let deadline = Instant::now() + Duration::from_secs_f64(RUN_TIMEOUT_S);
        let mut count = 0;
        while count < wanted {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            let line = match lines.recv_timeout(left) {
                Ok(Some(line)) => line,
                Ok(None) | Err(RecvTimeoutError::Timeout | RecvTimeoutError::Disconnected) => break,
            };
            count += usize::from(answers(&line, ids));
            got.push(line);
        }
    }
    drop(input);
    end(process);
    Ok(got)
}

/// The plan's usage from Claude Code itself: a headless usage request, no message to the
/// model, nothing kept of the session.
pub fn ask_claude(path: &Path) -> Result<Quotas, UsageError> {
    let version = claude_version(path);
    if !version
        .as_ref()
        .is_some_and(|v| v[..] >= CLAUDE_MIN_VERSION[..])
    {
        let found = version.map_or(String::new(), |version| {
            let parts: Vec<String> = version.iter().map(u64::to_string).collect();
            parts.join(".")
        });
        let here = if found.is_empty() {
            "no version"
        } else {
            &found
        };
        return Err(UsageError {
            text: format!("Claude Code 2.1.293 or later answers in the background (here: {here})"),
            reason: "old".into(),
            version: found,
        });
    }
    let mut command = vec![path.to_string_lossy().into_owned()];
    command.extend(
        [
            "--safe-mode",
            "--no-session-persistence",
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        .map(String::from),
    );
    let environment = claude_environment(std::env::vars_os().map(|(name, value)| {
        (
            name.to_string_lossy().into_owned(),
            value.to_string_lossy().into_owned(),
        )
    }));
    let lines = exchange(&command, &[claude_request()], Some(environment), 1, false)?;
    claude_quotas(&claude_usage_reply(lines.iter().map(String::as_str))?)
}

/// What Libre Panel says to Codex's app-server, a line each.
pub fn codex_requests() -> Vec<String> {
    let dict = |items: Vec<(&str, Value)>| {
        Value::Dict(items.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
    };
    let text = |value: &str| Value::Str(value.to_string());
    let info = dict(vec![
        ("name", text("libre_panel")),
        ("version", text("1.0")),
    ]);
    let asks = |id: i64, method: &str| {
        dict(vec![
            ("id", Value::int(id)),
            ("method", text(method)),
            ("params", dict(vec![])),
        ])
    };
    vec![
        one_line(&dict(vec![
            ("id", Value::int(1)),
            ("method", text("initialize")),
            ("params", dict(vec![("clientInfo", info)])),
        ])),
        one_line(&dict(vec![
            ("method", text("initialized")),
            ("params", dict(vec![])),
        ])),
        one_line(&asks(2, "account/rateLimits/read")),
        one_line(&asks(3, "account/usage/read")),
    ]
}

/// The account's limits (and its daily tokens where Codex has them) in the app-server's
/// answers.
pub fn codex_reply<'a>(
    lines: impl IntoIterator<Item = &'a str>,
) -> Result<(Quotas, Option<Dict>), UsageError> {
    let mut replies: IndexMap<BigInt, Dict> = IndexMap::new();
    for line in lines {
        if let Ok(Value::Dict(message)) = lp_py::json::loads(line) {
            if let Some(id) = id_of(&message) {
                replies.insert(id, message);
            }
        }
    }
    let result = |id: i64| {
        replies
            .get(&BigInt::from(id))
            .and_then(|r| table(r, "result"))
    };
    let Some(limits) = result(2) else {
        return Err(UsageError::because(
            "Codex reports no limits: sign in to Codex with ChatGPT",
            "signed_out",
        ));
    };
    Ok((codex_quotas(limits), result(3).cloned()))
}

pub fn ask_codex(path: &Path) -> Result<(Quotas, Option<Dict>), UsageError> {
    let command = [path.to_string_lossy().into_owned(), "app-server".into()];
    let lines = exchange(&command, &codex_requests(), None, 3, true)?;
    codex_reply(lines.iter().map(String::as_str))
}
