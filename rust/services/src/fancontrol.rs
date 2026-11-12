//! FanControl's configuration, read only: its curves, the fans they drive and the
//! temperature each curve reads (services/fancontrol.py).
//!
//! FanControl (Windows) keeps all of it in one JSON file. The cooling analysis compares what
//! the fans really did with what the curves say they should do, so it has to know the
//! curves; the memory notes when they change, so that the analysis looks at the data since
//! the last change. Libre Panel only ever opens the file for reading: how the fans run is the
//! user's decision.

use std::cmp::Ordering;
use std::time::SystemTime;

use indexmap::IndexMap;
use lp_py::regex::Regex;
use lp_py::{Dict, Value};

use crate::paths::{self, Path, HERE};
use crate::steam::{is_file, Places};

pub const CONFIG_NAME: &str = "userConfig.json";
/// how often the file's modification time is looked at
pub const CHECK_S: f64 = 30.0;
/// What of a fan output decides how it cools. Its window position, whether it is hidden and
/// the like do not: or every move of the window would look like a new setting.
const CONTROL_FIELDS: [&str; 10] = [
    "NickName",
    "Identifier",
    "Enable",
    "SelectedFanCurve",
    "SelectedOffset",
    "SelectedStart",
    "SelectedStop",
    "MinimumPercent",
    "SelectedCommandStepUp",
    "SelectedCommandStepDown",
];

/// default_paths: where FanControl keeps its configuration unless told otherwise: next to the
/// program (installed for everyone, or in the user's folders) or in Documents.
pub fn default_paths(env: &dyn Fn(&str) -> Option<String>, home: &Path) -> Vec<Path> {
    let set = |name: &str| env(name).filter(|value| !value.is_empty());
    let mut folders: Vec<Path> = ["ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"]
        .iter()
        .filter_map(|name| set(name))
        .map(|folder| Path::new(HERE, &folder).join("FanControl"))
        .collect();
    folders.push(home.join("Documents").join("FanControl"));
    let file = |folder: Path| folder.join("Configurations").join(CONFIG_NAME);
    folders.into_iter().map(file).collect()
}

/// find: the configuration file: `given` (the user's setting), else the first usual place
/// that has one. None where FanControl is not installed; an error where Python raises.
pub fn find(given: &str, places: &Places) -> Result<Option<Path>, String> {
    if !given.is_empty() {
        let homes = paths::Homes {
            env: &*places.env,
            user: &paths::user_home,
        };
        let path = (Path::new(HERE, given).expanduser(&homes))
            .ok_or("RuntimeError: Could not determine home directory.")?;
        return Ok(Some(path).filter(is_file));
    }
    let home = crate::downloads::home(places);
    Ok((default_paths(&*places.env, &home).into_iter()).find(is_file))
}

/// The `FanControl` part of a file with these bytes; see `read_section`.
pub fn section_of(bytes: &[u8]) -> Option<Dict> {
    let text = lp_py::codecs::decode_utf8(bytes).ok()?;
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    match lp_py::json::loads(text).ok()? {
        Value::Dict(mut whole) => match whole.shift_remove("FanControl")? {
            Value::Dict(section) => Some(section),
            _ => None,
        },
        _ => None,
    }
}

/// read_section: the `FanControl` part of the file, or None when it cannot be read: it is
/// gone, FanControl is writing it just now, or it is not what we expect.
pub fn read_section(path: &Path) -> Option<Dict> {
    section_of(&std::fs::read(path.text()).ok()?)
}

fn dicts(value: Option<&Value>) -> Vec<&Dict> {
    let items = value.and_then(Value::as_list).unwrap_or(&[]);
    items.iter().filter_map(Value::as_dict).collect()
}

