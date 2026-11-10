//! The panel's loop (app.py's MainLoop, as far as this build has it): config.toml followed as
//! it changes, the theme that is due (the screens in turn, the one config.toml names, the
//! panel's own) in its version for the panel and dressed as the person chose, the readings
//! taken at the theme's pace, a frame drawn of them and shown, at the frame rate while
//! something moves and at rest otherwise.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use indexmap::IndexMap;
use lp_config::sequence::Rotation;
use lp_config::Config;
use lp_devices::models::{find_model, PanelModel};
use lp_pixels::{Filter, Image, Transpose};
use lp_py::Value;
use lp_render::{Moment, Options, Renderer, Snapshot};
use lp_theme::versions::{self, Places};
use lp_theme::{Env, Theme};

use crate::display::{Display, Failed};
use crate::home::Home;
use crate::sensors::Hub;

/// a panel that is missing or gone is tried again after so long, longer each time (_Link)
const BACKOFF_S: [f64; 5] = [1.0, 2.0, 5.0, 10.0, 30.0];
/// ... and one that restarts to clear its hung decoder, after so long
const RESTART_RETRY_S: f64 = 1.0;
/// config.toml is looked at this often
const WATCH_S: f64 = 1.0;

/// What the loop was started with (the command line's).
#[derive(Clone, Debug, Default)]
pub struct Asked {
    /// config.toml (None: the settings folder's)
    pub config: Option<PathBuf>,
    /// a theme shown instead of config.toml's
    pub theme: Option<String>,
    /// readings made up instead of the computer's
    pub demo: bool,
    /// frames written to this PNG file instead of the configured panel
    pub output: Option<PathBuf>,
    /// so many frames, then the loop ends (None: until it is stopped)
    pub frames: Option<u64>,
}

pub struct MainLoop {
    home: Home,
    env: Env,
    places: Places,
    asked: Asked,
    config: Config,
    config_file: PathBuf,
    watched: f64,
    hub: Hub,
    panel: Option<Display>,
    /// how often the panel failed in a row, and when it is tried again
    failures: u32,
    retry_at: f64,
    /// a panel was in use: one that is gone is waited for (`driver = "auto"`)
    had_panel: bool,
    renderer: Option<Renderer>,
    /// the name of the theme the renderer draws
    shown: String,
    /// a theme that could not be shown, and why: not tried again until something changes
    refused: Option<(String, String)>,
    rotation: Rotation,
    snapshot: Option<Snapshot>,
    next_sample: f64,
    brightness: Option<i64>,
    began: Instant,
    pub frames: u64,
}

fn say(message: &str) {
    eprintln!("libre-panel: {message}");
}

/// accent_of: the colour config.toml's `accent` stands for: None for the themes' own (and for
/// the system's, which this build does not read yet).
fn accent_of(choice: &str) -> Option<String> {
    let hex = choice.len() == 7
        && choice.starts_with('#')
        && choice[1..].chars().all(|c| c.is_ascii_hexdigit());
    hex.then(|| choice.to_ascii_uppercase())
}

/// The local time of now, and its fold.
fn moment() -> (Moment, bool) {
    let since = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64());
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    lp_py::zones::fromtimestamp(zone, since).unwrap_or((Moment::date(1970, 1, 1), false))
}

impl MainLoop {
    pub fn new(home: Home, asked: Asked) -> Result<MainLoop, String> {
        let config_file = asked.config.clone().unwrap_or_else(|| home.config_file());
        let config =
            lp_config::load_config(&config_file, lp_config::platform()).map_err(|e| e.msg)?;
        let (hub, missing) = Hub::of(&config.sensors, asked.demo);
        say(&format!("readings from: {}", hub.names().join(", ")));
        if !missing.is_empty() {
            say(&format!(
                "sensor sources not in this build yet: {}",
                missing.join(", ")
            ));
        }
        let made = MainLoop {
            env: Env {
                fonts_dir: home.fonts(),
            },
            places: home.places(),
            home,
            asked,
            config,
            config_file,
            watched: 0.0,
            hub,
            panel: None,
            failures: 0,
            retry_at: 0.0,
            had_panel: false,
            renderer: None,
            shown: String::new(),
            refused: None,
            rotation: Rotation::default(),
            snapshot: None,
            next_sample: 0.0,
            brightness: None,
            began: Instant::now(),
            frames: 0,
        };
        made.speak();
        Ok(made)
    }

    /// The loop's clock: seconds since it began.
    fn clock(&self) -> f64 {
        self.began.elapsed().as_secs_f64()
    }

