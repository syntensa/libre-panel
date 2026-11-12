//! The round report: a game just played, set against the last ones of the same program
//! (services/round_report.py).
//!
//! `build` reads the memory's database; `readings` turns the report into plain readings
//! (`report.fps`, `report.level.0`, ...) for a theme made of the usual widgets, and the course
//! of the temperatures into a list a graph draws whole (`report.course.cpu_temp`). A screen
//! that draws more reads the whole report from the state `report` (docs/PLUGINS.md has its
//! shape). `session_readings` does the same for a round that is still under way
//! (`session.time`, `session.fps`, ...): its totals so far.

use indexmap::IndexMap;
use lp_i18n::t;
use lp_py::{Dict, Value};
use num_bigint::BigInt;
use rusqlite::Connection;

use crate::memory_store::{rows, FRAME_FIGURES, SESSION_PEAKS};

const PEAK_UNITS: [(&str, &str); 7] = [
    ("cpu_temp", "°C"),
    ("gpu_temp", "°C"),
    ("gpu_hotspot", "°C"),
    ("cpu_power", "W"),
    ("gpu_power", "W"),
    ("vram_used", "GB"),
    ("power", "W"),
];
const FRAME_UNITS: [(&str, &str); 5] = [
    ("fps_avg", "fps"),
    ("fps_1low", "fps"),
    ("fps_01low", "fps"),
    ("frametime_avg", "ms"),
    ("frametime_max", "ms"),
];

/// Readings by their keys: (value, unit, label).
pub type Told = IndexMap<String, (Value, String, String)>;

/// What is averaged over the earlier games.
fn values() -> Vec<String> {
    let mut names: Vec<String> = ["fps_mean", "fps_p01", "fps_low_mean", "frametime_mean"]
        .map(str::to_string)
        .to_vec();
    names.extend(SESSION_PEAKS.iter().map(|name| format!("{name}_max")));
    names.extend(FRAME_FIGURES.iter().map(|name| (*name).to_string()));
    names
}

fn number(value: Option<&Value>) -> Option<f64> {
    match value? {
        Value::Bool(on) => Some(f64::from(u8::from(*on))),
        value @ (Value::Int(_) | Value::Float(_)) => value.to_f64(),
        _ => None,
    }
}

/// `sum(items)`: whole numbers exactly, others as Python adds floats; an error where Python
/// raises.
fn sum(items: &Value) -> Result<Value, String> {
    let Value::List(items) = items else {
        return Err(format!(
            "TypeError: '{}' object is not iterable",
            items.type_name()
        ));
    };
    let mut whole = BigInt::default();
    let mut floats = false;
    for item in items {
        match item {
            Value::Bool(on) => whole += u8::from(*on),
            Value::Int(n) => whole += n,
            Value::Float(_) => floats = true,
            other => {
                return Err(format!(
                    "TypeError: unsupported operand type(s) for +: 'int' and '{}'",
                    other.type_name()
                ))
            }
        }
    }
    Ok(match floats {
        true => Value::Float(lp_py::sum_floats(
            items.iter().filter_map(|item| number(Some(item))),
        )),
        false => Value::Int(whole),
    })
}

fn levels(text: Option<&Value>) -> Result<Dict, String> {
    let found = match text {
        Some(Value::Str(text)) if !text.is_empty() => lp_py::json::loads(text).ok(),
        Some(text) if text.truthy() => None, // (no text: json.loads raises TypeError)
        _ => Some(Value::Dict(Dict::default())),
    };
    let found = match found.unwrap_or_else(|| Value::Dict(Dict::default())) {
        Value::Dict(found) => found,
        other => {
            return Err(format!(
                "AttributeError: '{}' object has no attribute 'get'",
                other.type_name()
            ))
        }
    };
    let part = |key: &str| match found.get(key).filter(|value| value.truthy()) {
        Some(value) => value.clone(),
        None => Value::List(Vec::new()),
    };
    let mut out = Dict::default();
    out.insert("edges".into(), part("edges"));
    out.insert("seconds".into(), part("seconds"));
    Ok(out)
}

