//! What the sensor service and Libre Panel say to each other over the pipe
//! (sensorservice/protocol.py), Libre Panel's side of it: a message is one JSON object of at
//! most a set number of bytes in UTF-8, sent after its length as four bytes (little endian).
//! The client asks (`hello`, `read`), the service answers, one at a time. Everything that
//! comes in is checked here before anything else looks at it: a length out of bounds ends the
//! connection, an answer that is not as the protocol has it is taken for no answer.

use lp_py::json::{dumps, loads, Dumps};
use lp_py::{Dict, Value};
use num_traits::ToPrimitive;

use crate::lhm::LhmSensor;

pub const PROTOCOL: i64 = 1;
/// the length of the message that follows
pub const HEADER: usize = 4;
/// bytes: a request names at most MAX_HARDWARE pieces of hardware
pub const MAX_REQUEST: usize = 4096;
/// bytes: some thousand sensors fit many times over
pub const MAX_ANSWER: usize = 4 * 1024 * 1024;
/// pieces of hardware one request may name
pub const MAX_HARDWARE: usize = 64;
/// sensors one answer may carry
pub const MAX_SENSORS: usize = 10_000;
/// characters of a name, a label, a version or an error
pub const MAX_TEXT: usize = 300;
/// parts one round's times may name
pub const MAX_TIMED: usize = 256;
/// what the service says it holds is less than this
pub const MAX_BYTES: u64 = 1 << 48;
pub const STATES: [&str; 3] = ["starting", "running", "failed"];

/// Why an answer was not taken.
#[derive(Clone, Debug, PartialEq)]
pub enum Error {
    /// a message that is not one of this protocol; the text says what is wrong with it
    Protocol(String),
    /// the service understood the request and said no (`{"ok": false}`)
    Service(String),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::Protocol(why) | Error::Service(why) => f.write_str(why),
        }
    }
}

fn wrong<T>(why: impl Into<String>) -> Result<T, Error> {
    Err(Error::Protocol(why.into()))
}

// -- framing ----------------------------------------------------------------------------------

/// encode: `message` with its length in front, ready to be written; an error when it is longer
/// than `limit` bytes.
pub fn encode(message: &Value, limit: usize) -> Result<Vec<u8>, Error> {
    let how = Dumps {
        ensure_ascii: false,
        separators: Some((",".into(), ":".into())),
        ..Dumps::default()
    };
    let body = dumps(message, &how);
    if body.len() > limit {
        return wrong(format!("{} bytes, more than {limit}", body.len()));
    }
    let mut data = (body.len() as u32).to_le_bytes().to_vec();
    data.extend_from_slice(body.as_bytes());
    Ok(data)
}

/// body_length: the length that four bytes announce; an error when it is none or above
/// `limit` (then the connection cannot go on: nobody knows where the next message starts).
pub fn body_length(header: &[u8], limit: usize) -> Result<usize, Error> {
    let Ok(bytes) = <[u8; HEADER]>::try_from(header) else {
        return wrong("the length is cut short");
    };
    let length = u32::from_le_bytes(bytes) as usize;
    if length == 0 || length > limit {
        return wrong(format!("a message of {length} bytes (at most {limit})"));
    }
    Ok(length)
}

/// Whether the JSON text has a number JSON does not have (NaN, Infinity) outside its texts.
fn has_constant(text: &str) -> bool {
    let mut in_text = false;
    let mut escaped = false;
    for ch in text.chars() {
        if in_text {
            match ch {
                _ if escaped => escaped = false,
                '\\' => escaped = true,
                '"' => in_text = false,
                _ => {}
            }
        } else if ch == '"' {
            in_text = true;
        } else if ch == 'N' || ch == 'I' {
            return true;
        }
    }
    false
}

/// decode: the JSON object in `body`; an error for anything else.
pub fn decode(body: &[u8]) -> Result<Dict, Error> {
    let Ok(text) = std::str::from_utf8(body) else {
        return wrong("not JSON: not UTF-8");
    };
    if has_constant(text) {
        return wrong("not JSON: a number JSON does not have");
    }
    match loads(text) {
        Ok(Value::Dict(message)) => Ok(message),
        Ok(_) => wrong("not a JSON object"),
        Err(e) => wrong(format!("not JSON: {e}")),
    }
}

// -- requests ---------------------------------------------------------------------------------

fn request(op: &str) -> Dict {
    let mut message = Dict::new();
    message.insert("v".into(), Value::int(PROTOCOL));
    message.insert("op".into(), Value::Str(op.into()));
    message
}

pub fn hello_request() -> Value {
    Value::Dict(request("hello"))
}

