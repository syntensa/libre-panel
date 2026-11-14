//! Cooling analysis: the fan curves checked against what the PC really did, and what to do
//! (services/cooling.py).
//!
//! The data is the memory's `cooling` table (opened read only: the `memory` service writes
//! it, with `cooling` on) and FanControl's own configuration (read only too). The service
//! does nothing on its own: an editor page or a script asks it, `analysis()` for everything a
//! page draws (and the same as a plain-text report), `recommendations()` for what to change.
//! Nothing here changes FanControl; the user decides.
//!
//! Everything is weighed by the time a row holds, not by rows; the recommendations look only
//! at the data since FanControl was last changed and name the data they rest on; what a curve
//! really reads is found from the duty the fan had (the reverse question). Device names come
//! from the readings, limits from the options.

use std::sync::{Arc, Mutex};
use std::time::Instant;

use indexmap::IndexMap;
use lp_i18n::t;
use lp_py::{Dict, Value};

use crate::cooling_advice::{self as advice, Limit, Series};
use crate::cooling_data::{self as data, device_sensor, weighted_all, Mode, Row};
use crate::cooling_text::{self as text, floor_div, n0, n1, num, s, tf};
use crate::fancontrol::{self, Curve, Fan};
use crate::manager::{Part, Schema, Service};
use crate::memory_store::{last_fingerprint, DATABASE};
use crate::paths::{self, Path, HERE};
use crate::steam::Places;

/// an answer holds this long, and until FanControl's settings change
const CACHE_S: f64 = 300.0;
/// how many answers (periods, modes) are kept at a time
const CACHE_ENTRIES: usize = 8;
/// points: the 95th percentile of the duty's distance from its curve
const BAD_DEVIATION: f64 = 8.0;
/// K: a curve whose sensor is this far from its device's follows another one
const OFFSET_WRONG: f64 = 8.0;
const MIN_MIX_S: f64 = 600.0;

/// The curves by name and the fans by the name they were given.
pub type Setup = (IndexMap<String, Curve>, IndexMap<String, Fan>);

fn table(pairs: Vec<(&str, Value)>) -> Dict {
    pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect()
}

fn float(value: Option<f64>) -> Value {
    value.map_or(Value::None, Value::Float)
}

/// A whole number of Python's.
fn whole(x: f64) -> Value {
    Value::Int(format!("{:.0}", x.trunc()).parse().unwrap_or_default())
}

/// `round(x)`.
fn rounded(x: f64) -> Value {
    whole(lp_py::round(x))
}

fn share_over(pairs: &[(f64, f64)], limit: f64) -> f64 {
    let held = lp_py::sum_floats(pairs.iter().map(|(_, d)| *d));
    if held == 0.0 {
        return 0.0;
    }
    let over = pairs
        .iter()
        .filter(|(gap, _)| *gap > limit)
        .map(|(_, d)| *d);
    lp_py::sum_floats(over) / held
}

/// The curve a series is drawn against: its own, or for a mix the first part that reads a
/// CPU or a GPU (the CPU's first).
fn reference<'a>(
    curves: &'a IndexMap<String, Curve>,
    series: &Series,
) -> (Option<&'a Curve>, Option<String>) {
    let curve = curves.get(&series.curve);
    let Some(mix) = curve.filter(|_| series.device == "mix") else {
        return (curve, Some(series.curve.clone()));
    };
    let parts: Vec<&Curve> = mix.mix.iter().filter_map(|p| curves.get(p)).collect();
    for kind in ["cpu", "gpu"] {
        if let Some(found) = parts.iter().find(|p| p.device() == kind) {
            return (Some(found), Some(found.name.clone()));
        }
    }
    match parts.first() {
        Some(first) => (Some(first), Some(first.name.clone())),
        None => (None, None),
    }
}

