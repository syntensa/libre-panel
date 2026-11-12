//! The cooling analysis in words: the plain-text report and the sentences the
//! recommendations and findings share (services/cooling_text.py). Everything a person reads
//! goes through `t()`, so it comes in the language of the panel.

use indexmap::IndexMap;
use lp_i18n::t;
use lp_py::{Dict, Value};

use crate::cooling_data::{self as data, weighted, weighted_all, Row};
use crate::fancontrol::{Curve, Fan};

/// recorded so long, the analysis draws conclusions
pub const ENOUGH_S: f64 = 6.0 * 3600.0;
/// K a back-calculated course must have moved before a sensor is judged (cooling_data's)
const SPAN_NEEDED: f64 = 5.0;

/// A text with its values put in (`t(text, name=value, ...)`).
pub fn tf(text: &str, values: &[(&str, Value)]) -> String {
    lp_i18n::t_format(text, values).unwrap_or_else(|_| t(text))
}

/// A text as a value of a sentence.
pub fn s(text: impl Into<String>) -> Value {
    Value::Str(text.into())
}

/// num: a number as the language writes it: 26.7 or 26,7.
pub fn num(value: f64, digits: usize, sign: bool) -> String {
    let text = match sign {
        true => format!("{value:+.digits$}"),
        false => format!("{value:.digits$}"),
    };
    match lp_i18n::DECIMAL_COMMA.contains(&lp_i18n::language().code()) {
        true => text.replace('.', ","),
        false => text,
    }
}

/// A number with no decimals, as the language writes it.
pub fn n0(value: f64) -> Value {
    s(num(value, 0, false))
}

/// A number with one decimal, as the language writes it.
pub fn n1(value: f64) -> Value {
    s(num(value, 1, false))
}

/// One decimal, none where it would be zero: "1.8", "6".
fn short(value: f64) -> String {
    let text = num(value, 1, false);
    match text.strip_suffix(".0").or(text.strip_suffix(",0")) {
        Some(whole) => whole.to_string(),
        None => text,
    }
}

/// duration: "45 s", "30 min", "1.8 h", "6 h", "3.5 days".
pub fn duration(seconds: f64) -> String {
    if seconds < 90.0 {
        tf("{n} s", &[("n", n0(seconds))])
    } else if seconds < 5400.0 {
        tf("{n} min", &[("n", n0(seconds / 60.0))])
    } else if seconds < 2.0 * 86400.0 {
        tf("{n} h", &[("n", s(short(seconds / 3600.0)))])
    } else {
        tf("{n} days", &[("n", s(short(seconds / 86400.0)))])
    }
}

/// The local time at a timestamp, as time.localtime gives it.
pub fn local(ts: f64) -> lp_py::datetime::DateTime {
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    let found = lp_py::zones::fromtimestamp(zone, ts.floor());
    found.map_or_else(
        || lp_py::datetime::DateTime {
            year: 1970,
            month: 1,
            day: 1,
            hour: 0,
            minute: 0,
            second: 0,
            microsecond: 0,
        },
        |found| found.0,
    )
}

/// The day of a timestamp: `time.strftime("%Y-%m-%d", time.localtime(ts))`.
pub fn day(ts: f64) -> String {
    let at = local(ts);
    format!("{:04}-{:02}-{:02}", at.year, at.month, at.day)
}

/// clock: `time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))`.
pub fn clock(ts: f64) -> String {
    let at = local(ts);
    format!("{} {:02}:{:02}", day(ts), at.hour, at.minute)
}

/// device_name: "CPU temperature (name of the CPU)": the name is what the readings say,
/// never fixed.
pub fn device_name(kind: &str, names: &Dict) -> String {
    let base = t(match kind {
        "cpu" => "CPU temperature",
        _ => "GPU temperature",
    });
    match names.get(kind).filter(|name| name.truthy()) {
        Some(name) => format!("{base} ({})", name.str()),
        None => base,
    }
}

/// The catalogue's entries of the sensors (`names["sensors"]`), those that are tables.
pub fn sensor_entries(names: &Dict) -> Vec<&Dict> {
    let entries = names.get("sensors").and_then(Value::as_list).unwrap_or(&[]);
    entries.iter().filter_map(Value::as_dict).collect()
}

