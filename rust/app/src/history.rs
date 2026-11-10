//! What the memory keeps, for the app's History (editor/history.py): the readings of a day, a
//! week or a month as a course, and the rounds of the games played in that time. Only read,
//! from the memory service's database beside its writer (WAL lets both). The minutes are put
//! together in steps that make a course of a few hundred points; a step without a row is time
//! the PC was off, and the course has a gap there.

use std::path::{Path, PathBuf};

use lp_py::toml::{Table, Toml};
use lp_py::{Dict, Value};
use rusqlite::types::ValueRef;
use rusqlite::Connection;

const DAY_S: i64 = 86400;
/// how far back, and the step of the course
const SPANS: [(&str, i64, i64); 3] = [
    ("day", DAY_S, 300),
    ("week", 7 * DAY_S, 3600),
    ("month", 30 * DAY_S, 4 * 3600),
];
const SERIES: [&str; 6] = [
    "cpu_temp",
    "gpu_temp",
    "cpu_load",
    "gpu_load",
    "cpu_power",
    "gpu_power",
];
/// with a highest value per minute
const PEAKED: [&str; 4] = ["cpu_temp", "gpu_temp", "cpu_power", "gpu_power"];
/// the last rounds listed one by one
const ROUNDS: i64 = 8;
/// the games listed, the most played first
const GAMES: i64 = 12;
const DATABASE: &str = "memory.db";

/// database: where the memory service keeps its file (its `database` option, else the
/// default).
pub fn database(config: &lp_config::Config) -> PathBuf {
    let given = match config.services.options.get("memory") {
        Some(table) => match table.get("database") {
            Some(Toml::Str(text)) => text.clone(),
            Some(Toml::Int(n)) if *n != 0.into() => n.to_string(),
            Some(Toml::Bool(true)) => "True".into(),
            _ => String::new(),
        },
        None => String::new(),
    };
    if given.is_empty() {
        return lp_config::dirs::config_dir()
            .join("plugins-data")
            .join("memory")
            .join(DATABASE);
    }
    let places = lp_services::steam::Places::here();
    let homes = lp_services::paths::Homes {
        env: &*places.env,
        user: &lp_services::paths::user_home,
    };
    let path = lp_services::paths::Path::new(lp_services::paths::HERE, &given);
    path.expanduser(&homes)
        .map_or_else(|| PathBuf::from(&given), |p| PathBuf::from(p.text()))
}

/// What SQLite holds as Python reads it.
fn cell(value: ValueRef) -> Value {
    lp_services::memory_store::value(value)
}

/// _round: a number to `digits` places, as a float; None for anything else.
fn rounded(value: &Value, digits: usize) -> Value {
    let number = match value {
        Value::Float(x) => *x,
        Value::Int(n) => lp_py::num::int_to_float(n).unwrap_or(f64::NAN),
        Value::Bool(b) => f64::from(u8::from(*b)),
        _ => return Value::None,
    };
    Value::Float(lp_py::round_digits(number, digits))
}

fn all_rows(con: &Connection, sql: &str, params: &[i64]) -> rusqlite::Result<Vec<Vec<Value>>> {
    let mut asked = con.prepare(sql)?;
    let width = asked.column_count();
    let mut found = Vec::new();
    let mut rows = asked.query(rusqlite::params_from_iter(params))?;
    while let Some(row) = rows.next()? {
        let mut cells = Vec::with_capacity(width);
        for at in 0..width {
            cells.push(cell(row.get_ref(at)?));
        }
        found.push(cells);
    }
    Ok(found)
}

fn course(con: &Connection, since: i64, step: i64) -> rusqlite::Result<Value> {
    let means: Vec<String> = SERIES.iter().map(|name| format!("AVG({name})")).collect();
    let peaks: Vec<String> = PEAKED
        .iter()
        .map(|name| format!("MAX({name}_max)"))
        .collect();
    let sql = format!(
        "SELECT ts / {step} * {step} AS at, {}, {}, SUM(CASE WHEN mode = 'game' THEN 1 ELSE \
         0 END) * 1.0 / COUNT(*) FROM minutes WHERE ts >= ? GROUP BY at ORDER BY at",
        means.join(", "),
        peaks.join(", ")
    );
    let rows = all_rows(con, &sql, &[since])?;
    let column = |i: usize, digits: usize| -> Value {
        Value::List(rows.iter().map(|row| rounded(&row[i], digits)).collect())
    };
    let mut told = Dict::new();
    told.insert(
        "t".into(),
        Value::List(rows.iter().map(|row| row[0].clone()).collect()),
    );
    for (i, name) in SERIES.iter().enumerate() {
        told.insert((*name).to_string(), column(1 + i, 1));
    }
    for (i, name) in PEAKED.iter().enumerate() {
        told.insert(format!("{name}_max"), column(1 + SERIES.len() + i, 1));
    }
    let last = 1 + SERIES.len() + PEAKED.len();
    told.insert("game".into(), column(last, 2));
    Ok(Value::Dict(told))
}

