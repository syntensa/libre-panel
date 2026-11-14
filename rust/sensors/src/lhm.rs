//! LibreHardwareMonitor's sensors as readings (sensors/librehardwaremonitor.py): every sensor
//! under its raw key (`lhm:<id>`), the common ones under friendly keys (`cpu.temp`,
//! `gpu.load`), every temperature and fan by device and name, the hottest memory module, and
//! what a power monitor on the graphics card's cable tells. Whichever way the sensors were
//! read (the sensor service, the web server's tree), they end here, so they are named and
//! grouped the same.

use std::sync::LazyLock;

use indexmap::IndexMap;
use lp_py::regex::Regex;
use lp_py::Value;
use lp_render::Reading;

use crate::hwnames::{tidy_cpu, tidy_gpu};

pub type Readings = IndexMap<String, Reading>;
/// The ids of the sensors each key is made from (none for a name).
pub type Sources = IndexMap<String, Vec<String>>;

/// One sensor with a value.
#[derive(Clone, Debug, PartialEq)]
pub struct LhmSensor {
    /// "/amdcpu/0/temperature/2"
    pub sensor_id: String,
    /// "/amdcpu/0"
    pub hardware_id: String,
    /// as LibreHardwareMonitor shows it
    pub hardware_name: String,
    /// LibreHardwareMonitor's SensorType: "Temperature", "Load", ...
    pub sensor_type: String,
    /// "Core (Tctl/Tdie)"
    pub label: String,
    pub value: f64,
    /// "°C"; "" for a plain number
    pub unit: String,
}

static NUMBER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*(-?[0-9]+(?:[.,][0-9]+)?)\s*(.*)$", false).expect("the number's pattern")
});
static CORE_CLOCK: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(CPU )?Core #\d+$", false).expect("the core's pattern"));

type Alias = (&'static str, &'static str, &'static [&'static str]);
/// alias, sensor type, preferred sensor names in order: as LibreHardwareMonitor reports them
/// for Intel, AMD and NVIDIA.
const CPU_ALIASES: [Alias; 4] = [
    (
        "cpu.temp",
        "temperature",
        &["CPU Package", "Core (Tctl/Tdie)", "Tctl", "Tdie", "Package"],
    ),
    ("cpu.load", "load", &["CPU Total"]),
    ("cpu.power", "power", &["CPU Package", "Package"]),
    ("cpu.freq", "clock", &["Cores (Average)"]),
];
const GPU_ALIASES: [Alias; 8] = [
    ("gpu.temp", "temperature", &["GPU Core", "GPU Hot Spot"]),
    ("gpu.hotspot", "temperature", &["GPU Hot Spot"]),
    ("gpu.load", "load", &["GPU Core", "D3D 3D"]),
    (
        "gpu.power",
        "power",
        &["GPU Package", "GPU Power", "GPU Core"],
    ),
    ("gpu.freq", "clock", &["GPU Core"]),
    ("gpu.mem.load", "load", &["GPU Memory"]),
    ("gpu.mem.used", "smalldata", &["GPU Memory Used"]),
    ("gpu.fan", "fan", &["GPU Fan", "GPU Fan 1"]),
];
const STORAGE: [&str; 4] = ["nvme", "hdd", "ssd", "storage"];

/// parse_value: texts like "45,0 °C" or "1234 RPM"; "NaN" and "-" give none.
pub fn parse_value(text: &str) -> Option<(f64, String)> {
    let chars: Vec<char> = text.chars().collect();
    let found = NUMBER.matches(&chars)?;
    let number = found.group(&chars, 1)?.replace(',', ".");
    let unit = found.group(&chars, 2).unwrap_or_default();
    Some((number.parse().ok()?, lp_py::strip(&unit).to_string()))
}

/// A reading that a device holds, with the key it has among all readings.
#[derive(Clone)]
struct Held {
    key: String,
    reading: Reading,
}

struct Device {
    hardware_id: String,
    name: String,
    /// "cpu", "gpu" or the id's first part (motherboard, battery, ...)
    kind: String,
    sensors: IndexMap<(String, String), Held>,
}

