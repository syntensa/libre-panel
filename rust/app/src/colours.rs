//! Settings → Panel → Check the colours, run by the app (editor/colours.py,
//! `/api/colour-check`): who converts the colours for a panel and why, and the A/B on that
//! panel (colour_show.rs): its test picture made two ways in turn for 30 s.
//!
//! The Rust app's video is made by Libre Panel's engine alone ("native, boxes": the changed
//! parts of the frames, converted into YUV as the processor does): there is no conversion by
//! a graphics card through ffmpeg to compare, so the check of devices/colour_check.py ends at
//! once, saying so, as Python's ends where it finds no ffmpeg.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, Weak};

use lp_devices::models::{find_model, PanelModel};
use lp_py::{Dict, Value};

use crate::background::BackgroundApp;

const DEFAULT_MODEL: &str = "turing-9.2-usb";
/// how often the A/B looks whether it is over
const TICK_S: f64 = 0.2;
/// what the check compares (devices/colour_check.py's ENCODERS)
const ENCODERS: [&str; 4] = ["h264_amf", "h264_nvenc", "h264_qsv", "libx264"];
const CARD_ENCODERS: [&str; 3] = ["h264_amf", "h264_nvenc", "h264_qsv"];
const FULL_RANGE_CARDS: [&str; 2] = ["h264_amf", "h264_qsv"];
/// why the check finds nothing to compare in this app
const NOTHING_TO_CHECK: &str = "Libre Panel's engine makes the video and converts its colours \
                                as the processor does: there is no graphics card's conversion \
                                to compare";

/// What a panel's loop says of it: its model, the size of its frames and the meter's numbers.
type Running = (String, Option<(i64, i64)>, Option<Dict>);

/// ColourError: what is refused, and its status.
pub struct Refused {
    pub message: String,
    pub status: u16,
}

fn refused(message: impl Into<String>, status: u16) -> Refused {
    Refused {
        message: message.into(),
        status,
    }
}

/// in_use_of: the encoder, its feed and whether the card converts, from the counters'
/// `encoder` ("h264_amf, rgb0", "h264_amf, rgb0, card", "native, boxes").
pub fn in_use_of(text: Option<&str>) -> Option<(String, String, bool)> {
    let parts: Vec<&str> = text.unwrap_or("").split(',').map(str::trim).collect();
    if parts.len() < 2 || parts[0].is_empty() || !["rgb0", "rgb24", "boxes"].contains(&parts[1]) {
        return None;
    }
    Some((
        parts[0].to_string(),
        parts[1].to_string(),
        parts.get(2) == Some(&"card"),
    ))
}

/// card_choice.can: whether the card can convert for `encoder` at all.
fn can(encoder: &str, full: bool) -> bool {
    CARD_ENCODERS.contains(&encoder) && (!full || FULL_RANGE_CARDS.contains(&encoder))
}

/// why_of: why the card converts for `encoder` or not (no verdict is kept in this app).
fn why_of(encoder: &str, pixels: &str, full: bool) -> &'static str {
    if !CARD_ENCODERS.contains(&encoder) {
        return "x264";
    }
    if !can(encoder, full) {
        return "limited-only";
    }
    match pixels {
        "card" => "card",
        "rgb" => "rgb",
        _ => "unchecked",
    }
}

/// way_of: who converts for that reason.
fn way_of(why: &str, pixels: &str) -> &'static str {
    if matches!(why, "card" | "identical") {
        "card"
    } else if pixels == "rgb" {
        "rgb"
    } else {
        "processor"
    }
}

fn text(t: &str) -> Value {
    Value::Str(t.to_string())
}