/// state_of: what of the configuration decides the cooling: the curves and the fan outputs.
pub fn state_of(section: &Dict) -> Dict {
    let curves = dicts(section.get("FanCurves")).into_iter();
    let controls = dicts(section.get("Controls")).into_iter().map(|control| {
        let fields = CONTROL_FIELDS.iter().map(|name| {
            let value = control.get(*name).cloned().unwrap_or(Value::None);
            ((*name).to_string(), value)
        });
        Value::Dict(fields.collect())
    });
    let mut state = Dict::default();
    let curves = curves.map(|curve| Value::Dict(curve.clone())).collect();
    state.insert("curves".into(), Value::List(curves));
    state.insert("controls".into(), Value::List(controls.collect()));
    state
}

/// SHA-1 of some bytes (FIPS 180-4), as hex.
fn sha1_hex(data: &[u8]) -> String {
    let mut state: [u32; 5] = [
        0x6745_2301,
        0xEFCD_AB89,
        0x98BA_DCFE,
        0x1032_5476,
        0xC3D2_E1F0,
    ];
    let mut padded = data.to_vec();
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&(data.len() as u64).wrapping_mul(8).to_be_bytes());
    for block in padded.chunks_exact(64) {
        let mut w = [0u32; 80];
        for (word, bytes) in w.iter_mut().zip(block.chunks_exact(4)) {
            *word = u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let [mut a, mut b, mut c, mut d, mut e] = state;
        for (i, word) in w.iter().enumerate() {
            let (f, k) = match i / 20 {
                0 => ((b & c) | (!b & d), 0x5A82_7999),
                1 => (b ^ c ^ d, 0x6ED9_EBA1),
                2 => ((b & c) | (b & d) | (c & d), 0x8F1B_BCDC),
                _ => (b ^ c ^ d, 0xCA62_C1D6u32),
            };
            let next = (a.rotate_left(5))
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(*word);
            (e, d, c, b, a) = (d, c, b.rotate_left(30), a, next);
        }
        for (sum, add) in state.iter_mut().zip([a, b, c, d, e]) {
            *sum = sum.wrapping_add(add);
        }
    }
    state.iter().map(|word| format!("{word:08x}")).collect()
}

/// The text a state is kept and marked as: `json.dumps(state, sort_keys=True,
/// ensure_ascii=False)`.
pub fn state_text(state: &Dict) -> String {
    let how = lp_py::json::Dumps {
        sort_keys: true,
        ensure_ascii: false,
        ..Default::default()
    };
    lp_py::json::dumps(&Value::Dict(state.clone()), &how)
}

/// fingerprint: a short mark of a state: the same cooling settings, the same mark.
pub fn fingerprint(state: &Dict) -> String {
    sha1_hex(state_text(state).as_bytes())[..16].to_string()
}

// -- curves and fans ------------------------------------------------------------------------

/// One fan curve: duty (percent) over temperature, read from a sensor, or a mix of curves.
#[derive(Clone, Debug, PartialEq)]
pub struct Curve {
    pub name: String,
    /// (°C, duty %), by temperature
    pub points: Vec<(f64, f64)>,
    /// the sensor it reads (LibreHardwareMonitor's id), None for a mix
    pub source: Option<String>,
    /// the curves a mix takes together
    pub mix: Vec<String>,
    pub hysteresis: Dict,
}

