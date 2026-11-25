//! Dell's fans and temperatures, as Dell Command | Monitor reports them (Windows): its WMI
//! namespace `root\dcim\sysman` holds a numeric sensor for each (CIM_NumericSensor, made
//! DCIM_NumericSensor by Dell), with its name, its reading and the unit of it. Many Dell PCs
//! show their fans to no other program; with Dell Command | Monitor installed they come as
//! `fan.dell.<name>` (RPM) and `temp.dell.<name>` (°C), named as it names them ("Processor
//! Fan" is `fan.dell.processor_fan`), where every fan and temperature of a PC is.
//!
//! It is asked every `update_s` seconds in a thread of its own; a PC without Dell Command |
//! Monitor is said once and asked again only a minute later. What it reads is said when it
//! first answers and again when that changes (each reading's name, or why there is none: the
//! sensors it gave and what they are), so that an owner finds the names in the log. A reading
//! that does not come again keeps its last value for three times `update_s`, then it goes.

use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_py::Value;
use lp_render::Reading;

use crate::lhm::Readings;

/// Dell Command | Monitor's namespace
pub const NAMESPACE: &str = r"root\dcim\sysman";
/// what it is asked: its numeric sensors, with what a reading needs of each
pub const QUERY: &str = "SELECT ElementName, DeviceID, SensorType, BaseUnits, UnitModifier, \
                         CurrentReading FROM CIM_NumericSensor";
/// the properties of each sensor the query asks for (in its order)
pub const FIELDS: [&str; 6] = [
    "ElementName",
    "DeviceID",
    "SensorType",
    "BaseUnits",
    "UnitModifier",
    "CurrentReading",
];
/// a namespace that is not there is asked again after this long
const ABSENT_S: f64 = 60.0;

/// CIM_NumericSensor's SensorType: a temperature, a fan (its tachometer)
const TEMPERATURE: i64 = 2;
const TACHOMETER: i64 = 5;
/// CIM's BaseUnits: degrees Celsius, revolutions per minute
const DEGREES_C: i64 = 2;
const RPM: i64 = 19;

/// One sensor as WMI gives it: its properties by name, a number or a text (none where WMI
/// gives none, as a sensor gives none that has no reading).
pub type Row = IndexMap<String, Value>;

/// `[sensors.dell]`.
#[derive(Clone, Debug, PartialEq)]
pub struct Options {
    pub update_s: f64,
}

impl Default for Options {
    fn default() -> Options {
        Options { update_s: 2.0 }
    }
}

fn number(row: &Row, key: &str) -> Option<i64> {
    match row.get(key)? {
        Value::Int(n) => i64::try_from(n).ok(),
        Value::Float(f) if f.is_finite() => Some(f.trunc() as i64),
        Value::Str(text) => lp_py::int_of_str(text).and_then(|n| i64::try_from(n).ok()),
        _ => None,
    }
}

fn text(row: &Row, key: &str) -> String {
    match row.get(key) {
        Some(Value::Str(text)) => lp_py::strip(text).to_string(),
        _ => String::new(),
    }
}

/// readings_from_rows: each fan as `fan.dell.<name>` (RPM) and each temperature as
/// `temp.dell.<name>` (°C), its reading times ten to the power of its UnitModifier; a sensor
/// of another kind, or of no reading, is none. A name met again is numbered from its second
/// on (`processor_fan_2`); a sensor of no name is named by its DeviceID's last part.
pub fn readings_from_rows(rows: &[Row]) -> Readings {
    let mut out = Readings::new();
    for row in rows {
        let kind = number(row, "SensorType");
        let units = number(row, "BaseUnits");
        let (prefix, unit) = match (kind, units) {
            (Some(TACHOMETER), _) | (_, Some(RPM)) => ("fan", "RPM"),
            (Some(TEMPERATURE), _) | (_, Some(DEGREES_C)) => ("temp", "°C"),
            _ => continue,
        };
        let Some(reading) = number(row, "CurrentReading") else {
            continue;
        };
        let power = number(row, "UnitModifier").unwrap_or(0).clamp(-12, 12) as i32;
        let value = if power >= 0 {
            reading as f64 * 10f64.powi(power)
        } else {
            reading as f64 / 10f64.powi(-power)
        };
        let mut label = text(row, "ElementName");
        if label.is_empty() {
            let device = text(row, "DeviceID");
            label = device.rsplit(['/', '\\']).next().unwrap_or("").to_string();
        }
        let name = match crate::lhm::slug(&label) {
            name if name.is_empty() => prefix.to_string(),
            name => name,
        };
        let mut key = format!("{prefix}.dell.{name}");
        let mut nth = 1;
        while out.contains_key(&key) {
            nth += 1;
            key = format!("{prefix}.dell.{name}_{nth}");
        }
        let reading = Reading {
            unit: unit.to_string(),
            label: if label.is_empty() { name } else { label },
            device: "Dell".to_string(),
            ..Reading::of(Value::Float(value))
        };
        out.insert(key, reading);
    }
    out
}

