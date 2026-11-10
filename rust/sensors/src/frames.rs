//! The frames of the game one plays, counted (sensors/presentmon.py's FrameTimes,
//! FrameCounter, limited_by and Columns): what PresentMon's lines (or RTSS's and MangoHud's
//! samples) come to.
//!
//! Readings: `game.fps` (the last second), `game.frametime` (ms), `game.low` (the 1% lows of
//! the last 30 s, in fps), `game.frametime_peak` (the longest frame time since the last
//! reading), `game.limiter` (`cpu`, `gpu` or `cap`: who holds the frame rate down), `game.app`
//! (the program), `game.fps_trace` (the frame rate finely: 25 points a second over the last
//! five seconds, as a list of numbers in text), and what every frame of the round has come to
//! (`game.fps_avg`, `game.fps_1low`, `game.fps_01low`, `game.frametime_avg`,
//! `game.frametime_max`).
//!
//! The game is the program that the `game` service found (`set_target`); without it, or where
//! that program drew nothing for the last half minute, the program with the most frames. The
//! desktop compositor and known overlays do not count.

use std::collections::{BTreeMap, VecDeque};

use indexmap::IndexMap;
use lp_py::Value;
use lp_render::Reading;

use crate::lhm::Readings;

/// seconds the frame rate is taken over
pub const WINDOW: f64 = 1.0;
/// seconds the 1% lows are taken over
pub const LOWS_WINDOW: f64 = 30.0;
/// a round's frame times up to this are counted in steps of 0.01 ms, longer of 1 ms
pub const FINE_MS: f64 = 100.0;
/// what a round's frames come to is worked out anew at most this often
pub const ROUND_EVERY_S: f64 = 1.0;
/// with no game found for us: the peak goes on this long after the last frame
pub const HOLD_SECONDS: f64 = 3.0;
/// seconds of the fine frame rate in each reading
pub const TRACE_S: f64 = 5.0;
/// a point is the rate of the frames of this long up to it ...
pub const TRACE_WINDOW_S: f64 = 0.1;
/// ... of the samples of this long, where they are samples (RTSS's)
pub const SAMPLED_WINDOW_S: f64 = 0.3;
/// a frame placed this much earlier than it came: placed anew from it
pub const TRACE_SLACK_S: f64 = 0.5;
/// A fine course of a reading: this many points a second, the last this long before the
/// reading that brings it (sensors/base.py).
pub const TRACE_RATE: f64 = 25.0;
pub const TRACE_LAG_S: f64 = 0.25;
pub const IGNORED: [&str; 9] = [
    "dwm.exe",
    "explorer.exe",
    "<unknown>",
    "steamwebhelper.exe",
    "discord.exe",
    "nvidia overlay.exe",
    "gamebar.exe",
    "librepanel.exe",
    "libre-panel.exe",
];
/// the time from one frame to the next, as PresentMon 2 (and 1) names it
const FRAME_COLUMNS: [&str; 4] = [
    "MsBetweenPresents",
    "msBetweenPresents",
    "FrameTime",
    "MsBetweenAppStart",
];
/// how long each side works and waits for the other, per frame (PresentMon 2): named like
/// this by 2.3 and by --v2_metrics, with an Ms in front by the default of 2.6 and later
const WAIT_COLUMNS: [[&str; 4]; 2] = [
    ["CPUBusy", "CPUWait", "GPUBusy", "GPUWait"],
    ["MsCPUBusy", "MsCPUWait", "MsGPUBusy", "MsGPUWait"],
];
const FINE_STEPS: i64 = 10_000;
/// What the frames of a round come to: the readings' names after `game.`, unit and label.
pub const ROUND_READINGS: [(&str, &str, &str); 5] = [
    ("fps_avg", "fps", "Average FPS of the round"),
    ("fps_1low", "fps", "1% low of the round"),
    ("fps_01low", "fps", "0.1% low of the round"),
    ("frametime_avg", "ms", "Average frame time of the round"),
    ("frametime_max", "ms", "Longest frame of the round"),
];

/// How long the processor and the graphics card work and wait for each other in a frame:
/// CPU busy, CPU wait, GPU busy, GPU wait.
pub type Waits = [f64; 4];

/// Every frame time of a round, counted by its length: in steps of 0.01 ms up to `FINE_MS`,
/// of 1 ms above (a stutter that long is one however it is rounded). So a round of any length
/// takes at most some 15,000 counts, and its lows are those of all its frames, exact to the
/// step.
#[derive(Default)]
pub struct FrameTimes {
    /// step -> frames of that length
    counts: BTreeMap<i64, u64>,
    pub frames: u64,
    pub total_ms: f64,
    pub longest: f64,
    /// (when, figures)
    figures: Option<(f64, Option<[f64; 5]>)>,
}