/// analysis: everything an editor page draws, as plain data (see docs/PLUGINS.md for the
/// shape).
pub fn analysis(
    rows: &[Row],
    curves: &IndexMap<String, Curve>,
    fans: &IndexMap<String, Fan>,
    names: &Dict,
    recommended: &Dict,
    points: i64,
) -> Dict {
    let (Some(first), Some(last)) = (rows.first(), rows.last()) else {
        return Dict::default();
    };
    let seconds = data::total(rows);
    let (start, end) = (first.ts, last.ts + last.duration);
    let mut states: IndexMap<String, f64> = IndexMap::new();
    let mut games: Vec<(String, f64, f64)> = Vec::new();
    for r in rows {
        let name = text::state_name(r);
        *states.entry(name.clone()).or_insert(0.0) += r.duration;
        if !r.in_game() {
            continue;
        }
        match games.last_mut() {
            Some(last) if last.0 == name && r.ts - last.2 <= data::HEARTBEAT_S + 1.0 => {
                last.2 = r.ts + r.duration;
            }
            _ => games.push((name, r.ts, r.ts + r.duration)),
        }
    }
    let series = advice::series_of(rows, fans, curves);
    let temps = data::candidates(rows);
    let mut findings: Vec<Dict> = Vec::new();
    let mut plots: Vec<Dict> = series
        .iter()
        .map(|series| plot(rows, series, curves, names, &mut findings))
        .collect();
    findings.extend(mix_findings(rows, &series, curves));
    if seconds < text::ENOUGH_S {
        let said = tf(
            "Unused curve points and oscillation are judged from {needed} of recording: so \
             far {have}.",
            &[
                ("needed", s(text::duration(text::ENOUGH_S))),
                ("have", s(text::duration(seconds))),
            ],
        );
        findings.push(table(vec![("level", s("info")), ("text", s(said))]));
    }
    let listed = recommended
        .get("list")
        .and_then(Value::as_list)
        .unwrap_or(&[]);
    let covered: Vec<&Value> = (listed.iter())
        .filter_map(|e| e.as_dict()?.get("id"))
        .collect();
    findings.retain(|f| !f.get("id").is_some_and(|id| covered.contains(&id)));
    let proposals = recommended.get("proposals").and_then(Value::as_dict);
    for plot in &mut plots {
        // a proposal is drawn only where its axis is the proposal's input
        let curve = plot.get("curve").map(Value::str).unwrap_or_default();
        let proposal = proposals.and_then(|proposals| proposals.get(&curve));
        let measured = plot.get("measured").is_some_and(Value::truthy);
        let mix = plot.get("device").and_then(Value::as_str) == Some("mix");
        let drawn = proposal.filter(|p| p.truthy() && !measured && !mix);
        plot.insert("proposal".into(), drawn.cloned().unwrap_or(Value::None));
    }
    let desktop = t("Desktop");
    let mut held: Vec<(&String, &f64)> = states.iter().collect();
    held.sort_by(|a, b| {
        let first = (*a.0 != desktop).cmp(&(*b.0 != desktop));
        first.then(b.1.partial_cmp(a.1).unwrap_or(std::cmp::Ordering::Equal))
    });
    let states = held.into_iter().map(|(name, seconds)| {
        Value::Dict(table(vec![
            ("name", s(name)),
            ("seconds", rounded(*seconds)),
        ]))
    });
    let sessions = games.into_iter().map(|(name, from, to)| {
        Value::Dict(table(vec![
            ("name", s(name)),
            ("from", whole(from)),
            ("to", Value::Float(to)),
        ]))
    });
    let lines = series.iter().map(|series| {
        let fans = series.fans.iter().map(s).collect();
        Value::Dict(table(vec![
            ("curve", s(&series.curve)),
            ("name", s(series.name())),
            ("fans", Value::List(fans)),
            ("device", s(&series.device)),
        ]))
    });
    let fan_rows = data::fan_names(rows).into_iter();
    let fan_rows = fan_rows.map(|name| Value::Dict(fan_row(rows, &name, fans)));
    let all = |items: Vec<Dict>| Value::List(items.into_iter().map(Value::Dict).collect());
    table(vec![
        ("empty", Value::Bool(false)),
        ("from", whole(start)),
        ("to", Value::Float(end)),
        ("seconds", rounded(seconds)),
        ("rows", Value::int(i64::try_from(rows.len()).unwrap_or(0))),
        ("advice", Value::Dict(recommended.clone())),
        ("states", Value::List(states.collect())),
        ("sessions", Value::List(sessions.collect())),
        ("series", Value::List(lines.collect())),
        (
            "history",
            Value::Dict(history(rows, &series, &temps, points, start, end)),
        ),
        ("plots", all(plots)),
        ("table", Value::List(fan_rows.collect())),
        ("findings", all(findings)),
    ])
}