fn dict(pairs: Vec<(&str, Value)>) -> Value {
    Value::Dict(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

/// The check's state (it ends at once in this app) and the A/B's.
#[derive(Default)]
struct Kept {
    error: String,
    ended: String,
    picture: Dict,
    show_error: String,
    show_ended: String,
    show_kind: String,
    show_panel: i64,
}

/// ColourCheck: the check and the A/B for the app.
pub struct ColourCheck {
    config_file: std::path::PathBuf,
    controls: Weak<BackgroundApp>,
    kept: Arc<Mutex<Kept>>,
    show_stop: Mutex<Arc<AtomicBool>>,
    show_thread: Mutex<Option<std::thread::JoinHandle<()>>>,
    lock: Mutex<()>,
}

impl ColourCheck {
    pub fn new(config_file: std::path::PathBuf, controls: Weak<BackgroundApp>) -> ColourCheck {
        ColourCheck {
            config_file,
            controls,
            kept: Arc::default(),
            show_stop: Mutex::default(),
            show_thread: Mutex::default(),
            lock: Mutex::default(),
        }
    }

    fn kept(&self) -> std::sync::MutexGuard<'_, Kept> {
        self.kept.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// _config: the settings of panel `number` (1 the first).
    fn config(&self, number: &Value) -> Result<(lp_config::Config, i64), Refused> {
        let config = lp_config::load_config(&self.config_file, lp_config::platform())
            .map_err(|why| refused(why.msg, 409))?;
        let Value::Int(n) = number else {
            return Err(refused("panel: expected a whole number", 400));
        };
        let count = 1 + config.panels.len() as i64;
        let n = match i64::try_from(n.clone()) {
            Ok(n) if (1..=count).contains(&n) => n,
            _ => return Err(refused(format!("panel {n}: there is no such panel"), 400)),
        };
        let config = if n == 1 {
            config
        } else {
            config
                .for_panel(n as usize - 2)
                .ok_or_else(|| refused(format!("panel {n}: there is no such panel"), 400))?
        };
        Ok((config, n))
    }

    /// _status: the model, the size and the meter's numbers of panel `number` as it runs.
    fn status(&self, number: i64) -> Option<Running> {
        let app = self.controls.upgrade()?;
        let status = app.panel.status();
        let first = status.lock().ok()?;
        if number == 1 {
            return Some((first.model.clone(), first.size, first.stats.clone()));
        }
        let more = Arc::clone(first.more.get(&((number - 2) as usize))?);
        drop(first);
        let each = more.lock().ok()?;
        Some((each.model.clone(), each.size, each.stats.clone()))
    }

    fn full(config: &lp_config::Config, model: Option<&PanelModel>) -> bool {
        lp_devices::turzx::full_range(&config.device.video_range, model)
    }

    /// _model: the model of panel `number` as it runs, else as Settings name it, else the one
    /// the last check was made for, else the check's own default.
    fn model(&self, number: i64, config: &lp_config::Config) -> Option<&'static PanelModel> {
        let running = self.status(number).map(|s| s.0).unwrap_or_default();
        let found = find_model(&running).or_else(|| find_model(&config.device.model));
        let found = found.or_else(|| {
            let kept = self.kept();
            match (kept.picture.get("panel"), kept.picture.get("model")) {
                (Some(Value::Int(n)), Some(model)) if *n == number.into() => {
                    find_model(&lp_py::format::str_of(model))
                }
                _ => None,
            }
        });
        found.or_else(|| find_model(DEFAULT_MODEL))
    }

    /// _picture: the model, the frame size and the range of panel `number`.
    fn picture(&self, number: i64, config: &lp_config::Config) -> Dict {
        let status = self.status(number);
        let mut model = status.as_ref().and_then(|s| find_model(&s.0));
        if model.is_none() && number == 1 {
            model = lp_devices::usb::present_model(&config.device.beside, 0);
        }
        let model = model
            .or_else(|| find_model(&config.device.model))
            .or_else(|| find_model(DEFAULT_MODEL));
        let way = match config.device.orientation.as_str() {
            "" => "landscape",
            given => given,
        };
        let size = status
            .and_then(|s| s.1)
            .or_else(|| model.map(|m| m.size(way)))
            .unwrap_or((0, 0));
        let mut found = Dict::new();
        found.insert("panel".into(), Value::Int(number.into()));
        found.insert("model".into(), model.map_or(Value::None, |m| text(m.id)));
        found.insert(
            "size".into(),
            Value::List(vec![Value::Int(size.0.into()), Value::Int(size.1.into())]),
        );
        found.insert("full".into(), Value::Bool(Self::full(config, model)));
        found
    }

    /// _running: the encoder panel `number` runs, its feed and whether the card converts:
    /// its counters' while it streams video, else None.
    fn running(&self, number: i64) -> Option<(String, String, bool)> {
        let stats = self.status(number)?.2?;
        if !stats.get("streaming").is_some_and(Value::truthy) {
            return None;
        }
        match stats.get("encoder") {
            Some(Value::Str(encoder)) => in_use_of(Some(encoder)),
            _ => None,
        }
    }

    /// start_check: the check begun with panel `number`'s settings, size and range; in this
    /// app it ends at once, saying why there is nothing to compare.
    pub fn start_check(&self, number: &Value) -> Result<(), Refused> {
        let _one = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        let (config, n) = self.config(number)?;
        let picture = self.picture(n, &config);
        let mut kept = self.kept();
        kept.picture = picture;
        kept.error = NOTHING_TO_CHECK.to_string();
        kept.ended = "done".into();
        Ok(())
    }

    /// start_show: panel `number`'s test picture, A and B of `kind` in turn: "feed", the
    /// card's conversion against the processor's (a card's encoder that can only); "range",
    /// limited against full range.
    pub fn start_show(&self, number: &Value, kind: &str) -> Result<(), Refused> {
        let _one = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        if self.controls.upgrade().is_none() {
            return Err(refused(
                "showing it on the panel needs the running app",
                409,
            ));
        }
        if !crate::colour_show::WAYS
            .iter()
            .any(|(name, _)| *name == kind)
        {
            return Err(refused("kind: one of feed, range", 400));
        }
        let (config, n) = self.config(number)?;
        let mut thread = self.show_thread.lock().unwrap_or_else(|e| e.into_inner());
        if thread.as_ref().is_some_and(|t| !t.is_finished()) {
            return Err(refused("a panel shows the test picture already", 409));
        }
        let Some(running) = self.running(n) else {
            return Err(refused(format!("panel {n} shows no video now"), 409));
        };
        let full = Self::full(&config, self.model(n, &config));
        if kind == "feed" && !can(&running.0, full) {
            return Err(refused(
                format!("panel {n} runs {}: the processor converts", running.0),
                409,
            ));
        }
        {
            let mut kept = self.kept();
            kept.show_error.clear();
            kept.show_ended.clear();
            kept.show_kind = kind.to_string();
            kept.show_panel = n;
        }
        if let Err(why) = crate::colour_show::begin(n, None, kind) {
            crate::colour_show::end();
            return Err(refused(
                format!("the test picture did not go to panel {n}: {why}"),
                409,
            ));
        }
        let stop = Arc::new(AtomicBool::new(false));
        *self.show_stop.lock().unwrap_or_else(|e| e.into_inner()) = Arc::clone(&stop);
        let kept = Arc::clone(&self.kept);
        *thread = Some(
            std::thread::Builder::new()
                .name("colour-show".into())
                .spawn(move || {
                    // until the A/B's time is over, or it is stopped
                    while crate::colour_show::state().is_some() && !stop.load(Ordering::Relaxed) {
                        std::thread::sleep(std::time::Duration::from_secs_f64(TICK_S));
                    }
                    crate::colour_show::end();
                    let mut kept = kept.lock().unwrap_or_else(|e| e.into_inner());
                    kept.show_ended = if stop.load(Ordering::Relaxed) {
                        "stopped".into()
                    } else {
                        "done".into()
                    };
                })
                .map_err(|e| refused(e.to_string(), 409))?,
        );
        Ok(())
    }

    pub fn stop_show(&self) {
        let stop = self.show_stop.lock().unwrap_or_else(|e| e.into_inner());
        stop.store(true, Ordering::Relaxed);
    }

    /// close: the app ends: the panel shows its theme (and the marker goes).
    pub fn close(&self) {
        self.stop_show();
        let thread = self.show_thread.lock().ok().and_then(|mut t| t.take());
        if let Some(thread) = thread {
            let _ = thread.join();
        }
    }

    /// auto: what the video starts: Libre Panel's engine, where it is here.
    fn auto(video: &lp_config::VideoConfig) -> Value {
        let picked = lp_devices::engine::find_engine().map_or(Value::None, |_| text("native"));
        dict(vec![
            ("set", text(&video.encoder)),
            ("picked", picked),
            ("passed", Value::List(Vec::new())),
        ])
    }

    /// choice: who converts the colours for `encoder` on panel `number` and why.
    fn choice(&self, number: i64, config: &lp_config::Config, encoder: Option<&str>) -> Dict {
        let video = &config.device.video;
        let model = self.model(number, config);
        let full = Self::full(config, model);
        let mut found = Dict::new();
        for (key, value) in [
            ("encoder", encoder.map_or(Value::None, text)),
            ("pixels", text(&video.pixels)),
            ("full", Value::Bool(full)),
            ("range", text(&config.device.video_range)),
            (
                "model_range",
                text(model.map_or("limited", |m| m.video_range)),
            ),
            ("way", Value::None),
            ("why", text("")),
            ("verdict", Value::None),
            ("worst", Value::None),
            ("when", Value::None),
            ("by", Value::None),
            ("checking", Value::Bool(false)),
        ] {
            found.insert(key.into(), value);
        }
        if let Some(encoder) = encoder.filter(|e| !e.is_empty()) {
            let why = why_of(encoder, &video.pixels, full);
            found.insert("why".into(), text(why));
            found.insert("way".into(), text(way_of(why, &video.pixels)));
        }
        found
    }

    /// state: the colour check as it stands for panel `number`, what it runs and feeds, what
    /// to do about it, and the A/B.
    pub fn state(&self, number: &Value) -> Result<Value, Refused> {
        let (config, n) = self.config(number)?;
        let video = &config.device.video;
        let running = self.running(n);
        let auto = Self::auto(video);
        let picked = match &auto {
            Value::Dict(auto) => match auto.get("picked") {
                Some(Value::Str(picked)) => Some(picked.clone()),
                _ => None,
            },
            _ => None,
        };
        let encoder = running.as_ref().map(|r| r.0.clone()).or(picked);
        let choice = self.choice(n, &config, encoder.as_deref());
        let way = match choice.get("way") {
            Some(Value::Str(way)) => way.clone(),
            _ => String::new(),
        };
        let in_use = match (&running, &encoder) {
            (Some((encoder, feed, card)), _) => dict(vec![
                ("encoder", text(encoder)),
                ("feed", text(feed)),
                ("card", Value::Bool(*card)),
                ("running", Value::Bool(true)),
            ]),
            (None, Some(encoder)) if !encoder.is_empty() => dict(vec![
                ("encoder", text(encoder)),
                ("feed", text(if way == "rgb" { "rgb24" } else { "rgb0" })),
                ("card", Value::Bool(way == "card")),
                ("running", Value::Bool(false)),
            ]),
            _ => Value::None,
        };
        let why = match choice.get("why") {
            Some(Value::Str(why)) => why.clone(),
            _ => String::new(),
        };
        let app = self.controls.upgrade().is_some();
        let ab = if !app {
            "app".to_string()
        } else if running.is_none() {
            "video".to_string()
        } else if why == "x264" || why == "limited-only" {
            why.clone()
        } else {
            String::new()
        };
        let showing = crate::colour_show::state();
        let alive = (self.show_thread.lock().ok())
            .is_some_and(|t| t.as_ref().is_some_and(|t| !t.is_finished()));
        let kept = self.kept();
        let check = dict(vec![
            ("running", Value::Bool(false)),
            ("results", Value::List(Vec::new())),
            ("error", text(&kept.error)),
            ("ended", text(&kept.ended)),
            ("picture", Value::Dict(kept.picture.clone())),
            ("saving_ms", Value::None),
            (
                "encoders",
                Value::List(ENCODERS.iter().map(|e| text(e)).collect()),
            ),
        ]);
        let show = dict(vec![
            ("running", Value::Bool(alive)),
            (
                "panel",
                showing
                    .as_ref()
                    .map_or(Value::None, |s| Value::Int(s.panel.into())),
            ),
            (
                "kind",
                text(showing.as_ref().map_or(kept.show_kind.as_str(), |s| s.kind)),
            ),
            ("of", Value::Int(kept.show_panel.into())),
            (
                "letter",
                showing.as_ref().map_or(Value::None, |s| text(s.letter)),
            ),
            (
                "value",
                showing.as_ref().map_or(Value::None, |s| text(s.value)),
            ),
            (
                "left_s",
                Value::Int(
                    showing
                        .as_ref()
                        .map_or(0, |s| s.left_s.round_ties_even() as i64)
                        .into(),
                ),
            ),
            ("seconds", Value::Float(crate::colour_show::SHOW_S)),
            ("switch_s", Value::Float(crate::colour_show::SWITCH_S)),
            ("error", text(&kept.show_error)),
            ("ended", text(&kept.show_ended)),
        ]);
        Ok(dict(vec![
            ("panel", Value::Int(n.into())),
            ("app", Value::Bool(app)),
            ("video", Value::Bool(running.is_some())),
            ("ab", Value::Str(ab)),
            ("pixels", text(&video.pixels)),
            ("auto", auto),
            ("in_use", in_use),
            ("choice", Value::Dict(choice)),
            ("check", check),
            ("show", show),
        ]))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_encoder_in_use_is_read_as_colours_py_reads_it() {
        assert_eq!(
            in_use_of(Some("h264_amf, rgb0, card")),
            Some(("h264_amf".into(), "rgb0".into(), true))
        );
        assert_eq!(
            in_use_of(Some("native, boxes")),
            Some(("native".into(), "boxes".into(), false))
        );
        assert_eq!(in_use_of(None), None);
        assert_eq!(in_use_of(Some("libx264")), None);
        assert_eq!(why_of("native", "auto", true), "x264");
        assert_eq!(why_of("h264_nvenc", "card", true), "limited-only");
        assert_eq!(way_of("x264", "rgb"), "rgb");
    }
}
