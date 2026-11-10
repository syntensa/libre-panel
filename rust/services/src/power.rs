//! Power use: the watts the PC draws now, and the energy of today and of this month, with
//! what it costs at the price you give (services/power.py).
//!
//! The watts are those the sensors report (`cpu.power` and `gpu.power`, or the `sensors`
//! named) and `rest_w` for the rest of the PC. It is an estimate: only a meter at the socket
//! knows.
//!
//! Readings: `power.watts`, `power.today` and `power.month` (kWh), with a price
//! `power.today.cost` and `power.month.cost` (in the currency). The totals are kept in the
//! service's folder (`power.json`) every minute and when it stops, so a restart loses
//! nothing; a new day and a new month begin at zero. Time the PC sleeps is not counted.

use std::sync::{Arc, Mutex};
use std::time::Instant;

use lp_py::datetime::DateTime;
use lp_py::{round_digits, Dict, Value};

use crate::host::{Event, ServiceHost};
use crate::manager::{Part, Schema, Service};

/// how often the totals are written
const KEEP_S: f64 = 60.0;
/// a longer pause between two samples (sleep, a stall) is not counted
const GAP_S: f64 = 10.0;
const FILE: &str = "power.json";

/// number: a reading's value as a finite number, or none (services/readings.py).
pub fn number(value: Option<&Value>) -> Option<f64> {
    match value? {
        Value::Int(_) => value?.to_f64(),
        Value::Float(f) if f.is_finite() => Some(*f),
        _ => None,
    }
}

/// The count itself: the watts of a sample, and the energy they add up to.
#[derive(Clone, Debug, PartialEq)]
pub struct Core {
    keys: Vec<String>,
    rest: f64,
    price: f64,
    currency: String,
    day: String,
    month: String,
    today_wh: f64,
    month_wh: f64,
    /// when the last sample came
    last: Option<f64>,
}

fn day_of(wall: &DateTime) -> (String, String) {
    let month = format!("{:04}-{:02}", wall.year, wall.month);
    (format!("{month}-{:02}", wall.day), month)
}

impl Core {
    pub fn new(sensors: &[Value], rest_w: f64, price: f64, currency: &str) -> Core {
        Core {
            keys: sensors
                .iter()
                .map(Value::str)
                .filter(|key| !lp_py::strip(key).is_empty())
                .collect(),
            rest: rest_w.max(0.0),
            price: price.max(0.0),
            currency: currency.to_string(),
            day: String::new(),
            month: String::new(),
            today_wh: 0.0,
            month_wh: 0.0,
            last: None,
        }
    }

    /// The readings the watts are made of.
    pub fn keys(&self) -> &[String] {
        &self.keys
    }

    /// The totals kept before (power.json's text, where there is one), as far as they are
    /// this day's and this month's.
    pub fn load(&mut self, kept: Option<&str>, wall: &DateTime) {
        let kept = match kept.map(lp_py::json::loads) {
            Some(Ok(Value::Dict(kept))) => kept,
            _ => Dict::new(),
        };
        (self.day, self.month) = day_of(wall);
        let same = |key: &str, now: &str| kept.get(key).and_then(Value::as_str) == Some(now);
        if same("day", &self.day) {
            self.today_wh = number(kept.get("today_wh")).unwrap_or(0.0);
        }
        if same("month", &self.month) {
            self.month_wh = number(kept.get("month_wh")).unwrap_or(0.0);
        }
    }

    /// The totals as power.json keeps them.
    pub fn kept(&self) -> String {
        let mut kept = Dict::new();
        kept.insert("day".into(), Value::Str(self.day.clone()));
        kept.insert(
            "today_wh".into(),
            Value::Float(round_digits(self.today_wh, 3)),
        );
        kept.insert("month".into(), Value::Str(self.month.clone()));
        kept.insert(
            "month_wh".into(),
            Value::Float(round_digits(self.month_wh, 3)),
        );
        lp_py::json::dumps(&Value::Dict(kept), &lp_py::json::Dumps::default())
    }

    /// watts: what the PC draws now: the sensors' sum and the rest; none without any sensor.
    fn watts(&self, value_of: &dyn Fn(&str) -> Option<Value>) -> Option<f64> {
        let values: Vec<f64> = self
            .keys
            .iter()
            .filter_map(|key| number(value_of(key).as_ref()))
            .filter(|value| *value >= 0.0)
            .collect();
        if values.is_empty() {
            return None;
        }
        Some(values.iter().fold(0.0, |sum, value| sum + value) + self.rest)
    }

