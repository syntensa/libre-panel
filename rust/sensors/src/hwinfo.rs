//! Temperatures, power, clocks, loads and fans from HWiNFO on Windows (sensors/hwinfo.py).
//!
//! For those who run HWiNFO anyway: it reads what HWiNFO shares while its Shared Memory
//! Support is on. It starts no program and needs no rights: only the memory HWiNFO shares,
//! `Global\HWiNFO_SENS_SM2`, is read, once a second at most and only when HWiNFO has polled
//! anew. Without HWiNFO it reads nothing and looks again every few seconds.
//!
//! Readings: every one under `hwinfo:<sensor>/<reading>`, and the common ones under the keys
//! LibreHardwareMonitor's give (`cpu.temp`, `gpu.load`, ...): a theme made for one shows the
//! same with the other.

use std::sync::OnceLock;
use std::time::Instant;

use lp_py::regex::Regex;

use crate::lhm::{readings_from_sensors, LhmSensor, Readings};
use crate::shm::{f64_at, u32_at, Memory};

pub const MAPPING: &str = "Global\\HWiNFO_SENS_SM2";
/// "HWiS", as a DWORD
const SIGNATURE: u32 = 0x5369_5748;
/// "DEAD": sharing is off (the free version's twelve hours are over)
const STOPPED: u32 = 0x4441_4544;
/// signature, version, revision, poll time (8 bytes), then offset, size and count of the
/// sensors and of the readings
const HEADER_SIZE: usize = 44;
/// id, instance, original name, the person's name
const SENSOR_SIZE: usize = 8 + 128 + 128;
/// (version 2 on) the person's name in UTF-8, after it
const SENSOR_UTF_SIZE: usize = 128;
/// type, sensor index, id, original label, the person's label, unit, value, lowest, highest,
/// mean
const READING_SIZE: usize = 12 + 128 + 128 + 16 + 32;
/// (version 2 on) the label and unit in UTF-8, after it
const READING_UTF_SIZE: usize = 128 + 16;
/// (a header that says more sensors or readings than this is not HWiNFO's)
const MOST: u32 = 16384;
/// without HWiNFO: how often it is looked for again
const LOOK_SECONDS: f64 = 5.0;
/// the memory read at most this often
const FRESH_SECONDS: f64 = 1.0;

/// HWiNFO's reading types, as LibreHardwareMonitor's sensor types (whose keys these become).
const TYPES: [&str; 8] = [
    "Temperature",
    "Voltage",
    "Fan",
    "Current",
    "Power",
    "Clock",
    "Load",
    "Other",
];
/// What HWiNFO calls what LibreHardwareMonitor's keys are made from, per device kind: its
/// type, its label, LibreHardwareMonitor's label.
const LABELS: [(&str, &str, &str, &str); 17] = [
    ("cpu", "Temperature", "CPU (Tctl/Tdie)", "Core (Tctl/Tdie)"),
    ("cpu", "Temperature", "CPU Package", "CPU Package"),
    ("cpu", "Load", "Total CPU Usage", "CPU Total"),
    ("cpu", "Power", "CPU Package Power", "CPU Package"),
    ("cpu", "Power", "CPU PPT", "Package"),
    ("cpu", "Clock", "Core Clocks (avg)", "Cores (Average)"),
    ("cpu", "Clock", "Average Effective Clock", "Cores (Average)"),
    ("gpu", "Temperature", "GPU Temperature", "GPU Core"),
    (
        "gpu",
        "Temperature",
        "GPU Hot Spot Temperature",
        "GPU Hot Spot",
    ),
    ("gpu", "Load", "GPU Core Load", "GPU Core"),
    ("gpu", "Load", "GPU D3D Usage", "D3D 3D"),
    ("gpu", "Load", "GPU Memory Usage", "GPU Memory"),
    ("gpu", "Power", "Total Board Power (TBP)", "GPU Package"),
    ("gpu", "Power", "GPU Power", "GPU Power"),
    ("gpu", "Clock", "GPU Clock", "GPU Core"),
    ("gpu", "Fan", "GPU Fan1", "GPU Fan 1"),
    ("gpu", "Fan", "GPU Fan", "GPU Fan"),
];
/// MB, as LibreHardwareMonitor's "GPU Memory Used"
const MEMORY_USED: &str = "GPU Memory Allocated";

