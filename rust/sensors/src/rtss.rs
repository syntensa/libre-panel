//! Frames per second of the game you play on Windows, from RivaTuner Statistics Server
//! (sensors/rtss.py).
//!
//! RTSS measures the games it shows its overlay in and shares what it measures in a memory
//! of its own, `RTSSSharedMemoryV2`. This source reads that memory, ten times a second, while
//! RTSS runs and a theme or a service shows `game.*` readings. It starts no program, needs
//! no rights and touches no game. Without RTSS it reads nothing, and looks again every few
//! seconds.
//!
//! Readings, as PresentMon's: `game.fps`, `game.frametime`, `game.low`, `game.app` and what
//! the round's frames come to. RTSS shares the time of the last frame, not every frame: the
//! frame times are those of its samples, ten a second.

use std::collections::HashMap;
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use crate::frames::FrameCounter;
use crate::lhm::Readings;
use crate::service::{wanted, Wanted};
use crate::shm::{u32_at, Memory};

pub const MAPPING: &str = "RTSSSharedMemoryV2";
/// "RTSS", as a DWORD
const SIGNATURE: u32 = 0x5254_5353;
const FIRST_VERSION: u32 = 0x0002_0000;
/// signature, version, entry size, entries' offset, entries
const HEADER_SIZE: usize = 20;
/// pid, path (260), flags, time0, time1, frames, frame time (µs)
const ENTRY_SIZE: usize = 4 + 260 + 20;
/// (a header that says more is not RTSS's)
const MOST_ENTRIES: u32 = 1024;
const POLL_SECONDS: f64 = 0.1;
/// a program whose entry changed this long ago still draws (RTSS's period is a second: a game
/// capped at an even frame time keeps its frame time, but not its period)
const ACTIVE_SECONDS: f64 = 2.0;
/// without RTSS: how often it is looked for again
const LOOK_SECONDS: f64 = 5.0;
/// nobody looked for this long: the thread ends, and starts on demand
const IDLE_SECONDS: f64 = 60.0;

/// A program RTSS measures, as its entry has it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    pub pid: u32,
    /// its file's name ("game.exe")
    pub app: String,
    /// when its last period began and ended, ms (GetTickCount)
    pub time0: u32,
    pub time1: u32,
    /// the frames in it
    pub frames: u32,
    /// the last frame's time, µs
    pub frame_us: u32,
}

/// A path's bytes as text: by the system's code page on Windows, Latin-1 elsewhere.
fn path_text(raw: &[u8]) -> String {
    #[cfg(windows)]
    {
        use windows_sys::Win32::Globalization::{MultiByteToWideChar, CP_ACP};
        if raw.is_empty() {
            return String::new();
        }
        let mut wide = vec![0u16; raw.len()];
        // SAFETY: `raw` has `raw.len()` bytes to read and `wide` room for as many characters
        // (a byte never becomes more than one).
        let count = unsafe {
            MultiByteToWideChar(
                CP_ACP,
                0,
                raw.as_ptr(),
                raw.len() as i32,
                wide.as_mut_ptr(),
                wide.len() as i32,
            )
        };
        String::from_utf16_lossy(&wide[..usize::try_from(count).unwrap_or(0)])
    }
    #[cfg(not(windows))]
    {
        raw.iter().map(|&byte| char::from(byte)).collect()
    }
}

