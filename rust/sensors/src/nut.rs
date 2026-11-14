//! The UPS, through Network UPS Tools (NUT): its battery, how long it lasts, its load and
//! whether the PC runs on it (sensors/nut.py). NUT's server (`upsd`, on this PC, a NAS or a
//! Raspberry Pi, and WinNUT's on Windows) says it to anyone on its port.
//!
//! Readings: `ups.charge` (%), `ups.runtime` (min), `ups.load` (%), `ups.power` (W, as the UPS
//! says, or its load of the nominal power), `ups.input` (V), `ups.on_battery` (1 while the
//! mains is off) and `ups.status`. A UPS that does not answer keeps its last values for three
//! times `update_s`, then they go. It is asked in a thread of its own.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use lp_py::Value;
use lp_render::Reading;

use crate::lhm::Readings;

pub const PORT: u16 = 3493;
const TIMEOUT_S: f64 = 5.0;
/// a UPS has some 50 to 150 values
const MAX_LINES: usize = 500;

/// shlex.split(line): the words of a line as a POSIX shell splits it (quotes, backslashes);
/// an error for a quote that does not close or a backslash at the end.
pub fn shlex_split(line: &str) -> Result<Vec<String>, String> {
    #[derive(Clone, Copy, PartialEq)]
    enum State {
        Between,
        Word,
        Quote(char),
        Escape,
    }
    let space = |ch: char| matches!(ch, ' ' | '\t' | '\r' | '\n');
    let mut words = Vec::new();
    let mut token = String::new();
    let mut quoted = false;
    let mut state = State::Between;
    // (where an escape goes back to)
    let mut escaped = State::Word;
    for ch in line.chars() {
        match state {
            State::Between | State::Word => {
                if space(ch) {
                    if state == State::Word && (!token.is_empty() || quoted) {
                        words.push(std::mem::take(&mut token));
                        quoted = false;
                    }
                    state = State::Between;
                } else if ch == '\\' {
                    escaped = State::Word;
                    state = State::Escape;
                } else if ch == '\'' || ch == '"' {
                    state = State::Quote(ch);
                    quoted = true;
                } else {
                    token.push(ch);
                    state = State::Word;
                }
            }
            State::Quote(quote) => {
                if ch == quote {
                    state = State::Word;
                } else if ch == '\\' && quote == '"' {
                    escaped = state;
                    state = State::Escape;
                } else {
                    token.push(ch);
                }
            }
            State::Escape => {
                // (in double quotes a backslash stays before all but a quote and itself)
                if let State::Quote(quote) = escaped {
                    if ch != '\\' && ch != quote {
                        token.push('\\');
                    }
                }
                token.push(ch);
                state = escaped;
            }
        }
    }
    match state {
        State::Quote(_) => return Err("No closing quotation".into()),
        State::Escape => return Err("No escaped character".into()),
        State::Word if !token.is_empty() || quoted => words.push(token),
        _ => {}
    }
    Ok(words)
}

/// One talk with upsd: a line out, lines back (the protocol is text, a line each).
pub struct Client {
    socket: TcpStream,
    file: BufReader<TcpStream>,
}

impl Client {
    pub fn connect(host: &str, port: u16) -> Result<Client, String> {
        let timeout = Duration::from_secs_f64(TIMEOUT_S);
        let mut last = format!("no address of {host}");
        for address in (host, port)
            .to_socket_addrs()
            .map_err(|why| why.to_string())?
        {
            match TcpStream::connect_timeout(&address, timeout) {
                Ok(socket) => {
                    let set = socket
                        .set_read_timeout(Some(timeout))
                        .and_then(|()| socket.set_write_timeout(Some(timeout)));
                    let file = set.and_then(|()| socket.try_clone());
                    let file = file.map_err(|why| why.to_string())?;
                    return Ok(Client {
                        socket,
                        file: BufReader::new(file),
                    });
                }
                Err(why) => last = why.to_string(),
            }
        }
        Err(last)
    }

    pub fn ask(&mut self, line: &str) -> Result<String, String> {
        let sent = self.socket.write_all(format!("{line}\n").as_bytes());
        sent.map_err(|why| why.to_string())?;
        self.line()
    }

    fn line(&mut self) -> Result<String, String> {
        let mut raw = Vec::new();
        let read = (&mut self.file).take(4096).read_until(b'\n', &mut raw);
        read.map_err(|why| why.to_string())?;
        if raw.is_empty() {
            return Err("upsd closed the connection".into());
        }
        let text = String::from_utf8_lossy(&raw);
        let text = text.trim_end_matches(['\r', '\n']);
        match text.strip_prefix("ERR ") {
            Some(why) => Err(why.to_string()),
            None => Ok(text.to_string()),
        }
    }

    /// `LIST <what>`: the lines between BEGIN and END, each split as the shell splits.
    pub fn listing(&mut self, what: &str) -> Result<Vec<Vec<String>>, String> {
        let first = self.ask(&format!("LIST {what}"))?;
        if first != format!("BEGIN LIST {what}") {
            return Err(format!("unexpected answer {}", lp_py::repr(&first)));
        }
        let mut rows = Vec::new();
        for _ in 0..MAX_LINES {
            let line = self.line()?;
            if line == format!("END LIST {what}") {
                return Ok(rows);
            }
            rows.push(shlex_split(&line)?);
        }
        Err("too long an answer".into())
    }

    pub fn close(&mut self) {
        let _ = self.socket.write_all(b"LOGOUT\n");
        let _ = self.socket.shutdown(std::net::Shutdown::Both);
    }
}

