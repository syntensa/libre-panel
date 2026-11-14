//! What the cooling analysis stands on: the memory's cooling rows, weighted by the time they
//! hold, and what the fan curves say about them (services/cooling_data.py).
//!
//! A row of the `cooling` table holds until the next one, but no longer than the memory's
//! heartbeat: half an hour of the desktop standing still is one row, a minute of load changes
//! is twelve, so everything is weighed by time, never by rows. From the duty a fan really had
//! one can calculate back the temperature its curve must have seen, which tells which sensor
//! a curve follows.

use std::collections::HashMap;
use std::hint::black_box;
use std::sync::Arc;

use indexmap::IndexMap;
use lp_py::{Dict, Value};

use crate::fancontrol::Curve;
use crate::memory_store::{read_only, rows as asked, value};

pub const DAY_S: f64 = 86400.0;
/// a row holds this long at most (the memory's COOLING_MAX_S)
pub const HEARTBEAT_S: f64 = 60.0;
/// the temperatures that have a column
pub const TEMPS: [&str; 3] = ["cpu_temp", "gpu_temp", "gpu_hotspot"];
/// K a back-calculated course must have moved before a sensor is judged
const SPAN_NEEDED: f64 = 5.0;
/// fewer than this many rows in the rising part of a curve judge nothing
pub const MIN_SAMPLES: usize = 20;
/// distinct JSON texts of a column that are kept while the rows are read
const DECODED_MAX: usize = 50_000;

/// device_sensor: what the panel shows for a device (DEVICE_SENSOR).
pub fn device_sensor(device: &str) -> Option<&'static str> {
    match device {
        "cpu" => Some("cpu_temp"),
        "gpu" => Some("gpu_temp"),
        _ => None,
    }
}

/// `x ** y` as Python computes it (the C library's pow, whatever the exponent).
pub fn pow(x: f64, y: f64) -> f64 {
    x.powf(black_box(y))
}

/// A fan's speed and duty (percent).
pub type FanState = (Option<f64>, Option<f64>);

/// The cooling state from `ts` on, for `duration` seconds. `fans` map a name to (rpm, duty
/// percent), `sources` a curve to the temperature it read.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Row {
    pub ts: f64,
    pub mode: String,
    pub game: Option<String>,
    pub duration: f64,
    pub cpu_temp: Option<f64>,
    pub cpu_power: Option<f64>,
    pub cpu_load: Option<f64>,
    pub gpu_temp: Option<f64>,
    pub gpu_hotspot: Option<f64>,
    pub gpu_power: Option<f64>,
    pub gpu_load: Option<f64>,
    pub outside: Option<f64>,
    pub temps: Arc<IndexMap<String, f64>>,
    pub fans: Arc<IndexMap<String, FanState>>,
    pub sources: Arc<IndexMap<String, f64>>,
}

impl Row {
    pub fn in_game(&self) -> bool {
        self.mode == "game"
    }

    /// temp: a temperature by column (`cpu_temp`) or by the key of the reading it was kept
    /// as.
    pub fn temp(&self, sensor: &str) -> Option<f64> {
        match sensor {
            "cpu_temp" => self.cpu_temp,
            "gpu_temp" => self.gpu_temp,
            "gpu_hotspot" => self.gpu_hotspot,
            _ => self.temps.get(sensor).copied(),
        }
    }

    pub fn duty(&self, fan: &str) -> Option<f64> {
        self.fans.get(fan)?.1
    }

    pub fn rpm(&self, fan: &str) -> Option<f64> {
        self.fans.get(fan)?.0
    }
}

/// A column's JSON text as a dict; an empty one where it is none.
pub fn json(text: &Value) -> Dict {
    match text {
        Value::Str(text) if !text.is_empty() => match lp_py::json::loads(text) {
            Ok(Value::Dict(found)) => found,
            _ => Dict::default(),
        },
        _ => Dict::default(),
    }
}

fn number(value: Option<&Value>) -> Option<f64> {
    match value? {
        value @ (Value::Int(_) | Value::Float(_)) => value.to_f64(),
        _ => None,
    }
}

