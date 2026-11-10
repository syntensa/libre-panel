//! lp-check's cases for lp-config (tests/test_rust_config.py): config.toml texts and files as
//! config.py's parse_config and load_config make a Config of them, each further panel's own
//! Config, and the shortcuts and times of day read alone. A Config is written with nothing
//! lost: every field, in the order config.py has them.

use lp_config::{
    Config, DeviceConfig, Error, HardwareConfig, ModeConfig, PanelConfig, SceneConfig,
    SensorsConfig, SequenceConfig, ServicesConfig, TransitionsConfig, VideoConfig, WeatherConfig,
};
use lp_py::toml::{Table, Toml};
use serde_json::{json, Value};

use crate::toml_value;

fn text(value: &str) -> Value {
    json!(["str", value])
}

fn whole(value: impl ToString) -> Value {
    json!(["int", value.to_string()])
}

fn float(value: f64) -> Value {
    json!(["float", format!("{:016x}", value.to_bits())])
}

fn flag(value: bool) -> Value {
    json!(["bool", value])
}

fn list(items: Vec<Value>) -> Value {
    Value::Array(vec![Value::from("list"), Value::Array(items)])
}

fn texts<S: AsRef<str>>(items: &[S]) -> Value {
    list(items.iter().map(|item| text(item.as_ref())).collect())
}

fn dict<'a, V: 'a>(
    items: impl IntoIterator<Item = (&'a String, &'a V)>,
    value: impl Fn(&V) -> Value,
) -> Value {
    Value::Array(vec![
        Value::from("dict"),
        Value::Array(
            items
                .into_iter()
                .map(|(key, item)| Value::Array(vec![Value::from(key.as_str()), value(item)]))
                .collect(),
        ),
    ])
}

fn names(items: &indexmap::IndexMap<String, String>) -> Value {
    dict(items, |name: &String| text(name))
}

fn tables(items: &indexmap::IndexMap<String, Table>) -> Value {
    dict(items, |table| toml_value(&Toml::Table(table.clone())))
}

fn maybe<T>(value: &Option<T>, written: impl Fn(&T) -> Value) -> Value {
    value.as_ref().map_or(Value::Null, written)
}

/// A dataclass: its fields by name, in their order.
fn object(fields: Vec<(&str, Value)>) -> Value {
    Value::Array(vec![
        Value::from("obj"),
        Value::Array(
            fields
                .into_iter()
                .map(|(name, value)| Value::Array(vec![Value::from(name), value]))
                .collect(),
        ),
    ])
}

fn video(v: &VideoConfig) -> Value {
    object(vec![
        ("mode", text(&v.mode)),
        ("fps", whole(v.fps)),
        ("device_fps", whole(v.device_fps)),
        ("crf", whole(v.crf)),
        ("preset", text(&v.preset)),
        ("keyframe_s", float(v.keyframe_s)),
        ("maxrate", text(&v.maxrate)),
        ("profile", text(&v.profile)),
        ("encoder", text(&v.encoder)),
        ("pixels", text(&v.pixels)),
        ("engine_sends", flag(v.engine_sends)),
        ("engine_layers", flag(v.engine_layers)),
        ("engine_encoder", text(&v.engine_encoder)),
        ("direct", flag(v.direct)),
        ("rest", flag(v.rest)),
        ("ffmpeg", text(&v.ffmpeg)),
        ("local_clip", text(&v.local_clip)),
        ("clip_asked", flag(v.clip_asked)),
    ])
}

fn device(d: &DeviceConfig) -> Value {
    object(vec![
        ("model", text(&d.model)),
        ("driver", text(&d.driver)),
        ("brightness", whole(d.brightness)),
        ("rotate", whole(d.rotate)),
        ("orientation", text(&d.orientation)),
        ("motion", text(&d.motion)),
        ("still", text(&d.still)),
        ("video_range", text(&d.video_range)),
        ("jpeg", text(&d.jpeg)),
        ("jpeg_quality", whole(d.jpeg_quality)),
        ("dark_on_quit", flag(d.dark_on_quit)),
        ("dark_on_sleep", flag(d.dark_on_sleep)),
        ("night", flag(d.night)),
        ("night_from", text(&d.night_from)),
        ("night_to", text(&d.night_to)),
        ("night_brightness", whole(d.night_brightness)),
        ("dark_when_away", flag(d.dark_when_away)),
        ("away_brightness", whole(d.away_brightness)),
        ("output", text(&d.output)),
        ("port", text(&d.port)),
        ("at", maybe(&d.at, |(x, y)| list(vec![whole(x), whole(y)]))),
        ("video", video(&d.video)),
        ("beside", texts(&d.beside)),
    ])
}