    /// A sample of the readings at `now` (the loop's clock) and `wall` (the day's): its
    /// readings as key, value, unit and label.
    pub fn sample(
        &mut self,
        value_of: &dyn Fn(&str) -> Option<Value>,
        now: f64,
        wall: &DateTime,
    ) -> Vec<(&'static str, Value, String, &'static str)> {
        let watts = self.watts(value_of);
        let (day, month) = day_of(wall);
        if day != self.day {
            self.day = day;
            self.today_wh = 0.0;
        }
        if month != self.month {
            self.month = month;
            self.month_wh = 0.0;
        }
        let last = self.last.replace(now);
        if let (Some(watts), Some(last)) = (watts, last) {
            if 0.0 < now - last && now - last <= GAP_S {
                let energy = watts * (now - last) / 3600.0;
                self.today_wh += energy;
                self.month_wh += energy;
            }
        }
        let (today, month) = (self.today_wh / 1000.0, self.month_wh / 1000.0);
        let float = |value: f64, digits: usize| Value::Float(round_digits(value, digits));
        let mut out = vec![
            (
                "power.watts",
                watts.map_or(Value::None, |watts| float(watts, 1)),
                "W".to_string(),
                "Power",
            ),
            (
                "power.today",
                float(today, 3),
                "kWh".to_string(),
                "Energy today",
            ),
            (
                "power.month",
                float(month, 3),
                "kWh".to_string(),
                "Energy this month",
            ),
        ];
        if self.price > 0.0 {
            let unit = self.currency.clone();
            out.push((
                "power.today.cost",
                float(today * self.price, 2),
                unit.clone(),
                "Cost today",
            ));
            out.push((
                "power.month.cost",
                float(month * self.price, 2),
                unit,
                "Cost this month",
            ));
        }
        out
    }
}

fn wall_now() -> Option<DateTime> {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs_f64();
    Some(lp_py::zones::fromtimestamp(lp_py::zones::local()?, now)?.0)
}

struct Shared {
    core: Mutex<(Core, f64)>,
    host: ServiceHost,
    began: Instant,
}

impl Shared {
    fn save(&self) {
        let Some(folder) = self.host.data_dir() else {
            return;
        };
        let text = match self.core.lock() {
            Ok(core) => core.0.kept(),
            Err(_) => return,
        };
        if let Err(why) = std::fs::write(folder.join(FILE), text) {
            self.host
                .note(&format!("power: the totals cannot be kept: {why}"));
        }
    }
}

/// Watts now, kWh today and this month, and their cost (Power).
pub struct Power {
    shared: Arc<Shared>,
}

fn options() -> Schema {
    let sensors = ["cpu.power", "gpu.power"].map(|key| Value::Str(key.into()));
    vec![
        ("sensors", "list", Value::List(sensors.to_vec())),
        ("rest_w", "number", Value::Float(0.0)),
        ("price", "number", Value::Float(0.0)),
        ("currency", "string", Value::Str("€".into())),
    ]
}

/// The power count as a kind of service.
pub fn part() -> Part {
    Part {
        options,
        make: |host, options| {
            let number = |key: &str| options.get(key).and_then(Value::to_f64).unwrap_or(0.0);
            let sensors = options
                .get("sensors")
                .and_then(Value::as_list)
                .unwrap_or(&[]);
            let currency = options.get("currency").map(Value::str).unwrap_or_default();
            let core = Core::new(sensors, number("rest_w"), number("price"), &currency);
            Ok(Box::new(Power {
                shared: Arc::new(Shared {
                    core: Mutex::new((core, 0.0)),
                    host,
                    began: Instant::now(),
                }),
            }))
        },
    }
}

impl Service for Power {
    fn start(&mut self) -> Result<(), String> {
        let shared = &self.shared;
        let kept = shared
            .host
            .data_dir()
            .and_then(|folder| std::fs::read_to_string(folder.join(FILE)).ok());
        if let (Ok(mut core), Some(wall)) = (shared.core.lock(), wall_now()) {
            core.0.load(kept.as_deref(), &wall);
            let keys: Vec<&str> = core.0.keys().iter().map(String::as_str).collect();
            shared.host.want(&keys);
        }
        let shared = Arc::clone(&self.shared);
        self.shared.host.on(
            "sample",
            Box::new(move |event| {
                let (Event::Sample { snapshot }, Some(wall)) = (event, wall_now()) else {
                    return;
                };
                let now = shared.began.elapsed().as_secs_f64();
                let value_of = |key: &str| snapshot.readings.get(key).map(|r| r.value.clone());
                let (readings, keep) = match shared.core.lock() {
                    Ok(mut core) => {
                        let readings = core.0.sample(&value_of, now, &wall);
                        let keep = now - core.1 >= KEEP_S;
                        if keep {
                            core.1 = now;
                        }
                        (readings, keep)
                    }
                    Err(_) => return,
                };
                for (key, value, unit, label) in readings {
                    shared.host.publish(key, value, &unit, label);
                }
                if keep {
                    shared.save();
                }
            }),
        )
    }

    fn stop(&mut self) {
        self.shared.save();
    }
}