fn numbers(raw: &Dict) -> IndexMap<String, f64> {
    let kept = raw
        .iter()
        .filter_map(|(k, v)| Some((k.clone(), number(Some(v))?)));
    kept.collect()
}

fn fans(raw: &Dict) -> IndexMap<String, FanState> {
    let kept = raw.iter().filter_map(|(name, pair)| match pair {
        Value::List(pair) if pair.len() == 2 => {
            Some((name.clone(), (number(pair.first()), number(pair.get(1)))))
        }
        _ => None,
    });
    kept.collect()
}

/// Decodes a column's JSON text, once for each distinct text: rows are written when
/// something moves, and then most of what they hold is what the row before held. The rows
/// share what comes back.
struct Once<T> {
    make: fn(&Dict) -> T,
    seen: HashMap<String, Arc<T>>,
    none: Arc<T>,
}

impl<T: Default> Once<T> {
    fn new(make: fn(&Dict) -> T) -> Once<T> {
        Once {
            make,
            seen: HashMap::new(),
            none: Arc::new(T::default()),
        }
    }

    fn decode(&mut self, text: &Value) -> Arc<T> {
        let Value::Str(text) = text else {
            return match text.truthy() {
                true => Arc::new((self.make)(&Dict::default())),
                false => Arc::clone(&self.none),
            };
        };
        if text.is_empty() {
            return Arc::clone(&self.none);
        }
        if let Some(found) = self.seen.get(text) {
            return Arc::clone(found);
        }
        let found = Arc::new((self.make)(&json(&Value::Str(text.clone()))));
        if self.seen.len() < DECODED_MAX {
            self.seen.insert(text.clone(), Arc::clone(&found));
        }
        found
    }
}

/// Which of the rows are kept.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Mode {
    All,
    Game,
    Desktop,
}

/// load_rows: the cooling rows of the last `days` days (None: all), each with the time it
/// holds. A mode keeps only its rows; the time each holds is worked out first, so a row
/// beside a dropped one keeps its own. An error where the file is missing or has no such
/// table.
pub fn load_rows(
    path: &std::path::Path,
    days: Option<f64>,
    mode: Mode,
    now: f64,
) -> Result<Vec<Row>, String> {
    let connection = read_only(path).map_err(|why| format!("OperationalError: {why}"))?;
    let raw = match days.filter(|days| *days != 0.0) {
        Some(days) => {
            let since = format!("{:.0}", (now - days * DAY_S).trunc());
            let since = Value::Int(since.parse().unwrap_or_default());
            asked(
                &connection,
                "SELECT * FROM cooling WHERE ts >= ? ORDER BY ts",
                &[since],
            )?
        }
        None => asked(&connection, "SELECT * FROM cooling ORDER BY ts", &[])?,
    };
    drop(connection);
    let none = Value::None;
    let of = |row: &Dict, key: &str| number(row.get(key));
    let stamp = |row: &Dict| of(row, "ts").unwrap_or(0.0);
    let (mut fans_of, mut temps_of, mut sources_of) =
        (Once::new(fans), Once::new(numbers), Once::new(numbers));
    let mut rows = Vec::with_capacity(raw.len());
    for (i, r) in raw.iter().enumerate() {
        let held = raw
            .get(i + 1)
            .map_or(HEARTBEAT_S, |next| stamp(next) - stamp(r));
        let text = |key: &str| match r.get(key) {
            Some(Value::Str(text)) if !text.is_empty() => Some(text.clone()),
            _ => None,
        };
        rows.push(Row {
            ts: stamp(r),
            mode: text("mode").unwrap_or_else(|| "normal".into()),
            game: text("game").or_else(|| match r.get("game") {
                Some(Value::Str(empty)) => Some(empty.clone()),
                _ => None,
            }),
            duration: if HEARTBEAT_S < held {
                HEARTBEAT_S
            } else {
                held
            },
            cpu_temp: of(r, "cpu_temp"),
            cpu_power: of(r, "cpu_power"),
            cpu_load: of(r, "cpu_load"),
            gpu_temp: of(r, "gpu_temp"),
            gpu_hotspot: of(r, "gpu_hotspot"),
            gpu_power: of(r, "gpu_power"),
            gpu_load: of(r, "gpu_load"),
            outside: of(r, "outside_temp"),
            temps: temps_of.decode(r.get("temps").unwrap_or(&none)),
            fans: fans_of.decode(r.get("fans").unwrap_or(&none)),
            sources: sources_of.decode(r.get("sources").unwrap_or(&none)),
        });
    }
    match mode {
        Mode::Game => rows.retain(Row::in_game),
        Mode::Desktop => rows.retain(|row| !row.in_game()),
        Mode::All => {}
    }
    Ok(rows)
}