/// The course in `points` bins, each the mean over the time it covers; a bin that is less
/// than a third covered stays empty (a gap stays a gap).
fn history(
    rows: &[Row],
    series: &[Series],
    temps: &[String],
    points: i64,
    start: f64,
    end: f64,
) -> Dict {
    let n = usize::try_from(points.max(10)).unwrap_or(10);
    let width = (end - start) / n as f64;
    let width = if width > 1.0 { width } else { 1.0 };
    let fields = temps.len() + series.len();
    let mut sums = vec![vec![0.0f64; n]; fields];
    let mut weights = vec![vec![0.0f64; n]; fields];
    let mut covered = vec![0.0f64; n];
    for r in rows {
        let values = (temps.iter().map(|sensor| r.temp(sensor)))
            .chain(series.iter().map(|s| r.duty(&s.fans[0])));
        let values: Vec<Option<f64>> = values.collect();
        let (a, e) = (r.ts, r.ts + r.duration);
        let first = floor_div(a - start, width);
        if first < 0.0 {
            continue;
        }
        let mut i = first as usize;
        while i < n && start + i as f64 * width < e {
            let (upper, lower) = (start + (i + 1) as f64 * width, start + i as f64 * width);
            let share = (if upper < e { upper } else { e }) - (if lower > a { lower } else { a });
            if share > 0.0 {
                covered[i] += share;
                for (k, value) in values.iter().enumerate() {
                    if let Some(value) = value {
                        sums[k][i] += value * share;
                        weights[k][i] += share;
                    }
                }
            }
            i += 1;
        }
    }
    let course = |k: usize| -> Value {
        let means = (0..n).map(|i| match weights[k][i] > 0.0 && covered[i] >= width / 3.0 {
            true => Value::Float(lp_py::round_digits(sums[k][i] / weights[k][i], 1)),
            false => Value::None,
        });
        Value::List(means.collect())
    };
    let times = (0..n).map(|i| rounded(start + (i as f64 + 0.5) * width));
    let temperatures: Dict = (temps.iter().enumerate())
        .map(|(k, sensor)| (sensor.clone(), course(k)))
        .collect();
    let duty: Dict = (series.iter().enumerate())
        .map(|(k, series)| (series.curve.clone(), course(temps.len() + k)))
        .collect();
    table(vec![
        ("t", Value::List(times.collect())),
        ("temps", Value::Dict(temperatures)),
        ("duty", Value::Dict(duty)),
    ])
}

