//! The Settings' hardware (editor/server.py `_hardware`, `_fans`, `_set_hardware`): the names
//! of the processor, graphics card and memory as Libre Panel finds them and as the user
//! wrote them; the fans this computer reads, their names, looks and colours.

use std::path::Path;

use indexmap::IndexMap;
use lp_config::edit::Change;
use lp_py::toml::{Table, Toml};
use lp_py::{Dict, Value};
use lp_render::snapshot::Reading;
use num_bigint::BigInt;

use crate::fan_names::{is_fan, FanNames};

/// `[hardware]`'s settings for the parts' names
const SETTINGS: [(&str, &str); 3] = [
    ("cpu", "cpu_name"),
    ("gpu", "gpu_name"),
    ("memory", "memory_name"),
];
const TABLES: [&str; 3] = ["fans", "fan_looks", "fan_paints"];

/// _natural: a key in the order people count (fan_2 before fan_10).
#[derive(PartialEq, Eq, PartialOrd, Ord)]
enum Part {
    Text(String),
    Number(BigInt),
}

fn natural(key: &str) -> Vec<Part> {
    // (re.split(r"(\d+)", key): text and numbers in turn, a text first and last)
    let mut parts = Vec::new();
    let mut text = String::new();
    let mut digits = String::new();
    for c in key.chars() {
        if c.is_ascii_digit() {
            digits.push(c);
        } else {
            if !digits.is_empty() {
                parts.push(Part::Text(std::mem::take(&mut text)));
                parts.push(Part::Number(digits.parse().unwrap_or_default()));
                digits.clear();
            }
            text.push(c);
        }
    }
    if !digits.is_empty() {
        parts.push(Part::Text(std::mem::take(&mut text)));
        parts.push(Part::Number(digits.parse().unwrap_or_default()));
    }
    parts.push(Part::Text(text));
    parts
}

fn entry(pairs: &[(&str, Value)]) -> Value {
    Value::Dict(
        pairs
            .iter()
            .map(|(key, value)| ((*key).to_string(), value.clone()))
            .collect(),
    )
}

fn card_of(config: &lp_config::Config) -> String {
    match config.sensors.options.get("librehardwaremonitor") {
        Some(table) => match table.get("gpu") {
            Some(Toml::Str(card)) => card.clone(),
            _ => String::new(),
        },
        None => String::new(),
    }
}

/// _fans: the fans this computer reads, each `{key, detected, custom, value, look, paint}`;
/// a fan named but not read now stays in the list, so that its name can be taken back.
fn fans(config: &lp_config::Config, readings: &IndexMap<String, Reading>) -> Value {
    let mut names = FanNames::default();
    names.follow_config(config);
    let hw = &config.hardware;
    let own = &hw.fans;
    let mut found: IndexMap<String, Dict> = IndexMap::new();
    for (key, reading) in readings {
        if !is_fan(key, reading) {
            continue;
        }
        let value = match &reading.value {
            Value::Float(f) if f.is_finite() => {
                lp_py::num::round_float(*f).map_or(Value::None, Value::Int)
            }
            Value::Int(n) => Value::Int(n.clone()),
            Value::Bool(b) => Value::int(i64::from(*b)),
            _ => Value::None,
        };
        let Value::Dict(fan) = entry(&[
            ("key", Value::from(key.as_str())),
            ("detected", Value::Str(names.detected(reading, readings))),
            (
                "custom",
                Value::from(own.get(key).map_or("", String::as_str)),
            ),
            ("value", value),
        ]) else {
            continue;
        };
        found.insert(key.clone(), fan);
    }
    for key in own
        .keys()
        .chain(hw.fan_looks.keys())
        .chain(hw.fan_paints.keys())
    {
        if !found.contains_key(key) {
            let Value::Dict(fan) = entry(&[
                ("key", Value::from(key.as_str())),
                ("detected", Value::from("")),
                (
                    "custom",
                    Value::from(own.get(key).map_or("", String::as_str)),
                ),
                ("value", Value::None),
            ]) else {
                continue;
            };
            found.insert(key.clone(), fan);
        }
    }
    for (key, fan) in &mut found {
        // (its own look and colours; "": as all fans)
        let look = hw.fan_looks.get(key).map_or("", String::as_str);
        let paint = hw.fan_paints.get(key).map_or("", String::as_str);
        fan.insert("look".into(), Value::from(look));
        fan.insert("paint".into(), Value::from(paint));
    }
    let mut keys: Vec<&String> = found.keys().collect();
    keys.sort_by_key(|key| natural(key));
    Value::List(
        keys.iter()
            .map(|key| Value::Dict(found[*key].clone()))
            .collect(),
    )
}