/// gives: what a round read, for the log: each reading's name and label, or why there is none
/// (no sensor at all, or those there are, with their type, units and reading: the first eight).
pub fn gives(rows: &[Row], readings: &Readings) -> String {
    if !readings.is_empty() {
        let named: Vec<String> = (readings.iter())
            .map(|(key, reading)| format!("{key} ({})", reading.label))
            .collect();
        return format!("dell: Dell Command | Monitor gives {}", named.join(", "));
    }
    if rows.is_empty() {
        return "dell: Dell Command | Monitor answers, but with no sensor".into();
    }
    let n = |row: &Row, key: &str| number(row, key).map_or("none".to_string(), |v| v.to_string());
    let seen: Vec<String> = (rows.iter().take(8))
        .map(|row| {
            let name = match text(row, "ElementName") {
                name if name.is_empty() => text(row, "DeviceID"),
                name => name,
            };
            format!(
                "{name}: type {}, units {}, reading {}",
                n(row, "SensorType"),
                n(row, "BaseUnits"),
                n(row, "CurrentReading")
            )
        })
        .collect();
    format!(
        "dell: Dell Command | Monitor answers, but none of its {} sensors is a fan or a \
         temperature with a reading ({})",
        rows.len(),
        seen.join("; ")
    )
}

/// WMI on Windows: a query's rows in a namespace, each with the properties asked for.
#[cfg(windows)]
pub mod wmi {
    use super::Row;
    use lp_py::Value;
    use windows::core::{BSTR, PCWSTR};
    use windows::Win32::System::Com::{
        CoCreateInstance, CoInitializeEx, CoSetProxyBlanket, CoTaskMemFree, CLSCTX_INPROC_SERVER,
        COINIT_MULTITHREADED, EOAC_NONE, RPC_C_AUTHN_LEVEL_CALL, RPC_C_IMP_LEVEL_IMPERSONATE,
    };
    use windows::Win32::System::Variant::{
        VariantClear, VariantToInt32, VariantToStringAlloc, VARENUM, VARIANT, VT_BSTR, VT_EMPTY,
        VT_NULL,
    };
    use windows::Win32::System::Wmi::{
        IWbemClassObject, IWbemLocator, IWbemServices, WbemLocator, WBEM_FLAG_FORWARD_ONLY,
        WBEM_FLAG_RETURN_IMMEDIATELY,
    };

    /// RPC_C_AUTHN_WINNT and RPC_C_AUTHZ_NONE (rpcdce.h)
    const AUTHN_WINNT: u32 = 10;
    const AUTHZ_NONE: u32 = 0;
    /// how long the next row is waited for, in milliseconds
    const ROW_WAIT_MS: i32 = 5000;

    fn said(why: windows::core::Error) -> String {
        format!("{} (0x{:08X})", why.message(), why.code().0 as u32)
    }

    /// A connection to a namespace of this PC's WMI, on a thread of the multithreaded
    /// apartment (the thread's COM is set up here, once; it stays for the thread).
    pub struct Wmi {
        services: IWbemServices,
    }

    impl Wmi {
        pub fn connect(namespace: &str) -> Result<Wmi, String> {
            // SAFETY: COM calls with their documented arguments; what they give is checked.
            unsafe {
                // (S_FALSE where the thread has it already; the thread is the source's own)
                let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
                let locator: IWbemLocator =
                    CoCreateInstance(&WbemLocator, None, CLSCTX_INPROC_SERVER).map_err(said)?;
                let empty = BSTR::new();
                let services = locator
                    .ConnectServer(
                        &BSTR::from(namespace),
                        &empty,
                        &empty,
                        &empty,
                        0,
                        &empty,
                        None,
                    )
                    .map_err(said)?;
                CoSetProxyBlanket(
                    &services,
                    AUTHN_WINNT,
                    AUTHZ_NONE,
                    PCWSTR::null(),
                    RPC_C_AUTHN_LEVEL_CALL,
                    RPC_C_IMP_LEVEL_IMPERSONATE,
                    None,
                    EOAC_NONE,
                )
                .map_err(said)?;
                Ok(Wmi { services })
            }
        }