/// One curve against the duty its fans had, and what that says: the cloud of (input, duty,
/// seconds), the curve, and a finding if the fans do not follow it.
fn plot(
    rows: &[Row],
    series: &Series,
    curves: &IndexMap<String, Curve>,
    names: &Dict,
    findings: &mut Vec<Dict>,
) -> Dict {
    let curve = curves.get(&series.curve);
    let fan = &series.fans[0];
    let kind = series.device.as_str();
    let (ref_curve, ref_name) = reference(curves, series);
    let ref_kind = ref_curve.map_or(kind, |curve| curve.device());
    let sensor = device_sensor(ref_kind);
    let mut note = Value::None;
    if let Some(curve) = curve.filter(|_| kind == "mix") {
        note = s(tf(
            "Mix curve of {parts}: the fan follows what they give together. \
             The line shows only the part {part}.",
            &[
                ("parts", s(curve.mix.join(&t(" and ")))),
                ("part", s(ref_name.clone().unwrap_or_else(|| "?".into()))),
            ],
        ));
    }
    let mut cloud: IndexMap<(u64, u64), f64> = IndexMap::new();
    let mut measured = 0usize;
    let half = |x: f64| lp_py::round(x * 2.0) / 2.0 + 0.0;
    for r in rows {
        let Some(duty) = r.duty(fan) else {
            continue;
        };
        let mut x = ref_name
            .as_ref()
            .and_then(|name| r.sources.get(name).copied());
        match (x, sensor) {
            (Some(_), _) => measured += 1,
            (None, Some(sensor)) => x = r.temp(sensor),
            _ => {}
        }
        if let Some(x) = x {
            let key = (half(x).to_bits(), half(duty).to_bits());
            *cloud.entry(key).or_insert(0.0) += r.duration;
        }
    }
    let real_input = measured as f64 > 0.5 * rows.len() as f64;
    let mut dots: Vec<(f64, f64, f64)> = cloud
        .iter()
        .map(|((x, y), d)| (f64::from_bits(*x), f64::from_bits(*y), lp_py::round(*d)))
        .collect();
    dots.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));
    dots.truncate(800);
    let dots = dots
        .into_iter()
        .map(|(x, y, d)| Value::List(vec![Value::Float(x), Value::Float(y), whole(d)]));
    let target = ref_curve.map_or_else(Vec::new, |curve| {
        let pair = |(t, d): &(f64, f64)| Value::List(vec![Value::Float(*t), Value::Float(*d)]);
        curve.points.iter().map(pair).collect()
    });
    let x_name = match real_input {
        true => t("Curve input (measured)"),
        false => text::device_name(ref_kind, names),
    };
    let mut plot = table(vec![
        ("curve", s(&series.curve)),
        ("name", s(series.name())),
        ("device", s(kind)),
        ("target", Value::List(target)),
        ("x_name", s(x_name)),
        ("measured", Value::Bool(real_input)),
        ("cloud", Value::List(dots.collect())),
        ("note", note),
        ("proposal", Value::None),
    ]);
    let Some(curve) = curve.filter(|curve| kind != "mix" && !curve.points.is_empty()) else {
        return plot;
    };
    let pairs = data::data_of(rows, fan);
    let found = match real_input {
        true => data::deviation(&pairs, curve),
        false => None,
    };
    if let Some((median, high, _)) = found {
        let ok = high <= BAD_DEVIATION;
        let named = [("fans", s(series.name())), ("curve", s(&curve.name))];
        let said = match ok {
            true => tf(
                "{fans} follows the curve {curve}: {median} points off on average, p95 {high}: \
                 that is hysteresis and response time of FanControl.",
                &[
                    named[0].clone(),
                    named[1].clone(),
                    ("median", n1(median)),
                    ("high", n1(high)),
                ],
            ),
            false => tf(
                "{fans} deviates from the curve {curve}: p95 {high} points.",
                &[named[0].clone(), named[1].clone(), ("high", n1(high))],
            ),
        };
        let level = if ok { "ok" } else { "warn" };
        findings.push(table(vec![("level", s(level)), ("text", s(said))]));
    } else if let Some(sensor) = sensor.filter(|_| !real_input) {
        source_finding(
            rows, series, curve, sensor, ref_kind, names, &mut plot, findings,
        );
    }
    plot
}

