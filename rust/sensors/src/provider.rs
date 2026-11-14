//! The source of LibreHardwareMonitor's readings (sensors/librehardwaremonitor.py's
//! LibreHardwareMonitorProvider): through Libre Panel's sensor service where it is installed,
//! else from the web server of a LibreHardwareMonitor that runs by itself.
//!
//! `mode`: "service" asks the sensor service only; "webserver" the web server only; "auto"
//! the service where it is installed, and the web server while the service does not answer
//! (and it is looked now and then whether the service was installed meanwhile). What the
//! Python app also has, LibreHardwareMonitor inside its own process ("inprocess"), is not in
//! this program: that needs .NET in the process, which the sensor service is there to spare.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, Weak};
use std::time::{Duration, Instant};

use lp_py::Value;

use crate::lhm::{readings_from_tree, Readings};
use crate::service::{LhmStatus, Link, Options, ServiceSource, Unavailable, Wanted};

pub const DEFAULT_URL: &str = "http://127.0.0.1:8085/data.json";
/// without an answer for this long the readings are dropped
pub const STALE_SECONDS: f64 = 5.0;
/// one request, connecting included: it answers in milliseconds or not at all
pub const REQUEST_SECONDS: f64 = 1.0;
/// the longest wait between two requests while nothing answers
pub const BACKOFF_SECONDS: f64 = 30.0;
/// auto without the sensor service: how often to look for it
pub const SERVICE_LOOK_SECONDS: f64 = 10.0;
pub const MODES: [&str; 4] = ["auto", "service", "inprocess", "webserver"];
const NOT_INSIDE: &str = "LibreHardwareMonitor does not run inside this program: the sensor \
                          service reads it (or its own web server)";

/// `[sensors.librehardwaremonitor]`.
#[derive(Clone, Debug, PartialEq)]
pub struct LhmOptions {
    pub url: String,
    pub poll_seconds: f64,
    /// 0 (or less) keeps the last readings however long the web server is silent
    pub stale_seconds: f64,
    pub gpu: Option<String>,
    pub mode: String,
}

impl Default for LhmOptions {
    fn default() -> LhmOptions {
        LhmOptions {
            url: DEFAULT_URL.into(),
            poll_seconds: 1.0,
            stale_seconds: STALE_SECONDS,
            gpu: None,
            mode: "auto".into(),
        }
    }
}

/// What the provider asks of the system: whether the sensor service is installed, and a
/// connection to it.
pub struct System {
    pub installed: Box<dyn Fn() -> bool + Send + Sync>,
    pub open: Arc<dyn Fn() -> Result<Box<dyn Link>, Unavailable> + Send + Sync>,
    /// without an answer of the service for this long, the web server is read meanwhile
    pub wait_seconds: f64,
    /// ... before the service's first answer since the program started
    pub start_wait_seconds: f64,
}

impl System {
    /// This computer's: Windows' record of the service and its pipe; none elsewhere.
    pub fn own() -> System {
        #[cfg(windows)]
        {
            System {
                installed: Box::new(crate::winlink::installed),
                open: Arc::new(crate::winlink::open_link),
                wait_seconds: crate::service::WAIT_SECONDS,
                start_wait_seconds: crate::service::START_WAIT_SECONDS,
            }
        }
        #[cfg(not(windows))]
        {
            System {
                installed: Box::new(|| false),
                open: Arc::new(|| {
                    Err(Unavailable("the sensor service is for Windows only".into()))
                }),
                wait_seconds: crate::service::WAIT_SECONDS,
                start_wait_seconds: crate::service::START_WAIT_SECONDS,
            }
        }
    }
}

// -- the web server ---------------------------------------------------------------------------

