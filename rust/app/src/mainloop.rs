//! The panel's loop (app.py's MainLoop, as far as this build has it): config.toml followed as
//! it changes, the theme that is due (the screens in turn, the one config.toml names, the
//! panel's own) in its version for the panel and dressed as the person chose, the readings
//! taken at the theme's pace, a frame drawn of them and shown, at the frame rate while
//! something moves and at rest otherwise. What the services ask of it through their host: a
//! theme of theirs, a mode (with its page and frame rate), messages, the transition of a
//! switch; and the pages it switches to by itself (music, the PC at rest).

use std::collections::{BTreeSet, HashSet};
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use indexmap::IndexMap;
use lp_config::sequence::Rotation;
use lp_config::Config;
use lp_devices::models::{find_model, PanelModel};
use lp_pixels::{Filter, Image, Transpose};
use lp_py::Value;
use lp_render::alarm::AlarmEdge;
use lp_render::overlays::ToastLayer;
use lp_render::{Moment, Options, Renderer, Snapshot};
use lp_services::host::{Delivery, Event, PluginHost, LEFT_OUT, NO_PANEL, OFF_IN_THEME};
use lp_theme::versions::{self, Places};
use lp_theme::{Env, Theme};

use crate::beside::More;
use crate::company::Company;
use crate::display::{Display, Failed};
use crate::home::Home;
use crate::modes::{self, PageSwitch};
use crate::sensors::{Hub, View};
use crate::switch::{self, Blend};

/// a panel that is missing or gone is tried again after so long, longer each time (_Link)
const BACKOFF_S: [f64; 5] = [1.0, 2.0, 5.0, 10.0, 30.0];
/// ... and one that restarts to clear its hung decoder, after so long
const RESTART_RETRY_S: f64 = 1.0;
/// config.toml is looked at this often
const WATCH_S: f64 = 1.0;
/// a video panel's frame rate while a game runs, where [modes.game] names none
const GAME_STREAM_FPS: i64 = 24;
/// the notices service's reading: a sensor is at its critical limit
const ALARM: &str = "alarm";
/// a companion that is away a moment (a reconnect) keeps the first panel's pair versions
const PAIR_HOLD_S: f64 = 10.0;
/// the frame on the panel is kept for the live picture this long after it was last asked for
pub const LIVE_HOLD_S: f64 = 10.0;
/// a panel beside the first takes its readings this long after the first panel took them, so
/// that a value glides on both at once
const IN_STEP_S: f64 = 0.1;

/// What `notify` is told of a message, kept where any thread may ask: whether the panel is
/// connected, the kinds the theme switches off and those the screen shows itself.
#[derive(Default)]
struct Judged {
    connected: bool,
    off: HashSet<String>,
    left_out: HashSet<String>,
}

/// What the loop does now (app.py's RunStatus, as far as this build has it).
#[derive(Clone, Debug, Default)]
pub struct RunStatus {
    /// "starting", "waiting" (for the panel) or "showing"
    pub state: &'static str,
    /// the theme on show
    pub theme: String,
    pub frames: u64,
    /// which run of the loop this is, counted from 1 (0: none runs)
    pub run: u64,
    /// the size of the panel's frames
    pub size: Option<(i64, i64)>,
    /// where the screens in turn stand: the step, its theme and the seconds left of it
    pub sequence: Option<(usize, String, f64)>,
    /// why the panel is not available ("waiting"), or why a panel beside the first stopped
    /// ("error")
    pub detail: String,
    /// where frames go: a panel, or a PNG file
    pub target: String,
    /// the panel's model, set or reported ("": not known yet)
    pub model: String,
    /// the mode a service set (a game, music)
    pub mode: Option<String>,
    /// what a config.toml without a theme shows on this panel
    pub default_theme: String,
    /// the frame on the panel now, after transitions and messages, in the theme's orientation:
    /// kept while the live picture was asked for a moment ago (`live_asked`)
    pub shown: Option<Arc<Image>>,
    pub live_asked: Option<Instant>,
    /// the sensor sources the loop reads (the first panel's), for the app's pages
    pub hub: Option<Arc<Mutex<Hub>>>,
    /// the meter's numbers of the last seconds (None before the first second is over)
    pub stats: Option<lp_py::Dict>,
    /// the loops of the panels beside the first, by their place in `[[panels]]`
    pub more: IndexMap<usize, Arc<Mutex<RunStatus>>>,
}

