//! Where a frame's time goes, measured while it runs (LP_PROFILE=1): the time of each named
//! part summed per thread and said every ten seconds. Off, a part costs a check of a flag.

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

fn on() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("LP_PROFILE").is_some_and(|v| !v.is_empty()))
}

/// Since when a thread sums, and per part its time and how often it ran.
type Sums = (Option<Instant>, BTreeMap<&'static str, (Duration, u64)>);

thread_local! {
    static SUMS: RefCell<Sums> = const { RefCell::new((None, BTreeMap::new())) };
}

/// `f`, its time added to the part `name` where profiling is on.
pub fn part<T>(name: &'static str, f: impl FnOnce() -> T) -> T {
    if !on() {
        return f();
    }
    let began = Instant::now();
    let out = f();
    add(name, began.elapsed());
    out
}

/// `took` added to the part `name`; every ten seconds the sums are said and begin again.
pub fn add(name: &'static str, took: Duration) {
    if !on() {
        return;
    }
    SUMS.with(|sums| {
        let mut sums = sums.borrow_mut();
        let since = *sums.0.get_or_insert_with(Instant::now);
        let entry = sums.1.entry(name).or_default();
        entry.0 += took;
        entry.1 += 1;
        let span = since.elapsed();
        if span >= Duration::from_secs(10) {
            let thread = std::thread::current();
            let mut line = format!(
                "profile {} ({:.1} s):",
                thread.name().unwrap_or("?"),
                span.as_secs_f64()
            );
            for (name, (sum, n)) in &sums.1 {
                let share = 100.0 * sum.as_secs_f64() / span.as_secs_f64();
                line += &format!(
                    " {name} {share:.1}% ({n}x {:.2} ms)",
                    sum.as_secs_f64() * 1000.0 / *n as f64
                );
            }
            eprintln!("{line}");
            sums.1.clear();
            sums.0 = Some(Instant::now());
        }
    });
}

/// A part timed from now until it is dropped.
pub struct Span(Option<(&'static str, Instant)>);

impl Span {
    pub fn new(name: &'static str) -> Span {
        Span(on().then(|| (name, Instant::now())))
    }
}

impl Drop for Span {
    fn drop(&mut self) {
        if let Some((name, began)) = self.0.take() {
            add(name, began.elapsed());
        }
    }
}

/// A count (pixels, bytes) said with the times: as microseconds of a part named so, `n` of
/// them (its "ms" a thousandth of the count's mean).
pub fn add_count(name: &'static str, n: u64) {
    add(name, Duration::from_micros(0));
    if on() {
        SUMS.with(|sums| {
            if let Some(entry) = sums.borrow_mut().1.get_mut(name) {
                entry.0 += Duration::from_micros(n);
            }
        });
    }
}
