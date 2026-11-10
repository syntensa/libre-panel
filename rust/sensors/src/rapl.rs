//! Power from energy counters: the processor's RAPL zones, a graphics card's energy file
//! (sensors/rapl.py).
//!
//! The kernel counts the energy a processor package has used, in microjoules, in
//! /sys/class/powercap/intel-rapl:0/energy_uj (Intel since Sandy Bridge, AMD from Zen, through
//! the same driver). The power in watts is the difference between two readings over the time
//! between them. Since 2020 (the PLATYPUS side channel) the kernel lets only root read those
//! files; `libre-panel system-setup --linux` can open them, if the person says so. Without
//! it the processor's power is simply not there.
//!
//! A counter counts up to a range, then starts again from zero: the range is in
//! `max_energy_range_uj` next to it.

use std::path::{Path, PathBuf};

use crate::sysfs::{names, read_number, read_text};

pub const POWERCAP: &str = "sys/class/powercap";
/// readings closer than this give the last power again: a counter ticks
pub const MIN_SECONDS: f64 = 0.25;

/// An energy counter: where it is, and where it starts again (0: not told).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Counter {
    pub path: PathBuf,
    pub wrap: i128,
}

/// `^intel-rapl(?:-mmio)?:\d+$`: a package, not its parts (intel-rapl:0:0).
fn is_zone(name: &str) -> bool {
    let name = name.strip_suffix('\n').unwrap_or(name);
    let Some(rest) = name.strip_prefix("intel-rapl") else {
        return false;
    };
    let rest = rest.strip_prefix("-mmio").unwrap_or(rest);
    rest.strip_prefix(':').is_some_and(|digits| {
        !digits.is_empty() && digits.chars().all(|ch| lp_py::decimal(ch).is_some())
    })
}

/// The counters of the processor packages (a PC with two sockets has two). The zones of the
/// MSR interface come first; the memory-mapped ones (`intel-rapl-mmio`) report the same
/// packages and count only where there are no others.
pub fn package_counters(root: &Path) -> Vec<Counter> {
    let folder = root.join(POWERCAP);
    let (mut msr, mut mapped) = (Vec::new(), Vec::new());
    for name in names(&folder).unwrap_or_default() {
        if !is_zone(&name) {
            continue;
        }
        let zone = folder.join(&name);
        let package = read_text(&zone.join("name")).unwrap_or_default();
        if !package.starts_with("package") || !zone.join("energy_uj").exists() {
            continue;
        }
        let counter = Counter {
            path: zone.join("energy_uj"),
            wrap: read_number(&zone.join("max_energy_range_uj"), 10).unwrap_or(0),
        };
        match name.contains("mmio") {
            true => mapped.push(counter),
            false => msr.push(counter),
        }
    }
    if msr.is_empty() {
        mapped
    } else {
        msr
    }
}

/// Whether the counters are there but this user may not read them (the kernel's way of
/// keeping the side channel shut).
pub fn unreadable(counters: &[Counter]) -> bool {
    !counters.is_empty()
        && !counters
            .iter()
            .any(|counter| std::fs::File::open(&counter.path).is_ok())
}

/// Watts from counters of microjoules, asked now and then. The first call has nothing to
/// compare with and gives None; a counter that cannot be read leaves the sum out (None).
#[derive(Clone, Debug)]
pub struct Joules {
    pub counters: Vec<Counter>,
    last: Option<(f64, Vec<i128>)>,
    watts: Option<f64>,
}

impl Joules {
    pub fn new(counters: Vec<Counter>) -> Joules {
        Joules {
            counters,
            last: None,
            watts: None,
        }
    }

    /// The power at `now` (seconds on a clock that only goes forward).
    pub fn watts(&mut self, now: f64) -> Option<f64> {
        let energies: Option<Vec<i128>> = self
            .counters
            .iter()
            .map(|counter| read_number(&counter.path, 10))
            .collect();
        let known = match energies {
            Some(known) if !self.counters.is_empty() => known,
            _ => {
                // a counter that is gone gives no half sum
                (self.last, self.watts) = (None, None);
                return None;
            }
        };
        let Some((then, before)) = self.last.take() else {
            self.last = Some((now, known));
            return None;
        };
        if now - then < MIN_SECONDS {
            self.last = Some((then, before));
            return self.watts;
        }
        let mut joules = 0.0;
        for ((counter, old), new) in self.counters.iter().zip(&before).zip(&known) {
            let mut step = new - old;
            if step < 0 {
                step += counter.wrap; // the counter started again
            }
            if step < 0 {
                (self.last, self.watts) = (Some((now, known)), None);
                return None;
            }
            joules += step as f64 / 1e6;
        }
        self.last = Some((now, known));
        self.watts = Some(joules / (now - then));
        self.watts
    }
}
