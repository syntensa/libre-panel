//! Settings → Panel → Video experiment, run by itself (`/api/experiment`, editor/experiment.py).
//!
//! The steps try what a panel takes beyond what was measured on the 9.2": the bit rate's cap
//! left to "auto" for minutes (its ladder climbs), a lower crf, x264's fastest preset, the
//! profiles main and high, short keyframe spacing, the graphics card's encoder where the PC
//! has one, 60 frames a second, and a flood (a high cap at a low crf). Each step writes its
//! settings (every panel's loop opens its panel again with them), lets the panel settle, then
//! samples the numbers the meter makes (`RunStatus::stats`) for every panel for a while, and
//! judges them. The settings from before the run come back at the end, or when it is stopped.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, Weak};
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_py::{Dict, Value};

use crate::background::BackgroundApp;
use crate::home::Home;
use crate::settings::{Restart, Settings};

/// a change: the panel opens again, the encoder starts, the meter's first second
const SETTLE_S: f64 = 6.0;
const SAMPLE_S: f64 = 1.0;
/// what is worse than what, for a step's verdict
const WORST: [&str; 5] = ["no video", "ok", "pc", "stutter", "failed"];

/// The video mode's defaults (config.py VideoConfig): what every step starts from.
fn measured() -> Vec<(&'static str, Value)> {
    vec![
        ("video.maxrate", Value::Str("auto".into())),
        ("video.crf", Value::int(20)),
        ("video.preset", Value::Str("superfast".into())),
        ("video.profile", Value::Str("auto".into())),
        ("video.encoder", Value::Str("auto".into())),
        ("video.keyframe_s", Value::Float(10.0)),
        ("video.fps", Value::int(25)),
        ("video.device_fps", Value::int(60)),
    ]
}

#[derive(Clone)]
struct Step {
    id: String,
    /// what differs from the measured settings
    changes: Vec<(&'static str, Value)>,
    /// how long the numbers are sampled
    seconds: f64,
}

/// gpu_vendor: "amd", "nvidia", "intel" or "" from the graphics card's name.
fn gpu_vendor(name: &str) -> &'static str {
    let lower = name.to_lowercase();
    let any = |words: &[&str]| words.iter().any(|word| lower.contains(word));
    if any(&["nvidia", "geforce", "rtx", "gtx", "quadro"]) {
        "nvidia"
    } else if any(&["amd", "radeon", "ati "]) {
        "amd"
    } else if any(&["intel", "arc ", "iris"]) {
        "intel"
    } else {
        ""
    }
}

/// steps: the steps in their order; the graphics card's encoder only where `vendor` has one.
fn steps(vendor: &str) -> Vec<Step> {
    let step = |id: &str, changes: Vec<(&'static str, Value)>, seconds: f64| Step {
        id: id.to_string(),
        changes,
        seconds,
    };
    let mut found = vec![
        // ("auto" climbs a step a minute while the panel keeps up)
        step("auto", vec![], 240.0),
        step("crf-18", vec![("video.crf", Value::int(18))], 30.0),
        step(
            "ultrafast",
            vec![("video.preset", Value::Str("ultrafast".into()))],
            20.0,
        ),
        step(
            "main",
            vec![("video.profile", Value::Str("main".into()))],
            20.0,
        ),
        step(
            "high",
            vec![("video.profile", Value::Str("high".into()))],
            20.0,
        ),
        step(
            "keyframe-2",
            vec![("video.keyframe_s", Value::Float(2.0))],
            20.0,
        ),
    ];
    let encoder = match vendor {
        "amd" => Some("h264_amf"),
        "nvidia" => Some("h264_nvenc"),
        "intel" => Some("h264_qsv"),
        _ => None,
    };
    if let Some(encoder) = encoder {
        found.push(step(
            encoder,
            vec![("video.encoder", Value::Str(encoder.into()))],
            30.0,
        ));
    }
    let fps = |fps: i64, device: i64| {
        vec![
            ("video.fps", Value::int(fps)),
            ("video.device_fps", Value::int(device)),
        ]
    };
    found.push(step("fps-50", fps(50, 60), 30.0));
    found.push(step("fps-60", fps(60, 70), 30.0));
    let flood = vec![
        ("video.maxrate", Value::Str("24M".into())),
        ("video.crf", Value::int(16)),
    ];
    found.push(step("flood", flood, 30.0));
    found
}