/// sensor_name: what to call a recorded temperature: the CPU, the GPU, its hot spot, or its
/// reading.
pub fn sensor_name(sensor: &str, names: &Dict) -> String {
    match sensor {
        "cpu_temp" => return t("CPU"),
        "gpu_temp" => return t("GPU"),
        "gpu_hotspot" => return t("GPU hot spot"),
        _ => {}
    }
    for entry in sensor_entries(names) {
        let label = entry.get("label").filter(|label| label.truthy());
        if let (Some(Value::Str(key)), Some(label)) = (entry.get("key"), label) {
            if key == sensor {
                return label.str();
            }
        }
    }
    sensor.to_string()
}

/// basis: what the data is: how long a game (how many rounds) and how long the desktop.
pub fn basis(desk: &[Row], game: &[Row]) -> String {
    let mut parts = Vec::new();
    if !game.is_empty() {
        let n = data::rounds(game);
        let values = [
            ("time", s(duration(data::total(game)))),
            ("n", Value::int(i64::try_from(n).unwrap_or(0))),
        ];
        parts.push(match n {
            1 => tf("{time} game ({n} round)", &values),
            _ => tf("{time} game ({n} rounds)", &values),
        });
    }
    if !desk.is_empty() {
        let time = s(duration(data::total(desk)));
        parts.push(tf("{time} desktop", &[("time", time)]));
    }
    match parts.is_empty() {
        true => t("no data yet"),
        false => parts.join(", "),
    }
}

// -- what the curves do -------------------------------------------------------------------------

fn number(value: Option<&Value>) -> f64 {
    value.and_then(Value::to_f64).unwrap_or(0.0)
}

/// source_lines: `find_source` in words; the sensors are named as `sensor_name` does.
pub fn source_lines(verdict: &Dict, names: &Dict) -> Vec<String> {
    let kind = verdict.get("verdict").and_then(Value::as_str).unwrap_or("");
    let Some(Value::List(span)) = verdict.get("span") else {
        let n = verdict.get("samples").cloned().unwrap_or(Value::None);
        let words = "not enough samples yet in the rising part of the curve ({n})";
        return vec![tf(words, &[("n", n)])];
    };
    let (low, high) = (number(span.first()), number(span.get(1)));
    let mut lines = vec![tf(
        "the measured duty corresponds to {low}..{high} degrees",
        &[("low", n0(low)), ("high", n0(high))],
    )];
    if kind == "too_quiet" {
        lines.push(tf(
            "too quiet for a verdict ({span} K range, {needed} needed): the comparison needs \
             load changes.",
            &[("span", n1(high - low)), ("needed", n0(SPAN_NEEDED))],
        ));
        return lines;
    }
    if kind == "too_few" {
        return lines;
    }
    let empty = Dict::default();
    let best = verdict
        .get("best")
        .and_then(Value::as_dict)
        .unwrap_or(&empty);
    let of = |entry: &Dict, key: &str| number(entry.get(key));
    let named = |entry: &Dict| {
        let sensor = entry.get("sensor").and_then(Value::as_str).unwrap_or("");
        s(sensor_name(sensor, names))
    };
    let r = s(num(of(best, "r"), 2, false));
    let spread = n1(of(best, "spread"));
    if kind == "follows" {
        if of(best, "offset").abs() <= 2.0 {
            lines.push(tf(
                "follows {sensor} exactly (spread {spread} K, r={r})",
                &[("sensor", named(best)), ("spread", spread), ("r", r)],
            ));
        } else {
            lines.push(tf(
                "follows {sensor} with a fixed offset of {offset} K (spread {spread} K, r={r})",
                &[
                    ("sensor", named(best)),
                    ("offset", s(num(of(best, "offset"), 1, true))),
                    ("spread", spread),
                    ("r", r),
                ],
            ));
            lines.push(t("so another sensor of the same part."));
        }
    } else {
        lines.push(tf(
            "NO recorded sensor explains it (the nearest, {sensor}: spread {spread} K, r={r}).",
            &[("sensor", named(best)), ("spread", spread), ("r", r)],
        ));
        let id = match verdict.get("source").filter(|source| source.truthy()) {
            Some(source) => source.clone(),
            None => s("?"),
        };
        lines.push(tf("Sensor in FanControl: {id}", &[("id", id)]));
    }
    let others = verdict
        .get("others")
        .and_then(Value::as_list)
        .unwrap_or(&[]);
    for other in others.iter().filter_map(Value::as_dict) {
        let r = match other.get("r").and_then(Value::to_f64) {
            Some(r) => num(r, 2, false),
            None => "--".into(),
        };
        lines.push(tf(
            "for comparison {sensor}: offset {offset} K, spread {spread} K, r={r}",
            &[
                ("sensor", named(other)),
                ("offset", s(num(of(other, "offset"), 1, true))),
                ("spread", n1(of(other, "spread"))),
                ("r", s(r)),
            ],
        ));
    }
    lines
}

