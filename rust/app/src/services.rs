//! The services this build has (src/libre_panel/services), by their names in config.toml's
//! `[services] enabled`; and one of the tests' own, which does at its moments what a file
//! says a service does.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_py::{Dict, Value};
use lp_render::overlays::Toast;
use lp_services::host::{Scope, ServiceHost, Transition};
use lp_services::manager::{Part, Service};

/// the tests' own: a file of steps `[seconds, what, ...]` a service called "script" follows
pub const SCRIPT_ENV: &str = "LP_HOST_SCRIPT";

/// The services of this build.
pub fn parts() -> IndexMap<&'static str, Part> {
    let mut parts = IndexMap::new();
    parts.insert("agents", lp_services::agents_service::part());
    parts.insert("autopilot", lp_services::autopilot::part());
    parts.insert("game", lp_services::game::part());
    parts.insert("notices", lp_services::notices::part());
    parts.insert("power", lp_services::power::part());
    parts.insert("timer", lp_services::timer::part());
    if std::env::var_os(SCRIPT_ENV).is_some() {
        parts.insert(
            "script",
            Part {
                options: Vec::new,
                make: |host, _| Ok(Box::new(Script::new(host))),
            },
        );
    }
    parts
}

/// Does what the steps of the file `LP_HOST_SCRIPT` names say, each at its seconds after the
/// start: `["publish", key, value, unit, label]`, `["state", key, value]`, `["unpublish",
/// key]`, `["want", key, ...]`, `["theme", name or null, transition or null, priority, mode
/// ("*": every)]`, `["mode", name or null, transition or null]`, `["notify", text, kind,
/// rank, seconds or null]`, `["shortcut", action]` (a keyboard shortcut pressed), `["hook",
/// agent, event]` and `["status", agent, data]` (an AI agent's hook and status line).
struct Script {
    host: ServiceHost,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Script {
    fn new(host: ServiceHost) -> Script {
        Script {
            host,
            stop: Arc::default(),
            thread: None,
        }
    }
}

fn follow(host: &ServiceHost, step: &[Value]) -> Result<(), String> {
    let text = |n: usize| step.get(n).and_then(Value::as_str);
    let transition = |n: usize| text(n).map(Transition::named);
    let whole = |n: usize| match step.get(n) {
        Some(Value::Int(n)) => i64::try_from(n).unwrap_or(0),
        _ => 0,
    };
    let key = text(2).unwrap_or("");
    match text(1).unwrap_or("") {
        "publish" => {
            let value = step.get(3).cloned().unwrap_or(Value::None);
            host.publish(key, value, text(4).unwrap_or(""), text(5).unwrap_or(""));
        }
        "state" => host.publish_state(key, step.get(3).unwrap_or(&Value::None), "")?,
        "unpublish" => host.unpublish(key),
        "want" => {
            let keys: Vec<&str> = step[2..].iter().filter_map(Value::as_str).collect();
            host.want(&keys);
        }
        "theme" => match text(2) {
            None => host.restore_theme(transition(3)),
            Some(name) => {
                let scope = match step.get(5) {
                    Some(Value::Str(mode)) if mode == "*" => Scope::Any,
                    Some(Value::Str(mode)) => Scope::Mode(Some(mode.clone())),
                    Some(Value::None) => Scope::Mode(None),
                    _ => Scope::Any,
                };
                host.show_theme(name, transition(3), whole(4), scope);
            }
        },
        "mode" => host.set_mode(text(2), transition(3)),
        "shortcut" => {
            let action = key.to_string();
            host.hub()
                .emit(lp_services::host::Event::Shortcut { action }, false);
        }
        "hook" => lp_services::agents::board().hook(key, step.get(3).unwrap_or(&Value::None)),
        "status" => {
            lp_services::agents::board().status(key, step.get(3).unwrap_or(&Value::None));
        }
        "notify" => {
            let toast = Toast {
                kind: text(3).unwrap_or("").to_string(),
                rank: whole(4),
                seconds: step.get(5).and_then(Value::to_f64),
                ..Toast::new(key)
            };
            host.notify(toast);
        }
        other => return Err(format!("no such step: {other}")),
    }
    Ok(())
}

impl Service for Script {
    fn start(&mut self) -> Result<(), String> {
        let path = std::env::var_os(SCRIPT_ENV).ok_or("no script")?;
        let text = std::fs::read_to_string(path).map_err(|why| why.to_string())?;
        let steps = match lp_py::json::loads(&text).map_err(|why| why.to_string())? {
            Value::List(steps) => steps,
            _ => return Err("the script is no list".into()),
        };
        let (host, stop) = (self.host.clone(), Arc::clone(&self.stop));
        let began = Instant::now();
        let run = move || {
            for step in &steps {
                let Some(step) = step.as_list() else {
                    continue;
                };
                let at = step.first().and_then(Value::to_f64).unwrap_or(0.0);
                while began.elapsed().as_secs_f64() < at {
                    if stop.load(Ordering::Relaxed) {
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(5));
                }
                if let Err(why) = follow(&host, step) {
                    eprintln!("libre-panel: script: {why}");
                }
            }
        };
        self.thread = std::thread::Builder::new()
            .name("service-script".into())
            .spawn(run)
            .ok();
        Ok(())
    }

    fn stop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// config.toml's tables of the services as the services take them.
pub fn options_of(config: &lp_config::Config) -> IndexMap<String, Dict> {
    let tables = config.services.options.iter();
    tables
        .map(|(name, table)| {
            let options = table
                .iter()
                .map(|(key, value)| (key.clone(), crate::sensors::value_of(value)))
                .collect();
            (name.clone(), options)
        })
        .collect()
}