/// `row[n] or 0`
fn or_zero(value: &Value) -> Value {
    match value {
        Value::None => Value::int(0),
        Value::Int(n) if *n == 0.into() => Value::int(0),
        Value::Float(x) if *x == 0.0 => Value::int(0),
        other => other.clone(),
    }
}

fn summary(con: &Connection, since: i64) -> rusqlite::Result<Dict> {
    let rows = all_rows(
        con,
        "SELECT COUNT(*), SUM(CASE WHEN mode = 'game' THEN 1 ELSE 0 END), AVG(cpu_temp), \
         MAX(cpu_temp_max), AVG(gpu_temp), MAX(gpu_temp_max), AVG(cpu_load), AVG(gpu_load), \
         MAX(cpu_power_max), MAX(gpu_power_max), SUM(cpu_power), SUM(gpu_power) FROM minutes \
         WHERE ts >= ?",
        &[since],
    )?;
    let row = rows.into_iter().next().unwrap_or_default();
    let at = |n: usize| row.get(n).cloned().unwrap_or(Value::None);
    let names = [
        "cpu_temp",
        "cpu_temp_max",
        "gpu_temp",
        "gpu_temp_max",
        "cpu_load",
        "gpu_load",
        "cpu_power_max",
        "gpu_power_max",
        "cpu_watt_minutes",
        "gpu_watt_minutes",
    ];
    let mut told = Dict::new();
    told.insert("minutes".into(), or_zero(&at(0)));
    told.insert("game_minutes".into(), or_zero(&at(1)));
    for (n, name) in names.iter().enumerate() {
        told.insert((*name).to_string(), rounded(&at(2 + n), 1));
    }
    Ok(told)
}

/// Rows as dicts by `keys`, those in `raw` as they are, the others rounded.
fn listed(rows: Vec<Vec<Value>>, keys: &[&str], raw: &[&str]) -> Value {
    Value::List(
        rows.into_iter()
            .map(|row| {
                let d: Dict = keys
                    .iter()
                    .zip(row)
                    .map(|(key, value)| {
                        let value = match raw.contains(key) {
                            true => value,
                            false => rounded(&value, 1),
                        };
                        ((*key).to_string(), value)
                    })
                    .collect();
                Value::Dict(d)
            })
            .collect(),
    )
}

fn rounds(con: &Connection, since: i64) -> rusqlite::Result<Value> {
    let rows = all_rows(
        con,
        "SELECT game, started, duration_s, fps_mean, COALESCE(fps_low_mean, fps_p01), \
         cpu_temp_max, gpu_temp_max, id FROM sessions WHERE started >= ? AND duration_s > 0 \
         ORDER BY started DESC LIMIT ?",
        &[since, ROUNDS],
    )?;
    let keys = [
        "game",
        "started",
        "seconds",
        "fps",
        "fps_low",
        "cpu_temp_max",
        "gpu_temp_max",
        "id",
    ];
    Ok(listed(rows, &keys, &["game", "started", "seconds", "id"]))
}

fn games(con: &Connection, since: i64) -> rusqlite::Result<Value> {
    let rows = all_rows(
        con,
        "SELECT game, COUNT(*), SUM(duration_s), SUM(fps_mean * fps_s) / NULLIF(SUM(CASE WHEN \
         fps_mean IS NULL THEN 0 ELSE fps_s END), 0), AVG(COALESCE(fps_low_mean, fps_p01)), \
         MAX(cpu_temp_max), MAX(gpu_temp_max) FROM sessions WHERE started >= ? AND duration_s \
         > 0 AND game IS NOT NULL GROUP BY game ORDER BY SUM(duration_s) DESC LIMIT ?",
        &[since, GAMES],
    )?;
    let keys = [
        "game",
        "rounds",
        "seconds",
        "fps",
        "fps_low",
        "cpu_temp_max",
        "gpu_temp_max",
    ];
    Ok(listed(rows, &keys, &["game", "rounds", "seconds"]))
}

