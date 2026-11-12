//! AI activity's ways in (editor/server.py `_ai_in`, `_ai_setup`): a hook event of Claude
//! Code or Codex, or Claude Code's status line, with the AI key instead of the token; and
//! for Settings, whether the two are there and set up, and Libre Panel's part of their
//! settings shown, added or taken out again.

use std::path::PathBuf;

use lp_py::{Dict, Value};
use lp_services::agents_setup as setup;

/// What a route answers where it does not do what it was asked.
pub enum Refused {
    /// the message and the status
    Said(String, u16),
    /// what Python does not catch: the request is left unanswered
    Failed(String),
}

fn said(message: impl Into<String>, status: u16) -> Refused {
    Refused::Said(message.into(), status)
}

fn agents_on(config: &lp_config::Config) -> bool {
    config.services.enabled.iter().any(|name| name == "agents")
}

fn switched_off() -> String {
    lp_i18n::t("AI activity is switched off (Settings → Game mode and more).")
}

/// `POST /api/ai/hook` and `/api/ai/status` before the body is read: AI activity on, the AI
/// key right, an agent named. The agent.
pub fn admitted(
    config: &lp_config::Config,
    given: Option<&str>,
    agent: &str,
) -> Result<String, Refused> {
    if !agents_on(config) {
        return Err(said(switched_off(), 403));
    }
    let expected = setup::read_key(&lp_config::dirs::config_dir());
    if !crate::access::matches(given, &expected) {
        return Err(said(lp_i18n::t("The AI key is missing or wrong."), 403));
    }
    if !lp_services::agents::AGENTS.contains(&agent) {
        return Err(said("agent: claude or codex", 400));
    }
    Ok(agent.to_string())
}

/// The event's body as a JSON object ("" is `{}`).
pub fn event(body: &[u8]) -> Result<Value, Refused> {
    let text = std::str::from_utf8(body).map_err(|_| said("invalid JSON", 400))?;
    let text = if text.is_empty() { "{}" } else { text };
    let data = lp_py::json::loads(text).map_err(|_| said("invalid JSON", 400))?;
    match data {
        Value::Dict(_) => Ok(data),
        _ => Err(said("expected a JSON object", 400)),
    }
}

