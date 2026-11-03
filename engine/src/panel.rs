//! `libre-panel-engine panel ...`: the engine with a TURZX panel of its own (stage 1b of
//! docs/ROADMAP.md). It opens the panel Libre Panel picked, sends the commands Libre Panel
//! gives it (the video's start and stop, the brightness, the PNG layer: devices/engine.py)
//! as devices/turzx_usb.py sends them, and the pictures it makes of the frames into the
//! panel's decoder as devices/turzx_video.py does (video.rs), each on its time: a frame's
//! planned time, a fixed lead later, measured on the engine's own clock. Two threads: this
//! one reads the frames and encodes them; the sender, above the others in priority, sends
//! a command as soon as it comes and a picture when it is due.

use std::collections::VecDeque;
use std::io::{self, BufReader, BufWriter, Stdout};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use crate::link::{Link, TcpLink, UsbLink};
use crate::transport::{Error, Transport};
use crate::video::{Video, DEFAULT_BLOCK};
use crate::wire::{self, Boxes, Op, Record, Request};
use crate::{x264, yuv};

const USAGE: &str = "libre-panel-engine panel --pid HEX [--ports N.N...] [--bus N] [--address N] \
[--lead-ms N] [--link tcp:HOST:PORT]";

pub struct Options {
    pid: u16,
    ports: Option<Vec<u8>>,
    bus: Option<u8>,
    address: Option<u8>,
    lead_ms: Option<u32>,
    /// The blocks the hang check looks at (40; fewer for the tests, which cannot wait 8 s).
    hang_blocks: usize,
    /// The simulated panel of the tests (link.rs, TcpLink) instead of USB.
    link: Option<String>,
}

pub fn parse(args: &[String]) -> Result<Options, String> {
    let mut options = Options {
        pid: 0,
        ports: None,
        bus: None,
        address: None,
        lead_ms: None,
        hang_blocks: crate::video::HANG_WINDOW,
        link: None,
    };
    let mut pid = None;
    let mut i = 0;
    while i < args.len() {
        let value = args
            .get(i + 1)
            .ok_or_else(|| format!("{} wants a value; {USAGE}", args[i]))?;
        match args[i].as_str() {
            "--pid" => pid = Some(u16::from_str_radix(value, 16).map_err(|_| "--pid")?),
            "--ports" => {
                let ports: Result<Vec<u8>, _> = value.split('.').map(str::parse).collect();
                options.ports = Some(ports.map_err(|_| "--ports is N.N...")?);
            }
            "--bus" => options.bus = Some(value.parse().map_err(|_| "--bus")?),
            "--address" => options.address = Some(value.parse().map_err(|_| "--address")?),
            "--lead-ms" => options.lead_ms = Some(value.parse().map_err(|_| "--lead-ms")?),
            "--hang-blocks" => {
                options.hang_blocks = value.parse().map_err(|_| "--hang-blocks")?;
                if options.hang_blocks == 0 {
                    return Err("--hang-blocks is 1 or more".into());
                }
            }
            "--link" => options.link = Some(value.clone()),
            other => return Err(format!("unknown option {other}; {USAGE}")),
        }
        i += 2;
    }
    options.pid = pid.ok_or_else(|| format!("--pid is wanted; {USAGE}"))?;
    Ok(options)
}

/// Pictures waiting for the panel at most, as turzx_video.py's queue holds them; then frames
/// wait in the pipe, and so does the main loop: fewer frames, never torn ones.
const MOST_PICTURES: usize = 2;
const COUNTS_EVERY: Duration = Duration::from_millis(250);
/// A picture this much past its time counts as late.
const LATE: Duration = Duration::from_millis(2);
/// The last stretch of a wait is slept, not waited on the condition: precisely on time.
const SLEPT: Duration = Duration::from_millis(3);

struct Picture {
    stream: u64,
    due: Option<Instant>,
    data: Vec<u8>,
    block: usize,
}

#[derive(Default)]
struct State {
    commands: VecDeque<Request>,
    pictures: VecDeque<Picture>,
    stream: u64,
    closing: bool,
    failed: bool,
    drops: u64,
    frames: u64,
    encode_s: f64,
}

struct Shared {
    state: Mutex<State>,
    wake: Condvar,
    out: Mutex<BufWriter<Stdout>>,
}

