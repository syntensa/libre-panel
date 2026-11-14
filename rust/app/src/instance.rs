//! Only one Libre Panel may drive the panel at a time (instance.py): the start at login, a
//! second click on the program and `libre-panel run` in a terminal would otherwise fight over
//! the USB device. The lock is the system's file lock, so it goes with the process, even after
//! a crash.

use std::fs::{File, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const LOCK_FILENAME: &str = "libre-panel.lock";
/// how long a second start waits for the first to serve its app
pub const STARTING_S: f64 = 30.0;

/// Why the lock was not had: another Libre Panel holds it (AlreadyRunning), or the settings
/// folder cannot be written.
#[derive(Debug)]
pub enum NotLocked {
    Running(String),
    Failed(String),
}

impl NotLocked {
    pub fn message(self) -> String {
        match self {
            NotLocked::Running(why) | NotLocked::Failed(why) => why,
        }
    }
}

/// The lock, held until it is dropped.
pub struct InstanceLock {
    _file: File,
}

fn already_running() -> String {
    lp_i18n::t(
        "Libre Panel is already running and drives the panel (look for its tray icon, or stop \
         `libre-panel run`/`start` in the other terminal)",
    )
}

impl InstanceLock {
    /// acquire: the lock in the settings folder `settings`, with this process's number in
    /// it. Err: why not (another Libre Panel holds it, or the folder cannot be written).
    pub fn acquire(settings: &Path) -> Result<InstanceLock, NotLocked> {
        InstanceLock::acquire_named(settings, LOCK_FILENAME)
    }

    /// As `acquire`, the lock of the file `name` in the settings folder (the window's own).
    pub fn acquire_named(settings: &Path, name: &str) -> Result<InstanceLock, NotLocked> {
        let path: PathBuf = settings.join(name);
        let failed = |why: std::io::Error| NotLocked::Failed(why.to_string());
        std::fs::create_dir_all(settings).map_err(failed)?;
        let mut file = (OpenOptions::new().read(true).append(true).create(true))
            .open(&path)
            .map_err(failed)?;
        if !try_lock(&file) {
            return Err(NotLocked::Running(already_running()));
        }
        let _ = file.set_len(0);
        let _ = file.seek(SeekFrom::Start(0));
        let _ = write!(file, "{}", std::process::id());
        let _ = file.flush();
        Ok(InstanceLock { _file: file })
    }

    /// _instance_running: whether another Libre Panel holds the lock now.
    pub fn held_elsewhere(settings: &Path) -> bool {
        matches!(InstanceLock::acquire(settings), Err(NotLocked::Running(_)))
    }
}

/// running_instance's `editor`: the address of the running app's server, waiting up to
/// `seconds` for one that is starting (it writes its address down once its server runs).
pub fn editor_of_running_app(settings: &Path, seconds: f64) -> Option<String> {
    let until = Instant::now() + Duration::from_secs_f64(seconds);
    loop {
        let text = std::fs::read_to_string(settings.join("instance.json")).ok();
        let found = text
            .and_then(|text| lp_py::json::loads(&text).ok())
            .and_then(|told| match told {
                lp_py::Value::Dict(told) => match told.get("editor") {
                    Some(lp_py::Value::Str(url)) if !url.is_empty() => Some(url.clone()),
                    _ => None,
                },
                _ => None,
            });
        if found.is_some() || Instant::now() >= until {
            return found;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

/// The file locked for this process alone, without waiting (fcntl.flock, LOCK_EX | LOCK_NB).
#[cfg(unix)]
fn try_lock(file: &File) -> bool {
    use std::os::fd::AsRawFd;
    // SAFETY: the descriptor is the open file's, for as long as the call
    unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) == 0 }
}

/// The file's first byte locked for this process alone, without waiting (msvcrt.locking's
/// LK_NBLCK on one byte, as the Python app locks it).
#[cfg(windows)]
fn try_lock(file: &File) -> bool {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        LockFileEx, LOCKFILE_EXCLUSIVE_LOCK, LOCKFILE_FAIL_IMMEDIATELY,
    };
    use windows_sys::Win32::System::IO::OVERLAPPED;
    let flags = LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY;
    // SAFETY: the handle is the open file's; the OVERLAPPED says the range starts at 0
    unsafe {
        let mut at: OVERLAPPED = std::mem::zeroed();
        LockFileEx(file.as_raw_handle() as _, flags, 0, 1, 0, &mut at) != 0
    }
}

#[cfg(not(any(unix, windows)))]
fn try_lock(_file: &File) -> bool {
    true
}
