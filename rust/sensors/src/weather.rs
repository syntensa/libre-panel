//! Weather from Open-Meteo (https://open-meteo.com), as weather/open_meteo.py reads it.
//!
//! No API key needed. Open-Meteo's data is CC BY 4.0 and free for non-commercial use. The
//! place always comes from the user's settings; nothing is sent but the request for it.

use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use lp_py::datetime::{fromisoformat, DateTime, Iso};
use lp_py::zones::{fromtimestamp, Zone};
use lp_py::{float_repr, Dict, Value};
use lp_render::Reading;

use crate::lhm::Readings;

pub const FORECAST_URL: &str = "https://api.open-meteo.com/v1/forecast";

/// Open-Meteo's name of each of the present weather's values, ours and its label.
const CURRENT_FIELDS: [(&str, &str, &str); 6] = [
    ("temperature_2m", "temperature", "Temperature"),
    ("apparent_temperature", "apparent_temperature", "Feels like"),
    ("relative_humidity_2m", "humidity", "Humidity"),
    ("wind_speed_10m", "wind_speed", "Wind"),
    ("weather_code", "code", "Weather code"),
    ("is_day", "is_day", "Daylight"),
];
const HOURLY_FIELDS: [(&str, &str); 4] = [
    ("temperature_2m", "temperature"),
    ("weather_code", "code"),
    ("precipitation_probability", "rain"),
    ("is_day", "day"),
];
const DAILY_FIELDS: [(&str, &str); 4] = [
    ("temperature_2m_max", "high"),
    ("temperature_2m_min", "low"),
    ("weather_code", "code"),
    ("precipitation_probability_max", "rain"),
];
/// weather.hour.1 ... weather.hour.24: the next hours
pub const HOURS: i64 = 24;
/// weather.day.0 (today) ... weather.day.6
pub const DAYS: i64 = 7;
/// the steps of Open-Meteo's minutely_15 answer, in microseconds
const RAIN_STEP: i64 = 15 * 60 * 1_000_000;
/// the steps after the current one: the next two hours
const RAIN_STEPS: i64 = 8;
/// a step with at least this much precipitation is rain
const RAIN_MM: f64 = 0.1;

/// The WMO's weather interpretation codes as Open-Meteo uses them.
const WMO: [(i64, &str); 28] = [
    (0, "Clear sky"),
    (1, "Mainly clear"),
    (2, "Partly cloudy"),
    (3, "Overcast"),
    (45, "Fog"),
    (48, "Rime fog"),
    (51, "Light drizzle"),
    (53, "Drizzle"),
    (55, "Dense drizzle"),
    (56, "Freezing drizzle"),
    (57, "Freezing drizzle"),
    (61, "Light rain"),
    (63, "Rain"),
    (65, "Heavy rain"),
    (66, "Freezing rain"),
    (67, "Freezing rain"),
    (71, "Light snow"),
    (73, "Snow"),
    (75, "Heavy snow"),
    (77, "Snow grains"),
    (80, "Light showers"),
    (81, "Showers"),
    (82, "Heavy showers"),
    (85, "Snow showers"),
    (86, "Snow showers"),
    (95, "Thunderstorm"),
    (96, "Thunderstorm, hail"),
    (99, "Thunderstorm, hail"),
];

/// describe_weather_code: a code in words ("" for none, "Unknown" for one the table lacks).
pub fn describe_weather_code(code: &Value) -> String {
    let whole = match code {
        Value::None => return String::new(),
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Int(n) => i64::try_from(n).ok(),
        // (int() of a float cuts its fraction off)
        Value::Float(f) if f.is_finite() && f.abs() < 9e15 => Some(f.trunc() as i64),
        Value::Str(s) => lp_py::int_of_str(s).and_then(|n| i64::try_from(n).ok()),
        _ => None,
    };
    let found = whole.and_then(|code| WMO.iter().find(|(known, _)| *known == code));
    found.map_or("Unknown", |(_, text)| text).to_string()
}