/// read_request: the readings; the service keeps `hardware` up to date for the next few
/// seconds (`everything`: every piece once).
pub fn read_request(hardware: &[String], everything: bool) -> Value {
    let mut wanted: Vec<&String> = Vec::new();
    for id in hardware {
        if !wanted.contains(&id) {
            wanted.push(id);
        }
    }
    wanted.truncate(MAX_HARDWARE);
    let mut message = request("read");
    message.insert(
        "hardware".into(),
        Value::List(
            wanted
                .into_iter()
                .map(|id| Value::Str(id.clone()))
                .collect(),
        ),
    );
    message.insert("all".into(), Value::Bool(everything));
    Value::Dict(message)
}

// -- answers ----------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
pub struct Hello {
    pub service: String,
    pub protocol: i64,
}

/// How long one piece of hardware took in a round.
#[derive(Clone, Debug, PartialEq)]
pub struct PartTime {
    pub hardware_id: String,
    pub name: String,
    /// LibreHardwareMonitor's HardwareType: Cpu, GpuNvidia, SuperIO, Network ...
    pub kind: String,
    pub seconds: f64,
}

/// One round of updates, the parts in the order they were updated. `full`: it updated all
/// hardware, not only what the themes on show read.
#[derive(Clone, Debug, PartialEq, Default)]
pub struct RoundTime {
    pub parts: Vec<PartTime>,
    pub full: bool,
}

impl RoundTime {
    pub fn seconds(&self) -> f64 {
        lp_py::sum_floats(self.parts.iter().map(|part| part.seconds))
    }
}

/// What the rounds of the service cost: the last one, and the last that updated all hardware.
#[derive(Clone, Debug, PartialEq, Default)]
pub struct Timing {
    pub last: Option<RoundTime>,
    pub full: Option<RoundTime>,
}

