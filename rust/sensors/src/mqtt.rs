//! MQTT: values your devices publish, as readings (sensors/mqtt.py).
//!
//! A message on `home/kitchen/temperature` becomes `mqtt.home.kitchen.temperature`. A JSON
//! object is taken apart: `{"temperature": 21.5, "humidity": 40}` on `zigbee2mqtt/sensor`
//! gives `mqtt.zigbee2mqtt.sensor.temperature` and `...humidity`.
//!
//! The broker is spoken to here as the Python app's client (paho) speaks to it: MQTT 3.1.1,
//! a clean session, the topics subscribed at quality 0, a ping every minute, and after a
//! lost connection another try after 1, 2, 4 ... 60 seconds.

use std::io::{Read, Write};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_net::stream::Stream;
use lp_py::Value;
use lp_render::Reading;

use crate::homeassistant::as_float;
use crate::lhm::Readings;

pub const MAX_KEYS: usize = 2000;
const KEEPALIVE_S: u16 = 60;
/// the largest packet read (a message's topic and payload)
const MAX_PACKET: usize = 1 << 20;

/// topic_matches: MQTT's wildcards, `+` one level, `#` the rest.
pub fn topic_matches(pattern: &str, topic: &str) -> bool {
    let want: Vec<&str> = pattern.split('/').collect();
    let have: Vec<&str> = topic.split('/').collect();
    for (i, part) in want.iter().enumerate() {
        if *part == "#" {
            return true;
        }
        if i >= have.len() || (*part != "+" && *part != have[i]) {
            return false;
        }
    }
    want.len() == have.len()
}

fn key_of(topic: &str) -> String {
    let parts: Vec<String> = topic
        .split('/')
        .map(|part| part.replace(['.', ' '], "_"))
        .collect();
    format!("mqtt.{}", parts.join("."))
}

/// _scalar: a value a reading can hold: a number, a text (a number where it reads as one),
/// true and false as 1 and 0.
fn scalar(value: &Value) -> Option<Value> {
    match value {
        Value::Bool(on) => Some(Value::Float(f64::from(u8::from(*on)))),
        Value::Int(_) | Value::Float(_) => as_float(value).map(Value::Float),
        Value::Str(text) => {
            let text = lp_py::strip(text);
            Some(match lp_py::float_of_str(text) {
                Some(number) => Value::Float(number),
                None => Value::Str(text.chars().take(200).collect()),
            })
        }
        _ => None,
    }
}

/// readings_from_message: what a message on `topic` gives; the unit of the first pattern of
/// `units` the topic matches.
pub fn readings_from_message(
    topic: &str,
    payload: &[u8],
    units: &IndexMap<String, String>,
) -> Readings {
    let unit = units
        .iter()
        .find(|(pattern, _)| topic_matches(pattern, topic))
        .map_or("", |(_, unit)| unit.as_str());
    let text = String::from_utf8_lossy(payload);
    let text = lp_py::strip(&text);
    let key = key_of(topic);
    let data = lp_py::json::loads(text).unwrap_or_else(|_| Value::Str(text.to_string()));
    let mut out = Readings::new();
    let mut put = |key: String, value: Value, label: String| {
        let reading = Reading {
            unit: unit.into(),
            label,
            ..Reading::of(value)
        };
        out.insert(key, reading);
    };
    match &data {
        Value::Dict(table) => {
            for (name, value) in table {
                if let Some(value) = scalar(value) {
                    let sub = format!("{key}.{}", name.replace(' ', "_"));
                    put(sub, value, format!("{topic} {name}"));
                }
            }
        }
        other => {
            if let Some(value) = scalar(other) {
                put(key, value, topic.to_string());
            }
        }
    }
    out
}

/// A text as MQTT sends one: its length in two bytes, then its bytes.
fn text_field(out: &mut Vec<u8>, text: &[u8]) {
    let length = text.len().min(usize::from(u16::MAX));
    out.extend((length as u16).to_be_bytes());
    out.extend(&text[..length]);
}

/// A packet: its first byte, the length of the rest as MQTT writes lengths, the rest.
fn packet(first: u8, body: &[u8]) -> Vec<u8> {
    let mut out = vec![first];
    let mut length = body.len();
    loop {
        let mut byte = (length % 128) as u8;
        length /= 128;
        if length > 0 {
            byte |= 0x80;
        }
        out.push(byte);
        if length == 0 {
            break;
        }
    }
    out.extend(body);
    out
}