        /// The rows of a WQL query, each with `fields` (a number where WMI gives one, a text
        /// where it gives a text, none where it gives none).
        pub fn rows(&self, query: &str, fields: &[&str]) -> Result<Vec<Row>, String> {
            let mut out = Vec::new();
            // SAFETY: COM calls with their documented arguments; each VARIANT is cleared.
            unsafe {
                let found = self
                    .services
                    .ExecQuery(
                        &BSTR::from("WQL"),
                        &BSTR::from(query),
                        WBEM_FLAG_FORWARD_ONLY | WBEM_FLAG_RETURN_IMMEDIATELY,
                        None,
                    )
                    .map_err(said)?;
                // (the rows come through a proxy of their own: as the connection's, its calls
                // made on this process's behalf, as a provider that acts for the caller needs
                // them; where it is no proxy, there is nothing to set)
                let _ = CoSetProxyBlanket(
                    &found,
                    AUTHN_WINNT,
                    AUTHZ_NONE,
                    PCWSTR::null(),
                    RPC_C_AUTHN_LEVEL_CALL,
                    RPC_C_IMP_LEVEL_IMPERSONATE,
                    None,
                    EOAC_NONE,
                );
                loop {
                    let mut one: [Option<IWbemClassObject>; 1] = [None];
                    let mut returned = 0u32;
                    let done = found.Next(ROW_WAIT_MS, &mut one, &mut returned);
                    if done.is_err() {
                        return Err(said(windows::core::Error::from(done)));
                    }
                    let Some(object) = one[0].take().filter(|_| returned == 1) else {
                        break;
                    };
                    let mut row = Row::new();
                    for &field in fields {
                        let name: Vec<u16> = field.encode_utf16().chain([0]).collect();
                        let mut value = VARIANT::default();
                        if object
                            .Get(PCWSTR(name.as_ptr()), 0, &mut value, None, None)
                            .is_ok()
                        {
                            row.insert(field.to_string(), of_variant(&value));
                        }
                        let _ = VariantClear(&mut value);
                    }
                    out.push(row);
                }
            }
            Ok(out)
        }
    }

    /// A VARIANT as a value: a text for a text, a number for what reads as one, none for
    /// none.
    unsafe fn of_variant(value: &VARIANT) -> Value {
        let kind: VARENUM = value.Anonymous.Anonymous.vt;
        if kind == VT_EMPTY || kind == VT_NULL {
            return Value::None;
        }
        if kind == VT_BSTR {
            return match VariantToStringAlloc(value) {
                Ok(text) => {
                    let out = text.to_string().unwrap_or_default();
                    CoTaskMemFree(Some(text.0 as *const _));
                    Value::Str(out)
                }
                Err(_) => Value::None,
            };
        }
        match VariantToInt32(value) {
            Ok(n) => Value::int(i64::from(n)),
            Err(_) => Value::None,
        }
    }
}

/// The rows of Dell Command | Monitor's numeric sensors; why there are none.
#[cfg(windows)]
fn fetch(connection: &mut Option<wmi::Wmi>) -> Result<Vec<Row>, String> {
    if connection.is_none() {
        *connection = Some(wmi::Wmi::connect(NAMESPACE)?);
    }
    let rows = connection.as_ref().map(|wmi| wmi.rows(QUERY, &FIELDS));
    match rows {
        Some(Ok(rows)) => Ok(rows),
        Some(Err(why)) => {
            *connection = None; // (connected anew the next time)
            Err(why)
        }
        None => Ok(Vec::new()),
    }
}

#[cfg(not(windows))]
fn fetch(_connection: &mut Option<()>) -> Result<Vec<Row>, String> {
    Err("Dell Command | Monitor is a Windows program".into())
}