fn paths() -> [(&'static str, PathBuf); 2] {
    let home = crate::autostart::user_home();
    let codex = std::env::var_os("CODEX_HOME").filter(|base| !base.is_empty());
    [
        ("claude", setup::claude_settings_path(&home)),
        ("codex", setup::codex_hooks_path(&home, codex.as_deref())),
    ]
}

fn installed(settings: &Dict) -> Dict {
    let found = setup::installed(settings);
    let port = found
        .port
        .and_then(|digits| lp_py::int_of_str(&digits))
        .map_or(Value::None, Value::Int);
    let mut told = Dict::default();
    told.insert("hooks".into(), Value::Bool(found.hooks));
    told.insert("status_line".into(), Value::Bool(found.status_line));
    told.insert(
        "other_status_line".into(),
        Value::Bool(found.other_status_line),
    );
    told.insert("port".into(), port);
    told
}

fn seconds(value: Option<f64>) -> Value {
    value.map_or(Value::None, Value::Float)
}

/// `GET /api/ai/setup`: whether AI activity is on, the server's port, and of each agent
/// whether its program is there, its settings file, Libre Panel's part in it, when it was last
/// heard and where the plan's usage came from.
pub fn setup_state(config: &lp_config::Config, port: u16) -> Value {
    let on = agents_on(config);
    let options = config.services.options.get("agents");
    let board = lp_services::agents::board();
    let mut told = Dict::default();
    told.insert("on".into(), Value::Bool(on));
    told.insert("port".into(), Value::int(i64::from(port)));
    for (agent, path) in paths() {
        let (state, problem) = match setup::read_settings(&path) {
            Ok(current) => (installed(&current), String::new()),
            Err(why) => (Dict::default(), why),
        };
        let (ago, what) = board.last_heard(agent);
        let mut usage = Dict::default();
        if let Some(found) = board.usage(agent) {
            usage.insert("source".into(), Value::Str(found.source));
            usage.insert("ago".into(), seconds(found.ago));
            usage.insert("error".into(), Value::Str(found.error));
            usage.insert("reason".into(), Value::Str(found.reason));
            usage.insert("version".into(), Value::Str(found.version));
        }
        let asked = options.and_then(|o| o.get(&format!("{agent}_usage")));
        let asked = on && !matches!(asked, Some(lp_py::toml::Toml::Bool(false)));
        usage.insert("asked".into(), Value::Bool(asked));
        let program = lp_devices::rights::which(agent, &mut Vec::new()).is_some();
        let mut entry = Dict::default();
        entry.insert("program".into(), Value::Bool(program));
        entry.insert(
            "path".into(),
            Value::Str(path.to_string_lossy().into_owned()),
        );
        entry.extend(state);
        entry.insert("problem".into(), Value::Str(problem));
        entry.insert("heard".into(), seconds(ago));
        entry.insert("heard_what".into(), Value::Str(what));
        entry.insert("usage".into(), Value::Dict(usage));
        told.insert(agent.to_string(), Value::Dict(entry));
    }
    Value::Dict(told)
}

/// `POST /api/ai/setup {"agent", "action": "preview" | "install" | "remove", "status_line"}`:
/// what Libre Panel adds to the agent's settings, or adds it (a copy of the file is kept), or
/// takes it out again.
pub fn set_up(config: &lp_config::Config, payload: &Value, port: u16) -> Result<Value, Refused> {
    let get = |key: &str| match payload {
        Value::Dict(d) => d.get(key),
        _ => None,
    };
    let agent = get("agent").and_then(Value::as_str).unwrap_or("");
    let action = get("action").and_then(Value::as_str).unwrap_or("");
    let path = paths().into_iter().find(|(name, _)| *name == agent);
    let wanted = ["preview", "install", "remove"].contains(&action)
        && matches!(get("action"), Some(Value::Str(_)))
        && matches!(get("agent"), Some(Value::Str(_)));
    let (Some((_, path)), true) = (path, wanted) else {
        return Err(said(
            "expected {\"agent\": \"claude\"|\"codex\", \"action\": ...}",
            400,
        ));
    };
    if !agents_on(config) && action != "remove" {
        return Err(said(switched_off(), 409));
    }
    let file = path.to_string_lossy().into_owned();
    let told = |message: &str, error: String| {
        lp_i18n::t_format(
            message,
            &[
                ("file", Value::Str(file.clone())),
                ("error", Value::Str(error)),
            ],
        )
        .unwrap_or_else(|why| why.msg)
    };
    let current = setup::read_settings(&path)
        .map_err(|why| said(told("{file} cannot be read: {error}", why), 400))?;
    let after = if action == "remove" {
        setup::without_ours(&current)
    } else {
        let folder = lp_config::dirs::config_dir();
        let secret =
            setup::key(&folder, true).map_err(|why| Refused::Failed(format!("OSError: {why}")))?;
        let port = port.to_string();
        let planned = match agent {
            "claude" => {
                let line = matches!(get("status_line"), Some(Value::Bool(true)));
                setup::claude_plan(&current, &port, &secret, line)
            }
            _ => setup::codex_plan(&current, &port, &secret),
        };
        planned.map_err(Refused::Failed)?
    };
    let how = lp_py::json::Dumps::file(2);
    let text = lp_py::json::dumps(&Value::Dict(after.clone()), &how);
    let mut answer = Dict::default();
    if action == "preview" {
        let before = lp_py::json::dumps(&Value::Dict(current), &how);
        answer.insert("path".into(), Value::Str(file));
        answer.insert("after".into(), Value::Str(text));
        answer.insert("before".into(), Value::Str(before));
        return Ok(Value::Dict(answer));
    }
    let kept = setup::write_settings(&path, &after).map_err(|why| {
        let error = lp_theme::model::os_error_of(&why, &path).1;
        said(told("{file} cannot be written: {error}", error), 400)
    })?;
    answer.insert("done".into(), Value::Bool(true));
    answer.insert("path".into(), Value::Str(file));
    answer.insert(
        "backup".into(),
        kept.map_or(Value::None, |kept| {
            Value::Str(kept.to_string_lossy().into_owned())
        }),
    );
    answer.extend(installed(&after));
    Ok(Value::Dict(answer))
}
