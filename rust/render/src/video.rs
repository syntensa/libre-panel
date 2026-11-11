//! The videos of image widgets (render/moving.py's other half): ffmpeg reads a video into raw
//! RGBA frames of the widget's size, fitted as the widget says (its command as Python's, to the
//! letter); what `ffmpeg -i` says of a video (how long it plays, its size upright, its frames a
//! second), read as moving.video_info's patterns read it; its first frame, for a preview or
//! until the clip's first comes; and VideoFeed, which plays it round and round a few frames
//! ahead in a thread of its own and gives the frame of the moment each frame of the panel is
//! drawn for. The frames are ffmpeg's own bytes: the same program makes the same pixels.

use std::collections::VecDeque;
use std::ffi::OsString;
use std::io::Read;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, OnceLock};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use lp_pixels::{Image, Mode};

/// the frames a second a video is decoded at, at most, where nothing says more
pub const VIDEO_FPS: u32 = 30;
/// a video nobody asked a frame of for this long: ffmpeg stops
pub const IDLE_S: f64 = 5.0;
/// frames of a video decoded before their moment (ffmpeg waits for room meanwhile)
pub const AHEAD: usize = 3;
/// a video further behind its moment than this (after a pause): on from there
pub const BEHIND_S: f64 = 0.5;
pub const START_TIMEOUT_S: f64 = 10.0;