/// fancontrol_states: the FanControl states the memory kept, the oldest first: `ts`,
/// `fingerprint` and `state`. Empty without a database.
pub fn fancontrol_states(path: &std::path::Path) -> Vec<Dict> {
    let read = || -> Result<Vec<Dict>, String> {
        let connection = read_only(path).map_err(|why| why.to_string())?;
        let sql = "SELECT ts, fingerprint, state FROM fancontrol ORDER BY ts";
        asked(&connection, sql, &[])
    };
    let found = read().unwrap_or_default().into_iter();
    let states = found.map(|mut row| {
        let state = json(row.get("state").unwrap_or(&Value::None));
        row.insert("state".into(), Value::Dict(state));
        row
    });
    states.collect()
}

/// catalogue: the names of the hardware and the sensors the memory noted (`meta`
/// `sensors`): `cpu` and `gpu` as the readings name them, and `sensors`, every temperature
/// sensor with its id and label and which one the panel shows for the CPU and the GPU.
pub fn catalogue(path: &std::path::Path) -> Dict {
    let read = || -> rusqlite::Result<Option<Value>> {
        let connection = read_only(path)?;
        let mut asked = connection.prepare("SELECT value FROM meta WHERE key = 'sensors'")?;
        let mut rows = asked.query([])?;
        match rows.next()? {
            Some(row) => Ok(Some(value(row.get_ref(0)?))),
            None => Ok(None),
        }
    };
    read()
        .ok()
        .flatten()
        .map_or_else(Dict::default, |text| json(&text))
}

// -- statistics ---------------------------------------------------------------------------------