/// change_lines: what changed between two FanControl states, in words.
pub fn change_lines(changes: &[Value]) -> Vec<String> {
    let mut lines = Vec::new();
    for c in changes.iter().filter_map(Value::as_dict) {
        let of = |key: &str| c.get(key).cloned().unwrap_or(Value::None);
        let joined = |key: &str, with: &str| {
            let items = c.get(key).and_then(Value::as_list).unwrap_or(&[]);
            let texts: Vec<String> = items.iter().map(Value::str).collect();
            s(texts.join(with))
        };
        let curve = ("curve", of("curve"));
        match c.get("kind").and_then(Value::as_str).unwrap_or("") {
            "curve-added" => lines.push(tf("Curve {curve} created", &[curve])),
            "curve-removed" => lines.push(tf("Curve {curve} deleted", &[curve])),
            "source" => lines.push(tf(
                "{curve}: sensor {old} → {new}",
                &[curve, ("old", of("old")), ("new", of("new"))],
            )),
            "points" => lines.push(tf(
                "{curve}: points {old} → {new}",
                &[
                    curve,
                    ("old", joined("old", " ")),
                    ("new", joined("new", " ")),
                ],
            )),
            "mix-function" => lines.push(tf(
                "{curve}: mix function {old} → {new}",
                &[curve, ("old", of("old")), ("new", of("new"))],
            )),
            "hysteresis" => lines.push(tf("{curve}: hysteresis changed", &[curve])),
            "fan" => lines.push(tf(
                "Fan {fan}: {fields}",
                &[("fan", of("fan")), ("fields", joined("fields", ", "))],
            )),
            _ => {}
        }
    }
    if lines.is_empty() {
        lines.push(t("only small things that do not touch the curves"));
    }
    lines
}

/// Duty -> speed, as measured.
pub type Characteristic = Vec<(f64, f64)>;

/// percent_rpm: "52 % (≈ 782 RPM)", or honestly that it was never measured there.
pub fn percent_rpm(duty: f64, characteristic: &Characteristic) -> String {
    let said = [("duty", n0(duty))];
    if let Some(speed) = rpm_at(characteristic, Some(duty)) {
        return tf(
            "{duty} % (≈ {rpm} RPM)",
            &[("duty", n0(duty)), ("rpm", n0(speed))],
        );
    }
    match characteristic.first() {
        Some(first) if duty < first.0 => tf(
            "{duty} % (below everything measured so far, speed not known yet)",
            &said,
        ),
        Some(_) => tf(
            "{duty} % (above everything measured so far, speed not known yet)",
            &said,
        ),
        None => tf("{duty} %", &said),
    }
}

/// rpm_at: the speed at this duty, from the measured characteristic, and only inside what
/// was measured.
pub fn rpm_at(characteristic: &Characteristic, duty: Option<f64>) -> Option<f64> {
    let (first, last, duty) = (characteristic.first()?, characteristic.last()?, duty?);
    if duty < first.0 - 1.5 || duty > last.0 + 1.5 {
        return None;
    }
    if duty <= first.0 {
        return Some(first.1);
    }
    for pair in characteristic.windows(2) {
        let ((d0, r0), (d1, r1)) = (pair[0], pair[1]);
        if duty <= d1 {
            return Some(r0 + (r1 - r0) * (duty - d0) / (d1 - d0));
        }
    }
    Some(last.1)
}

/// `a // b` of two floats.
pub fn floor_div(a: f64, b: f64) -> f64 {
    let rest = a % b;
    let mut div = (a - rest) / b;
    if rest != 0.0 && ((b < 0.0) != (rest < 0.0)) {
        div -= 1.0;
    }
    if div == 0.0 {
        return (0.0f64).copysign(a / b);
    }
    let floor = div.floor();
    if div - floor > 0.5 {
        floor + 1.0
    } else {
        floor
    }
}

