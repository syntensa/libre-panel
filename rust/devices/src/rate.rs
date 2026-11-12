//! The bit rate of a panel's video (devices/h264.py's RateControl, auto_rate, rate_text and
//! mbit) and what a run keeps of it for the next (devices/video_state.py's rates).

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use lp_py::Value;

/// The bit rate "auto" starts from: so many Mbit/s for so many pixels at so many frames a
/// second (REFERENCE_RATE).
pub const REFERENCE_RATE: (f64, f64, f64) = (2.0, 1920.0 * 480.0, 50.0);
/// The steps "auto" moves along, in Mbit/s (RATE_STEPS).
pub const RATE_STEPS: [f64; 11] = [1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0];
pub const STATE_FILENAME: &str = "video-state.json";

/// mbit: a bit rate as config.toml gives it ("2M", "1500k", "2000000") in Mbit/s; 0 for
/// "auto".
pub fn mbit(maxrate: &str) -> f64 {
    let text = lp_py::strip(maxrate);
    if text.is_empty() || text == "auto" {
        return 0.0;
    }
    let (number, scale) = match text.chars().last() {
        Some('k' | 'K') => (&text[..text.len() - 1], 1e-3),
        Some('M') => (&text[..text.len() - 1], 1.0),
        _ => (text, 1e-6),
    };
    match lp_py::float_of_str(number) {
        Some(value) => lp_py::round_digits(value * scale, 3),
        None => 0.0,
    }
}

/// auto_rate: the bit rate "auto" starts with for pictures of `size`, in Mbit/s: the
/// reference rate scaled by the pixels and the frame rate, and never less than it.
pub fn auto_rate(fps: i64, size: (i64, i64)) -> f64 {
    let (rate, pixels, at) = REFERENCE_RATE;
    let scaled = rate * ((size.0 * size.1) as f64 / pixels) * (fps as f64 / at);
    rate.max(lp_py::round_digits(scaled, 1))
}

/// rate_text: a bit rate as it is written: 3.0 -> "3.0M".
pub fn rate_text(mbit: f64) -> String {
    format!("{mbit:.1}M")
}

/// The bit rate of `maxrate` "auto" while the panel runs (RateControl). It starts at the
/// rate "auto" starts with and moves along the steps from there up; a step down when the
/// panel takes less than it is sent, seen within a window either as the sender waiting for
/// room in the panel's queue or as half the blocks taking longer than a frame; a step up
/// after a calm minute, as long as the step above did not fail lately: a failed step is held
/// off, twice as long each time it fails again. After a step it settles before the next
/// window begins.
#[derive(Clone, Debug)]
pub struct RateControl {
    steps: Vec<f64>,
    pub rate: f64,
    pub changes: u64,
    /// trouble at the start rate: the frames, not the bits
    pub floored: bool,
    /// the counts when the window began
    base: Option<(u64, u64, u64)>,
    window: f64,
    calm: f64,
    settled: f64,
    /// a step, until when it is not tried, and how often it failed
    held: Vec<(f64, f64, u32)>,
}

impl RateControl {
    pub const WINDOW_S: f64 = 10.0;
    pub const TROUBLE_WAITS: u64 = 5;
    pub const TROUBLE_SLOW: u64 = 25;
    pub const CALM_S: f64 = 60.0;
    pub const HOLD_S: f64 = 600.0;
    pub const SETTLE_S: f64 = 3.0;

    /// From `start`; `remembered`: the rate an earlier run got to (0: none), where this one
    /// begins: the step at or below it.
    pub fn new(start: f64, remembered: f64) -> RateControl {
        let mut steps: Vec<f64> = RATE_STEPS.iter().copied().filter(|s| *s > start).collect();
        steps.insert(0, start);
        let rate = steps
            .iter()
            .copied()
            .filter(|step| *step <= remembered)
            .fold(start, f64::max);
        RateControl {
            steps,
            rate,
            changes: 0,
            floored: false,
            base: None,
            window: 0.0,
            calm: 0.0,
            settled: 0.0,
            held: Vec::new(),
        }
    }

