//! The panels beside [device]'s (app.py's `_MorePanels` and `run_beside`): each of
//! `[[panels]]` that is on in a loop of its own, in a thread of its own (`panel-2`, ...), on
//! the first panel's readings. Each follows config.toml as the first panel's loop does; one
//! starts as it is added or switched on, ends as it is taken out or off, and all end with the
//! first panel's loop. A loop that cannot start (its config or theme) is tried again after
//! `RETRY_S`, one that broke after `BUG_S`; the first panel goes on through either.

use std::collections::BTreeSet;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_config::Config;

use crate::company::Company;
use crate::home::Home;
use crate::mainloop::{Asked, Beside, MainLoop, RunStatus};
use crate::sensors::Hub;

/// after a config or theme that cannot be shown
const RETRY_S: f64 = 30.0;
/// after a bug in a panel's loop
const BUG_S: f64 = 5.0;
/// how long an end waits for a loop to let its panel go
const HALT_S: f64 = 10.0;

fn say(message: &str) {
    crate::logs::say("libre_panel.app", message);
}

/// A panel's loop: its thread and what stops it.
struct Run {
    thread: JoinHandle<()>,
    stop: Arc<AtomicBool>,
}

/// What every panel's loop is started with.
#[derive(Clone)]
struct Start {
    home: Home,
    asked: Asked,
    company: Arc<Company>,
    hub: Arc<Mutex<Hub>>,
    /// the first panel's status: the theme it shows (for a companion), and where the others'
    /// are told
    first: Arc<Mutex<RunStatus>>,
    /// whether Libre Panel quits as the loops end (their panels may go dark)
    quitting: Arc<AtomicBool>,
}

pub struct More {
    start: Start,
    runs: IndexMap<usize, Run>,
    statuses: IndexMap<usize, Arc<Mutex<RunStatus>>>,
}

impl More {
    pub fn new(
        home: Home,
        asked: Asked,
        company: Arc<Company>,
        hub: Arc<Mutex<Hub>>,
        first: Arc<Mutex<RunStatus>>,
    ) -> More {
        More {
            start: Start {
                home,
                asked,
                company,
                hub,
                first,
                quitting: Arc::default(),
            },
            runs: IndexMap::new(),
            statuses: IndexMap::new(),
        }
    }

    /// The statuses of the loops that run.
    pub fn statuses(&self) -> Vec<Arc<Mutex<RunStatus>>> {
        self.statuses.values().cloned().collect()
    }

    /// Starts and ends loops as `config` (the first panel's) says. Cheap while it is the same:
    /// the first panel's loop calls it on every frame.
    pub fn follow(&mut self, config: &Config) {
        let wanted: BTreeSet<usize> = (config.panels.iter().enumerate())
            .filter(|(_, panel)| panel.on)
            .map(|(n, _)| n)
            .collect();
        let gone: Vec<usize> = (self.runs.keys().copied())
            .filter(|n| !wanted.contains(n))
            .collect();
        let new: Vec<usize> = (wanted.into_iter())
            .filter(|n| !self.runs.contains_key(n))
            .collect();
        if gone.is_empty() && new.is_empty() {
            return;
        }
        self.end(&gone);
        for n in new {
            self.begin(n);
        }
        self.tell();
    }

    fn begin(&mut self, n: usize) {
        let status: Arc<Mutex<RunStatus>> = Arc::default();
        let stop: Arc<AtomicBool> = Arc::default();
        let (start, told, stopped) = (self.start.clone(), Arc::clone(&status), Arc::clone(&stop));
        let thread = std::thread::Builder::new()
            .name(format!("panel-{}", n + 2))
            .spawn(move || run_beside(n, &start, &told, &stopped));
        match thread {
            Ok(thread) => {
                self.runs.insert(n, Run { thread, stop });
                self.statuses.insert(n, status);
            }
            Err(why) => say(&format!("panel {}: its loop did not start: {why}", n + 2)),
        }
    }

    /// The loops of the panels `numbers` ended: all at once, each lets its panel go while the
    /// others do.
    fn end(&mut self, numbers: &[usize]) {
        let runs: Vec<(usize, Run)> = (numbers.iter())
            .filter_map(|n| self.runs.shift_remove(n).map(|run| (*n, run)))
            .collect();
        for (_, run) in &runs {
            run.stop.store(true, Ordering::Relaxed);
        }
        let until = Instant::now() + Duration::from_secs_f64(HALT_S);
        for (n, run) in runs {
            while !run.thread.is_finished() && Instant::now() < until {
                std::thread::sleep(Duration::from_millis(10));
            }
            match run.thread.is_finished() {
                true => drop(run.thread.join()),
                false => say(&format!("the loop of panel {} did not stop in time", n + 2)),
            }
            self.statuses.shift_remove(&n);
        }
    }

    /// The first panel's status told which loops run beside it.
    fn tell(&self) {
        if let Ok(mut first) = self.start.first.lock() {
            first.more.clone_from(&self.statuses);
        }
    }

    /// Every loop ended, with the first panel's: `quitting` whether Libre Panel ends.
    pub fn close(&mut self, quitting: bool) {
        self.start.quitting.store(quitting, Ordering::Relaxed);
        let all: Vec<usize> = self.runs.keys().copied().collect();
        self.end(&all);
        self.tell();
    }
}

/// Waits `seconds`, or until `stop`.
fn rest(stop: &AtomicBool, seconds: f64) {
    let until = Instant::now() + Duration::from_secs_f64(seconds);
    while !stop.load(Ordering::Relaxed) && Instant::now() < until {
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn stopped(status: &Mutex<RunStatus>, detail: String) {
    if let Ok(mut status) = status.lock() {
        status.state = "error";
        status.detail = detail;
    }
}

/// run_beside: the loop of the panel at `[[panels]]` `n` until `stop`, again after a config
/// or theme it cannot show and after a bug in it, with config.toml read anew.
fn run_beside(n: usize, start: &Start, status: &Arc<Mutex<RunStatus>>, stop: &AtomicBool) {
    while !stop.load(Ordering::Relaxed) {
        let beside = Beside {
            n,
            first: Arc::clone(&start.first),
        };
        let made = MainLoop::beside(
            start.home.clone(),
            start.asked.clone(),
            Arc::clone(&start.company),
            Arc::clone(&start.hub),
            beside,
            Arc::clone(status),
        );
        match made {
            Ok(mut made) => {
                let quitting = || start.quitting.load(Ordering::Relaxed);
                let ran = catch_unwind(AssertUnwindSafe(|| made.run(stop, &quitting)));
                if let Err(bug) = ran {
                    let why = (bug.downcast_ref::<String>().cloned())
                        .or_else(|| bug.downcast_ref::<&str>().map(|why| why.to_string()))
                        .unwrap_or_default();
                    stopped(status, format!("unexpected error: {why}"));
                    say(&format!(
                        "the loop of panel {} crashed; starting again",
                        n + 2
                    ));
                    rest(stop, BUG_S);
                }
            }
            Err(why) => {
                say(&format!("panel {} stopped: {why}", n + 2));
                stopped(status, why);
                rest(stop, RETRY_S);
            }
        }
    }
}
