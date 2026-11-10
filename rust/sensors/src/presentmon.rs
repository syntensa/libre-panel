//! Frames per second of the game you play, through Intel's PresentMon (Windows;
//! sensors/presentmon.py's source and its looking after the program).
//!
//! PresentMon (MIT license) is a separate program. The Windows downloads carry its console
//! version in their `tools` folder; otherwise it is named in `[sensors.presentmon]` (`path`)
//! or found on the PATH. It reads the frames Windows shows, which needs administrator rights
//! or membership in the "Performance Log Users" group. It never touches the game: no hook, no
//! memory read, no overlay.
//!
//! The recording session must exist *before* the game starts: the anti-cheat of some games
//! refuses to let a new session begin while the game runs, but leaves one that is already
//! running alone. So PresentMon runs for as long as a theme shows `game.*` readings (it stops
//! a minute after none does), and is started again only slowly while a game runs. It sits in
//! a Windows job that ends it with Libre Panel, whatever way Libre Panel ends. While Windows
//! ends the session it is not started again, nor to stop its recording session: Windows
//! refuses new programs then, with a box that names PresentMon.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use crate::frames::{csv_row, Columns, FrameCounter};
use crate::lhm::Readings;
use crate::service::{wanted, Wanted};

/// the one Windows recording session of Libre Panel
pub const SESSION_NAME: &str = "LibrePanel";
/// how often the session is looked after
pub const SUPERVISE_SECONDS: f64 = 1.0;
/// nobody reads game.* for this long: PresentMon ends, and starts on demand
pub const IDLE_SECONDS: f64 = 60.0;
/// a PresentMon that lives this long has its session: old complaints are over
pub const STEADY_SECONDS: f64 = 3.0;
/// it ended after it had worked: start it again after this long
pub const RESTART_SECONDS: f64 = 2.0;
/// it ended at once: wait this long, doubling up to the second value
pub const RETRY_SECONDS: (f64, f64) = (2.0, 60.0);
/// the anti-cheat blocks a new session, or a game runs: try this seldom
pub const BLOCKED_SECONDS: f64 = 15.0;
/// PresentMon is not there: look again after this long
pub const NOT_FOUND_SECONDS: f64 = 30.0;
/// a PresentMon that does not end when asked is killed after this long
pub const STOP_SECONDS: f64 = 5.0;

/// Windows ends the session (the user logs off, the computer shuts down): the program says so
/// here, and PresentMon is not started again.
pub static SESSION_ENDING: AtomicBool = AtomicBool::new(false);

/// find_presentmon: the PresentMon program: the path named in the settings, the one a
/// download carries (its `tools` folder), PATH, or Intel's folder under Program Files. With
/// the highest rights only one from a safe place: a named one elsewhere is passed over for
/// the others (and said in `notes`).
pub fn find_presentmon(path: &str, notes: &mut Vec<String>) -> Option<PathBuf> {
    use lp_devices::rights;
    let mut passed = Vec::new();
    if !path.is_empty() {
        let named = if Path::new(path).is_file() {
            Some(PathBuf::from(path))
        } else {
            rights::which(path, &mut passed)
        };
        match named {
            Some(named) if rights::safe_place(&named) => return Some(named),
            None if passed.is_empty() => return None,
            Some(named) => passed.push(named),
            None => {}
        }
        for program in passed.drain(..) {
            notes.push(format!(
                "PresentMon {} is not started: Libre Panel runs with the highest rights",
                program.display()
            ));
        }
    }
    let name = if cfg!(windows) {
        "PresentMon.exe"
    } else {
        "PresentMon"
    };
    let own = std::env::current_exe()
        .ok()
        .and_then(|program| std::fs::canonicalize(program).ok())
        .and_then(|program| Some(program.parent()?.join("tools").join(name)));
    if let Some(own) = own.filter(|own| own.is_file()) {
        return Some(own);
    }
    for name in ["PresentMon", "PresentMon-x64", "presentmon"] {
        if let Some(found) = rights::which(name, &mut passed) {
            return Some(found);
        }
    }
    // Only where installing needs administrator rights: Libre Panel may run elevated, and a
    // PresentMon*.exe anyone can drop into the downloads must not be started with that.
    let folder = Path::new("C:/Program Files/Intel/PresentMon");
    let mut matches: Vec<PathBuf> = std::fs::read_dir(folder)
        .into_iter()
        .flatten()
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name().and_then(|n| n.to_str()).is_some_and(|n| {
                n.starts_with("PresentMon") && n.to_ascii_lowercase().ends_with(".exe")
            })
        })
        .collect();
    matches.sort();
    matches.pop()
}