/// float(x) of a setting; None where Python raises.
fn float_of(value: Option<&Toml>) -> Option<f64> {
    match value {
        None => Some(0.0),
        Some(Toml::Bool(b)) => Some(f64::from(u8::from(*b))),
        Some(Toml::Int(n)) => lp_py::num::int_to_float(n).ok(),
        Some(Toml::Float(x)) => Some(*x),
        Some(Toml::Str(s)) if s.is_empty() => Some(0.0),
        Some(Toml::Str(s)) => lp_py::float_of_str(s),
        Some(_) => None,
    }
}

fn number(value: Option<&Value>) -> f64 {
    match value {
        Some(Value::Float(x)) => *x,
        Some(Value::Int(n)) => lp_py::num::int_to_float(n).unwrap_or(0.0),
        _ => 0.0,
    }
}

/// energy: the energy the span's minutes add up to and what it cost, as the power service
/// counts it; None without a price or without minutes.
pub fn energy(summary: &Dict, options: &Table) -> Value {
    let (Some(price), Some(rest)) = (
        float_of(options.get("price")),
        float_of(options.get("rest_w")),
    ) else {
        return Value::None;
    };
    let minutes = number(summary.get("minutes"));
    if price <= 0.0 || minutes == 0.0 {
        return Value::None;
    }
    let watt_minutes =
        number(summary.get("cpu_watt_minutes")) + number(summary.get("gpu_watt_minutes"));
    let kwh = (watt_minutes + rest * minutes) / 60.0 / 1000.0;
    let currency = match options.get("currency") {
        Some(Toml::Str(s)) if !s.is_empty() => s.clone(),
        Some(Toml::Int(n)) if *n != 0.into() => n.to_string(),
        _ => "€".to_string(),
    };
    let mut told = Dict::new();
    told.insert("kwh".into(), Value::Float(lp_py::round_digits(kwh, 2)));
    told.insert(
        "cost".into(),
        Value::Float(lp_py::round_digits(kwh * price, 2)),
    );
    told.insert("price".into(), Value::Float(price));
    told.insert("currency".into(), Value::Str(currency));
    told.insert("rest_w".into(), Value::Float(rest));
    Value::Dict(told)
}

/// Why there is no history to show.
pub enum Refused {
    /// ValueError: the span is none of them
    Span(&'static str),
    /// the database could not be opened, which Python does not catch
    Failed(String),
}

/// history: what the memory kept in the `span` up to `now`; `found` is false where there is
/// no database (yet), and the rest is then empty.
pub fn history(path: &Path, span: &str, now: f64) -> Result<Dict, Refused> {
    let Some((_, length, step)) = SPANS.iter().find(|(name, _, _)| *name == span) else {
        return Err(Refused::Span("span is day, week or month"));
    };
    let since = now as i64 - length;
    let found = path.is_file();
    let mut told = Dict::new();
    told.insert("span".into(), Value::from(span));
    told.insert("since".into(), Value::int(since));
    told.insert("step".into(), Value::int(*step));
    told.insert("found".into(), Value::Bool(found));
    let mut empty = Dict::new();
    empty.insert("t".into(), Value::List(Vec::new()));
    told.insert("course".into(), Value::Dict(empty));
    let mut nothing = Dict::new();
    nothing.insert("minutes".into(), Value::int(0));
    nothing.insert("game_minutes".into(), Value::int(0));
    told.insert("summary".into(), Value::Dict(nothing));
    told.insert("rounds".into(), Value::List(Vec::new()));
    told.insert("games".into(), Value::List(Vec::new()));
    if !found {
        return Ok(told);
    }
    let con = lp_services::memory_store::read_only(path)
        .map_err(|why| Refused::Failed(format!("OperationalError: {}", error_text(&why))))?;
    let mut read = || -> rusqlite::Result<()> {
        told.insert("course".into(), course(&con, since, *step)?);
        told.insert("summary".into(), Value::Dict(summary(&con, since)?));
        told.insert("rounds".into(), rounds(&con, since)?);
        told.insert("games".into(), games(&con, since)?);
        Ok(())
    };
    if let Err(why) = read() {
        // (a database of another make, or one the writer is setting up: nothing to show yet)
        told.insert("error".into(), Value::Str(error_text(&why)));
    }
    Ok(told)
}

/// str(sqlite3.Error): SQLite's own message.
fn error_text(why: &rusqlite::Error) -> String {
    match why {
        rusqlite::Error::SqliteFailure(_, Some(message)) => message.clone(),
        other => other.to_string(),
    }
}