/// The input is not measured: work out what the curve must have seen and compare it with
/// the sensor the curve should hang on.
#[allow(clippy::too_many_arguments)]
fn source_finding(
    rows: &[Row],
    series: &Series,
    curve: &Curve,
    sensor: &str,
    kind: &str,
    names: &Dict,
    plot: &mut Dict,
    findings: &mut Vec<Dict>,
) {
    let pairs = data::data_of(rows, &series.fans[0]);
    let back: Vec<(f64, f64, f64)> = data::back_calculated(&pairs, curve)
        .into_iter()
        .filter_map(|(r, calc)| Some((calc, r.temp(sensor)?, r.duration)))
        .collect();
    if back.len() < data::MIN_SAMPLES {
        return;
    }
    let of = |pick: &dyn Fn(&(f64, f64, f64)) -> f64| {
        let picked: Vec<(f64, f64)> = back.iter().map(|p| (pick(p), p.2)).collect();
        weighted_all(&picked, 0.5).unwrap_or(0.0)
    };
    let (seen, real, offset) = (of(&|p| p.0), of(&|p| p.1), of(&|p| p.0 - p.1));
    let place = text::device_name(kind, names);
    let source = curve.source.clone().filter(|source| !source.is_empty());
    let source = source.unwrap_or_else(|| "?".into());
    if offset.abs() <= 2.0 {
        let said = tf(
            "{fans} follows the curve {curve} at the {where} (worked back {offset} K off).",
            &[
                ("fans", s(series.name())),
                ("curve", s(&curve.name)),
                ("where", s(&place)),
                ("offset", s(num(offset, 1, true))),
            ],
        );
        findings.push(table(vec![("level", s("ok")), ("text", s(said))]));
    }
    if offset.abs() > OFFSET_WRONG {
        let said = tf(
            "{fans} is not tied to the {where}: the curve {curve} sees {seen} °C on \
             average, the {where} was at {real} °C. FanControl reads {source} for this \
             curve, which the panel does not know. The points therefore lie beside the \
             line.",
            &[
                ("fans", s(series.name())),
                ("where", s(&place)),
                ("curve", s(&curve.name)),
                ("seen", n0(seen)),
                ("real", n0(real)),
                ("source", s(&source)),
            ],
        );
        findings.push(table(vec![
            ("level", s("warn")),
            ("id", s(format!("source:{}", curve.name))),
            ("text", s(said)),
        ]));
        let note = tf(
            "Drawn over the {where}, but the curve reads {source}.",
            &[("where", s(&place)), ("source", s(&source))],
        );
        plot.insert("note".into(), s(note));
    }
}

/// Does a mix take the higher of its parts? Measured from the duty of fans on each.
fn mix_findings(rows: &[Row], series: &[Series], curves: &IndexMap<String, Curve>) -> Vec<Dict> {
    let mut found = Vec::new();
    for mixed in series.iter().filter(|series| series.device == "mix") {
        let Some(curve) = curves.get(&mixed.curve) else {
            continue;
        };
        for part in &curve.mix {
            let Some(direct) = series.iter().find(|x| x.curve == *part) else {
                continue;
            };
            let gaps: Vec<(f64, f64, f64, f64)> = rows
                .iter()
                .filter_map(|r| {
                    let (mix, own) = (r.duty(&mixed.fans[0])?, r.duty(&direct.fans[0])?);
                    Some((own - mix, r.duration, own, mix))
                })
                .collect();
            if lp_py::sum_floats(gaps.iter().map(|g| g.1)) < MIN_MIX_S {
                continue;
            }
            let pairs: Vec<(f64, f64)> = gaps.iter().map(|g| (g.0, g.1)).collect();
            let share = share_over(&pairs, advice::JUMP);
            if share >= 0.5 {
                let middle = |own: bool| {
                    let pick = |g: &(f64, f64, f64, f64)| if own { g.2 } else { g.3 };
                    let picked: Vec<(f64, f64)> = gaps.iter().map(|g| (pick(g), g.1)).collect();
                    weighted_all(&picked, 0.5).unwrap_or(0.0)
                };
                let said = tf(
                    "{fans} ({curve}) does not follow the higher part: {part} was more \
                     than {jump} points above for {share} % of the time ({own} % against \
                     {mixed} % on average). The mix does not pass the value of {part} \
                     on: if it should take the maximum, the mix function in FanControl \
                     is wrong.",
                    &[
                        ("fans", s(mixed.name())),
                        ("curve", s(&mixed.curve)),
                        ("part", s(part)),
                        ("jump", n0(advice::JUMP)),
                        ("share", n0(100.0 * share)),
                        ("own", n0(middle(true))),
                        ("mixed", n0(middle(false))),
                    ],
                );
                found.push(table(vec![
                    ("level", s("warn")),
                    ("id", s(format!("mix:{}", mixed.curve))),
                    ("text", s(said)),
                ]));
            }
        }
    }
    found
}