/// (host, port, path) of an `http://` address.
fn split_url(url: &str) -> Result<(String, u16, String), String> {
    let Some(rest) = url.strip_prefix("http://") else {
        return Err(format!("{url}: only http:// addresses are read"));
    };
    let (place, path) = match rest.find('/') {
        Some(at) => (&rest[..at], &rest[at..]),
        None => (rest, "/"),
    };
    let place = place.rsplit('@').next().unwrap_or(place);
    let (host, port) = if let Some(inner) = place.strip_prefix('[') {
        // ("[::1]:8085")
        let Some((host, after)) = inner.split_once(']') else {
            return Err(format!("{url}: not an address"));
        };
        (host, after.strip_prefix(':'))
    } else {
        match place.rsplit_once(':') {
            Some((host, port)) => (host, Some(port)),
            None => (place, None),
        }
    };
    let port = match port {
        None | Some("") => 80,
        Some(port) => port.parse().map_err(|_| format!("{url}: not an address"))?,
    };
    if host.is_empty() {
        return Err(format!("{url}: not an address"));
    }
    Ok((host.to_string(), port, path.to_string()))
}

/// The body of a response whose transfer encoding is chunked.
fn unchunked(mut data: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    loop {
        let Some(end) = data.windows(2).position(|pair| pair == b"\r\n") else {
            return Err("the answer is cut short".into());
        };
        let line = String::from_utf8_lossy(&data[..end]);
        let size = line.split(';').next().unwrap_or("").trim();
        let Ok(size) = usize::from_str_radix(size, 16) else {
            return Err("the answer is not as HTTP has it".into());
        };
        data = &data[end + 2..];
        if size == 0 {
            return Ok(out);
        }
        if data.len() < size + 2 {
            return Err("the answer is cut short".into());
        }
        out.extend_from_slice(&data[..size]);
        data = &data[size + 2..];
    }
}

/// fetch_tree: the web server's `data.json`, asked directly (no proxy: it is this PC's own);
/// an error when it does not answer within `timeout` seconds or answers something else.
pub fn fetch_tree(url: &str, timeout: f64) -> Result<lp_py::Dict, String> {
    let (host, port, path) = split_url(url)?;
    let deadline = Instant::now() + Duration::from_secs_f64(timeout);
    let left = |what: &str| {
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            Err(format!("timed out {what}"))
        } else {
            Ok(left)
        }
    };
    let address = (host.as_str(), port)
        .to_socket_addrs()
        .map_err(|e| e.to_string())?
        .next()
        .ok_or_else(|| format!("{host}: no such host"))?;
    let mut stream =
        TcpStream::connect_timeout(&address, left("connecting")?).map_err(|e| e.to_string())?;
    let named = if host.contains(':') {
        format!("[{host}]")
    } else {
        host.clone()
    };
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: {named}:{port}\r\nUser-Agent: libre-panel/{}\r\n\
         Accept-Encoding: identity\r\nConnection: close\r\n\r\n",
        lp_py::VERSION
    );
    stream
        .set_write_timeout(Some(left("asking")?))
        .map_err(|e| e.to_string())?;
    stream
        .write_all(request.as_bytes())
        .map_err(|e| e.to_string())?;
    let mut answer = Vec::new();
    let mut buffer = [0u8; 16 * 1024];
    loop {
        stream
            .set_read_timeout(Some(left("reading")?))
            .map_err(|e| e.to_string())?;
        match stream.read(&mut buffer) {
            Ok(0) => break,
            Ok(count) => answer.extend_from_slice(&buffer[..count]),
            Err(e)
                if matches!(
                    e.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) =>
            {
                return Err("timed out reading".into())
            }
            Err(e) => return Err(e.to_string()),
        }
        if answer.len() > 64 * 1024 * 1024 {
            return Err("the answer is too long".into());
        }
    }
    let Some(end) = answer.windows(4).position(|four| four == b"\r\n\r\n") else {
        return Err("the answer is not as HTTP has it".into());
    };
    let head = String::from_utf8_lossy(&answer[..end]).into_owned();
    let mut lines = head.split("\r\n");
    let status = lines.next().unwrap_or("");
    let code = status.split(' ').nth(1).unwrap_or("");
    if code != "200" {
        return Err(format!(
            "HTTP Error {}",
            status.split_once(' ').map_or(status, |s| s.1)
        ));
    }
    let chunked = lines.any(|line| {
        line.split_once(':').is_some_and(|(name, value)| {
            name.trim().eq_ignore_ascii_case("transfer-encoding")
                && value.to_ascii_lowercase().contains("chunked")
        })
    });
    let body = if chunked {
        unchunked(&answer[end + 4..])?
    } else {
        answer[end + 4..].to_vec()
    };
    let text = String::from_utf8(body).map_err(|e| e.to_string())?;
    match lp_py::json::loads(&text) {
        Ok(Value::Dict(tree)) => Ok(tree),
        Ok(_) => Err("the answer is no tree of sensors".into()),
        Err(e) => Err(e.to_string()),
    }
}