/// The course of a round's temperatures as stored (memory's Course), or None.
fn course(text: Option<&Value>) -> Value {
    let found = match text {
        Some(Value::Str(text)) if !text.is_empty() => lp_py::json::loads(text).ok(),
        _ => None,
    };
    match found {
        Some(found @ Value::Dict(_)) => found,
        _ => Value::None,
    }
}

/// build: the report of a game (`session_id`; None: the latest that was long enough) against
/// the last `compare` games of the same program that were long enough too. None when the
/// game was shorter than `min_s` seconds or had no frame rates. Reads the file: call it on
/// the store's thread, after the game's row was written. An error where Python raises.
pub fn build(
    con: &Connection,
    session_id: Option<i64>,
    min_s: f64,
    compare: i64,
) -> Result<Option<Dict>, String> {
    let found = match session_id {
        None => rows(
            con,
            "SELECT * FROM sessions WHERE closed = 1 AND duration_s >= ? AND fps_s > 0 \
             ORDER BY started DESC LIMIT 1",
            &[Value::Float(min_s)],
        )?,
        Some(id) => rows(
            con,
            "SELECT * FROM sessions WHERE id = ?",
            &[Value::int(id)],
        )?,
    };
    let Some(mut session) = found.into_iter().next() else {
        return Ok(None);
    };
    let long = number(session.get("duration_s")).unwrap_or(0.0) >= min_s;
    if !long || !session.get("fps_s").is_some_and(Value::truthy) {
        return Ok(None);
    }
    let own = levels(session.get("levels"))?;
    session.insert("levels".into(), Value::Dict(own.clone()));
    let stored = course(session.get("course"));
    session.insert("course".into(), stored);
    let of = |key: &str| session.get(key).cloned().unwrap_or(Value::None);
    let (column, value) = match of("program").truthy() {
        true => ("program", of("program")),
        false => ("game", of("game")),
    };
    // (a file the service has not opened since an update lacks the newer columns)
    let earlier = rows(
        con,
        &format!(
            "SELECT * FROM sessions WHERE {column} = ? AND id <> ? AND started < ? \
             AND closed = 1 AND duration_s >= ? AND fps_s > 0 ORDER BY started DESC LIMIT ?"
        ),
        &[
            value,
            of("id"),
            of("started"),
            Value::Float(min_s),
            Value::int(compare),
        ],
    )?;
    let mut means = Dict::default();
    for name in values() {
        let found: Vec<f64> = (earlier.iter())
            .filter_map(|row| number(row.get(&name)))
            .collect();
        let mean = match found.len() {
            0 => Value::None,
            count => Value::Float(lp_py::sum_floats(found) / count as f64),
        };
        means.insert(name, mean);
    }
    let mut shares: Vec<Vec<f64>> = Vec::new();
    for row in &earlier {
        let theirs = levels(row.get("levels"))?;
        let total = sum(&theirs["seconds"])?;
        if theirs["edges"] == own["edges"] && total.truthy() {
            let total = number(Some(&total)).unwrap_or(1.0);
            let seconds = theirs["seconds"].as_list().unwrap_or(&[]);
            let share = seconds.iter().filter_map(|n| number(Some(n)));
            shares.push(share.map(|seconds| seconds / total).collect());
        }
    }
    let average = match shares.first() {
        None => Value::None,
        Some(first) => {
            if shares.iter().any(|share| share.len() != first.len()) {
                return Err("ValueError: zip() arguments have different lengths".into());
            }
            let mean = |at: usize| {
                let column = shares.iter().map(|share| share[at]);
                Value::Float(lp_py::sum_floats(column) / shares.len() as f64)
            };
            Value::List((0..first.len()).map(mean).collect())
        }
    };
    let mut comparison = Dict::default();
    let count = i64::try_from(earlier.len()).unwrap_or(i64::MAX);
    comparison.insert("count".into(), Value::int(count));
    comparison.insert("mean".into(), Value::Dict(means));
    comparison.insert("levels".into(), average);
    let mut report = Dict::default();
    report.insert("session".into(), Value::Dict(session));
    report.insert("comparison".into(), Value::Dict(comparison));
    Ok(Some(report))
}