fn sensors(s: &SensorsConfig) -> Value {
    object(vec![
        ("providers", texts(&s.providers)),
        ("options", tables(&s.options)),
    ])
}

fn hardware(h: &HardwareConfig) -> Value {
    object(vec![
        ("cpu_name", text(&h.cpu_name)),
        ("gpu_name", text(&h.gpu_name)),
        ("memory_name", text(&h.memory_name)),
        ("fans", names(&h.fans)),
        ("fan_look", text(&h.fan_look)),
        ("fan_paint", text(&h.fan_paint)),
        ("fan_looks", names(&h.fan_looks)),
        ("fan_paints", names(&h.fan_paints)),
    ])
}

fn weather(w: &WeatherConfig) -> Value {
    object(vec![
        ("enabled", flag(w.enabled)),
        ("provider", text(&w.provider)),
        ("latitude", maybe(&w.latitude, |at| float(*at))),
        ("longitude", maybe(&w.longitude, |at| float(*at))),
        ("place", text(&w.place)),
        ("units", text(&w.units)),
        ("update_minutes", whole(&w.update_minutes)),
    ])
}

fn services(s: &ServicesConfig) -> Value {
    object(vec![
        ("enabled", texts(&s.enabled)),
        ("options", tables(&s.options)),
    ])
}

fn mode(m: &ModeConfig) -> Value {
    object(vec![
        ("fps", maybe(&m.fps, |fps| whole(fps))),
        ("theme", maybe(&m.theme, |theme| text(theme))),
        ("on", flag(m.on)),
        ("minutes", maybe(&m.minutes, |minutes| float(*minutes))),
    ])
}

fn transitions(t: &TransitionsConfig) -> Value {
    object(vec![
        ("default", text(&t.default)),
        ("speed", text(&t.speed)),
        ("occasions", names(&t.occasions)),
        ("random", texts(&t.random)),
    ])
}

fn sequence(s: &SequenceConfig) -> Value {
    let steps = s.steps.iter().map(|step| {
        object(vec![
            ("theme", text(&step.theme)),
            ("seconds", whole(step.seconds)),
        ])
    });
    object(vec![("on", flag(s.on)), ("steps", list(steps.collect()))])
}

fn panel(p: &PanelConfig) -> Value {
    object(vec![
        ("theme", text(&p.theme)),
        ("on", flag(p.on)),
        ("device", device(&p.device)),
        ("follow_brightness", flag(p.follow_brightness)),
        ("brightness_offset", whole(p.brightness_offset)),
        ("game_theme", text(&p.game_theme)),
        ("media_theme", text(&p.media_theme)),
        ("alarm_theme", text(&p.alarm_theme)),
        ("same_look", flag(p.same_look)),
        ("follow", maybe(&p.follow, |follow| flag(*follow))),
        ("video_mode", text(&p.video_mode)),
        ("local_clip", text(&p.local_clip)),
        ("clip_asked", flag(p.clip_asked)),
    ])
}

fn scene(s: &SceneConfig) -> Value {
    object(vec![("name", text(&s.name)), ("themes", texts(&s.themes))])
}

fn config(c: &Config) -> Value {
    object(vec![
        ("theme", text(&c.theme)),
        ("language", text(&c.language)),
        ("refresh_ms", maybe(&c.refresh_ms, |ms| whole(ms))),
        ("fps", whole(c.fps)),
        ("transition", text(&c.transition)),
        ("transitions", transitions(&c.transitions)),
        (
            "check_updates",
            maybe(&c.check_updates, |check| flag(*check)),
        ),
        ("allow_notify", flag(c.allow_notify)),
        ("device", device(&c.device)),
        ("sensors", sensors(&c.sensors)),
        ("hardware", hardware(&c.hardware)),
        ("weather", weather(&c.weather)),
        ("services", services(&c.services)),
        ("modes", dict(&c.modes, mode)),
        ("sequence", sequence(&c.sequence)),
        ("panels", list(c.panels.iter().map(panel).collect())),
        ("scenes", list(c.scenes.iter().map(scene).collect())),
        ("scene", text(&c.scene)),
        ("hotkeys", names(&c.hotkeys)),
        ("hotkeys_enabled", flag(c.hotkeys_enabled)),
        ("symbols", text(&c.symbols)),
        ("cards", texts(&c.cards)),
        ("card_order", text(&c.card_order)),
        ("card_transition", text(&c.card_transition)),
        ("accent", text(&c.accent)),
        ("card_opacity", whole(c.card_opacity)),
        ("light_colours", flag(c.light_colours)),
        ("graph_steps", flag(c.graph_steps)),
        ("across", text(&c.across)),
        (
            "switches",
            dict(&c.switches, |theme| dict(theme, |on| flag(*on))),
        ),
        ("same_look", flag(c.same_look)),
        ("follow", maybe(&c.follow, |follow| flag(*follow))),
        (
            "path",
            maybe(&c.path, |path| text(&path.display().to_string())),
        ),
        ("stamp", maybe(&c.stamp, |stamp| whole(stamp))),
        ("theme_given", flag(c.theme_given)),
    ])
}