    /// The app's language as config.toml sets it ("auto": the system's).
    fn speak(&self) {
        let env = |name: &str| std::env::var(name).unwrap_or_default();
        let code = lp_i18n::resolve(Some(&self.config.language), || {
            lp_i18n::system_language(&env, None)
        });
        match lp_i18n::Language::load(&self.home.locale(), &code) {
            Ok(language) => lp_i18n::set_language(language),
            Err(why) => say(&format!("language {code}: {why}")),
        }
    }

    /// config.toml read again where it changed; one that cannot be read leaves the last.
    fn follow_config(&mut self, now: f64) {
        if now - self.watched < WATCH_S {
            return;
        }
        self.watched = now;
        let stamp = std::fs::metadata(&self.config_file)
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_nanos() as i128);
        if stamp == self.config.stamp {
            return;
        }
        match lp_config::load_config(&self.config_file, lp_config::platform()) {
            Ok(fresh) => {
                if fresh.device != self.config.device {
                    self.close_panel();
                }
                if fresh.sensors != self.config.sensors {
                    let (hub, _) = Hub::of(&fresh.sensors, self.asked.demo);
                    self.hub = hub;
                    if let Some(renderer) = &self.renderer {
                        self.hub.want(renderer.reads());
                    }
                }
                self.config = fresh;
                self.renderer = None;
                self.refused = None;
                self.speak();
                say("config.toml changed: read again");
            }
            Err(e) => {
                // (the stamp kept, so that the same mistake is said once)
                self.config.stamp = stamp;
                say(&format!("config.toml: {}", e.msg));
            }
        }
    }

    fn close_panel(&mut self) {
        if let Some(mut panel) = self.panel.take() {
            panel.close();
            for note in panel.notes() {
                say(&note); // (its last words: what it sent)
            }
        }
        self.brightness = None;
    }

    /// The panel opened, where it is not; tried again a while after it failed.
    fn ensure_panel(&mut self, now: f64) -> bool {
        if self.panel.is_some() {
            return true;
        }
        if now < self.retry_at {
            return false;
        }
        let device = &self.config.device;
        let (moment, _) = moment();
        let level = lp_config::night::brightness_now(device, &moment, false, None);
        let opened = match &self.asked.output {
            Some(path) => Ok(Display::file(device, path.clone())),
            None => Display::open(device, &self.home.settings, level, self.had_panel, now),
        };
        let opened = opened.and_then(|mut panel| match panel.set_brightness(level) {
            Ok(()) => Ok(panel),
            Err(why) => {
                panel.close();
                Err(why)
            }
        });
        match opened {
            Ok(mut panel) => {
                for note in panel.notes() {
                    say(&note);
                }
                if self.failures > 0 {
                    say("panel connected");
                }
                say(&format!("showing on {}", panel.describe()));
                self.failures = 0;
                self.had_panel |= panel.on_panel();
                self.panel = Some(panel);
                self.brightness = Some(level);
                self.renderer = None; // (its size may be the panel's)
                true
            }
            Err(why) => {
                self.failed(&why, now);
                false
            }
        }
    }

    /// _Link._failed: a panel that is missing or gone is tried again with growing pauses (one
    /// that restarts to clear its hung decoder: soon), said once.
    fn failed(&mut self, why: &str, now: f64) {
        if self.failures == 0 {
            say(&format!(
                "panel not available: {why} (retrying in the background)"
            ));
        }
        let step = (self.failures as usize).min(BACKOFF_S.len() - 1);
        let delay = if lp_devices::turzx::restart_under_way() {
            RESTART_RETRY_S
        } else {
            BACKOFF_S[step]
        };
        self.failures += 1;
        self.retry_at = now + delay;
    }

    /// panel_model: the configured model, else the one the panel that is there is.
    fn model(&self) -> Option<&'static PanelModel> {
        find_model(&self.config.device.model)
            .or_else(|| self.panel.as_ref().and_then(Display::model))
    }

    /// panel_size: the size of the panel's frames, turned as it is mounted.
    fn panel_size(&self) -> Option<(i64, i64)> {
        let orientation = match self.config.device.orientation.as_str() {
            "portrait" => "portrait",
            _ => "landscape",
        };
        self.model().map(|model| model.size(orientation))
    }

    /// _theme_name: the theme asked for on the command line, else the step of the screens in
    /// turn that is due, else config.toml's; where that names none, the panel's own. In its
    /// version for the panel.
    fn theme_name(&mut self, now: f64) -> String {
        let named = if let Some(theme) = &self.asked.theme {
            theme.clone()
        } else if let Some(place) = self
            .config
            .sequence
            .on
            .then(|| self.rotation.at(&self.config.sequence.steps, now))
            .flatten()
        {
            place.theme
        } else if self.config.theme_given {
            self.config.theme.clone()
        } else {
            lp_theme::defaults::default_theme(self.model(), &self.places).to_string()
        };
        let orientation = &self.config.device.orientation;
        let shown = versions::theme_for(&named, orientation, self.panel_size(), &self.places);
        versions::unpaired_version(&shown, &self.places).unwrap_or(shown)
    }

    /// _dressed: the theme as it is drawn: in the accent, in light colours where asked, its
    /// cards as see-through as asked, with the person's cards in its card slots.
    fn dressed(&self, theme: &Theme) -> Result<Theme, String> {
        let config = &self.config;
        let accent = accent_of(&config.accent);
        let dressed = versions::with_accent(theme, accent.as_deref());
        let dressed =
            versions::with_light_colours(&dressed, config.light_colours).map_err(|e| e.msg)?;
        let dressed = versions::with_card_opacity(&dressed, config.card_opacity);
        Ok(lp_theme::modules::slots::with_cards(
            &dressed,
            &config.cards,
            &config.card_order,
            &config.card_transition,
        )
        .unwrap_or(dressed))
    }

    /// _renderer: the renderer of the theme `name`, laid out on the panel's frames and dressed.
    fn renderer_of(&self, name: &str) -> Result<Renderer, String> {
        let folder = versions::find_theme(name, &self.places).map_err(|e| e.msg)?;
        let theme = lp_theme::load_theme(&folder, &self.env).map_err(|e| e.msg)?;
        let theme = match self.model() {
            Some(model) => lp_theme::adapt::laid_out(
                &theme,
                Some(model.size(theme.orientation())),
                model.id,
                &self.places.builtin,
                &self.env,
            )
            .map_err(|e| e.msg)?,
            None => theme,
        };
        let config = &self.config;
        let chosen: lp_py::Dict = config
            .switches
            .iter()
            .map(|(theme, switches)| {
                let table = switches
                    .iter()
                    .map(|(name, on)| (name.clone(), Value::Bool(*on)))
                    .collect();
                (theme.clone(), Value::Dict(table))
            })
            .collect();
        let switches: IndexMap<String, bool> = versions::switches_for(&theme, &chosen)
            .into_iter()
            .filter_map(|(name, on)| match on {
                Value::Bool(on) => Some((name, on)),
                _ => None,
            })
            .collect();
        let options = Options {
            preview: false,
            animate: config.fps > 1,
            dither: false,
        };
        let mut renderer =
            Renderer::new(self.dressed(&theme)?, &self.env, options).map_err(|e| e.0)?;
        renderer.place = config.weather.latitude.zip(config.weather.longitude);
        let hardware = &config.hardware;
        renderer.fan_choices = lp_render::fan::Choices {
            look: hardware.fan_look.clone(),
            paint: hardware.fan_paint.clone(),
            looks: hardware.fan_looks.clone().into_iter().collect(),
            paints: hardware.fan_paints.clone().into_iter().collect(),
        };
        renderer.symbols = config.symbols.clone();
        renderer.switches = switches;
        let streams = self.panel.as_ref().is_some_and(Display::streams);
        renderer.fps = if streams {
            config.device.video.fps as f64
        } else {
            config.fps as f64
        };
        Ok(renderer)
    }

    /// The frame as it goes out: made the panel's size where the theme is another (fit_frame:
    /// scaled into it, the theme's background round it), upside down on a panel mounted so.
    fn outgoing(&self, frame: Image, renderer: &Renderer) -> Result<Image, String> {
        let mut frame = frame;
        if let Some(model) = self.model() {
            let (width, height) = model.size(renderer.theme.orientation());
            let size = (width as usize, height as usize);
            if frame.size() != size {
                let ground = renderer
                    .color(Some(&renderer.theme.background_color), 1.0)
                    .map_err(|e| e.0)?
                    .unwrap_or([0, 0, 0, 255]);
                let colour: Vec<u8> = ground[..3].iter().map(|c| *c as u8).collect();
                frame = frame
                    .pad(size, Filter::Lanczos, Some(&colour), (0.5, 0.5))
                    .map_err(|e| e.to_string())?;
            }
        }
        let on_panel = self.panel.as_ref().is_some_and(Display::on_panel);
        if on_panel && self.config.device.rotate == 180 {
            frame = frame.transpose(Transpose::Rotate180);
        }
        Ok(frame)
    }

    /// One frame: config.toml followed, the theme that is due drawn of the readings and
    /// shown. How long to wait before the next.
    pub fn step(&mut self) -> Duration {
        let started = self.clock();
        self.follow_config(started);
        if !self.ensure_panel(started) {
            return Duration::from_millis(250);
        }
        let name = self.theme_name(started);
        if self.renderer.is_none() || name != self.shown {
            if self
                .refused
                .as_ref()
                .is_some_and(|(theme, _)| *theme == name)
            {
                return Duration::from_millis(500);
            }
            match self.renderer_of(&name) {
                Ok(renderer) => {
                    for warning in renderer.warnings() {
                        say(&format!("theme {name}: {warning}"));
                    }
                    say(&format!("theme {name}"));
                    self.hub.want(renderer.reads());
                    self.renderer = Some(renderer);
                    self.shown = name;
                    self.refused = None;
                    self.next_sample = started; // (its readings at once)
                }
                Err(why) => {
                    say(&format!("theme {name}: {why}"));
                    self.refused = Some((name, why));
                    return Duration::from_millis(500);
                }
            }
        }
        let Some(mut renderer) = self.renderer.take() else {
            return Duration::from_millis(500);
        };

        // the readings, at the theme's pace; between them the last ones at the time of now
        let every = match (&self.config.refresh_ms, &renderer.theme.refresh_ms) {
            (Some(ms), _) => lp_py::num::int_to_float(ms).unwrap_or(1000.0),
            (None, Value::Int(ms)) => lp_py::num::int_to_float(ms).unwrap_or(1000.0),
            _ => 1000.0,
        } / 1000.0;
        let (now, fold) = moment();
        let snapshot = match self.snapshot.take() {
            Some(last) if started < self.next_sample => Snapshot { now, fold, ..last },
            _ => {
                self.next_sample = started + every;
                let snapshot = self.hub.snapshot(now, fold);
                for note in self.hub.notes() {
                    say(&note);
                }
                snapshot
            }
        };
        self.snapshot = Some(snapshot.clone());

        let level = lp_config::night::brightness_now(&self.config.device, &now, false, None);
        if self.brightness != Some(level) {
            if let Some(panel) = &mut self.panel {
                match panel.set_brightness(level) {
                    Ok(()) => self.brightness = Some(level),
                    Err(why) => say(&format!("brightness: {why}")),
                }
            }
        }

        let drawn = renderer
            .render(snapshot, started)
            .map_err(|e| format!("not drawn by this build yet: {}", e.0))
            .and_then(|(frame, _)| self.outgoing(frame, &renderer));
        let (moving, due) = (renderer.moving, renderer.due);
        self.renderer = Some(renderer);
        match drawn {
            Ok(frame) => {
                let shown = match &mut self.panel {
                    Some(panel) => panel.show(frame, started),
                    None => Ok(false),
                };
                match shown {
                    Ok(found) => {
                        self.frames += 1;
                        if found {
                            // (a panel took over from the PNG file: drawn for it from now on)
                            self.had_panel = true;
                            self.renderer = None;
                            self.brightness = None;
                        }
                    }
                    Err(Failed::Frame(why)) => say(&format!("frame skipped: {why}")),
                    Err(Failed::Panel(why)) => {
                        self.close_panel();
                        self.failed(&why, started);
                    }
                }
            }
            Err(why) => {
                say(&format!("theme {}: {why}", self.shown));
                self.refused = Some((self.shown.clone(), why));
                self.renderer = None;
            }
        }

        if let Some(panel) = &mut self.panel {
            for note in panel.notes() {
                say(&note);
            }
        }

        // pause: while something moves at the frame rate (a panel showing video: its own);
        // otherwise until the next reading, but at least twice a second, so that a seconds
        // clock never skips
        let calm = self.config.device.motion == "calm";
        let streams = self.panel.as_ref().is_some_and(Display::streams);
        let fps = if streams {
            self.config.device.video.fps.max(1)
        } else if calm {
            1
        } else {
            self.config.fps.max(1)
        };
        let mut interval = 1.0 / fps as f64;
        if !moving {
            let mut longest = (self.next_sample - started).min(0.5);
            if let Some(due) = due {
                longest = longest.min(due - started);
            }
            interval = interval.max(longest);
        }
        Duration::from_secs_f64((interval - (self.clock() - started)).max(0.0))
    }

    /// The loop, until `stop` is set or the frames asked for are shown.
    pub fn run(&mut self, stop: &AtomicBool) {
        while !stop.load(Ordering::Relaxed) {
            let mut wait = self.step();
            if self
                .asked
                .frames
                .is_some_and(|frames| self.frames >= frames)
            {
                break;
            }
            // (in short naps, so that a stop is followed at once)
            while !wait.is_zero() && !stop.load(Ordering::Relaxed) {
                let nap = wait.min(Duration::from_millis(50));
                std::thread::sleep(nap);
                wait -= nap;
            }
        }
        self.close();
    }

    /// The panel let go: dark where config.toml says so.
    pub fn close(&mut self) {
        if let Some(renderer) = &mut self.renderer {
            renderer.close();
        }
        if self.config.device.dark_on_quit {
            if let Some(panel) = &mut self.panel {
                let _ = panel.set_brightness(0);
            }
        }
        self.close_panel();
    }
}
