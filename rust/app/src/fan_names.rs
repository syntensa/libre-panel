//! The names fans go by on the panel (sensors/fan_names.py): the user's own
//! (`[[hardware.fans]]`), else the one FanControl gives the fan, else the sensor's own ("Fan
//! #1"). FanControl's configuration is only read, and only now and then, when it changed.

use std::time::Instant;

use indexmap::IndexMap;
use lp_render::snapshot::Reading;
use lp_services::paths::Path;

pub const LHM_PREFIX: &str = "lhm:";
/// how often FanControl's file is looked at (its modification time)
const CHECK_S: f64 = 30.0;

/// is_fan: a fan's speed under a key a theme uses (`fan.lpc.fan_1`, `gpu.fan`), not a raw
/// `lhm:` key: those keep the sensor's own name.
pub fn is_fan(key: &str, reading: &Reading) -> bool {
    reading.unit.to_uppercase() == "RPM" && !key.starts_with(LHM_PREFIX)
}

/// The LibreHardwareMonitor id of the sensor a friendly reading repeats, or "".
fn sensor_id(reading: &Reading) -> &str {
    reading.origin.strip_prefix(LHM_PREFIX).unwrap_or("")
}

/// FanControl's names for the fans, by the id of the sensor that measures each: read again
/// when its file changed, looked at every `CHECK_S` at most.
#[derive(Default)]
pub struct FanControlNames {
    pub given: String,
    checked: Option<Instant>,
    stamp: Option<(String, i128, u64)>,
    names: IndexMap<String, String>,
}

impl FanControlNames {
    pub fn names(&mut self) -> &IndexMap<String, String> {
        if self
            .checked
            .is_some_and(|at| at.elapsed().as_secs_f64() < CHECK_S)
        {
            return &self.names;
        }
        self.checked = Some(Instant::now());
        let places = lp_services::steam::Places::here();
        let path: Option<Path> = lp_services::fancontrol::find(&self.given, &places)
            .ok()
            .flatten();
        let Some(path) = path else {
            self.stamp = None;
            self.names.clear();
            return &self.names;
        };
        let text = path.text();
        let Ok(info) = std::fs::metadata(&text) else {
            return &self.names;
        };
        let modified = info
            .modified()
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map_or(0, |d| d.as_nanos() as i128);
        let stamp = (text, modified, info.len());
        if self.stamp.as_ref() != Some(&stamp) {
            // (FanControl may be writing it just now: next time)
            if let Some(section) = lp_services::fancontrol::read_section(&path) {
                self.names = lp_services::fancontrol::names_and_hidden(&section).0;
                self.stamp = Some(stamp);
            }
        }
        &self.names
    }
}

/// Puts the fans' names into the readings.
#[derive(Default)]
pub struct FanNames {
    pub custom: IndexMap<String, String>,
    pub fancontrol: FanControlNames,
}

impl FanNames {
    pub fn follow_config(&mut self, config: &lp_config::Config) {
        self.custom = config.hardware.fans.clone();
        // where FanControl's file is, if the cooling analysis or the memory were told
        let mut given = String::new();
        for service in ["cooling", "memory"] {
            let path = match config.services.options.get(service) {
                Some(table) => match table.get("fancontrol") {
                    Some(lp_py::toml::Toml::Str(path)) => path.as_str(),
                    _ => "",
                },
                None => "",
            };
            if !path.is_empty() {
                given = path.to_string();
                break;
            }
        }
        self.fancontrol.given = given;
    }

    /// The name a fan goes by without one of the user's own: FanControl's, else the
    /// sensor's (the raw reading it repeats keeps that).
    pub fn detected(&mut self, reading: &Reading, readings: &IndexMap<String, Reading>) -> String {
        let sensor = sensor_id(reading);
        if !sensor.is_empty() {
            if let Some(given) = self.fancontrol.names().get(sensor) {
                if !given.is_empty() {
                    return given.clone();
                }
            }
        }
        let raw = match reading.origin.is_empty() {
            true => None,
            false => readings.get(&reading.origin),
        };
        raw.map_or(&reading.label, |raw| &raw.label).clone()
    }

    /// `readings` with each fan under the name it goes by.
    pub fn apply(&mut self, readings: &mut IndexMap<String, Reading>) {
        let wanted = readings.values().any(|r| !sensor_id(r).is_empty());
        let known = match wanted {
            true => self.fancontrol.names().clone(),
            false => IndexMap::new(),
        };
        for (key, reading) in readings.iter_mut() {
            if !is_fan(key, reading) {
                continue;
            }
            let sensor = sensor_id(reading);
            let own = self.custom.get(key).filter(|name| !name.is_empty());
            let name = match own {
                Some(name) => name.clone(),
                None if !sensor.is_empty() => known.get(sensor).cloned().unwrap_or_default(),
                None => String::new(),
            };
            if !name.is_empty() && name != reading.label {
                reading.label = name;
            }
        }
    }
}