/// characteristic_of: duty -> speed, as measured: the duties in steps of two points are
/// averaged, and only steps in which the fan ran for a while count (not a start-up).
pub fn characteristic_of(rows: &[Row], fan: &str) -> Characteristic {
    let mut steps: IndexMap<i64, Vec<(f64, f64, f64)>> = IndexMap::new();
    for r in rows {
        if let Some((Some(rpm), Some(duty))) = r.fans.get(fan) {
            let step = floor_div(*duty, 2.0) as i64;
            steps
                .entry(step)
                .or_default()
                .push((*duty, *rpm, r.duration));
        }
    }
    steps.sort_keys();
    let mut found = Vec::new();
    for items in steps.values() {
        let seconds = lp_py::sum_floats(items.iter().map(|(_, _, held)| *held));
        if seconds >= 30.0 {
            let duty = lp_py::sum_floats(items.iter().map(|(d, _, held)| d * held)) / seconds;
            let speeds: Vec<(f64, f64)> =
                items.iter().map(|(_, rpm, held)| (*rpm, *held)).collect();
            found.push((duty, weighted_all(&speeds, 0.5).unwrap_or(0.0)));
        }
    }
    found
}

/// compare: "Game: 52 % ... instead of 33 % ...", or that it stays about the same.
pub fn compare(title: &str, new: f64, now: f64, characteristic: &Characteristic) -> String {
    if (new - now).abs() < 2.0 {
        return tf(
            "{title}: practically unchanged ({now} % → {new} %)",
            &[("title", s(title)), ("now", n0(now)), ("new", n0(new))],
        );
    }
    tf(
        "{title}: {new} instead of {now}",
        &[
            ("title", s(title)),
            ("new", s(percent_rpm(new, characteristic))),
            ("now", s(percent_rpm(now, characteristic))),
        ],
    )
}

fn whole(x: f64) -> Value {
    Value::Int(format!("{:.0}", x.trunc()).parse().unwrap_or_default())
}

/// points_text: curve points as a list in words.
pub fn points_text(points: &[(f64, f64)]) -> String {
    let said = points.iter().map(|(temp, duty)| {
        tf(
            "{temp} °C → {duty} %",
            &[("temp", whole(*temp)), ("duty", whole(*duty))],
        )
    });
    said.collect::<Vec<_>>().join(", ")
}

// -- the plain-text report ------------------------------------------------------------------------

fn row(label: &str, text: &str) -> String {
    format!("  {label:<14}{text}")
}

/// What a row counts as: its game, or that it is one, or the desktop.
pub fn state_name(r: &Row) -> String {
    match r.game.as_ref().filter(|game| !game.is_empty()) {
        Some(game) => game.clone(),
        None if r.in_game() => t("Game (unnamed)"),
        None => t("Desktop"),
    }
}

/// scope_lines: how much was recorded, and how it divides between the desktop and each
/// game.
pub fn scope_lines(rows: &[Row]) -> Vec<String> {
    let (Some(first), Some(last)) = (rows.first(), rows.last()) else {
        return Vec::new();
    };
    let seconds = data::total(rows);
    let mut lines = vec![tf(
        "RECORDED  {time} in {rows} rows, from {first} to {last}",
        &[
            ("time", s(duration(seconds))),
            ("rows", Value::int(i64::try_from(rows.len()).unwrap_or(0))),
            ("first", s(clock(first.ts))),
            ("last", s(clock(last.ts))),
        ],
    )];
    let mut by_state: IndexMap<String, f64> = IndexMap::new();
    for r in rows {
        *by_state.entry(state_name(r)).or_insert(0.0) += r.duration;
    }
    let mut states: Vec<(&String, &f64)> = by_state.iter().collect();
    states.sort_by(|a, b| b.1.partial_cmp(a.1).unwrap_or(std::cmp::Ordering::Equal));
    for (name, held) in states {
        let share = 100.0 * held / seconds;
        lines.push(format!(
            "         {name:<28} {:>8}  {share:4.1} %",
            duration(*held)
        ));
    }
    lines
}

/// The fan of a name the rows know: FanControl's of that name, or of it with `_` for its
/// spaces.
pub fn fan_of<'a>(fans: &'a IndexMap<String, Fan>, name: &str) -> Option<&'a Fan> {
    fans.get(name).or_else(|| fans.get(&name.replace(' ', "_")))
}