/// the runs of the loop so far
static RUNS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

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
    /// the port of the app's server (None: its usual one)
    pub port: Option<u16>,
}

/// What a loop of a panel beside the first has of it: its place in `[[panels]]`, and the
/// first panel's status (the theme it shows, which a companion follows).
pub struct Beside {
    pub n: usize,
    pub first: Arc<Mutex<RunStatus>>,
}

pub struct MainLoop {
    home: Home,
    env: Env,
    places: Places,
    asked: Asked,
    config: Config,
    config_file: PathBuf,
    watched: f64,
    /// the sources' hub: the first panel's loop reads them, those beside it share it
    hub: Arc<Mutex<Hub>>,
    /// a panel beside the first: its own history of the shared readings
    view: Option<View>,
    beside: Option<Beside>,
    /// the first panel's: the loops of the panels beside it
    more: Option<More>,
    /// when a companion was last seen showing beside the first panel
    paired_at: Option<f64>,
    /// frame rate, drawing time, data rate, the video link, faults; and what the drivers
    /// counted in this run
    meter: crate::meter::Meter,
    counted: crate::meter::Counted,
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
    /// the services' host, and the services that run (the loop's own: stopped with it)
    host: Arc<PluginHost>,
    company: Arc<Company>,
    owns: bool,
    /// what the loop does now, for who asks from another thread
    status: Arc<Mutex<RunStatus>>,
    pages: PageSwitch,
    /// the readings asked of the sources: the theme's, the services' and the pages'
    wants: BTreeSet<String>,
    /// what the themes on show read, as the services' host was told
    theme_reads: BTreeSet<String>,
    toasts: Option<ToastLayer>,
    judged: Arc<Mutex<Judged>>,
    alarm: AlarmEdge,
    /// a transition under way, and the mode it began in
    blend: Option<Blend>,
    began_in: Option<String>,
    /// the frame before toasts, and with them where one showed
    last_frame: Option<Image>,
    shown_frame: Option<Image>,
    /// the host's switches seen, the occasion of the theme on show, and whether it is a step
    /// of the screens in turn
    switches: u64,
    occasion: &'static str,
    in_sequence: bool,
    /// the step of the screens in turn the theme was last taken from
    sequence_at: Option<(usize, String, f64)>,
    /// counts the transitions (each breaks its old frame anew)
    blends: u64,
    connected: bool,
}

fn say(message: &str) {
    crate::logs::say("libre_panel.app", message);
}

/// accent_of: the colour config.toml's `accent` stands for: None for the themes' own (and for
/// the system's, which this build does not read yet).
pub fn accent_of(choice: &str) -> Option<String> {
    let hex = choice.len() == 7
        && choice.starts_with('#')
        && choice[1..].chars().all(|c| c.is_ascii_hexdigit());
    hex.then(|| choice.to_ascii_uppercase())
}

/// _alarm: whether a sensor is at its critical limit (the notices service says).
fn alarm_in(snapshot: Option<&Snapshot>) -> bool {
    let alarm = snapshot.and_then(|snapshot| snapshot.readings.get(ALARM));
    alarm.is_some_and(|alarm| alarm.value.to_f64() == Some(1.0))
}