/// `GET /api/hardware`: what Libre Panel finds (`detected`) and the user's own text for each
/// part (`custom`, empty: the detected one), the fans, and how they turn. `refresh` asks the
/// computer again.
pub fn hardware(
    config_file: &Path,
    readings: &IndexMap<String, Reading>,
    refresh: bool,
) -> Result<Value, String> {
    let config =
        lp_config::load_config(config_file, lp_config::platform()).map_err(|why| why.msg)?;
    let found = match refresh {
        true => lp_sensors::hardware::scan_again(),
        false => lp_sensors::hardware::scan(),
    };
    let detected = lp_sensors::hardware::describe(&found, &card_of(&config));
    let own = &config.hardware;
    let custom = [&own.cpu_name, &own.gpu_name, &own.memory_name];
    let mut told = Dict::new();
    for (n, (part, _)) in SETTINGS.iter().enumerate() {
        told.insert(
            (*part).to_string(),
            entry(&[
                ("detected", Value::Str(detected[n].clone())),
                ("custom", Value::Str(custom[n].clone())),
            ]),
        );
    }
    told.insert("fans".into(), fans(&config, readings));
    let looks = lp_render::fan_looks::labels()
        .iter()
        .map(|(id, label)| entry(&[("id", Value::from(*id)), ("label", Value::from(*label))]))
        .collect();
    told.insert(
        "fan_look".into(),
        entry(&[
            ("look", Value::Str(own.fan_look.clone())),
            ("paint", Value::Str(own.fan_paint.clone())),
            ("looks", Value::List(looks)),
        ]),
    );
    Ok(Value::Dict(told))
}

/// fan_table: text by sensor, no sensor blank.
fn fan_table(value: &Value) -> bool {
    match value {
        Value::Dict(d) => d
            .iter()
            .all(|(k, v)| !lp_py::strip(k).is_empty() && matches!(v, Value::Str(_))),
        _ => false,
    }
}

fn one_of(value: &Value, allowed: &[&str]) -> bool {
    matches!(value, Value::Str(s) if s.is_empty() || allowed.contains(&s.as_str()))
}

const EXPECTED: &str = "expected text for \"cpu\", \"gpu\" or \"memory\", \"fans\" by sensor, \
    or the fans' \"fan_look\" and \"fan_paint\" (also \"fan_looks\" and \"fan_paints\" by \
    sensor): {\"cpu\": \"My CPU\", \"fans\": {\"fan.lpc.fan_1\": \"Intake\"}, \"fan_look\": \
    \"libre\"}";