/// Windows-1252's characters of the bytes 0x80 to 0x9f (none: the byte is no character).
const CP1252: [Option<char>; 32] = [
    Some('€'),
    None,
    Some('‚'),
    Some('ƒ'),
    Some('„'),
    Some('…'),
    Some('†'),
    Some('‡'),
    Some('ˆ'),
    Some('‰'),
    Some('Š'),
    Some('‹'),
    Some('Œ'),
    None,
    Some('Ž'),
    None,
    None,
    Some('‘'),
    Some('’'),
    Some('“'),
    Some('”'),
    Some('•'),
    Some('–'),
    Some('—'),
    Some('˜'),
    Some('™'),
    Some('š'),
    Some('›'),
    Some('œ'),
    None,
    Some('ž'),
    Some('Ÿ'),
];

/// A name's bytes up to its NUL as text (Windows-1252, or UTF-8), without the space around it.
fn text(raw: &[u8], utf: bool) -> String {
    let raw = raw.split(|&byte| byte == 0).next().unwrap_or(&[]);
    let whole: String = match utf {
        true => String::from_utf8_lossy(raw).into_owned(),
        false => raw
            .iter()
            .map(|&byte| match byte {
                0x80..=0x9f => CP1252[usize::from(byte - 0x80)].unwrap_or('\u{fffd}'),
                _ => char::from(byte),
            })
            .collect(),
    };
    lp_py::strip(&whole).to_string()
}

/// _kind: a sensor's kind as LibreHardwareMonitor's ids have it ("cpu", "gpu", "storage",
/// "lpc" for the mainboard's chips, "memory", ...), the id of its device and the device's
/// name: the processor's and the graphics card's sensors are one device each, by their
/// number in HWiNFO.
fn kind_of(name: &str) -> (String, String, String) {
    static DEVICE: OnceLock<Option<Regex>> = OnceLock::new();
    let device = DEVICE
        .get_or_init(|| Regex::new(r"^(CPU|GPU) \[#(\d+)\]:\s*(.*?)(?::\s*[^:]*)?$", false).ok());
    let chars: Vec<char> = name.chars().collect();
    if let Some(found) = device.as_ref().and_then(|device| device.matches(&chars)) {
        let group = |n: usize| found.group(&chars, n).unwrap_or_default();
        let kind = lp_py::lower(&group(1));
        let rest = group(3);
        let first = lp_py::strip(rest.split(':').next().unwrap_or(""));
        let device = if first.is_empty() { name } else { first };
        return (
            kind.clone(),
            format!("/{kind}/{}", group(2)),
            device.to_string(),
        );
    }
    let lower = lp_py::lower(name);
    let or_name = |part: &str| {
        let part = lp_py::strip(part);
        if part.is_empty() { name } else { part }.to_string()
    };
    if lower.starts_with("s.m.a.r.t") || lower.starts_with("drive") {
        let last = name.split_once(':').map_or(name, |(_, last)| last);
        return ("storage".into(), format!("/storage/{name}"), or_name(last));
    }
    if lower.starts_with("system") || lower.contains("memory") {
        return ("memory".into(), format!("/memory/{name}"), name.to_string());
    }
    let firsts = ["network", "windows hardware errors", "battery"];
    if firsts.iter().any(|first| lower.starts_with(first)) {
        let word = lower
            .split(lp_py::isspace)
            .find(|word| !word.is_empty())
            .unwrap_or("")
            .to_string();
        return (word.clone(), format!("/{word}/{name}"), name.to_string());
    }
    // the mainboard's chips: fans as LibreHardwareMonitor's
    ("lpc".into(), format!("/lpc/{name}"), name.to_string())
}

