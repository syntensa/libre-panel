//! How the main loop is doing (meter.py): frame rate, drawing time, data rate, the video link,
//! faults. The loop tells the meter of every frame it draws (`drew`) and every step it takes
//! (`tick`); once a second the numbers are made and handed to the run's status (`stats`),
//! where the tray, the app's panel menu and its Control page read them.

use std::collections::VecDeque;

use lp_devices::base::Counters;
use lp_py::{Dict, Value};

/// drawing times kept: what counts is the last few seconds
const FRAMES: usize = 120;
/// how often the numbers are made
const EVERY_S: f64 = 1.0;
/// rates are measured over the samples kept: the last five seconds
const SAMPLES: usize = 6;

/// What the drivers counted in this run: those of the panels opened before (a replug makes
/// the display anew) and of the one there now.
#[derive(Clone, Copy, Debug, Default)]
pub struct Counted {
    before: Counters,
    now: Counters,
    /// the video's bit rate cap in force, Mbit/s
    pub bit_rate: f64,
}

impl Counted {
    /// The panel's own counters as they stand.
    pub fn follow(&mut self, now: Counters, bit_rate: f64) {
        self.now = now;
        if bit_rate > 0.0 {
            self.bit_rate = bit_rate;
        }
    }

    /// The panel let go: what it counted stays in the run's.
    pub fn closed(&mut self) {
        self.before = self.total();
        self.now = Counters::default();
    }

    pub fn total(&self) -> Counters {
        let (a, b) = (&self.before, &self.now);
        Counters {
            bytes: a.bytes + b.bytes,
            usb_errors: a.usb_errors + b.usb_errors,
            blocks: a.blocks + b.blocks,
            waits: a.waits + b.waits,
            drops: a.drops + b.drops,
            max_depth: a.max_depth.max(b.max_depth),
            slow_blocks: a.slow_blocks + b.slow_blocks,
            panel_restarts: a.panel_restarts + b.panel_restarts,
        }
    }
}

/// Counts frames and times their drawing; makes the stats once a second.
pub struct Meter {
    began: f64,
    next_at: f64,
    drawn: u64,
    times: VecDeque<f64>,
    /// (time, frames drawn, bytes sent, blocks sent)
    samples: VecDeque<(f64, u64, u64, u64)>,
}

/// Python's round(x, 1).
fn round1(x: f64) -> f64 {
    lp_py::round_digits(x, 1)
}

/// statistics.median
fn median(times: &VecDeque<f64>) -> f64 {
    let mut sorted: Vec<f64> = times.iter().copied().collect();
    sorted.sort_by(f64::total_cmp);
    let n = sorted.len();
    match n % 2 {
        1 => sorted[n / 2],
        _ => (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0,
    }
}

impl Meter {
    /// A meter from `now`, on the main loop's clock.
    pub fn new(now: f64, counted: &Counted) -> Meter {
        let mut meter = Meter {
            began: now,
            next_at: now + EVERY_S,
            drawn: 0,
            times: VecDeque::with_capacity(FRAMES),
            samples: VecDeque::with_capacity(SAMPLES),
        };
        let sample = meter.sample(now, counted);
        meter.samples.push_back(sample);
        meter
    }

    fn sample(&self, now: f64, counted: &Counted) -> (f64, u64, u64, u64) {
        let total = counted.total();
        (now, self.drawn, total.bytes, total.blocks)
    }

    /// A frame is drawn (render, fit, transition and message); it took `seconds`.
    pub fn drew(&mut self, seconds: f64) {
        if self.times.len() == FRAMES {
            self.times.pop_front();
        }
        self.times.push_back(seconds);
        self.drawn += 1;
    }

    /// After a step: the new numbers once a second has passed since the last ones (None
    /// before). `target_fps` is the rate the loop plans for, `streaming` whether the panel
    /// wants every frame (video).
    pub fn tick(
        &mut self,
        now: f64,
        target_fps: i64,
        streaming: bool,
        counted: &Counted,
    ) -> Option<Dict> {
        if now < self.next_at {
            return None;
        }
        self.next_at = now + EVERY_S; // (not += EVERY_S: a stalled loop makes no burst)
        if self.samples.len() == SAMPLES {
            self.samples.pop_front();
        }
        let sample = self.sample(now, counted);
        self.samples.push_back(sample);
        Some(self.stats(now, target_fps, streaming, counted))
    }

    /// The numbers. None is "not known" (nothing counted it): the data rate for a driver that
    /// does not count, the video link outside video mode.
    fn stats(&self, now: f64, target_fps: i64, streaming: bool, counted: &Counted) -> Dict {
        let (first, last) = (self.samples[0], self.samples[self.samples.len() - 1]);
        let span = last.0 - first.0;
        let rate = |a: u64, b: u64, scale: f64| match span > 0.0 {
            true => Value::Float(round1((b as f64 - a as f64) / span / scale)),
            false => Value::None,
        };
        let total = counted.total();
        let video = total.blocks > 0; // (the video link has been used in this run)
        let ms = |seconds: f64| Value::Float(round1(seconds * 1000.0));
        let times = !self.times.is_empty();
        let peak = self.times.iter().copied().fold(f64::MIN, f64::max);
        let int = |n: u64| Value::int(n as i64);
        let video_only = |value: Value| match video {
            true => value,
            false => Value::None,
        };
        let pairs = vec![
            ("fps", rate(first.1, last.1, 1.0)),
            ("target_fps", Value::int(target_fps)),
            ("streaming", Value::Bool(streaming)),
            (
                "render_ms",
                match times {
                    true => ms(median(&self.times)),
                    false => Value::None,
                },
            ),
            (
                "render_peak_ms",
                match times {
                    true => ms(peak),
                    false => Value::None,
                },
            ),
            (
                "kbps",
                match total.bytes > 0 {
                    true => rate(first.2, last.2, 1000.0),
                    false => Value::None,
                },
            ),
            ("blocks_per_s", video_only(rate(first.3, last.3, 1.0))),
            ("max_depth", video_only(int(total.max_depth))),
            ("bit_rate", video_only(Value::Float(counted.bit_rate))),
            // (the encoder and what it is fed: the engine's settings are not told here yet)
            ("encoder", Value::None),
            ("waits", video_only(int(total.waits))),
            ("drops", video_only(int(total.drops))),
            ("usb_errors", int(total.usb_errors)),
            ("panel_restarts", int(total.panel_restarts)),
            ("ffmpeg_restarts", int(0)),
            (
                "uptime_s",
                Value::int((now - self.began).round_ties_even() as i64),
            ),
        ];
        pairs
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect()
    }
}