impl Device {
    fn get(&self, sensor_type: &str, names: &[&str]) -> Option<&Held> {
        names.iter().find_map(|name| {
            self.sensors
                .get(&(sensor_type.to_string(), name.to_string()))
        })
    }
}

fn kind(hardware_id: &str) -> String {
    let head = hardware_id.trim_matches('/');
    let head = lp_py::lower(head.split('/').next().unwrap_or(""));
    if head.contains("cpu") {
        "cpu".into()
    } else if head == "gpu-powermonitor" {
        // (on the graphics card's cable, not a graphics card)
        "powermonitor".into()
    } else if head.starts_with("gpu") {
        "gpu".into()
    } else {
        head
    }
}

fn number(reading: &Reading) -> f64 {
    match reading.value {
        Value::Float(f) => f,
        _ => 0.0,
    }
}

fn reading(value: f64, unit: &str, label: &str, origin: &str, device: &str) -> Reading {
    Reading {
        unit: unit.to_string(),
        label: label.to_string(),
        origin: origin.to_string(),
        device: device.to_string(),
        ..Reading::of(Value::Float(value))
    }
}

/// An amount of data as LibreHardwareMonitor 0.9.6 tells it, whichever tells it: 0.9.7 has one
/// type for every amount, in bytes (its web server: in a unit that reads well), where 0.9.6
/// told GB ("Data") and a graphics card's memory in MB ("SmallData"), which is what the keys
/// here are made from.
fn as_before(sensor: &LhmSensor) -> LhmSensor {
    let small_kind = lp_py::lower(&sensor.sensor_type);
    if small_kind != "data" && small_kind != "smalldata" {
        return sensor.clone();
    }
    let unit = lp_py::upper(lp_py::strip(&sensor.unit)).replace("IB", "B");
    let size = match unit.as_str() {
        "B" => 1.0,
        "KB" => 1024.0,
        "MB" => 1_048_576.0,
        "GB" => 1_073_741_824.0,
        "TB" => 1_099_511_627_776.0,
        _ => return sensor.clone(),
    };
    let small = small_kind == "smalldata" || kind(&sensor.sensor_id) == "gpu";
    let (name, unit, per) = if small {
        ("SmallData", "MB", 1_048_576.0)
    } else {
        ("Data", "GB", 1_073_741_824.0)
    };
    if sensor.sensor_type == name && sensor.unit == unit {
        return sensor.clone();
    }
    LhmSensor {
        sensor_type: name.into(),
        value: sensor.value * size / per,
        unit: unit.into(),
        ..sensor.clone()
    }
}

/// Dedicated GPUs first: NVIDIA, then AMD/Intel with more than 2 GB of own memory.
fn gpu_rank(device: &Device) -> (i32, f64) {
    let memory = device
        .sensors
        .get(&("smalldata".to_string(), "GPU Memory Total".to_string()))
        .map_or(0.0, |held| number(&held.reading));
    let vendor = if device.hardware_id.contains("nvidia") {
        2
    } else if memory > 2048.0 {
        1
    } else {
        0
    };
    (vendor, memory)
}

fn slug(text: &str) -> String {
    let mut out = String::new();
    let mut gap = false;
    for ch in lp_py::lower(text).chars() {
        if ch.is_ascii_lowercase() || ch.is_ascii_digit() {
            if gap {
                out.push('_');
            }
            gap = false;
            out.push(ch);
        } else {
            gap = true;
        }
    }
    // (a gap at the start or the end is cut, as strip("_") cuts it; one the text itself has
    // there in "_" was a gap like any other)
    out.trim_matches('_').to_string()
}