impl Curve {
    /// device: what the curve cools by: `cpu`, `gpu`, `mix` (other curves together) or
    /// `other` (some other sensor). LibreHardwareMonitor's ids start with the hardware.
    pub fn device(&self) -> &'static str {
        let source = self.source.as_deref().unwrap_or("");
        if !self.mix.is_empty() {
            "mix"
        } else if source.starts_with("/amdcpu") || source.starts_with("/intelcpu") {
            "cpu"
        } else if source.starts_with("/gpu") {
            "gpu"
        } else {
            "other"
        }
    }

    /// duty_at: the duty the curve commands at this temperature (percent).
    pub fn duty_at(&self, temp: f64) -> f64 {
        let points = &self.points;
        let (Some(first), Some(last)) = (points.first(), points.last()) else {
            return 0.0;
        };
        if temp <= first.0 {
            return first.1;
        }
        for pair in points.windows(2) {
            let ((t0, d0), (t1, d1)) = (pair[0], pair[1]);
            if temp <= t1 {
                return if t1 > t0 {
                    d0 + (d1 - d0) * (temp - t0) / (t1 - t0)
                } else {
                    d1
                };
            }
        }
        last.1
    }

    /// temp_at: the temperature a curve must have seen to command this duty: the other way
    /// round from `duty_at`. Only the rising part tells; on the flat start or end there is no
    /// single temperature, and the answer is None.
    pub fn temp_at(&self, duty: f64) -> Option<f64> {
        let points = &self.points;
        let (first, last) = (points.first()?, points.last()?);
        if points.len() < 2 || duty <= first.1 + 1e-9 || duty >= last.1 - 1e-9 {
            return None;
        }
        points.windows(2).find_map(|pair| {
            let ((t0, d0), (t1, d1)) = (pair[0], pair[1]);
            (d0 <= duty && duty <= d1 && d1 > d0).then(|| t0 + (t1 - t0) * (duty - d0) / (d1 - d0))
        })
    }
}

/// A fan output of FanControl and the curve that drives it.
#[derive(Clone, Debug, PartialEq)]
pub struct Fan {
    /// the name the user gave it
    pub name: String,
    pub curve: String,
    /// the lowest duty FanControl gives it
    pub minimum: Option<f64>,
    /// the id of the sensor that measures its speed
    pub sensor: Option<String>,
}

fn number(value: Option<&Value>) -> Option<f64> {
    match value? {
        value @ (Value::Int(_) | Value::Float(_)) => value.to_f64(),
        _ => None,
    }
}

fn points(raw: Option<&Value>) -> Vec<(f64, f64)> {
    let mut found = Vec::new();
    for item in raw.and_then(Value::as_list).unwrap_or(&[]) {
        let text = item.str();
        let (temp, duty) = text.split_once(',').unwrap_or((&text, ""));
        // (a point that is no pair is left out, the others still tell)
        if let (Some(temp), Some(duty)) = (lp_py::float_of_str(temp), lp_py::float_of_str(duty)) {
            found.push((temp, duty));
        }
    }
    found.sort_by(|a, b| {
        let first = a.0.partial_cmp(&b.0).unwrap_or(Ordering::Equal);
        first.then(a.1.partial_cmp(&b.1).unwrap_or(Ordering::Equal))
    });
    found
}

/// fan_sensor: the id of the sensor that measures the speed of the fan an output drives:
/// LibreHardwareMonitor names both alike, `/lpc/chip/control/1` and `/lpc/chip/fan/1`.
pub fn fan_sensor(control_id: Option<&str>) -> Option<String> {
    let control_id = control_id.filter(|id| !id.is_empty())?;
    let pattern = Regex::new(r"/control/(\d+)$", false).ok()?;
    let chars: Vec<char> = control_id.chars().collect();
    let found = pattern.search(&chars)?;
    let before: String = chars[..found.start()].iter().collect();
    Some(format!("{before}/fan/{}", found.group(&chars, 1)?))
}

fn text_of<'a>(table: &'a Dict, key: &str) -> Option<&'a str> {
    table.get(key).and_then(Value::as_str)
}

fn truthy(value: Option<&Value>) -> Option<&Value> {
    value.filter(|value| value.truthy())
}