/// `POST /api/hardware`: the user's own text for any of `cpu`, `gpu` and `memory`, and for
/// fans (`{"fans": {"fan.lpc.fan_1": "Intake"}}`); "" gives the detected name back. The fans'
/// look and colours: `fan_look` and `fan_paint` for all of them, and `fan_looks` /
/// `fan_paints` by sensor ("" for "as all of them"). All of it in one write.
pub fn set_hardware(payload: &Value, config_file: &Path) -> Result<(), String> {
    let choices: [(&str, &[&str]); 2] = [
        ("fan_look", &lp_config::FAN_LOOKS),
        ("fan_paint", &lp_config::FAN_PAINTS),
    ];
    let by_fan: [(&str, &[&str]); 2] = [
        ("fan_looks", &lp_config::FAN_LOOKS),
        ("fan_paints", &lp_config::FAN_PAINTS),
    ];
    let Value::Dict(payload) = payload else {
        return Err(EXPECTED.into());
    };
    let special = |k: &str| TABLES.contains(&k) || choices.iter().any(|(c, _)| *c == k);
    let parts: Vec<(&String, &Value)> = payload.iter().filter(|(k, _)| !special(k)).collect();
    let fine = !payload.is_empty()
        && parts
            .iter()
            .all(|(k, _)| SETTINGS.iter().any(|(part, _)| part == k))
        && parts.iter().all(|(_, v)| matches!(v, Value::Str(_)))
        && TABLES.iter().all(|k| payload.get(*k).is_none_or(fan_table))
        && choices
            .iter()
            .all(|(k, allowed)| payload.get(*k).is_none_or(|v| one_of(v, allowed)))
        && by_fan.iter().all(|(k, allowed)| match payload.get(*k) {
            Some(Value::Dict(d)) => d.values().all(|v| one_of(v, allowed)),
            _ => true,
        });
    if !fine {
        return Err(EXPECTED.into());
    }
    let platform = lp_config::platform();
    let mut changes = Vec::new();
    for (part, text) in &parts {
        let setting = SETTINGS.iter().find(|(p, _)| p == part).map_or("", |s| s.1);
        let name = format!("hardware.{setting}");
        let text = Toml::Str(text.str());
        let named = lp_config::hardware_name(&text, &name).map_err(|why| why.msg)?;
        changes.push(Change::Value(name, Toml::Str(named)));
    }
    for (key, _) in &choices {
        if let Some(value) = payload.get(*key) {
            changes.push(Change::Value(
                format!("hardware.{key}"),
                Toml::Str(value.str()),
            ));
        }
    }
    if TABLES.iter().any(|k| payload.contains_key(*k)) {
        let hw = lp_config::load_config(config_file, platform)
            .map_err(|why| why.msg)?
            .hardware;
        let mut kept = [hw.fans, hw.fan_looks, hw.fan_paints];
        for (n, table) in TABLES.iter().enumerate() {
            let Some(Value::Dict(given)) = payload.get(*table) else {
                continue;
            };
            for (key, text) in given {
                let mut text = text.str();
                if n == 0 {
                    let name = format!("hardware.fans ({key})");
                    text =
                        lp_config::hardware_name(&Toml::Str(text), &name).map_err(|why| why.msg)?;
                }
                let key = lp_py::strip(key).to_string();
                if text.is_empty() {
                    kept[n].shift_remove(&key);
                } else {
                    kept[n].insert(key, text);
                }
            }
        }
        let mut sensors: Vec<&String> = Vec::new();
        for key in kept.iter().flat_map(IndexMap::keys) {
            if !sensors.contains(&key) {
                sensors.push(key);
            }
        }
        sensors.sort_by_key(|key| natural(key));
        let rows: Vec<Table> = sensors
            .iter()
            .map(|key| {
                let mut row = Table::new();
                row.insert("sensor".into(), Toml::Str((*key).clone()));
                for (n, field) in ["name", "look", "paint"].iter().enumerate() {
                    if let Some(text) = kept[n].get(*key) {
                        row.insert((*field).to_string(), Toml::Str(text.clone()));
                    }
                }
                row
            })
            .collect();
        changes.push(Change::Tables("hardware.fans".into(), rows));
    }
    lp_config::edit::edit_config(&changes, config_file, platform).map_err(|why| why.msg)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_go_in_the_order_people_count() {
        let mut keys = vec!["fan_10", "fan_2", "fan", "a1b", "fan_1"];
        keys.sort_by_key(|key| natural(key));
        assert_eq!(keys, ["a1b", "fan", "fan_1", "fan_2", "fan_10"]);
    }
}