/// entries: the programs RTSS measures now; none where the memory is no RTSS's (or RTSS is
/// ending); an error where the memory ends before its entries do.
pub fn entries(memory: &dyn Memory) -> Result<Vec<Entry>, String> {
    let header = memory.read(0, HEADER_SIZE)?;
    let field = |n: usize| u32_at(&header, 4 * n).unwrap_or(0);
    let (signature, version, size, offset, count) =
        (field(0), field(1), field(2), field(3), field(4));
    if signature != SIGNATURE || version < FIRST_VERSION || (size as usize) < ENTRY_SIZE {
        return Ok(Vec::new());
    }
    let mut found = Vec::new();
    for n in 0..count.min(MOST_ENTRIES) {
        let at = offset as usize + n as usize * size as usize;
        let entry = memory.read(at, ENTRY_SIZE)?;
        let field = |n: usize| u32_at(&entry, 264 + 4 * n).unwrap_or(0);
        let pid = u32_at(&entry, 0).unwrap_or(0);
        if pid == 0 {
            continue; // (a free entry)
        }
        let path = &entry[4..264];
        let path = path.split(|&byte| byte == 0).next().unwrap_or(&[]);
        let name = path_text(path).replace('/', "\\");
        let app = name.rsplit('\\').next().unwrap_or("");
        if !app.is_empty() {
            found.push(Entry {
                pid,
                app: app.to_string(),
                time0: field(1),
                time1: field(2),
                frames: field(3),
                frame_us: field(4),
            });
        }
    }
    Ok(found)
}

/// What was last read of each program: (time1, frame time), and when they last changed
/// (none: not seen before).
#[derive(Default)]
pub struct Seen(HashMap<u32, ((u32, u32), Option<f64>)>);

impl Seen {
    /// poll: one look at RTSS's memory: the last frame time of each program that still draws
    /// (its entry changed in the last `ACTIVE_SECONDS`), as a sample of its frames.
    pub fn poll(
        &mut self,
        memory: &dyn Memory,
        now: f64,
        counter: &mut FrameCounter,
    ) -> Result<(), String> {
        let found = entries(memory)?;
        let mut seen = HashMap::new();
        for entry in found {
            let mark = (entry.time1, entry.frame_us);
            let changed = match self.0.get(&entry.pid) {
                None => None,
                Some((before, _)) if *before != mark => Some(now),
                Some((_, changed)) => *changed,
            };
            seen.insert(entry.pid, (mark, changed));
            let drawing = changed.is_some_and(|changed| now - changed <= ACTIVE_SECONDS);
            if drawing && entry.frame_us > 0 {
                counter.add(&entry.app, f64::from(entry.frame_us) / 1000.0, now, None);
            }
        }
        self.0 = seen;
        Ok(())
    }
}

type Opener = Box<dyn Fn(&str) -> Option<Box<dyn Memory>> + Send + Sync>;

struct Shared {
    counter: Mutex<FrameCounter>,
    /// (stop, the thread runs, RTSS's memory was there at the last look)
    flags: Mutex<(bool, bool, bool)>,
    changed: Condvar,
    notes: Mutex<Vec<String>>,
    wanted: Wanted,
    mapping: String,
    opener: Opener,
    began: Instant,
}

impl Shared {
    fn note(&self, text: String) {
        if let Ok(mut notes) = self.notes.lock() {
            notes.push(text);
        }
    }

    fn set_running(&self, running: bool) {
        if let Ok(mut flags) = self.flags.lock() {
            flags.2 = running;
        }
    }
}

fn run(shared: &Shared) {
    let mut memory: Option<Box<dyn Memory>> = None;
    let mut seen = Seen::default();
    let mut looked = Instant::now();
    let mut tried: Option<Instant> = None;
    let mut running = false;
    loop {
        if shared.flags.lock().map(|flags| flags.0).unwrap_or(true) {
            break;
        }
        let now = Instant::now();
        if wanted(&shared.wanted.keys(), "game.") {
            looked = now;
        } else if now.duration_since(looked).as_secs_f64() > IDLE_SECONDS {
            break;
        }
        let due = tried.is_none_or(|at| now.duration_since(at).as_secs_f64() >= LOOK_SECONDS);
        if memory.is_none() && due {
            tried = Some(now);
            memory = (shared.opener)(&shared.mapping);
            if memory.is_some() && !running {
                shared.note("rtss: reading RivaTuner's frame rates".into());
            }
            running = memory.is_some();
            shared.set_running(running);
        }
        if let Some(open) = &memory {
            let at = now.duration_since(shared.began).as_secs_f64();
            let polled = match shared.counter.lock() {
                Ok(mut counter) => seen.poll(open.as_ref(), at, &mut counter),
                Err(_) => break,
            };
            match polled {
                Err(why) => {
                    shared.note(format!("rtss: its memory is gone ({why})"));
                    memory = None;
                    running = false;
                    shared.set_running(false);
                    seen = Seen::default();
                }
                Ok(()) => {
                    let due =
                        tried.is_none_or(|at| now.duration_since(at).as_secs_f64() >= LOOK_SECONDS);
                    if due {
                        // (RTSS ended: let its memory go, and look for it anew)
                        memory = None;
                        tried = None;
                    }
                }
            }
        }
        let Ok(flags) = shared.flags.lock() else {
            break;
        };
        let wait = Duration::from_secs_f64(POLL_SECONDS);
        if shared
            .changed
            .wait_timeout_while(flags, wait, |flags| !flags.0)
            .map(|(flags, _)| flags.0)
            .unwrap_or(true)
        {
            break;
        }
    }
    if let Ok(mut flags) = shared.flags.lock() {
        flags.1 = false;
    }
}

