//! What to do about the cooling, from what was measured: the recommendations
//! (services/cooling_advice.py).
//!
//! Each rule looks at the data since the last change in FanControl (what was fixed leaves the
//! list at once) and names the data it rests on. Nothing here changes FanControl: the user
//! decides. A recommendation is a dict: `id` (`source:<curve>`, `mix:<curve>`,
//! `warm:<sensor>`, `oscillation:<curve>`, `quieter:<curve>` or `trend`), `curve`, `proposal`
//! (new curve points, or None), `title`, `why`, `do` (steps), `effect` (lines), `note`,
//! `confidence`, `basis` and its `number`.

use indexmap::IndexMap;
use lp_i18n::t;
use lp_py::{Dict, Value};

use crate::cooling_data::{
    self as data, back_calculated, data_of, device_sensor, total, weighted, weighted_all, Row,
    MIN_SAMPLES,
};
use crate::cooling_text::{
    basis, change_lines, characteristic_of, clock, compare, duration, fan_of, jumps, n0, num,
    percent_rpm, points_text, rpm_at, s, sensor_entries, sensor_name, tf,
};
use crate::fancontrol::{self, Curve, Fan};

/// this much desktop for a statement about the desktop
pub const MIN_DESKTOP_S: f64 = 30.0 * 60.0;
/// ... and about a game
pub const MIN_GAME_S: f64 = 20.0 * 60.0;
/// from here "reliable" instead of "preliminary" ...
const RELIABLE_GAME_S: f64 = 3.0 * 3600.0;
/// ... and from this many rounds
const RELIABLE_ROUNDS: usize = 3;
/// points: from here a step in the duty is heard
pub const JUMP: f64 = 5.0;
/// this many jumps an hour are oscillation
const OSCILLATION_PER_H: f64 = 20.0;
const OSCILLATION_MIN_S: f64 = 6.0 * 3600.0;
/// below that a change of the curve is not worth it
const QUIETER_MIN_RPM: f64 = 60.0;
const TREND_DAYS: i64 = 14;
/// K warmer than at the start, at the same load and outside temperature
const TREND_K: f64 = 3.0;
/// older rows are not read for the recommendations
pub const LOOKBACK_DAYS: f64 = 60.0;
const MIN_MIX_S: f64 = 600.0;
/// K: a curve whose sensor is this far from its device's follows another one
const OFFSET_WRONG: f64 = 8.0;

fn weight(id: &str) -> usize {
    match id.split(':').next().unwrap_or("") {
        "source" => 0,
        "warm" => 1,
        "mix" => 2,
        "oscillation" => 3,
        "quieter" => 4,
        "trend" => 5,
        _ => 9,
    }
}

/// A temperature that is too warm in a game when it stays above `max` for 5 % of the time.
/// `sensor` is a column of the memory (`cpu_temp`, `gpu_temp`, `gpu_hotspot`) or the key of a
/// reading it keeps.
#[derive(Clone, Debug, PartialEq)]
pub struct Limit {
    pub sensor: String,
    pub max: f64,
    pub name: String,
}

/// DEFAULT_LIMITS: only a starting point: what is too warm depends on the hardware, so the
/// options say.
pub fn default_limits() -> Vec<Limit> {
    let limit = |sensor: &str, max: f64| Limit {
        sensor: sensor.into(),
        max,
        name: String::new(),
    };
    vec![
        limit("cpu_temp", 90.0),
        limit("gpu_temp", 85.0),
        limit("gpu_hotspot", 100.0),
    ]
}

/// parse_limits: the `limits` option: `{ key = "cpu.temp", max = 90 }` each (`gpu.hotspot`
/// and the keys of the readings the memory keeps are known too). None given: the defaults.
pub fn parse_limits(raw: &[Value], report: &mut dyn FnMut(String)) -> Vec<Limit> {
    let mut found = Vec::new();
    for (n, entry) in raw.iter().enumerate() {
        let entry = entry.as_dict();
        let key = entry
            .and_then(|entry| entry.get("key"))
            .and_then(Value::as_str);
        let limit = match entry.and_then(|entry| entry.get("max")) {
            Some(limit @ (Value::Int(_) | Value::Float(_))) => limit.to_f64(),
            _ => None,
        };
        let (Some(key), Some(limit)) = (key.filter(|key| !key.is_empty()), limit) else {
            report(format!(
                "limits[{}]: needs a reading and a maximum, e.g. {{ key = 'cpu.temp', max = 90 }}",
                n + 1
            ));
            continue;
        };
        let name = entry
            .and_then(|entry| entry.get("name"))
            .and_then(Value::as_str);
        let sensor = match key {
            "cpu.temp" => "cpu_temp",
            "gpu.temp" => "gpu_temp",
            "gpu.hotspot" => "gpu_hotspot",
            other => other,
        };
        found.push(Limit {
            sensor: sensor.to_string(),
            max: limit,
            name: name.unwrap_or("").to_string(),
        });
    }
    match found.is_empty() {
        true => default_limits(),
        false => found,
    }
}

/// The fans that one curve drives: one line in a chart.
#[derive(Clone, Debug, PartialEq)]
pub struct Series {
    pub curve: String,
    pub fans: Vec<String>,
    pub device: String,
}

impl Series {
    pub fn name(&self) -> String {
        self.fans.join(" + ")
    }
}