impl Shared {
    fn state(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// A record to Libre Panel; the state's lock is never held meanwhile.
    fn say(&self, kind: u8, a: u32, data: &[&[u8]]) {
        let mut out = self.out.lock().unwrap_or_else(|e| e.into_inner());
        if let Err(e) = wire::write(&mut *out, kind, a, data) {
            eprintln!("libre-panel-engine: cannot write to Libre Panel: {e}");
        }
    }

    fn drop_pictures(state: &mut State) {
        state.drops += state.pictures.len() as u64;
        state.pictures.clear();
    }
}

/// When a picture is due at the panel: the time its frame was drawn for (on the main loop's
/// clock) moved onto the engine's clock by the least delay seen between the two, the fastest
/// a frame came (its drawing and the pipe), and `lead` later, so that a frame that took
/// longer to draw or to encode is still on time. No shared clock is assumed.
#[derive(Default)]
struct Pacer {
    least: Option<f64>,
}

impl Pacer {
    fn due(
        &mut self,
        planned: Option<f64>,
        origin: Instant,
        read: Instant,
        lead: Duration,
    ) -> Option<Instant> {
        let planned = planned?;
        let seen = (read - origin).as_secs_f64() - planned;
        let least = self.least.map_or(seen, |least: f64| least.min(seen));
        self.least = Some(least);
        let at = planned + least + lead.as_secs_f64();
        Some(origin + Duration::from_secs_f64(at.max(0.0)))
    }
}

/// The encoder of one stream, and the YUV it is fed.
struct Stream {
    frame: yuv::Yuv,
    encoder: x264::Encoder,
    mb: Vec<u8>,
    block: usize,
    lead: Duration,
}

impl Stream {
    fn open(options: &[String], block: u32, lead_ms: Option<u32>) -> Result<Stream, String> {
        let o = crate::parse(options)?;
        let frame = yuv::Yuv::new(o.size.0, o.size.1, o.turn, o.settings.full)?;
        let encoder = x264::Encoder::open(&o.settings)?;
        let (cols, rows) = frame.macroblocks();
        // two frames' time at the stream's rate: 40 ms at 50 a second, 80 at 25
        let lead = lead_ms.unwrap_or_else(|| (2000 / o.settings.fps.max(1)).clamp(30, 100));
        Ok(Stream {
            frame,
            encoder,
            mb: vec![0u8; cols * rows],
            block: if block == 0 {
                DEFAULT_BLOCK
            } else {
                block as usize
            },
            lead: Duration::from_millis(u64::from(lead)),
        })
    }

