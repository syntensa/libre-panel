//! Libre Panel's engine with a TURZX panel of its own (devices/engine.py's EngineLink and
//! EngineTransport; `libre-panel-engine panel`, engine/src/panel.rs). The engine opens the
//! panel and is the only one to talk to it: it sends the commands it is given, as
//! devices/turzx_usb.py sends them, and the pictures it makes of the frames into the panel's
//! decoder, each on its time. The records it takes and gives: engine/src/wire.rs.

use std::collections::{HashMap, VecDeque};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::mpsc::{sync_channel, SyncSender};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use crate::base::{DeviceError, Result};

pub const MAGIC: &[u8; 4] = b"LPE1";
/// The engine's program, as it is named without its ending.
pub const ENGINE: &str = "libre-panel-engine";
/// How a line of the engine's for the log starts that is a note, no error.
pub const ENGINE_NOTE: &str = "libre-panel-engine (note): ";
// what the engine says (wire.rs)
const READY: u8 = 64;
const REPLY: u8 = 65;
const COUNTS: u8 = 66;
const FAILED: u8 = 67;
const WAITING: u8 = 68;
// what it is told
const FRAME: u8 = 1;
const RATE: u8 = 2;
const COMMAND: u8 = 4;
const STREAM: u8 = 5;
const END: u8 = 6;
const PLANNED: u8 = 7;
/// the frame of layers composed last, asked for (its reply: width, height and its RGB)
const SHOWN: u8 = 28;
/// A reply may wait for the picture being sent (5 s a block at most, 1.5 s for room in the
/// panel's queue, the hang check's readings): this long beyond a command's own time.
const REPLY_SLACK: Duration = Duration::from_secs(10);
/// How long the engine has to open the panel.
const READY_FOR: Duration = Duration::from_secs(10);

/// How the engine reads a command's reply (OPS).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Op {
    Command = 1,
    Ask = 2,
    Query = 3,
    Drain = 4,
    Push = 5,
}

/// The engine's video path stopped: `hung` where it found the panel's decoder hung (the text
/// is the depth its queue stood at).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Failure {
    pub hung: bool,
    pub text: String,
}

#[derive(Default)]
struct State {
    /// where the panel is, once the engine has it
    place: Option<String>,
    ended: bool,
    /// the replies asked for and not yet taken: none yet, or its status and bytes
    replies: HashMap<u32, Option<(u8, Vec<u8>)>>,
    counts: HashMap<String, f64>,
    failed: Option<Failure>,
    waiting: bool,
    errors: VecDeque<String>,
    notes: Vec<String>,
}

struct Shared {
    state: Mutex<State>,
    heard: Condvar,
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }
}

pub(crate) fn head(kind: u8, a: u32, b: u32) -> Vec<u8> {
    let mut record = MAGIC.to_vec();
    record.extend_from_slice(&[kind, 0, 0, 0]);
    record.extend_from_slice(&a.to_le_bytes());
    record.extend_from_slice(&b.to_le_bytes());
    record
}

/// find_engine: the engine's program: the one LIBRE_PANEL_ENGINE names, the one beside this
/// program (in its `tools` folder or next to it), the one on the PATH, or the one built in
/// the source tree this program was built in. With the highest rights only one from a safe
/// place (rights.rs): one elsewhere is passed over for the others.
pub fn find_engine() -> Option<PathBuf> {
    let name = if cfg!(windows) {
        format!("{ENGINE}.exe")
    } else {
        ENGINE.to_string()
    };
    if let Some(named) = std::env::var_os("LIBRE_PANEL_ENGINE").map(PathBuf::from) {
        if named.is_file() && crate::rights::safe_place(&named) {
            return Some(named);
        }
    }
    let program = std::env::current_exe().ok()?;
    let program = std::fs::canonicalize(&program).unwrap_or(program);
    let beside = program.parent()?;
    let own = [beside.join("tools").join(&name), beside.join(&name)];
    if let Some(found) = own.into_iter().find(|file| file.is_file()) {
        return Some(found);
    }
    if let Some(found) = crate::rights::which(ENGINE, &mut Vec::new()) {
        return Some(found);
    }
    beside
        .ancestors()
        .map(|folder| {
            folder
                .join("engine")
                .join("target")
                .join("release")
                .join(&name)
        })
        .find(|file| file.is_file() && crate::rights::safe_place(file))
}

