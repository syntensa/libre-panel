//! A timer on the panel: so many minutes counted down, and a notice when they are up
//! (services/timer.py). With `break_minutes`, focus and break take turns (the Pomodoro way):
//! when the focus time is up the break begins by itself, and after the break the next focus
//! waits to be started.
//!
//! A keyboard shortcut starts and pauses it (`[hotkeys] timer`), another sets it back
//! (`timer_reset`). Readings for a theme: `timer.left` (seconds), `timer.text` ("24:13"),
//! `timer.running` (1 or 0), `timer.share` (how much of the time has passed, 0 to 1),
//! `timer.phase` ("Timer", or "Focus" and "Break") and `timer.rounds` (the focus times
//! finished since the service started).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use lp_py::{round_digits, Dict, Value};
use lp_render::overlays::Toast;

use crate::host::{Event, ServiceHost};
use crate::manager::{Part, Schema, Service};

const TICK_S: f64 = 0.2;
/// the shortcuts' actions
const TOGGLE: &str = "timer";
const RESET: &str = "timer_reset";

/// clock_text: `24:13`; `1:02:05` from an hour on. A second begun counts (`0:01` until the
/// end).
pub fn clock_text(seconds: f64) -> String {
    let whole = if seconds.is_finite() {
        seconds.ceil().max(0.0) as i64
    } else {
        0
    };
    let (hours, rest) = (whole / 3600, whole % 3600);
    let (minutes, secs) = (rest / 60, rest % 60);
    match hours {
        0 => format!("{minutes:02}:{secs:02}"),
        _ => format!("{hours}:{minutes:02}:{secs:02}"),
    }
}

/// The timer itself: how it stands, and what a start, a pause and the passing time do to it.
#[derive(Clone, Debug, PartialEq)]
pub struct Core {
    focus_s: f64,
    break_s: f64,
    on_break: bool,
    left: f64,
    /// while it runs: when it ends, on the clock
    ends: Option<f64>,
    rounds: i64,
}

impl Core {
    pub fn new(minutes: f64, break_minutes: f64) -> Core {
        let focus_s = (minutes * 60.0).max(1.0);
        Core {
            focus_s,
            break_s: (break_minutes * 60.0).max(0.0),
            on_break: false,
            left: focus_s,
            ends: None,
            rounds: 0,
        }
    }

    fn length(&self) -> f64 {
        match self.on_break {
            true => self.break_s,
            false => self.focus_s,
        }
    }

    /// Start, or pause where it is.
    pub fn toggle(&mut self, now: f64) {
        match self.ends {
            None => {
                if self.left <= 0.0 {
                    self.left = self.length();
                }
                self.ends = Some(now + self.left);
            }
            Some(ends) => {
                self.left = (ends - now).max(0.0);
                self.ends = None;
            }
        }
    }

    /// Back to the start of a focus time, stopped.
    pub fn reset(&mut self) {
        self.on_break = false;
        self.left = self.focus_s;
        self.ends = None;
    }

    /// The time passes to `now`: what to say where it is up (to be put in the panel's
    /// language), and what comes next.
    pub fn tick(&mut self, now: f64) -> Option<&'static str> {
        let ends = self.ends?;
        self.left = (ends - now).max(0.0);
        if self.left > 0.0 {
            return None;
        }
        if !self.on_break {
            self.rounds += 1;
            if self.break_s > 0.0 {
                // the break begins by itself
                self.on_break = true;
                self.left = self.break_s;
                self.ends = Some(now + self.break_s);
                return Some("Time for a break");
            }
            self.ends = None;
            return Some("Time is up");
        }
        // the next focus waits
        self.on_break = false;
        self.left = self.focus_s;
        self.ends = None;
        Some("The break is over")
    }

    /// Its readings: key, value, unit, label.
    pub fn readings(&self) -> Vec<(&'static str, Value, &'static str, &'static str)> {
        let length = self.length();
        let name = if self.break_s <= 0.0 {
            "Timer"
        } else if self.on_break {
            "Break"
        } else {
            "Focus"
        };
        let share = match length > 0.0 {
            true => 1.0 - self.left / length,
            false => 1.0,
        };
        let share = round_digits(share.clamp(0.0, 1.0), 4);
        let running = i64::from(self.ends.is_some());
        vec![
            (
                "timer.left",
                Value::Float(round_digits(self.left, 1)),
                "s",
                "Time left",
            ),
            (
                "timer.text",
                Value::Str(clock_text(self.left)),
                "",
                "Time left",
            ),
            ("timer.running", Value::int(running), "", "Timer runs"),
            ("timer.share", Value::Float(share), "", "Time passed"),
            ("timer.phase", Value::Str(lp_i18n::t(name)), "", "Timer"),
            ("timer.rounds", Value::int(self.rounds), "", "Rounds"),
        ]
    }
}