fn number(value: Option<&Value>) -> Option<f64> {
    match value? {
        Value::Int(_) | Value::Float(_) => value?.to_f64(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        _ => None,
    }
}

/// math.fsum: the sum of `values` rounded once, as CPython's mathmodule.c makes it (Shewchuk's
/// partials, the last of them rounded half to even).
fn fsum(values: &[f64]) -> f64 {
    let mut partials: Vec<f64> = Vec::new();
    for &value in values {
        let mut x = value;
        let mut i = 0;
        for j in 0..partials.len() {
            let mut y = partials[j];
            if x.abs() < y.abs() {
                std::mem::swap(&mut x, &mut y);
            }
            let hi = x + y;
            let lo = y - (hi - x);
            if lo != 0.0 {
                partials[i] = lo;
                i += 1;
            }
            x = hi;
        }
        partials.truncate(i);
        partials.push(x);
    }
    let mut n = partials.len();
    let mut hi = 0.0;
    if n > 0 {
        n -= 1;
        hi = partials[n];
        let mut lo = 0.0;
        while n > 0 {
            let x = hi;
            n -= 1;
            let y = partials[n];
            hi = x + y;
            let yr = hi - x;
            lo = y - yr;
            if lo != 0.0 {
                break;
            }
        }
        if n > 0 && ((lo < 0.0 && partials[n - 1] < 0.0) || (lo > 0.0 && partials[n - 1] > 0.0)) {
            let y = lo * 2.0;
            let x = hi + y;
            let yr = x - hi;
            if y == yr {
                hi = x;
            }
        }
    }
    hi
}

/// _mean: the samples' mean of `key`, to a tenth; None where none has it.
fn mean(samples: &[Dict], key: &str) -> Option<f64> {
    let values: Vec<f64> = samples.iter().filter_map(|s| number(s.get(key))).collect();
    if values.is_empty() {
        return None;
    }
    let fmean = fsum(&values) / values.len() as f64;
    Some(lp_py::round_digits(fmean, 1))
}

/// int(x or 0)
fn whole(value: Option<&Value>) -> i64 {
    match value {
        Some(Value::Int(n)) => n.to_string().parse().unwrap_or(0),
        Some(Value::Float(f)) => f.trunc() as i64,
        Some(Value::Bool(b)) => i64::from(*b),
        _ => 0,
    }
}

fn delta(first: &Dict, last: &Dict, keys: &[&str]) -> i64 {
    keys.iter()
        .map(|k| whole(last.get(*k)) - whole(first.get(*k)))
        .sum()
}

/// judge: one panel's numbers over a step, and the verdict.
fn judge(samples: &[Dict], first: &Dict, last: &Dict) -> Dict {
    let (fps, kbps, blocks) = (
        mean(samples, "fps"),
        mean(samples, "kbps"),
        mean(samples, "blocks_per_s"),
    );
    let render = mean(samples, "render_ms");
    let target = match last.get("target_fps") {
        Some(value) if value.truthy() => value.clone(),
        _ => first.get("target_fps").cloned().unwrap_or(Value::None),
    };
    let target_n = number(Some(&target)).filter(|_| target.truthy());
    let (waits, drops) = (
        delta(first, last, &["waits"]),
        delta(first, last, &["drops"]),
    );
    let faults = delta(
        first,
        last,
        &["usb_errors", "panel_restarts", "ffmpeg_restarts"],
    );
    let slow = matches!((fps, target_n), (Some(fps), Some(t)) if fps < 0.9 * t);
    let no_depth = matches!(last.get("max_depth"), None | Some(Value::None));
    let verdict = if samples.is_empty() || (no_depth && blocks.is_none()) {
        "no video"
    } else if blocks.unwrap_or(0.0) < 1.0
        || delta(first, last, &["panel_restarts"]) != 0
        || delta(first, last, &["ffmpeg_restarts"]) >= 2
    {
        "failed"
    } else if waits > 10 || drops > 5 {
        "stutter"
    } else if slow && matches!((render, target_n), (Some(r), Some(t)) if r > 0.8 * 1000.0 / t) {
        "pc"
    } else if slow {
        "stutter"
    } else {
        "ok"
    };
    let float = |x: Option<f64>| x.map_or(Value::None, Value::Float);
    let got = |key: &str| last.get(key).cloned().unwrap_or(Value::None);
    let mut told = Dict::new();
    told.insert("fps".into(), float(fps));
    told.insert("target_fps".into(), target);
    told.insert("render_ms".into(), float(render));
    told.insert("kbps".into(), float(kbps));
    told.insert("blocks_per_s".into(), float(blocks));
    told.insert("bit_rate".into(), got("bit_rate"));
    told.insert("encoder".into(), got("encoder"));
    told.insert("waits".into(), Value::int(waits));
    told.insert("drops".into(), Value::int(drops));
    told.insert("faults".into(), Value::int(faults));
    told.insert("verdict".into(), Value::Str(verdict.into()));
    told
}

/// What the run reaches of the app: its settings, as the app writes them, and every panel's
/// numbers from the running app's panel loop.
pub struct Context {
    pub home: Home,
    pub config_file: PathBuf,
    pub controls: Weak<BackgroundApp>,
    pub restart: Arc<Restart>,
}

impl Context {
    fn settings(&self) -> Settings<'_> {
        Settings {
            home: &self.home,
            config_file: &self.config_file,
            controls: self.controls.upgrade(),
            restart: &self.restart,
        }
    }

    fn write(&self, changes: &[(String, Value)]) -> Result<(), String> {
        let set: Dict = changes.iter().cloned().collect();
        let mut payload = Dict::new();
        payload.insert("set".into(), Value::Dict(set));
        (self.settings().write(&Value::Dict(payload)))
            .map(drop)
            .map_err(|refused| refused.message)
    }

    /// The video table as the settings serve it.
    fn video(&self) -> Result<Dict, String> {
        let read = self.settings().read().map_err(|refused| refused.message)?;
        let video = match &read {
            Value::Dict(read) => match read.get("config") {
                Some(Value::Dict(config)) => config.get("video").cloned(),
                _ => None,
            },
            _ => None,
        };
        match video {
            Some(Value::Dict(video)) => Ok(video),
            _ => Err("the settings have no video table".into()),
        }
    }

    /// Every panel's stats now, by its number (1 the first).
    fn snapshot(&self) -> IndexMap<usize, Dict> {
        let mut found = IndexMap::new();
        let Some(app) = self.controls.upgrade() else {
            return found;
        };
        let status = app.panel.status();
        let Ok(first) = status.lock() else {
            return found;
        };
        found.insert(1, first.stats.clone().unwrap_or_default());
        let mut more: Vec<(usize, Arc<Mutex<crate::mainloop::RunStatus>>)> = first
            .more
            .iter()
            .map(|(n, s)| (*n, Arc::clone(s)))
            .collect();
        drop(first);
        more.sort_by_key(|(n, _)| *n);
        for (n, each) in more {
            let stats = each.lock().ok().and_then(|each| each.stats.clone());
            found.insert(n + 2, stats.unwrap_or_default());
        }
        found
    }

    /// The graphics card's name (a nice-to-have: without it the card's step is left out).
    fn gpu(&self) -> String {
        let app = self.controls.upgrade();
        match app
            .and_then(|app| app.latest())
            .map(|s| s.value("gpu.name"))
        {
            Some(Value::Str(name)) => name,
            Some(Value::None) | None => String::new(),
            Some(other) => lp_py::format::str_of(&other),
        }
    }
}

