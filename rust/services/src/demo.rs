//! Deterministic fake sensors for the app's previews and the tests (sensors/demo.py): every
//! reading a theme can show, made up of the time alone, so that a fixed time gives identical
//! frames; with a history for the graphs, a round's report, a round under way and a
//! download, as the services publish them.

use std::f64::consts::PI;

use indexmap::IndexMap;
use lp_py::datetime::DateTime;
use lp_py::zones::{self, Zone};
use lp_py::{Dict, Value};
use lp_render::snapshot::{Reading, Snapshot};
use lp_sensors::frames::{TRACE_LAG_S, TRACE_RATE};
use lp_sensors::lhm::Readings;
use lp_sensors::netlink;

use crate::round_report;

/// somewhere in the middle of Europe, for the sun's times
pub const DEMO_PLACE: (f64, f64) = (50.0, 10.0);
/// a graph of SPUR II's panorama (1713 points) is full in a preview
pub const HISTORY_SAMPLES: usize = 1900;

/// A wave: (key, label, unit, base, amplitude, period in seconds).
type Wave = (&'static str, &'static str, &'static str, f64, f64, f64);

const WAVES: [Wave; 50] = [
    ("cpu.load", "CPU load", "%", 38.0, 30.0, 17.0),
    ("cpu.temp", "CPU temperature", "°C", 58.0, 14.0, 29.0),
    ("cpu.freq", "CPU clock", "MHz", 4200.0, 500.0, 11.0),
    ("cpu.power", "CPU power", "W", 65.0, 30.0, 19.0),
    ("gpu.load", "GPU load", "%", 55.0, 40.0, 23.0),
    ("gpu.temp", "GPU temperature", "°C", 62.0, 12.0, 31.0),
    ("gpu.power", "GPU power", "W", 180.0, 90.0, 21.0),
    ("gpu.freq", "GPU clock", "MHz", 2400.0, 300.0, 17.0),
    ("gpu.mem.load", "GPU memory", "%", 40.0, 15.0, 37.0),
    ("gpu.fan", "GPU fan", "RPM", 1400.0, 400.0, 41.0),
    ("mem.load", "Memory usage", "%", 47.0, 6.0, 43.0),
    ("mem.used", "Memory used", "GiB", 15.0, 2.0, 43.0),
    // (what mem.used leaves of mem.total)
    ("mem.free", "Memory free", "GiB", 17.0, -2.0, 43.0),
    ("mem.total", "Memory total", "GiB", 32.0, 0.0, 1.0),
    ("mem.temp", "Memory temperature", "°C", 46.0, 2.0, 67.0),
    ("mem.speed", "Memory speed", "MT/s", 5600.0, 0.0, 1.0),
    ("swap.load", "Swap usage", "%", 3.0, 2.0, 53.0),
    ("disk.load", "Disk usage", "%", 63.0, 0.0, 1.0),
    ("disk.used", "Disk used", "GiB", 600.0, 0.0, 1.0),
    ("disk.total", "Disk total", "GiB", 953.0, 0.0, 1.0),
    ("disk.read", "Disk read", "B/s", 3e6, 3e6, 13.0),
    ("disk.write", "Disk write", "B/s", 1.5e6, 1.5e6, 7.0),
    ("net.down", "Network download", "B/s", 2.4e6, 2.2e6, 9.0),
    ("net.up", "Network upload", "B/s", 3.1e5, 2.9e5, 12.0),
    ("fan.cpu", "CPU fan", "RPM", 1100.0, 250.0, 27.0),
    ("battery.load", "Battery", "%", 80.0, 0.0, 1.0),
    ("net.ping", "Ping", "ms", 16.0, 5.0, 23.0),
    ("game.fps", "Frames per second", "fps", 141.0, 12.0, 7.0),
    ("game.frametime", "Frame time", "ms", 7.1, 0.6, 7.0),
    ("game.low", "1% low", "fps", 104.0, 4.0, 19.0),
    (
        "game.frametime_peak",
        "Frame time peak",
        "ms",
        9.4,
        1.8,
        7.0,
    ),
    (
        "game.fps_avg",
        "Average FPS of the round",
        "fps",
        139.0,
        1.0,
        61.0,
    ),
    (
        "game.fps_1low",
        "1% low of the round",
        "fps",
        97.0,
        2.0,
        67.0,
    ),
    (
        "game.fps_01low",
        "0.1% low of the round",
        "fps",
        71.0,
        3.0,
        71.0,
    ),
    (
        "game.frametime_avg",
        "Average frame time of the round",
        "ms",
        7.2,
        0.05,
        61.0,
    ),
    (
        "game.frametime_max",
        "Longest frame of the round",
        "ms",
        38.0,
        0.0,
        1.0,
    ),
    ("temp.cpu.ccd1", "CPU CCD1", "°C", 52.0, 12.0, 29.0),
    ("gpu.hotspot", "GPU hot spot", "°C", 71.0, 12.0, 31.0),
    ("temp.nvme.composite", "NVMe SSD", "°C", 41.0, 3.0, 61.0),
    ("temp.board.system", "Motherboard", "°C", 34.0, 2.0, 71.0),
    ("fan.board.case_1", "Case fan 1", "RPM", 820.0, 120.0, 47.0),
    ("fan.board.case_2", "Case fan 2", "RPM", 760.0, 90.0, 53.0),
    ("fan.board.pump", "Pump", "RPM", 2100.0, 150.0, 59.0),
    // as LibreHardwareMonitor names them, around SPUR II's sample values
    (
        "gpu.mem.used",
        "GPU memory used",
        "MB",
        13824.0,
        400.0,
        37.0,
    ),
    ("fan.lpc.fan_1", "Fan #1", "RPM", 820.0, 40.0, 47.0),
    ("fan.lpc.fan_2", "Fan #2", "RPM", 1140.0, 60.0, 41.0),
    ("fan.lpc.fan_3", "Fan #3", "RPM", 900.0, 50.0, 53.0),
    ("fan.lpc.fan_4", "Fan #4", "RPM", 760.0, 40.0, 59.0),
    ("audio.volume", "Volume", "%", 30.0, 0.0, 1.0),
    // (the first of eight cores, each busy in its own rhythm: the others follow)
    ("cpu.core.1.load", "Core 1", "%", 62.0, 30.0, 7.0),
];
/// The other cores: (base, amplitude, period).
const CORES: [(f64, f64, f64); 7] = [
    (35.0, 25.0, 11.0),
    (48.0, 30.0, 13.0),
    (20.0, 15.0, 17.0),
    (71.0, 22.0, 5.0),
    (28.0, 20.0, 19.0),
    (40.0, 35.0, 9.0),
    (15.0, 12.0, 23.0),
];

/// Every wave: its key and label (owned: the cores' are made), unit, base, amplitude and
/// period.
fn waves() -> Vec<(String, String, &'static str, f64, f64, f64)> {
    let mut all: Vec<_> = (WAVES.iter())
        .map(|w| (w.0.to_string(), w.1.to_string(), w.2, w.3, w.4, w.5))
        .collect();
    for (n, (base, amp, period)) in CORES.iter().enumerate() {
        let n = n + 2;
        let (key, label) = (format!("cpu.core.{n}.load"), format!("Core {n}"));
        all.push((key, label, "%", *base, *amp, *period));
    }
    all
}

/// Copies under friendlier names: the reading they repeat (Reading.origin).
const COPIES: [(&str, &str, &str); 5] = [
    ("temp.cpu.package", "cpu.temp", "CPU Package"),
    ("temp.gpu.core", "gpu.temp", "GPU Core"),
    ("temp.gpu.gpu_hot_spot", "gpu.hotspot", "GPU Hot Spot"),
    ("fan.gpu.fan_1", "gpu.fan", "GPU Fan"),
    ("fan.cpu.fan", "fan.cpu", "CPU Fan"),
];
/// name, GiB, used
const DRIVES: [(&str, f64, f64); 3] = [
    ("C:", 953.0, 0.63),
    ("D:", 1863.0, 0.41),
    ("E:", 931.0, 0.87),
];
/// name, CPU %, memory %
const PROGRAMS: [(&str, f64, f64); 8] = [
    ("blender", 18.4, 9.1),
    ("firefox", 6.2, 7.4),
    ("code", 3.1, 4.2),
    ("obs64", 2.6, 2.1),
    ("steam", 1.2, 1.9),
    ("discord", 0.9, 2.6),
    ("explorer", 0.6, 0.8),
    ("python", 0.4, 1.1),
];

fn reading(value: Value, unit: &str, label: &str) -> Reading {
    Reading {
        unit: unit.to_string(),
        label: label.to_string(),
        ..Reading::of(value)
    }
}

fn put(out: &mut Readings, key: &str, value: Value, unit: &str, label: &str) {
    out.insert(key.to_string(), reading(value, unit, label));
}

fn text(value: &str) -> Value {
    Value::Str(value.to_string())
}

fn float(out: &mut Readings, key: &str) -> f64 {
    out.get(key).and_then(|r| r.value.to_f64()).unwrap_or(0.0)
}

/// demo_fps_trace: the game's frame rate finely (`game.fps_trace`), as PresentMon's: 25
/// points a second up to a quarter of a second before `t`, around the demo's `game.fps` with
/// the jitter of real frames and now and then a frame that took long. Each point depends on
/// its own time only, so one reading's course goes on where the last one's ended.
pub fn demo_fps_trace(t: f64, seconds: f64) -> String {
    let (base, amp, period) = (141.0, 12.0, 7.0); // (game.fps)
    let count = lp_py::round(seconds * TRACE_RATE) as i64;
    let last = lp_py::round((t - TRACE_LAG_S) * TRACE_RATE) as i64;
    let points = (last - count + 1..=last).map(|k| {
        let at = k as f64 / TRACE_RATE;
        let mut value = base + amp * (2.0 * PI * at / period).sin();
        value += 2.5 * (at * 7.3).sin() * (at * 2.9).sin() + 1.2 * (at * 23.0).sin();
        // a frame that took long, every seven seconds
        match k.rem_euclid(173) {
            0 => value *= 0.62,
            1 => value *= 0.84,
            _ => {}
        }
        Value::Float(lp_py::round_digits(value, 1))
    });
    compact(&Value::List(points.collect()))
}

/// json.dumps(value, ensure_ascii=False, separators=(",", ":"))
fn compact(value: &Value) -> String {
    let how = lp_py::json::Dumps {
        ensure_ascii: false,
        separators: Some((",".into(), ":".into())),
        ..lp_py::json::Dumps::default()
    };
    lp_py::json::dumps(value, &how)
}

/// demo_details: drives, processes, battery and network details, made up; and what the
/// power service, the timer and the agents service would publish.
fn demo_details(t: f64, out: &mut Readings) {
    for (key, alias, label) in COPIES {
        let Some(source) = out.get(alias).cloned() else {
            continue;
        };
        put(out, key, source.value.clone(), &source.unit, label);
        if let Some(original) = out.get_mut(alias) {
            original.origin = key.to_string();
        }
    }
    for (n, (name, total, used)) in DRIVES.iter().enumerate() {
        let n = n + 1;
        let values = [
            ("name", text(name), ""),
            ("load", Value::Float(used * 100.0), "%"),
            ("used", Value::Float(total * used), "GiB"),
            ("free", Value::Float(total * (1.0 - used)), "GiB"),
            ("total", Value::Float(*total), "GiB"),
        ];
        for (field, value, unit) in values {
            put(out, &format!("disk.{n}.{field}"), value, unit, name);
        }
    }
    let wobble = (2.0 * PI * t / 13.0).sin();
    for (sort, index) in [("cpu", 1), ("mem", 2)] {
        let of = |program: &(&str, f64, f64)| if index == 1 { program.1 } else { program.2 };
        let mut ranked = PROGRAMS.to_vec();
        ranked.sort_by(|a, b| of(b).total_cmp(&of(a)));
        for (n, program) in ranked.iter().enumerate() {
            let n = n + 1;
            let value = lp_py::round_digits(of(program) * (1.0 + 0.15 * wobble), 1);
            put(
                out,
                &format!("proc.{sort}.{n}.name"),
                text(program.0),
                "",
                "",
            );
            put(
                out,
                &format!("proc.{sort}.{n}.value"),
                Value::Float(value),
                "%",
                "",
            );
        }
    }
    put(out, "battery.plugged", Value::int(0), "", "Plugged in");
    put(out, "battery.state", text("On battery"), "", "Battery");
    put(
        out,
        "battery.left",
        Value::int(2 * 3600 + 40 * 60),
        "s",
        "Battery time left",
    );
    put(out, "net.ip", text("192.168.1.20"), "", "IP address");
    // (a good Wi-Fi)
    let link = netlink::Link {
        kind: netlink::WLAN,
        speed: Some(866.7),
        signal: Some(82.0),
        name: String::new(),
    };
    out.extend(netlink::link_readings(Some(&link)));
    put(out, "game.app", text("Starfall"), "", "Game");
    put(out, "game.limiter", text("gpu"), "", "Limited by");
    put(out, "audio.muted", Value::int(0), "", "Muted");
    put(
        out,
        "net.today.down",
        Value::Float(3.42e9),
        "B",
        "Received today",
    );
    put(out, "net.today.up", Value::Float(6.1e8), "B", "Sent today");
    put(out, "net.ping.lost", Value::int(0), "%", "Ping lost");
    put(out, "net.ping.host", text("1.1.1.1"), "", "Ping host");
    put(
        out,
        "audio.mic.muted",
        Value::int(0),
        "",
        "Microphone muted",
    );
    put(
        out,
        "audio.mic.volume",
        Value::Float(80.0),
        "%",
        "Microphone level",
    );
    // the power service's and the timer's, as they would publish them
    let watts = float(out, "cpu.power") + float(out, "gpu.power") + 50.0;
    put(
        out,
        "power.watts",
        Value::Float(lp_py::round_digits(watts, 1)),
        "W",
        "Power",
    );
    for (key, value, unit, label) in [
        ("power.today", 2.84, "kWh", "Energy today"),
        ("power.month", 61.2, "kWh", "Energy this month"),
        ("power.today.cost", 0.85, "€", "Cost today"),
        ("power.month.cost", 18.36, "€", "Cost this month"),
    ] {
        put(out, key, Value::Float(value), unit, label);
    }
    let whole = 25.0 * 60.0;
    let left = whole - t.rem_euclid(whole);
    put(
        out,
        "timer.left",
        Value::Float(lp_py::round_digits(left, 1)),
        "s",
        "Time left",
    );
    let clock = format!(
        "{:02}:{:02}",
        (left / 60.0).floor() as i64,
        left.rem_euclid(60.0) as i64
    );
    put(out, "timer.text", Value::Str(clock), "", "Time left");
    let share = lp_py::round_digits(1.0 - left / whole, 4);
    put(out, "timer.share", Value::Float(share), "", "Time passed");
    put(out, "timer.running", Value::int(1), "", "Timer runs");
    put(out, "timer.phase", text("Focus"), "", "Timer");
    put(out, "timer.rounds", Value::int(2), "", "Rounds");
    // the agents service's: Claude Code at work, Codex waiting for you
    let agents = [
        (
            "claude",
            "Claude Code",
            "working",
            "Edit",
            Some("The hooks of the panel"),
            Some("Opus"),
            23.0,
            41.0,
        ),
        ("codex", "Codex", "waiting", "", None, None, 12.0, 55.0),
    ];
    for (agent, name, state, tool, task, model, five, week) in agents {
        let some = |value: Option<&str>| value.map(text);
        let mut values: IndexMap<&str, Option<Value>> = IndexMap::new();
        values.insert("state", Some(text(state)));
        values.insert(
            "busy",
            Some(Value::int(if state == "waiting" { 2 } else { 1 })),
        );
        values.insert("tool", Some(text(tool)));
        values.insert("task", some(task));
        values.insert("since", Some(Value::Float(95.0)));
        values.insert("sessions", Some(Value::int(1)));
        values.insert("model", some(model));
        values.insert("context", model.map(|_| Value::Float(34.0)));
        values.insert("five_hour", Some(Value::Float(five)));
        values.insert(
            "five_hour_reset",
            Some(Value::Float(2.0 * 3600.0 + 14.0 * 60.0)),
        );
        values.insert("five_hour_age", Some(Value::Float(30.0)));
        values.insert("week", Some(Value::Float(week)));
        values.insert(
            "week_reset",
            Some(Value::Float(3.0 * 86400.0 + 5.0 * 3600.0)),
        );
        values.insert("week_age", Some(Value::Float(30.0)));
        if agent == "claude" {
            // three sessions: one at work, one waiting, one idle; $ today
            values.insert("sessions", Some(Value::int(3)));
            values.insert("cost_today", Some(Value::Float(4.37)));
            values.insert("s1.state", Some(text("working")));
            values.insert("s1.task", some(task));
            values.insert("s1.since", Some(Value::Float(95.0)));
            values.insert("s2.state", Some(text("waiting")));
            values.insert("s2.task", Some(text("Release notes for 0.7.6")));
            values.insert("s2.since", Some(Value::Float(40.0)));
            values.insert("s3.state", Some(text("idle")));
            values.insert("s3.task", Some(text("The video element")));
            values.insert("s3.since", Some(Value::Float(1260.0)));
        }
        for (part, value) in values {
            let Some(value) = value else {
                continue;
            };
            let unit = match part {
                "context" | "five_hour" | "week" => "%",
                "cost" | "cost_today" => "$",
                _ if ["_reset", "_age", "since"]
                    .iter()
                    .any(|end| part.ends_with(end)) =>
                {
                    "s"
                }
                _ => "",
            };
            let label = format!("{name} {}", part.replace('_', " "));
            put(out, &format!("ai.{agent}.{part}"), value, unit, &label);
        }
    }
}

/// demo_media: a song playing (made up).
fn demo_media(t: f64) -> Readings {
    let track = lp_sensors::media::Track {
        state: "playing".into(),
        title: "Midnight Drive".into(),
        artist: "The Night Owls".into(),
        album: "Neon Roads".into(),
        position: Some(83.0 + t.rem_euclid(60.0)),
        duration: Some(227.0),
        cover: None,
        source: "Music".into(),
        at: 0.0,
        app_id: String::new(),
    };
    track.readings(0.0)
}

/// So many hours after `from`.
fn after(from: &DateTime, hours: f64) -> DateTime {
    from.plus((hours * 3600.0 * 1e6).round() as i64)
        .unwrap_or(*from)
}

/// demo_calendar: a few days of made-up appointments around `now`.
fn demo_calendar(now: &DateTime, local: &Zone) -> Readings {
    use lp_sensors::calendar::{calendar_readings, Event};
    let today = now.midnight();
    let event = |title: &str, days: f64, hour: f64, length: f64, all_day: bool, location: &str| {
        let start = after(&today, days * 24.0 + hour);
        Event {
            title: title.to_string(),
            start,
            end: after(&start, length),
            all_day,
            location: location.to_string(),
            calendar: String::new(),
        }
    };
    let events = [
        event(
            "Team meeting",
            0.0,
            f64::from(now.hour) + 1.0,
            1.0,
            false,
            "Room 4",
        ),
        event("Pick up parcel", 0.0, 17.5, 0.5, false, ""),
        event("Dentist", 1.0, 9.5, 1.0, false, "Main Street 12"),
        event("Birthday: Sam", 2.0, 0.0, 24.0, true, ""),
        event("Football", 3.0, 18.0, 2.0, false, ""),
        event("Dinner with friends", 5.0, 19.5, 3.0, false, ""),
    ];
    calendar_readings(&events, now, local)
}

/// A week of autumn: high, low, code, rain.
const DAILY: [(&str, [i64; 7]); 4] = [
    ("temperature_2m_max", [17, 15, 13, 16, 18, 19, 14]),
    ("temperature_2m_min", [9, 8, 6, 7, 10, 11, 8]),
    ("weather_code", [2, 61, 63, 3, 1, 0, 80]),
    ("precipitation_probability_max", [10, 70, 80, 30, 10, 0, 60]),
];
const HOURLY_CODES: [i64; 12] = [0, 0, 1, 1, 2, 2, 3, 3, 61, 61, 3, 2];

fn dict(pairs: Vec<(&str, Value)>) -> Value {
    Value::Dict(
        pairs
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect(),
    )
}

fn ints(values: impl IntoIterator<Item = i64>) -> Value {
    Value::List(values.into_iter().map(Value::int).collect())
}

/// demo_forecast: a made-up forecast around `t`, in the shape Open-Meteo sends.
fn demo_forecast(t: f64, now: &DateTime, fold: bool, local: &Zone) -> Readings {
    let offset = zones::utcoffset(local, now, fold).unwrap_or(0);
    let midnight = now.midnight();
    let step_now = now.since(&midnight).div_euclid(15 * 60 * 1_000_000);
    // dry now, then an hour (a step is the rain of the quarter hour up to its time)
    let rain = (0..192i64).map(|n| match (4..8).contains(&(n - step_now)) {
        true => Value::Float(0.6),
        false => Value::Float(0.0),
    });
    let stamp = |at: DateTime| {
        let (y, m, d, h, min) = (at.year, at.month, at.day, at.hour, at.minute);
        Value::Str(format!("{y:04}-{m:02}-{d:02}T{h:02}:{min:02}"))
    };
    let day = |at: DateTime| Value::Str(format!("{:04}-{:02}-{:02}", at.year, at.month, at.day));
    let hours = 0..48i64;
    let temperature = hours.clone().map(|h| {
        let wave = (2.0 * PI * (h.rem_euclid(24) - 9) as f64 / 24.0).sin();
        Value::Float(lp_py::round_digits(11.0 + 6.0 * wave, 1))
    });
    let hourly = dict(vec![
        (
            "time",
            Value::List(
                hours
                    .clone()
                    .map(|h| stamp(after(&midnight, h as f64)))
                    .collect(),
            ),
        ),
        ("temperature_2m", Value::List(temperature.collect())),
        (
            "weather_code",
            ints(hours.clone().map(|h| HOURLY_CODES[(h % 12) as usize])),
        ),
        (
            "precipitation_probability",
            ints(hours.clone().map(|h| (h * 7) % 60)),
        ),
        (
            "is_day",
            ints(hours.map(|h| i64::from((7..19).contains(&(h % 24))))),
        ),
    ]);
    let mut daily = vec![(
        "time",
        Value::List(
            (0..7)
                .map(|d| day(after(&midnight, f64::from(d) * 24.0)))
                .collect(),
        ),
    )];
    daily.extend(
        DAILY
            .iter()
            .map(|(key, values)| (*key, ints(values.iter().copied()))),
    );
    let quarters = (0..192).map(|n| stamp(after(&midnight, f64::from(n) * 0.25)));
    let data = dict(vec![
        ("utc_offset_seconds", Value::int(offset)),
        ("hourly", hourly),
        (
            "hourly_units",
            dict(vec![
                ("temperature_2m", text("°C")),
                ("precipitation_probability", text("%")),
            ]),
        ),
        ("daily", dict(daily)),
        (
            "minutely_15",
            dict(vec![
                ("time", Value::List(quarters.collect())),
                ("precipitation", Value::List(rain.collect())),
            ]),
        ),
        (
            "daily_units",
            dict(vec![
                ("temperature_2m_max", text("°C")),
                ("temperature_2m_min", text("°C")),
                ("precipitation_probability_max", text("%")),
            ]),
        ),
    ]);
    match data {
        Value::Dict(data) => lp_sensors::weather::forecast_readings(&data, t),
        _ => Readings::new(),
    }
}

/// DemoProvider.read: every reading at `t` (with the weather).
pub fn read(t: f64, local: &Zone) -> Readings {
    let mut out = Readings::new();
    for (key, label, unit, base, amp, period) in waves() {
        let mut value = base + amp * (2.0 * PI * t / period).sin();
        if unit == "%" {
            value = value.clamp(0.0, 100.0);
        }
        put(&mut out, &key, Value::Float(value), unit, &label);
    }
    put(
        &mut out,
        "sys.uptime",
        Value::int(3 * 86400 + 5 * 3600 + 42 * 60),
        "s",
        "Uptime",
    );
    // a round played for an hour and seven minutes, its seconds going on
    let played = 3600 + 7 * 60 + (t.trunc() as i64).rem_euclid(60);
    put(&mut out, "game.time", Value::int(played), "s", "Played");
    demo_details(t, &mut out);
    out.extend(lp_sensors::sky::sky_readings(local, t, Some(DEMO_PLACE)).unwrap_or_default());
    out.extend(demo_media(t));
    let (now, fold) = zones::fromtimestamp(local, t).unwrap_or((DateTime::date(1970, 1, 1), false));
    out.extend(demo_calendar(&now, local));
    let trace = Value::Str(demo_fps_trace(t, 5.0));
    put(
        &mut out,
        "game.fps_trace",
        trace,
        "fps",
        "Frame rate, 25 points a second",
    );
    put(&mut out, "cpu.name", text("8-Core Processor"), "", "CPU");
    put(&mut out, "gpu.name", text("Graphics Card"), "", "GPU");
    put(&mut out, "mem.name", text("DDR5-5600 32 GB"), "", "Memory");
    for (key, label, unit, value) in [
        (
            "weather.temperature",
            "Temperature",
            "°C",
            Value::Float(14.0),
        ),
        (
            "weather.apparent_temperature",
            "Feels like",
            "°C",
            Value::Float(12.0),
        ),
        ("weather.humidity", "Humidity", "%", Value::Float(71.0)),
        ("weather.wind_speed", "Wind", "km/h", Value::Float(18.0)),
        ("weather.code", "Weather code", "", Value::Float(3.0)),
        ("weather.description", "Weather", "", text("Overcast")),
    ] {
        put(&mut out, key, value, unit, label);
    }
    out.extend(demo_forecast(t, &now, fold, local));
    out
}

fn floats(values: &[f64]) -> Value {
    Value::List(values.iter().copied().map(Value::Float).collect())
}

fn told(found: round_report::Told) -> Readings {
    let made = found.into_iter();
    made.map(|(key, (value, unit, label))| (key, reading(value, &unit, &label)))
        .collect()
}

/// demo_course: the temperatures over a round of 45 minutes (made up): the CPU and the GPU
/// warm up in the first minutes, then go up and down with the game, as the memory service
/// keeps them.
pub fn demo_course(points: usize) -> Value {
    let (mut cpu, mut gpu) = (Vec::new(), Vec::new());
    for i in 0..points {
        let i = i as f64;
        let warm = 1.0 - (-i / 9.0).exp();
        let core = 48.0 + 22.0 * warm + 2.5 * (i / 3.1).sin() + 0.8 * (i / 1.3).sin();
        cpu.push(lp_py::round_digits(core, 1));
        gpu.push(lp_py::round_digits(
            41.0 + 13.0 * warm + 1.5 * (i / 5.3 + 1.0).sin(),
            1,
        ));
    }
    dict(vec![
        ("seconds", Value::Float(30.0)),
        ("cpu_temp", floats(&cpu)),
        ("gpu_temp", floats(&gpu)),
    ])
}

/// demo_report: a round report on show (made up), so that the theme the memory service
/// shows after a game is complete in a preview. `earlier` is how many rounds of the game it
/// is set against; with 0 it is the first round, with nothing to compare.
pub fn demo_report(t: f64, earlier: i64) -> Result<Readings, String> {
    let number = |pairs: &[(&str, f64)]| -> Vec<(String, Value)> {
        let pairs = pairs.iter();
        pairs
            .map(|(key, value)| ((*key).to_string(), Value::Float(*value)))
            .collect()
    };
    let mean: Dict = number(&[
        ("fps_mean", 142.1),
        ("fps_p01", 101.2),
        ("fps_low_mean", 99.0),
        ("frametime_mean", 6.9),
        ("cpu_temp_max", 72.4),
        ("gpu_temp_max", 57.1),
        ("gpu_hotspot_max", 78.0),
        ("cpu_power_max", 94.0),
        ("gpu_power_max", 318.0),
        ("vram_used_max", 14.5),
        ("power_max", 452.0),
        ("fps_avg", 142.0),
        ("fps_1low", 94.0),
        ("fps_01low", 66.0),
        ("frametime_avg", 7.0),
        ("frametime_max", 41.0),
    ])
    .into_iter()
    .collect();
    let mut session = Dict::default();
    session.insert("game".into(), text("Starfall"));
    session.insert("started".into(), Value::Float(t - 2760.0));
    session.insert("ended".into(), Value::Float(t - 60.0));
    session.insert("duration_s".into(), Value::int(2700));
    session.extend(number(&[
        ("outside_temp", 12.6),
        ("fps_mean", 148.3),
        ("fps_low_mean", 101.0),
        ("frametime_mean", 6.7),
    ]));
    for (key, seconds) in [
        ("limit_cpu_s", 210),
        ("limit_gpu_s", 2290),
        ("limit_cap_s", 110),
    ] {
        session.insert(key.into(), Value::int(seconds));
    }
    let levels = dict(vec![
        ("edges", ints([60, 90, 120, 144, 165])),
        ("seconds", ints([26, 52, 235, 1150, 1040, 107])),
    ]);
    session.insert("levels".into(), levels);
    session.extend(number(&[
        ("cpu_temp_max", 74.1),
        ("gpu_temp_max", 56.0),
        ("gpu_hotspot_max", 80.0),
        ("cpu_power_max", 96.0),
        ("gpu_power_max", 327.0),
        ("vram_used_max", 14.8),
        ("power_max", 468.0),
        ("fps_avg", 148.0),
        ("fps_1low", 97.0),
        ("fps_01low", 71.0),
        ("frametime_avg", 6.76),
        ("frametime_max", 38.0),
    ]));
    session.insert("course".into(), demo_course(90));
    let comparison = dict(vec![
        ("count", Value::int(earlier)),
        (
            "mean",
            Value::Dict(if earlier != 0 { mean } else { Dict::default() }),
        ),
        (
            "levels",
            match earlier != 0 {
                true => floats(&[0.02, 0.03, 0.12, 0.40, 0.36, 0.07]),
                false => Value::None,
            },
        ),
    ]);
    let mut report = Dict::default();
    report.insert("session".into(), Value::Dict(session));
    report.insert("comparison".into(), comparison);
    round_report::readings(&report).map(told)
}

/// demo_session: a game under way for 1:23:45 (made up), as the memory service publishes
/// the totals of a round while it is played: `session.*`.
pub fn demo_session() -> Readings {
    let peaks = [
        ("cpu_temp", 81.0),
        ("gpu_temp", 71.0),
        ("gpu_hotspot", 89.0),
        ("cpu_power", 118.0),
        ("gpu_power", 318.0),
        ("vram_used", 14.2),
    ];
    let totals = round_report::Totals {
        duration_s: 5025.0,
        fps_mean: Some(156.0),
        edges: vec![60.0, 90.0, 120.0, 144.0, 165.0],
        seconds: vec![50, 55, 20, 700, 2865, 1335],
        peaks: peaks.into_iter().collect(),
    };
    told(round_report::session_readings(&totals))
}

/// demo_downloads: a game that downloads (made up): the state the `downloads` service
/// publishes while one does, with a speed that wanders about 38 MB/s. The progress goes on
/// for a quarter of an hour and begins again.
pub fn demo_downloads(t: f64) -> Readings {
    let mb = 1024.0 * 1024.0;
    // bytes a second, smoothed as the service has it
    let rate = |at: f64| {
        (38.0 + 9.0 * (2.0 * PI * at / 23.0).sin() + 4.0 * (2.0 * PI * at / 7.0).sin()) * mb
    };
    let whole = |value: f64| Value::int(lp_py::round(value) as i64);
    let total = 24.0 * 1024.0 * mb;
    let elapsed = 90.0 + t.rem_euclid(900.0);
    let loaded = total * (0.08 + 0.74 * t.rem_euclid(900.0) / 900.0);
    let history = (1..=60).map(|n| whole(rate(t - 60.0 + f64::from(n))));
    let state = dict(vec![
        ("id", text("demo:1")),
        ("source", text("steam")),
        ("name", text("Space Game")),
        ("phase", text("downloading")),
        ("bytes", whole(loaded)),
        ("total", Value::int(total as i64)),
        ("rate", whole(rate(t))),
        (
            "remaining_s",
            Value::Float(lp_py::round_digits((total - loaded) / rate(t), 1)),
        ),
        ("elapsed_s", Value::Float(elapsed)),
        ("estimated", Value::Bool(false)),
        ("history", Value::List(history.collect())),
        ("more", Value::int(0)),
    ]);
    let mut out = Readings::new();
    put(
        &mut out,
        "downloads",
        Value::Str(compact(&state)),
        "",
        "Downloads",
    );
    out
}

/// demo_history: synthetic history so that graphs are not empty in previews: so many
/// samples, a second apart, up to `t`; incommensurate slow waves plus a little flutter read
/// like real load.
pub fn demo_history(t: f64, wave: (&str, f64, f64, f64), samples: usize) -> Vec<f64> {
    let (unit, base, amp, period) = wave;
    let points = (0..samples).map(|i| {
        let at = t - (samples - 1 - i) as f64 * 1.0;
        let slow = amp * 0.6 * (2.0 * PI * at / (period * 3.7)).sin();
        let fast = amp * 0.35 * (2.0 * PI * at / (period * 1.3) + 1.1).sin();
        let flutter = amp * 0.08 * (at * 0.9).sin() * (at / 3.1).sin();
        let value = base + slow + fast + flutter;
        match unit == "%" {
            true => value.clamp(0.0, 100.0),
            false => value,
        }
    });
    points.collect()
}

/// demo_snapshot: a complete snapshot at `t` with filled graph history, for previews and
/// the app (its pictures apart: `pictures`).
pub fn demo_snapshot(t: f64, samples: usize, local: &Zone) -> Result<Snapshot, String> {
    let mut readings = read(t, local);
    readings.extend(demo_report(t, 7)?);
    readings.extend(demo_session());
    readings.extend(demo_downloads(t));
    let history = waves()
        .into_iter()
        .map(|(key, _, unit, base, amp, period)| {
            (key, demo_history(t, (unit, base, amp, period), samples))
        });
    let (now, fold) = zones::fromtimestamp(local, t).unwrap_or((DateTime::date(1970, 1, 1), false));
    Ok(Snapshot {
        readings,
        history: std::sync::Arc::new(history.collect()),
        now,
        fold,
        images: IndexMap::new(),
    })
}

// -- the pictures -----------------------------------------------------------------------------------

use lp_pixels::{Draw, Filter, Image, Mode, ALL_CORNERS};

fn drawn<T>(done: lp_pixels::Result<T>) -> Result<T, String> {
    done.map_err(|why| why.to_string())
}

/// demo_cover: an album cover for previews: soft light on a dark gradient.
pub fn demo_cover(size: usize) -> Result<Image, String> {
    let side = size as f64;
    let mut cover = Image::new(Mode::RGB, size, size, &[0, 0, 0]);
    {
        let mut draw = Draw::new(&mut cover);
        for y in 0..size {
            let k = y as f64 / side;
            let ink = [
                (40.0 + 90.0 * k) as u8,
                (18.0 + 30.0 * k) as u8,
                (90.0 - 40.0 * k) as u8,
                255,
            ];
            drawn(draw.line(&[(0.0, y as f64), (side, y as f64)], Some(ink), 1, false))?;
        }
    }
    let mut glow = Image::new(Mode::RGB, size, size, &[0, 0, 0]);
    let light = (side * 0.18, side * 0.5, side * 0.82, side * 1.14);
    drawn(Draw::new(&mut glow).ellipse(light, Some([255, 120, 90, 255]), None, 1))?;
    let radius = (side / 10.0) as f32;
    let glow = drawn(glow.gaussian_blur(radius, radius))?;
    let mut cover = drawn(lp_pixels::chops::blend(&cover, &glow, 0.45))?;
    {
        let mut draw = Draw::new(&mut cover);
        let width = 1.max(size / 120) as i32;
        for i in 0..6 {
            let y = side * (0.62 + f64::from(i) * 0.065);
            drawn(draw.line(
                &[(0.0, y), (side, y)],
                Some([255, 200, 170, 255]),
                width,
                false,
            ))?;
        }
    }
    drawn(cover.convert(Mode::RGBA))
}

/// demo_logo: a game's logo for previews, made up: a star over a word on a clear ground, the
/// way Steam's cut-out logos come, in about the proportions of one (drawn three times larger
/// and reduced, so its edges are smooth). `font`: the file of Barlow Bold.
pub fn demo_logo(width: usize, height: usize, font: &std::path::Path) -> Result<Image, String> {
    use lp_text::text::{draw as draw_text, Text};
    let k = 3;
    let (wide, high) = ((width * k) as f64, (height * k) as f64);
    let mut logo = Image::new(Mode::RGBA, width * k, height * k, &[0, 0, 0, 0]);
    let (cx, cy, r) = (wide / 2.0, high * 0.34, high * 0.30);
    // a four-pointed star: points on the axes, waists between them
    let waist = r * 0.28;
    let ways = [
        (0.0, -1.0),
        (0.7, -0.7),
        (1.0, 0.0),
        (0.7, 0.7),
        (0.0, 1.0),
        (-0.7, 0.7),
        (-1.0, 0.0),
        (-0.7, -0.7),
    ];
    let star: Vec<(f64, f64)> = (ways.iter().enumerate())
        .map(|(i, (dx, dy))| {
            let reach = if i % 2 == 0 { r } else { waist };
            (cx + dx * reach, cy + dy * reach)
        })
        .collect();
    let (amber, white) = ([255, 176, 74, 255], [242, 247, 255, 255]);
    {
        let mut draw = Draw::new(&mut logo);
        drawn(draw.polygon(&star, Some(amber), None, 1))?;
        let core = r * 0.2; // a pale centre
        let middle = (cx - core, cy - core, cx + core, cy + core);
        drawn(draw.ellipse(middle, Some([255, 244, 214, 255]), None, 1))?;
    }
    let word = "STARFALL";
    let size = lp_py::round(high * 0.27);
    let face = lp_text::truetype(font, size, 0, "", None).map_err(|why| why.to_string())?;
    let gap = size * 0.12; // letters apart, as a logo sets them
    let mut letter = [0u8; 4];
    let mut lengths = Vec::new();
    for c in word.chars() {
        let length = face.getlength(c.encode_utf8(&mut letter), "", None, None, None);
        lengths.push(length.map_err(|why| why.to_string())?);
    }
    let total = lp_py::sum_floats(lengths.iter().map(|length| length + gap)) - gap;
    let (mut pen, top) = ((wide - total) / 2.0, high * 0.70);
    for (c, length) in word.chars().zip(&lengths) {
        let text = c.encode_utf8(&mut letter);
        let written = Text::new(text, &face, "RGBA");
        draw_text(
            &mut logo,
            (pen, top),
            &written,
            white,
            None,
            Some("lm"),
            "left",
        )
        .map_err(|why| why.to_string())?;
        pen += length + gap;
    }
    let line = high * 0.88; // a thin rule under it
    let rule = (wide * 0.18, line, wide * 0.82, line + (k * 2) as f64);
    drawn(Draw::new(&mut logo).rectangle(rule, Some(amber), None, 1))?;
    drawn(logo.resize(width, height, Filter::Lanczos, None, None))
}

/// demo_app_icon: the icon of a music player for previews, made up: a note on a rounded
/// square that shades from blue to violet (drawn four times larger and reduced, so its edges
/// are smooth), as large as the icons the media source reads.
pub fn demo_app_icon(size: usize) -> Result<Image, String> {
    let big = size * 4;
    let side = big as f64;
    let mut ground = Image::new(Mode::RGBA, big, big, &[0, 0, 0, 0]);
    {
        let mut paint = Draw::new(&mut ground);
        for y in 0..big {
            // blue above, violet below
            let f = y as f64 / (side - 1.0);
            let ink = [
                lp_py::round(86.0 + 56.0 * f) as u8,
                lp_py::round(160.0 - 72.0 * f) as u8,
                250,
                255,
            ];
            drawn(paint.line(&[(0.0, y as f64), (side, y as f64)], Some(ink), 1, false))?;
        }
    }
    let mut mask = Image::new(Mode::L, big, big, &[0]);
    let whole = (0.0, 0.0, side - 1.0, side - 1.0);
    let round = side * 0.22;
    drawn(Draw::new(&mut mask).rounded_rectangle(
        whole,
        round,
        Some([255, 0, 0, 0]),
        None,
        1,
        ALL_CORNERS,
    ))?;
    let mut icon = Image::new(Mode::RGBA, big, big, &[0, 0, 0, 0]);
    drawn(icon.paste(&ground, 0, 0, Some(&mask)))?;
    let white = [244, 248, 255, 255];
    let head = side * 0.085; // two notes joined by a beam
    let mut stems = Vec::new();
    {
        let mut draw = Draw::new(&mut icon);
        for (cx, cy) in [(0.35, 0.69), (0.67, 0.61)] {
            let note = (
                side * cx - head,
                side * cy - head,
                side * cx + head,
                side * cy + head,
            );
            drawn(draw.ellipse(note, Some(white), None, 1))?;
            let stem: f64 = side * cx + head * 0.86;
            stems.push(stem);
            let bar = (
                stem - side * 0.018,
                side * 0.28,
                stem + side * 0.018,
                side * cy,
            );
            drawn(draw.rectangle(bar, Some(white), None, 1))?;
        }
        let beam = [
            (stems[0] - side * 0.018, side * 0.26),
            (stems[1] + side * 0.018, side * 0.18),
            (stems[1] + side * 0.018, side * 0.27),
            (stems[0] - side * 0.018, side * 0.35),
        ];
        drawn(draw.polygon(&beam, Some(white), None, 1))?;
    }
    drawn(icon.resize(size, size, Filter::Lanczos, None, None))
}

/// DemoProvider.images: the pictures a source or service has in a preview: an album cover,
/// the player's icon and a game's logo. `fonts`: the folder of the bundled fonts.
pub fn pictures(fonts: &std::path::Path) -> Result<IndexMap<String, Image>, String> {
    let mut found = IndexMap::new();
    found.insert("media.cover".to_string(), demo_cover(320)?);
    found.insert("media.app_icon".to_string(), demo_app_icon(96)?);
    found.insert(
        "game.logo".to_string(),
        demo_logo(240, 144, &fonts.join("Barlow-Bold.ttf"))?,
    );
    Ok(found)
}