/// The numbers of one fan: duty and speed (median, 95th percentile, highest), how much of
/// the time at its minimum, and the speed at each duty.
fn fan_row(rows: &[Row], name: &str, fans: &IndexMap<String, Fan>) -> Dict {
    let fan = text::fan_of(fans, name);
    let pairs = data::data_of(rows, name);
    let duty: Vec<(f64, f64)> = pairs
        .iter()
        .filter_map(|(r, (_rpm, d))| Some(((*d)?, r.duration)))
        .collect();
    let speed: Vec<(f64, f64)> = pairs
        .iter()
        .filter_map(|(r, (rpm, _d))| Some(((*rpm)?, r.duration)))
        .collect();
    let held = lp_py::sum_floats(pairs.iter().map(|(r, _)| r.duration));
    let held = if held == 0.0 { 1.0 } else { held };
    let minimum = fan
        .and_then(|fan| fan.minimum)
        .filter(|least| *least != 0.0);
    let minimum = minimum.unwrap_or(20.0);
    let mut steps: IndexMap<i64, Vec<(f64, f64)>> = IndexMap::new();
    for (r, (rpm, d)) in &pairs {
        if let (Some(rpm), Some(d)) = (rpm, d) {
            let step = floor_div(*d, 10.0) as i64 * 10;
            steps.entry(step).or_default().push((*rpm, r.duration));
        }
    }
    steps.sort_keys();
    let highest = |values: &[(f64, f64)]| -> Value {
        let mut top: Option<f64> = None;
        for (value, _) in values {
            if top.is_none_or(|top| *value > top) {
                top = Some(*value);
            }
        }
        float(top)
    };
    let minimum_share = match duty.is_empty() {
        true => Value::None,
        false => {
            let low = duty.iter().filter(|(d, _)| *d <= minimum + 2.0);
            let low = lp_py::sum_floats(low.map(|(_, s)| *s));
            Value::Float(lp_py::round_digits(100.0 * low / held, 1))
        }
    };
    let characteristic = steps.iter().filter_map(|(step, v)| {
        let speed = weighted_all(v, 0.5).filter(|speed| *speed != 0.0)?;
        Some(Value::List(vec![Value::int(*step), rounded(speed)]))
    });
    let curve = fan.map_or(Value::None, |fan| s(&fan.curve));
    table(vec![
        ("fan", s(name)),
        ("curve", curve),
        ("p50", float(weighted_all(&duty, 0.5))),
        ("p95", float(weighted_all(&duty, 0.95))),
        ("max", highest(&duty)),
        ("minimum_share", minimum_share),
        ("rpm50", float(weighted_all(&speed, 0.5))),
        ("rpm_max", highest(&speed)),
        ("characteristic", Value::List(characteristic.collect())),
    ])
}

// -- the analyst: the work, and what it keeps -----------------------------------------------------

#[derive(Clone)]
enum Kept {
    Table(Dict),
    Lines(Vec<String>),
}

type Key = (String, String, String, Option<fancontrol::Stamp>, String);

struct Inner {
    watch: fancontrol::Watch,
    setup: Option<(Option<fancontrol::Stamp>, Arc<Setup>)>,
    kept: IndexMap<Key, (f64, Kept)>,
}

fn wall() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |since| since.as_secs_f64())
}

fn mode_name(mode: Mode) -> &'static str {
    match mode {
        Mode::All => "",
        Mode::Game => "game",
        Mode::Desktop => "desktop",
    }
}

/// Reads the file and FanControl, and keeps what it worked out: each answer for `CACHE_S`
/// seconds, and until FanControl's settings change (a new state in the file, or the
/// configuration itself), per language and per question (period, mode).
pub struct Analyst {
    database: std::path::PathBuf,
    limits: Vec<Limit>,
    began: Instant,
    inner: Mutex<Inner>,
}

impl Analyst {
    pub fn new(
        database: &std::path::Path,
        limits: Vec<Limit>,
        watch: fancontrol::Watch,
    ) -> Analyst {
        Analyst {
            database: database.to_path_buf(),
            limits,
            began: Instant::now(),
            inner: Mutex::new(Inner {
                watch,
                setup: None,
                kept: IndexMap::new(),
            }),
        }
    }

    pub fn database(&self) -> &std::path::Path {
        &self.database
    }

    /// What the watch over FanControl's file has to say for the log.
    pub fn notes(&self) -> Vec<String> {
        let inner = self.inner.lock();
        inner.map_or_else(
            |_| Vec::new(),
            |mut inner| std::mem::take(&mut inner.watch.notes),
        )
    }