fn failed(class: &str, msg: &str) -> Value {
    json!({"error": {"type": class, "msg": msg}})
}

/// A Config, and each of its further panels' own; or the error instead.
fn outcome(read: Result<Config, Error>) -> Value {
    match read {
        Ok(read) => {
            let panels: Vec<Value> = (0..read.panels.len())
                .map(|n| read.for_panel(n).map_or(Value::Null, |own| config(&own)))
                .collect();
            let mut answer = serde_json::Map::new();
            answer.insert("ok".into(), config(&read));
            answer.insert("panels".into(), Value::Array(panels));
            Value::Object(answer)
        }
        Err(e) => failed(e.class, &e.msg),
    }
}

/// `texts`: each as `parse_config(tomllib.loads(text))` reads it, `frame` calls deep; `files`:
/// each path as `load_config(path)` reads it, `file_frame` deep; `hotkeys`: each as
/// hotkeys.parse reads it; `times`: each as time_of_day and night_time read it. All of it on
/// `platform` (sys.platform) and in `language`, whose catalog is in `locale`.
pub fn run(case: &Value) -> Result<Value, String> {
    let platform = case["platform"].as_str().unwrap_or("linux");
    let locale = std::path::PathBuf::from(case["locale"].as_str().unwrap_or(""));
    let frame = |key: &str| {
        case[key]
            .as_u64()
            .map_or(lp_py::toml::LOADS_FRAME, |f| f as usize)
    };
    let strings = |key: &str| -> Vec<&str> {
        case[key]
            .as_array()
            .into_iter()
            .flatten()
            .map(|item| item.as_str().unwrap_or_default())
            .collect()
    };
    lp_i18n::set_language(lp_i18n::Language::load(
        &locale,
        case["language"].as_str().unwrap_or("en"),
    )?);

    let results: Vec<Value> = strings("texts")
        .into_iter()
        .map(|text| match lp_py::toml::loads_at(text, frame("frame")) {
            Ok(table) => outcome(lp_config::parse_config(&table, None, platform)),
            Err(e) => failed(
                match e.kind {
                    lp_py::toml::ErrorKind::Decode => "TOMLDecodeError",
                    lp_py::toml::ErrorKind::Value => "ValueError",
                    lp_py::toml::ErrorKind::Recursion => "RecursionError",
                },
                &e.msg,
            ),
        })
        .collect();
    let files: Vec<Value> = strings("files")
        .into_iter()
        .map(|path| {
            outcome(lp_config::load_config_at(
                std::path::Path::new(path),
                platform,
                frame("file_frame"),
            ))
        })
        .collect();
    let hotkeys: Vec<Value> = strings("hotkeys")
        .into_iter()
        .map(|keys| match lp_config::hotkeys::parse(keys) {
            Ok(combo) => json!({"ok": combo.to_string(), "mods": combo.mods, "key": combo.key}),
            Err(e) => failed(e.class, &e.msg),
        })
        .collect();
    let times: Vec<Value> = strings("times")
        .into_iter()
        .map(|time| json!([lp_config::time_of_day(time), lp_config::night_time(time)]))
        .collect();

    lp_i18n::set_language(lp_i18n::Language::english());
    Ok(json!({
        "results": results,
        "files": files,
        "hotkeys": hotkeys,
        "times": times,
        "providers": lp_config::default_providers(platform),
        "defaults": config(&Config::new(None, platform)),
    }))
}