// -- the provider -----------------------------------------------------------------------------

#[derive(Default)]
struct Web {
    latest: Readings,
    /// when the web server last answered
    answered_at: Option<Instant>,
}

struct State {
    service: Option<ServiceSource>,
    /// auto: the service does not answer, the web server is read meanwhile
    fallback: bool,
    /// why the service does not answer, once it was given up for now
    silent: String,
    /// auto: when it was last looked whether the service is installed
    looked: Instant,
    /// the poller of the web server is no longer wanted when this is set
    web_off: Option<Arc<AtomicBool>>,
    /// requests in a row that nobody answered while no readings were shown
    misses: u32,
    /// said that the web server is silent; says so when it answers again
    warned: bool,
}

struct Inner {
    options: LhmOptions,
    wanted: Wanted,
    system: System,
    state: Mutex<State>,
    web: Mutex<Web>,
    stop: AtomicBool,
    notes: Mutex<Vec<String>>,
}

impl Inner {
    fn note(&self, line: String) {
        if let Ok(mut notes) = self.notes.lock() {
            notes.push(line);
        }
    }

    fn stopped(&self) -> bool {
        self.stop.load(Ordering::Relaxed)
    }
}

/// LibreHardwareMonitor's readings, as config.toml's `[sensors.librehardwaremonitor]` asks
/// for them.
pub struct Provider {
    inner: Arc<Inner>,
}

fn start_service(inner: &Arc<Inner>) {
    let (ready, failure, log) = (
        Arc::downgrade(inner),
        Arc::downgrade(inner),
        Arc::downgrade(inner),
    );
    let options = Options {
        // twice a round of the service: no reading is skipped, none is late by more
        poll_seconds: inner.options.poll_seconds / 2.0,
        stale_seconds: inner.options.stale_seconds,
        gpu: inner.options.gpu.clone(),
        wanted: inner.wanted.clone(),
        wait_seconds: inner.system.wait_seconds,
        start_wait_seconds: inner.system.start_wait_seconds,
        on_ready: Some(Box::new(move |_status: &LhmStatus| {
            if let Some(inner) = ready.upgrade() {
                service_ready(&inner);
            }
        })),
        on_failure: Some(Box::new(move |why: &str| {
            if let Some(inner) = failure.upgrade() {
                service_failed(&inner, why);
            }
        })),
        log: Some(Box::new(move |line: &str| {
            if let Some(inner) = log.upgrade() {
                inner.note(line.to_string());
            }
        })),
    };
    let open = inner.system.open.clone();
    let Ok(mut state) = inner.state.lock() else {
        return;
    };
    if inner.stopped() {
        return; // (closed meanwhile: nothing more to start)
    }
    state.service = Some(ServiceSource::start(options, Box::new(move || open())));
}

/// The service has not answered for a while: in `auto`, the web server is read until it does
/// (the status says why). One that was removed is let go, as if it had never been there (and
/// looked for again).
fn service_failed(inner: &Arc<Inner>, why: &str) {
    if inner.options.mode != "auto" {
        return;
    }
    let removed = !(inner.system.installed)();
    let gone = {
        let Ok(mut state) = inner.state.lock() else {
            return;
        };
        if inner.stopped() {
            return;
        }
        if removed {
            state.fallback = false;
            state.silent.clear();
            state.looked = Instant::now();
            state.service.take()
        } else {
            state.silent = why.to_string();
            if state.fallback {
                return;
            }
            state.fallback = true;
            None
        }
    };
    if removed {
        inner.note(
            "the sensor service was removed: LibreHardwareMonitor is read another way".into(),
        );
        drop(gone); // (from its own thread: it ends when this returns)
    } else {
        inner.note(format!(
            "the sensor service does not answer ({why}): LibreHardwareMonitor is read another \
             way until it does"
        ));
    }
    start_web(inner);
}