/// readings_from_sensors: the readings of the sensors; `gpu`: a part of the name of the
/// graphics card to take where there is more than one; `sources`, where given, is filled with
/// the ids of the sensors each key is made from.
pub fn readings_from_sensors(
    sensors: &[LhmSensor],
    gpu: Option<&str>,
    sources: Option<&mut Sources>,
) -> Readings {
    let mut out = Readings::new();
    let mut devices: IndexMap<String, Device> = IndexMap::new();
    for sensor in sensors.iter().map(as_before) {
        let key = format!("lhm:{}", sensor.sensor_id);
        let read = reading(
            sensor.value,
            &sensor.unit,
            &sensor.label,
            "",
            &sensor.hardware_name,
        );
        out.insert(key.clone(), read.clone());
        let device = devices
            .entry(sensor.hardware_id.clone())
            .or_insert_with(|| Device {
                hardware_id: sensor.hardware_id.clone(),
                name: sensor.hardware_name.clone(),
                kind: kind(&sensor.sensor_id),
                sensors: IndexMap::new(),
            });
        device
            .sensors
            .entry((lp_py::lower(&sensor.sensor_type), sensor.label.clone()))
            .or_insert(Held { key, reading: read });
    }
    let devices: Vec<Device> = devices.into_values().collect();
    let cpus: Vec<&Device> = devices.iter().filter(|d| d.kind == "cpu").collect();
    let mut gpus: Vec<&Device> = devices.iter().filter(|d| d.kind == "gpu").collect();
    if let Some(wanted) = gpu.filter(|name| !name.is_empty()) {
        let wanted = lp_py::lower(wanted);
        let named: Vec<&Device> = gpus
            .iter()
            .copied()
            .filter(|d| lp_py::lower(&d.name).contains(&wanted))
            .collect();
        if !named.is_empty() {
            gpus = named;
        }
    }
    gpus.sort_by(|a, b| {
        gpu_rank(b)
            .partial_cmp(&gpu_rank(a))
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let firsts: [(Option<&Device>, &[Alias]); 2] = [
        (cpus.first().copied(), &CPU_ALIASES),
        (gpus.first().copied(), &GPU_ALIASES),
    ];
    for (device, aliases) in firsts {
        let Some(device) = device else {
            continue;
        };
        for (alias, sensor_type, names) in aliases {
            if let Some(src) = device.get(sensor_type, names) {
                let r = &src.reading;
                out.insert(
                    alias.to_string(),
                    reading(number(r), &r.unit, &r.label, &src.key, &r.device),
                );
            }
        }
    }
    let mut averaged = Vec::new();
    if let Some(cpu) = cpus.first().filter(|_| !out.contains_key("cpu.freq")) {
        // No average clock (Intel, and older versions): the mean of the cores.
        let cores: Vec<&Held> = cpu
            .sensors
            .iter()
            .filter(|((sensor_type, name), _)| {
                sensor_type == "clock"
                    && CORE_CLOCK
                        .matches(&name.chars().collect::<Vec<_>>())
                        .is_some()
            })
            .map(|(_, held)| held)
            .collect();
        if !cores.is_empty() {
            let mean = lp_py::sum_floats(cores.iter().map(|held| number(&held.reading)))
                / cores.len() as f64;
            out.insert("cpu.freq".into(), reading(mean, "MHz", "CPU clock", "", ""));
            averaged = cores.iter().map(|held| held.key[4..].to_string()).collect();
        }
    }
    // tidied as the hardware source tidies the names it finds, so they read alike
    let name = |text: String, label: &str| Reading {
        label: label.to_string(),
        ..Reading::of(Value::Str(text))
    };
    if let Some(cpu) = cpus.first() {
        out.insert("cpu.name".into(), name(tidy_cpu(&cpu.name), "CPU"));
    }
    if let Some(gpu) = gpus.first() {
        out.insert("gpu.name".into(), name(tidy_gpu(&gpu.name), "GPU"));
    }
    temperatures_and_fans(&devices, &mut out);
    let modules = memory_temperature(&devices, &mut out);
    let pins = cable(&devices, &mut out);
    if let Some(sources) = sources {
        for (key, read) in &out {
            let origin = if !read.origin.is_empty() {
                read.origin.as_str()
            } else if key.starts_with("lhm:") {
                key.as_str()
            } else {
                ""
            };
            let ids = if origin.is_empty() {
                Vec::new()
            } else {
                vec![origin.chars().skip(4).collect()]
            };
            sources.insert(key.clone(), ids);
        }
        if !averaged.is_empty() {
            sources.insert("cpu.freq".into(), averaged);
        }
        if !modules.is_empty() {
            // (whichever module is the hottest: all of them are read)
            sources.insert("mem.temp".into(), modules);
        }
        if !pins.is_empty() {
            sources.insert("gpu.cable.pin.max".into(), pins);
        }
    }
    out
}

/// Every temperature and fan as temp.<device>.<name> and fan.<device>.<name>.
fn temperatures_and_fans(devices: &[Device], out: &mut Readings) {
    let mut count: IndexMap<&str, usize> = IndexMap::new();
    for device in devices {
        *count.entry(device.kind.as_str()).or_default() += 1;
    }
    let mut seen: IndexMap<&str, usize> = IndexMap::new();
    for device in devices {
        let nth = seen.entry(device.kind.as_str()).or_default();
        *nth += 1;
        let mut place = slug(&device.kind);
        if count[device.kind.as_str()] > 1 {
            place.push_str(&nth.to_string());
        }
        for ((sensor_type, label), held) in &device.sensors {
            let prefix = match sensor_type.as_str() {
                "temperature" => "temp",
                "fan" => "fan",
                _ => continue,
            };
            let key = format!("{prefix}.{place}.{}", slug(label));
            let name = if STORAGE.contains(&device.kind.as_str()) {
                device.name.clone()
            } else if (device.kind == "cpu" || device.kind == "gpu")
                && !lp_py::lower(label).starts_with(&device.kind)
            {
                format!("{} {label}", lp_py::upper(&device.kind))
            } else if device.kind == "powermonitor" {
                // ("WireView Pro II Onboard Temperature In")
                format!("{} {label}", device.name)
            } else {
                label.clone()
            };
            let r = &held.reading;
            out.entry(key)
                .or_insert_with(|| reading(number(r), &r.unit, &name, &held.key, &device.name));
        }
    }
}

/// The first of the readings with the highest value (max(), which keeps the first).
fn highest<'a>(found: &[&'a Held]) -> Option<&'a Held> {
    let mut best: Option<&Held> = None;
    for held in found {
        if best.is_none_or(|b| number(&held.reading) > number(&b.reading)) {
            best = Some(held);
        }
    }
    best
}