/// `game.*` from RTSS's shared memory, while a theme or a service shows them (RTSSProvider).
pub struct RtssSource {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl RtssSource {
    /// `mapping`: the memory's name ("" for RTSS's own; another for the tests).
    pub fn new(mapping: &str, wanted: Wanted) -> RtssSource {
        RtssSource::with(mapping, wanted, Box::new(crate::shm::open))
    }

    /// As `new`, the memory opened by `opener`.
    pub fn with(mapping: &str, wanted: Wanted, opener: Opener) -> RtssSource {
        RtssSource {
            shared: Arc::new(Shared {
                // (samples of the last frame's time)
                counter: Mutex::new(FrameCounter::new(false)),
                flags: Mutex::new((false, false, false)),
                changed: Condvar::new(),
                notes: Mutex::new(Vec::new()),
                wanted,
                mapping: if mapping.is_empty() { MAPPING } else { mapping }.to_string(),
                opener,
                began: Instant::now(),
            }),
            thread: None,
        }
    }

    /// Which program is the game, as the `game` service finds it; none takes it back.
    pub fn set_target(&self, exe: Option<&str>) {
        if let Ok(mut counter) = self.shared.counter.lock() {
            counter.set_target(exe);
        }
    }

    /// Whether RTSS runs, as the last look found it; none while nothing shows `game.*`.
    pub fn status(&self) -> Option<bool> {
        let flags = self.shared.flags.lock().ok()?;
        flags.1.then_some(flags.2)
    }

    pub fn read(&mut self) -> Readings {
        let (stopped, runs) = self
            .shared
            .flags
            .lock()
            .map(|flags| (flags.0, flags.1))
            .unwrap_or((true, false));
        if !runs {
            if stopped || !wanted(&self.shared.wanted.keys(), "game.") {
                return Readings::new();
            }
            if let Some(ended) = self.thread.take() {
                let _ = ended.join();
            }
            if let Ok(mut flags) = self.shared.flags.lock() {
                flags.1 = true;
            }
            let shared = self.shared.clone();
            self.thread = std::thread::Builder::new()
                .name("sensors-rtss".into())
                .spawn(move || run(&shared))
                .ok();
        }
        let now = self.shared.began.elapsed().as_secs_f64();
        let counter = self.shared.counter.lock();
        counter
            .map(|mut counter| counter.readings(now))
            .unwrap_or_default()
    }

    /// What the source noted for the log since it was last asked.
    pub fn notes(&self) -> Vec<String> {
        let notes = self.shared.notes.lock();
        notes
            .map(|mut notes| std::mem::take(&mut *notes))
            .unwrap_or_default()
    }

    pub fn close(&mut self) {
        if let Ok(mut flags) = self.shared.flags.lock() {
            flags.0 = true;
        }
        self.shared.changed.notify_all();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for RtssSource {
    fn drop(&mut self) {
        self.close();
    }
}