/// _unit: a reading's type and value as LibreHardwareMonitor's keys need them: an amount of
/// memory in MB as "SmallData", one in GB as "Data", a rate as "Throughput" in bytes.
fn unit_of(kind: &'static str, unit: &str, value: f64) -> (&'static str, f64) {
    let lower = lp_py::lower(lp_py::strip(unit));
    if kind == "Other" {
        match lower.as_str() {
            "mb" => return ("SmallData", value),
            "gb" => return ("Data", value),
            "kb/s" => return ("Throughput", value * 1024.0),
            "mb/s" => return ("Throughput", value * 1_048_576.0),
            "gb/s" => return ("Throughput", value * 1_073_741_824.0),
            _ => {}
        }
    }
    (kind, value)
}

/// sensors_of: the readings in HWiNFO's memory as sensors (LibreHardwareMonitor's kind of
/// record, so that they are named the same); none where it is not HWiNFO's, or sharing is
/// off; an error where the memory ends before what its header says.
pub fn sensors_of(memory: &dyn Memory) -> Result<Option<Vec<LhmSensor>>, String> {
    let header = memory.read(0, HEADER_SIZE)?;
    let field = |at: usize| u32_at(&header, at).unwrap_or(0);
    let (signature, version) = (field(0), field(4));
    let (s_at, s_size, s_count) = (field(20) as usize, field(24) as usize, field(28));
    let (r_at, r_size, r_count) = (field(32) as usize, field(36) as usize, field(40));
    if signature != SIGNATURE || s_count == 0 || s_count > MOST || r_count > MOST {
        return Ok(None);
    }
    if s_size < SENSOR_SIZE || r_size < READING_SIZE {
        return Ok(None);
    }
    let (s_count, r_count) = (s_count as usize, r_count as usize);
    let short = || "the memory ends before its records".to_string();
    let mut devices = Vec::new();
    let sensor_utf = version >= 2 && s_size >= SENSOR_SIZE + SENSOR_UTF_SIZE;
    let block = memory.read(s_at, s_size * s_count)?;
    for n in 0..s_count {
        let at = n * s_size;
        let record = block.get(at..at + s_size).ok_or_else(short)?;
        let mut name = text(&record[136..264], false);
        if name.is_empty() {
            name = text(&record[8..136], false);
        }
        if sensor_utf {
            let own = text(&record[SENSOR_SIZE..SENSOR_SIZE + SENSOR_UTF_SIZE], true);
            if !own.is_empty() {
                name = own;
            }
        }
        devices.push(kind_of(&name));
    }
    let reading_utf = version >= 2 && r_size >= READING_SIZE + READING_UTF_SIZE;
    let block = memory.read(r_at, r_size * r_count)?;
    let mut out = Vec::new();
    for n in 0..r_count {
        let at = n * r_size;
        let record = block.get(at..at + r_size).ok_or_else(short)?;
        let number = |at: usize| u32_at(record, at).unwrap_or(0);
        let (kind_no, index, reading_id) = (number(0), number(4) as usize, number(8));
        let value = f64_at(record, 284).unwrap_or(f64::NAN);
        if !(1..=8).contains(&kind_no) || index >= devices.len() || value.is_nan() {
            continue;
        }
        let or = |own: String, other: String| if own.is_empty() { other } else { own };
        let mut label = or(
            text(&record[140..268], false),
            text(&record[12..140], false),
        );
        let mut unit = text(&record[268..284], false);
        if reading_utf {
            let utf = &record[READING_SIZE..READING_SIZE + READING_UTF_SIZE];
            label = or(text(&utf[..128], true), label);
            unit = or(text(&utf[128..], true), unit);
        }
        let (kind, device_id, device_name) = &devices[index];
        let (sensor_type, value) = unit_of(TYPES[kind_no as usize - 1], &unit, value);
        if kind == "gpu" && sensor_type == "SmallData" && label == MEMORY_USED {
            label = "GPU Memory Used".into();
        }
        let known = LABELS.iter().find(|(of, of_type, theirs, _)| {
            of == kind && *of_type == sensor_type && *theirs == label
        });
        if let Some((_, _, _, ours)) = known {
            label = (*ours).to_string();
        }
        let unit = match sensor_type {
            "Throughput" => "B/s".to_string(),
            "Fan" => "RPM".to_string(),
            _ => unit,
        };
        out.push(LhmSensor {
            sensor_id: format!("{device_id}/{index}/{reading_id}"),
            hardware_id: device_id.clone(),
            hardware_name: device_name.clone(),
            sensor_type: sensor_type.to_string(),
            label,
            value,
            unit,
        });
    }
    Ok(Some(out))
}