/// weighted: the quantile (the median at 0.5) of [(value, seconds), ...], by time: the value
/// below which `share` of the time lay.
pub fn weighted(pairs: &[(Option<f64>, f64)], share: f64) -> Option<f64> {
    let kept: Vec<(f64, f64)> = pairs
        .iter()
        .filter_map(|(value, seconds)| Some(((*value)?, *seconds)))
        .filter(|(_, seconds)| *seconds > 0.0)
        .collect();
    let mut order: Vec<usize> = (0..kept.len()).collect();
    order.sort_by(|a, b| {
        (kept[*a].0)
            .partial_cmp(&kept[*b].0)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    // the time up to each value
    let mut reached = Vec::with_capacity(order.len());
    let mut sum = 0.0;
    for (n, at) in order.iter().enumerate() {
        sum = if n == 0 {
            kept[*at].1
        } else {
            sum + kept[*at].1
        };
        reached.push(sum);
    }
    let goal = *reached.last()? * share;
    let at = reached.partition_point(|time| *time < goal);
    Some(kept[order[at.min(order.len() - 1)]].0)
}

/// The quantile of values that are all there.
pub fn weighted_all(pairs: &[(f64, f64)], share: f64) -> Option<f64> {
    let pairs: Vec<(Option<f64>, f64)> = pairs.iter().map(|(v, d)| (Some(*v), *d)).collect();
    weighted(&pairs, share)
}

/// correlation: Pearson's correlation of two series: does one follow the other?
pub fn correlation(pairs: &[(f64, f64)]) -> Option<f64> {
    let n = pairs.len();
    if n < MIN_SAMPLES {
        return None;
    }
    let sum = |values: &mut dyn Iterator<Item = f64>| lp_py::sum_floats(values);
    let mean_x = sum(&mut pairs.iter().map(|(a, _)| *a)) / n as f64;
    let mean_y = sum(&mut pairs.iter().map(|(_, b)| *b)) / n as f64;
    let sxy = sum(&mut pairs.iter().map(|(a, b)| (a - mean_x) * (b - mean_y)));
    let sxx = sum(&mut pairs.iter().map(|(a, _)| pow(a - mean_x, 2.0)));
    let syy = sum(&mut pairs.iter().map(|(_, b)| pow(b - mean_y, 2.0)));
    if sxx <= 1e-9 || syy <= 1e-9 {
        return None;
    }
    Some(sxy / pow(sxx * syy, 0.5))
}

pub fn total(rows: &[Row]) -> f64 {
    lp_py::sum_floats(rows.iter().map(|row| row.duration))
}

/// rounds: how many separate games the rows hold (a gap longer than the heartbeat ends
/// one).
pub fn rounds(rows: &[Row]) -> usize {
    let (mut count, mut last) = (0, None);
    for r in rows.iter().filter(|row| row.in_game()) {
        if last.is_none_or(|last: f64| r.ts - last > HEARTBEAT_S + 1.0) {
            count += 1;
        }
        last = Some(r.ts + r.duration);
    }
    count
}

// -- curves against what happened ------------------------------------------------------------------

/// The rows that know a fan, each with the fan's state in it.
pub type Data<'a> = Vec<(&'a Row, FanState)>;

/// fan_names: every fan the rows know, in the order they first show up.
pub fn fan_names(rows: &[Row]) -> Vec<String> {
    let mut names: Vec<String> = Vec::new();
    let mut last: Option<&Arc<IndexMap<String, FanState>>> = None;
    for r in rows {
        if last.is_some_and(|last| Arc::ptr_eq(last, &r.fans)) {
            continue; // (the same fans as the row before)
        }
        last = Some(&r.fans);
        for name in r.fans.keys() {
            if !names.contains(name) {
                names.push(name.clone());
            }
        }
    }
    names
}

pub fn data_of<'a>(rows: &'a [Row], fan: &str) -> Data<'a> {
    let known = rows.iter().filter_map(|r| Some((r, *r.fans.get(fan)?)));
    known.collect()
}

/// back_calculated: for each row the temperature the curve must have seen to command the
/// duty the fan had.
pub fn back_calculated<'a>(data: &Data<'a>, curve: &Curve) -> Vec<(&'a Row, f64)> {
    let found = data
        .iter()
        .filter_map(|(row, (_rpm, duty))| Some((*row, curve.temp_at((*duty)?)?)));
    found.collect()
}

/// candidates: the sensors a curve might read, among those the memory keeps: the CPU, the
/// GPU, its hot spot and the other temperatures it was asked to keep.
pub fn candidates(rows: &[Row]) -> Vec<String> {
    let mut found: Vec<String> = TEMPS
        .iter()
        .filter(|name| rows.iter().any(|r| r.temp(name).is_some()))
        .map(|name| (*name).to_string())
        .collect();
    let mut extra: Vec<&String> = Vec::new();
    let mut last: Option<&Arc<IndexMap<String, f64>>> = None;
    for r in rows {
        if last.is_some_and(|last| Arc::ptr_eq(last, &r.temps)) {
            continue;
        }
        last = Some(&r.temps);
        extra.extend(r.temps.keys());
    }
    extra.sort();
    extra.dedup();
    found.extend(extra.into_iter().cloned());
    found
}

fn float(value: Option<f64>) -> Value {
    value.map_or(Value::None, Value::Float)
}