/// `mem.temp`: the temperature of the hottest memory module. LibreHardwareMonitor names each
/// module's sensor "DIMM #<n>"; the module's limits are temperatures too, but never change,
/// so only the "DIMM" ones count. Returns the ids of the sensors it was made from.
fn memory_temperature(devices: &[Device], out: &mut Readings) -> Vec<String> {
    let found: Vec<&Held> = devices
        .iter()
        .filter(|device| device.kind == "memory" || device.kind == "ram")
        .flat_map(|device| device.sensors.iter())
        .filter(|((sensor_type, label), _)| {
            sensor_type == "temperature" && lp_py::upper(label).starts_with("DIMM")
        })
        .map(|(_, held)| held)
        .collect();
    let Some(hottest) = highest(&found) else {
        return Vec::new();
    };
    let r = &hottest.reading;
    let unit = if r.unit.is_empty() { "°C" } else { &r.unit };
    out.insert(
        "mem.temp".into(),
        reading(number(r), unit, "Memory temperature", &hottest.key, ""),
    );
    found.iter().map(|held| held.key[4..].to_string()).collect()
}

/// `gpu.cable.*` from the first power monitor on the graphics card's cable (the WireView Pro
/// II), and `gpu.cable.pin.max`: the current of the pin that carries the most, what a
/// connector that is not seated well shows first. Returns the ids of the pins' sensors.
fn cable(devices: &[Device], out: &mut Readings) -> Vec<String> {
    let Some(monitor) = devices.iter().find(|d| d.kind == "powermonitor") else {
        return Vec::new();
    };
    let of = |sensor_type: &str, name: &str| {
        monitor
            .sensors
            .get(&(sensor_type.to_string(), name.to_string()))
    };
    let mut put = |alias: String, sensor_type: &str, name: String, label: String| {
        if let Some(src) = of(sensor_type, &name) {
            let r = &src.reading;
            out.insert(
                alias,
                reading(number(r), &r.unit, &label, &src.key, &r.device),
            );
        }
    };
    put(
        "gpu.cable.power".into(),
        "power",
        "Total Power".into(),
        "GPU cable power".into(),
    );
    put(
        "gpu.cable.current".into(),
        "current",
        "Total Current".into(),
        "GPU cable current".into(),
    );
    for (what, sensor_type, name) in [
        ("current", "current", "Current"),
        ("voltage", "voltage", "Voltage"),
    ] {
        for n in 1..=6 {
            put(
                format!("gpu.cable.pin{n}.{what}"),
                sensor_type,
                format!("Pin {n} {name}"),
                format!("Cable pin {n} {what}"),
            );
        }
    }
    let pins: Vec<&Held> = (1..=6)
        .filter_map(|n| of("current", &format!("Pin {n} Current")))
        .collect();
    let Some(most) = highest(&pins) else {
        return Vec::new();
    };
    let r = &most.reading;
    let unit = if r.unit.is_empty() { "A" } else { &r.unit };
    out.insert(
        "gpu.cable.pin.max".into(),
        reading(
            number(r),
            unit,
            "Cable pin current (highest)",
            &most.key,
            "",
        ),
    );
    pins.iter().map(|held| held.key[4..].to_string()).collect()
}