/// The service answers (again): what read meanwhile stops.
fn service_ready(inner: &Arc<Inner>) {
    {
        let Ok(mut state) = inner.state.lock() else {
            return;
        };
        state.silent.clear();
        if !state.fallback {
            return;
        }
        state.fallback = false;
    }
    stop_web(inner);
    inner.note("the sensor service answers again: LibreHardwareMonitor is read through it".into());
}

fn start_web(inner: &Arc<Inner>) {
    let Ok(mut state) = inner.state.lock() else {
        return;
    };
    if state.web_off.is_some() || inner.stopped() {
        return;
    }
    let off = Arc::new(AtomicBool::new(false));
    state.web_off = Some(off.clone());
    let weak = Arc::downgrade(inner);
    let _ = std::thread::Builder::new()
        .name("lhm-poll".into())
        .spawn(move || poll(&weak, &off));
}

/// The poller ends at its next turn, and what it read is forgotten.
fn stop_web(inner: &Arc<Inner>) {
    if let Ok(mut state) = inner.state.lock() {
        match state.web_off.take() {
            Some(off) => off.store(true, Ordering::Relaxed),
            None => return,
        }
    }
    if let Ok(mut web) = inner.web.lock() {
        *web = Web::default();
    }
}

fn poll(inner: &Weak<Inner>, off: &AtomicBool) {
    loop {
        let Some(inner) = inner.upgrade() else {
            return;
        };
        if inner.stopped() || off.load(Ordering::Relaxed) {
            return;
        }
        let started = Instant::now();
        let delay = poll_once(&inner, off);
        drop(inner);
        let wait = (delay - started.elapsed().as_secs_f64()).max(0.1);
        // (in short naps, so that closing does not wait for a long pause)
        let until = Instant::now() + Duration::from_secs_f64(wait);
        while Instant::now() < until && !off.load(Ordering::Relaxed) {
            let nap = until.saturating_duration_since(Instant::now());
            std::thread::sleep(nap.min(Duration::from_millis(100)));
        }
    }
}

/// One request; the seconds to wait before the next.
fn poll_once(inner: &Arc<Inner>, off: &AtomicBool) -> f64 {
    let options = &inner.options;
    let found = fetch_tree(&options.url, REQUEST_SECONDS)
        .map(|tree| readings_from_tree(&tree, options.gpu.as_deref()));
    let now = Instant::now();
    match found {
        Ok(readings) => {
            if off.load(Ordering::Relaxed) {
                return options.poll_seconds; // (no longer wanted: nothing is kept)
            }
            if let Ok(mut web) = inner.web.lock() {
                web.latest = readings;
                web.answered_at = Some(now);
            }
            if let Ok(mut state) = inner.state.lock() {
                state.misses = 0;
                if state.warned {
                    state.warned = false;
                    inner.note(format!(
                        "LibreHardwareMonitor answers again at {}: its readings are back",
                        options.url
                    ));
                }
            }
            options.poll_seconds
        }
        Err(why) => unanswered(inner, &why, now),
    }
}

/// A short silence keeps the last readings (one slow answer is no reason to blank the panel);
/// a long one drops them, so numbers never stand still unseen. With nothing to show, the
/// requests come less and less often.
fn unanswered(inner: &Arc<Inner>, why: &str, now: Instant) -> f64 {
    let options = &inner.options;
    let (shown, expired, silent, answered) = {
        let Ok(mut web) = inner.web.lock() else {
            return options.poll_seconds;
        };
        let shown = !web.latest.is_empty();
        let silent = web
            .answered_at
            .map_or(0.0, |at| now.duration_since(at).as_secs_f64());
        let expired = shown && 0.0 < options.stale_seconds && options.stale_seconds <= silent;
        if expired {
            web.latest = Readings::new();
        }
        (shown, expired, silent, web.answered_at.is_some())
    };
    let Ok(mut state) = inner.state.lock() else {
        return options.poll_seconds;
    };
    if !state.warned {
        // (said once; a short silence says nothing yet)
        let line = if !answered {
            Some(format!(
                "LibreHardwareMonitor does not answer at {} ({why}): temperature, power and GPU \
                 readings need it, run as administrator with Options -> Remote Web Server -> Run",
                options.url
            ))
        } else if expired {
            Some(format!(
                "LibreHardwareMonitor has not answered at {} for {silent:.0} s ({why}): its \
                 readings are hidden until it does",
                options.url
            ))
        } else if options.stale_seconds <= 0.0 {
            Some(format!(
                "LibreHardwareMonitor not reachable at {}: {why}",
                options.url
            ))
        } else {
            None
        };
        if let Some(line) = line {
            state.warned = true;
            inner.note(line);
        }
    }
    if shown && !expired {
        return options.poll_seconds; // their time is not up: ask as usual, they go in time
    }
    state.misses += 1;
    let wait = options.poll_seconds * f64::from(1u32 << (state.misses - 1).min(10));
    options.poll_seconds.max(BACKOFF_SECONDS.min(wait))
}

