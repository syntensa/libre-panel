//! Light mode (light.py): what only the app's pages need is let go of while none of them is
//! open, and the memory goes back to the system.
//!
//! The app's pictures of every theme (Start, Design) are drawn in this process, by the threads
//! that answer its pages: a hundred megabytes of pictures made and let go again, which the C
//! heap keeps for the program (glibc in a heap of each thread's own, Windows' heap as the
//! pages between the blocks that stay). 0.8 gave them back; 0.9 did not until now: Lumen on a
//! 12.3", 94 MB as it starts, stood at 200 MB once its window had been open, for as long as
//! the app ran.
//!
//! - Once no page of the app has asked anything for [`IDLE_S`] seconds (its window closed, or
//!   never opened since the app started, as at login), a look every [`LOOK_S`] seconds
//!   ([`Watch`]) lets go of the app's own pictures, and what every renderer shares (the fans'
//!   rotors, the weather's symbols, the moon, the agents' signs: lp_render's `shared`) notes
//!   from then on what the panel draws with. A look later the shared caches let go of the
//!   rest (the pictures of every theme filled them with what those themes draw with) and the
//!   memory goes back to the system ([`give_back`]). What a panel has drawn with before
//!   stays whether or not it asked for it in that while (a fan that stands still asks for
//!   nothing: lp_render's `shared::for_a_panel`), and the moon's face goes with the rest
//!   where no panel shows a moon.
//! - Each look after that, while the app stays closed, gives back what the panel's frames
//!   freed meanwhile ([`tidy`]). Once, [`SAID_S`] after the memory went back, the log says
//!   what the process holds then (what stays with the app closed, to read in Status →
//!   Diagnose → Log once the window is open again).
//!
//! The panel draws on as it did: what it draws with stays, nothing of it is made again, and
//! the app draws its pictures again when a page asks for them.

use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

/// No page of the app asked anything for this long: it is closed.
pub const IDLE_S: f64 = 60.0;
/// How often the watch looks.
pub const LOOK_S: f64 = 15.0;
/// This long after its memory went back, the log says what the process holds.
pub const SAID_S: f64 = 180.0;

fn say(message: &str) {
    crate::logs::say("libre_panel.light", message);
}

/// tidy: the C heap's free pages back to the system. A frame's work lets pictures and buffers
/// go between those that stay, and the heap keeps their pages for the program. Linux with
/// glibc: its trim (every heap's free pages, the ones in the middle too). Windows: its heaps'
/// free memory ([`windows::heaps`]). Elsewhere nothing.
pub fn tidy() {
    // (the large blocks kept for a next frame that did not come, a panel paused or gone:
    // the keeper's clock moves with the frames otherwise)
    crate::heap::tick();
    #[cfg(all(target_os = "linux", target_env = "gnu"))]
    // SAFETY: malloc_trim takes the allocator's locks itself; any thread may call it.
    unsafe {
        libc::malloc_trim(0);
    }
    #[cfg(windows)]
    windows::heaps();
}

/// give_back: the memory no longer used back to the system: the C heap's free pages, and on
/// Windows then the pages of the program that are not touched (its working set: the "Memory"
/// the Task Manager shows), which come back as the panel touches them.
pub fn give_back() {
    tidy();
    #[cfg(windows)]
    windows::empty_working_set();
}

#[cfg(windows)]
mod windows {
    use windows_sys::Win32::System::Memory::{
        GetProcessHeap, HeapCompact, HeapOptimizeResources, HeapSetInformation,
    };
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, SetProcessWorkingSetSize};

    /// HEAP_OPTIMIZE_RESOURCES_INFORMATION (Windows 8.1 and later): version 1, no flags.
    #[repr(C)]
    struct HeapOptimize {
        version: u32,
        flags: u32,
    }

    /// windows_heaps: the free memory of the process's heaps back to the system. First what
    /// the heaps' low-fragmentation caches hold and what can be decommitted
    /// (HeapOptimizeResources, for all of them), then the process heap's free blocks merged
    /// and the large ones decommitted (HeapCompact). The process heap is the one Rust's
    /// allocator takes its blocks from: the pictures made and let go between those that stay
    /// left its pages committed to the program. Safe beside the other threads: both take the
    /// heaps' locks.
    pub fn heaps() {
        let info = HeapOptimize {
            version: 1,
            flags: 0,
        };
        // SAFETY: the information is of the size given and lives through the call; a null
        // heap means all of the process's heaps for this class; the process heap is valid
        // for as long as the process runs.
        unsafe {
            HeapSetInformation(
                std::ptr::null_mut(),
                HeapOptimizeResources,
                (&raw const info).cast(),
                size_of::<HeapOptimize>(),
            );
            HeapCompact(GetProcessHeap(), 0);
        }
    }

    /// empty_working_set: the pages of the program leave its working set; those it touches
    /// come back as it touches them.
    pub fn empty_working_set() {
        // SAFETY: the pseudo handle of this process; both sizes -1: as many pages as can go.
        unsafe {
            SetProcessWorkingSetSize(GetCurrentProcess(), usize::MAX, usize::MAX);
        }
    }
}