/// level_names: the frame-rate ranges the levels stand for: "< 60", "60–90", ..., "165+".
pub fn level_names(edges: &[f64]) -> Vec<String> {
    let g = |edge: &f64| lp_py::format_g(*edge, 6);
    let (Some(first), Some(last)) = (edges.first(), edges.last()) else {
        return Vec::new();
    };
    let mut names = vec![format!("< {}", g(first))];
    names.extend(
        edges
            .windows(2)
            .map(|pair| format!("{}–{}", g(&pair[0]), g(&pair[1]))),
    );
    names.push(format!("{}+", g(last)));
    names
}

/// peak_labels: the names of the highest values, in the language of the panel.
fn peak_label(name: &str) -> String {
    t(match name {
        "cpu_temp" => "CPU temp",
        "gpu_temp" => "GPU temp",
        "gpu_hotspot" => "GPU hot spot",
        "cpu_power" => "CPU power",
        "gpu_power" => "GPU power",
        "vram_used" => "Video memory",
        _ => "PC power",
    })
}

/// frame_labels: the names of what the round's frames came to, in the language of the panel.
fn frame_label(name: &str) -> String {
    t(match name {
        "fps_avg" => "Average FPS",
        "fps_1low" => "1% low",
        "fps_01low" => "0.1% low",
        "frametime_avg" => "Frame time",
        _ => "Longest frame",
    })
}

/// What a round under way has come to (memory's Session.totals).
pub struct Totals {
    pub duration_s: f64,
    pub fps_mean: Option<f64>,
    pub edges: Vec<f64>,
    pub seconds: Vec<u64>,
    /// the highest values by name
    pub peaks: IndexMap<&'static str, f64>,
}

fn float(value: Option<f64>) -> Value {
    value.map_or(Value::None, Value::Float)
}

/// session_readings: a round under way as plain readings, key -> (value, unit, label), in
/// the language of the panel: how long it has been played (`session.time`), its mean frame
/// rate (`session.fps`), the share of the time at each range of frame rates in percent
/// (`session.level.<n>`, the slowest first, as `report.level.<n>`) and the highest value of
/// each peak since it began (`session.<peak>_max`). A value may be None (not known yet).
pub fn session_readings(totals: &Totals) -> Told {
    let mut out = Told::new();
    let mut set = |key: String, value: Value, unit: &str, label: String| {
        out.insert(key, (value, unit.to_string(), label));
    };
    let time = Value::Float(totals.duration_s);
    set("session.time".into(), time, "s", t("Played"));
    let fps = float(totals.fps_mean);
    set("session.fps".into(), fps, "fps", t("Average FPS"));
    let total: u64 = totals.seconds.iter().sum();
    for (i, name) in level_names(&totals.edges).into_iter().enumerate() {
        let seconds = totals.seconds.get(i).filter(|_| total != 0);
        let share = seconds.map(|seconds| (100.0 * *seconds as f64) / total as f64);
        set(format!("session.level.{i}"), float(share), "%", name);
    }
    for (name, unit) in PEAK_UNITS {
        if name != "power" {
            // (the PC's, from the power service: the report's alone)
            let peak = float(totals.peaks.get(name).copied());
            set(format!("session.{name}_max"), peak, unit, peak_label(name));
        }
    }
    out
}

/// `time.strftime("%H:%M", time.localtime(at))`.
fn clock(at: Option<&Value>) -> Result<String, String> {
    let at = match at {
        Some(Value::None) | None => std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0.0, |since| since.as_secs_f64()),
        other => {
            number(other).ok_or("TypeError: 'str' object cannot be interpreted as an integer")?
        }
    };
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    let moment = lp_py::zones::fromtimestamp(zone, at.floor())
        .ok_or("OverflowError: timestamp out of range for platform time_t")?
        .0;
    Ok(format!("{:02}:{:02}", moment.hour, moment.minute))
}