/// curves_and_fans: the curves by name and the fans (enabled, with a curve) by the name they
/// were given.
pub fn curves_and_fans(state: &Dict) -> (IndexMap<String, Curve>, IndexMap<String, Fan>) {
    let mut curves = IndexMap::new();
    for raw in dicts(state.get("curves")) {
        let Some(name) = text_of(raw, "Name").filter(|name| !name.is_empty()) else {
            continue;
        };
        let source = raw.get("SelectedTempSource").and_then(Value::as_dict);
        let identifier = source.and_then(|source| text_of(source, "Identifier"));
        let parts = dicts(raw.get("SelectedFanCurves")).into_iter();
        let hysteresis = raw.get("HysteresisConfig").and_then(Value::as_dict);
        let curve = Curve {
            name: name.to_string(),
            points: points(raw.get("Points")),
            source: identifier.filter(|id| !id.is_empty()).map(str::to_string),
            mix: (parts.filter_map(|part| truthy(part.get("Name"))))
                .map(Value::str)
                .collect(),
            hysteresis: hysteresis.cloned().unwrap_or_default(),
        };
        curves.insert(name.to_string(), curve);
    }
    let mut fans = IndexMap::new();
    for raw in dicts(state.get("controls")) {
        let selected = raw.get("SelectedFanCurve").and_then(Value::as_dict);
        let curve = selected.and_then(|selected| truthy(selected.get("Name")));
        let (Some(_), Some(curve)) = (truthy(raw.get("Enable")), curve) else {
            continue;
        };
        let named = truthy(raw.get("NickName")).or(truthy(raw.get("Identifier")));
        let name = named.map(Value::str).unwrap_or_default();
        if !name.is_empty() {
            let fan = Fan {
                name: name.clone(),
                curve: curve.str(),
                minimum: number(raw.get("MinimumPercent")),
                sensor: fan_sensor(text_of(raw, "Identifier")),
            };
            fans.insert(name, fan);
        }
    }
    (curves, fans)
}

/// names_and_hidden: the names the user gave the fans, by the id of the sensor that measures
/// each, and the fans FanControl hides (usually connectors with nothing on them).
pub fn names_and_hidden(section: &Dict) -> (IndexMap<String, String>, Vec<String>) {
    let mut names = IndexMap::new();
    let mut hidden = Vec::new();
    for control in dicts(section.get("Controls")) {
        let Some(sensor) = fan_sensor(text_of(control, "Identifier")) else {
            continue;
        };
        let nick = truthy(control.get("NickName")).map(Value::str);
        let nick = lp_py::strip(&nick.unwrap_or_default()).to_string();
        if !nick.is_empty() {
            names.insert(sensor.clone(), nick);
        }
        if truthy(control.get("IsHidden")).is_some() && !hidden.contains(&sensor) {
            hidden.push(sensor);
        }
    }
    (names, hidden)
}

// -- what changed ------------------------------------------------------------------------------

fn record(kind: &str, fields: Vec<(&str, Value)>) -> Value {
    let mut found = Dict::default();
    found.insert("kind".into(), Value::Str(kind.into()));
    for (key, value) in fields {
        found.insert(key.to_string(), value);
    }
    Value::Dict(found)
}

fn source_of(curve: &Dict) -> Value {
    let source = curve.get("SelectedTempSource").and_then(Value::as_dict);
    let identifier = source.and_then(|source| source.get("Identifier"));
    identifier.cloned().unwrap_or(Value::None)
}

fn curve_changes(name: &str, a: &Dict, b: &Dict) -> Vec<Value> {
    let name = || ("curve", Value::Str(name.to_string()));
    let of = |curve: &Dict, key: &str| curve.get(key).cloned().unwrap_or(Value::None);
    let mut found = Vec::new();
    if source_of(a) != source_of(b) {
        let fields = vec![name(), ("old", source_of(a)), ("new", source_of(b))];
        found.push(record("source", fields));
    }
    if of(a, "Points") != of(b, "Points") {
        let points = |curve: &Dict| match truthy(curve.get("Points")) {
            Some(points) => points.clone(),
            None => Value::List(Vec::new()),
        };
        let fields = vec![name(), ("old", points(a)), ("new", points(b))];
        found.push(record("points", fields));
    }
    let mix = "SelectedMixFunction";
    if of(a, mix) != of(b, mix) {
        let fields = vec![name(), ("old", of(a, mix)), ("new", of(b, mix))];
        found.push(record("mix-function", fields));
    }
    if of(a, "HysteresisConfig") != of(b, "HysteresisConfig") {
        found.push(record("hysteresis", vec![name()]));
    }
    found
}