/// series_of: the fans the rows know, grouped by the curve that drives them (cpu, gpu, mix,
/// other).
pub fn series_of(
    rows: &[Row],
    fans: &IndexMap<String, Fan>,
    curves: &IndexMap<String, Curve>,
) -> Vec<Series> {
    let mut groups: IndexMap<String, Vec<String>> = IndexMap::new();
    for name in data::fan_names(rows) {
        if let Some(fan) = fan_of(fans, &name) {
            groups.entry(fan.curve.clone()).or_default().push(name);
        }
    }
    let mut found: Vec<Series> = groups
        .into_iter()
        .map(|(curve, fans)| {
            let device = curves
                .get(&curve)
                .map_or("other", Curve::device)
                .to_string();
            Series {
                curve,
                fans,
                device,
            }
        })
        .collect();
    found.sort_by_key(|series| match series.device.as_str() {
        "cpu" => 0,
        "gpu" => 1,
        "mix" => 2,
        _ => 3,
    });
    found
}

/// (value, seconds) of each row.
type Pairs = Vec<(Option<f64>, f64)>;

fn duties(rows: &[Row], fan: &str) -> Pairs {
    rows.iter().map(|r| (r.duty(fan), r.duration)).collect()
}

fn temps(rows: &[Row], sensor: &str) -> Pairs {
    rows.iter().map(|r| (r.temp(sensor), r.duration)).collect()
}

fn median(pairs: &Pairs) -> Option<f64> {
    weighted(pairs, 0.5)
}

/// A whole number of Python's.
fn whole(x: f64) -> Value {
    Value::Int(format!("{:.0}", x.trunc()).parse().unwrap_or_default())
}

fn float(value: Option<f64>) -> Value {
    value.map_or(Value::None, Value::Float)
}

fn texts(lines: Vec<String>) -> Value {
    Value::List(lines.into_iter().map(Value::Str).collect())
}

fn table(pairs: Vec<(&str, Value)>) -> Dict {
    pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect()
}

/// Curve points as they were proposed: whole numbers where Python has them.
pub type Points = Vec<(Value, Value)>;

fn pairs_of(points: &Points) -> Vec<(f64, f64)> {
    let number = |value: &Value| value.to_f64().unwrap_or(0.0);
    points.iter().map(|(t, d)| (number(t), number(d))).collect()
}

fn listed(points: &Points) -> Value {
    let pair = |(t, d): &(Value, Value)| Value::List(vec![t.clone(), d.clone()]);
    Value::List(points.iter().map(pair).collect())
}

fn curve_of(name: &str, points: &Points) -> Curve {
    Curve {
        name: name.to_string(),
        points: pairs_of(points),
        source: None,
        mix: Vec::new(),
        hysteresis: Dict::new(),
    }
}

/// Everything the rules need, worked out once.
pub struct Situation<'a> {
    rows: &'a [Row],
    curves: &'a IndexMap<String, Curve>,
    names: &'a Dict,
    limits: &'a [Limit],
    pub desk: Vec<Row>,
    pub game: Vec<Row>,
    pub series: Vec<Series>,
    found: Vec<Dict>,
    checked: Vec<String>,
    /// curve -> its new points and the sensor they were proposed for
    proposals: IndexMap<String, (Points, String)>,
}

impl<'a> Situation<'a> {
    pub fn new(
        rows: &'a [Row],
        curves: &'a IndexMap<String, Curve>,
        fans: &'a IndexMap<String, Fan>,
        names: &'a Dict,
        limits: &'a [Limit],
    ) -> Situation<'a> {
        Situation {
            rows,
            curves,
            names,
            limits,
            desk: rows.iter().filter(|r| !r.in_game()).cloned().collect(),
            game: rows.iter().filter(|r| r.in_game()).cloned().collect(),
            series: series_of(rows, fans, curves),
            found: Vec::new(),
            checked: Vec::new(),
            proposals: IndexMap::new(),
        }
    }

    fn confidence(&self) -> String {
        let reliable =
            total(&self.game) >= RELIABLE_GAME_S && data::rounds(&self.game) >= RELIABLE_ROUNDS;
        t(if reliable { "reliable" } else { "preliminary" })
    }

    /// output: what the curve `name` commands in this row (percent), as far as that is
    /// known: a mix by its parts' highest, a proposed curve by the sensor it was proposed
    /// for, a measured input by the curve, else the duty of a fan on it, else the device's
    /// sensor.
    fn output(&self, row: &Row, name: &str, with_proposals: bool) -> Option<f64> {
        let curve = self.curves.get(name);
        if let Some(curve) = curve.filter(|curve| !curve.mix.is_empty()) {
            let mut highest: Option<f64> = None; // as a mix is meant to work
            for part in &curve.mix {
                if let Some(found) = self.output(row, part, with_proposals) {
                    if highest.is_none_or(|highest| found > highest) {
                        highest = Some(found);
                    }
                }
            }
            return highest;
        }
        if let Some((points, sensor)) = self.proposals.get(name).filter(|_| with_proposals) {
            return Some(curve_of(name, points).duty_at(row.temp(sensor)?));
        }
        if let (Some(curve), Some(seen)) = (curve, row.sources.get(name)) {
            return Some(curve.duty_at(*seen));
        }
        if let Some(direct) = self.series.iter().find(|series| series.curve == name) {
            return row.duty(&direct.fans[0]);
        }
        let curve = curve?;
        Some(curve.duty_at(row.temp(device_sensor(curve.device())?)?))
    }

    /// panel_sensor: the catalogue entry of the sensor the panel shows for the CPU or the
    /// GPU.
    fn panel_sensor(&self, kind: &str) -> Option<&'a Dict> {
        let wanted = device_sensor(kind).map_or(Value::None, |sensor| Value::Str(sensor.into()));
        let entries = sensor_entries(self.names).into_iter();
        let mut shown = entries.filter(|e| e.get("panel").unwrap_or(&Value::None) == &wanted);
        shown.next()
    }
}