/// CONNECT: MQTT 3.1.1, a clean session, a ping expected every `KEEPALIVE_S` seconds.
pub fn connect_packet(client: &str, username: &str, password: &str) -> Vec<u8> {
    let mut body = Vec::new();
    text_field(&mut body, b"MQTT");
    body.push(4); // the protocol's level: 3.1.1
    let mut flags = 0x02; // a clean session
    if !username.is_empty() {
        flags |= 0x80 | 0x40; // (paho sends the password with the name, empty or not)
    }
    body.push(flags);
    body.extend(KEEPALIVE_S.to_be_bytes());
    text_field(&mut body, client.as_bytes());
    if !username.is_empty() {
        text_field(&mut body, username.as_bytes());
        text_field(&mut body, password.as_bytes());
    }
    packet(0x10, &body)
}

/// SUBSCRIBE to one topic at quality 0, as the packet numbered `id`.
pub fn subscribe_packet(id: u16, topic: &str) -> Vec<u8> {
    let mut body = Vec::from(id.to_be_bytes());
    text_field(&mut body, topic.as_bytes());
    body.push(0);
    packet(0x82, &body)
}

/// What the broker sent.
#[derive(Clone, Debug, PartialEq)]
pub enum Incoming {
    /// CONNACK with its return code (0: accepted)
    Connected(u8),
    /// PUBLISH: the topic and the payload
    Message(String, Vec<u8>),
    /// SUBACK, PINGRESP and what else needs no answer
    Other,
}

/// The first whole packet of `data` and how many bytes it takes; none while it is not all
/// there; an error for one that is no MQTT.
pub fn parse_packet(data: &[u8]) -> Result<Option<(Incoming, usize)>, String> {
    let Some(&first) = data.first() else {
        return Ok(None);
    };
    let mut length = 0usize;
    let mut at = 1;
    loop {
        let Some(&byte) = data.get(at) else {
            return Ok(None);
        };
        length |= usize::from(byte & 0x7f) << (7 * (at - 1));
        at += 1;
        if byte & 0x80 == 0 {
            break;
        }
        if at > 4 {
            return Err("a packet's length of more than four bytes".into());
        }
    }
    if length > MAX_PACKET {
        return Err(format!("a packet of {length} bytes"));
    }
    let Some(body) = data.get(at..at + length) else {
        return Ok(None);
    };
    let incoming = match first >> 4 {
        2 => Incoming::Connected(*body.get(1).ok_or("a short CONNACK")?),
        3 => {
            let size = body.get(..2).ok_or("a short PUBLISH")?;
            let size = usize::from(u16::from_be_bytes([size[0], size[1]]));
            let topic = body.get(2..2 + size).ok_or("a short PUBLISH")?;
            // (a message of quality 1 or 2 carries its number after the topic)
            let skip = if first & 0x06 != 0 { 2 } else { 0 };
            let payload = body.get(2 + size + skip..).ok_or("a short PUBLISH")?;
            Incoming::Message(
                String::from_utf8_lossy(topic).into_owned(),
                payload.to_vec(),
            )
        }
        _ => Incoming::Other,
    };
    Ok(Some((incoming, at + length)))
}

/// `[sensors.mqtt]`.
#[derive(Clone, Debug, PartialEq)]
pub struct Options {
    pub host: String,
    pub port: u16,
    /// if the broker wants one
    pub username: String,
    pub password: String,
    pub tls: bool,
    pub topics: Vec<String>,
    /// by topic pattern
    pub units: IndexMap<String, String>,
}

impl Default for Options {
    fn default() -> Options {
        Options {
            host: "localhost".into(),
            port: 1883,
            username: String::new(),
            password: String::new(),
            tls: false,
            topics: Vec::new(),
            units: IndexMap::new(),
        }
    }
}

#[derive(Default)]
struct State {
    stop: bool,
    /// the broker took the topics, or the first try failed
    settled: bool,
    latest: Readings,
    notes: Vec<String>,
}

struct Shared {
    state: Mutex<State>,
    changed: Condvar,
}

impl Shared {
    fn stopped(&self) -> bool {
        self.state.lock().map(|state| state.stop).unwrap_or(true)
    }

    fn note(&self, text: String) {
        if let Ok(mut state) = self.state.lock() {
            state.notes.push(text);
        }
    }

    fn settle(&self) {
        if let Ok(mut state) = self.state.lock() {
            state.settled = true;
        }
        self.changed.notify_all();
    }
}