/// _can: what `engine` says it can, after its x264 build (its `version`: "panel", "scenes",
/// "cards", "proven" ...); "" for one that does not start. Asked once a program.
pub fn capabilities(engine: &Path) -> String {
    static SAID: std::sync::OnceLock<Mutex<HashMap<PathBuf, String>>> = std::sync::OnceLock::new();
    let mut said = SAID
        .get_or_init(Default::default)
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    said.entry(engine.to_path_buf())
        .or_insert_with(|| {
            let done = hidden(&mut Command::new(engine))
                .arg("version")
                .stdin(Stdio::null())
                .stderr(Stdio::null())
                .output();
            match done {
                Ok(done) if done.status.success() => {
                    let text = String::from_utf8_lossy(&done.stdout).into_owned();
                    text.rsplit('(').next().unwrap_or("").to_string()
                }
                _ => String::new(),
            }
        })
        .clone()
}

/// `command` started without a console window of its own: on Windows the engine, a console
/// program, started by the app (which has no console) got one for each panel otherwise.
fn hidden(command: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
}

/// can_send: whether `engine` can have the panel (`libre-panel-engine panel`): an engine of
/// before encodes only.
pub fn can_send(engine: &Path) -> bool {
    capabilities(engine).contains("panel")
}

/// A frame as the engine takes it: its number, the time it was drawn for (seconds on the
/// loop's clock; the panel gets it a fixed lead later; None: at once) and the boxes of it
/// that may have changed (x0, y0, x1, y1), each with its pixels, three bytes each, row by
/// row. `rgb`: the whole frame, `width` pixels wide.
pub fn frame_record(
    number: u32,
    planned: Option<f64>,
    width: usize,
    rgb: &[u8],
    boxes: &[[u16; 4]],
) -> Vec<u8> {
    let kind = if planned.is_some() { PLANNED } else { FRAME };
    let pixels: usize = boxes
        .iter()
        .map(|b| usize::from(b[2] - b[0]) * usize::from(b[3] - b[1]) * 3)
        .sum();
    let mut record = head(kind, number, boxes.len() as u32);
    record.reserve(8 + boxes.len() * 8 + pixels);
    if let Some(planned) = planned {
        record.extend_from_slice(&planned.to_le_bytes());
    }
    for edges in boxes {
        for edge in edges {
            record.extend_from_slice(&edge.to_le_bytes());
        }
        let (x0, x1) = (usize::from(edges[0]) * 3, usize::from(edges[2]) * 3);
        for y in usize::from(edges[1])..usize::from(edges[3]) {
            let row = y * width * 3;
            record.extend_from_slice(&rgb[row + x0..row + x1]);
        }
    }
    record
}

/// The engine's process with the panel: its records in and out. A thread reads what it says
/// (replies, counts, a failure); a thread writes what it is told, in order, whole: a frame's
/// records waiting there while the one before goes, so the render loop waits for a full pipe
/// only when two frames are on their way (devices/engine.py's EngineStream writer).
pub struct EngineLink {
    child: Mutex<Child>,
    writer: Mutex<Option<SyncSender<Vec<Vec<u8>>>>>,
    /// why the writer stopped: the engine took no more
    unwritten: Arc<Mutex<Option<String>>>,
    shared: Arc<Shared>,
    numbers: AtomicU32,
    /// a command and its reply as one
    asking: Mutex<()>,
    threads: Vec<thread::JoinHandle<()>>,
}

/// The engine's input and output, as the link has them.
type Input = Box<dyn Write + Send>;
type Output = Box<dyn Read + Send>;

/// The writer: each batch of records into the engine's input, until the link lets go of it or
/// the engine takes no more (`unwritten` says why); then the input ends.
fn write_loop(
    mut pipe: Input,
    batches: std::sync::mpsc::Receiver<Vec<Vec<u8>>>,
    unwritten: &Mutex<Option<String>>,
) {
    for batch in batches {
        let written = batch
            .iter()
            .try_for_each(|record| pipe.write_all(record))
            .and_then(|()| pipe.flush());
        if let Err(e) = written {
            *unwritten.lock().unwrap_or_else(|e| e.into_inner()) = Some(e.to_string());
            return;
        }
    }
}

