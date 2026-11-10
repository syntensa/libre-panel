//! Home Assistant: the states of your entities as readings (sensors/homeassistant.py).
//!
//! `[sensors.homeassistant]` names the server and a long-lived access token. Each entity
//! becomes `ha.<entity id>` with its unit and friendly name; numbers become numbers. Numeric
//! attributes of other entities (a thermostat's `current_temperature`) come as
//! `ha.<entity id>.<attribute>`.

use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use lp_py::Value;
use lp_render::Reading;

use crate::lhm::Readings;

const MISSING: [&str; 4] = ["unavailable", "unknown", "none", ""];
const SKIP_ATTRIBUTES: [&str; 8] = [
    "friendly_name",
    "unit_of_measurement",
    "icon",
    "supported_features",
    "attribution",
    "entity_picture",
    "device_class",
    "state_class",
];

/// as_float: a number as a float; an int too large for one is infinity with its sign.
pub fn as_float(value: &Value) -> Option<f64> {
    match value {
        Value::Float(f) => Some(*f),
        Value::Int(n) => Some(
            lp_py::num::int_to_float(n).unwrap_or_else(|_| match n.sign() {
                num_bigint::Sign::Minus => f64::NEG_INFINITY,
                _ => f64::INFINITY,
            }),
        ),
        _ => None,
    }
}

/// _value: a state as a reading holds it: a number, a text, or nothing.
fn value_of(raw: Option<&Value>) -> Value {
    match raw {
        None | Some(Value::None) => Value::None,
        Some(Value::Bool(on)) => Value::Float(f64::from(u8::from(*on))),
        Some(number @ (Value::Int(_) | Value::Float(_))) => {
            as_float(number).map_or(Value::None, Value::Float)
        }
        Some(other) => {
            let text = other.str();
            let text = lp_py::strip(&text);
            if MISSING.contains(&lp_py::lower(text).as_str()) {
                return Value::None;
            }
            match lp_py::float_of_str(text) {
                Some(number) => Value::Float(number),
                None => Value::Str(text.to_string()),
            }
        }
    }
}

/// `str(table.get(key) or fallback)`.
fn text_or(table: Option<&lp_py::Dict>, key: &str, fallback: &str) -> String {
    match table.and_then(|table| table.get(key)) {
        Some(value) if value.truthy() => value.str(),
        _ => fallback.to_string(),
    }
}

/// readings_from_states: the entities one of the patterns names, as readings. An error where
/// the states are not what Home Assistant gives (Python raises).
pub fn readings_from_states(states: &[Value], patterns: &[String]) -> Result<Readings, String> {
    let patterns: Vec<lp_py::fnmatch::Pattern> = patterns
        .iter()
        .filter_map(|pattern| lp_py::fnmatch::Pattern::new(pattern).ok())
        .collect();
    let mut out = Readings::new();
    for state in states {
        let Some(state) = state.as_dict() else {
            return Err(format!("a state that is a {}", state.type_name()));
        };
        let entity = state.get("entity_id").map(Value::str).unwrap_or_default();
        if entity.is_empty() || !patterns.iter().any(|pattern| pattern.matches(&entity)) {
            continue;
        }
        let attributes = match state.get("attributes") {
            Some(Value::Dict(attributes)) => Some(attributes),
            Some(other) if other.truthy() => {
                return Err(format!("attributes that are a {}", other.type_name()));
            }
            _ => None,
        };
        let name = text_or(attributes, "friendly_name", &entity);
        let unit = text_or(attributes, "unit_of_measurement", "");
        let key = format!("ha.{entity}");
        let reading = Reading {
            unit,
            label: name.clone(),
            ..Reading::of(value_of(state.get("state")))
        };
        out.insert(key.clone(), reading);
        if entity.starts_with("sensor.") || entity.starts_with("binary_sensor.") {
            continue;
        }
        for (attribute, raw) in attributes.into_iter().flatten() {
            if SKIP_ATTRIBUTES.contains(&attribute.as_str()) {
                continue;
            }
            if let Some(number) = as_float(raw) {
                let reading = Reading {
                    label: format!("{name} {}", attribute.replace('_', " ")),
                    ..Reading::of(Value::Float(number))
                };
                out.insert(format!("{key}.{attribute}"), reading);
            }
        }
    }
    Ok(out)
}

/// `[sensors.homeassistant]`.
#[derive(Clone, Debug, PartialEq)]
pub struct Options {
    pub url: String,
    pub token: String,
    /// which entities (patterns)
    pub entities: Vec<String>,
    pub poll_seconds: f64,
}