/// The readings of a UPS from NUT's variables (`battery.charge` and so on).
pub fn readings(values: &HashMap<String, String>) -> Readings {
    let number = |name: &str| values.get(name).and_then(|text| lp_py::float_of_str(text));
    let mut out = Readings::new();
    let mut put = |key: &str, value: Option<Value>, unit: &str, label: &str| {
        if let Some(value) = value {
            let reading = Reading {
                unit: unit.into(),
                label: label.into(),
                ..Reading::of(value)
            };
            out.insert(key.to_string(), reading);
        }
    };
    let float = Value::Float;
    put(
        "ups.charge",
        number("battery.charge").map(float),
        "%",
        "UPS battery",
    );
    let runtime = number("battery.runtime").map(|s| lp_py::round_digits(s / 60.0, 1));
    put("ups.runtime", runtime.map(float), "min", "UPS runtime");
    let load = number("ups.load");
    put("ups.load", load.map(float), "%", "UPS load");
    let power = match (
        number("ups.realpower"),
        number("ups.realpower.nominal"),
        load,
    ) {
        (Some(power), _, _) => Some(float(power)),
        (None, Some(nominal), Some(load)) => lp_py::num::round_float(nominal * load / 100.0)
            .ok()
            .map(Value::Int),
        _ => None,
    };
    put("ups.power", power, "W", "UPS power");
    put(
        "ups.input",
        number("input.voltage").map(float),
        "V",
        "UPS input",
    );
    let status = values.get("ups.status").map_or("", String::as_str);
    let flags: Vec<&str> = status
        .split(lp_py::isspace)
        .filter(|flag| !flag.is_empty())
        .collect();
    if !flags.is_empty() {
        let on_battery = flags.contains(&"OB");
        let flag = Value::int(i64::from(on_battery));
        put("ups.on_battery", Some(flag), "", "UPS on battery");
        let text = if flags.contains(&"LB") {
            lp_i18n::t("Battery low")
        } else if on_battery {
            lp_i18n::t("On battery")
        } else {
            lp_i18n::t("On mains")
        };
        put("ups.status", Some(Value::Str(text)), "", "UPS");
    }
    out
}

/// `[sensors.nut]`.
#[derive(Clone, Debug, PartialEq)]
pub struct Options {
    /// where upsd runs
    pub host: String,
    pub port: u16,
    /// its name in ups.conf ("": the first it has)
    pub ups: String,
    pub update_s: f64,
    /// only where upsd wants one to read (upsd.users)
    pub username: String,
    pub password: String,
}

impl Default for Options {
    fn default() -> Options {
        Options {
            host: "127.0.0.1".into(),
            port: PORT,
            ups: String::new(),
            update_s: 5.0,
            username: String::new(),
            password: String::new(),
        }
    }
}

/// What upsd says of the UPS now: its variables by their names.
pub fn fetch(options: &Options) -> Result<HashMap<String, String>, String> {
    let mut client = Client::connect(&options.host, options.port)?;
    let mut talk = || -> Result<Vec<Vec<String>>, String> {
        if !options.username.is_empty() {
            client.ask(&format!("USERNAME {}", options.username))?;
            client.ask(&format!("PASSWORD {}", options.password))?;
        }
        let mut name = options.ups.clone();
        if name.is_empty() {
            let found = client.listing("UPS")?;
            let first = found.first().ok_or("upsd knows no UPS")?;
            name = first.get(1).ok_or("list index out of range")?.clone();
        }
        client.listing(&format!("VAR {name}"))
    };
    let rows = talk();
    client.close();
    let mut values = HashMap::new();
    for row in rows? {
        if row.len() >= 4 && row[0] == "VAR" {
            values.insert(row[2].clone(), row[3].clone());
        }
    }
    Ok(values)
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

fn run(shared: &Shared, options: &Options, every: Duration) {
    // said once, until it answers again
    let mut failed = false;
    loop {
        let found = fetch(options);
        let Ok(mut state) = shared.state.lock() else {
            return;
        };
        let place = format!("{}:{}", options.host, options.port);
        match found {
            Ok(values) => {
                if std::mem::take(&mut failed) {
                    state.notes.push(format!("nut: {place} answers again"));
                }
                state.got = Some((Instant::now(), readings(&values)));
            }
            Err(why) if !failed => {
                failed = true;
                let note = format!("nut: {place} does not answer: {why}");
                state.notes.push(note);
            }
            Err(_) => {}
        }
        state.rounds += 1;
        shared.changed.notify_all();
        let until = Instant::now() + every;
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

/// `ups.*` from NUT's upsd (NutProvider).
pub struct NutSource {
    shared: Arc<Shared>,
    every: f64,
    thread: Option<JoinHandle<()>>,
}

impl NutSource {
    pub fn new(options: Options) -> NutSource {
        let every = options.update_s.max(1.0);
        let shared = Arc::new(Shared {
            state: Mutex::new(State::default()),
            changed: Condvar::new(),
        });
        let theirs = Arc::clone(&shared);
        let wait = Duration::from_secs_f64(every);
        let thread = std::thread::Builder::new()
            .name("sensors-nut".into())
            .spawn(move || run(&theirs, &options, wait))
            .ok();
        NutSource {
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

    /// Waits for upsd's first answer (or the first failure), `seconds` at most.
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

impl Drop for NutSource {
    fn drop(&mut self) {
        self.close();
    }
}