    /// judge: with the run's counts (the sender's waits, its blocks and the slow ones among
    /// them) at `now`, as often as wanted: the new rate when one is due.
    pub fn judge(&mut self, counts: (u64, u64, u64), now: f64) -> Option<f64> {
        let Some(base) = self.base else {
            self.base = Some(counts);
            (self.window, self.calm) = (now, now);
            self.settled = now + Self::SETTLE_S;
            return None;
        };
        if now < self.settled || self.window < self.settled {
            // (settling: the window begins after)
            self.base = Some(counts);
            self.window = now;
            return None;
        }
        let waits = counts.0.saturating_sub(base.0);
        let blocks = counts.1.saturating_sub(base.1);
        let slow = counts.2.saturating_sub(base.2);
        let trouble =
            waits >= Self::TROUBLE_WAITS || (slow >= Self::TROUBLE_SLOW && 2 * slow >= blocks);
        if !trouble && now - self.window < Self::WINDOW_S {
            return None;
        }
        self.base = Some(counts); // a new window
        self.window = now;
        if trouble {
            self.calm = now;
            let rate = self.rate;
            match self.held.iter_mut().find(|(step, _, _)| *step == rate) {
                Some(entry) => {
                    entry.1 = now + Self::HOLD_S * 2f64.powi(entry.2 as i32);
                    entry.2 += 1;
                }
                None => self.held.push((rate, now + Self::HOLD_S, 1)),
            }
            let moved = self.step(-1);
            self.floored = moved.is_none();
            return moved;
        }
        if now - self.calm < Self::CALM_S {
            return None;
        }
        let above = self.beside(1)?;
        let until = self
            .held
            .iter()
            .find(|(step, _, _)| *step == above)
            .map_or(0.0, |entry| entry.1);
        if until > now {
            return None;
        }
        self.calm = now;
        self.step(1)
    }

    fn beside(&self, direction: i64) -> Option<f64> {
        let at = self.steps.iter().position(|step| *step == self.rate)? as i64 + direction;
        usize::try_from(at)
            .ok()
            .and_then(|at| self.steps.get(at))
            .copied()
    }

    fn step(&mut self, direction: i64) -> Option<f64> {
        let rate = self.beside(direction)?;
        self.rate = rate;
        self.changes += 1;
        self.settled = self.window + Self::SETTLE_S; // (the window began now)
        Some(rate)
    }
}

/// The panel a rate is for: its model, its place on the bus and the picture's size.
pub type RateKey = (String, String, (i64, i64));

/// The bit rate "auto" found for a panel, kept per process (RATES): a panel opened again
/// after a reconnect goes on at it instead of climbing again.
pub fn rates() -> std::sync::MutexGuard<'static, HashMap<RateKey, RateControl>> {
    static RATES: OnceLock<Mutex<HashMap<RateKey, RateControl>>> = OnceLock::new();
    RATES
        .get_or_init(Default::default)
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

fn key_text(key: &RateKey) -> String {
    format!("{} {} {}x{}", key.0, key.1, key.2 .0, key.2 .1)
}

/// What the video mode found out, kept for the next run: video-state.json beside
/// config.toml (video_state.py). One that cannot be read counts as empty.
#[derive(Clone, Debug)]
pub struct VideoState {
    pub path: PathBuf,
}

impl VideoState {
    /// The state kept in `folder` (config.toml's).
    pub fn in_folder(folder: &Path) -> VideoState {
        VideoState {
            path: folder.join(STATE_FILENAME),
        }
    }

    fn read(&self) -> lp_py::Dict {
        let text = std::fs::read(&self.path)
            .ok()
            .and_then(|bytes| String::from_utf8(bytes).ok());
        match text.map(|text| lp_py::json::loads(&text)) {
            Some(Ok(Value::Dict(data))) => data,
            _ => lp_py::Dict::default(),
        }
    }

    /// rate: the bit rate "auto" got to on this panel last time, in Mbit/s; 0 when none is
    /// known.
    pub fn rate(&self, key: &RateKey) -> f64 {
        let Some(Value::Dict(rates)) = self.read().get("rates").cloned() else {
            return 0.0;
        };
        let value = match rates.get(&key_text(key)) {
            Some(Value::Float(value)) => *value,
            Some(Value::Int(value)) => lp_py::num::int_to_float(value).unwrap_or(0.0),
            _ => 0.0,
        };
        if value > 0.0 {
            value
        } else {
            0.0
        }
    }

    /// keep_rate: `mbit` for this panel, the rest of the file as it was; written in whole (a
    /// file beside it first), only where config.toml's folder is there.
    pub fn keep_rate(&self, key: &RateKey, mbit: f64) {
        let Some(folder) = self.path.parent().filter(|folder| folder.is_dir()) else {
            return;
        };
        let mut data = self.read();
        let mut rates = match data.get("rates") {
            Some(Value::Dict(rates)) => rates.clone(),
            _ => lp_py::Dict::default(),
        };
        rates.insert(key_text(key), Value::Float(mbit));
        data.insert("rates".to_string(), Value::Dict(rates));
        let how = lp_py::json::Dumps {
            indent: Some(" ".into()),
            ..lp_py::json::Dumps::default()
        };
        let text = lp_py::json::dumps(&Value::Dict(data), &how) + "\n";
        let part = folder.join(format!("{STATE_FILENAME}.{}.tmp", std::process::id()));
        if std::fs::write(&part, text).is_err() || std::fs::rename(&part, &self.path).is_err() {
            let _ = std::fs::remove_file(&part);
        }
    }
}