/// changes: what differs between two states, as records the report words (`kind`:
/// `curve-added`, `curve-removed`, `source`, `points`, `mix-function`, `hysteresis`, `fan`).
/// (Curves are told by their names, which FanControl writes as texts; others are left out.)
pub fn changes(old: &Dict, new: &Dict) -> Vec<Value> {
    let named = |state: &Dict| -> IndexMap<String, Dict> {
        let curves = dicts(state.get("curves")).into_iter();
        let named = curves.filter_map(|curve| Some((text_of(curve, "Name")?, curve)));
        named
            .map(|(name, curve)| (name.to_string(), curve.clone()))
            .collect()
    };
    let (before, after) = (named(old), named(new));
    let mut names: Vec<&String> = before.keys().chain(after.keys()).collect();
    names.sort();
    names.dedup();
    let mut found = Vec::new();
    for name in names {
        let curve = ("curve", Value::Str(name.clone()));
        match (before.get(name), after.get(name)) {
            (None, _) => found.push(record("curve-added", vec![curve])),
            (_, None) => found.push(record("curve-removed", vec![curve])),
            (Some(a), Some(b)) => found.extend(curve_changes(name, a, b)),
        }
    }
    let nick = |control: &Dict| control.get("NickName").cloned().unwrap_or(Value::None);
    let outputs = dicts(old.get("controls"));
    for control in dicts(new.get("controls")) {
        // (of two outputs with one name, the later)
        let was = (outputs.iter().rev()).find(|was| nick(was) == nick(control));
        let Some(was) =
            was.filter(|was| Value::Dict((**was).clone()) != Value::Dict(control.clone()))
        else {
            continue;
        };
        let differs = |key: &&String| control.get(*key) != was.get(*key).or(Some(&Value::None));
        let fields = control.keys().filter(differs);
        let fields = fields.map(|key| Value::Str(key.clone())).collect();
        let fields = vec![("fan", nick(control)), ("fields", Value::List(fields))];
        found.push(record("fan", fields));
    }
    found
}

// -- looking for changes -------------------------------------------------------------------------

/// A file as it is at a moment: its path, when it was changed and its size.
pub type Stamp = (String, Option<SystemTime>, u64);

/// Tells when the configuration file changed. Its modification time is looked at every
/// `CHECK_S`; the file is read only when that has moved (and again if it could not be read,
/// since FanControl may be writing it just then).
pub struct Watch {
    given: String,
    places: Places,
    due: f64,
    seen: Option<Stamp>,
    warned: bool,
    pub path: Option<Path>,
    /// what there is to say for the log
    pub notes: Vec<String>,
}

impl Watch {
    pub fn new(given: &str, places: Places) -> Watch {
        Watch {
            given: given.to_string(),
            places,
            due: f64::NEG_INFINITY,
            seen: None,
            warned: false,
            path: None,
            notes: Vec::new(),
        }
    }

    /// stamp: the file as it is now (path, modification time, size); None without one.
    pub fn stamp(&mut self) -> Result<Option<Stamp>, String> {
        let path = find(&self.given, &self.places)?;
        if path.is_none() && !self.given.is_empty() && !self.warned {
            self.warned = true;
            self.notes
                .push(format!("FanControl: {} is no file", self.given));
        }
        self.path = path;
        let Some(path) = &self.path else {
            return Ok(None);
        };
        let text = path.text();
        Ok(std::fs::metadata(&text)
            .ok()
            .map(|info| (text, info.modified().ok(), info.len())))
    }

    /// poll: the `FanControl` section if the file is new or changed since the last time.
    pub fn poll(&mut self, now: f64) -> Result<Option<Dict>, String> {
        if now < self.due {
            return Ok(None);
        }
        self.due = now + CHECK_S;
        let Some(stamp) = self
            .stamp()?
            .filter(|stamp| Some(stamp) != self.seen.as_ref())
        else {
            return Ok(None);
        };
        let Some(path) = &self.path else {
            return Ok(None);
        };
        // (not readable now: the next look tries again)
        let section = read_section(path);
        if section.is_some() {
            self.seen = Some(stamp);
        }
        Ok(section)
    }
}