/// propose_curve: curve points for a new input, from what was measured. Careful: on the
/// desktop the fan stays at the base value (the first point lies 3 K above what the input
/// reached 95 % of the time there); in a game it gets the cooling it had (the second point at
/// the input's 90 % value of the game with the duty it ran at); the upper points of the old
/// curve stay.
fn propose_curve(curve: &Curve, desk: &Pairs, game: &Pairs, game_duty: &Pairs) -> Option<Points> {
    let old = &curve.points;
    let base = old.first().map_or(20.0, |first| first.1);
    let quiet = weighted(desk, 0.95)?;
    let mut new = vec![((quiet + 3.0).ceil(), base)];
    let (during, duty) = (weighted(game, 0.9), median(game_duty));
    let played = lp_py::sum_floats(game.iter().map(|(_, d)| *d));
    if let (true, Some(during), Some(duty)) = (played >= MIN_GAME_S, during, duty) {
        let (temp, level) = (lp_py::round(during), lp_py::round(duty));
        if temp > new[0].0 + 3.0 && level > base + 2.0 {
            new.push((temp, level));
        }
    }
    let last = new[new.len() - 1];
    let above = |(temp, level): &&(f64, f64)| *temp > last.0 + 2.0 && *level > last.1;
    new.extend(old.iter().filter(above));
    let points = new
        .iter()
        .map(|(temp, level)| (whole(*temp), whole(lp_py::round(*level))));
    Some(points.collect())
}

fn target_name(curves: &IndexMap<String, Curve>, part: &str) -> String {
    match curves.get(part).map_or("other", Curve::device) {
        "gpu" => t("the graphics card"),
        "cpu" => t("the CPU"),
        _ => part.to_string(),
    }
}

// -- the rules ----------------------------------------------------------------------------------

/// rule_source: does a curve read another sensor than the device it cools? What it reads is
/// known where the sensor can be read (it was kept), else it is worked out from the duty the
/// fan had.
fn rule_source(st: &mut Situation) {
    for series in st.series.clone() {
        let (Some(sensor), Some(curve)) =
            (device_sensor(&series.device), st.curves.get(&series.curve))
        else {
            continue;
        };
        if curve.points.is_empty() || total(&st.desk) < MIN_DESKTOP_S {
            continue;
        }
        let pairs = data_of(&st.desk, &series.fans[0]);
        let mut seen_pairs: Vec<(f64, f64, f64)> = pairs
            .iter()
            .filter_map(|(r, _)| Some((*r.sources.get(&curve.name)?, r.temp(sensor)?, r.duration)))
            .collect();
        if seen_pairs.len() < MIN_SAMPLES {
            seen_pairs = back_calculated(&pairs, curve)
                .into_iter()
                .filter_map(|(r, calc)| Some((calc, r.temp(sensor)?, r.duration)))
                .collect();
        }
        if seen_pairs.len() < MIN_SAMPLES {
            continue;
        }
        let of = |pick: &dyn Fn(&(f64, f64, f64)) -> f64| {
            let picked: Vec<(f64, f64)> = seen_pairs.iter().map(|p| (pick(p), p.2)).collect();
            weighted_all(&picked, 0.5).unwrap_or(0.0)
        };
        let (offset, seen, real) = (of(&|p| p.0 - p.1), of(&|p| p.0), of(&|p| p.1));
        if offset.abs() <= OFFSET_WRONG {
            let said = tf(
                "{curve} follows the right device ({offset} K).",
                &[
                    ("curve", s(&curve.name)),
                    ("offset", s(num(offset, 0, true))),
                ],
            );
            st.checked.push(said);
            continue;
        }
        add_source(st, &series, curve, sensor, seen, real);
    }
}