#[derive(Default)]
struct Run {
    steps: Vec<Step>,
    results: Vec<Value>,
    before: Vec<(String, Value)>,
    running: bool,
    step: i64,
    left_s: f64,
    error: String,
    /// "done", "stopped" or "" while it runs or never ran
    ended: String,
}

/// Experiment: the run, `start`ed in a thread of its own, `stop`ped, its `state` for the app.
pub struct Experiment {
    context: Arc<Context>,
    run: Arc<Mutex<Run>>,
    stop: Arc<AtomicBool>,
}

fn say(message: &str) {
    crate::logs::log(
        crate::logs::Level::Info,
        "libre_panel.editor.experiment",
        message,
    );
}

impl Experiment {
    pub fn new(context: Context) -> Experiment {
        Experiment {
            context: Arc::new(context),
            run: Arc::new(Mutex::new(Run {
                step: -1,
                ..Run::default()
            })),
            stop: Arc::default(),
        }
    }

    pub fn start(&self) -> Result<(), String> {
        let mut run = self.run.lock().map_err(|_| "the experiment is stuck")?;
        if run.running {
            return Err("the experiment is running".into());
        }
        let video = self.context.video()?;
        run.before = measured()
            .into_iter()
            .filter_map(|(key, _)| {
                let name = key.split_once('.').map_or(key, |(_, name)| name);
                video
                    .get(name)
                    .map(|value| (key.to_string(), value.clone()))
            })
            .collect();
        run.steps = steps(gpu_vendor(&self.context.gpu()));
        (run.results, run.error, run.ended, run.step) =
            (Vec::new(), String::new(), String::new(), -1);
        self.stop.store(false, Ordering::Relaxed);
        run.running = true;
        let (context, shared, stop) = (
            Arc::clone(&self.context),
            Arc::clone(&self.run),
            Arc::clone(&self.stop),
        );
        let thread = std::thread::Builder::new().name("video-experiment".into());
        let spawned = thread.spawn(move || go(&context, &shared, &stop));
        if let Err(why) = spawned {
            run.running = false;
            return Err(why.to_string());
        }
        Ok(())
    }