/// A Windows job that ends everything in it when its last handle closes, which is when this
/// process ends, however it ends. Without it a PresentMon would run on after a Libre Panel
/// that was killed, until its output pipe is full.
#[cfg(windows)]
mod job {
    use std::os::windows::io::AsRawHandle;
    use std::sync::OnceLock;

    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    /// The one job of this process, made on first use; none if Windows will not make it.
    fn shared() -> Option<isize> {
        static JOB: OnceLock<Option<isize>> = OnceLock::new();
        *JOB.get_or_init(|| {
            // SAFETY: the job's handle is this process's own, kept until it ends; the
            // limits are a structure of this frame, of the size given.
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() {
                    return None;
                }
                let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    (&raw const limits).cast(),
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                ) == 0
                {
                    CloseHandle(handle);
                    return None;
                }
                Some(handle as isize)
            }
        })
    }

    /// Puts a started program in the job. False if that is not possible (it may be in a job
    /// whose rules do not allow it): it then runs without this safeguard.
    pub fn add(process: &std::process::Child) -> bool {
        let Some(job) = shared() else {
            return false;
        };
        // SAFETY: both handles are open: the job's for good, the child's while it is held.
        unsafe { AssignProcessToJobObject(job as _, process.as_raw_handle() as _) != 0 }
    }
}

/// One PresentMon process and what is known about its output.
struct Run {
    process: Child,
    started: Instant,
    /// what its readers tell: the last lines of stderr, whether a header row came (the
    /// session works), and whether this source ended it
    told: Arc<Told>,
    /// it has lived long enough to have its session
    steady: bool,
    readers: Vec<JoinHandle<()>>,
}

#[derive(Default)]
struct Told {
    errors: Mutex<VecDeque<String>>,
    header: AtomicBool,
    ended_by_us: AtomicBool,
}

struct Shared {
    counter: Mutex<FrameCounter>,
    /// (stop, the thread runs)
    flags: Mutex<(bool, bool)>,
    changed: Condvar,
    notes: Mutex<Vec<String>>,
    /// the problem the log has told, so that it says it once; why there is no data, for a
    /// status display; the anti-cheat of a game refuses the session
    said: Mutex<Option<String>>,
    problem: Mutex<Option<String>>,
    blocked: AtomicBool,
    wanted: Wanted,
    path: String,
    began: Instant,
}

impl Shared {
    fn now(&self) -> f64 {
        self.began.elapsed().as_secs_f64()
    }

    fn wants(&self) -> bool {
        wanted(&self.wanted.keys(), "game.")
    }

    fn note(&self, line: String) {
        if let Ok(mut notes) = self.notes.lock() {
            notes.push(line);
        }
    }

    /// Tell a problem in the log once, and again only if its cause is another (the words may
    /// differ from try to try).
    fn say(&self, problem: &str, cause: &str) {
        if let Ok(mut held) = self.problem.lock() {
            *held = Some(problem.to_string());
        }
        let cause = if cause.is_empty() { problem } else { cause };
        if let Ok(mut said) = self.said.lock() {
            if said.as_deref() != Some(cause) {
                *said = Some(cause.to_string());
                self.note(problem.to_string());
            }
        }
    }

    /// PresentMon lives: whatever was said before is over.
    fn recording(&self) {
        if let Ok(mut said) = self.said.lock() {
            if said.take().is_some() {
                self.note("PresentMon is recording".into());
            }
        }
        if let Ok(mut held) = self.problem.lock() {
            *held = None;
        }
        self.blocked.store(false, Ordering::Relaxed);
    }