/// urllib.parse.quote_plus of `text`.
pub fn quote_plus(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => {
                out.push(char::from(byte));
            }
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// The address of the forecast for a place, in metric units or (`imperial`) Fahrenheit and
/// miles an hour (build_params, urlencoded).
pub fn forecast_url(base: &str, latitude: f64, longitude: f64, imperial: bool) -> String {
    let names = |fields: &[&str]| fields.join(",");
    let current: Vec<&str> = CURRENT_FIELDS.iter().map(|field| field.0).collect();
    let hourly: Vec<&str> = HOURLY_FIELDS.iter().map(|field| field.0).collect();
    let daily: Vec<&str> = DAILY_FIELDS.iter().map(|field| field.0).collect();
    let mut params = vec![
        ("latitude", float_repr(latitude)),
        ("longitude", float_repr(longitude)),
        ("current", names(&current)),
        ("hourly", names(&hourly)),
        ("daily", names(&daily)),
        // (the rain of the next hours, in 15-minute steps)
        ("minutely_15", "precipitation".to_string()),
        ("forecast_days", DAYS.to_string()),
        ("timezone", "auto".to_string()),
    ];
    if imperial {
        params.push(("temperature_unit", "fahrenheit".to_string()));
        params.push(("wind_speed_unit", "mph".to_string()));
    }
    let query: Vec<String> = params
        .iter()
        .map(|(name, value)| format!("{name}={}", quote_plus(value)))
        .collect();
    format!("{base}?{}", query.join("&"))
}

fn reading(value: Value, unit: &str, label: &str) -> Reading {
    Reading {
        unit: unit.into(),
        label: label.into(),
        ..Reading::of(value)
    }
}

/// `data.get(key) or {}`.
fn table<'a>(data: &'a Dict, key: &str) -> Option<&'a Dict> {
    data.get(key).and_then(Value::as_dict)
}

/// `table.get(key) or []`.
fn list<'a>(table: Option<&'a Dict>, key: &str) -> &'a [Value] {
    table
        .and_then(|table| table.get(key))
        .and_then(Value::as_list)
        .unwrap_or(&[])
}

/// `units.get(key, "")`.
fn unit_of(units: Option<&Dict>, key: &str) -> String {
    match units.and_then(|units| units.get(key)) {
        Some(Value::Str(text)) => text.clone(),
        Some(other) => other.str(),
        None => String::new(),
    }
}

/// parse_current: the present weather of an answer.
pub fn parse_current(data: &Dict) -> Readings {
    let mut out = Readings::new();
    let units = table(data, "current_units");
    let Some(current) = table(data, "current") else {
        return out;
    };
    for (api_key, name, label) in CURRENT_FIELDS {
        let Some(value) = current.get(api_key) else {
            continue;
        };
        let unit = match name {
            "code" => String::new(),
            _ => unit_of(units, api_key),
        };
        out.insert(
            format!("weather.{name}"),
            reading(value.clone(), &unit, label),
        );
    }
    if let Some(code) = out.get("weather.code") {
        let text = describe_weather_code(&code.value);
        out.insert(
            "weather.description".into(),
            reading(Value::Str(text), "", "Weather"),
        );
    }
    out
}

/// A time without a zone, as the forecast's times are.
fn naive(text: Option<&Value>) -> Option<DateTime> {
    match fromisoformat(text?.as_str()?) {
        Iso::Naive(at) => Some(at),
        _ => None,
    }
}

/// forecast_readings: the forecast as readings relative to `now` (a Unix time).
///
/// `weather.hour.<n>.temperature|code|rain|day|time` for the next hours (1 = the coming full
/// hour) and `weather.day.<n>.high|low|code|rain|day|name` for today (0) and the next days,
/// and the rain of the next two hours. Times and the days' names are local to the weather's
/// place; the names follow the language.
pub fn forecast_readings(data: &Dict, now: f64) -> Readings {
    let mut out = Readings::new();
    let offset = match data.get("utc_offset_seconds") {
        Some(Value::Int(n)) => i64::try_from(n).unwrap_or(0),
        Some(Value::Float(f)) if f.is_finite() => f.trunc() as i64,
        Some(Value::Bool(b)) => i64::from(*b),
        _ => 0,
    };
    let local_now = fromtimestamp(&Zone::utc(), now)
        .and_then(|(at, _)| at.plus(offset.checked_mul(1_000_000)?));
    let Some(local_now) = local_now else {
        return out;
    };

    let hourly = table(data, "hourly");
    let hourly_units = table(data, "hourly_units");
    let times = list(hourly, "time");
    if let Some(first) = naive(times.first()) {
        let current = local_now.since(&first).div_euclid(3_600_000_000);
        for n in 1..=HOURS {
            let index = current + n;
            if index < 0 || index >= times.len() as i64 {
                break;
            }
            let Some(moment) = first.plus(index * 3_600_000_000) else {
                break;
            };
            let label = format!("+{n} h");
            for (api_key, name) in HOURLY_FIELDS {
                let value = list(hourly, api_key).get(index as usize);
                if let Some(value) = value.filter(|value| !matches!(value, Value::None)) {
                    let unit = match name {
                        "temperature" | "rain" => unit_of(hourly_units, api_key),
                        _ => String::new(),
                    };
                    out.insert(
                        format!("weather.hour.{n}.{name}"),
                        reading(value.clone(), &unit, &label),
                    );
                }
            }
            let time = format!("{:02}:{:02}", moment.hour, moment.minute);
            out.insert(
                format!("weather.hour.{n}.time"),
                reading(Value::Str(time), "", &label),
            );
        }
    }

    let daily = table(data, "daily");
    let daily_units = table(data, "daily_units");
    let today = local_now.toordinal();
    for (index, day) in list(daily, "time").iter().enumerate() {
        let Some(date) = naive(Some(day)).map(|at| at.midnight()) else {
            continue;
        };
        let label = day.as_str().unwrap_or("");
        let n = date.toordinal() - today;
        if !(0..DAYS).contains(&n) {
            continue;
        }
        for (api_key, name) in DAILY_FIELDS {
            let value = list(daily, api_key).get(index);
            if let Some(value) = value.filter(|value| !matches!(value, Value::None)) {
                let unit = match name {
                    "code" => String::new(),
                    _ => unit_of(daily_units, api_key),
                };
                out.insert(
                    format!("weather.day.{n}.{name}"),
                    reading(value.clone(), &unit, label),
                );
            }
        }
        out.insert(
            format!("weather.day.{n}.day"),
            reading(Value::int(1), "", label),
        );
        let name = match n {
            0 => lp_i18n::t("Today"),
            _ => lp_i18n::format_date(&date, "%a").unwrap_or_default(),
        };
        out.insert(
            format!("weather.day.{n}.name"),
            reading(Value::Str(name), "", label),
        );
    }
    out.extend(rain_readings(data, &local_now));
    out
}