fn highest(values: impl Iterator<Item = f64>) -> f64 {
    let mut top: Option<f64> = None;
    for value in values {
        if top.is_none_or(|top| value > top) {
            top = Some(value);
        }
    }
    top.unwrap_or(0.0)
}

/// fan_lines: what one fan did, and how that fits its curve.
pub fn fan_lines(
    rows: &[Row],
    name: &str,
    fans: &IndexMap<String, Fan>,
    curves: &IndexMap<String, Curve>,
    names: &Dict,
) -> Vec<String> {
    let pairs = data::data_of(rows, name);
    if pairs.is_empty() {
        return Vec::new();
    }
    let fan = fan_of(fans, name);
    let curve = fan.and_then(|fan| curves.get(&fan.curve));
    let mut lines = vec![String::new(), lp_py::upper(name)];
    match curve {
        Some(curve) => {
            let source = match curve.source.as_ref().filter(|source| !source.is_empty()) {
                Some(source) => source.clone(),
                None => tf(
                    "a mix of {parts}",
                    &[("parts", s(curve.mix.join(&t(" and "))))],
                ),
            };
            let said = tf(
                "{curve} on {source}",
                &[("curve", s(&curve.name)), ("source", s(source))],
            );
            lines.push(row(&t("Curve"), &said));
            if !curve.points.is_empty() {
                let g = |x: f64| lp_py::format_g(x, 6);
                let points = curve
                    .points
                    .iter()
                    .map(|p| format!("{}->{}%", g(p.0), g(p.1)));
                lines.push(row("", &points.collect::<Vec<_>>().join(", ")));
            }
        }
        None => lines.push(row(
            &t("Curve"),
            &t("none (FanControl does not drive this fan)"),
        )),
    }
    let held = lp_py::sum_floats(pairs.iter().map(|(r, _)| r.duration));
    let duty: Vec<(f64, f64)> = pairs
        .iter()
        .filter_map(|(r, (_rpm, d))| Some(((*d)?, r.duration)))
        .collect();
    let speed: Vec<(f64, f64)> = pairs
        .iter()
        .filter_map(|(r, (rpm, _d))| Some(((*rpm)?, r.duration)))
        .collect();
    let middle = |pairs: &[(f64, f64)], share: f64| weighted_all(pairs, share).unwrap_or(0.0);
    if !duty.is_empty() {
        let said = tf(
            "median {median} %, p95 {high} %, highest {top} %",
            &[
                ("median", n1(middle(&duty, 0.5))),
                ("high", n1(middle(&duty, 0.95))),
                ("top", n1(highest(duty.iter().map(|(d, _)| *d)))),
            ],
        );
        lines.push(row(&t("Duty"), &said));
        let minimum = fan
            .and_then(|fan| fan.minimum)
            .filter(|least| *least != 0.0);
        let minimum = minimum.unwrap_or(20.0);
        let share = |keep: &dyn Fn(f64) -> bool| {
            let kept = duty.iter().filter(|(d, _)| keep(*d)).map(|(_, s)| *s);
            lp_py::sum_floats(kept) / held * 100.0
        };
        let said = tf(
            "{top} % of the time at the limit (>=95 %), {low} % at the minimum",
            &[
                ("top", n1(share(&|d| d >= 95.0))),
                ("low", n1(share(&|d| d <= minimum + 2.0))),
            ],
        );
        lines.push(row("", &said));
    }
    if !speed.is_empty() {
        let said = tf(
            "median {median} RPM, p95 {high} RPM, highest {top} RPM",
            &[
                ("median", n0(middle(&speed, 0.5))),
                ("high", n0(middle(&speed, 0.95))),
                ("top", n0(highest(speed.iter().map(|(s, _)| *s)))),
            ],
        );
        lines.push(row(&t("Speed"), &said));
    }
    let mut steps: IndexMap<i64, Vec<(f64, f64)>> = IndexMap::new();
    for (r, (rpm, d)) in &pairs {
        if let (Some(rpm), Some(d)) = (rpm, d) {
            let step = floor_div(*d, 10.0) as i64 * 10;
            steps.entry(step).or_default().push((*rpm, r.duration));
        }
    }
    if !steps.is_empty() {
        steps.sort_keys();
        let cells = steps
            .iter()
            .map(|(k, v)| format!("{k}%: {}", num(middle(v, 0.5), 0, false)));
        let table = cells.collect::<Vec<_>>().join("  ");
        lines.push(row(
            &t("Duty to speed"),
            &tf("{table} RPM", &[("table", s(table))]),
        ));
    }
    if let Some(curve) = curve.filter(|curve| curve.mix.is_empty() && !duty.is_empty()) {
        match data::deviation(&pairs, curve) {
            Some((median, high, seconds)) => {
                let said = tf(
                    "measured: the duty is off the curve by {median} on average, p95 {high} \
                     points ({time})",
                    &[
                        ("median", n1(median)),
                        ("high", n1(high)),
                        ("time", s(duration(seconds))),
                    ],
                );
                lines.push(row(&t("Input"), &said));
            }
            None => {
                let found = data::find_source(&pairs, curve, &data::candidates(rows));
                let verdict = source_lines(&found, names);
                lines.push(row(&t("Sensor"), &verdict[0]));
                lines.extend(verdict[1..].iter().map(|line| row("", line)));
            }
        }
    }
    lines
}

