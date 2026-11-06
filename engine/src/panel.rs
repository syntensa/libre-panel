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
use crate::scene::{How, Layer, Scene};
use crate::transport::{Error, Transport};
use crate::video::{Video, DEFAULT_BLOCK};
use crate::wire::{self, Boxes, Op, Record, Request};
use crate::{bar, ring};
use crate::{encoder, yuv};

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
/// The last stretch of a wait is slept, not waited on the condition: a sleep ends on time (a
/// high-resolution timer), a wait on the condition on the system's next tick, which may come
/// 15.6 ms apart (Windows, where the finer timer fine_timer asks for is not kept).
const SLEPT: Duration = Duration::from_millis(17);

struct Picture {
    stream: u64,
    due: Option<Instant>,
    /// When it was made (encoded): after its time, it came late, whatever the sender did.
    ready: Instant,
    data: Vec<u8>,
    block: usize,
}

/// What the sender counted of the pictures on their time: sent, late (more than LATE past
/// it), late as they came (made after their time: the frame, or its encoding, was late) and
/// the most any was late.
#[derive(Default)]
struct Lateness {
    sent: u64,
    late: u64,
    came_late: u64,
    most: Duration,
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
    /// Of `encode_s`: composing the frame from its layers, its YUV, and the encoder (x264's,
    /// or the graphics card's: `card`).
    compose_s: f64,
    yuv_s: f64,
    x264_s: f64,
    card: bool,
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

/// The encoder of one stream, the YUV it is fed and the layers its frames may be made of.
struct Stream {
    frame: yuv::Yuv,
    scene: Scene,
    encoder: encoder::Encoder,
    mb: Vec<u8>,
    block: usize,
    lead: Duration,
}

impl Stream {
    /// A stream's encoder with `options` (those of `encode`, and `--lead-ms` for its lead,
    /// which Libre Panel decides: devices/h264.py's engine_lead; else `lead_ms`, the panel's).
    fn open(options: &[String], block: u32, lead_ms: Option<u32>) -> Result<Stream, String> {
        let mut lead_ms = lead_ms;
        let mut rest = Vec::with_capacity(options.len());
        let mut given = options.iter();
        while let Some(option) = given.next() {
            if option == "--lead-ms" {
                let value = given.next().ok_or("--lead-ms wants a value")?;
                lead_ms = Some(value.parse().map_err(|_| "--lead-ms")?);
            } else {
                rest.push(option.clone());
            }
        }
        let o = crate::parse(&rest)?;
        let frame = yuv::Yuv::new(o.size.0, o.size.1, o.turn, o.settings.full)?;
        let scene = Scene::new(o.size.0, o.size.1, frame.block);
        let encoder = encoder::Encoder::open(&o.settings, o.encoder)?;
        let (cols, rows) = frame.macroblocks();
        // two frames' time at the stream's rate: 40 ms at 50 a second, 80 at 25
        let lead = lead_ms.unwrap_or_else(|| (2000 / o.settings.fps.max(1)).clamp(30, 100));
        Ok(Stream {
            frame,
            scene,
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

    /// The frame's picture, and the seconds its YUV and its encoding took.
    fn encode(&mut self, number: u32, boxes: &Boxes) -> Result<(Vec<u8>, f64, f64), String> {
        let began = Instant::now();
        for (x0, y0, x1, y1, pixels) in boxes {
            self.frame.put(*x0, *y0, *x1, *y1, pixels)?;
        }
        self.frame.finish(&mut self.mb);
        let yuv = began.elapsed().as_secs_f64();
        let stride = self.frame.turned.0;
        let encoding = Instant::now();
        let picture = self.encoder.encode(
            &mut self.frame.y,
            &mut self.frame.u,
            &mut self.frame.v,
            stride,
            &mut self.mb,
            i64::from(number),
        )?;
        Ok((picture.to_vec(), yuv, encoding.elapsed().as_secs_f64()))
    }
}

#[cfg(windows)]
fn fine_timer() {
    use std::ffi::c_void;
    use windows_sys::Win32::System::Threading::{
        GetCurrentProcess, ProcessPowerThrottling, SetProcessInformation,
        PROCESS_POWER_THROTTLING_CURRENT_VERSION, PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
        PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION, PROCESS_POWER_THROTTLING_STATE,
    };
    // A wait on a condition ends on the system's timer, every 15.6 ms unless asked for 1 (since
    // Windows 10 2004 for this process alone). Windows 11 forgets that ask while no window of
    // the process, or of the one that started it, is seen (Libre Panel's window closed) unless
    // the process wants it kept; and it may then run the process on the slower cores at a low
    // clock (EcoQoS). The engine sends the pictures on their time: it asks out of both, of
    // each on its own (Windows 10 knows only the second, and refuses a call with one it does
    // not know).
    unsafe {
        windows_sys::Win32::Media::timeBeginPeriod(1);
        for mechanism in [
            PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION,
            PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
        ] {
            let state = PROCESS_POWER_THROTTLING_STATE {
                Version: PROCESS_POWER_THROTTLING_CURRENT_VERSION,
                ControlMask: mechanism,
                StateMask: 0, // (the mechanism off)
            };
            // SAFETY: the state lives through the call, its size is given with it.
            SetProcessInformation(
                GetCurrentProcess(),
                ProcessPowerThrottling,
                (&state as *const PROCESS_POWER_THROTTLING_STATE).cast::<c_void>(),
                std::mem::size_of::<PROCESS_POWER_THROTTLING_STATE>() as u32,
            );
        }
    }
}

#[cfg(not(windows))]
fn fine_timer() {}

#[cfg(windows)]
fn raise_priority() {
    use windows_sys::Win32::System::Threading::{
        AvSetMmThreadCharacteristicsW, GetCurrentThread, SetThreadPriority, THREAD_PRIORITY_HIGHEST,
    };
    // A busy PC must not starve the panel (SPUR II): the sender under the multimedia scheduler
    // as video players' threads are ("Playback": above every thread of normal programs, a
    // game's too, while the scheduler keeps a share of the processor for them), else at the
    // highest priority of its process. It sleeps between the pictures; a send takes a
    // millisecond or two.
    let task: Vec<u16> = "Playback\0".encode_utf16().collect();
    let mut index = 0u32;
    // SAFETY: a NUL-terminated name and an index to fill; the handle stays the thread's.
    let scheduled = unsafe { !AvSetMmThreadCharacteristicsW(task.as_ptr(), &mut index).is_null() };
    if !scheduled {
        unsafe {
            SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
        }
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

fn counts(video: &Video, state: &State, paced: &Lateness) -> String {
    let c = &video.counts;
    let per_frame = |seconds: f64| {
        if state.frames > 0 {
            seconds * 1000.0 / state.frames as f64
        } else {
            0.0
        }
    };
    format!(
        "{{\"blocks\": {}, \"bytes\": {}, \"waits\": {}, \"slow_blocks\": {}, \"max_depth\": {}, \
         \"pictures\": {}, \"late\": {}, \"came_late\": {}, \"late_most_ms\": {:.1}, \
         \"drops\": {}, \"frames\": {}, \"encode_ms\": {:.3}, \"compose_ms\": {:.3}, \
         \"yuv_ms\": {:.3}, \"x264_ms\": {:.3}, \"card\": {}}}",
        c.blocks,
        c.bytes,
        c.waits,
        c.slow_blocks,
        c.max_depth,
        paced.sent,
        paced.late,
        paced.came_late,
        paced.most.as_secs_f64() * 1000.0,
        state.drops,
        state.frames,
        per_frame(state.encode_s),
        per_frame(state.compose_s),
        per_frame(state.yuv_s),
        per_frame(state.x264_s),
        u8::from(state.card),
    )
}

fn sender(shared: Arc<Shared>, mut transport: Transport, hang_blocks: usize) {
    raise_priority();
    let mut video = Video::new(DEFAULT_BLOCK);
    video.hang_window = hang_blocks;
    let mut paced = Lateness::default();
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
                if let Some(due) = picture.due {
                    let now = Instant::now();
                    if now > due + LATE {
                        paced.late += 1;
                        paced.came_late += u64::from(picture.ready > due);
                        paced.most = paced.most.max(now - due);
                    }
                }
                video.block_size = picture.block;
                match video.send(&mut transport, &picture.data) {
                    Ok(()) => paced.sent += 1,
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
            let text = counts(&video, &shared.state(), &paced);
            shared.say(wire::COUNTS, 0, &[text.as_bytes()]);
        }
    }
    let text = counts(&video, &shared.state(), &paced);
    shared.say(wire::COUNTS, 0, &[text.as_bytes()]);
}

/// The app's picture of the panel (wire.rs's kind 28): whether the panel's frame is one of
/// layers composed here (`layers`), whether no record of the next frame came since it was
/// (`settled`: what the scene holds is that frame), and who asked meanwhile (answered after
/// the next frame: one at most, an earlier one gets none).
#[derive(Default)]
struct Asked {
    layers: bool,
    settled: bool,
    waiting: Option<u32>,
}

impl Asked {
    /// Asked for by `id`: answered now where the scene holds the frame shown, after the next
    /// frame where records of it came, else with none.
    fn ask(&mut self, shared: &Shared, id: u32, scene: Option<&Scene>) {
        match scene {
            Some(scene) if self.layers && self.settled => answer(shared, id, Some(scene)),
            Some(_) if self.layers => {
                if let Some(earlier) = self.waiting.replace(id) {
                    answer(shared, earlier, None);
                }
            }
            _ => answer(shared, id, None),
        }
    }

    /// A frame of layers was composed (`scene`), or one of another kind went (None: one of
    /// Libre Panel's own frames, a picture made elsewhere, a new stream or its end): whoever
    /// waits is answered.
    fn composed(&mut self, shared: &Shared, scene: Option<&Scene>) {
        self.layers = scene.is_some();
        self.settled = self.layers;
        if let Some(id) = self.waiting.take() {
            answer(shared, id, scene);
        }
    }
}

/// The frame shown for whoever asked (`id`): the scene's picture, whole, or none.
fn answer(shared: &Shared, id: u32, scene: Option<&Scene>) {
    match scene {
        Some(scene) => {
            let (w, h) = scene.size();
            let pixels = scene.picture();
            let (w, h) = ((w as u32).to_le_bytes(), (h as u32).to_le_bytes());
            shared.say(wire::REPLY, id, &[&[0], &w, &h, &pixels]);
        }
        None => shared.say(wire::REPLY, id, &[&[1]]),
    }
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

/// A frame's boxes into the stream's encoder, and its picture to the sender for its time
/// (the engine's start and when the frame was read: Pacer::due).
fn take_frame(
    shared: &Shared,
    s: &mut Stream,
    pacer: &mut Pacer,
    (origin, read): (Instant, Instant),
    number: u32,
    boxes: &Boxes,
    planned: Option<f64>,
) -> Result<(), String> {
    let composed = read.elapsed().as_secs_f64(); // (a frame of layers: composed since it was read)
    let (picture, yuv, x264) = s.encode(number, boxes)?;
    let current = {
        let mut state = shared.state();
        state.frames += 1;
        state.encode_s += read.elapsed().as_secs_f64();
        state.compose_s += composed;
        state.yuv_s += yuv;
        state.x264_s += x264;
        state.card = s.encoder.on_card();
        state.stream
    };
    if !picture.is_empty() {
        let due = pacer.due(planned, origin, read, s.lead);
        hand_on(
            shared,
            Picture {
                stream: current,
                due,
                ready: Instant::now(),
                data: picture,
                block: s.block,
            },
        );
    }
    Ok(())
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
    let mut asked = Asked::default();
    let result = loop {
        let record = match wire::read(&mut input) {
            Ok(Some(record)) => record,
            Ok(None) => break Ok(()),
            Err(e) => break Err(format!("the input: {e}")),
        };
        let between = !matches!(
            record,
            Record::Command(_) | Record::Rate(_) | Record::Keyframe | Record::Shown { .. }
        );
        if between {
            asked.settled = false; // (a record of the next frame: the scene no longer the one shown)
        }
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
                s.scene.frame_between(); // (the next frame of layers: composed whole)
                asked.composed(&shared, None);
                if let Err(e) = take_frame(
                    &shared,
                    s,
                    &mut pacer,
                    (origin, read),
                    number,
                    &boxes,
                    planned,
                ) {
                    break Err(e);
                }
            }
            Record::Scene {
                number,
                planned,
                half,
                order,
            } => {
                let read = Instant::now();
                let Some(s) = stream.as_mut() else {
                    break Err("a frame before its stream".into());
                };
                let boxes = match s.scene.frame(order, half) {
                    Ok(boxes) => boxes,
                    Err(e) => break Err(e),
                };
                if let Err(e) = take_frame(
                    &shared,
                    s,
                    &mut pacer,
                    (origin, read),
                    number,
                    &boxes,
                    planned,
                ) {
                    break Err(e);
                }
                asked.composed(&shared, Some(&s.scene)); // (after its picture went on)
            }
            Record::Shown { id } => asked.ask(&shared, id, stream.as_ref().map(|s| &s.scene)),
            Record::Ground { colour, picture } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a ground before its stream".into());
                };
                match colour {
                    Some(rgb) => s.scene.ground_colour(rgb),
                    None => {
                        if let Err(e) = s.scene.ground_picture(picture) {
                            break Err(e);
                        }
                    }
                }
            }
            Record::RingBase {
                id,
                side,
                geometry,
                groove,
                lit,
            } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a ring before its stream".into());
                };
                match ring::Base::new(side, &geometry, groove, lit) {
                    Ok(base) => s.scene.ring_base(id, base),
                    Err(e) => break Err(e),
                }
            }
            Record::Ring {
                id,
                ring,
                x,
                y,
                frac,
            } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a ring before its stream".into());
                };
                if let Err(e) = s.scene.ring(id, ring, x, y, frac) {
                    break Err(e);
                }
            }
            Record::BarBase {
                id,
                geometry,
                canvas,
                pills,
            } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a bar before its stream".into());
                };
                match bar::Base::new(*geometry, canvas, *pills) {
                    Ok(base) => s.scene.bar_base(id, base),
                    Err(e) => break Err(e),
                }
            }
            Record::Bar {
                id,
                bar,
                x,
                y,
                light,
                paint,
                spans,
            } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a bar before its stream".into());
                };
                if let Err(e) = s.scene.bar((id, light), bar, (x, y), &paint, &spans) {
                    break Err(e);
                }
            }
            Record::Source { id, w, h, pixels } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a source before its stream".into());
                };
                if let Err(e) = s.scene.source(id, w, h, pixels) {
                    break Err(e);
                }
            }
            Record::Grown {
                id,
                w,
                h,
                from,
                shift,
                keep,
                rest,
            } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a source before its stream".into());
                };
                if let Err(e) = s.scene.grown(id, (w, h), from, (shift, keep), &rest) {
                    break Err(e);
                }
            }
            Record::View {
                id,
                x,
                y,
                w,
                h,
                parts,
            } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a layer before its stream".into());
                };
                if let Err(e) = s.scene.view(id, (x, y, w, h), &parts) {
                    break Err(e);
                }
            }
            Record::Rotor { id, rotor } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a rotor before its stream".into());
                };
                s.scene.rotor(id, rotor);
            }
            Record::Blades { id, rotor, blades } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a source before its stream".into());
                };
                if let Err(e) = s.scene.blades(id, rotor, &blades) {
                    break Err(e);
                }
            }
            Record::FanRing { id, spec } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a source before its stream".into());
                };
                if let Err(e) = s.scene.fan_ring(id, &spec) {
                    break Err(e);
                }
            }
            Record::Masked {
                id,
                w,
                h,
                ink,
                mask,
            } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a source before its stream".into());
                };
                if let Err(e) = s.scene.masked(id, (w, h), ink, &mask) {
                    break Err(e);
                }
            }
            Record::MaskedLayer {
                id,
                x,
                y,
                w,
                h,
                ink,
                mask,
            } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a layer before its stream".into());
                };
                if let Err(e) = s.scene.masked_layer(id, (x, y, w, h), ink, &mask) {
                    break Err(e);
                }
            }
            Record::Laid {
                id,
                x,
                y,
                w,
                h,
                parts,
                kept,
            } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a layer before its stream".into());
                };
                if let Err(e) = s.scene.laid(id, (x, y, w, h), &parts, &kept) {
                    break Err(e);
                }
            }
            Record::Globe { id, tables } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a globe before its stream".into());
                };
                s.scene.globe(id, tables);
            }
            Record::Fill {
                id,
                x,
                y,
                w,
                h,
                rgba,
            } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a layer before its stream".into());
                };
                if let Err(e) = s.scene.fill(id, (x, y, w, h), rgba) {
                    break Err(e);
                }
            }
            Record::GlobeLayer {
                id,
                globe,
                map,
                air,
                step,
                x,
                y,
                mark,
            } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a layer before its stream".into());
                };
                if let Err(e) = s
                    .scene
                    .globe_layer(id, globe, (map, air, step), (x, y), mark)
                {
                    break Err(e);
                }
            }
            Record::Layer {
                id,
                x,
                y,
                w,
                h,
                how,
                pixels,
            } => {
                let Some(s) = stream.as_mut() else {
                    break Err("a layer before its stream".into());
                };
                let Some(how) = How::of(how) else {
                    break Err(format!("layer {id} goes on in no way known ({how})"));
                };
                match Layer::new(x, y, w, h, how, pixels) {
                    Ok(layer) => s.scene.layer(id, layer),
                    Err(e) => break Err(e),
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
                asked.composed(&shared, None);
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
                asked.composed(&shared, None);
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
                        ready: Instant::now(),
                        data,
                        block,
                    },
                );
            }
            Record::End => {
                asked.composed(&shared, None);
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