#[derive(Default)]
struct State {
    stop: bool,
    rounds: u64,
    got: Option<(Instant, Readings)>,
    notes: Vec<String>,
}

struct Shared {
    state: Mutex<State>,
    changed: Condvar,
}

fn run(shared: &Shared, every: Duration) {
    let mut connection = None;
    // said once, until it answers again
    let mut failed = false;
    // the readings' names said last (what it reads is said again when they change)
    let mut said: Option<Vec<String>> = None;
    loop {
        let found = fetch(&mut connection);
        let Ok(mut state) = shared.state.lock() else {
            return;
        };
        let mut wait = every;
        match found {
            Ok(rows) => {
                if std::mem::take(&mut failed) {
                    state
                        .notes
                        .push("dell: Dell Command | Monitor answers again".into());
                }
                let readings = readings_from_rows(&rows);
                let names: Vec<String> = readings.keys().cloned().collect();
                if said.as_ref() != Some(&names) {
                    state.notes.push(gives(&rows, &readings));
                    said = Some(names);
                }
                state.got = Some((Instant::now(), readings));
            }
            Err(why) => {
                if !failed {
                    failed = true;
                    let note = format!("dell: no Dell Command | Monitor to ask ({why})");
                    state.notes.push(note);
                }
                wait = wait.max(Duration::from_secs_f64(ABSENT_S));
            }
        }
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

/// `fan.dell.*` and `temp.dell.*` from Dell Command | Monitor.
pub struct DellSource {
    shared: Arc<Shared>,
    every: f64,
    thread: Option<JoinHandle<()>>,
}

impl DellSource {
    pub fn new(options: Options) -> DellSource {
        let every = options.update_s.max(1.0);
        let shared = Arc::new(Shared {
            state: Mutex::new(State::default()),
            changed: Condvar::new(),
        });
        let theirs = Arc::clone(&shared);
        let wait = Duration::from_secs_f64(every);
        let thread = std::thread::Builder::new()
            .name("sensors-dell".into())
            .spawn(move || run(&theirs, wait))
            .ok();
        DellSource {
            shared,
            every,
            thread,
        }
    }

    pub fn read(&mut self) -> Readings {
        let Ok(state) = self.shared.state.lock() else {
            return Readings::new();
        };
        match &state.got {
            Some((when, readings)) if when.elapsed().as_secs_f64() <= 3.0 * self.every => {
                readings.clone()
            }
            _ => Readings::new(),
        }
    }

    /// What it said since it was last asked.
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

    pub fn close(&mut self) {
        if let Ok(mut state) = self.shared.state.lock() {
            state.stop = true;
        }
        self.shared.changed.notify_all();
        drop(self.thread.take());
    }
}

impl Drop for DellSource {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(fields: &[(&str, Value)]) -> Row {
        fields
            .iter()
            .map(|(k, v)| (k.to_string(), v.clone()))
            .collect()
    }

    fn int(n: i64) -> Value {
        Value::int(n)
    }

    fn text(t: &str) -> Value {
        Value::Str(t.into())
    }

    #[test]
    fn fans_and_temperatures_as_dell_command_monitor_reports_them() {
        let rows = [
            // (a fan as the issue's PC reports it, and one of no SensorType but RPM)
            row(&[
                ("ElementName", text("Processor Fan")),
                ("DeviceID", text("Root/MainSystemChassis/FanObj")),
                ("SensorType", int(5)),
                ("BaseUnits", int(19)),
                ("UnitModifier", int(0)),
                ("CurrentReading", int(2345)),
            ]),
            row(&[
                ("ElementName", text("System Fan")),
                ("SensorType", int(1)),
                ("BaseUnits", int(19)),
                ("CurrentReading", int(812)),
            ]),
            // (a temperature in tenths of a degree)
            row(&[
                ("ElementName", text("CPU Temperature")),
                ("SensorType", int(2)),
                ("BaseUnits", int(2)),
                ("UnitModifier", int(-1)),
                ("CurrentReading", int(523)),
            ]),
            // (a voltage, and a fan with no reading: none of them)
            row(&[
                ("ElementName", text("CPU Voltage")),
                ("SensorType", int(3)),
                ("BaseUnits", int(5)),
                ("CurrentReading", int(1100)),
            ]),
            row(&[
                ("ElementName", text("Rear Fan")),
                ("SensorType", int(5)),
                ("CurrentReading", Value::None),
            ]),
            // (the same name again, and none at all)
            row(&[
                ("ElementName", text("Processor Fan")),
                ("SensorType", int(5)),
                ("CurrentReading", text("1200")),
            ]),
            row(&[
                ("DeviceID", text("Root/MainSystemChassis/Fan2Obj")),
                ("SensorType", int(5)),
                ("CurrentReading", int(900)),
            ]),
        ];
        let got = readings_from_rows(&rows);
        let seen: Vec<(&str, f64, &str, &str)> = got
            .iter()
            .map(|(k, r)| {
                let Value::Float(v) = r.value else {
                    panic!("{k}")
                };
                (k.as_str(), v, r.unit.as_str(), r.label.as_str())
            })
            .collect();
        assert_eq!(
            seen,
            [
                ("fan.dell.processor_fan", 2345.0, "RPM", "Processor Fan"),
                ("fan.dell.system_fan", 812.0, "RPM", "System Fan"),
                ("temp.dell.cpu_temperature", 52.3, "°C", "CPU Temperature"),
                ("fan.dell.processor_fan_2", 1200.0, "RPM", "Processor Fan"),
                ("fan.dell.fan2obj", 900.0, "RPM", "Fan2Obj"),
            ]
        );
        assert!(got.values().all(|r| r.device == "Dell"));
    }

    #[test]
    fn what_it_reads_is_said_by_name_or_why_it_reads_nothing() {
        let fan = row(&[
            ("ElementName", text("Processor Fan")),
            ("DeviceID", text("Root/MainSystemChassis/FanObj")),
            ("SensorType", int(5)),
            ("BaseUnits", int(19)),
            ("CurrentReading", int(2345)),
        ]);
        let rows = [fan];
        assert_eq!(
            gives(&rows, &readings_from_rows(&rows)),
            "dell: Dell Command | Monitor gives fan.dell.processor_fan (Processor Fan)"
        );
        assert_eq!(
            gives(&[], &Readings::new()),
            "dell: Dell Command | Monitor answers, but with no sensor"
        );
        // (sensors, but no fan or temperature with a reading among them: what they are)
        let rows = [
            row(&[
                ("DeviceID", text("Root/MainSystemChassis/FanObj")),
                ("SensorType", int(5)),
                ("CurrentReading", Value::None),
            ]),
            row(&[
                ("ElementName", text("CPU Voltage")),
                ("SensorType", int(3)),
                ("BaseUnits", int(5)),
                ("CurrentReading", int(1100)),
            ]),
        ];
        assert_eq!(
            gives(&rows, &readings_from_rows(&rows)),
            "dell: Dell Command | Monitor answers, but none of its 2 sensors is a fan or a \
             temperature with a reading (Root/MainSystemChassis/FanObj: type 5, units none, \
             reading none; CPU Voltage: type 3, units 5, reading 1100)"
        );
    }

    #[test]
    fn without_dell_command_monitor_it_says_so_once_and_reads_nothing() {
        let mut source = DellSource::new(Options::default());
        source.wait_ready(30.0);
        // (no Dell Command | Monitor on this PC, as on CI's: none, and said once)
        if source.read().is_empty() {
            let notes = source.notes();
            assert_eq!(notes.len(), 1, "{notes:?}");
            assert!(
                notes[0].starts_with("dell: no Dell Command | Monitor"),
                "{notes:?}"
            );
        }
        assert!(source.notes().is_empty());
        source.close();
    }

    #[cfg(windows)]
    #[test]
    fn wmi_gives_rows_of_texts_and_numbers() {
        // (Windows' own classes: what the source asks of Dell's, asked of these)
        let wmi = wmi::Wmi::connect(r"root\cimv2").expect("root\\cimv2");
        let rows = wmi
            .rows(
                "SELECT Caption, NumberOfProcesses FROM Win32_OperatingSystem",
                &["Caption", "NumberOfProcesses", "NoSuchProperty"],
            )
            .expect("Win32_OperatingSystem");
        assert_eq!(rows.len(), 1, "{rows:?}");
        assert!(matches!(&rows[0]["Caption"], Value::Str(t) if t.contains("Windows")));
        assert!(matches!(&rows[0]["NumberOfProcesses"], Value::Int(_)));
        assert!(!rows[0].contains_key("NoSuchProperty"));
    }
}