    fn stopped(&self) -> bool {
        self.flags.lock().map(|flags| flags.0).unwrap_or(true)
    }

    /// Waits `seconds`, or until the source is closed: true then.
    fn wait(&self, seconds: f64) -> bool {
        let Ok(flags) = self.flags.lock() else {
            return true;
        };
        self.changed
            .wait_timeout_while(flags, Duration::from_secs_f64(seconds), |flags| !flags.0)
            .map(|(flags, _)| flags.0)
            .unwrap_or(true)
    }
}

/// Ends a PresentMon and waits for it.
fn end(process: &mut Child) {
    if matches!(process.try_wait(), Ok(None)) {
        let _ = process.kill();
        let until = Instant::now() + Duration::from_secs_f64(STOP_SECONDS);
        while matches!(process.try_wait(), Ok(None)) && Instant::now() < until {
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

fn read_frames(shared: &Shared, told: &Told, out: impl std::io::Read) {
    let mut columns: Option<Columns> = None;
    for line in BufReader::new(out).split(b'\n') {
        let Ok(line) = line else {
            break; // the pipe closed under us
        };
        if shared.stopped() || told.ended_by_us.load(Ordering::Relaxed) {
            break;
        }
        let row = csv_row(&String::from_utf8_lossy(&line));
        let Some(known) = &columns else {
            columns = Columns::of(&row);
            if columns.is_some() {
                told.header.store(true, Ordering::Relaxed);
            } else if row.iter().any(|column| column == "Application") {
                // a header, but not one with a frame time
                let shown: Vec<&String> = row.iter().take(12).collect();
                shared.say(
                    &format!("PresentMon: no frame time column in {shown:?}"),
                    "",
                );
                told.ended_by_us.store(true, Ordering::Relaxed);
                break;
            }
            continue;
        };
        if let Some((app, ms, waits)) = known.frame_of(&row) {
            if let Ok(mut counter) = shared.counter.lock() {
                counter.add(&app, ms, shared.now(), waits);
            }
        }
    }
}

fn read_errors(told: &Told, errors: impl std::io::Read) {
    for line in BufReader::new(errors).split(b'\n') {
        let Ok(line) = line else {
            break;
        };
        let line = String::from_utf8_lossy(&line);
        let line = lp_py::strip(&line);
        if !line.is_empty() {
            if let Ok(mut kept) = told.errors.lock() {
                if kept.len() == 4 {
                    kept.pop_front();
                }
                kept.push_back(line.to_string());
            }
        }
    }
}

fn begin(shared: &Arc<Shared>, program_of: &mut Option<PathBuf>) -> Option<Run> {
    let mut notes = Vec::new();
    let program = find_presentmon(&shared.path, &mut notes);
    for note in notes {
        shared.note(note);
    }
    let Some(program) = program else {
        shared.say("PresentMon not found: set path in [sensors.presentmon]", "");
        return None;
    };
    // Frames of every program: which one counts is decided here, so that a game that starts
    // later is no reason to start PresentMon again.
    let mut command = Command::new(&program);
    command
        .args(["--output_stdout", "--no_console_stats", "--session_name"])
        .args([SESSION_NAME, "--stop_existing_session"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0000_0008); // (DETACHED_PROCESS: no console host beside it)
    }
    let mut process = match command.spawn() {
        Ok(process) => process,
        Err(why) => {
            shared.say(&format!("PresentMon could not start: {why}"), "");
            return None;
        }
    };
    *program_of = Some(program);
    #[cfg(windows)]
    {
        let _ = job::add(&process); // (without the job it runs without the safeguard)
    }
    let told = Arc::new(Told::default());
    let mut readers = Vec::new();
    if let Some(out) = process.stdout.take() {
        let (shared, told) = (shared.clone(), told.clone());
        readers.extend(
            std::thread::Builder::new()
                .name("presentmon-frames".into())
                .spawn(move || read_frames(&shared, &told, out)),
        );
    }
    if let Some(errors) = process.stderr.take() {
        let told = told.clone();
        readers.extend(
            std::thread::Builder::new()
                .name("presentmon-errors".into())
                .spawn(move || read_errors(&told, errors)),
        );
    }
    Some(Run {
        process,
        started: Instant::now(),
        told,
        steady: false,
        readers,
    })
}

/// A PresentMon ended: say why, and (how long to wait, how often it failed in a row).
fn ended(shared: &Shared, run: Run, failures: u32, game_runs: bool) -> (f64, u32) {
    for reader in run.readers {
        let _ = reader.join(); // the last lines of its output
    }
    if run.told.header.load(Ordering::Relaxed) {
        // it worked: start it again soon
        shared.blocked.store(false, Ordering::Relaxed);
        let wait = if game_runs {
            BLOCKED_SECONDS
        } else {
            RESTART_SECONDS
        };
        return (wait, 0);
    }
    let lines: Vec<String> = run
        .told
        .errors
        .lock()
        .map(|kept| kept.iter().cloned().collect())
        .unwrap_or_default();
    // The cause is in the "error:" line; before it there is always the same note on rights.
    let named: Vec<&String> = lines
        .iter()
        .filter(|line| lp_py::lower(line).starts_with("error"))
        .collect();
    let error = if named.is_empty() {
        lines.join(" / ")
    } else {
        named
            .iter()
            .map(|line| line.as_str())
            .collect::<Vec<_>>()
            .join(" / ")
    };
    let blocked = lp_py::lower(&error).contains("access denied");
    shared.blocked.store(blocked, Ordering::Relaxed);
    let failures = failures + 1;
    if blocked {
        shared.say(
            "PresentMon cannot start a session while a game with an anti-cheat runs; it tries \
             again until the game is over",
            "blocked",
        );
        return (BLOCKED_SECONDS, failures);
    }
    if !run.told.ended_by_us.load(Ordering::Relaxed) {
        // (else it was said, and the cause is the header)
        let why = if error.is_empty() {
            String::new()
        } else {
            format!(": {error}")
        };
        shared.say(
            &format!(
                "PresentMon ended at once: it needs administrator rights or the \"Performance \
                 Log Users\" group{why}"
            ),
            "rights",
        );
    }
    let delay = (RETRY_SECONDS.0 * 2f64.powi(failures as i32 - 1)).min(RETRY_SECONDS.1);
    let delay = if game_runs {
        delay.max(BLOCKED_SECONDS)
    } else {
        delay
    };
    (delay, failures)
}

/// A recording session of Windows outlives its program when that is killed: stop it by name
/// (the next start would do so too). Windows only.
fn release_session(program: Option<&Path>) {
    #[cfg(windows)]
    if let Some(program) = program {
        use std::os::windows::process::CommandExt;
        let spawned = Command::new(program)
            .args([
                "--session_name",
                SESSION_NAME,
                "--terminate_existing_session",
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(0x0800_0000) // (CREATE_NO_WINDOW)
            .spawn();
        if let Ok(mut tool) = spawned {
            end_after(&mut tool, STOP_SECONDS);
        }
    }
    #[cfg(not(windows))]
    {
        let _ = program;
    }
}

/// Waits for a program `seconds`, and ends it if it has not ended by then.
#[cfg(windows)]
fn end_after(process: &mut Child, seconds: f64) {
    let until = Instant::now() + Duration::from_secs_f64(seconds);
    while matches!(process.try_wait(), Ok(None)) {
        if Instant::now() >= until {
            let _ = process.kill();
            let _ = process.wait();
            return;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

/// Keeps a PresentMon running while somebody reads `game.*`: starts it, starts it again when
/// it ended (slowly while it keeps failing, or a game runs), and ends it when nobody has read
/// for a minute.
fn supervise(shared: &Arc<Shared>) {
    let mut run: Option<Run> = None;
    let mut program: Option<PathBuf> = None;
    let mut next_start = Instant::now();
    let mut failures = 0;
    let mut idle_since: Option<Instant> = None;
    while !shared.stopped() {
        let now = Instant::now();
        if shared.wants() {
            idle_since = None;
        } else {
            match idle_since {
                None => idle_since = Some(now),
                Some(since) if now.duration_since(since).as_secs_f64() > IDLE_SECONDS => break,
                Some(_) => {}
            }
        }
        if let Some(mut live) = run.take() {
            if matches!(live.process.try_wait(), Ok(None)) {
                if live.told.ended_by_us.load(Ordering::Relaxed) {
                    end(&mut live.process); // (its header had no frame time)
                }
                if !live.steady && live.started.elapsed().as_secs_f64() >= STEADY_SECONDS {
                    live.steady = true;
                    shared.recording();
                }
                run = Some(live);
            } else {
                let game_runs = shared
                    .counter
                    .lock()
                    .is_ok_and(|counter| counter.target().is_some());
                let (delay, failed) = ended(shared, live, failures, game_runs);
                failures = failed;
                next_start = now + Duration::from_secs_f64(delay);
            }
        }
        if run.is_none() && now >= next_start && !SESSION_ENDING.load(Ordering::Relaxed) {
            run = begin(shared, &mut program);
            if run.is_none() {
                // not there, or it would not start
                next_start = now + Duration::from_secs_f64(NOT_FOUND_SECONDS);
            }
        }
        if shared.wait(SUPERVISE_SECONDS) {
            break;
        }
    }
    if let Some(mut live) = run {
        live.told.ended_by_us.store(true, Ordering::Relaxed);
        end(&mut live.process);
        for reader in live.readers {
            let _ = reader.join();
        }
    }
    if !SESSION_ENDING.load(Ordering::Relaxed) {
        // (Windows ends the recording session with itself)
        release_session(program.as_deref());
    }
    if let Ok(mut flags) = shared.flags.lock() {
        flags.1 = false;
    }
}

/// `game.*` readings from PresentMon, while a theme or a service reads them.
pub struct PresentMonSource {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl PresentMonSource {
    /// `path`: `[sensors.presentmon] path`, "" for none.
    pub fn new(path: &str, wanted: Wanted) -> PresentMonSource {
        PresentMonSource {
            shared: Arc::new(Shared {
                counter: Mutex::new(FrameCounter::new(true)),
                flags: Mutex::new((false, false)),
                changed: Condvar::new(),
                notes: Mutex::new(Vec::new()),
                said: Mutex::new(None),
                problem: Mutex::new(None),
                blocked: AtomicBool::new(false),
                wanted,
                path: path.to_string(),
                began: Instant::now(),
            }),
            thread: None,
        }
    }

    /// Which program is the game, as the `game` service finds it: only its frames count from
    /// now on. None takes it back (the program with the most frames counts).
    pub fn set_target(&self, exe: Option<&str>) {
        if let Ok(mut counter) = self.shared.counter.lock() {
            counter.set_target(exe);
        }
    }

    /// What the frames say now; PresentMon is started where a theme reads `game.*` and it is
    /// not looked after.
    pub fn read(&mut self) -> Readings {
        let (stopped, runs) = self
            .shared
            .flags
            .lock()
            .map(|f| *f)
            .unwrap_or((true, false));
        if stopped || !self.shared.wants() {
            return Readings::new();
        }
        if !runs {
            if let Some(ended) = self.thread.take() {
                let _ = ended.join();
            }
            if let Ok(mut flags) = self.shared.flags.lock() {
                flags.1 = true;
            }
            let shared = self.shared.clone();
            self.thread = std::thread::Builder::new()
                .name("sensors-presentmon".into())
                .spawn(move || supervise(&shared))
                .ok();
        }
        let now = self.shared.now();
        self.shared
            .counter
            .lock()
            .map(|mut counter| counter.readings(now))
            .unwrap_or_default()
    }

    /// Why there is no data, for a status display; and whether the anti-cheat of a game
    /// refuses the session.
    pub fn problem(&self) -> Option<String> {
        self.shared
            .problem
            .lock()
            .ok()
            .and_then(|held| held.clone())
    }

    pub fn blocked(&self) -> bool {
        self.shared.blocked.load(Ordering::Relaxed)
    }

    /// What the source noted for the log since it was last asked.
    pub fn notes(&self) -> Vec<String> {
        self.shared
            .notes
            .lock()
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

impl Drop for PresentMonSource {
    fn drop(&mut self) {
        self.close();
    }
}