fn add_source(
    st: &mut Situation,
    series: &Series,
    curve: &Curve,
    sensor: &str,
    seen: f64,
    real: f64,
) {
    let fan = &series.fans[0];
    let target = st.panel_sensor(&series.device);
    let characteristic = characteristic_of(st.rows, fan);
    let (desk_x, game_x) = (temps(&st.desk, sensor), temps(&st.game, sensor));
    let game_duty: Pairs = duties(&st.game, fan)
        .into_iter()
        .filter(|pair| pair.0.is_some())
        .collect();
    let points = propose_curve(curve, &desk_x, &game_x, &game_duty).filter(|p| !p.is_empty());
    if let Some(points) = &points {
        (st.proposals).insert(curve.name.clone(), (points.clone(), sensor.to_string()));
    }
    let now = median(&duties(&st.desk, fan));
    let proposed = curve_of(&curve.name, points.as_ref().unwrap_or(&Vec::new()));
    let commanded = |pairs: &Pairs| -> Option<f64> {
        let commanded = pairs
            .iter()
            .filter_map(|(x, d)| Some((Some(proposed.duty_at((*x)?)), *d)));
        median(&commanded.collect())
    };
    let new = points.as_ref().and_then(|_| commanded(&desk_x));
    let mut effect = Vec::new();
    if let (Some(new), Some(now)) = (new, now) {
        effect.push(compare(&t("Desktop"), new, now, &characteristic));
    }
    if total(&st.game) >= MIN_GAME_S && points.is_some() {
        let (before, after) = (median(&duties(&st.game, fan)), commanded(&game_x));
        if let (Some(before), Some(after)) = (before, after) {
            effect.push(format!(
                "{} — {}",
                compare(&t("Game"), after, before, &characteristic),
                t("the same cooling as today, but steered by the right device")
            ));
        }
    }
    let device = if series.device == "gpu" { "gpu" } else { "cpu" };
    let place = t(match device {
        "gpu" => "the graphics card",
        _ => "the CPU",
    });
    let part = |key: &str| target.and_then(|target| target.get(key).filter(|v| v.truthy()));
    let where_ = match part("label") {
        Some(label) => {
            let named = st.names.get(device).filter(|name| name.truthy());
            let device = named.cloned().unwrap_or_else(|| s(&place));
            tf(
                "“{sensor}” of “{device}”",
                &[("sensor", label.clone()), ("device", device)],
            )
        }
        None => t("the sensor of the device"),
    };
    let id = part("id").map_or(String::new(), |id| format!(" ({})", id.str()));
    let mut steps = vec![
        tf(
            "Open the curve “{curve}” in FanControl.",
            &[("curve", s(&curve.name))],
        ),
        tf(
            "Choose {where} as the temperature source: the sensor the panel shows as \
             {device}{id}.",
            &[
                ("where", s(where_)),
                ("device", s(lp_py::upper(device))),
                ("id", s(id)),
            ],
        ),
    ];
    if let Some(points) = &points {
        let said = points_text(&pairs_of(points));
        steps.push(tf(
            "Set the curve points: {points}.",
            &[("points", s(said))],
        ));
    }
    let source = curve.source.clone().filter(|source| !source.is_empty());
    let common = [
        ("curve", s(&curve.name)),
        ("fans", s(series.name())),
        ("device", s(&place)),
        ("seen", n0(seen)),
        ("real", n0(real)),
        ("duty", n0(now.unwrap_or(0.0))),
        ("source", s(source.unwrap_or_else(|| "?".into()))),
    ];
    let why = match seen > real {
        true => tf(
            "The curve {curve} reads {source}. On the desktop it sees {seen} °C, {device} is at \
             {real} °C: it follows another device. {fans} therefore runs at {duty} % although \
             {device} is cool.",
            &common,
        ),
        false => tf(
            "The curve {curve} reads {source}. On the desktop it sees {seen} °C, {device} is at \
             {real} °C: it follows another device. {fans} therefore stays at {duty} % however \
             warm {device} gets.",
            &common,
        ),
    };
    let note = match points.is_some() {
        true => s(t(
            "The points leave the fan at its base value on the desktop and give it, in a \
             game, the cooling it had. The upper points stay as a reserve.",
        )),
        false => Value::None,
    };
    let confidence = tf(
        "certain (source) · {confidence} (points)",
        &[("confidence", s(st.confidence()))],
    );
    let title = tf(
        "Tie {fans} to {device}",
        &[("fans", s(series.name())), ("device", s(&place))],
    );
    st.found.push(table(vec![
        ("id", s(format!("source:{}", curve.name))),
        ("curve", s(&curve.name)),
        ("proposal", points.as_ref().map_or(Value::None, listed)),
        ("title", s(title)),
        ("why", s(why)),
        ("do", texts(steps)),
        ("effect", texts(effect)),
        ("note", note),
        ("confidence", s(confidence)),
        ("basis", s(basis(&st.desk, &st.game))),
    ]));
}

/// rule_mix: does a mix curve take the highest of its parts?
fn rule_mix(st: &mut Situation) {
    for series in st.series.clone() {
        let Some(curve) = st
            .curves
            .get(&series.curve)
            .filter(|_| series.device == "mix")
        else {
            continue;
        };
        let fan = &series.fans[0];
        for part in &curve.mix {
            let Some(direct) = st.series.iter().find(|x| x.curve == *part).cloned() else {
                continue;
            };
            let gaps: Vec<(f64, f64)> = (st.rows.iter())
                .filter_map(|r| Some((r.duty(&direct.fans[0])? - r.duty(fan)?, r.duration)))
                .collect();
            let seconds = lp_py::sum_floats(gaps.iter().map(|(_, d)| *d));
            if seconds < MIN_MIX_S {
                continue;
            }
            let above = gaps.iter().filter(|(gap, _)| *gap > JUMP).map(|(_, d)| *d);
            let share = lp_py::sum_floats(above) / seconds;
            if share < 0.5 {
                st.checked.push(tf(
                    "{curve} takes the higher value of {part}.",
                    &[("curve", s(&series.curve)), ("part", s(part))],
                ));
                continue;
            }
            add_mix(st, &series, curve, &direct, part, share);
        }
    }
}