/// What the sensor service holds, as it says itself; none where it does not know.
#[derive(Clone, Debug, PartialEq, Default)]
pub struct ServiceMemory {
    pub private: Option<u64>,
    pub working_set: Option<u64>,
    pub managed: Option<u64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Readings {
    /// `starting`, `running` or `failed` (then `error` says why)
    pub state: String,
    pub error: String,
    /// how many seconds ago the sensors were read (none before the first time)
    pub age: Option<f64>,
    pub ring0: Option<bool>,
    pub pawnio: Option<String>,
    pub sensors: Vec<LhmSensor>,
    /// none: the service does not say (one older than this)
    pub timing: Option<Timing>,
    pub memory: Option<ServiceMemory>,
}

fn text(value: Option<&Value>, what: &str, empty: bool) -> Result<String, Error> {
    let Some(Value::Str(found)) = value else {
        return wrong(format!(
            "{what} is not a text of at most {MAX_TEXT} characters"
        ));
    };
    if found.chars().count() > MAX_TEXT || found.contains('\0') {
        return wrong(format!(
            "{what} is not a text of at most {MAX_TEXT} characters"
        ));
    }
    if !empty && found.is_empty() {
        return wrong(format!("{what} is empty"));
    }
    Ok(found.clone())
}

fn number(value: Option<&Value>, what: &str) -> Result<f64, Error> {
    let found = match value {
        Some(Value::Float(f)) => *f,
        Some(Value::Int(n)) => match lp_py::num::int_to_float(n) {
            Ok(f) => f,
            Err(_) => return wrong(format!("{what} is too large")),
        },
        _ => return wrong(format!("{what} is not a number")),
    };
    if !found.is_finite() {
        return wrong(format!("{what} is not a finite number"));
    }
    Ok(found)
}

fn checked(message: &Dict) -> Result<(), Error> {
    let version = message.get("v");
    let ours = matches!(version, Some(v) if !matches!(v, Value::Str(_) | Value::List(_) | Value::Dict(_) | Value::None)
        && lp_py::value::compare(v, &Value::int(PROTOCOL)) == Some(std::cmp::Ordering::Equal));
    if !ours {
        let said = version.map_or("None".to_string(), Value::repr);
        return wrong(format!("protocol {said} is not {PROTOCOL}"));
    }
    match message.get("ok") {
        Some(Value::Bool(false)) => {
            let empty = Value::Str(String::new());
            let why = text(Some(message.get("error").unwrap_or(&empty)), "error", true)?;
            Err(Error::Service(why))
        }
        Some(Value::Bool(true)) => Ok(()),
        _ => wrong("ok is true or false"),
    }
}

pub fn parse_hello(message: &Dict) -> Result<Hello, Error> {
    checked(message)?;
    let Some(Value::Int(protocol)) = message.get("protocol") else {
        return wrong("protocol is not a whole number");
    };
    Ok(Hello {
        service: text(message.get("service"), "service", false)?,
        // (a number of another size is not this protocol's either way)
        protocol: protocol.to_i64().unwrap_or(i64::MIN),
    })
}

fn sensor(row: &Value) -> Result<LhmSensor, Error> {
    let Some(row) = row.as_list().filter(|row| row.len() == 7) else {
        return wrong("a sensor is a list of seven values");
    };
    Ok(LhmSensor {
        sensor_id: text(row.first(), "a sensor id", false)?,
        hardware_id: text(row.get(1), "a hardware id", false)?,
        hardware_name: text(row.get(2), "a hardware name", true)?,
        sensor_type: text(row.get(3), "a sensor type", false)?,
        label: text(row.get(4), "a label", true)?,
        value: number(row.get(5), "a value")?,
        unit: text(row.get(6), "a unit", true)?,
    })
}

fn part(row: &Value) -> Result<PartTime, Error> {
    let Some(row) = row.as_list().filter(|row| row.len() == 4) else {
        return wrong("a part's time is a list of four values");
    };
    let seconds = number(row.get(3), "a part's seconds")?;
    if seconds < 0.0 {
        return wrong("a part's seconds are not below zero");
    }
    Ok(PartTime {
        hardware_id: text(row.first(), "a hardware id", false)?,
        name: text(row.get(1), "a hardware name", true)?,
        kind: text(row.get(2), "a hardware type", false)?,
        seconds,
    })
}

fn round(value: Option<&Value>) -> Result<Option<RoundTime>, Error> {
    let found = match value {
        None | Some(Value::None) => return Ok(None),
        Some(Value::Dict(found)) => found,
        Some(_) => return wrong("a round's times are an object or null"),
    };
    let Some(Value::Bool(full)) = found.get("full") else {
        return wrong("full is true or false");
    };
    let Some(parts) = found
        .get("parts")
        .and_then(Value::as_list)
        .filter(|parts| parts.len() <= MAX_TIMED)
    else {
        return wrong(format!("parts is a list of at most {MAX_TIMED}"));
    };
    Ok(Some(RoundTime {
        parts: parts.iter().map(part).collect::<Result<_, _>>()?,
        full: *full,
    }))
}

/// parse_timing: what the service's rounds cost; none where it does not say.
pub fn parse_timing(value: Option<&Value>) -> Result<Option<Timing>, Error> {
    match value {
        None | Some(Value::None) => Ok(None),
        Some(Value::Dict(found)) => Ok(Some(Timing {
            last: round(found.get("last"))?,
            full: round(found.get("full"))?,
        })),
        Some(_) => wrong("timing is an object"),
    }
}

fn bytes(value: Option<&Value>, what: &str) -> Result<Option<u64>, Error> {
    match value {
        None | Some(Value::None) => Ok(None),
        Some(Value::Int(n)) => match n.to_u64().filter(|n| *n < MAX_BYTES) {
            Some(n) => Ok(Some(n)),
            None => wrong(format!("{what} is a number of bytes or null")),
        },
        Some(_) => wrong(format!("{what} is a number of bytes or null")),
    }
}

/// parse_memory: what the service holds; none where it does not say.
pub fn parse_memory(value: Option<&Value>) -> Result<Option<ServiceMemory>, Error> {
    match value {
        None | Some(Value::None) => Ok(None),
        Some(Value::Dict(found)) => Ok(Some(ServiceMemory {
            private: bytes(found.get("private"), "private")?,
            working_set: bytes(found.get("working_set"), "working_set")?,
            managed: bytes(found.get("managed"), "managed")?,
        })),
        Some(_) => wrong("memory is an object"),
    }
}

/// parse_readings: the answer to `read`, checked whole: one thing wrong in it and none of it
/// is taken.
pub fn parse_readings(message: &Dict) -> Result<Readings, Error> {
    checked(message)?;
    let state = match message.get("state") {
        Some(Value::Str(state)) if STATES.contains(&state.as_str()) => state.clone(),
        _ => return wrong(format!("state is one of {}", STATES.join(", "))),
    };
    let age = match message.get("age") {
        None | Some(Value::None) => None,
        found => Some(number(found, "age")?),
    };
    let ring0 = match message.get("ring0") {
        None | Some(Value::None) => None,
        Some(Value::Bool(found)) => Some(*found),
        Some(_) => return wrong("ring0 is true, false or null"),
    };
    let pawnio = match message.get("pawnio") {
        None | Some(Value::None) => None,
        found => Some(text(found, "pawnio", true)?),
    };
    let Some(rows) = message
        .get("sensors")
        .and_then(Value::as_list)
        .filter(|rows| rows.len() <= MAX_SENSORS)
    else {
        return wrong(format!("sensors is a list of at most {MAX_SENSORS}"));
    };
    let empty = Value::Str(String::new());
    Ok(Readings {
        state,
        error: text(Some(message.get("error").unwrap_or(&empty)), "error", true)?,
        age,
        ring0,
        pawnio,
        sensors: rows.iter().map(sensor).collect::<Result<_, _>>()?,
        timing: parse_timing(message.get("timing"))?,
        memory: parse_memory(message.get("memory"))?,
    })
}