impl Default for Options {
    fn default() -> Options {
        Options {
            url: "http://homeassistant.local:8123".into(),
            token: String::new(),
            entities: vec!["sensor.*".into(), "binary_sensor.*".into()],
            poll_seconds: 10.0,
        }
    }
}

#[derive(Default)]
struct State {
    stop: bool,
    /// how many times the server was asked, with an answer or without
    rounds: u64,
    latest: Readings,
    notes: Vec<String>,
}

struct Shared {
    state: Mutex<State>,
    changed: Condvar,
}

fn fetch(options: &Options, agent: &str) -> Result<Readings, String> {
    let url = format!("{}/api/states", options.url);
    let token = format!("Bearer {}", options.token);
    let headers = [("Authorization", token.as_str()), ("User-Agent", agent)];
    let data = lp_net::get(&url, &headers, 8.0, 64 << 20).map_err(|why| why.to_string())?;
    let text = String::from_utf8(data).map_err(|why| why.to_string())?;
    match lp_py::json::loads(&text).map_err(|why| why.to_string())? {
        Value::List(states) => readings_from_states(&states, &options.entities),
        _ => Ok(Readings::new()),
    }
}

fn poll(shared: &Shared, options: &Options, agent: &str) {
    let mut warned = false;
    loop {
        let started = Instant::now();
        let found = fetch(options, agent);
        let Ok(mut state) = shared.state.lock() else {
            return;
        };
        match found {
            Ok(readings) => {
                state.latest = readings;
                warned = false;
            }
            // (the server may be away for a while)
            Err(why) if !warned => {
                let hint = match why.contains("401") {
                    true => " (is the token right?)",
                    false => "",
                };
                state.notes.push(format!(
                    "Home Assistant not reachable at {}: {why}{hint}",
                    options.url
                ));
                warned = true;
            }
            Err(_) => {}
        }
        state.rounds += 1;
        shared.changed.notify_all();
        let wait = (options.poll_seconds - started.elapsed().as_secs_f64()).max(0.5);
        let until = Instant::now() + Duration::from_secs_f64(wait);
        while !state.stop {
            let left = until.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            state = match shared.changed.wait_timeout(state, left) {
                Ok((state, _)) => state,
                Err(_) => return,
            };
        }
        if state.stop {
            return;
        }
    }
}

/// Asks Home Assistant in the background; `read` never waits (HomeAssistantProvider).
pub struct HomeAssistantSource {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl HomeAssistantSource {
    /// `agent`: what the program names itself to the server.
    pub fn new(mut options: Options, agent: &str) -> HomeAssistantSource {
        options.url = options.url.trim_end_matches('/').to_string();
        options.poll_seconds = options.poll_seconds.max(2.0);
        let shared = Arc::new(Shared {
            state: Mutex::new(State::default()),
            changed: Condvar::new(),
        });
        let mut thread = None;
        if options.token.is_empty() {
            if let Ok(mut state) = shared.state.lock() {
                let note = "Home Assistant: no token in [sensors.homeassistant]";
                state.notes.push(note.into());
                state.rounds = 1; // (nothing to wait for)
            }
        } else {
            let (theirs, agent) = (Arc::clone(&shared), agent.to_string());
            thread = std::thread::Builder::new()
                .name("sensors-ha".into())
                .spawn(move || poll(&theirs, &options, &agent))
                .ok();
        }
        HomeAssistantSource { shared, thread }
    }

    pub fn read(&mut self) -> Readings {
        self.shared
            .state
            .lock()
            .map(|state| state.latest.clone())
            .unwrap_or_default()
    }

    /// What went wrong since it was last asked.
    pub fn notes(&self) -> Vec<String> {
        self.shared
            .state
            .lock()
            .map(|mut state| std::mem::take(&mut state.notes))
            .unwrap_or_default()
    }

    /// Waits for the first answer (or the first failure), `seconds` at most.
    pub fn wait_ready(&self, seconds: f64) {
        let until = Instant::now() + Duration::from_secs_f64(seconds.max(0.0));
        let Ok(mut state) = self.shared.state.lock() else {
            return;
        };
        while state.rounds == 0 {
            let left = until.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return;
            }
            state = match self.shared.changed.wait_timeout(state, left) {
                Ok((state, _)) => state,
                Err(_) => return,
            };
        }
    }

    /// Stops asking. The thread ends by itself once an answer under way is in.
    pub fn close(&mut self) {
        if let Ok(mut state) = self.shared.state.lock() {
            state.stop = true;
        }
        self.shared.changed.notify_all();
        drop(self.thread.take());
    }
}

impl Drop for HomeAssistantSource {
    fn drop(&mut self) {
        self.close();
    }
}