/// find_source: which recorded sensor does this curve really follow?
///
/// Judged only once the back-calculated course has moved (`SPAN_NEEDED`): while nothing
/// happens any sensor fits with a constant offset. The order is: first the one that lies on
/// top of it, then the one that correlates best, last the one that scatters least. The
/// verdict is `too_few` (not enough rows), `too_quiet` (nothing moved), `follows` or `none`.
pub fn find_source(data: &Data, curve: &Curve, sensors: &[String]) -> Dict {
    let back = back_calculated(data, curve);
    let samples = Value::int(i64::try_from(back.len()).unwrap_or(i64::MAX));
    let mut out = Dict::default();
    let verdict = |out: &mut Dict, verdict: &str| {
        out.insert("verdict".into(), Value::Str(verdict.into()));
    };
    if back.len() < MIN_SAMPLES {
        verdict(&mut out, "too_few");
        out.insert("samples".into(), samples);
        return out;
    }
    let (mut low, mut high) = (back[0].1, back[0].1);
    for (_, temp) in &back[1..] {
        low = if *temp < low { *temp } else { low };
        high = if *temp > high { *temp } else { high };
    }
    let span = Value::List(vec![Value::Float(low), Value::Float(high)]);
    if high - low < SPAN_NEEDED {
        verdict(&mut out, "too_quiet");
        out.insert("span".into(), span);
        return out;
    }
    struct Found {
        sensor: String,
        spread: Option<f64>,
        offset: Option<f64>,
        r: Option<f64>,
    }
    let mut found = Vec::new();
    for sensor in sensors {
        let pairs: Vec<(f64, f64)> = back
            .iter()
            .filter_map(|(row, t)| Some((*t, row.temp(sensor)?)))
            .collect();
        if pairs.len() < MIN_SAMPLES {
            continue;
        }
        let gaps: Vec<(f64, f64)> = pairs.iter().map(|(t, v)| (t - v, 1.0)).collect();
        let offset = weighted_all(&gaps, 0.5);
        let middle = offset.unwrap_or(0.0);
        let apart: Vec<(f64, f64)> = (pairs.iter())
            .map(|(t, v)| (((t - v) - middle).abs(), 1.0))
            .collect();
        found.push(Found {
            sensor: sensor.clone(),
            spread: weighted_all(&apart, 0.5),
            offset,
            r: correlation(&pairs),
        });
    }
    if found.is_empty() {
        verdict(&mut out, "too_few");
        out.insert("span".into(), span);
        out.insert("samples".into(), samples);
        return out;
    }
    let key = |x: &Found| {
        let far = x.offset.unwrap_or(0.0).abs() > 2.0;
        (far, -x.r.unwrap_or(0.0), x.spread.unwrap_or(0.0))
    };
    found.sort_by(|a, b| {
        let (a, b) = (key(a), key(b));
        (a.0.cmp(&b.0))
            .then(a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
            .then(a.2.partial_cmp(&b.2).unwrap_or(std::cmp::Ordering::Equal))
    });
    let entry = |x: &Found| {
        let mut entry = Dict::default();
        entry.insert("sensor".into(), Value::Str(x.sensor.clone()));
        entry.insert("spread".into(), float(x.spread));
        entry.insert("offset".into(), float(x.offset));
        entry.insert("r".into(), float(x.r));
        Value::Dict(entry)
    };
    let best = &found[0];
    let follows = best.spread.unwrap_or(f64::INFINITY) <= 2.0 && best.r.unwrap_or(0.0) >= 0.9;
    verdict(&mut out, if follows { "follows" } else { "none" });
    out.insert("span".into(), span);
    out.insert("best".into(), entry(best));
    out.insert(
        "others".into(),
        Value::List(found[1..].iter().map(entry).collect()),
    );
    let source = curve.source.clone().map_or(Value::None, Value::Str);
    out.insert("source".into(), source);
    out
}

/// deviation: (median, 95th percentile, seconds) of how far the duty is from what the curve
/// commands for the temperature it read, where that was measured. Never zero: FanControl
/// answers with hysteresis and a delay.
pub fn deviation(data: &Data, curve: &Curve) -> Option<(f64, f64, f64)> {
    let mut pairs = Vec::new();
    for (row, (_rpm, duty)) in data {
        if let (Some(seen), Some(duty)) = (row.sources.get(&curve.name), duty) {
            pairs.push(((duty - curve.duty_at(*seen)).abs(), row.duration));
        }
    }
    let (median, high) = (weighted_all(&pairs, 0.5), weighted_all(&pairs, 0.95));
    if pairs.len() < MIN_SAMPLES {
        return None;
    }
    let seconds = lp_py::sum_floats(pairs.iter().map(|(_, seconds)| *seconds));
    Some((median?, high?, seconds))
}