    /// FanControl's file, where there is one.
    pub fn fancontrol(&self) -> Option<Path> {
        self.inner.lock().ok()?.watch.path.clone()
    }

    /// read_setup: the curves and the fans in FanControl's configuration now (empty without
    /// one). Read again only when the file changed.
    pub fn setup(&self) -> Arc<Setup> {
        let Ok(mut inner) = self.inner.lock() else {
            return Arc::default();
        };
        let stamp = inner.watch.stamp().unwrap_or(None);
        if let Some((kept, found)) = &inner.setup {
            if *kept == stamp {
                return Arc::clone(found);
            }
        }
        let path = inner.watch.path.clone().filter(|_| stamp.is_some());
        let section = path.and_then(|path| fancontrol::read_section(&path));
        let found = section
            .filter(|section| !section.is_empty())
            .map(|section| fancontrol::curves_and_fans(&fancontrol::state_of(&section)));
        let found = Arc::new(found.unwrap_or_default());
        inner.setup = Some((stamp, Arc::clone(&found)));
        found
    }

    fn names(&self) -> Dict {
        data::catalogue(&self.database)
    }

    fn key(&self, question: String) -> Key {
        let stamp = match self.inner.lock() {
            Ok(mut inner) => inner.watch.stamp().unwrap_or(None),
            Err(_) => None,
        };
        (
            question,
            self.database.display().to_string(),
            last_fingerprint(&self.database),
            stamp,
            lp_i18n::language().code().to_string(),
        )
    }

    fn recall(&self, key: &Key) -> Option<Kept> {
        let inner = self.inner.lock().ok()?;
        let (at, kept) = inner.kept.get(key)?;
        (self.began.elapsed().as_secs_f64() - at < CACHE_S).then(|| kept.clone())
    }

    fn keep(&self, key: Key, value: Kept) {
        if let Ok(mut inner) = self.inner.lock() {
            inner.kept.shift_remove(&key);
            let now = self.began.elapsed().as_secs_f64();
            inner.kept.insert(key, (now, value));
            while inner.kept.len() > CACHE_ENTRIES {
                inner.kept.shift_remove_index(0); // the oldest goes
            }
        }
    }

    fn none(reason: &str) -> Dict {
        table(vec![("empty", Value::Bool(true)), ("reason", s(t(reason)))])
    }

    /// recommendations: what to change, from the data since FanControl was last changed (see
    /// `advise`).
    pub fn recommendations(&self) -> Dict {
        if !self.database.exists() {
            return Self::none("no database");
        }
        let key = self.key("recommendations".into());
        if let Some(Kept::Table(found)) = self.recall(&key) {
            return found;
        }
        let now = wall();
        let Ok(rows) = data::load_rows(&self.database, Some(advice::LOOKBACK_DAYS), Mode::All, now)
        else {
            return Self::none("no cooling data yet");
        };
        let setup = self.setup();
        let states = data::fancontrol_states(&self.database);
        let names = self.names();
        let value = advice::advise(
            &rows,
            &states,
            &setup.0,
            &setup.1,
            &names,
            &self.limits,
            now,
        );
        self.keep(key, Kept::Table(value.clone()));
        value
    }

    /// analysis: everything a page draws for the last `days` days (None: all) of a mode, and
    /// `text`, the plain-text report.
    pub fn analysis(&self, days: Option<f64>, mode: Mode, points: i64) -> Dict {
        if !self.database.exists() {
            return Self::none("no database");
        }
        let key = self.key(format!("analysis {days:?} {} {points}", mode_name(mode)));
        if let Some(Kept::Table(found)) = self.recall(&key) {
            return found;
        }
        let rows = data::load_rows(&self.database, days, mode, wall()).unwrap_or_default();
        if rows.is_empty() {
            return Self::none("no cooling data in the chosen period yet");
        }
        let recommended = self.recommendations();
        let setup = self.setup();
        let names = self.names();
        let mut result = analysis(&rows, &setup.0, &setup.1, &names, &recommended, points);
        let lines = text::report(&rows, &setup.1, &setup.0, &recommended, &names);
        let lines = lines.into_iter().map(Value::Str).collect();
        result.insert("text".into(), Value::List(lines));
        self.keep(key, Kept::Table(result.clone()));
        result
    }