/// Who held the frame rate down, by share of the time: "GPU 94 %  CPU 6 %".
fn held_back(session: &Dict) -> Value {
    let mut held: Vec<(f64, &str)> = ["gpu", "cpu", "cap"]
        .iter()
        .map(|name| {
            let seconds = number(session.get(&format!("limit_{name}_s")));
            (seconds.unwrap_or(0.0), *name)
        })
        .collect();
    held.sort_by(|a, b| b.0.total_cmp(&a.0).then(b.1.cmp(a.1)));
    let total: f64 = held.iter().map(|(seconds, _)| seconds).sum();
    let parts: Vec<String> = held
        .iter()
        .filter(|(seconds, _)| total != 0.0 && seconds / total >= 0.005)
        .map(|(seconds, name)| {
            let share = 100.0 * seconds / total;
            format!("{} {share:.0} %", name.to_uppercase())
        })
        .collect();
    match parts.is_empty() {
        true => Value::None,
        false => Value::Str(parts.join("  ")),
    }
}

/// readings: the report as plain readings: key -> (value, unit, label). The labels, and the
/// texts that are values, are in the language of the panel. A value may be None (not known).
/// An error where Python raises.
pub fn readings(report: &Dict) -> Result<Told, String> {
    let empty = Dict::default();
    let dict = |value: Option<&Value>| value.and_then(Value::as_dict).cloned();
    let session = dict(report.get("session")).ok_or("KeyError: 'session'")?;
    let comparison = dict(report.get("comparison")).ok_or("KeyError: 'comparison'")?;
    let count = number(comparison.get("count")).unwrap_or(0.0);
    let means = match count != 0.0 {
        true => dict(comparison.get("mean")).unwrap_or_default(),
        false => empty.clone(),
    };
    let against = |key: &str| -> Value {
        match (number(session.get(key)), number(means.get(key))) {
            (Some(value), Some(mean)) => Value::Float(value - mean),
            _ => Value::None,
        }
    };
    let of = |key: &str| session.get(key).cloned().unwrap_or(Value::None);
    let text = |words: &str| Value::Str(t(words));
    let versus = if count > 1.0 {
        let n = comparison.get("count").cloned().unwrap_or(Value::None);
        let words = "Compared with the last {n} rounds";
        lp_i18n::t_format(words, &[("n", n)]).unwrap_or_else(|_| t(words))
    } else if count == 1.0 {
        t("Compared with the last round")
    } else {
        t("First round of this game")
    };
    let played = [clock(session.get("started"))?, clock(session.get("ended"))?].join(" – ");
    // the heading's hint: the tick on each bar of the chart is the earlier rounds' average
    let levels = t(match count != 0.0 {
        true => "Share of the round · tick: average",
        false => "Share of the round at each frame rate",
    });
    let mut out = Told::new();
    let mut set = |key: &str, value: Value, unit: &str, label: String| {
        out.insert(key.to_string(), (value, unit.to_string(), label));
    };
    let average = || t("Against the average");
    set("report.title", text("Round report"), "", t("Round report"));
    set("report.game", of("game"), "", t("Game"));
    set("report.when", Value::Str(played), "", t("Played"));
    set("report.length", of("duration_s"), "s", t("Length"));
    set("report.outside", of("outside_temp"), "°C", t("Outside"));
    set("report.versus", Value::Str(versus), "", t("Compared with"));
    set("report.fps", of("fps_mean"), "fps", t("Average FPS"));
    set("report.fps_delta", against("fps_mean"), "fps", average());
    set("report.low", of("fps_low_mean"), "fps", t("1% low"));
    set(
        "report.low_delta",
        against("fps_low_mean"),
        "fps",
        average(),
    );
    set(
        "report.frametime",
        of("frametime_mean"),
        "ms",
        t("Frame time"),
    );
    let delta = against("frametime_mean");
    set("report.frametime_delta", delta, "ms", average());
    let earlier_rounds = comparison.get("count").cloned().unwrap_or(Value::None);
    set("report.count", earlier_rounds, "", t("Earlier rounds"));
    set("report.limit", held_back(&session), "", t("Held back by"));
    set("report.head.levels", Value::Str(levels), "", String::new());
    let peaks = text("Highest values of the round");
    set("report.head.peaks", peaks, "", String::new());
    set("report.head.course", text("Temperature"), "", String::new());
    // what every frame of the round came to (none for a round recorded before it was counted)
    for (name, unit) in FRAME_UNITS {
        set(&format!("report.{name}"), of(name), unit, frame_label(name));
        set(
            &format!("report.{name}_delta"),
            against(name),
            unit,
            average(),
        );
    }
    // the course of the temperatures: a list as text, which a graph draws across its width
    let course = dict(session.get("course").filter(|course| course.truthy()));
    let course = course.unwrap_or_default();
    for (name, label) in [("cpu_temp", "CPU temp"), ("gpu_temp", "GPU temp")] {
        let drawn = match course.get(name) {
            Some(points @ Value::List(items)) if items.len() > 1 => {
                Value::Str(lp_py::json::dumps(points, &lp_py::json::Dumps::default()))
            }
            _ => Value::None,
        };
        set(&format!("report.course.{name}"), drawn, "°C", t(label));
    }
    let held = ["gpu", "cpu", "cap"].map(|name| {
        let seconds = number(session.get(&format!("limit_{name}_s")));
        (name, seconds.unwrap_or(0.0))
    });
    let total_held: f64 = held.iter().map(|(_, seconds)| seconds).sum();
    for (name, seconds) in held {
        // each one's share, for a bar in up to three colours
        let share = match total_held != 0.0 {
            true => 100.0 * seconds / total_held,
            false => 0.0,
        };
        let share = float(Some(share).filter(|share| *share >= 0.5));
        set(
            &format!("report.limit.{name}"),
            share,
            "%",
            name.to_uppercase(),
        );
    }
    for name in SESSION_PEAKS {
        let unit = PEAK_UNITS.iter().find(|(peak, _)| *peak == name);
        let unit = unit.map_or("", |(_, unit)| unit);
        let column = format!("{name}_max");
        set(
            &format!("report.{name}"),
            of(&column),
            unit,
            peak_label(name),
        );
        let usual = means.get(&column).cloned().unwrap_or(Value::None);
        let label = t("Average of the earlier rounds");
        set(&format!("report.{name}_mean"), usual, unit, label);
        set(
            &format!("report.{name}_delta"),
            against(&column),
            unit,
            average(),
        );
    }
    let own =
        dict(session.get("levels")).ok_or("TypeError: 'NoneType' object is not subscriptable")?;
    let list = |value: Option<&Value>| value.and_then(Value::as_list).unwrap_or(&[]).to_vec();
    let (edges, seconds) = (list(own.get("edges")), list(own.get("seconds")));
    let total = number(Some(&sum(&Value::List(seconds.clone()))?)).unwrap_or(0.0);
    let earlier = match count != 0.0 {
        true => list(comparison.get("levels")),
        false => Vec::new(),
    };
    let edges: Option<Vec<f64>> = edges.iter().map(|edge| number(Some(edge))).collect();
    let edges = edges.ok_or("ValueError: Unknown format code 'g' for object of type 'str'")?;
    for (i, name) in level_names(&edges).into_iter().enumerate() {
        let here = seconds.get(i).filter(|_| total != 0.0);
        let share = here.and_then(|seconds| number(Some(seconds)));
        let share = float(share.map(|seconds| 100.0 * seconds / total));
        set(&format!("report.level.{i}"), share, "%", name.clone());
        let before = earlier.get(i).and_then(|share| number(Some(share)));
        let before = float(before.map(|share| 100.0 * share));
        set(&format!("report.level.{i}.mean"), before, "%", name);
    }
    Ok(out)
}