impl Provider {
    /// The provider started: `wanted` are the keys the themes on show read.
    pub fn new(options: LhmOptions, wanted: Wanted, system: System) -> Provider {
        let mut options = options;
        let mut notes = Vec::new();
        let mode = options.mode.to_lowercase();
        if MODES.contains(&mode.as_str()) {
            options.mode = mode;
        } else {
            notes.push(format!(
                "[sensors.librehardwaremonitor] mode must be \"auto\", \"service\", \
                 \"inprocess\" or \"webserver\", not {:?}: using \"auto\"",
                options.mode
            ));
            options.mode = "auto".into();
        }
        if options.mode == "inprocess" {
            notes.push(format!(
                "LibreHardwareMonitor cannot run inside Libre Panel: {NOT_INSIDE}"
            ));
        }
        let inner = Arc::new(Inner {
            options,
            wanted,
            system,
            state: Mutex::new(State {
                service: None,
                fallback: false,
                silent: String::new(),
                looked: Instant::now(),
                web_off: None,
                misses: 0,
                warned: false,
            }),
            web: Mutex::new(Web::default()),
            stop: AtomicBool::new(false),
            notes: Mutex::new(notes),
        });
        let mode = inner.options.mode.as_str();
        if mode == "service" || (mode == "auto" && (inner.system.installed)()) {
            start_service(&inner);
        } else if mode != "inprocess" {
            start_web(&inner);
        }
        Provider { inner }
    }

    /// `auto` without the service: was it installed meanwhile (the setup)? Then it takes over
    /// as soon as it answers. A look at the registry, now and then.
    fn look_for_service(&self) {
        let inner = &self.inner;
        {
            let Ok(mut state) = inner.state.lock() else {
                return;
            };
            if state.looked.elapsed().as_secs_f64() < SERVICE_LOOK_SECONDS {
                return;
            }
            state.looked = Instant::now();
        }
        if !(inner.system.installed)() {
            return;
        }
        {
            let Ok(mut state) = inner.state.lock() else {
                return;
            };
            if inner.stopped() || state.service.is_some() {
                return;
            }
            state.fallback = true; // what reads now goes on until the service answers
        }
        inner.note(
            "the sensor service is installed now: it takes over as soon as it answers".into(),
        );
        start_service(inner);
    }

    /// The readings of now.
    pub fn read(&self) -> Readings {
        let inner = &self.inner;
        let (has_service, fallback) = {
            let Ok(state) = inner.state.lock() else {
                return Readings::new();
            };
            match &state.service {
                Some(service) => {
                    let readings = service.read();
                    if !readings.is_empty() || !state.fallback {
                        return readings;
                    }
                    (true, state.fallback)
                }
                None => (false, state.fallback),
            }
        };
        let _ = fallback;
        if !has_service && inner.options.mode == "auto" {
            self.look_for_service();
        }
        inner
            .web
            .lock()
            .map(|web| web.latest.clone())
            .unwrap_or_default()
    }