    /// report: the plain-text report: what was recorded, each fan, what is notable, the
    /// trend and the recommendations, as lines in the language of the panel.
    pub fn report(&self, days: Option<f64>, mode: Mode) -> Vec<String> {
        if !self.database.exists() {
            let path = self.database.display().to_string();
            return vec![tf("No database: {path}", &[("path", s(path))])];
        }
        let key = self.key(format!("report {days:?} {}", mode_name(mode)));
        if let Some(Kept::Lines(found)) = self.recall(&key) {
            return found;
        }
        let rows = data::load_rows(&self.database, days, mode, wall()).unwrap_or_default();
        if rows.is_empty() {
            return vec![t(
                "No cooling data yet. The memory service writes it while `cooling` is on.",
            )];
        }
        let setup = self.setup();
        let recommended = self.recommendations();
        let lines = text::report(&rows, &setup.1, &setup.0, &recommended, &self.names());
        self.keep(key, Kept::Lines(lines.clone()));
        lines
    }
}

// -- the service ----------------------------------------------------------------------------------

/// Checks the fan curves against what the PC did, for an editor page or a script.
///
/// `analysis(days, mode, points)` returns everything a page draws as data, and `text`, the
/// plain-text report in the language of the panel; `recommendations()` what to change;
/// `report(days, mode)` only the text. The work is done when asked and kept for 5 minutes,
/// and until FanControl's settings change. The shapes are in docs/PLUGINS.md.
pub struct Cooling {
    host: crate::host::ServiceHost,
    pub analyst: Arc<Analyst>,
}

fn options() -> Schema {
    let text = || Value::Str(String::new());
    vec![
        ("limits", "list", Value::List(Vec::new())),
        ("fancontrol", "string", text()),
        ("database", "string", text()),
    ]
}

/// The cooling analysis as a kind of service.
pub fn part() -> Part {
    Part {
        options,
        make: |host, options| {
            let mut notes = Vec::new();
            let raw = options
                .get("limits")
                .and_then(Value::as_list)
                .unwrap_or(&[]);
            let limits = advice::parse_limits(raw, &mut |note| notes.push(note));
            for note in notes {
                host.note(&note);
            }
            let text = |key: &str| options.get(key).map(Value::str).unwrap_or_default();
            let places = Places::here();
            let given = text("database");
            let database = match given.is_empty() {
                // (where the memory service keeps its file unless told otherwise)
                true => {
                    let own = host
                        .data_dir()
                        .ok_or("cooling: no folder of the services' data")?;
                    let all = own.parent().map(std::path::Path::to_path_buf);
                    all.unwrap_or(own).join("memory").join(DATABASE)
                }
                false => {
                    let homes = paths::Homes {
                        env: &*places.env,
                        user: &paths::user_home,
                    };
                    let path = (Path::new(HERE, &given).expanduser(&homes))
                        .ok_or("RuntimeError: Could not determine home directory.")?;
                    std::path::PathBuf::from(path.text())
                }
            };
            let watch = fancontrol::Watch::new(&text("fancontrol"), places);
            let analyst = Arc::new(Analyst::new(&database, limits, watch));
            Ok(Box::new(Cooling { host, analyst }))
        },
    }
}

impl Service for Cooling {
    fn start(&mut self) -> Result<(), String> {
        let database = self.analyst.database().display();
        self.host
            .note(&format!("cooling analysis: reads {database}"));
        Ok(())
    }

    /// What the service reads, for a console page.
    fn summary(&self) -> Option<Value> {
        let setup = self.analyst.setup();
        for note in self.analyst.notes() {
            self.host.note(&note);
        }
        let database = self.analyst.database();
        let count = |n: usize| Value::int(i64::try_from(n).unwrap_or(0));
        let file = self.analyst.fancontrol();
        Some(Value::Dict(table(vec![
            ("database", s(database.display().to_string())),
            ("database_found", Value::Bool(database.is_file())),
            (
                "fancontrol",
                file.map_or(Value::None, |file| s(file.text())),
            ),
            ("curves", count(setup.0.len())),
            ("fans", count(setup.1.len())),
        ])))
    }
}