/// panel_config: for a panel beside the first, the config of `[[panels]]` at `n`.
pub fn panel_config(config: &Config, n: usize) -> Result<Config, String> {
    let gone = || format!("panels[{n}]: no longer in config.toml");
    match config.panels.get(n) {
        Some(panel) if panel.on => config.for_panel(n).ok_or_else(gone),
        _ => Err(gone()),
    }
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
    /// A loop with services of its own, which end with it.
    pub fn new(home: Home, asked: Asked) -> Result<MainLoop, String> {
        MainLoop::with(home, asked, Company::new(), true, Arc::default())
    }

    /// A loop in the `company` of the background app: its services go on when the loop ends
    /// (unless it `owns` them).
    pub fn with(
        home: Home,
        asked: Asked,
        company: Arc<Company>,
        owns: bool,
        status: Arc<Mutex<RunStatus>>,
    ) -> Result<MainLoop, String> {
        let config_file = asked.config.clone().unwrap_or_else(|| home.config_file());
        let config =
            lp_config::load_config(&config_file, lp_config::platform()).map_err(|e| e.msg)?;
        let host = Arc::clone(&company.host);
        if let Ok(mut status) = status.lock() {
            *status = RunStatus {
                state: "starting",
                run: RUNS.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1,
                ..RunStatus::default()
            };
        }
        let (mut hub, missing) = Hub::of(&config, asked.demo);
        hub.add(Box::new(crate::sensors::Services(Arc::clone(&host))));
        say(&format!("readings from: {}", hub.names().join(", ")));
        if !missing.is_empty() {
            say(&format!(
                "sensor sources not in this build yet: {}",
                missing.join(", ")
            ));
        }
        let judged: Arc<Mutex<Judged>> = Arc::default();
        let asks = Arc::clone(&judged);
        host.set_judge(Some(Box::new(move |toast| {
            let Ok(judged) = asks.lock() else {
                return Delivery::held(NO_PANEL);
            };
            if !judged.connected {
                Delivery::held(NO_PANEL)
            } else if judged.off.contains(&toast.kind) {
                Delivery::held(OFF_IN_THEME)
            } else if judged.left_out.contains(&toast.kind) {
                Delivery::held(LEFT_OUT)
            } else {
                Delivery::shown()
            }
        })));
        host.set_data_dir(home.settings.join("plugins-data"));
        let themes = home.places();
        host.set_themes(Some(Box::new(move |name| {
            versions::find_theme(name, &themes).is_ok()
        })));
        let hub = Arc::new(Mutex::new(hub));
        // (the panels beside the first: not for a look at a few frames or into a file)
        let more = (asked.frames.is_none() && asked.output.is_none()).then(|| {
            More::new(
                home.clone(),
                asked.clone(),
                Arc::clone(&company),
                Arc::clone(&hub),
                Arc::clone(&status),
            )
        });
        if let Ok(mut status) = status.lock() {
            status.hub = Some(Arc::clone(&hub));
        }
        let mut made = MainLoop::made(home, asked, config_file, config, hub, host, company, owns);
        made.status = status;
        made.judged = judged;
        made.more = more;
        made.speak();
        made.follow_services();
        Ok(made)
    }

    /// The loop of the panel beside the first at `[[panels]]` `n`, on the first panel's
    /// readings (`hub`), with no services of its own: of a mode the first panel is in (a game,
    /// music) it shows only a theme config.toml gives it for that.
    pub fn beside(
        home: Home,
        asked: Asked,
        company: Arc<Company>,
        hub: Arc<Mutex<Hub>>,
        beside: Beside,
        status: Arc<Mutex<RunStatus>>,
    ) -> Result<MainLoop, String> {
        let config_file = asked.config.clone().unwrap_or_else(|| home.config_file());
        let config =
            lp_config::load_config(&config_file, lp_config::platform()).map_err(|e| e.msg)?;
        let config = panel_config(&config, beside.n)?;
        if let Ok(mut status) = status.lock() {
            *status = RunStatus {
                state: "starting",
                ..RunStatus::default()
            };
        }
        let began = company.began;
        let host = PluginHost::new(Box::new(move || began.elapsed().as_secs_f64()));
        let asked = Asked {
            theme: None,
            output: None,
            ..asked
        };
        let mut made = MainLoop::made(home, asked, config_file, config, hub, host, company, false);
        made.status = status;
        made.view = Some(View::default());
        made.beside = Some(beside);
        Ok(made)
    }

    #[allow(clippy::too_many_arguments)]
    fn made(
        home: Home,
        asked: Asked,
        config_file: PathBuf,
        config: Config,
        hub: Arc<Mutex<Hub>>,
        host: Arc<PluginHost>,
        company: Arc<Company>,
        owns: bool,
    ) -> MainLoop {
        let began = company.began;
        MainLoop {
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
            view: None,
            beside: None,
            more: None,
            paired_at: None,
            meter: crate::meter::Meter::new(began.elapsed().as_secs_f64(), &Default::default()),
            counted: Default::default(),
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
            began,
            frames: 0,
            host,
            company,
            owns,
            status: Arc::default(),
            pages: PageSwitch::new(),
            wants: BTreeSet::new(),
            theme_reads: BTreeSet::new(),
            toasts: None,
            judged: Arc::default(),
            alarm: AlarmEdge::new(),
            blend: None,
            began_in: None,
            last_frame: None,
            shown_frame: None,
            switches: 0,
            occasion: "manual",
            in_sequence: false,
            sequence_at: None,
            blends: 0,
            connected: false,
        }
    }

    /// The sources' hub, while this loop reads it.
    fn hub(&self) -> MutexGuard<'_, Hub> {
        self.hub
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Who the hub is told the readings of: 0 for the first panel's loop, n + 1 for that of
    /// `[[panels]]` n.
    fn owner(&self) -> usize {
        self.beside.as_ref().map_or(0, |beside| beside.n + 1)
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

    /// The services (and what else the company has) brought in line with config.toml (the
    /// first panel's loop: its config has them).
    fn follow_services(&mut self) {
        if self.beside.is_none() {
            self.company.follow(&self.config);
        }
    }

    /// The readings the sources are asked for: those the theme on show reads, those the
    /// services want and those the pages read.
    fn rewant(&mut self, renderer: &Renderer) {
        let mut wants: BTreeSet<String> = self.host.wanted();
        let mut shown: BTreeSet<String> = (self.pages.reads.iter())
            .map(|key| (*key).to_string())
            .collect();
        shown.extend(renderer.reads());
        wants.extend(shown.iter().cloned());
        if shown != self.theme_reads {
            self.host.set_shown(shown.clone());
            self.theme_reads = shown;
        }
        if wants != self.wants {
            let owner = self.owner();
            self.hub().want(owner, wants.iter().cloned());
            self.wants = wants;
        }
    }

    /// What the host is told of the panel: connected, lost, back after a restart.
    fn follow_link(&mut self, restarting: bool) {
        let connected = self.panel.is_some();
        if connected == self.connected {
            return;
        }
        self.connected = connected;
        if let Ok(mut judged) = self.judged.lock() {
            judged.connected = connected;
        }
        match connected {
            true => {
                self.host.emit(Event::PanelConnected, false);
                if restarting {
                    self.host.emit(Event::PanelRestarted, false);
                }
            }
            false => self.host.emit(Event::PanelLost, false),
        }
    }

    /// _follow_pages: the page that is due (music while it plays, the clock when the PC is
    /// idle), or back; never over a mode a service set (a game).
    fn follow_pages(&mut self, now: f64) {
        if self.beside.is_some() {
            return; // (the first panel's loop switches the pages)
        }
        let owner = self.host.mode_owner();
        if self.host.mode().is_some() && owner.as_deref() != Some(modes::OWNER) {
            return;
        }
        let readings = self.snapshot.as_ref().map(|snapshot| &snapshot.readings);
        let due = self.pages.due(&self.config, readings, now);
        if due.map(str::to_string) != self.host.mode() {
            self.host.set_mode(due, Some(modes::OWNER), None);
        }
    }

    /// _fps: frames per second: one where the motion is calm, else the active mode's, else a
    /// streaming display's (in game mode at most 24), else config.toml's.
    fn fps(&self) -> i64 {
        let streams = self.panel.as_ref().is_some_and(Display::streams);
        if !streams && self.config.device.motion == "calm" {
            return 1;
        }
        let mode = self.host.mode().unwrap_or_default();
        let own = self.config.modes.get(&mode).and_then(|mode| mode.fps);
        if let Some(fps) = own.filter(|fps| *fps != 0) {
            return fps.max(1);
        }
        if streams {
            let video = self.config.device.video.fps;
            return match mode.as_str() {
                "game" => video.min(GAME_STREAM_FPS),
                _ => video,
            }
            .max(1);
        }
        self.config.fps.max(1)
    }

    /// The transition of a switch that begins now, where config.toml has one for it.
    fn hand_over(&mut self, renderer: &Renderer, policy: Option<&'static str>, now: f64) {
        let chosen = self.host.take_transition();
        let came = match self.beside {
            Some(_) => {
                let mode = self.beside_mode().unwrap_or_default();
                switch::beside_occasion(&self.config, &mode, self.in_sequence)
            }
            None => switch::occasion(&self.config, &self.host, self.in_sequence),
        };
        let (occasion, leaving) = switch::switch_occasion(self.occasion, came);
        self.occasion = came;
        if policy == Some("follow") && self.host.switches() == self.switches {
            return; // (a following one uncovers the new theme)
        }
        // a calm panel changes at once: a fade is a whole picture a frame
        if self.config.device.motion == "calm" {
            return;
        }
        let (moment, _) = moment();
        self.blends += 1;
        let kind = switch::handover(
            &self.config,
            occasion,
            leaving,
            chosen,
            renderer,
            self.snapshot.as_ref().map(|snapshot| &snapshot.readings),
            &renderer.theme.name,
            &moment,
            self.blends as usize + now.to_bits() as usize,
        );
        let began = Blend::begin(
            kind,
            self.last_frame.as_ref(),
            self.shown_frame.as_ref(),
            now,
            self.blends,
            self.toasts.as_mut(),
        );
        if let Some(blend) = began {
            self.blend = Some(blend);
            self.began_in = self.host.mode();
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
        let loaded = lp_config::load_config(&self.config_file, lp_config::platform())
            .map_err(|e| e.msg)
            .and_then(|fresh| match &self.beside {
                Some(beside) => panel_config(&fresh, beside.n),
                None => Ok(fresh),
            });
        match loaded {
            Ok(fresh) => {
                if fresh.device != self.config.device {
                    self.close_panel();
                }
                if self.beside.is_none() {
                    // (the sources: the first panel's loop tells them, its config has the
                    // same readings)
                    if fresh.sensors != self.config.sensors {
                        let (mut hub, _) = Hub::of(&fresh, self.asked.demo);
                        hub.add(Box::new(crate::sensors::Services(Arc::clone(&self.host))));
                        self.hub().replace(hub);
                        self.wants.clear();
                    }
                    // ([hardware]: the names of this computer's parts and its fans)
                    self.hub().follow_config(&fresh);
                }
                self.config = fresh;
                self.renderer = None;
                self.refused = None;
                if self.beside.is_none() {
                    self.speak();
                    say("config.toml changed: read again");
                }
                self.follow_services();
            }
            Err(why) => {
                // (the stamp kept, so that the same mistake is said once)
                self.config.stamp = stamp;
                say(&format!("config.toml: {why}"));
            }
        }
    }

    fn close_panel(&mut self) {
        if let Some(mut panel) = self.panel.take() {
            panel.close();
            self.counted.follow(panel.counters(), panel.bit_rate());
            self.counted.closed();
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
                if let Ok(mut status) = self.status.lock() {
                    status.target = panel.describe();
                    status.detail.clear();
                }
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
        if let Ok(mut status) = self.status.lock() {
            status.detail = why.to_string();
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
        self.in_sequence = false;
        self.sequence_at = None;
        let page = match self.beside {
            // a panel beside the first: only a page it was given a theme for
            Some(_) => self.beside_mode().and_then(|mode| {
                let own = self.config.modes.get(&mode)?.theme.clone();
                own.filter(|theme| !theme.is_empty())
            }),
            None => modes::page_theme(&self.config, self.host.mode().as_deref()),
        };
        // beside Encore, Encore's companion
        let first = self.beside.as_ref().and_then(|beside| {
            let first = beside
                .first
                .lock()
                .map(|s| s.theme.clone())
                .unwrap_or_default();
            let follows = versions::follows_first(self.config.follow, &self.config.theme);
            (follows && !first.is_empty())
                .then(|| versions::companion_for(&first, &self.places))
                .flatten()
        });
        let named = if let Some(theme) = self.host.theme() {
            theme // (a theme a service shows)
        } else if let Some(theme) = page {
            theme // (the active mode's: music's page has Encore of its own)
        } else if let Some(theme) = first {
            theme
        } else if let Some(theme) = &self.asked.theme {
            theme.clone()
        } else if let Some(place) = self
            .config
            .sequence
            .on
            .then(|| self.rotation.at(&self.config.sequence.steps, now))
            .flatten()
        {
            self.in_sequence = true;
            self.sequence_at = Some((place.index, place.theme.clone(), place.left_s));
            place.theme
        } else if self.config.theme_given {
            self.config.theme.clone()
        } else {
            lp_theme::defaults::default_theme(self.model(), &self.places).to_string()
        };
        let orientation = &self.config.device.orientation;
        let shown = versions::theme_for(&named, orientation, self.panel_size(), &self.places);
        // while a companion runs beside it, the first panel's pair version (Lumen without the
        // fans the companion shows)
        let version = match self.paired(now) {
            true => versions::pair_version(&shown, &self.places),
            false => versions::unpaired_version(&shown, &self.places),
        };
        version.unwrap_or(shown)
    }

    /// _pairing: for the first panel, whether a companion runs beside it, and PAIR_HOLD_S
    /// after the last one was seen, so that a reconnect does not switch back and forth.
    fn paired(&mut self, now: f64) -> bool {
        let Some(more) = &self.more else {
            return false;
        };
        let showing = more.statuses().iter().any(|status| {
            status.lock().is_ok_and(|status| {
                status.state == "showing" && versions::is_companion(&status.theme)
            })
        });
        if showing {
            self.paired_at = Some(now);
        }
        self.paired_at.is_some_and(|seen| now - seen < PAIR_HOLD_S)
    }

    /// _mode: the mode of a panel beside the first: "alarm" while a sensor is at its critical
    /// limit, for one with a theme for that; else the mode the first panel is in.
    fn beside_mode(&self) -> Option<String> {
        if self.config.modes.contains_key(ALARM) && alarm_in(self.snapshot.as_ref()) {
            return Some(ALARM.into());
        }
        self.company.host.mode()
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

    /// The frame made the panel's size where the theme is another (fit_frame: scaled into it,
    /// the theme's background round it).
    fn fitted(&self, frame: Image, renderer: &Renderer) -> Result<Image, String> {
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
        Ok(frame)
    }

    /// The frame on the panel kept for the live picture, while it is asked for.
    fn keep_shown(&self, frame: &Image) {
        if let Ok(mut status) = self.status.lock() {
            let asked = status.live_asked;
            let wanted = asked.is_some_and(|at| at.elapsed().as_secs_f64() < LIVE_HOLD_S);
            status.shown = wanted.then(|| Arc::new(frame.clone()));
        }
    }

    /// The frame as it goes out: upside down on a panel mounted so.
    fn turned(&self, frame: Image) -> Image {
        let on_panel = self.panel.as_ref().is_some_and(Display::on_panel);
        match on_panel && self.config.device.rotate == 180 {
            true => frame.transpose(Transpose::Rotate180),
            false => frame,
        }
    }

    /// The frame as the panel shows it: the transition under way, the message on show and the
    /// alarm's edge over the theme's own.
    fn dressed_frame(&mut self, frame: Image, renderer: &Renderer, now: f64) -> Image {
        let mut frame = frame;
        if let Some(mut blend) = self.blend.take() {
            let (shown, goes_on, failed) = blend.frame(frame, now, renderer);
            frame = shown;
            if let Some(why) = failed {
                say(&format!("transition {} failed: {why}", blend.kind.name));
            }
            if goes_on {
                self.blend = Some(blend);
            }
        }
        // decided after the blend: the frame a hiding transition ends shows the toast
        let hidden = self
            .blend
            .as_ref()
            .is_some_and(|blend| blend.kind.toasts == "hide");
        self.shown_frame = None;
        if let Some(toasts) = &mut self.toasts {
            match toasts.draw(&frame, now, hidden, renderer) {
                Ok(Some(with)) => {
                    self.last_frame = Some(frame);
                    self.shown_frame = Some(with.clone());
                    frame = with;
                }
                Ok(None) => self.last_frame = Some(frame.clone()),
                Err(why) => {
                    say(&format!("message not drawn by this build yet: {}", why.0));
                    self.last_frame = Some(frame.clone());
                }
            }
        } else {
            self.last_frame = Some(frame.clone());
        }
        let crit = renderer.theme.palette.get("crit").and_then(Value::as_str);
        let still = self.config.fps <= 1 || self.config.device.motion == "calm";
        match self.alarm.draw(&frame, now, crit, still) {
            Ok(Some(edged)) => edged,
            _ => frame,
        }
    }

    /// One frame: config.toml followed, the theme that is due drawn of the readings and
    /// shown. How long to wait before the next.
    pub fn step(&mut self) -> Duration {
        let started = self.clock();
        self.follow_config(started);
        for note in self.host.notes() {
            say(&note);
        }
        self.follow_pages(started);
        let restarting = lp_devices::turzx::restart_under_way();
        let there = self.ensure_panel(started);
        self.follow_link(restarting);
        if !there {
            if let Ok(mut status) = self.status.lock() {
                status.state = "waiting";
            }
            return Duration::from_millis(250);
        }
        let name = self.theme_name(started);
        let default = lp_theme::defaults::default_theme(self.model(), &self.places);
        let (model, size) = (self.model().map(|model| model.id), self.panel_size());
        if let Ok(mut status) = self.status.lock() {
            status.model = model.unwrap_or_default().to_string();
            status.size = size;
            status.mode = self.host.mode();
            status.default_theme = default.to_string();
        }
        // while a transition plays, a switch waits for its end, unless the transition says
        // otherwise (follow or restart)
        let mode = self.host.mode();
        let policy = self
            .blend
            .as_ref()
            .map(|blend| blend.policy(mode.as_deref(), self.began_in.as_deref()));
        let may_switch = policy != Some("wait");
        if may_switch && (self.renderer.is_none() || name != self.shown) {
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
                    let toasts = match self.toasts.take() {
                        Some(mut toasts) => toasts.set_renderer(&renderer).map(|()| toasts),
                        None => ToastLayer::new(&renderer),
                    };
                    match toasts {
                        Ok(toasts) => {
                            if let Ok(mut judged) = self.judged.lock() {
                                judged.off = toasts.off().clone();
                                judged.left_out = toasts.left_out().clone();
                            }
                            self.toasts = Some(toasts);
                        }
                        Err(why) => say(&format!("theme {name}: its messages: {}", why.0)),
                    }
                    if name != self.shown {
                        self.hand_over(&renderer, policy, started);
                        self.switches = self.host.switches(); // (this was the switch)
                        let theme = name.clone();
                        self.host.emit(Event::ThemeChanged { theme }, false);
                    }
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
        if self.host.switches() != self.switches && policy != Some("wait") {
            // a mode change that keeps the theme, with a transition
            self.switches = self.host.switches();
            self.hand_over(&renderer, None, started);
        }
        renderer.fps = self.fps() as f64; // (the mode's, the panel's video's, config.toml's)

        // the readings, at the theme's pace; between them the last ones at the time of now
        let every = match (&self.config.refresh_ms, &renderer.theme.refresh_ms) {
            (Some(ms), _) => lp_py::num::int_to_float(ms).unwrap_or(1000.0),
            (None, Value::Int(ms)) => lp_py::num::int_to_float(ms).unwrap_or(1000.0),
            _ => 1000.0,
        } / 1000.0;
        let (now, fold) = moment();
        let snapshot = match self.snapshot.take() {
            Some(mut last) if started < self.next_sample => {
                if self.hub().every_frame() {
                    // cheap sources follow on every frame, not only with the readings
                    let streams = self.panel.as_ref().is_some_and(|panel| panel.streams());
                    let hub = Arc::clone(&self.hub);
                    let mut hub = hub
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner);
                    let latest = hub.fresh(streams);
                    // (a service hears of a change at once, not at the sample)
                    let before = self.host.hears("fresh").then(|| last.readings.clone());
                    last.readings
                        .extend(latest.iter().map(|(key, r)| (key.clone(), r.clone())));
                    match &mut self.view {
                        Some(view) => view.refresh_images(&hub, &mut last.images, streams),
                        None => hub.refresh_images(&mut last.images, false, streams),
                    }
                    drop(hub);
                    if let Some(before) = before {
                        let told = Arc::new(Snapshot {
                            now,
                            fold,
                            ..last.clone()
                        });
                        self.host.fresh(told, &before, &latest);
                    }
                }
                Snapshot { now, fold, ..last }
            }
            _ => {
                self.next_sample = started + every;
                let hub = Arc::clone(&self.hub);
                let mut hub = hub
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                let snapshot = match &mut self.view {
                    Some(view) => {
                        let snapshot = view.snapshot(&mut hub, now, fold);
                        // just after the first panel's next ones
                        if let Some(age) = hub.age().filter(|age| *age < every) {
                            self.next_sample = started - age + every + IN_STEP_S;
                        }
                        snapshot
                    }
                    None => {
                        let snapshot = hub.snapshot(now, fold);
                        for note in hub.notes() {
                            say(&note);
                        }
                        snapshot
                    }
                };
                drop(hub);
                let told = Arc::new(snapshot.clone());
                self.host.set_latest(Arc::clone(&told));
                self.host.emit(Event::Sample { snapshot: told }, false);
                snapshot
            }
        };
        self.snapshot = Some(snapshot.clone());
        self.rewant(&renderer);

        // the messages (while the panel is away they wait at the host, the newest), and the
        // alarm's edge
        if let Some(toasts) = &mut self.toasts {
            toasts.add(self.host.take_toasts().into_iter().map(Rc::new));
            let go_on = self
                .blend
                .as_ref()
                .is_none_or(|blend| matches!(blend.kind.toasts, "over" | "hide"));
            if let Err(why) = toasts.advance(started, !go_on) {
                say(&format!("message not shown by this build yet: {}", why.0));
            }
        }
        let alarm = alarm_in(Some(&snapshot));
        self.alarm.follow(alarm, started);

        let level = lp_config::night::brightness_now(&self.config.device, &now, false, None);
        if self.brightness != Some(level) {
            if let Some(panel) = &mut self.panel {
                match panel.set_brightness(level) {
                    Ok(()) => self.brightness = Some(level),
                    Err(why) => say(&format!("brightness: {why}")),
                }
            }
        }

        let drawing = self.clock();
        let drawn = renderer
            .render(snapshot, started)
            .map_err(|e| format!("not drawn by this build yet: {}", e.0))
            .and_then(|(frame, _)| self.fitted(frame, &renderer))
            .map(|frame| self.dressed_frame(frame, &renderer, started))
            .map(|frame| {
                self.keep_shown(&frame);
                self.turned(frame)
            });
        if drawn.is_ok() {
            self.meter.drew(self.clock() - drawing);
        }
        let (mut moving, due) = (renderer.moving, renderer.due);
        moving |= self.blend.is_some()
            || self.toasts.as_ref().is_some_and(ToastLayer::active)
            || self.alarm.showing(started);
        self.renderer = Some(renderer);
        match drawn {
            Ok(frame) => {
                let size = frame.size();
                let shown = match &mut self.panel {
                    Some(panel) => panel.show(frame, started),
                    None => Ok(false),
                };
                match shown {
                    Ok(found) => {
                        self.frames += 1;
                        if let Ok(mut status) = self.status.lock() {
                            status.state = "showing";
                            status.sequence.clone_from(&self.sequence_at);
                            status.size = Some((size.0 as i64, size.1 as i64));
                            status.frames = self.frames;
                            status.theme.clone_from(&self.shown);
                        }
                        if found {
                            if let Some(panel) = &self.panel {
                                let target = panel.describe();
                                if let Ok(mut status) = self.status.lock() {
                                    status.target = target;
                                }
                            }
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
            self.counted.follow(panel.counters(), panel.bit_rate());
        }
        self.follow_link(false);
        let streams = self.panel.as_ref().is_some_and(Display::streams);
        let fps = self.fps();
        if let Some(stats) = self.meter.tick(started, fps, streams, &self.counted) {
            if let Ok(mut status) = self.status.lock() {
                status.stats = Some(stats);
            }
        }
        if let Some(more) = &mut self.more {
            more.follow(&self.config);
        }

        // pause: while something moves at the frame rate (a panel showing video: its own);
        // otherwise until the next reading, but at least twice a second, so that a seconds
        // clock never skips
        let mut interval = 1.0 / self.fps() as f64;
        if !moving {
            let mut longest = (self.next_sample - started).min(0.5);
            if let Some(due) = due {
                longest = longest.min(due - started);
            }
            interval = interval.max(longest);
        }
        Duration::from_secs_f64((interval - (self.clock() - started)).max(0.0))
    }

    /// The loop, until `stop` is set or the frames asked for are shown. `quitting`: whether
    /// Libre Panel ends with it (the panel may go dark), asked when it ends.
    pub fn run(&mut self, stop: &AtomicBool, quitting: &dyn Fn() -> bool) {
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
        self.close(quitting());
    }

    /// The panel let go: dark where config.toml says so and Libre Panel is `quitting`.
    pub fn close(&mut self, quitting: bool) {
        // (on the first panel's readings, which close with it)
        if let Some(mut more) = self.more.take() {
            more.close(quitting);
        }
        if self.beside.is_some() {
            let owner = self.owner();
            self.hub().forget(owner);
        }
        if self.owns {
            self.company.stop_services();
        }
        self.host.set_judge(None);
        if self.owns {
            self.company.close();
        }
        if let Ok(mut status) = self.status.lock() {
            *status = RunStatus::default();
        }
        if let Some(toasts) = &mut self.toasts {
            toasts.close();
        }
        if let Some(renderer) = &mut self.renderer {
            renderer.close();
        }
        if quitting && self.config.device.dark_on_quit {
            if let Some(panel) = &mut self.panel {
                let _ = panel.set_brightness(0);
            }
        }
        self.close_panel();
    }
}