    fn encode(&mut self, number: u32, boxes: &Boxes) -> Result<Vec<u8>, String> {
        for (x0, y0, x1, y1, pixels) in boxes {
            self.frame.put(*x0, *y0, *x1, *y1, pixels)?;
        }
        self.frame.finish(&mut self.mb);
        let stride = self.frame.turned.0;
        let (picture, _key) = self.encoder.encode(
            &mut self.frame.y,
            &mut self.frame.u,
            &mut self.frame.v,
            stride,
            &mut self.mb,
            i64::from(number),
        )?;
        Ok(picture.to_vec())
    }
}

#[cfg(windows)]
fn fine_timer() {
    // a wait on a condition ends on the system's timer, every 15.6 ms unless asked for 1
    // (since Windows 10 2004 for this process alone)
    unsafe {
        windows_sys::Win32::Media::timeBeginPeriod(1);
    }
}

#[cfg(not(windows))]
fn fine_timer() {}

#[cfg(windows)]
fn raise_priority() {
    use windows_sys::Win32::System::Threading::{
        GetCurrentThread, SetThreadPriority, THREAD_PRIORITY_HIGHEST,
    };
    // (above the frames' threads: a busy PC must not starve the panel, SPUR II)
    unsafe {
        SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
    }
}

#[cfg(not(windows))]
fn raise_priority() {}

fn execute(transport: &mut Transport, r: &Request) -> (u8, Vec<u8>) {
    let given = |result: Result<Vec<u8>, Error>| match result {
        Ok(reply) => (0u8, reply),
        Err(e) => (2u8, e.to_string().into_bytes()),
    };
    let maybe = |result: Result<Option<Vec<u8>>, Error>| match result {
        Ok(Some(reply)) => (0u8, reply),
        Ok(None) => (1u8, Vec::new()),
        Err(e) => (2u8, e.to_string().into_bytes()),
    };
    match r.op {
        Op::Command => given(transport.command(r.cmd, &r.args, &r.payload, r.timeout_ms)),
        Op::Ask => given(transport.ask(r.cmd, &r.args, r.timeout_ms, r.echo)),
        Op::Query => maybe(transport.query(r.cmd, r.timeout_ms)),
        Op::Push => maybe(transport.push(r.cmd, &r.args, &r.payload, r.timeout_ms, r.reply_ms)),
        // (a drain's reply_ms is how many replies it throws away at most)
        Op::Drain => (
            0,
            transport
                .drain(r.timeout_ms, r.reply_ms)
                .to_le_bytes()
                .to_vec(),
        ),
    }
}

enum Job {
    Command(Request),
    Picture(Picture),
}

/// The next thing to send: a command at once, else the first picture once it is due; None
/// when the input ended and everything went.
fn next(shared: &Shared) -> Option<Job> {
    let mut state = shared.state();
    loop {
        if let Some(request) = state.commands.pop_front() {
            return Some(Job::Command(request));
        }
        if let Some(due) = state.pictures.front().map(|p| p.due) {
            let now = Instant::now();
            match due {
                Some(due) if due > now + SLEPT => {
                    let (back, _) = shared
                        .wake
                        .wait_timeout(state, due - now - SLEPT)
                        .unwrap_or_else(|e| e.into_inner());
                    state = back;
                }
                Some(due) if due > now => {
                    drop(state);
                    thread::sleep(due - now);
                    state = shared.state();
                }
                _ => return state.pictures.pop_front().map(Job::Picture),
            }
            continue;
        }
        if state.closing {
            return None;
        }
        state = shared.wake.wait(state).unwrap_or_else(|e| e.into_inner());
    }
}

fn counts(video: &Video, state: &State, sent: u64, late: u64) -> String {
    let c = &video.counts;
    let encode_ms = if state.frames > 0 {
        state.encode_s * 1000.0 / state.frames as f64
    } else {
        0.0
    };
    format!(
        "{{\"blocks\": {}, \"bytes\": {}, \"waits\": {}, \"slow_blocks\": {}, \"max_depth\": {}, \
         \"pictures\": {sent}, \"late\": {late}, \"drops\": {}, \"frames\": {}, \"encode_ms\": {:.3}}}",
        c.blocks, c.bytes, c.waits, c.slow_blocks, c.max_depth, state.drops, state.frames, encode_ms
    )
}

fn sender(shared: Arc<Shared>, mut transport: Transport, hang_blocks: usize) {
    raise_priority();
    let mut video = Video::new(DEFAULT_BLOCK);
    video.hang_window = hang_blocks;
    let (mut sent, mut late) = (0u64, 0u64);
    let mut told = Instant::now();
    while let Some(job) = next(&shared) {
        shared.wake.notify_all(); // (room for another picture)
        match job {
            Job::Command(request) => {
                let (status, data) = execute(&mut transport, &request);
                shared.say(wire::REPLY, request.id, &[&[status], &data]);
            }
            Job::Picture(picture) => {
                {
                    let mut state = shared.state();
                    if state.failed || picture.stream != state.stream {
                        state.drops += 1; // (of a stream that ended, or after a failure)
                        continue;
                    }
                }
                if picture.due.is_some_and(|due| Instant::now() > due + LATE) {
                    late += 1;
                }
                video.block_size = picture.block;
                match video.send(&mut transport, &picture.data) {
                    Ok(()) => sent += 1,
                    Err(e) => {
                        // (a hung decoder: the depth its queue stood at, for Libre Panel's words)
                        let (kind, text) = match &e {
                            Error::Hung(depth) => (2, depth.to_string()),
                            other => (1, other.to_string()),
                        };
                        {
                            let mut state = shared.state();
                            state.failed = true;
                            state.drops += 1;
                            Shared::drop_pictures(&mut state);
                        }
                        shared.wake.notify_all();
                        eprintln!("libre-panel-engine: the video stopped: {e}");
                        shared.say(wire::FAILED, kind, &[text.as_bytes()]);
                    }
                }
            }
        }
        if told.elapsed() >= COUNTS_EVERY {
            told = Instant::now();
            let text = counts(&video, &shared.state(), sent, late);
            shared.say(wire::COUNTS, 0, &[text.as_bytes()]);
        }
    }
    let text = counts(&video, &shared.state(), sent, late);
    shared.say(wire::COUNTS, 0, &[text.as_bytes()]);
}

/// Hand a picture to the sender, waiting while two wait already (the panel sets the pace;
/// Libre Panel is told, so that it does not take the engine for stalled).
fn hand_on(shared: &Shared, picture: Picture) {
    let mut state = shared.state();
    let mut waited = false;
    while state.pictures.len() >= MOST_PICTURES && !state.failed && !state.closing {
        if !waited {
            waited = true;
            drop(state);
            shared.say(wire::WAITING, 1, &[]);
            state = shared.state();
            continue;
        }
        state = shared.wake.wait(state).unwrap_or_else(|e| e.into_inner());
    }
    if state.failed || picture.stream != state.stream {
        state.drops += 1;
    } else {
        state.pictures.push_back(picture);
    }
    drop(state);
    shared.wake.notify_all();
    if waited {
        shared.say(wire::WAITING, 0, &[]);
    }
}

pub fn run(options: Options) -> Result<(), String> {
    fine_timer();
    let (link, place): (Box<dyn Link>, String) = match &options.link {
        Some(spec) => {
            let address = spec.strip_prefix("tcp:").ok_or("--link is tcp:HOST:PORT")?;
            (
                Box::new(TcpLink::connect(address)?),
                format!("test link {address}"),
            )
        }
        None => {
            let usb = UsbLink::open(
                options.pid,
                options.ports.as_deref(),
                options.bus,
                options.address,
            )?;
            let place = usb.place.clone();
            (Box::new(usb), place)
        }
    };
    let shared = Arc::new(Shared {
        state: Mutex::new(State::default()),
        wake: Condvar::new(),
        out: Mutex::new(BufWriter::with_capacity(1 << 16, io::stdout())),
    });
    shared.say(wire::READY, 0, &[place.as_bytes()]);
    let hang_blocks = options.hang_blocks;
    let sending = {
        let shared = shared.clone();
        let transport = Transport::new(link);
        thread::Builder::new()
            .name("panel".into())
            .spawn(move || sender(shared, transport, hang_blocks))
            .map_err(|e| format!("no thread for the panel: {e}"))?
    };
    let origin = Instant::now();
    let mut input = BufReader::with_capacity(1 << 20, io::stdin().lock());
    let mut stream: Option<Stream> = None;
    let mut pacer = Pacer::default();
    let result = loop {
        let record = match wire::read(&mut input) {
            Ok(Some(record)) => record,
            Ok(None) => break Ok(()),
            Err(e) => break Err(format!("the input: {e}")),
        };
        match record {
            Record::Frame {
                number,
                boxes,
                planned,
            } => {
                let read = Instant::now();
                let Some(s) = stream.as_mut() else {
                    break Err("a frame before its stream".into());
                };
                let picture = match s.encode(number, &boxes) {
                    Ok(picture) => picture,
                    Err(e) => break Err(e),
                };
                let current = {
                    let mut state = shared.state();
                    state.frames += 1;
                    state.encode_s += read.elapsed().as_secs_f64();
                    state.stream
                };
                if !picture.is_empty() {
                    let due = pacer.due(planned, origin, read, s.lead);
                    let block = s.block;
                    hand_on(
                        &shared,
                        Picture {
                            stream: current,
                            due,
                            data: picture,
                            block,
                        },
                    );
                }
            }
            Record::Rate(kbit) => {
                if let Some(s) = stream.as_mut() {
                    if let Err(e) = s.encoder.rate(kbit) {
                        break Err(e);
                    }
                }
            }
            Record::Keyframe => {
                if let Some(s) = stream.as_mut() {
                    s.encoder.keyframe();
                }
            }
            Record::Command(request) => {
                shared.state().commands.push_back(request);
                shared.wake.notify_all();
            }
            Record::Stream {
                block,
                options: encoding,
            } => {
                drop(stream.take()); // (the old encoder closed first: the memory of one)
                {
                    let mut state = shared.state();
                    state.stream += 1;
                    Shared::drop_pictures(&mut state);
                }
                shared.wake.notify_all();
                match Stream::open(&encoding, block, options.lead_ms) {
                    Ok(s) => stream = Some(s),
                    Err(e) => break Err(e),
                }
                pacer = Pacer::default();
            }
            Record::Picture { block, data } => {
                let current = shared.state().stream;
                let block = if block == 0 {
                    DEFAULT_BLOCK
                } else {
                    block as usize
                };
                hand_on(
                    &shared,
                    Picture {
                        stream: current,
                        due: None,
                        data,
                        block,
                    },
                );
            }
            Record::End => {
                stream = None;
                {
                    let mut state = shared.state();
                    state.stream += 1;
                    Shared::drop_pictures(&mut state);
                }
                shared.wake.notify_all();
            }
        }
    };
    {
        // the commands still to go are sent (the video's stop); pictures no longer
        let mut state = shared.state();
        state.closing = true;
        Shared::drop_pictures(&mut state);
    }
    shared.wake.notify_all();
    let _ = sending.join();
    result
}