impl FrameTimes {
    pub fn add(&mut self, ms: f64) {
        let step = if ms < FINE_MS {
            (lp_py::round(ms * 100.0) as i64).max(1)
        } else {
            FINE_STEPS + lp_py::round(ms - FINE_MS) as i64
        };
        *self.counts.entry(step).or_insert(0) += 1;
        self.frames += 1;
        self.total_ms += ms;
        self.longest = self.longest.max(ms);
    }

    /// The frame time (ms) that `per_mille` thousandths of the frames take at most, by the
    /// nearest rank (990: the 99th percentile); none without frames.
    pub fn percentile(&self, per_mille: u64) -> Option<f64> {
        if self.frames == 0 {
            return None;
        }
        let rank = (self.frames * per_mille).div_ceil(1000);
        // the frames from that rank on, the slowest first
        let slower = (self.frames + 1).saturating_sub(rank);
        let mut seen = 0;
        for (step, count) in self.counts.iter().rev() {
            seen += count;
            if seen >= slower {
                return Some(if *step <= FINE_STEPS {
                    *step as f64 / 100.0
                } else {
                    FINE_MS + (*step - FINE_STEPS) as f64
                });
            }
        }
        None
    }

    /// What the frames come to, in the order of `ROUND_READINGS`; none without frames. With
    /// `now` (seconds) worked out at most every `ROUND_EVERY_S`.
    pub fn figures(&mut self, now: Option<f64>) -> Option<[f64; 5]> {
        if let (Some(now), Some((at, found))) = (now, &self.figures) {
            if now - at < ROUND_EVERY_S {
                return *found;
            }
        }
        let found = (self.frames > 0).then(|| {
            let mean = self.total_ms / self.frames as f64;
            let at = |per_mille| {
                self.percentile(per_mille)
                    .filter(|ms| *ms != 0.0)
                    .unwrap_or(mean)
            };
            [
                1000.0 / mean,
                1000.0 / at(990),
                1000.0 / at(999),
                mean,
                self.longest,
            ]
        });
        if let Some(now) = now {
            self.figures = Some((now, found));
        }
        found
    }
}

/// statistics.median.
fn median(mut values: Vec<f64>) -> f64 {
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = values.len();
    if n % 2 == 1 {
        values[n / 2]
    } else {
        (values[n / 2 - 1] + values[n / 2]) / 2.0
    }
}

/// limited_by: `cpu`, `gpu` or `cap` from the waits of the last frames, or none. Judged from
/// the waits, not from the load: when the GPU waits longer for the CPU than the CPU for the
/// GPU, the CPU holds the frame rate down. When neither waits much and both work much less
/// than a frame takes, something else does: a frame rate limit, or vertical sync.
pub fn limited_by(waits: &[Waits], mean: f64) -> Option<&'static str> {
    if waits.len() < 3 || mean.is_nan() || mean <= 0.0 {
        return None;
    }
    let of = |i: usize| median(waits.iter().map(|w| w[i]).collect());
    let (cpu_busy, cpu_wait, gpu_busy, gpu_wait) = (of(0), of(1), of(2), of(3));
    if cpu_busy.max(gpu_busy) < 0.6 * mean && cpu_wait.max(gpu_wait) < 0.25 * mean {
        return Some("cap");
    }
    Some(if gpu_wait > cpu_wait { "cpu" } else { "gpu" })
}

/// Where PresentMon's CSV puts what is needed. The header row comes with the first frame, not
/// when PresentMon starts.
#[derive(Clone, Debug, PartialEq)]
pub struct Columns {
    pub app: usize,
    pub frame: usize,
    pub waits: Option<[usize; 4]>,
}

impl Columns {
    /// The columns of a header row; none if the row is no header or has no frame time.
    pub fn of(header: &[String]) -> Option<Columns> {
        let place = |name: &str| header.iter().position(|column| column == name);
        let frame = FRAME_COLUMNS.iter().find_map(|name| place(name))?;
        let app = place("Application")?;
        let waits = WAIT_COLUMNS.iter().find_map(|names| {
            Some([
                place(names[0])?,
                place(names[1])?,
                place(names[2])?,
                place(names[3])?,
            ])
        });
        Some(Columns { app, frame, waits })
    }