fn add_mix(
    st: &mut Situation,
    series: &Series,
    curve: &Curve,
    direct: &Series,
    part: &str,
    share: f64,
) {
    let fan = &series.fans[0];
    let characteristic = characteristic_of(st.rows, fan);
    let (mut effect, mut depends) = (Vec::new(), None);
    let states = [
        (t("Game"), &st.game, MIN_GAME_S, false),
        (t("Desktop"), &st.desk, MIN_DESKTOP_S, true),
    ];
    for (title, rows, needed, desktop) in states {
        if total(rows) < needed {
            continue;
        }
        let commanded = |with_proposals: bool| -> Option<f64> {
            let outputs = rows
                .iter()
                .map(|r| (st.output(r, &series.curve, with_proposals), r.duration));
            median(&outputs.collect())
        };
        let (Some(now), Some(new)) = (median(&duties(rows, fan)), commanded(true)) else {
            continue;
        };
        effect.push(compare(&title, new, now, &characteristic));
        // would the change alone make the desktop louder?
        if desktop && st.proposals.contains_key(part) {
            if let Some(alone) = commanded(false).filter(|alone| *alone > new + 3.0) {
                depends = Some((alone, new));
            }
        }
    }
    let target = target_name(st.curves, part);
    let now_game = median(&duties(&st.game, &direct.fans[0])).unwrap_or(0.0);
    let mixed_game = median(&duties(&st.game, fan)).unwrap_or(0.0);
    let title = tf(
        "Let {fans} react to {target} too",
        &[("fans", s(series.name())), ("target", s(&target))],
    );
    let why = tf(
        "{part} was above the mix by more than {jump} points {share} % of the time: in a \
         game {a} % against {b} %. So {fans} does not react to {target}.",
        &[
            ("part", s(part)),
            ("jump", n0(JUMP)),
            ("share", n0(100.0 * share)),
            ("a", n0(now_game)),
            ("b", n0(mixed_game)),
            ("fans", s(series.name())),
            ("target", s(&target)),
        ],
    );
    let steps = vec![
        tf(
            "Open the curve “{curve}” in FanControl.",
            &[("curve", s(&series.curve))],
        ),
        tf(
            "Choose “Max” as the mix function: the higher value of {parts} counts.",
            &[("parts", s(curve.mix.join(&t(" and "))))],
        ),
    ];
    let mut found = table(vec![
        ("id", s(format!("mix:{}", series.curve))),
        ("curve", s(&series.curve)),
        ("proposal", Value::None),
        ("title", s(title)),
        ("why", s(why)),
        ("do", texts(steps)),
        ("effect", texts(effect)),
        ("note", Value::None),
        ("confidence", s(st.confidence())),
        ("basis", s(basis(&st.desk, &st.game))),
    ]);
    if let Some((alone, new)) = depends {
        found.insert("after".into(), s(format!("source:{part}")));
        let note = tf(
            "Change the source of {part} first: alone, {fans} would run at {alone} % on the \
             desktop instead of {new} %, because {part} sees too much there today.",
            &[
                ("part", s(part)),
                ("fans", s(series.name())),
                ("alone", n0(alone)),
                ("new", n0(new)),
            ],
        );
        found.insert("note".into(), s(note));
    }
    st.found.push(found);
}

fn raise_step(st: &Situation, sensor: &str, name: &str, high: f64) -> String {
    let device = match sensor {
        "cpu_temp" => Some("cpu"),
        "gpu_temp" | "gpu_hotspot" => Some("gpu"),
        _ => None,
    };
    let curves: Vec<&str> = (st.series.iter())
        .filter(|c| Some(c.device.as_str()) == device)
        .map(|c| c.curve.as_str())
        .collect();
    if !curves.is_empty() {
        return tf(
            "Raise the curve {curves} around {temp} °C.",
            &[("curves", s(curves.join(", "))), ("temp", n0(high))],
        );
    }
    match device {
        Some("gpu") => t(
            "Raise the fan curve of the graphics card, in its driver software or in FanControl.",
        ),
        Some(_) => tf(
            "Let the fans cool the CPU more in a game: raise its fan curve around {temp} °C.",
            &[("temp", n0(high))],
        ),
        None => tf(
            "Let the fans cool {name} more in a game.",
            &[("name", s(name))],
        ),
    }
}

/// rule_warm: is anything too warm in a game (over its limit for 5 % of the time)?
fn rule_warm(st: &mut Situation) {
    if total(&st.game) < MIN_GAME_S {
        st.checked.push(tf(
            "Temperatures in a game: judged from {time} of game time.",
            &[("time", s(duration(MIN_GAME_S)))],
        ));
        return;
    }
    let mut fine = Vec::new();
    for limit in st.limits {
        let Some(high) = weighted(&temps(&st.game, &limit.sensor), 0.95) else {
            continue;
        };
        let name = match limit.name.is_empty() {
            true => sensor_name(&limit.sensor, st.names),
            false => limit.name.clone(),
        };
        if high <= limit.max {
            fine.push(format!("{name} {} °C", num(high, 0, false)));
            continue;
        }
        let title = tf("{name} gets too warm in a game", &[("name", s(&name))]);
        let why = tf(
            "In 5 % of the game time {name} was above {temp} °C (limit {limit} °C).",
            &[
                ("name", s(&name)),
                ("temp", n0(high)),
                ("limit", n0(limit.max)),
            ],
        );
        let step = raise_step(st, &limit.sensor, &name, high);
        st.found.push(table(vec![
            ("id", s(format!("warm:{}", limit.sensor))),
            ("curve", Value::None),
            ("proposal", Value::None),
            ("title", s(title)),
            ("why", s(why)),
            ("do", texts(vec![step])),
            ("effect", texts(Vec::new())),
            ("note", Value::None),
            ("confidence", s(st.confidence())),
            ("basis", s(basis(&[], &st.game))),
        ]));
    }
    if !fine.is_empty() {
        st.checked.push(tf(
            "Temperatures in a game are fine (95 % of the time below the limit): {list}.",
            &[("list", s(fine.join(", ")))],
        ));
    }
}