/// How much the pipes to and from the engine hold on Windows (Rust's own hold 4 KB there: a
/// frame of layers would go in a hundred turns, each waiting for the engine to read).
#[cfg(windows)]
const PIPE_BYTES: u32 = 1 << 20;

/// A pipe of PIPE_BYTES: its end to read and its end to write.
#[cfg(windows)]
fn big_pipe() -> std::io::Result<(std::fs::File, std::fs::File)> {
    use std::os::windows::io::{FromRawHandle, RawHandle};
    use windows_sys::Win32::System::Pipes::CreatePipe;
    let (mut read, mut write) = (std::ptr::null_mut(), std::ptr::null_mut());
    // SAFETY: CreatePipe fills both handles where it succeeds; each is owned by a File from
    // here on (not inherited: the child is given its own copy of its end).
    unsafe {
        if CreatePipe(&mut read, &mut write, std::ptr::null(), PIPE_BYTES) == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok((
            std::fs::File::from_raw_handle(read as RawHandle),
            std::fs::File::from_raw_handle(write as RawHandle),
        ))
    }
}

fn read_loop(mut out: impl Read, shared: &Shared) {
    loop {
        let mut head = [0u8; 16];
        if out.read_exact(&mut head).is_err() || &head[..4] != MAGIC {
            break;
        }
        let kind = head[4];
        let a = u32::from_le_bytes([head[8], head[9], head[10], head[11]]);
        let b = u32::from_le_bytes([head[12], head[13], head[14], head[15]]);
        let mut data = vec![0u8; b as usize];
        if out.read_exact(&mut data).is_err() {
            break;
        }
        let mut state = shared.lock();
        match kind {
            READY => state.place = Some(String::from_utf8_lossy(&data).into_owned()),
            REPLY => {
                if let (Some(slot), Some((status, rest))) =
                    (state.replies.get_mut(&a), data.split_first())
                {
                    *slot = Some((*status, rest.to_vec()));
                }
            }
            COUNTS => {
                let said = String::from_utf8_lossy(&data);
                if let Ok(lp_py::Value::Dict(counts)) = lp_py::json::loads(&said) {
                    state.counts = counts
                        .into_iter()
                        .filter_map(|(name, value)| match value {
                            lp_py::Value::Float(f) => Some((name, f)),
                            lp_py::Value::Int(n) => {
                                Some((name, lp_py::num::int_to_float(&n).ok()?))
                            }
                            _ => None,
                        })
                        .collect();
                }
            }
            FAILED => {
                state.failed = Some(Failure {
                    hung: a == 2,
                    text: String::from_utf8_lossy(&data).into_owned(),
                });
            }
            WAITING => state.waiting = a != 0,
            _ => {}
        }
        drop(state);
        shared.heard.notify_all();
    }
    shared.lock().ended = true;
    shared.heard.notify_all();
}

fn error_loop(err: impl Read, shared: &Shared) {
    for line in BufReader::new(err).split(b'\n') {
        let Ok(line) = line else { break };
        let text = String::from_utf8_lossy(&line).trim().to_string();
        if text.is_empty() {
            continue;
        }
        let mut state = shared.lock();
        match text.strip_prefix(ENGINE_NOTE) {
            Some(note) => state.notes.push(note.to_string()),
            None => {
                if state.errors.len() == 10 {
                    state.errors.pop_front();
                }
                state.errors.push_back(text);
            }
        }
    }
}