/// readings_of: every reading under `hwinfo:<device>/<sensor>/<reading>`, and the common ones
/// under the keys LibreHardwareMonitor's give.
pub fn readings_of(sensors: &[LhmSensor]) -> Readings {
    let mut out = Readings::new();
    for (key, mut reading) in readings_from_sensors(sensors, None, None) {
        let named = |key: &str| match key.strip_prefix("lhm:") {
            Some(rest) => format!("hwinfo:{rest}"),
            None => key.to_string(),
        };
        reading.origin = named(&reading.origin);
        out.insert(named(&key), reading);
    }
    out
}

type Opener = Box<dyn Fn() -> Option<Box<dyn Memory>> + Send>;

/// Readings from HWiNFO's shared memory, while HWiNFO shares it (HWiNFOProvider).
pub struct HwinfoSource {
    opener: Opener,
    began: Instant,
    memory: Option<Box<dyn Memory>>,
    looked: f64,
    read_at: f64,
    polled: Option<[u8; 8]>,
    readings: Readings,
    /// "reading", "looking" (no HWiNFO), "stopped" (sharing is off)
    pub state: &'static str,
}

impl Default for HwinfoSource {
    fn default() -> HwinfoSource {
        HwinfoSource::new()
    }
}

impl HwinfoSource {
    pub fn new() -> HwinfoSource {
        HwinfoSource::with(Box::new(|| crate::shm::open(MAPPING)))
    }

    /// As `new`, the memory opened by `opener`.
    pub fn with(opener: Opener) -> HwinfoSource {
        HwinfoSource {
            opener,
            began: Instant::now(),
            memory: None,
            looked: -LOOK_SECONDS,
            read_at: -FRESH_SECONDS,
            polled: None,
            readings: Readings::new(),
            state: "looking",
        }
    }

    pub fn read(&mut self) -> Readings {
        let now = self.began.elapsed().as_secs_f64();
        self.read_at_time(now)
    }

    /// `read` at `now` on the source's own clock (seconds).
    pub fn read_at_time(&mut self, now: f64) -> Readings {
        if now - self.read_at < FRESH_SECONDS {
            return self.readings.clone();
        }
        self.read_at = now;
        if self.memory.is_none() {
            if now - self.looked < LOOK_SECONDS {
                return Readings::new();
            }
            self.looked = now;
            self.memory = (self.opener)();
        }
        let Some(memory) = &self.memory else {
            self.state = "looking";
            self.readings = Readings::new();
            return Readings::new();
        };
        let fresh = || -> Result<Option<(Readings, [u8; 8])>, String> {
            let header = memory.read(0, HEADER_SIZE)?;
            if u32_at(&header, 0) == Some(STOPPED) {
                return Ok(None);
            }
            let mut polled = [0u8; 8];
            polled.copy_from_slice(&header[12..20]);
            if Some(polled) == self.polled {
                return Ok(Some((self.readings.clone(), polled)));
            }
            // (polled anew since: read again)
            let found = sensors_of(memory.as_ref())?.ok_or("not HWiNFO's memory")?;
            Ok(Some((readings_of(&found), polled)))
        };
        match fresh() {
            Ok(None) => {
                self.state = "stopped";
                self.readings = Readings::new();
            }
            Ok(Some((readings, polled))) => {
                if self.polled != Some(polled) {
                    self.state = "reading";
                }
                self.readings = readings;
                self.polled = Some(polled);
            }
            Err(_) => {
                self.memory = None;
                self.state = "looking";
                self.readings = Readings::new();
            }
        }
        self.readings.clone()
    }
}