/// `value + more`, of what a table of FanControl holds.
fn plus(value: &Value, more: i64) -> Value {
    match value {
        Value::Int(n) => Value::Int(n + more),
        Value::Bool(on) => Value::int(i64::from(*on) + more),
        Value::Float(x) => Value::Float(x + more as f64),
        _ => Value::None,
    }
}

/// rule_oscillation: does a duty keep jumping, which one hears as up and down?
fn rule_oscillation(st: &mut Situation) {
    let recorded = total(st.rows);
    if recorded < OSCILLATION_MIN_S {
        st.checked.push(tf(
            "Oscillation: judged from {time} of recording (so far {have}).",
            &[
                ("time", s(duration(OSCILLATION_MIN_S))),
                ("have", s(duration(recorded))),
            ],
        ));
        return;
    }
    let mut calm = Vec::new();
    for series in st.series.clone() {
        let per_hour = jumps(st.rows, &series.fans[0], JUMP) as f64 / (recorded / 3600.0);
        if per_hour < OSCILLATION_PER_H {
            calm.push(format!("{} {}/h", series.name(), num(per_hour, 0, false)));
            continue;
        }
        let none = Dict::new();
        let h = st
            .curves
            .get(&series.curve)
            .map_or(&none, |c| &c.hysteresis);
        let of = |key: &str| h.get(key).cloned().unwrap_or(Value::int(0));
        let (up, down, delay) = (
            of("HysteresisValueUp"),
            of("HysteresisValueDown"),
            of("ResponseTimeDown"),
        );
        let title = tf("{fans} oscillates", &[("fans", s(series.name()))]);
        let why = tf(
            "The duty jumps {n} times an hour by more than {jump} points: you hear that \
             as up and down.",
            &[("n", n0(per_hour)), ("jump", n0(JUMP))],
        );
        let steps = vec![
            tf(
                "Open the curve “{curve}” in FanControl.",
                &[("curve", s(&series.curve))],
            ),
            tf(
                "Raise the hysteresis from {up}/{down} K to {new_up}/{new_down} K and \
                 the response time downwards from {delay} s to {new_delay} s.",
                &[
                    ("new_up", plus(&up, 2)),
                    ("new_down", plus(&down, 2)),
                    ("new_delay", plus(&delay, 8)),
                    ("up", up),
                    ("down", down),
                    ("delay", delay),
                ],
            ),
        ];
        let effect = t("fewer audible changes; the cooling reacts a little slower");
        st.found.push(table(vec![
            ("id", s(format!("oscillation:{}", series.curve))),
            ("curve", s(&series.curve)),
            ("proposal", Value::None),
            ("title", s(title)),
            ("why", s(why)),
            ("do", texts(steps)),
            ("effect", texts(vec![effect])),
            ("note", Value::None),
            ("confidence", s(t("reliable"))),
            ("basis", s(duration(recorded))),
        ]));
    }
    if !calm.is_empty() {
        st.checked.push(tf(
            "No oscillation (jumps over {jump} points): {list}.",
            &[("jump", n0(JUMP)), ("list", s(calm.join(", ")))],
        ));
    }
}

/// rule_quieter: does a fan run higher on the desktop than it needs to?
fn rule_quieter(st: &mut Situation) {
    if total(&st.desk) < MIN_DESKTOP_S {
        return;
    }
    for series in st.series.clone() {
        let Some(curve) = st.curves.get(&series.curve) else {
            continue;
        };
        let proposed = st.proposals.contains_key(&series.curve);
        if series.device == "mix" || proposed || curve.points.is_empty() {
            continue;
        }
        let fan = &series.fans[0];
        let base = curve.points[0].1;
        let now = median(&duties(&st.desk, fan));
        let characteristic = characteristic_of(st.rows, fan);
        let Some(now) = now.filter(|now| *now >= base + 4.0) else {
            let said = match now.filter(|now| *now != 0.0) {
                Some(now) => percent_rpm(now, &characteristic),
                None => "?".into(),
            };
            st.checked.push(tf(
                "{fans} runs at its base value on the desktop: {now}.",
                &[("fans", s(series.name())), ("now", s(said))],
            ));
            continue;
        };
        let sensor = device_sensor(&series.device);
        let mut reads: Pairs = (st.desk.iter())
            .filter_map(|r| Some((Some(*r.sources.get(&curve.name)?), r.duration)))
            .collect();
        let read = lp_py::sum_floats(reads.iter().map(|(_, d)| *d));
        if let Some(sensor) = sensor.filter(|_| read < MIN_DESKTOP_S) {
            reads = (st.desk.iter())
                .filter_map(|r| Some((Some(r.temp(sensor)?), r.duration)))
                .collect();
        }
        let Some(quiet) = weighted(&reads, 0.9) else {
            continue;
        };
        let limit = (quiet + 2.0).ceil();
        let mut new: Points = vec![(whole(limit), Value::Float(base))];
        let above = curve.points.iter().filter(|(temp, _)| *temp > limit + 2.0);
        new.extend(above.map(|(temp, level)| (Value::Float(*temp), Value::Float(*level))));
        let speed = |duty: f64| rpm_at(&characteristic, Some(duty)).unwrap_or(0.0);
        let saved = speed(now) - speed(base);
        if saved < QUIETER_MIN_RPM {
            st.checked.push(tf(
                "{fans}: on the desktop {now}: quieter would save only {saved} RPM, which is \
                 not worth it.",
                &[
                    ("fans", s(series.name())),
                    ("now", s(percent_rpm(now, &characteristic))),
                    ("saved", n0(if saved > 0.0 { saved } else { 0.0 })),
                ],
            ));
            continue;
        }
        let kept = (new.clone(), sensor.unwrap_or("").to_string());
        st.proposals.insert(curve.name.clone(), kept);
        let reliable = total(&st.desk) >= 6.0 * 3600.0;
        let title = tf(
            "{fans} quieter on the desktop",
            &[("fans", s(series.name()))],
        );
        let why = tf(
            "On the desktop {fans} runs at {now}; the curve starts to rise at {start} \
             °C, but its input is below {limit} °C for 90 % of the desktop time.",
            &[
                ("fans", s(series.name())),
                ("now", s(percent_rpm(now, &characteristic))),
                ("start", whole(curve.points[0].0)),
                ("limit", whole(limit - 2.0)),
            ],
        );
        let steps = vec![
            tf(
                "Open the curve “{curve}” in FanControl.",
                &[("curve", s(&curve.name))],
            ),
            tf(
                "Set the curve points: {points}.",
                &[("points", s(points_text(&pairs_of(&new))))],
            ),
        ];
        let effect = compare(&t("Desktop"), base, now, &characteristic);
        st.found.push(table(vec![
            ("id", s(format!("quieter:{}", curve.name))),
            ("curve", s(&curve.name)),
            ("proposal", listed(&new)),
            ("title", s(title)),
            ("why", s(why)),
            ("do", texts(steps)),
            ("effect", texts(vec![effect])),
            (
                "note",
                s(t("Nothing changes in a game: the points above stay.")),
            ),
            (
                "confidence",
                s(t(if reliable { "reliable" } else { "preliminary" })),
            ),
            ("basis", s(basis(&st.desk, &[]))),
        ]));
    }
}