    pub fn stop(&self) {
        self.stop.store(true, Ordering::Relaxed);
    }

    pub fn state(&self) -> Value {
        let run = self
            .run
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut told = Dict::new();
        told.insert("running".into(), Value::Bool(run.running));
        told.insert("step".into(), Value::int(run.step));
        told.insert("of".into(), Value::int(run.steps.len() as i64));
        let left = lp_py::round(run.left_s) as i64;
        told.insert("left_s".into(), Value::int(left));
        let steps = (run.steps.iter())
            .map(|step| {
                let mut each = Dict::new();
                each.insert("id".into(), Value::Str(step.id.clone()));
                each.insert("changes".into(), Value::Dict(changes_of(&step.changes)));
                each.insert("seconds".into(), Value::Float(step.seconds));
                Value::Dict(each)
            })
            .collect();
        told.insert("steps".into(), Value::List(steps));
        told.insert("results".into(), Value::List(run.results.clone()));
        told.insert("error".into(), Value::Str(run.error.clone()));
        told.insert("ended".into(), Value::Str(run.ended.clone()));
        Value::Dict(told)
    }
}

fn changes_of(changes: &[(&'static str, Value)]) -> Dict {
    changes
        .iter()
        .map(|(k, v)| ((*k).to_string(), v.clone()))
        .collect()
}

/// Waits `seconds`, or until stopped.
fn wait(stop: &AtomicBool, seconds: f64) {
    let until = Instant::now() + Duration::from_secs_f64(seconds);
    while !stop.load(Ordering::Relaxed) && Instant::now() < until {
        std::thread::sleep(Duration::from_millis(20));
    }
}

/// _run: each step's settings written, the panel let settle, the numbers measured; the
/// settings from before back at the end, whatever happened.
fn go(context: &Context, run: &Mutex<Run>, stop: &AtomicBool) {
    let steps = run.lock().map(|run| run.steps.clone()).unwrap_or_default();
    let mut failed = None;
    for (i, step) in steps.iter().enumerate() {
        if stop.load(Ordering::Relaxed) {
            break;
        }
        if let Ok(mut run) = run.lock() {
            run.step = i as i64;
        }
        let mut changes: Vec<(String, Value)> = Vec::new();
        for (key, value) in measured().into_iter().chain(step.changes.clone()) {
            match changes.iter_mut().find(|(k, _)| k == key) {
                Some(entry) => entry.1 = value,
                None => changes.push((key.to_string(), value)),
            }
        }
        if let Err(why) = context.write(&changes) {
            failed = Some(why);
            break;
        }
        wait(stop, SETTLE_S);
        let result = measure(context, run, stop, step);
        say(&format!("experiment: {}: {}", step.id, summary(&result)));
        if let Ok(mut run) = run.lock() {
            run.results.push(Value::Dict(result));
        }
    }
    let before = run.lock().map(|run| run.before.clone()).unwrap_or_default();
    let back = context.write(&before);
    if let Ok(mut run) = run.lock() {
        if let Some(why) = failed {
            run.error = why;
        }
        if let Err(why) = back {
            say(&format!(
                "the settings from before the experiment did not come back: {why}"
            ));
            if run.error.is_empty() {
                run.error = why;
            }
        }
        run.ended = match stop.load(Ordering::Relaxed) {
            true => "stopped".into(),
            false => "done".into(),
        };
        run.running = false;
    }
}

/// _measure: every panel's numbers over a step, judged.
fn measure(context: &Context, run: &Mutex<Run>, stop: &AtomicBool, step: &Step) -> Dict {
    let first = context.snapshot();
    let mut samples: IndexMap<usize, Vec<Dict>> = first.keys().map(|n| (*n, Vec::new())).collect();
    let began = Instant::now();
    while !stop.load(Ordering::Relaxed) {
        let gone = began.elapsed().as_secs_f64();
        if let Ok(mut run) = run.lock() {
            run.left_s = (step.seconds - gone).max(0.0);
        }
        if gone >= step.seconds {
            break;
        }
        wait(stop, SAMPLE_S);
        for (n, stats) in context.snapshot() {
            samples.entry(n).or_default().push(stats);
        }
    }
    let last = context.snapshot();
    let mut numbers: Vec<usize> = first.keys().chain(last.keys()).copied().collect();
    numbers.sort_unstable();
    numbers.dedup();
    let empty = Dict::new();
    let panels: Vec<Dict> = numbers
        .iter()
        .map(|n| {
            let mut each = Dict::new();
            each.insert("panel".into(), Value::int(*n as i64));
            let judged = judge(
                samples.get(n).map_or(&[][..], Vec::as_slice),
                first.get(n).unwrap_or(&empty),
                last.get(n).unwrap_or(&empty),
            );
            each.extend(judged);
            each
        })
        .collect();
    let verdict = (panels.iter())
        .filter_map(|p| p.get("verdict").and_then(Value::as_str))
        .max_by_key(|v| WORST.iter().position(|w| w == v))
        .unwrap_or("no video")
        .to_string();
    let mut result = Dict::new();
    result.insert("id".into(), Value::Str(step.id.clone()));
    result.insert("changes".into(), Value::Dict(changes_of(&step.changes)));
    let panels = panels.into_iter().map(Value::Dict).collect();
    result.insert("panels".into(), Value::List(panels));
    result.insert("verdict".into(), Value::Str(verdict));
    result
}

/// _summary: a step's line in the log.
fn summary(result: &Dict) -> String {
    let Some(Value::List(panels)) = result.get("panels") else {
        return "no panel".into();
    };
    let parts: Vec<String> = (panels.iter())
        .filter_map(|p| match p {
            Value::Dict(p) => Some(p),
            _ => None,
        })
        .map(|p| {
            let rate = |key: &str| match p.get(key).and_then(Value::to_f64) {
                Some(x) if !matches!(p.get(key), Some(Value::None)) => format!("{x:.0}"),
                _ => "-".into(),
            };
            let text = |key: &str| p.get(key).map_or("None".into(), lp_py::format::str_of);
            let encoder = match p.get("encoder") {
                Some(Value::Str(e)) if !e.is_empty() => e.clone(),
                _ => "encoder -".into(),
            };
            format!(
                "panel {} {} ({} fps, {} KB/s, cap {}, {encoder}, waited {}, dropped {}, faults {})",
                text("panel"),
                text("verdict"),
                rate("fps"),
                rate("kbps"),
                text("bit_rate"),
                text("waits"),
                text("drops"),
                text("faults")
            )
        })
        .collect();
    match parts.is_empty() {
        true => "no panel".into(),
        false => parts.join("; "),
    }
}