/// _rain_readings: the rain of the next two hours, from the 15-minute precipitation.
///
/// `weather.rain.minutes` counts to the moment the rain starts (dry now) or stops (raining
/// now); it is missing when neither happens in those two hours. `weather.rain.text` says it in
/// words, and also when the rain stays or stays away. Nothing is said while the answer ends
/// before the two hours do.
///
/// A step is the rain of the 15 minutes up to its time (Open-Meteo: "preceding 15 minutes
/// sum"): the step of 14:45 is the rain from 14:30 to 14:45.
fn rain_readings(data: &Dict, local_now: &DateTime) -> Readings {
    let mut out = Readings::new();
    let minutely = table(data, "minutely_15");
    let times = list(minutely, "time");
    let amounts = list(minutely, "precipitation");
    let Some(first) = naive(times.first()) else {
        return out;
    };
    // the step that holds the present moment: the first whose time is not past
    let now_index = -first.since(local_now).div_euclid(RAIN_STEP);
    let end = (now_index + RAIN_STEPS + 1)
        .min(times.len() as i64)
        .min(amounts.len() as i64);
    if now_index < 0 || now_index >= end {
        return out;
    }
    let wet: Vec<bool> = (now_index..end)
        .map(|i| amounts[i as usize].to_f64().is_some_and(|mm| mm >= RAIN_MM))
        .collect();
    let raining = wet[0];
    let change = (1..wet.len()).find(|&i| wet[i] != raining);
    let text = match change {
        // nothing changes yet, and the answer ends before the two hours do
        None if wet.len() as i64 <= RAIN_STEPS => return out,
        Some(change) => {
            // where the changed step begins
            let steps = now_index + change as i64 - 1;
            let Some(moment) = first.plus(RAIN_STEP * steps) else {
                return out;
            };
            let minutes = -(-moment.since(local_now)).div_euclid(60_000_000);
            out.insert(
                "weather.rain.minutes".into(),
                reading(Value::int(minutes), "min", "Rain starts or stops"),
            );
            let words = match raining {
                true => "Rain stops in {minutes} min",
                false => "Rain in {minutes} min",
            };
            lp_i18n::t_format(words, &[("minutes", Value::int(minutes))]).unwrap_or_default()
        }
        None if raining => lp_i18n::t("Rain for the next 2 h"),
        None => lp_i18n::t("No rain for the next 2 h"),
    };
    out.insert(
        "weather.rain.text".into(),
        reading(Value::Str(text), "", "Rain"),
    );
    out
}

/// Open-Meteo's search of places by their names.
pub const GEOCODING_URL: &str = "https://geocoding-api.open-meteo.com/v1/search";

/// A place of the search: its name, region and country, and where it is.
#[derive(Clone, Debug, PartialEq)]
pub struct Place {
    pub name: String,
    pub region: String,
    pub country: String,
    pub latitude: Value,
    pub longitude: Value,
}