/// rule_trend: does the PC cool worse than two weeks ago, at the same load and outside
/// temperature?
fn rule_trend(st: &mut Situation) {
    let span = match (st.rows.first(), st.rows.last()) {
        (Some(first), Some(last)) => last.ts - first.ts,
        _ => -1.0,
    };
    if span < (TREND_DAYS * 86400) as f64 {
        st.checked.push(tf(
            "Cooling over time: compared from {n} days of recording.",
            &[("n", Value::int(TREND_DAYS))],
        ));
        return;
    }
    let band: Vec<(&Row, f64)> = (st.desk.iter())
        .filter(|r| r.cpu_load.is_some_and(|load| (5.0..=25.0).contains(&load)))
        .filter_map(|r| Some((r, r.cpu_temp? - r.outside?)))
        .collect();
    let (Some(first), Some(last)) = (band.first(), band.last()) else {
        return;
    };
    let (first, last) = (first.0.ts + 7.0 * 86400.0, last.0.ts - 7.0 * 86400.0);
    let gaps = |keep: &dyn Fn(&Row) -> bool| -> Option<f64> {
        let kept = band.iter().filter(|(r, _)| keep(r));
        median(&kept.map(|(r, gap)| (Some(*gap), r.duration)).collect())
    };
    let (Some(early), Some(late)) = (gaps(&|r| r.ts < first), gaps(&|r| r.ts > last)) else {
        return;
    };
    if late - early < TREND_K {
        st.checked.push(tf(
            "Cooling is stable: same load, same outside air, {k} K against the first seven \
             days.",
            &[("k", s(num(late - early, 1, true)))],
        ));
        return;
    }
    let held = lp_py::sum_floats(band.iter().map(|(r, _)| r.duration));
    let why = tf(
        "With the same load and the same outside temperature the CPU is {k} K warmer \
         than in the first seven days.",
        &[("k", s(num(late - early, 1, false)))],
    );
    let rests = tf(
        "{time} in the comparison band",
        &[("time", s(duration(held)))],
    );
    st.found.push(table(vec![
        ("id", s("trend")),
        ("curve", Value::None),
        ("proposal", Value::None),
        ("title", s(t("The cooling is getting worse"))),
        ("why", s(why)),
        (
            "do",
            texts(vec![t("Clean the dust filters and the cooler's fins.")]),
        ),
        ("effect", texts(Vec::new())),
        ("note", Value::None),
        ("confidence", s(t("reliable"))),
        ("basis", s(rests)),
    ]));
}

// -- before and after ---------------------------------------------------------------------------

fn tenth(value: Option<f64>) -> Value {
    float(value.map(|value| lp_py::round_digits(value, 1)))
}