/// One connection to the broker, to its end: why it ended (none: asked to stop).
fn session(shared: &Shared, options: &Options, client: &str) -> Result<(), String> {
    let failed = |why: std::io::Error| why.to_string();
    let mut stream = Stream::connect(&options.host, options.port, options.tls, 10.0)
        .map_err(|why| why.to_string())?;
    stream
        .write_all(&connect_packet(
            client,
            &options.username,
            &options.password,
        ))
        .map_err(failed)?;
    stream.set_read_timeout(0.5).map_err(failed)?;
    let keepalive = Duration::from_secs(u64::from(KEEPALIVE_S));
    let mut sent = Instant::now();
    let mut heard = Instant::now();
    let mut data: Vec<u8> = Vec::new();
    let mut buffer = [0u8; 4096];
    let mut pending: Option<Readings> = None;
    loop {
        if shared.stopped() {
            let _ = stream.write_all(&[0xe0, 0]); // DISCONNECT
            stream.close();
            return Ok(());
        }
        if sent.elapsed() >= keepalive {
            stream.write_all(&[0xc0, 0]).map_err(failed)?; // PINGREQ
            sent = Instant::now();
        }
        if heard.elapsed() > keepalive + keepalive / 2 {
            return Err("the broker is silent".into());
        }
        match stream.read(&mut buffer) {
            Ok(0) => return Err("the broker closed the connection".into()),
            Ok(count) => {
                data.extend(&buffer[..count]);
                heard = Instant::now();
            }
            Err(why)
                if matches!(
                    why.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) => {}
            Err(why) if why.kind() == std::io::ErrorKind::Interrupted => {}
            Err(why) => return Err(why.to_string()),
        }
        let mut subscribed = false;
        while let Some((incoming, used)) = parse_packet(&data)? {
            data.drain(..used);
            match incoming {
                Incoming::Connected(0) => {
                    for (n, topic) in options.topics.iter().enumerate() {
                        let id = (n % 65535) as u16 + 1;
                        stream
                            .write_all(&subscribe_packet(id, topic))
                            .map_err(failed)?;
                    }
                    sent = Instant::now();
                    subscribed = true;
                }
                Incoming::Connected(code) => {
                    return Err(format!("the broker refused the connection ({code})"));
                }
                Incoming::Message(topic, payload) => {
                    let found = readings_from_message(&topic, &payload, &options.units);
                    pending.get_or_insert_with(Readings::new).extend(found);
                }
                Incoming::Other => {}
            }
        }
        if let Some(found) = pending.take() {
            if let Ok(mut state) = shared.state.lock() {
                let known = found.keys().all(|key| state.latest.contains_key(key));
                if state.latest.len() < MAX_KEYS || known {
                    state.latest.extend(found);
                }
            }
        }
        if subscribed {
            shared.settle();
        }
    }
}

fn run(shared: &Shared, options: &Options) {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |since| since.subsec_nanos());
    let client = format!("libre-panel-{:08x}", nanos ^ std::process::id());
    let mut delay = 1u64;
    // said once, until the broker answers again
    let mut failed = false;
    loop {
        let began = Instant::now();
        match session(shared, options, &client) {
            Ok(()) => return,
            Err(why) => {
                if !failed {
                    let place = format!("{}:{}", options.host, options.port);
                    shared.note(format!("MQTT: {place}: {why}"));
                    failed = true;
                }
            }
        }
        shared.settle();
        if began.elapsed() > Duration::from_secs(120) {
            // (it held for a while: start anew, and say it again if it fails)
            delay = 1;
            failed = false;
        }
        let Ok(mut state) = shared.state.lock() else {
            return;
        };
        let until = Instant::now() + Duration::from_secs(delay);
        delay = (delay * 2).min(60);
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

/// `mqtt.*` from a broker's messages (MqttProvider).
pub struct MqttSource {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl MqttSource {
    pub fn new(options: Options) -> MqttSource {
        let mut state = State::default();
        let idle = options.topics.is_empty();
        if idle {
            state.notes.push("MQTT: no topics in [sensors.mqtt]".into());
            state.settled = true;
        }
        let shared = Arc::new(Shared {
            state: Mutex::new(state),
            changed: Condvar::new(),
        });
        let mut thread = None;
        if !idle {
            let theirs = Arc::clone(&shared);
            thread = std::thread::Builder::new()
                .name("sensors-mqtt".into())
                .spawn(move || run(&theirs, &options))
                .ok();
        }
        MqttSource { shared, thread }
    }

    pub fn read(&mut self) -> Readings {
        self.shared
            .state
            .lock()
            .map(|state| state.latest.clone())
            .unwrap_or_default()
    }

    /// What it said since it was last asked.
    pub fn notes(&self) -> Vec<String> {
        self.shared
            .state
            .lock()
            .map(|mut state| std::mem::take(&mut state.notes))
            .unwrap_or_default()
    }

    /// Waits until the broker took the topics (or the first try failed), `seconds` at most.
    pub fn wait_ready(&self, seconds: f64) {
        let until = Instant::now() + Duration::from_secs_f64(seconds.max(0.0));
        let Ok(mut state) = self.shared.state.lock() else {
            return;
        };
        while !state.settled {
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

impl Drop for MqttSource {
    fn drop(&mut self) {
        self.close();
    }
}