/// How often a fan's duty jumps by `step` points or more over the rows.
pub fn jumps(rows: &[Row], fan: &str, step: f64) -> u64 {
    let (mut jumps, mut before) = (0, None);
    for r in rows {
        let Some(duty) = r.duty(fan) else {
            continue;
        };
        if before.is_some_and(|before: f64| (duty - before).abs() >= step) {
            jumps += 1;
        }
        before = Some(duty);
    }
    jumps
}

/// notable_lines: what the data says by itself, with no advice into the blue.
pub fn notable_lines(
    rows: &[Row],
    fans: &IndexMap<String, Fan>,
    curves: &IndexMap<String, Curve>,
) -> Vec<String> {
    let seconds = data::total(rows);
    if seconds < ENOUGH_S {
        return vec![
            String::new(),
            tf(
                "No conclusions yet: they need at least {needed} of recording (here {have}).",
                &[
                    ("needed", s(duration(ENOUGH_S))),
                    ("have", s(duration(seconds))),
                ],
            ),
        ];
    }
    let mut lines = vec![String::new(), t("NOTABLE")];
    let before = lines.len();
    let names = data::fan_names(rows);
    for name in &names {
        // curve points that were never needed (by the duty reached)
        let curve = fan_of(fans, name).and_then(|fan| curves.get(&fan.curve));
        let Some(curve) = curve.filter(|c| c.mix.is_empty() && !c.points.is_empty()) else {
            continue;
        };
        let mut duties = rows.iter().filter_map(|r| r.duty(name)).peekable();
        if duties.peek().is_none() {
            continue;
        }
        let top = highest(duties);
        let unused: Vec<f64> = (curve.points.iter())
            .filter(|(_, duty)| *duty > top + 1.0)
            .map(|(temp, _)| *temp)
            .collect();
        if unused.len() >= 2 {
            let lowest = unused.iter().copied().fold(f64::INFINITY, f64::min);
            let said = tf(
                "{fan} never went above {top} %: the points from {temp} degrees of {curve} \
                 were never needed.",
                &[
                    ("fan", s(name)),
                    ("top", n0(top)),
                    ("temp", n0(lowest)),
                    ("curve", s(&curve.name)),
                ],
            );
            lines.push(format!("  {said}"));
        }
    }
    for name in &names {
        // oscillation: how often the duty jumps
        let per_hour = jumps(rows, name, 5.0) as f64 / (seconds / 3600.0);
        if per_hour >= 20.0 {
            let said = tf(
                "{fan} changes its duty {n} times an hour by more than 5 points: audible \
                 oscillation, check hysteresis or response time.",
                &[("fan", s(name)), ("n", n0(per_hour))],
            );
            lines.push(format!("  {said}"));
        }
    }
    let desk: Vec<Row> = rows.iter().filter(|r| !r.in_game()).cloned().collect();
    if data::total(&desk) > 3600.0 {
        // high on the desktop: noise without a reason
        for name in data::fan_names(&desk) {
            let duties: Vec<(Option<f64>, f64)> =
                desk.iter().map(|r| (r.duty(&name), r.duration)).collect();
            if let Some(median) = weighted(&duties, 0.5).filter(|median| *median >= 40.0) {
                let said = tf(
                    "{fan} runs at {median} % on the desktop on average: that is a lot for \
                     idling.",
                    &[("fan", s(&name)), ("median", n0(median))],
                );
                lines.push(format!("  {said}"));
            }
        }
    }
    if lines.len() == before {
        lines.push(format!("  {}", t("nothing the data gives.")));
    }
    lines
}