/// before_after: the cooling before and after the last change, per state (game, desktop):
/// the duty of each curve's fans and the temperatures (medians, by time), where both sides
/// have enough data.
fn before_after(before: &[Row], after: &[Row], series: &[Series], names: &Dict) -> Vec<Value> {
    let mut found = Vec::new();
    let both: Vec<Row> = before.iter().chain(after).cloned().collect();
    for (mode, enough) in [("game", MIN_GAME_S / 2.0), ("desktop", MIN_DESKTOP_S / 2.0)] {
        let of = |rows: &[Row]| -> Vec<Row> {
            let kept = rows.iter().filter(|r| r.in_game() == (mode == "game"));
            kept.cloned().collect()
        };
        let (a, b) = (of(before), of(after));
        if total(&a) < enough || total(&b) < enough {
            continue;
        }
        let mut rows = Vec::new();
        let mut add = |quantity: String, unit: &str, before: Value, after: Value| {
            if before != Value::None && after != Value::None {
                rows.push(Value::Dict(table(vec![
                    ("quantity", s(quantity)),
                    ("unit", s(unit)),
                    ("before", before),
                    ("after", after),
                ])));
            }
        };
        for series in series {
            let duty = |rows: &[Row]| tenth(median(&duties(rows, &series.fans[0])));
            add(series.name(), "%", duty(&a), duty(&b));
        }
        for sensor in data::candidates(&both) {
            let temp = |rows: &[Row]| tenth(median(&temps(rows, &sensor)));
            add(sensor_name(&sensor, names), "°C", temp(&a), temp(&b));
        }
        found.push(Value::Dict(table(vec![
            ("mode", s(mode)),
            ("before_s", whole(lp_py::round(total(&a)))),
            ("after_s", whole(lp_py::round(total(&b)))),
            ("rows", Value::List(rows)),
        ])));
    }
    found
}

/// advise: all recommendations for the current FanControl setting, from the data since it
/// was last changed (the first stored state is no change: it was noted when recording
/// began). `rows` are the cooling rows (the last `LOOKBACK_DAYS` days), `states` those of
/// FanControl, the oldest first.
pub fn advise(
    rows: &[Row],
    states: &[Dict],
    curves: &IndexMap<String, Curve>,
    fans: &IndexMap<String, Fan>,
    names: &Dict,
    limits: &[Limit],
    now: f64,
) -> Dict {
    let stamp = |state: &Dict| state.get("ts").cloned().unwrap_or(Value::None);
    let at = |state: &Dict| stamp(state).to_f64().unwrap_or(0.0);
    let (mut since, mut before, mut change) = (None, Vec::new(), Value::None);
    if let [.., earlier, last] = states {
        let (from, to) = (at(earlier), at(last));
        since = Some(to);
        let only = states.len() == 2;
        let kept = |r: &&Row| (from <= r.ts && r.ts < to) || (only && r.ts < to);
        before = rows.iter().filter(kept).cloned().collect();
        let empty = Dict::new();
        let settled = |state: &Dict| match state.get("state") {
            Some(Value::Dict(state)) => state.clone(),
            _ => empty.clone(),
        };
        let what = change_lines(&fancontrol::changes(&settled(earlier), &settled(last)));
        change = Value::Dict(table(vec![("ts", stamp(last)), ("what", texts(what))]));
    }
    let current: Vec<Row> = (rows.iter())
        .filter(|r| since.is_none_or(|since| r.ts >= since))
        .cloned()
        .collect();
    let mut situation = Situation::new(&current, curves, fans, names, limits);
    rule_source(&mut situation);
    rule_mix(&mut situation);
    rule_warm(&mut situation);
    rule_oscillation(&mut situation);
    rule_quieter(&mut situation);
    rule_trend(&mut situation);
    let id = |e: &Dict| e.get("id").map(Value::str).unwrap_or_default();
    let mut ranked = std::mem::take(&mut situation.found);
    ranked.sort_by_key(|e| weight(&id(e)));
    let mut number: IndexMap<String, i64> = IndexMap::new();
    for (i, e) in ranked.iter().enumerate() {
        number.insert(id(e), i64::try_from(i).unwrap_or(0) + 1);
    }
    for e in &mut ranked {
        e.insert("number".into(), Value::int(number[&id(e)]));
        let after = e.get("after").and_then(Value::as_str);
        if let Some(after) = after.and_then(|after| number.get(after)) {
            e.insert("after_number".into(), Value::int(*after));
        }
    }
    let changed = since.is_some();
    let began = match states.last().map(stamp).filter(|_| changed) {
        Some(since) if since.truthy() => since,
        _ => current.first().map_or(Value::None, |first| whole(first.ts)),
    };
    let proposals: Dict = (situation.proposals.iter())
        .map(|(curve, (points, _))| (curve.clone(), listed(points)))
        .collect();
    let effect = match since.filter(|since| *since != 0.0) {
        Some(_) => before_after(&before, &current, &situation.series, names),
        None => Vec::new(),
    };
    let later = states.iter().skip(1).map(stamp);
    let mut result = table(vec![
        ("empty", Value::Bool(false)),
        ("since", began),
        ("changed", Value::Bool(changed)),
        ("basis", s(basis(&situation.desk, &situation.game))),
        (
            "list",
            Value::List(ranked.into_iter().map(Value::Dict).collect()),
        ),
        ("checked", texts(std::mem::take(&mut situation.checked))),
        ("proposals", Value::Dict(proposals)),
        ("change", change),
        ("effect", Value::List(effect)),
        ("computed", whole(now)),
        ("changes", Value::List(later.collect())),
    ]);
    let little = total(&situation.desk) < MIN_DESKTOP_S && total(&situation.game) < MIN_GAME_S;
    if let Some(since) = since.filter(|_| little) {
        let waiting = tf(
            "Since the change at {time} only {have} of data: the recommendations are worked \
             out again as soon as {desktop} of desktop or {game} of game time are there.",
            &[
                ("time", s(clock(since))),
                ("have", s(duration(total(&current)))),
                ("desktop", s(duration(MIN_DESKTOP_S))),
                ("game", s(duration(MIN_GAME_S))),
            ],
        );
        result.insert("waiting".into(), s(waiting));
    }
    result
}