// -- the web server's tree --------------------------------------------------------------------

fn text_of(value: Option<&Value>) -> Option<String> {
    value.map(|v| match v {
        Value::Str(text) => text.clone(),
        other => other.str(),
    })
}

fn leaf_value(leaf: &lp_py::Dict) -> Option<(f64, String)> {
    let kind = text_of(leaf.get("Type")).unwrap_or_default();
    if lp_py::lower(&kind) == "throughput" {
        let raw = match leaf.get("RawValue") {
            Some(Value::Bool(b)) => Some(f64::from(u8::from(*b))),
            Some(v @ (Value::Int(_) | Value::Float(_))) => v.to_f64(),
            _ => None,
        };
        if let Some(raw) = raw {
            return Some((raw, "B/s".into()));
        }
    }
    parse_value(leaf.get("Value")?.as_str()?)
}

fn walk<'a>(
    node: &'a lp_py::Dict,
    mut device: Option<&'a lp_py::Dict>,
    out: &mut Vec<(&'a lp_py::Dict, Option<&'a lp_py::Dict>)>,
) {
    if node.get("HardwareId").is_some_and(Value::truthy) {
        device = Some(node);
    }
    if node.get("SensorId").is_some_and(Value::truthy) {
        out.push((node, device));
    }
    for child in node
        .get("Children")
        .and_then(Value::as_list)
        .unwrap_or_default()
    {
        if let Some(child) = child.as_dict() {
            walk(child, device, out);
        }
    }
}

/// sensors_from_tree: the sensors of the web server's `data.json` that have a value, in the
/// document's order.
pub fn sensors_from_tree(tree: &lp_py::Dict) -> Vec<LhmSensor> {
    let mut leaves = Vec::new();
    walk(tree, None, &mut leaves);
    let mut sensors = Vec::new();
    for (leaf, device) in leaves {
        let Some((value, unit)) = leaf_value(leaf) else {
            continue;
        };
        let sensor_id = text_of(leaf.get("SensorId")).unwrap_or_default();
        let hardware_id = device
            .and_then(|d| d.get("HardwareId"))
            .filter(|id| id.truthy())
            .and_then(|id| text_of(Some(id)))
            .unwrap_or_else(|| sensor_id.split('/').take(3).collect::<Vec<_>>().join("/"));
        let hardware_name = device
            .and_then(|d| text_of(d.get("Text")))
            .unwrap_or_else(|| hardware_id.clone());
        let sensor_type = match leaf.get("Type").filter(|kind| kind.truthy()) {
            Some(kind) => text_of(Some(kind)).unwrap_or_default(),
            None => match sensor_id.trim_matches('/').split('/').nth(2) {
                Some(part) => part.to_string(),
                None => continue,
            },
        };
        sensors.push(LhmSensor {
            sensor_id,
            hardware_id,
            hardware_name,
            sensor_type,
            label: text_of(leaf.get("Text")).unwrap_or_default(),
            value,
            unit,
        });
    }
    sensors
}

/// readings_from_tree: the readings of the web server's `data.json`.
pub fn readings_from_tree(tree: &lp_py::Dict, gpu: Option<&str>) -> Readings {
    readings_from_sensors(&sensors_from_tree(tree), gpu, None)
}