/// geocode: places of that name (open_meteo.geocode); why there are none where the search
/// could not be reached.
pub fn geocode(
    name: &str,
    count: usize,
    language: &str,
    agent: &str,
) -> Result<Vec<Place>, String> {
    // (the tests' own: a search of their own)
    let base = std::env::var("LP_GEOCODING_URL").unwrap_or_else(|_| GEOCODING_URL.to_string());
    let url = format!(
        "{base}?name={}&count={count}&language={}&format=json",
        quote_plus(name),
        quote_plus(language)
    );
    let data = lp_net::get(&url, &[("User-Agent", agent)], 10.0, 16 << 20)
        .map_err(|why| why.to_string())?;
    let data = String::from_utf8(data).map_err(|why| why.to_string())?;
    // (an answer that is nothing, an empty list among them, names no place)
    let answer = match lp_py::json::loads(&data).map_err(|why| why.to_string())? {
        Value::Dict(answer) => answer,
        other if !other.truthy() => Dict::new(),
        other => {
            return Err(format!(
                "the answer is no table but a {}",
                other.type_name()
            ))
        }
    };
    let text = |found: &Dict, key: &str| match found.get(key) {
        Some(Value::Str(text)) => text.clone(),
        Some(other) => lp_py::format::str_of(other),
        None => String::new(),
    };
    let results = match answer.get("results") {
        Some(Value::List(results)) => results.clone(),
        _ => Vec::new(),
    };
    Ok(results
        .iter()
        .filter_map(|found| match found {
            Value::Dict(found) => Some(Place {
                name: text(found, "name"),
                region: text(found, "admin1"),
                country: text(found, "country"),
                latitude: found.get("latitude").cloned().unwrap_or(Value::None),
                longitude: found.get("longitude").cloned().unwrap_or(Value::None),
            }),
            _ => None,
        })
        .collect())
}

/// The answer at `url`, as JSON: a dict, or why there is none.
fn fetch(url: &str, agent: &str) -> Result<Dict, String> {
    let data = lp_net::get(url, &[("User-Agent", agent)], 10.0, 16 << 20)
        .map_err(|why| why.to_string())?;
    let text = String::from_utf8(data).map_err(|why| why.to_string())?;
    match lp_py::json::loads(&text) {
        Ok(Value::Dict(answer)) => Ok(answer),
        Ok(other) => Err(format!(
            "the answer is no table but a {}",
            other.type_name()
        )),
        Err(why) => Err(why.to_string()),
    }
}

#[derive(Default)]
struct State {
    stop: bool,
    /// how many times the forecast was asked for, with an answer or without
    rounds: u64,
    latest: Readings,
    /// the last answer, for the forecast
    data: Option<Arc<Dict>>,
    notes: Vec<String>,
}

struct Shared {
    state: Mutex<State>,
    changed: Condvar,
}

fn run(shared: &Shared, url: &str, agent: &str, interval: Duration) {
    loop {
        let found = fetch(url, agent);
        let Ok(mut state) = shared.state.lock() else {
            return;
        };
        let wait = match found {
            Ok(data) => {
                state.latest = parse_current(&data);
                state.data = Some(Arc::new(data));
                interval
            }
            Err(why) => {
                state.notes.push(format!("weather update failed: {why}"));
                Duration::from_secs(120) // retry sooner after an error
            }
        };
        state.rounds += 1;
        shared.changed.notify_all();
        let until = Instant::now() + wait;
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

/// Fetches the weather in the background; `read` never waits (OpenMeteoProvider).
pub struct WeatherSource {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl WeatherSource {
    /// The weather of a place from `base` (Open-Meteo's forecast), asked for every
    /// `update_minutes` (five at least) by a program that names itself `agent`.
    pub fn new(
        base: &str,
        latitude: f64,
        longitude: f64,
        imperial: bool,
        update_minutes: u64,
        agent: &str,
    ) -> WeatherSource {
        let shared = Arc::new(Shared {
            state: Mutex::new(State::default()),
            changed: Condvar::new(),
        });
        let url = forecast_url(base, latitude, longitude, imperial);
        let interval = Duration::from_secs(update_minutes.clamp(5, 1 << 40) * 60);
        let (theirs, agent) = (Arc::clone(&shared), agent.to_string());
        let thread = std::thread::Builder::new()
            .name("weather".into())
            .spawn(move || run(&theirs, &url, &agent, interval))
            .ok();
        WeatherSource { shared, thread }
    }

    pub fn read(&mut self) -> Readings {
        let Ok(state) = self.shared.state.lock() else {
            return Readings::new();
        };
        let mut out = state.latest.clone();
        let data = state.data.clone();
        drop(state);
        if let Some(data) = data.filter(|data| !data.is_empty()) {
            // relative to now, so "in 1 hour" stays true between updates
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0.0, |since| since.as_secs_f64());
            out.extend(forecast_readings(&data, now));
        }
        out
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

impl Drop for WeatherSource {
    fn drop(&mut self) {
        self.close();
    }
}