impl EngineLink {
    /// The engine started with `command` (its program and arguments); it has the panel when
    /// this returns.
    pub fn start(command: &[String]) -> Result<EngineLink> {
        let (program, arguments) = command
            .split_first()
            .ok_or_else(|| DeviceError("no engine to start".into()))?;
        let cannot = |e: std::io::Error| {
            let error = e.to_string();
            let said = "cannot start Libre Panel's engine: {error}";
            DeviceError(crate::base::tf(said, &[("error", &error)]))
        };
        let mut command = Command::new(program);
        hidden(&mut command).args(arguments).stderr(Stdio::piped());
        #[cfg(windows)]
        let (ours_in, ours_out): (Option<Input>, Option<Output>) = {
            let (their_in, ours_in) = big_pipe().map_err(cannot)?;
            let (ours_out, their_out) = big_pipe().map_err(cannot)?;
            command
                .stdin(Stdio::from(their_in))
                .stdout(Stdio::from(their_out));
            (Some(Box::new(ours_in)), Some(Box::new(ours_out)))
        };
        #[cfg(not(windows))]
        let (ours_in, ours_out): (Option<Input>, Option<Output>) = {
            command.stdin(Stdio::piped()).stdout(Stdio::piped());
            (None, None)
        };
        let mut child = command.spawn().map_err(cannot)?;
        drop(command); // (its copies of the child's ends of the pipes close here)
        let shared = Arc::new(Shared {
            state: Mutex::new(State::default()),
            heard: Condvar::new(),
        });
        let stdin = ours_in.or_else(|| {
            let pipe = child.stdin.take()?;
            Some(Box::new(pipe) as Input)
        });
        let stdout = ours_out.or_else(|| {
            let pipe = child.stdout.take()?;
            Some(Box::new(pipe) as Output)
        });
        let stderr = child.stderr.take();
        let mut threads = Vec::new();
        let unwritten = Arc::new(Mutex::new(None));
        let writer = stdin.map(|pipe| {
            // (one batch waiting while one goes: a frame and its records, or a command)
            let (send, batches) = sync_channel(1);
            let unwritten = unwritten.clone();
            threads.push(thread::spawn(move || write_loop(pipe, batches, &unwritten)));
            send
        });
        if let Some(out) = stdout {
            let shared = shared.clone();
            threads.push(thread::spawn(move || read_loop(out, &shared)));
        }
        if let Some(err) = stderr {
            let shared = shared.clone();
            threads.push(thread::spawn(move || error_loop(err, &shared)));
        }
        let mut link = EngineLink {
            child: Mutex::new(child),
            writer: Mutex::new(writer),
            unwritten,
            shared,
            numbers: AtomicU32::new(1),
            asking: Mutex::new(()),
            threads,
        };
        let deadline = Instant::now() + READY_FOR;
        let mut state = link.shared.lock();
        while state.place.is_none() {
            if state.ended || Instant::now() > deadline {
                let ended = state.ended;
                drop(state);
                // (its last words)
                thread::sleep(Duration::from_millis(100));
                let why = if ended {
                    link.reason()
                } else {
                    "Libre Panel's engine did not open the panel".to_string()
                };
                link.close(Duration::from_secs(5));
                return Err(DeviceError(why));
            }
            state = link
                .shared
                .heard
                .wait_timeout(state, Duration::from_millis(50))
                .unwrap_or_else(|e| e.into_inner())
                .0;
        }
        drop(state);
        Ok(link)
    }

    /// Where the panel is, as the engine said it.
    pub fn place(&self) -> String {
        self.shared.lock().place.clone().unwrap_or_default()
    }

    pub fn alive(&self) -> bool {
        if self.shared.lock().ended {
            return false;
        }
        let mut child = self.child.lock().unwrap_or_else(|e| e.into_inner());
        matches!(child.try_wait(), Ok(None))
    }

    /// Why the engine stopped, as far as it said.
    pub fn reason(&self) -> String {
        if let Some(last) = self.shared.lock().errors.back() {
            let own = format!("{ENGINE}: ");
            return last.strip_prefix(&own).unwrap_or(last).to_string();
        }
        let mut child = self.child.lock().unwrap_or_else(|e| e.into_inner());
        match child.try_wait() {
            Ok(Some(status)) => match status.code() {
                Some(code) => format!("exit code {code}"),
                None => "ended by a signal".to_string(),
            },
            _ => "no answer".to_string(),
        }
    }

    /// The video path's failure, where the engine said one.
    pub fn failed(&self) -> Option<Failure> {
        self.shared.lock().failed.clone()
    }

    /// Whether frames wait for the panel to take pictures: it sets the pace, nothing stalled.
    pub fn waiting(&self) -> bool {
        self.shared.lock().waiting
    }

    /// What the engine counted (pictures, blocks, bytes, waits, late ...), as it last said.
    pub fn counts(&self) -> HashMap<String, f64> {
        self.shared.lock().counts.clone()
    }

    /// What the engine noted for the log since this was last asked (which encoder it runs).
    pub fn notes(&self) -> Vec<String> {
        std::mem::take(&mut self.shared.lock().notes)
    }

    /// A record, whole.
    pub fn write(&self, record: &[u8]) -> Result<()> {
        self.write_all(vec![record.to_vec()])
    }