/// The watch's times: [`IDLE_S`] and [`LOOK_S`], or what `LP_LIGHT_S` gives ("idle,look", in
/// seconds): for measuring what light mode costs the panel's frames in a run of half a
/// minute (tests/frame_load.py, CI's Windows job).
fn times() -> (f64, f64) {
    let given = std::env::var("LP_LIGHT_S").unwrap_or_default();
    let mut parts = given.split(',').map(|part| part.trim().parse::<f64>().ok());
    let valid = |seconds: Option<Option<f64>>, usual: f64| {
        seconds
            .flatten()
            .filter(|seconds| seconds.is_finite() && *seconds >= 0.1)
            .unwrap_or(usual)
    };
    let idle = valid(parts.next(), IDLE_S);
    (idle, valid(parts.next(), LOOK_S))
}

/// What a look does, by the times it is given (light.py's Watch.look).
#[derive(Debug)]
pub struct Looks {
    /// how long no page of the app must have asked anything for it to be closed
    idle: f64,
    /// the request the app's pictures went after, and the shared caches began to note
    closed_for: Option<Option<u64>>,
    /// the request the caches let go and the memory was given back after
    released_for: Option<Option<u64>>,
    /// the caches note what the panel draws with (between Closed and Release)
    marking: bool,
    /// when the memory was last given back
    released_at: f64,
    /// the request after which the log said what the process holds
    said_for: Option<Option<u64>>,
}

/// What a look found to do.
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Look {
    /// a page of the app asked something within [`IDLE_S`]: nothing; `unmark`: it was opened
    /// again before the caches let go, they keep all
    Open { unmark: bool },
    /// closed for [`IDLE_S`], for the first time since it was last open: the app's own
    /// pictures go, and the shared caches note from now on what the panel draws with
    Closed,
    /// a look later: the caches let go of the rest, and the memory goes back
    Release,
    /// still closed: what the panel's frames freed meanwhile goes back; `say`: and the log
    /// says what the process holds (once, [`SAID_S`] after the release)
    Tidy { say: bool },
}

impl Default for Looks {
    fn default() -> Looks {
        Looks::after(IDLE_S)
    }
}

impl Looks {
    /// The looks of an app that is closed once nothing was asked for `idle` seconds.
    pub fn after(idle: f64) -> Looks {
        Looks {
            idle,
            closed_for: None,
            released_for: None,
            marking: false,
            released_at: 0.0,
            said_for: None,
        }
    }

    /// One look at `now`: `last` is when a page of the app last asked something (None: never
    /// since `began`, when the watch started: an app started at login is closed since then).
    /// All in seconds on one clock.
    pub fn look(&mut self, now: f64, last: Option<f64>, began: f64) -> Look {
        if now - last.unwrap_or(began) < self.idle {
            return Look::Open {
                unmark: std::mem::take(&mut self.marking),
            };
        }
        let asked = Some(last.map(f64::to_bits));
        if self.closed_for != asked {
            self.closed_for = asked;
            self.marking = true;
            return Look::Closed;
        }
        if self.released_for == asked {
            let say = self.said_for != asked && now - self.released_at >= SAID_S;
            if say {
                self.said_for = asked;
            }
            return Look::Tidy { say };
        }
        self.released_for = asked;
        self.released_at = now;
        self.marking = false;
        Look::Release
    }
}