    /// How the readings come, and what keeps the CPU's from coming.
    pub fn status(&self) -> LhmStatus {
        let inner = &self.inner;
        let other = || {
            if inner.options.mode == "inprocess" {
                // (inside only: it is not there, and that is the problem)
                LhmStatus {
                    error: NOT_INSIDE.into(),
                    ..LhmStatus::of("inprocess")
                }
            } else {
                LhmStatus::of("webserver")
            }
        };
        let Ok(state) = inner.state.lock() else {
            return other();
        };
        if let Some(service) = &state.service {
            if !state.fallback || service.live() {
                return service.status();
            }
            if !state.silent.is_empty() {
                // given up for now: the web server is read, and the hints say why
                return LhmStatus {
                    service: state.silent.clone(),
                    ..other()
                };
            }
        }
        other()
    }

    /// Whether the first readings are in (or it has given up) within `timeout` seconds.
    pub fn wait_ready(&self, timeout: f64) -> bool {
        let end = Instant::now() + Duration::from_secs_f64(timeout.max(0.0));
        loop {
            let ready = {
                let Ok(state) = self.inner.state.lock() else {
                    return false;
                };
                match &state.service {
                    Some(service) if !state.fallback => service.wait_ready(0.0),
                    _ => {
                        state.web_off.is_none()
                            || self
                                .inner
                                .web
                                .lock()
                                .is_ok_and(|web| web.answered_at.is_some())
                            || state.misses > 0
                    }
                }
            };
            if ready {
                return true;
            }
            if Instant::now() >= end {
                return false;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    /// What the provider noted for the log since it was last asked.
    pub fn notes(&self) -> Vec<String> {
        self.inner
            .notes
            .lock()
            .map(|mut notes| std::mem::take(&mut *notes))
            .unwrap_or_default()
    }

    pub fn close(&self) {
        let service = {
            let Ok(mut state) = self.inner.state.lock() else {
                return;
            };
            self.inner.stop.store(true, Ordering::Relaxed);
            if let Some(off) = state.web_off.take() {
                off.store(true, Ordering::Relaxed);
            }
            state.service.take()
        };
        drop(service); // (its thread is the one that starts the other ways)
    }
}

impl Drop for Provider {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::PROTOCOL;
    use lp_py::Dict;
    use std::net::TcpListener;

    struct Fake {
        answers: Arc<AtomicBool>,
    }

    impl Link for Fake {
        fn request(&mut self, message: &Value, _timeout: f64) -> Result<Dict, Unavailable> {
            if !self.answers.load(Ordering::Relaxed) {
                return Err(Unavailable("the other side closed the pipe".into()));
            }
            let op = message
                .as_dict()
                .and_then(|m| m.get("op"))
                .and_then(Value::as_str)
                .unwrap_or("");
            let text = if op == "hello" {
                format!(r#"{{"v":1,"ok":true,"service":"0.8.1","protocol":{PROTOCOL}}}"#)
            } else {
                r#"{"v":1,"ok":true,"state":"running","error":"","age":0.1,"ring0":true,
                    "pawnio":"2.0.1","sensors":[["/amdcpu/0/temperature/2","/amdcpu/0",
                    "AMD Ryzen 7 7800X3D 8-Core Processor","Temperature","Core (Tctl/Tdie)",
                    61.5,"°C"]]}"#
                    .to_string()
            };
            match lp_py::json::loads(&text) {
                Ok(Value::Dict(message)) => Ok(message),
                _ => Err(Unavailable("the test's answer is no JSON".into())),
            }
        }
    }

    fn system(installed: bool, answers: &Arc<AtomicBool>) -> System {
        let answers = answers.clone();
        System {
            installed: Box::new(move || installed),
            open: Arc::new(move || {
                Ok(Box::new(Fake {
                    answers: answers.clone(),
                }) as Box<dyn Link>)
            }),
            wait_seconds: 0.3,
            start_wait_seconds: 0.3,
        }
    }

    fn value(readings: &Readings, key: &str) -> Option<f64> {
        match readings.get(key)?.value {
            Value::Float(f) => Some(f),
            _ => None,
        }
    }

    #[test]
    fn the_service_is_read_where_it_is_installed() {
        let answers = Arc::new(AtomicBool::new(true));
        let options = LhmOptions {
            poll_seconds: 0.2,
            ..LhmOptions::default()
        };
        let provider = Provider::new(options, Wanted::default(), system(true, &answers));
        assert!(provider.wait_ready(5.0));
        let readings = provider.read();
        assert_eq!(value(&readings, "cpu.temp"), Some(61.5));
        assert_eq!(
            readings.get("cpu.name").map(|r| r.value.str()),
            Some("Ryzen 7 7800X3D".to_string())
        );
        let status = provider.status();
        assert_eq!(
            (status.mode.as_str(), status.ring0, status.version.as_str()),
            ("service", Some(true), "0.8.1")
        );
        assert!(provider
            .notes()
            .iter()
            .any(|line| line.contains("through the sensor service (1 sensors)")));
        provider.close();
    }

    /// A web server as LibreHardwareMonitor's, with one graphics card's temperature; its
    /// port.
    fn web_server() -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { return };
                let mut asked = [0u8; 2048];
                let _ = stream.read(&mut asked);
                let body = r#"{"Text":"Sensor","Children":[{"Text":"NVIDIA GeForce RTX 4080",
                    "HardwareId":"/gpu-nvidia/0","Children":[{"Text":"GPU Core",
                    "SensorId":"/gpu-nvidia/0/temperature/0","Type":"Temperature",
                    "Value":"54,0 °C"}]}]}"#;
                // (in chunks, as LibreHardwareMonitor's server may send it)
                let (a, b) = body.as_bytes().split_at(40);
                let mut answer = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n".to_vec();
                for part in [a, b] {
                    answer.extend(format!("{:x}\r\n", part.len()).as_bytes());
                    answer.extend(part);
                    answer.extend(b"\r\n");
                }
                answer.extend(b"0\r\n\r\n");
                let _ = stream.write_all(&answer);
            }
        });
        port
    }

    fn until(what: impl Fn() -> bool) -> bool {
        let end = Instant::now() + Duration::from_secs(10);
        while Instant::now() < end {
            if what() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        false
    }

    #[test]
    fn the_web_server_is_read_while_the_service_is_silent() {
        let port = web_server();
        let answers = Arc::new(AtomicBool::new(false));
        let options = LhmOptions {
            url: format!("http://127.0.0.1:{port}/data.json"),
            poll_seconds: 0.2,
            ..LhmOptions::default()
        };
        let provider = Provider::new(options, Wanted::default(), system(true, &answers));
        assert!(until(|| value(&provider.read(), "gpu.temp") == Some(54.0)));
        let status = provider.status();
        assert_eq!(status.mode, "webserver");
        assert_eq!(status.service, "the other side closed the pipe");
        // (the service answers: it is read, and the web server no more)
        answers.store(true, Ordering::Relaxed);
        assert!(until(|| value(&provider.read(), "cpu.temp") == Some(61.5)));
        assert_eq!(value(&provider.read(), "gpu.temp"), None);
        assert_eq!(provider.status().mode, "service");
        let notes = provider.notes().join("\n");
        assert!(notes.contains("does not answer (the other side closed the pipe)"));
        assert!(notes.contains("answers again: LibreHardwareMonitor is read through it"));
        provider.close();
    }

    #[test]
    fn the_web_server_is_read_without_the_service() {
        let port = web_server();
        let answers = Arc::new(AtomicBool::new(true));
        let options = LhmOptions {
            url: format!("http://127.0.0.1:{port}/data.json"),
            poll_seconds: 0.2,
            ..LhmOptions::default()
        };
        let provider = Provider::new(options, Wanted::default(), system(false, &answers));
        assert!(provider.wait_ready(5.0));
        let readings = provider.read();
        assert_eq!(value(&readings, "gpu.temp"), Some(54.0));
        assert_eq!(provider.status().mode, "webserver");
        provider.close();
    }

    #[test]
    fn addresses_are_split() {
        assert_eq!(
            split_url(DEFAULT_URL),
            Ok(("127.0.0.1".into(), 8085, "/data.json".into()))
        );
        assert_eq!(
            split_url("http://[::1]:8085/x?y=1"),
            Ok(("::1".into(), 8085, "/x?y=1".into()))
        );
        assert_eq!(
            split_url("http://localhost"),
            Ok(("localhost".into(), 80, "/".into()))
        );
        assert!(split_url("https://127.0.0.1/").is_err());
    }
}