    /// Records one after the other (a frame's), whole: handed to the writer, which takes them
    /// while the one batch before goes into the engine (this waits for that).
    pub fn write_all(&self, records: Vec<Vec<u8>>) -> Result<()> {
        let writer = self.writer.lock().unwrap_or_else(|e| e.into_inner());
        let Some(send) = writer.as_ref() else {
            return Err(DeviceError("Libre Panel's engine is closed".into()));
        };
        send.send(records).map_err(|_| {
            let why = self
                .unwritten
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .clone();
            DeviceError(format!(
                "Libre Panel's engine is gone ({})",
                why.unwrap_or_else(|| "its input closed".into())
            ))
        })
    }

    /// request: a command for the panel and how the engine reads its reply: its status (0 the
    /// reply, 1 none in time, 2 it failed: the text why) and its bytes.
    #[allow(clippy::too_many_arguments)]
    pub fn request(
        &self,
        op: Op,
        cmd: u8,
        args: &[u8],
        payload: &[u8],
        timeout_ms: u32,
        reply_ms: u32,
        echo: bool,
    ) -> Result<(u8, Vec<u8>)> {
        let _one = self.asking.lock().unwrap_or_else(|e| e.into_inner());
        if !self.alive() {
            return Err(DeviceError(format!(
                "USB error on command {cmd}: the engine stopped ({})",
                self.reason()
            )));
        }
        let number = self.numbers.fetch_add(1, Ordering::Relaxed);
        let what = op as u32 | u32::from(cmd) << 8 | if echo { 0x1_0000 } else { 0 };
        let mut record = head(COMMAND, number, what);
        for value in [
            timeout_ms,
            reply_ms,
            args.len() as u32,
            payload.len() as u32,
        ] {
            record.extend_from_slice(&value.to_le_bytes());
        }
        record.extend_from_slice(args);
        record.extend_from_slice(payload);
        self.shared.lock().replies.insert(number, None);
        let written = self.write(&record);
        let deadline = Instant::now() + Duration::from_millis(u64::from(timeout_ms)) + REPLY_SLACK;
        let mut state = self.shared.lock();
        let reply = loop {
            if written.is_err() {
                break None;
            }
            if let Some(Some(_)) = state.replies.get(&number) {
                break state.replies.remove(&number).flatten();
            }
            let now = Instant::now();
            if state.ended || now >= deadline {
                break None;
            }
            state = self
                .shared
                .heard
                .wait_timeout(state, deadline - now)
                .unwrap_or_else(|e| e.into_inner())
                .0;
        };
        state.replies.remove(&number);
        drop(state);
        written?;
        reply.ok_or_else(|| {
            let why = if self.alive() {
                "in time".to_string()
            } else {
                self.reason()
            };
            DeviceError(format!(
                "USB error on command {cmd}: the engine gave no reply ({why})"
            ))
        })
    }

    /// shown: the frame of layers the engine composed last, whole, as it is before it turns
    /// (EngineLink.shown); None where it has none (a frame of pixels went since, no frame of
    /// layers yet), gives none within `timeout`, or is gone.
    pub fn shown(&self, timeout: Duration) -> Option<lp_pixels::Image> {
        if !self.alive() {
            return None;
        }
        let number = self.numbers.fetch_add(1, Ordering::Relaxed);
        self.shared.lock().replies.insert(number, None);
        let written = self.write(&head(SHOWN, number, 0));
        let deadline = Instant::now() + timeout;
        let mut state = self.shared.lock();
        let reply = loop {
            if written.is_err() {
                break None;
            }
            if let Some(Some(_)) = state.replies.get(&number) {
                break state.replies.remove(&number).flatten();
            }
            let now = Instant::now();
            if state.ended || now >= deadline {
                break None;
            }
            state = self
                .shared
                .heard
                .wait_timeout(state, deadline - now)
                .unwrap_or_else(|e| e.into_inner())
                .0;
        };
        state.replies.remove(&number);
        drop(state);
        let (status, data) = reply?;
        if status != 0 || data.len() < 8 {
            return None;
        }
        let width = u32::from_le_bytes(data[0..4].try_into().ok()?) as usize;
        let height = u32::from_le_bytes(data[4..8].try_into().ok()?) as usize;
        if width == 0 || height == 0 || data.len() != 8 + width * height * 3 {
            return None;
        }
        lp_pixels::Image::from_bytes(lp_pixels::Mode::RGB, width, height, data[8..].to_vec()).ok()
    }