/// The thread that looks every [`LOOK_S`] seconds until it is stopped.
pub struct Watch {
    stop: Arc<(Mutex<bool>, Condvar)>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl Watch {
    /// Starts the watch: `clock` gives now and when a page last asked something (the server's
    /// clock: seconds since it began), `closed` lets go of the app's own pictures.
    pub fn start(
        clock: impl Fn() -> (f64, Option<f64>) + Send + 'static,
        closed: impl Fn() + Send + 'static,
    ) -> Watch {
        let stop = Arc::new((Mutex::new(false), Condvar::new()));
        let stopped = Arc::clone(&stop);
        let began = clock().0;
        let (idle, every) = times();
        let thread = std::thread::Builder::new().name("light-mode".into());
        let thread = thread.spawn(move || {
            let mut looks = Looks::after(idle);
            loop {
                let (lock, wake) = &*stopped;
                let asked = lock.lock().unwrap_or_else(|e| e.into_inner());
                let wait = Duration::from_secs_f64(every);
                let (asked, _) = wake
                    .wait_timeout_while(asked, wait, |stop| !*stop)
                    .unwrap_or_else(|e| e.into_inner());
                if *asked {
                    return;
                }
                drop(asked);
                let (now, last) = clock();
                match looks.look(now, last, began) {
                    Look::Open { unmark: false } => {}
                    Look::Open { unmark: true } => lp_render::shared::unmark(),
                    Look::Closed => {
                        closed();
                        lp_render::shared::mark();
                    }
                    Look::Release => {
                        lp_render::shared::shed();
                        give_back();
                        say("light mode: the app is closed, its memory went back to the system");
                    }
                    Look::Tidy { say: held } => {
                        tidy();
                        if held {
                            say(&format!(
                                "light mode: {:.0} minutes after the app closed, {}",
                                SAID_S / 60.0,
                                crate::load::held_line()
                            ));
                        }
                    }
                }
            }
        });
        Watch {
            stop,
            thread: thread.ok(),
        }
    }

    pub fn stop(&mut self) {
        let (lock, wake) = &*self.stop;
        *lock.lock().unwrap_or_else(|e| e.into_inner()) = true;
        wake.notify_all();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for Watch {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TIDY: Look = Look::Tidy { say: false };
    const SAID: Look = Look::Tidy { say: true };

    #[test]
    fn the_memory_goes_a_minute_after_the_app_was_closed_and_once() {
        const OPEN: Look = Look::Open { unmark: false };
        let mut looks = Looks::default();
        // a page asked something at 100 s
        assert_eq!(looks.look(110.0, Some(100.0), 0.0), OPEN);
        assert_eq!(looks.look(159.9, Some(100.0), 0.0), OPEN);
        // its pictures go, and the caches note what the panel draws with
        assert_eq!(looks.look(160.0, Some(100.0), 0.0), Look::Closed);
        // a look later they let go of the rest and the memory goes back
        assert_eq!(looks.look(175.0, Some(100.0), 0.0), Look::Release);
        // while it stays closed, each look gives back what the frames freed
        assert_eq!(looks.look(190.0, Some(100.0), 0.0), TIDY);
        // three minutes after the release the log says what the process holds, once
        assert_eq!(looks.look(175.0 + SAID_S - 1.0, Some(100.0), 0.0), TIDY);
        assert_eq!(looks.look(175.0 + SAID_S, Some(100.0), 0.0), SAID);
        assert_eq!(looks.look(175.0 + SAID_S + 15.0, Some(100.0), 0.0), TIDY);
        assert_eq!(looks.look(9000.0, Some(100.0), 0.0), TIDY);
        // opened again, and closed again: once more
        assert_eq!(looks.look(9010.0, Some(9005.0), 0.0), OPEN);
        assert_eq!(looks.look(9070.0, Some(9005.0), 0.0), Look::Closed);
        assert_eq!(looks.look(9085.0, Some(9005.0), 0.0), Look::Release);
        assert_eq!(looks.look(9100.0, Some(9005.0), 0.0), TIDY);
        assert_eq!(looks.look(9085.0 + SAID_S, Some(9005.0), 0.0), SAID);
        assert_eq!(looks.look(9100.0 + SAID_S, Some(9005.0), 0.0), TIDY);
    }

    #[test]
    fn the_caches_keep_all_when_the_app_is_opened_again_before_they_let_go() {
        let mut looks = Looks::default();
        assert_eq!(looks.look(160.0, Some(100.0), 0.0), Look::Closed);
        // a page asks at 170 s, before the next look
        assert_eq!(
            looks.look(175.0, Some(170.0), 0.0),
            Look::Open { unmark: true }
        );
        assert_eq!(
            looks.look(190.0, Some(170.0), 0.0),
            Look::Open { unmark: false }
        );
        // closed again: from the start
        assert_eq!(looks.look(230.0, Some(170.0), 0.0), Look::Closed);
        assert_eq!(looks.look(245.0, Some(170.0), 0.0), Look::Release);
    }

    #[test]
    fn an_app_never_opened_lets_go_a_minute_after_the_start_and_once() {
        let mut looks = Looks::default();
        assert_eq!(looks.look(45.0, None, 0.0), Look::Open { unmark: false });
        assert_eq!(looks.look(60.0, None, 0.0), Look::Closed);
        assert_eq!(looks.look(75.0, None, 0.0), Look::Release);
        assert_eq!(looks.look(90.0, None, 0.0), TIDY);
        // its first page, a while later
        assert_eq!(
            looks.look(500.0, Some(499.0), 0.0),
            Look::Open { unmark: false }
        );
        assert_eq!(looks.look(560.0, Some(499.0), 0.0), Look::Closed);
        assert_eq!(looks.look(575.0, Some(499.0), 0.0), Look::Release);
    }

    #[test]
    fn a_watch_started_later_counts_from_its_own_start() {
        let mut looks = Looks::default();
        assert_eq!(
            looks.look(1030.0, None, 1000.0),
            Look::Open { unmark: false }
        );
        assert_eq!(looks.look(1060.0, None, 1000.0), Look::Closed);
    }

    #[test]
    fn giving_back_and_tidying_leave_the_program_as_it_was() {
        // (what they do is the system's; here: that they can be called, any number of times)
        let kept: Vec<Vec<u8>> = (0..64).map(|n| vec![n as u8; 300_000]).collect();
        tidy();
        give_back();
        tidy();
        assert!(kept
            .iter()
            .enumerate()
            .all(|(n, block)| block.iter().all(|&b| b == n as u8)));
    }

    #[test]
    fn the_watch_looks_and_stops() {
        let began = std::time::Instant::now();
        let mut watch = Watch::start(|| (0.0, None), || {});
        watch.stop(); // (at once, not after a look's wait)
        assert!(began.elapsed().as_secs_f64() < LOOK_S / 2.0);
    }
}