    /// (program, frame time, waits) of a row; none for a row that is no frame (PresentMon
    /// writes `NA` where it measured nothing).
    pub fn frame_of(&self, row: &[String]) -> Option<(String, f64, Option<Waits>)> {
        let number = |at: usize| lp_py::float_of_str(row.get(at)?);
        let (app, ms) = (row.get(self.app)?.clone(), number(self.frame)?);
        let waits = self.waits.and_then(|at| {
            Some([
                number(at[0])?,
                number(at[1])?,
                number(at[2])?,
                number(at[3])?,
            ])
        });
        Some((app, ms, waits))
    }
}

/// A line of CSV as its fields (csv.reader's default dialect: commas, fields in double quotes
/// with a quote doubled inside).
pub fn csv_row(line: &str) -> Vec<String> {
    let mut fields = Vec::new();
    let mut field = String::new();
    let mut chars = line.chars().peekable();
    let mut quoted = false;
    while let Some(ch) = chars.next() {
        if quoted {
            if ch == '"' {
                if chars.peek() == Some(&'"') {
                    field.push('"');
                    chars.next();
                } else {
                    quoted = false;
                }
            } else {
                field.push(ch);
            }
        } else if ch == '"' && field.is_empty() {
            quoted = true;
        } else if ch == ',' {
            fields.push(std::mem::take(&mut field));
        } else if ch != '\r' && ch != '\n' {
            field.push(ch);
        }
    }
    fields.push(field);
    fields
}

type Frame = (f64, f64, Option<Waits>);

fn float(value: f64, unit: &str, label: &str) -> Reading {
    Reading {
        unit: unit.into(),
        label: label.into(),
        ..Reading::of(Value::Float(value))
    }
}

fn text(value: &str, unit: &str, label: &str) -> Reading {
    Reading {
        unit: unit.into(),
        label: label.into(),
        ..Reading::of(Value::Str(value.into()))
    }
}

/// Frames per program as they come; the rate of the one that counts.
///
/// The one that counts is the target (the game the service found), else (or where the target
/// drew nothing in the last half minute) the program with the most frames in the last second.
/// Each program's round holds its frames since it began to draw; half a minute without a
/// frame ends it. The target's lasts as long as it is the target, and ends when another
/// program, or none, is.
pub struct FrameCounter {
    /// every frame (PresentMon's), or samples of the frame time (RTSS's, MangoHud's): those
    /// are placed when they came, and between them the last one holds
    every_frame: bool,
    /// app -> frames of the last LOWS_WINDOW
    frames: IndexMap<String, VecDeque<Frame>>,
    /// app -> (when it was shown, how long it took) of the last TRACE_S and a bit
    shown: IndexMap<String, VecDeque<(f64, f64)>>,
    /// app -> the sum of its frame times so far, and the clock's time at that sum's 0
    sums: IndexMap<String, (f64, f64)>,
    /// app -> longest frame since the last readings()
    peaks: IndexMap<String, f64>,
    /// app -> every frame of its round
    rounds: IndexMap<String, FrameTimes>,
    target: Option<String>,
    /// the program that counted, and when
    last: Option<(String, f64)>,
}

impl FrameCounter {
    pub fn new(every_frame: bool) -> FrameCounter {
        FrameCounter {
            every_frame,
            frames: IndexMap::new(),
            shown: IndexMap::new(),
            sums: IndexMap::new(),
            peaks: IndexMap::new(),
            rounds: IndexMap::new(),
            target: None,
            last: None,
        }
    }

    fn is_target(&self, app: &str) -> bool {
        self.target.as_deref() == Some(lp_py::lower(app).as_str())
    }

    /// Which program is the game, as the `game` service finds it; none takes it back.
    pub fn set_target(&mut self, exe: Option<&str>) {
        let exe = exe.filter(|exe| !exe.is_empty()).map(lp_py::lower);
        if exe != self.target {
            let target = self.target.clone();
            // the game's round is over
            self.rounds
                .retain(|app, _| Some(lp_py::lower(app)) != target);
            self.target = exe;
            self.last = None;
        }
    }