    fn given(cmd: u8, status: u8, data: Vec<u8>) -> Result<Vec<u8>> {
        match status {
            2 => Err(DeviceError(String::from_utf8_lossy(&data).into_owned())),
            1 => Err(DeviceError(format!(
                "USB error on command {cmd}: no reply in time"
            ))),
            _ => Ok(data),
        }
    }

    /// A command and its reply (UsbTransport.command).
    pub fn command(
        &self,
        cmd: u8,
        args: &[u8],
        payload: &[u8],
        timeout_ms: u32,
    ) -> Result<Vec<u8>> {
        let (status, data) = self.request(Op::Command, cmd, args, payload, timeout_ms, 0, true)?;
        Self::given(cmd, status, data)
    }

    /// A question whose reply is waited for (UsbTransport.ask).
    pub fn ask(&self, cmd: u8, args: &[u8], timeout_ms: u32, echo: bool) -> Result<Vec<u8>> {
        let (status, data) = self.request(Op::Ask, cmd, args, &[], timeout_ms, 0, echo)?;
        Self::given(cmd, status, data)
    }

    /// A question that may stay unanswered (UsbTransport.query): None then.
    pub fn query(&self, cmd: u8, timeout_ms: u32) -> Result<Option<Vec<u8>>> {
        let (status, data) = self.request(Op::Query, cmd, &[], &[], timeout_ms, 0, true)?;
        if status == 1 {
            return Ok(None);
        }
        Self::given(cmd, status, data).map(Some)
    }

    /// What the panel still had to say read away (UsbTransport.drain): how many packets.
    pub fn drain(&self, timeout_ms: u32, limit: u32) -> u32 {
        match self.request(Op::Drain, 0, &[], &[], timeout_ms, limit, true) {
            Ok((0, data)) if data.len() >= 4 => {
                u32::from_le_bytes([data[0], data[1], data[2], data[3]])
            }
            _ => 0,
        }
    }

    /// sync: the panel's answer to the sync command (10), after what it still had to say.
    pub fn sync(&self, cmd_sync: u8, attempts: usize) -> Result<Vec<u8>> {
        let mut last = String::new();
        for _ in 0..attempts {
            self.drain(50, 64);
            match self.command(cmd_sync, &[], &[], 2000) {
                Ok(reply) => return Ok(reply),
                Err(e) => last = e.0,
            }
        }
        Err(DeviceError(format!(
            "panel does not answer the sync command: {last}"
        )))
    }

    /// stream: a new stream of drawn frames: the engine's encoder with `options` (those of
    /// `libre-panel-engine encode`), its pictures cut into `block`-sized blocks.
    pub fn stream(&self, options: &[String], block: u32) -> Result<()> {
        let text = options.join("\0");
        let mut record = head(STREAM, block, text.len() as u32);
        record.extend_from_slice(text.as_bytes());
        self.write(&record)
    }

    /// end: the stream ended: its pictures not yet sent are dropped, its encoder closed.
    pub fn end(&self) -> Result<()> {
        self.write(&head(END, 0, 0))
    }

    /// The bit rate's cap from now on, in the running stream (kbit/s).
    pub fn rate(&self, kbit: u32) -> Result<()> {
        self.write(&head(RATE, kbit, 0))
    }

    /// close: the input ends: the engine sends the commands it still has (the video's stop),
    /// then lets the panel go and ends.
    pub fn close(&mut self, timeout: Duration) {
        // (the writer takes what it still has, then the input ends)
        self.writer.lock().unwrap_or_else(|e| e.into_inner()).take();
        let deadline = Instant::now() + timeout;
        {
            let mut child = self.child.lock().unwrap_or_else(|e| e.into_inner());
            while matches!(child.try_wait(), Ok(None)) {
                if Instant::now() > deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    break;
                }
                thread::sleep(Duration::from_millis(10));
            }
        }
        for thread in self.threads.drain(..) {
            let _ = thread.join();
        }
    }
}

impl Drop for EngineLink {
    fn drop(&mut self) {
        self.close(Duration::from_secs(5));
    }
}