struct Shared {
    core: Mutex<Core>,
    host: ServiceHost,
    notice: bool,
    began: Instant,
}

impl Shared {
    fn publish(&self) {
        let readings = match self.core.lock() {
            Ok(core) => core.readings(),
            Err(_) => return,
        };
        for (key, value, unit, label) in readings {
            self.host.publish(key, value, unit, label);
        }
    }

    fn change(&self, work: impl FnOnce(&mut Core, f64) -> Option<&'static str>) {
        let told = match self.core.lock() {
            Ok(mut core) => work(&mut core, self.began.elapsed().as_secs_f64()),
            Err(_) => None,
        };
        self.publish();
        if let (Some(told), true) = (told, self.notice) {
            let mut payload = Dict::default();
            payload.insert("label".into(), Value::Str(lp_i18n::t("Timer")));
            self.host.notify(Toast {
                icon: Some("hourglass".into()),
                seconds: Some(8.0),
                kind: "timer".into(),
                rank: 2,
                payload,
                key: "timer".into(),
                ..Toast::new(lp_i18n::t(told))
            });
        }
    }
}

/// Minutes counted down, started and paused by a shortcut or in the app (Timer).
pub struct Timer {
    shared: Arc<Shared>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

fn options() -> Schema {
    vec![
        ("minutes", "number", Value::Float(25.0)),
        // 0: no break, a plain timer
        ("break_minutes", "number", Value::Float(0.0)),
        ("notice", "bool", Value::Bool(true)),
    ]
}

/// The timer as a kind of service.
pub fn part() -> Part {
    Part {
        options,
        make: |host, options| {
            let number = |key: &str| options.get(key).and_then(Value::to_f64).unwrap_or(0.0);
            Ok(Box::new(Timer {
                shared: Arc::new(Shared {
                    core: Mutex::new(Core::new(number("minutes"), number("break_minutes"))),
                    host,
                    notice: options.get("notice").is_none_or(Value::truthy),
                    began: Instant::now(),
                }),
                stop: Arc::default(),
                thread: None,
            }))
        },
    }
}

impl Service for Timer {
    fn start(&mut self) -> Result<(), String> {
        let shared = Arc::clone(&self.shared);
        self.shared.host.on(
            "shortcut",
            Box::new(move |event| match event {
                Event::Shortcut { action } if action == TOGGLE => {
                    shared.change(|core, now| {
                        core.toggle(now);
                        None
                    });
                }
                Event::Shortcut { action } if action == RESET => {
                    shared.change(|core, _| {
                        core.reset();
                        None
                    });
                }
                _ => {}
            }),
        )?;
        self.shared.publish();
        let (shared, stop) = (Arc::clone(&self.shared), Arc::clone(&self.stop));
        let run = move || {
            while !stop.load(Ordering::Relaxed) {
                std::thread::sleep(Duration::from_secs_f64(TICK_S));
                shared.change(Core::tick);
            }
        };
        self.thread = std::thread::Builder::new()
            .name("service-timer".into())
            .spawn(run)
            .ok();
        Ok(())
    }

    fn stop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }

    /// For the app: how it stands now.
    fn summary(&self) -> Option<Value> {
        let core = self.shared.core.lock().ok()?;
        let mut state = Dict::default();
        state.insert("left".into(), Value::Float(round_digits(core.left, 1)));
        state.insert("running".into(), Value::Bool(core.ends.is_some()));
        let phase = if core.on_break { "break" } else { "focus" };
        state.insert("phase".into(), Value::Str(phase.into()));
        state.insert("rounds".into(), Value::int(core.rounds));
        Some(Value::Dict(state))
    }
}