    pub fn add(&mut self, app: &str, ms: f64, at: f64, waits: Option<Waits>) {
        if app.is_empty()
            || IGNORED.contains(&lp_py::lower(app).as_str())
            || !(0.0 < ms && ms < 5000.0)
        {
            return;
        }
        let is_target = self.is_target(app);
        let frames = self.frames.entry(app.to_string()).or_default();
        if frames.back().is_some_and(|last| at - last.0 > LOWS_WINDOW) && !is_target {
            self.rounds.shift_remove(app); // half a minute without a frame: another round
        }
        frames.push_back((at, ms, waits));
        let peak = self.peaks.entry(app.to_string()).or_insert(0.0);
        *peak = peak.max(ms);
        self.rounds.entry(app.to_string()).or_default().add(ms);
        while frames
            .front()
            .is_some_and(|first| at - first.0 > LOWS_WINDOW)
        {
            frames.pop_front();
        }
        let placed = self.placed(app, ms, at);
        let shown = self.shown.entry(app.to_string()).or_default();
        // (in their order)
        let placed = shown.back().map_or(placed, |last| placed.max(last.0));
        shown.push_back((placed, ms));
        while let (Some(first), Some(last)) = (shown.front(), shown.back()) {
            if last.0 - first.0 > TRACE_S + 1.0 {
                shown.pop_front();
            } else {
                break;
            }
        }
    }

    /// When a frame that came at `at` was shown, on the clock of `at`. Every frame: the sum
    /// of the frame times so far, from the moment that fits the frame that came the soonest
    /// after it was shown (lines come in bunches, never before their frame); it is placed
    /// anew where a frame comes much later than it would be placed (frames missed, a pause).
    /// Samples: when they came.
    fn placed(&mut self, app: &str, ms: f64, at: f64) -> f64 {
        if !self.every_frame {
            return at;
        }
        let (mut total, mut zero) = self.sums.get(app).copied().unwrap_or((0.0, at));
        total += ms / 1000.0;
        if at - total < zero || at - (zero + total) > TRACE_SLACK_S {
            zero = at - total;
        }
        self.sums.insert(app.to_string(), (total, zero));
        zero + total
    }

    /// What the frames say at `now`. Called once per reading of the sensors: the peak is the
    /// longest frame since the call before.
    pub fn readings(&mut self, now: f64) -> Readings {
        let mut best = self.counted(now);
        let peaks = std::mem::take(&mut self.peaks);
        let mut out = Readings::new();
        if let Some(app) = &best {
            self.last = Some((app.clone(), now));
            out = self.metrics(app, now);
        } else if self.target.is_none() {
            match &self.last {
                // a game that stalls for a moment is still a game
                Some((app, at)) if now - at <= HOLD_SECONDS => best = Some(app.clone()),
                _ => return out, // no game: no peak, nothing
            }
        }
        // With a game found (or just seen) the peak is always there, 0 without a frame. The
        // game found counts however few frames it has: a hitch of a second is one frame.
        let targeted = self.target.is_some() && best.as_ref().is_none_or(|app| self.is_target(app));
        let peak = if targeted {
            peaks
                .iter()
                .filter(|(app, _)| self.is_target(app))
                .map(|(_, peak)| *peak)
                .fold(0.0, f64::max)
        } else {
            peaks
                .get(best.as_deref().unwrap_or(""))
                .copied()
                .unwrap_or(0.0)
        };
        out.insert(
            "game.frametime_peak".into(),
            float(lp_py::round_digits(peak, 2), "ms", "Frame time peak"),
        );
        out
    }

    /// The program whose frames count now, or none (fewer than two in the last second).
    fn counted(&mut self, now: f64) -> Option<String> {
        let mut counts: Vec<(String, usize)> = Vec::new();
        let apps: Vec<String> = self.frames.keys().cloned().collect();
        for app in apps {
            let Some(frames) = self.frames.get_mut(&app) else {
                continue;
            };
            while frames
                .front()
                .is_some_and(|first| now - first.0 > LOWS_WINDOW)
            {
                frames.pop_front();
            }
            if frames.is_empty() {
                self.frames.shift_remove(&app);
                self.peaks.shift_remove(&app);
                self.shown.shift_remove(&app);
                self.sums.shift_remove(&app);
                if !self.is_target(&app) {
                    // (the game's round goes on while it is the game)
                    self.rounds.shift_remove(&app);
                }
                continue;
            }
            let count = frames
                .iter()
                .filter(|frame| now - frame.0 <= WINDOW)
                .count();
            counts.push((app, count));
        }
        if self.target.is_some() {
            // The program the game service found counts. Where it drew nothing for the last
            // half minute (a launcher, an anti-cheat's starter that another program of the
            // game draws for), the program with the most frames does.
            let mine: Vec<(String, usize)> = counts
                .iter()
                .filter(|(app, _)| self.is_target(app))
                .cloned()
                .collect();
            if !mine.is_empty() {
                counts = mine;
            }
        }
        let mut best: Option<(String, usize)> = None;
        for (app, count) in counts {
            if count > best.as_ref().map_or(0, |(_, most)| *most) {
                best = Some((app, count));
            }
        }
        best.filter(|(_, count)| *count >= 2).map(|(app, _)| app)
    }