/// trend_lines: is the cooling getting worse, or was it just warmer outside? The median CPU
/// temperature per day in one load band (5..25 %), next to the outside temperature: a day
/// with a lot of gaming would look like a dusty cooler otherwise.
pub fn trend_lines(rows: &[Row]) -> Vec<String> {
    let mut days: IndexMap<String, Vec<&Row>> = IndexMap::new();
    for r in rows {
        if r.cpu_load.is_some_and(|load| (5.0..=25.0).contains(&load)) {
            days.entry(day(r.ts)).or_default().push(r);
        }
    }
    if days.len() < 2 || data::total(rows) < ENOUGH_S {
        return Vec::new();
    }
    let mut lines = vec![
        String::new(),
        t("TREND  (CPU temperature at 5..25 % load, per day)"),
    ];
    let skip = days.len().saturating_sub(21);
    for (day, found) in days.iter().skip(skip) {
        let inside: Vec<(Option<f64>, f64)> =
            found.iter().map(|r| (r.cpu_temp, r.duration)).collect();
        let outside: Vec<(Option<f64>, f64)> = (found.iter())
            .filter(|r| r.outside.is_some())
            .map(|r| (r.outside, r.duration))
            .collect();
        let or_not = |value: Option<f64>, unit: &str| match value {
            Some(value) => format!("{}{unit}", num(value, 1, false)),
            None => "?".into(),
        };
        let held = lp_py::sum_floats(found.iter().map(|r| r.duration));
        let said = tf(
            "{day}  {inside} °C   outside {outside}   ({time})",
            &[
                ("day", s(day)),
                ("inside", s(or_not(weighted(&inside, 0.5), ""))),
                ("outside", s(or_not(weighted(&outside, 0.5), " °C"))),
                ("time", s(duration(held))),
            ],
        );
        lines.push(format!("  {said}"));
    }
    lines
}

/// advice_lines: the recommendations as text: what to do, why, what it changes, and what it
/// rests on.
pub fn advice_lines(advice: &Dict) -> Vec<String> {
    let found = advice.get("list").and_then(Value::as_list).unwrap_or(&[]);
    if found.is_empty() {
        return Vec::new();
    }
    let of = |table: &Dict, key: &str| table.get(key).cloned().unwrap_or(Value::None);
    let mut lines = vec![
        String::new(),
        tf(
            "RECOMMENDATIONS  (since the last change in FanControl, from {basis})",
            &[("basis", of(advice, "basis"))],
        ),
    ];
    for rec in found.iter().filter_map(Value::as_dict) {
        let text = |key: &str| of(rec, key).str();
        let list = |key: &str| rec.get(key).and_then(Value::as_list).unwrap_or(&[]);
        lines.push(String::new());
        lines.push(format!("  {}. {}", text("number"), text("title")));
        lines.push(format!("     {}", text("why")));
        lines.extend(
            list("do")
                .iter()
                .map(|step| format!("     - {}", step.str())),
        );
        lines.extend(
            list("effect")
                .iter()
                .map(|effect| format!("     = {}", effect.str())),
        );
        if rec.get("note").is_some_and(Value::truthy) {
            lines.push(format!("     ({})", text("note")));
        }
        let said = tf(
            "Basis: {basis} · {confidence}",
            &[
                ("basis", of(rec, "basis")),
                ("confidence", of(rec, "confidence")),
            ],
        );
        lines.push(format!("     {said}"));
    }
    lines
}

/// report: the whole report as lines: what was recorded, each fan, what is notable, the
/// trend and the recommendations. `names` are the hardware and sensor names the memory
/// noted.
pub fn report(
    rows: &[Row],
    fans: &IndexMap<String, Fan>,
    curves: &IndexMap<String, Curve>,
    advice: &Dict,
    names: &Dict,
) -> Vec<String> {
    let mut lines = scope_lines(rows);
    for name in data::fan_names(rows) {
        lines.extend(fan_lines(rows, &name, fans, curves, names));
    }
    lines.extend(notable_lines(rows, fans, curves));
    lines.extend(trend_lines(rows));
    lines.extend(advice_lines(advice));
    lines
}