/// time.monotonic(), as the feeds' own clock: seconds from the first time it is read.
pub fn monotonic() -> f64 {
    static START: OnceLock<Instant> = OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

/// _filter: ffmpeg's filter that fits the video into w x h as the widget's `fit` says.
fn filter(w: usize, h: usize, fit: &str) -> String {
    match fit {
        "cover" => format!("scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"),
        "contain" => format!(
            "scale={w}:{h}:force_original_aspect_ratio=decrease,\
             pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0"
        ),
        _ => format!("scale={w}:{h}"),
    }
}

/// How a video is read: its size, how it is fitted, how many frames a second, and the rest of
/// video_command's arguments.
#[derive(Clone, Debug, PartialEq)]
pub struct Reading {
    pub w: usize,
    pub h: usize,
    pub fit: String,
    pub fps: u32,
    /// its first frame alone
    pub once: bool,
    pub speed: f64,
    pub start: f64,
    /// round and round (False: to its end)
    pub rounds: bool,
    /// its keyframes alone
    pub keys: bool,
}

/// video_command: ffmpeg reading the video at `path` into raw RGBA frames of w x h on its
/// output, `fps` of them to a second of it played `speed` times as fast, round and round
/// (`rounds` false: to its end; `once`: its first frame alone), from `start` seconds into it;
/// `keys`: only its keyframes decoded, each shown until the next.
pub fn video_command(ffmpeg: &Path, path: &Path, r: &Reading) -> Vec<OsString> {
    let mut command: Vec<OsString> = vec![ffmpeg.into()];
    command.extend(["-v", "error", "-nostdin"].map(OsString::from));
    if r.keys {
        command.extend(["-skip_frame", "nokey"].map(OsString::from));
    }
    let mut paced = String::new();
    if !r.once {
        if r.rounds {
            command.extend(["-stream_loop", "-1"].map(OsString::from));
        }
        if r.speed != 1.0 {
            // (its frames' times so)
            paced = format!("setpts=PTS/{},", lp_py::format_g(r.speed, 6));
        }
    }
    if r.start > 0.0 {
        // (to the picture before, decoded on from there)
        command.push("-ss".into());
        command.push(format!("{:.3}", r.start).into());
    }
    let fitted = filter(r.w, r.h, &r.fit);
    let vf = if r.once {
        fitted
    } else {
        format!("{paced}{fitted},fps={}", r.fps)
    };
    command.push("-i".into());
    command.push(path.into());
    command.extend(["-an", "-sn", "-vf"].map(OsString::from));
    command.push(vf.into());
    command.extend(["-pix_fmt", "rgba", "-f", "rawvideo"].map(OsString::from));
    if r.once {
        command.extend(["-frames:v", "1"].map(OsString::from));
    }
    command.push("pipe:1".into());
    command
}

/// No console window for ffmpeg on Windows (_quiet).
fn quiet(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let _ = command;
}

/// subprocess.run(command, capture_output=True, timeout=…): its output and what it said, or
/// None where it did not start or took too long (killed then).
fn run(command: &[OsString], timeout: f64) -> Option<(Vec<u8>, Vec<u8>)> {
    let (program, args) = command.split_first()?;
    let mut cmd = Command::new(program);
    cmd.args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    quiet(&mut cmd);
    let mut child = cmd.spawn().ok()?;
    let take = |pipe: Option<Box<dyn Read + Send>>| {
        std::thread::spawn(move || {
            let mut out = Vec::new();
            if let Some(mut pipe) = pipe {
                let _ = pipe.read_to_end(&mut out);
            }
            out
        })
    };
    let out = take(
        child
            .stdout
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let err = take(
        child
            .stderr
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let deadline = Instant::now() + Duration::from_secs_f64(timeout);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(5)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
    Some((out.join().ok()?, err.join().ok()?))
}

/// VideoInfo: what ffmpeg reads of a video file: how long it plays (seconds), the size its
/// pictures come out at (turned as it says, as ffmpeg turns them) and its frames a second;
/// None where it cannot tell.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct VideoInfo {
    pub length: Option<f64>,
    pub size: Option<(i64, i64)>,
    pub fps: Option<f64>,
}

/// video_info's reading of what `ffmpeg -i` says: Err where Python's raises (a turn too large
/// for an int: round() of infinity).
pub fn read_said(said: &[u8]) -> Result<VideoInfo, String> {
    let length = said::duration(said).and_then(|(hours, minutes, seconds)| {
        let all = hours * 3600.0 + minutes * 60.0 + seconds;
        (all != 0.0).then_some(all)
    });
    let mut size = said::size(said);
    if let (Some((w, h)), Some(turn)) = (size, said::turn(said)) {
        if !turn.is_finite() {
            return Err("cannot convert float infinity to integer".into());
        }
        // round(…) % 180 == 90 (Python's round, its % never below 0)
        let turned = turn.round_ties_even() % 180.0;
        if (turned + 180.0) % 180.0 == 90.0 {
            size = Some((h, w)); // (played upright: ffmpeg turns it)
        }
    }
    let fps = said::rate(said).filter(|&f| f != 0.0);
    Ok(VideoInfo { length, size, fps })
}

/// video_info: a video's length, size and frames a second, from what `ffmpeg -i` says of it.
pub fn video_info(ffmpeg: &Path, path: &Path) -> Result<VideoInfo, String> {
    let command: Vec<OsString> = vec![
        ffmpeg.into(),
        "-hide_banner".into(),
        "-nostdin".into(),
        "-i".into(),
        path.into(),
    ];
    match run(&command, START_TIMEOUT_S) {
        Some((_, said)) => read_said(&said),
        None => Ok(VideoInfo::default()),
    }
}

/// first_frame: the video's first frame at w x h (`start` seconds into it: the one there);
/// None where ffmpeg cannot read it.
pub fn first_frame(
    ffmpeg: &Path,
    path: &Path,
    w: usize,
    h: usize,
    fit: &str,
    start: f64,
) -> Option<Image> {
    let reading = Reading {
        w,
        h,
        fit: fit.into(),
        fps: VIDEO_FPS,
        once: true,
        speed: 1.0,
        start,
        rounds: true,
        keys: false,
    };
    let (out, _) = run(&video_command(ffmpeg, path, &reading), START_TIMEOUT_S)?;
    let size = w * h * 4;
    if out.len() < size {
        return None;
    }
    Image::from_bytes(Mode::RGBA, w, h, out[..size].to_vec()).ok()
}

/// A frame of a video as it came, and whether its video has no transparency anywhere (its
/// first frame says: moving.solid), so it is laid without its alpha.
#[derive(Clone, Debug)]
pub struct Frame {
    pub picture: Arc<Image>,
    pub solid: bool,
}

/// What a feed and its reading thread share.
struct State {
    /// frames before their moment
    ready: VecDeque<Frame>,
    newest: Option<Frame>,
    /// frames shown: a new one is a new picture to draw
    count: i64,
    /// the moment of its first frame (the first ask once one is there; moved on after a
    /// pause): frame n shows from began + n / fps
    began: Option<f64>,
    asked: f64,
    /// the ffmpeg reading now (its number; 0: none) and its process
    process: u64,
    child: Option<Child>,
    /// the number of the latest reading thread, and whether it still runs
    thread: u64,
    running: bool,
    /// when ffmpeg last ended by itself (tried again after a while)
    failed: f64,
    /// played to its end (`rounds` false): its last frame stays
    ended: bool,
    started: u64,
}

/// VideoFeed: a video played round and round by ffmpeg into frames of w x h, `fps` to a
/// second; ffmpeg decodes up to AHEAD frames before their moment and then waits.
pub struct VideoFeed {
    pub command: Vec<OsString>,
    pub name: String,
    pub size: (usize, usize),
    pub fps: u32,
    rounds: bool,
    shared: Arc<(Mutex<State>, Condvar)>,
    handle: Mutex<Option<JoinHandle<()>>>,
}

fn lock(shared: &(Mutex<State>, Condvar)) -> MutexGuard<'_, State> {
    shared.0.lock().unwrap_or_else(|e| e.into_inner())
}

impl VideoFeed {
    pub fn new(ffmpeg: &Path, path: &Path, reading: Reading) -> VideoFeed {
        let command = video_command(ffmpeg, path, &reading);
        VideoFeed {
            command,
            name: path
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default(),
            size: (reading.w, reading.h),
            fps: reading.fps,
            rounds: reading.rounds,
            shared: Arc::new((
                Mutex::new(State {
                    ready: VecDeque::new(),
                    newest: None,
                    count: 0,
                    began: None,
                    asked: 0.0,
                    process: 0,
                    child: None,
                    thread: 0,
                    running: false,
                    failed: -IDLE_S,
                    ended: false,
                    started: 0,
                }),
                Condvar::new(),
            )),
            handle: Mutex::new(None),
        }
    }

    /// begin_at: its first frame shows at `at` (on the clock its frames are asked on), not
    /// at the first ask once one is there (VideoFeed's `began`).
    pub fn begin_at(&self, at: f64) {
        lock(&self.shared).began = Some(at);
    }

    /// Whether it played to its end (`rounds` false).
    pub fn ended(&self) -> bool {
        lock(&self.shared).ended
    }

    /// frame: the number of the frame of the moment `at` (on any clock, the same each time;
    /// the feed's own by default) and the frame (None before the first).
    pub fn frame(&self, at: Option<f64>) -> (i64, Option<Frame>) {
        let now = monotonic();
        let at = at.unwrap_or(now);
        let mut st = lock(&self.shared);
        st.asked = now;
        if !st.running && !st.ended && now - st.failed >= IDLE_S {
            self.start(&mut st);
        }
        if !st.ready.is_empty() && st.began.is_none() {
            st.began = Some(at); // (the first frame: now)
        }
        if let Some(began) = st.began {
            let fps = f64::from(self.fps);
            let due = ((at - began) * fps + 1e-6) as i64; // the frame of the moment
            while st.count <= due {
                let Some(next) = st.ready.pop_front() else {
                    break;
                };
                st.newest = Some(next);
                st.count += 1;
                self.shared.1.notify_all();
            }
            if (due - st.count) as f64 > BEHIND_S * fps {
                // (a pause: from the one it has)
                st.began = Some(at - (st.count - 1).max(0) as f64 / fps);
            }
        }
        (st.count, st.newest.clone())
    }

    /// _start: ffmpeg, and a thread reading its frames.
    fn start(&self, st: &mut State) {
        let (program, args) = self.command.split_first().expect("a command");
        let mut cmd = Command::new(program);
        cmd.args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        quiet(&mut cmd);
        let mut child = match cmd.spawn() {
            Ok(child) => child,
            Err(_) => {
                st.failed = monotonic(); // (ffmpeg did not start)
                return;
            }
        };
        let Some(stdout) = child.stdout.take() else {
            let _ = child.kill();
            let _ = child.wait();
            st.failed = monotonic();
            return;
        };
        st.started += 1;
        let number = st.started;
        st.process = number;
        st.child = Some(child);
        st.thread = number;
        st.running = true;
        let shared = self.shared.clone();
        let (size, rounds) = (self.size, self.rounds);
        let handle = std::thread::Builder::new()
            .name("video-feed".into())
            .spawn(move || read(shared, stdout, number, size, rounds));
        match handle {
            Ok(handle) => {
                if let Ok(mut kept) = self.handle.lock() {
                    *kept = Some(handle);
                }
            }
            Err(_) => {
                st.running = false;
                st.process = 0;
                if let Some(mut child) = st.child.take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                st.failed = monotonic();
            }
        }
    }

    /// close: ffmpeg stopped, the frames let go; shown again, it begins with its first frame.
    pub fn close(&self) {
        let child = {
            let mut st = lock(&self.shared);
            st.process = 0;
            st.asked = -IDLE_S * 2.0;
            st.newest = None;
            st.ready.clear();
            st.began = None;
            st.ended = false;
            self.shared.1.notify_all();
            st.child.take()
        };
        if let Some(mut child) = child {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    /// from_start: play it from its start (a slideshow comes to it again): ffmpeg starts anew
    /// with the next ask, and its first frame shows until the new one's first comes.
    pub fn from_start(&self) {
        self.close();
        let deadline = Instant::now() + Duration::from_secs(1);
        let mut st = lock(&self.shared);
        while st.running && Instant::now() < deadline {
            st = self
                .shared
                .1
                .wait_timeout(st, Duration::from_millis(20))
                .map(|(g, _)| g)
                .unwrap_or_else(|e| e.into_inner().0);
        }
        st.failed = -IDLE_S; // (not a failure: at once)
    }

    /// For the comparisons: wait until the thread has its frames ahead (or no more come), so
    /// that what the next ask gives does not depend on how fast ffmpeg is.
    pub fn settle(&self, timeout: Duration) {
        let deadline = Instant::now() + timeout;
        let mut st = lock(&self.shared);
        while st.running && st.ready.len() < AHEAD && Instant::now() < deadline {
            st = self
                .shared
                .1
                .wait_timeout(st, Duration::from_millis(5))
                .map(|(g, _)| g)
                .unwrap_or_else(|e| e.into_inner().0);
        }
    }
}

impl Drop for VideoFeed {
    fn drop(&mut self) {
        self.close();
    }
}

/// How many bytes `stream` gives into `buf` until it is full or ends.
fn fill(stream: &mut impl Read, buf: &mut [u8]) -> usize {
    let mut got = 0;
    while got < buf.len() {
        match stream.read(&mut buf[got..]) {
            Ok(0) => break,
            Ok(n) => got += n,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => break,
        }
    }
    got
}

/// _read: the frames of ffmpeg `number`, each kept for its moment, AHEAD at most; it stops
/// where ffmpeg ends (a video it cannot read, its end, or it was stopped) or nobody asked for
/// a frame for IDLE_S (another theme).
fn read(
    shared: Arc<(Mutex<State>, Condvar)>,
    mut stream: std::process::ChildStdout,
    number: u64,
    (w, h): (usize, usize),
    rounds: bool,
) {
    let size = w * h * 4;
    let mut opaque: Option<bool> = None;
    let mut whole = false;
    loop {
        let mut data = vec![0u8; size];
        let got = fill(&mut stream, &mut data);
        if got < size {
            // (it ended: a video it cannot read, or it was stopped)
            whole = got == 0 && !rounds && opaque.is_some();
            break;
        }
        let solid = *opaque.get_or_insert_with(|| data.chunks_exact(4).all(|px| px[3] == 255));
        let Ok(picture) = Image::from_bytes(Mode::RGBA, w, h, data) else {
            break;
        };
        let mut st = lock(&shared);
        while st.ready.len() >= AHEAD && st.process == number && monotonic() - st.asked <= IDLE_S {
            // (ffmpeg waits on its pipe meanwhile)
            st = shared
                .1
                .wait_timeout(st, Duration::from_millis(500))
                .map(|(g, _)| g)
                .unwrap_or_else(|e| e.into_inner().0);
        }
        if st.process != number {
            break; // closed
        }
        st.ready.push_back(Frame {
            picture: Arc::new(picture),
            solid,
        });
        shared.1.notify_all();
        let idle = monotonic() - st.asked > IDLE_S;
        drop(st);
        if idle {
            break; // nobody looks: another theme
        }
    }
    let child = {
        let mut st = lock(&shared);
        let mine = st.process == number;
        let child = if mine { st.child.take() } else { None };
        if mine {
            st.process = 0;
            if whole {
                st.ended = true;
            } else if monotonic() - st.asked <= IDLE_S {
                st.failed = monotonic(); // (it ended by itself: ffmpeg stopped)
            }
        }
        if st.thread == number {
            st.running = false;
        }
        shared.1.notify_all();
        child
    };
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// What `ffmpeg -i` says, read as moving.video_info's patterns (Python's re on bytes: \d the
/// ASCII digits, \s the ASCII spaces, the first match from the left, each lazy part as short
/// as it can be).
mod said {
    /// How many ASCII digits `said` has from `at` on.
    fn digits(said: &[u8], at: usize) -> usize {
        said.get(at..).map_or(0, |rest| {
            rest.iter().take_while(|b| b.is_ascii_digit()).count()
        })
    }

    /// Where `needle` comes in `said` from `from` on, whole before `to`, in turn.
    fn places<'a>(
        said: &'a [u8],
        needle: &'a [u8],
        from: usize,
        to: usize,
    ) -> impl Iterator<Item = usize> + 'a {
        (from..to.saturating_sub(needle.len() - 1))
            .filter(move |&at| said[at..].starts_with(needle))
    }

    /// The number Python's float() makes of ASCII digits (and a point).
    fn number(text: &[u8]) -> f64 {
        std::str::from_utf8(text)
            .ok()
            .and_then(|t| t.parse().ok())
            .unwrap_or(f64::NAN)
    }

    /// rb"Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)": hours, minutes and seconds.
    pub(super) fn duration(said: &[u8]) -> Option<(f64, f64, f64)> {
        for at in places(said, b"Duration: ", 0, said.len()) {
            let h0 = at + 10;
            let hn = digits(said, h0);
            if hn == 0 || said.get(h0 + hn) != Some(&b':') {
                continue;
            }
            let m0 = h0 + hn + 1;
            if digits(said, m0) < 2 || said.get(m0 + 2) != Some(&b':') {
                continue;
            }
            let s0 = m0 + 3;
            if digits(said, s0) < 2 {
                continue;
            }
            let mut s1 = s0 + 2;
            if said.get(s1) == Some(&b'.') && digits(said, s1 + 1) > 0 {
                s1 += 1 + digits(said, s1 + 1);
            }
            return Some((
                number(&said[h0..h0 + hn]),
                number(&said[m0..m0 + 2]),
                number(&said[s0..s1]),
            ));
        }
        None
    }

    /// The end of the line `at` is on (the first newline from `at` on, else the end).
    fn line_end(said: &[u8], at: usize) -> usize {
        said[at..]
            .iter()
            .position(|&b| b == b'\n')
            .map_or(said.len(), |n| at + n)
    }

    /// rb"Stream #[^\n]*?Video: [^\n]*?, " and then `tail` at the place after the comma's
    /// space: the first that matches, from the left.
    fn after_video<T>(said: &[u8], tail: impl Fn(usize) -> Option<T>) -> Option<T> {
        for s in places(said, b"Stream #", 0, said.len()) {
            let end = line_end(said, s + 8);
            for v in places(said, b"Video: ", s + 8, end) {
                for c in places(said, b", ", v + 7, end) {
                    if let Some(found) = tail(c + 2) {
                        return Some(found);
                    }
                }
            }
        }
        None
    }

    /// rb"Stream #[^\n]*?Video: [^\n]*?, (\d{2,5})x(\d{2,5})": the width and height.
    pub(super) fn size(said: &[u8]) -> Option<(i64, i64)> {
        after_video(said, |at| {
            let wn = digits(said, at);
            if !(2..=5).contains(&wn) || said.get(at + wn) != Some(&b'x') {
                return None;
            }
            let h0 = at + wn + 1;
            let hn = digits(said, h0).min(5);
            if hn < 2 {
                return None;
            }
            let int = |t: &[u8]| std::str::from_utf8(t).ok()?.parse::<i64>().ok();
            Some((int(&said[at..at + wn])?, int(&said[h0..h0 + hn])?))
        })
    }

    /// rb"Stream #[^\n]*?Video: [^\n]*?, (\d+(?:\.\d+)?) (?:fps|tbr)": the frames a second.
    pub(super) fn rate(said: &[u8]) -> Option<f64> {
        after_video(said, |at| {
            let n = digits(said, at);
            if n == 0 {
                return None;
            }
            let mut end = at + n;
            if said.get(end) == Some(&b'.') && digits(said, end + 1) > 0 {
                end += 1 + digits(said, end + 1);
            }
            let unit = said.get(end + 1..end + 4)?;
            (said[end] == b' ' && (unit == b"fps" || unit == b"tbr"))
                .then(|| number(&said[at..end]))
        })
    }

    /// Python's \s on bytes.
    fn space(b: u8) -> bool {
        matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
    }

    /// rb"rotation of (-?\d+(?:\.\d+)?) degrees|rotate\s*:\s*(-?\d+)": the number of the first
    /// that matches, from the left (at each place the first way first).
    pub(super) fn turn(said: &[u8]) -> Option<f64> {
        let signed = |at: usize, point: bool| -> Option<usize> {
            let first = at + usize::from(said.get(at) == Some(&b'-'));
            let n = digits(said, first);
            if n == 0 {
                return None;
            }
            let mut end = first + n;
            if point && said.get(end) == Some(&b'.') && digits(said, end + 1) > 0 {
                end += 1 + digits(said, end + 1);
            }
            Some(end)
        };
        for at in 0..said.len() {
            if said[at..].starts_with(b"rotation of ") {
                if let Some(end) = signed(at + 12, true) {
                    if said[end..].starts_with(b" degrees") {
                        return Some(number(&said[at + 12..end]));
                    }
                }
            }
            if said[at..].starts_with(b"rotate") {
                let mut i = at + 6;
                while said.get(i).is_some_and(|&b| space(b)) {
                    i += 1;
                }
                if said.get(i) == Some(&b':') {
                    i += 1;
                    while said.get(i).is_some_and(|&b| space(b)) {
                        i += 1;
                    }
                    if let Some(end) = signed(i, false) {
                        return Some(number(&said[i..end]));
                    }
                }
            }
        }
        None
    }
}