/// A value of a test's change as TOML: whole numbers as ints, others as floats.
fn toml_of(value: &Value) -> lp_py::toml::Toml {
    use lp_py::toml::Toml;
    match value {
        Value::Bool(on) => Toml::Bool(*on),
        Value::Number(number) => match number.as_i64() {
            Some(whole) => Toml::Int(whole.into()),
            None => Toml::Float(number.as_f64().unwrap_or(0.0)),
        },
        Value::String(text) => Toml::Str(text.clone()),
        Value::Array(items) => Toml::Array(items.iter().map(toml_of).collect()),
        Value::Object(table) => Toml::Table(table_of(table)),
        Value::Null => Toml::Date(lp_py::toml::Date {
            year: 2000,
            month: 1,
            day: 1,
        }),
    }
}

fn table_of(table: &serde_json::Map<String, Value>) -> lp_py::toml::Table {
    let pairs = table.iter().map(|(key, item)| (key.clone(), toml_of(item)));
    pairs.collect()
}

fn json_of(value: &lp_py::toml::Toml) -> Value {
    use lp_py::toml::Toml;
    match value {
        Toml::Str(text) => json!(text),
        Toml::Array(items) => items.iter().map(json_of).collect(),
        Toml::Table(table) => table.iter().map(|(k, v)| json!([k, json_of(v)])).collect(),
        other => json!(format!("{other:?}")),
    }
}

/// config.toml changed as config.py's edit_config and scenes.py change it: `files`, each a
/// path and the steps taken on it; after each step, what it gave (or its error) and the
/// file's bytes. `defaults`: default_config_toml on each of these platforms.
pub fn edit(case: &Value) -> Result<Value, String> {
    use lp_config::edit::{self, Change};
    use lp_config::scenes;
    let platform = case["platform"].as_str().unwrap_or("linux");
    let locale = std::path::PathBuf::from(case["locale"].as_str().unwrap_or(""));
    lp_i18n::set_language(lp_i18n::Language::load(
        &locale,
        case["language"].as_str().unwrap_or("en"),
    )?);
    let text = |value: &Value| value.as_str().unwrap_or("").to_string();
    let change = |given: &Value| match given[0].as_str() {
        Some("value") => Change::Value(text(&given[1]), toml_of(&given[2])),
        Some("unset") => Change::Unset(text(&given[1])),
        _ => {
            let tables = given[2].as_array().into_iter().flatten();
            let tables = tables.filter_map(Value::as_object).map(table_of);
            Change::Tables(text(&given[1]), tables.collect())
        }
    };
    let state = |state: scenes::State| {
        let scenes: Vec<Value> = (state.scenes.iter())
            .map(|scene| json_of(&lp_py::toml::Toml::Table(scene.clone())))
            .collect();
        json!({"ok": [scenes, state.on]})
    };
    let mut files = Vec::new();
    for file in case["files"].as_array().into_iter().flatten() {
        let path = std::path::PathBuf::from(text(&file["path"]));
        let mut steps = Vec::new();
        for step in file["steps"].as_array().into_iter().flatten() {
            let name = step[1].as_str().unwrap_or("");
            let done = match step[0].as_str().unwrap_or("") {
                "edit" => {
                    let changes: Vec<Change> =
                        (step[1].as_array().into_iter().flatten().map(change)).collect();
                    edit::edit_config(&changes, &path, platform).map(|()| json!({"ok": null}))
                }
                "theme" => {
                    edit::set_active_theme(name, &path, platform).map(|()| json!({"ok": null}))
                }
                "brightness" => {
                    let percent = step[1].as_i64().unwrap_or(0);
                    edit::set_brightness(percent, &path, platform).map(|()| json!({"ok": null}))
                }
                "state" => scenes::state(&path, platform).map(state),
                "put_on" => scenes::put_on(name, &path, platform).map(state),
                "next" => scenes::next_one(&path, platform).map(|name| json!({"ok": name})),
                "save" => scenes::save(name, &path, platform).map(state),
                "delete" => scenes::delete(name, &path, platform).map(state),
                other => return Err(format!("no step {other}")),
            };
            let done = done.unwrap_or_else(|e| failed(e.class, &e.msg));
            let bytes = std::fs::read(&path).ok();
            let bytes = bytes.map(|b| b.iter().map(|b| format!("{b:02x}")).collect::<String>());
            steps.push(json!([done, bytes]));
        }
        files.push(Value::Array(steps));
    }
    let defaults: Vec<Value> = (case["defaults"].as_array().into_iter().flatten())
        .map(|platform| json!(edit::default_config_toml(platform.as_str().unwrap_or(""))))
        .collect();
    lp_i18n::set_language(lp_i18n::Language::english());
    Ok(json!({"files": files, "defaults": defaults}))
}