    fn metrics(&mut self, app: &str, now: f64) -> Readings {
        let frames = &self.frames[app];
        let recent: Vec<&Frame> = frames.iter().filter(|f| now - f.0 <= WINDOW).collect();
        let mean = lp_py::sum_floats(recent.iter().map(|f| f.1)) / recent.len() as f64;
        let mut slowest: Vec<f64> = frames.iter().map(|f| f.1).collect();
        slowest.sort_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));
        let low = &slowest[..(slowest.len() / 100).max(1)]; // the slowest 1 %
        let low = lp_py::sum_floats(low.iter().copied()) / low.len() as f64;
        let name = if lp_py::lower(app).ends_with(".exe") {
            let cut = app.chars().count() - 4;
            app.chars().take(cut).collect()
        } else {
            app.to_string()
        };
        let waits: Vec<Waits> = recent.iter().filter_map(|f| f.2).collect();
        let mut out = Readings::new();
        out.insert(
            "game.fps".into(),
            float(
                lp_py::round_digits(1000.0 / mean, 1),
                "fps",
                "Frames per second",
            ),
        );
        out.insert(
            "game.frametime".into(),
            float(lp_py::round_digits(mean, 2), "ms", "Frame time"),
        );
        out.insert(
            "game.low".into(),
            float(lp_py::round_digits(1000.0 / low, 1), "fps", "1% low"),
        );
        out.insert("game.app".into(), text(&name, "", "Game"));
        if let Some(trace) = self.trace(app, now) {
            out.insert(
                "game.fps_trace".into(),
                text(&trace, "fps", "Frame rate, 25 points a second"),
            );
        }
        if let Some(limiter) = limited_by(&waits, mean) {
            out.insert("game.limiter".into(), text(limiter, "", "Limited by"));
        }
        let figures = self
            .rounds
            .get_mut(app)
            .and_then(|round| round.figures(Some(now)));
        for ((name, unit, label), value) in ROUND_READINGS.iter().zip(figures.into_iter().flatten())
        {
            let digits = if *unit == "fps" { 1 } else { 2 };
            out.insert(
                format!("game.{name}"),
                float(lp_py::round_digits(value, digits), unit, label),
            );
        }
        out
    }

    /// `app`'s frame rate finely (`game.fps_trace`): `TRACE_RATE` points a second over the
    /// last `TRACE_S`, the last `TRACE_LAG_S` before `now`, as a list of numbers in text:
    /// `null` before its first frame, and at the end where the frame under way has not come
    /// yet (the next reading has it); none without frames.
    pub fn trace(&self, app: &str, now: f64) -> Option<String> {
        let shown = self.shown.get(app).filter(|shown| !shown.is_empty())?;
        let count = lp_py::round(TRACE_S * TRACE_RATE) as usize;
        let end = now - TRACE_LAG_S;
        let window = if self.every_frame {
            TRACE_WINDOW_S
        } else {
            SAMPLED_WINDOW_S
        };
        let mut points: Vec<Option<f64>> = Vec::with_capacity(count);
        let mut first = 0; // the first frame after the window's start
        for k in 0..count {
            let at = end - (count - 1 - k) as f64 / TRACE_RATE;
            while first < shown.len() && shown[first].0 <= at - window {
                first += 1;
            }
            let mut last = first; // the first frame after `at`
            let mut total = 0.0;
            while last < shown.len() && shown[last].0 <= at {
                total += shown[last].1;
                last += 1;
            }
            let mut frames = last - first;
            if self.every_frame {
                if last == shown.len() {
                    break; // the frame under way then has not come yet
                }
                if last == 0 && at <= shown[0].0 - shown[0].1 / 1000.0 {
                    points.push(None); // before the first
                    continue;
                }
                total += shown[last].1;
                frames += 1;
            } else if frames == 0 && first > 0 {
                // between two samples the last one holds
                total = shown[first - 1].1;
                frames = 1;
            }
            points.push(
                (frames > 0 && total > 0.0)
                    .then(|| lp_py::round_digits(1000.0 * frames as f64 / total, 1)),
            );
        }
        points.resize(count, None); // (not known yet)
        let said: Vec<String> = points
            .iter()
            .map(|point| point.map_or("null".to_string(), lp_py::float_repr))
            .collect();
        Some(format!("[{}]", said.join(",")))
    }
}
