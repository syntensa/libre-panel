//! The panel's loop (app.py's MainLoop, as far as this build has it): config.toml followed as
//! it changes, the theme that is due (the screens in turn, the one config.toml names, the
//! panel's own) in its version for the panel and dressed as the person chose, the readings
//! taken at the theme's pace, a frame drawn of them and shown, at the frame rate while
//! something moves and at rest otherwise. What the services ask of it through their host: a
//! theme of theirs, a mode (with its page and frame rate), messages, the transition of a
//! switch; and the pages it switches to by itself (music, the PC at rest).

use std::collections::{BTreeSet, HashMap, HashSet};
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
use lp_render::renderer::Drawing;
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
    /// a theme config.toml names that cannot be shown (removed, or of a plugin that is not
    /// there): the panel shows its own instead until another is chosen
    pub missing_theme: String,
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
    /// this driver instead of config.toml's `[device] driver` (`run --driver virtual`)
    pub driver: Option<String>,
}

/// What a loop of a panel beside the first has of it: its place in `[[panels]]`, and the
/// first panel's status (the theme it shows, which a companion follows).
/// Why a panel's loop ends: Libre Panel quits, or the computer goes to sleep; the panel may
/// go dark then (`[device] dark_on_quit`, `dark_on_sleep`). A pause or a restart leaves it.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Ending {
    #[default]
    No,
    Quit,
    Sleep,
}

impl Ending {
    pub fn of(n: u8) -> Ending {
        match n {
            1 => Ending::Quit,
            2 => Ending::Sleep,
            _ => Ending::No,
        }
    }

    pub fn number(self) -> u8 {
        match self {
            Ending::No => 0,
            Ending::Quit => 1,
            Ending::Sleep => 2,
        }
    }
}

pub struct Beside {
    pub n: usize,
    pub first: Arc<Mutex<RunStatus>>,
}

/// _Versions: the version of a theme a panel shows (versions::theme_for, pair_version,
/// unpaired_version: they look into the theme folders), looked up again after [`LOOK_S`]: a
/// panel's loop asks for it every frame, and each lookup asks the disk. A version installed
/// meanwhile is shown within [`LOOK_S`].
#[derive(Default)]
struct Versions {
    /// (the theme named, the orientation, the panel's size, paired): the version, and when
    known: HashMap<VersionAsked, (String, f64)>,
}

type VersionAsked = (String, String, Option<(i64, i64)>, bool);

impl Versions {
    /// How long a version looked up stands (s).
    const LOOK_S: f64 = 1.0;
    /// Names kept at most (a theme in turn, a page, a mode: each one name).
    const MOST: usize = 64;

    fn of(
        &mut self,
        named: &str,
        orientation: &str,
        size: Option<(i64, i64)>,
        paired: bool,
        now: f64,
        places: &Places,
    ) -> String {
        let key = (named.to_string(), orientation.to_string(), size, paired);
        if let Some((name, when)) = self.known.get(&key) {
            if (0.0..Self::LOOK_S).contains(&(now - when)) {
                return name.clone();
            }
        }
        let shown = versions::theme_for(named, orientation, size, places);
        let version = match paired {
            true => versions::pair_version(&shown, places),
            false => versions::unpaired_version(&shown, places),
        };
        let name = version.unwrap_or(shown);
        if self.known.len() >= Self::MOST {
            self.known.clear();
        }
        self.known.insert(key, (name.clone(), now));
        name
    }
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
    /// the TURZX panel on the USB bus while none is open (panel_model), and when it was looked
    /// for
    on_bus: std::cell::Cell<Option<(Instant, Option<&'static PanelModel>)>>,
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
    /// why the one frame asked for (`run --once`) could not be shown: the loop ends with it
    pub fatal: Option<String>,
    /// a panel was in use: one that is gone is waited for (`driver = "auto"`)
    had_panel: bool,
    renderer: Option<Renderer>,
    /// the name of the theme the renderer draws
    shown: String,
    /// a theme that could not be shown, and why: not tried again until something changes
    refused: Option<(String, String)>,
    /// the theme on show is drawn anew (config.toml changed, another panel): kept on show
    /// where the new one cannot be
    relook: bool,
    /// this panel's part of the picture across the panels (arrangement.rs), as the renderer
    /// was made with it, and when that was last looked at
    across: Option<crate::arrangement::Across>,
    across_watched: f64,
    rotation: Rotation,
    /// the versions of themes looked up a moment ago (theme_name, every frame)
    versions: Versions,
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
    /// what `theme_reads` and the wants were last worked out of (rewant)
    rewanted: Option<(u64, u64, u64)>,
    toasts: Option<ToastLayer>,
    judged: Arc<Mutex<Judged>>,
    alarm: AlarmEdge,
    /// a transition under way, and the mode it began in
    blend: Option<Blend>,
    /// a transition shown to see it (Settings → Transitions), while it goes on
    showing: Option<crate::transition_show::Show>,
    began_in: Option<String>,
    /// the plugin transitions' failures to say how to switch, said once each
    failed_switches: std::collections::HashSet<String>,
    /// the frame before toasts, and with them where one showed
    last_frame: Option<Rc<Image>>,
    /// the frame shown last where it went as its layers (its pixels made only when a
    /// transition starts from it)
    last_scene: Option<lp_render::scene::SceneFrame>,
    /// frames of layers the engine composed as here (LP_SCENE_CHECK)
    scenes_same: u64,
    shown_frame: Option<Rc<Image>>,
    /// the renderer and its count of frames of the frame the panel showed last, where that
    /// was the theme's frame as drawn (no transition, message, edge or picture over it): the
    /// next such frame of that renderer differs from it only where the renderer says
    shown_drawn: Option<(u64, u64)>,
    /// what the app's Player plays here (player.rs), drawn instead of the theme
    player: crate::player::Screen,
    playing: bool,
    /// the screen shown here as a monitor (mirror.rs) ...
    mirror: crate::mirror::Screen,
    /// ... instead of the theme
    mirrored: bool,
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

/// For the test of what happens when the loop has a bug: where LP_BUG_AT_FRAME names a frame,
/// the loop fails at it, once in this process. (The environment is asked once, not each frame.)
fn bug_for_the_test(frames: u64) {
    static AT: std::sync::OnceLock<Option<u64>> = std::sync::OnceLock::new();
    static DONE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    let at = *AT.get_or_init(|| {
        let at = std::env::var_os("LP_BUG_AT_FRAME")?;
        at.to_str()?.parse::<u64>().ok()
    });
    if at == Some(frames) && !DONE.swap(true, std::sync::atomic::Ordering::Relaxed) {
        panic!("a bug at frame {frames}, as LP_BUG_AT_FRAME asks for");
    }
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

/// how long the panel found on the USB bus counts before it is looked for again
const BUS_LOOK_S: u64 = 5;

/// LP_SCENE_CHECK: each frame that goes as its layers compared with the engine's (a check while
/// measuring: it composes the frame here too).
fn scene_check() -> bool {
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("LP_SCENE_CHECK").is_some_and(|v| !v.is_empty()))
}

/// The local time of now, and its fold.
pub(crate) fn moment() -> (Moment, bool) {
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
        // (a source nobody brings is noted by the hub, and said with its first readings)
        let (mut hub, _) = Hub::of(&config, asked.demo);
        hub.add(Box::new(crate::sensors::Services(Arc::clone(&host))));
        say(&format!("readings from: {}", hub.names().join(", ")));
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
            on_bus: std::cell::Cell::new(None),
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
            fatal: None,
            had_panel: false,
            renderer: None,
            shown: String::new(),
            refused: None,
            relook: false,
            across: None,
            across_watched: f64::NEG_INFINITY,
            rotation: Rotation::default(),
            versions: Versions::default(),
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
            rewanted: None,
            toasts: None,
            judged: Arc::default(),
            alarm: AlarmEdge::new(),
            blend: None,
            showing: None,
            began_in: None,
            failed_switches: std::collections::HashSet::new(),
            last_frame: None,
            last_scene: None,
            scenes_same: 0,
            shown_frame: None,
            shown_drawn: None,
            player: crate::player::Screen::default(),
            playing: false,
            mirror: crate::mirror::Screen::default(),
            mirrored: false,
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
        let code = lp_i18n::resolve(Some(&self.config.language), lp_i18n::system_language_here);
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
        // (worked out again only where what it is made of changed: the renderer, what the
        // services read, the pages' own)
        let pages = {
            use std::hash::{Hash, Hasher};
            let mut hasher = std::collections::hash_map::DefaultHasher::new();
            self.pages.reads.hash(&mut hasher);
            hasher.finish()
        };
        let made_of = (renderer.serial, self.host.wanted_version(), pages);
        if self.rewanted == Some(made_of) {
            return;
        }
        self.rewanted = Some(made_of);
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
    /// calm: whether the panel's motion is kept calm (`[device] motion`): a picture a second,
    /// no gliding, turning or fading, for a slow panel (a USB serial one) by itself.
    fn calm(&self) -> bool {
        let motion = self.config.device.motion.as_str();
        motion == "calm" || (motion == "auto" && self.panel.as_ref().is_some_and(Display::slow))
    }

    fn fps(&self) -> i64 {
        let streams = self.panel.as_ref().is_some_and(Display::streams);
        if !streams && self.calm() {
            return 1;
        }
        let mode = self.host.mode().unwrap_or_default();
        let own = self.config.modes.get(&mode).and_then(|mode| mode.fps);
        if let Some(fps) = own.filter(|fps| *fps != 0) {
            return fps.max(1);
        }
        if streams {
            let video = (self.panel.as_ref().and_then(Display::stream_fps))
                .unwrap_or(self.config.device.video.fps);
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
        if self.calm() {
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
        if self.last_frame.is_none() {
            // (the frame shown last went as its layers: its pixels made now)
            self.last_frame = self
                .last_scene
                .as_ref()
                .and_then(|scene| scene.compose().ok())
                .map(Rc::new);
        }
        let began = Blend::begin(
            kind,
            self.last_frame.as_deref(),
            self.shown_frame.as_deref(),
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
                self.relook = true;
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
            // (the frame that went last as its layers: its pixels made now, to stay, turned as
            // the panel got it)
            if let Some(Ok(frame)) = self.last_scene.take().map(|scene| scene.compose()) {
                let frame = match panel.on_panel() && self.config.device.rotate == 180 {
                    true => frame.transpose(Transpose::Rotate180),
                    false => frame,
                };
                panel.keep_last(Rc::new(frame));
            }
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
        // (no new start on the way out of a Windows session)
        if now < self.retry_at || crate::winsession::ending() {
            return false;
        }
        let asked_for;
        let device = match &self.asked.driver {
            Some(driver) => {
                asked_for = lp_config::DeviceConfig {
                    driver: driver.clone(),
                    ..self.config.device.clone()
                };
                &asked_for
            }
            None => &self.config.device,
        };
        let (moment, _) = moment();
        let away = crate::presence::away();
        let level = lp_config::night::brightness_now(device, &moment, away, None);
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
                say(&format!("showing on {}{}", panel.describe(), panel.way()));
                if let Ok(mut status) = self.status.lock() {
                    status.target = panel.describe();
                    status.detail.clear();
                }
                self.failures = 0;
                self.had_panel |= panel.on_panel();
                self.panel = Some(panel);
                self.brightness = Some(level);
                self.relook = true; // (its size may be the panel's)
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
        if self.asked.frames == Some(1) && self.beside.is_none() {
            // (app.run's `once`: a single frame is sent, and a device's error is raised)
            self.fatal = Some(why.to_string());
            return;
        }
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
    /// panel_model: the configured model; else the one the panel says it is; else, with `model =
    /// "auto"` and a driver that looks for TURZX USB panels, the one on the USB bus (found by
    /// its ids, never opened; looked for again after a while).
    fn model(&self) -> Option<&'static PanelModel> {
        let device = &self.config.device;
        let known =
            find_model(&device.model).or_else(|| self.panel.as_ref().and_then(Display::model));
        if known.is_some()
            || device.model != "auto"
            || !["auto", "turzx"].contains(&device.driver.as_str())
        {
            return known;
        }
        if let Some((at, found)) = self.on_bus.get() {
            if at.elapsed() < Duration::from_secs(BUS_LOOK_S) {
                return found;
            }
        }
        let found = lp_devices::usb::present_model(&device.beside, 0);
        self.on_bus.set(Some((Instant::now(), found)));
        found
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
        // (while a companion runs beside it, the first panel's pair version: Lumen without
        // the fans the companion shows)
        let paired = self.paired(now);
        let size = self.panel_size();
        let orientation = &self.config.device.orientation;
        self.versions
            .of(&named, orientation, size, paired, now, &self.places)
    }

    /// _instead_of: where the theme that is due cannot be shown and none is on show yet: the
    /// theme config.toml names (or the panel's own, where it names none), else the panel's
    /// own while that one cannot be, so the panel stays lit and the app says which theme is
    /// missing. The renderer, the theme it draws and the missing one ("" for none).
    fn instead(&mut self, wanted: &str, why: String) -> Option<(Renderer, String, String)> {
        // (tried again when config.toml changes, not on every frame)
        self.refused = Some((wanted.to_string(), why));
        let default = lp_theme::defaults::default_theme(self.model(), &self.places).to_string();
        let base = match self.config.theme_given {
            true => self.config.theme.clone(),
            false => default.clone(),
        };
        let orientation = &self.config.device.orientation;
        let named = versions::theme_for(&base, orientation, self.panel_size(), &self.places);
        if wanted != named {
            say(&format!(
                "theme {} not found; showing {}",
                lp_py::repr(wanted),
                lp_py::repr(&named)
            ));
            if let Ok(renderer) = self.renderer_of(&named) {
                return Some((renderer, named, String::new()));
            }
        }
        let why = match versions::find_theme(&named, &self.places) {
            Err(e) => e.msg,
            Ok(_) => format!("theme {} cannot be loaded", lp_py::repr(&named)),
        };
        if named == default {
            say(&format!("theme {named}: {why}"));
            return None;
        }
        match self.renderer_of(&default) {
            Ok(renderer) => {
                say(&format!(
                    "{why}: showing {}, the panel's own, until it is back or another is chosen",
                    lp_py::repr(&default)
                ));
                Some((renderer, default, named))
            }
            Err(also) => {
                say(&format!("theme {named}: {why}; the panel's own: {also}"));
                None
            }
        }
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
        let accent = crate::accent::accent_of(&config.accent);
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

    /// _across: this panel's part of the picture across the panels (`across`): its file, when
    /// it was written, where every panel stands and which of them this is; None without a
    /// picture, or while this panel is not placed.
    fn across_now(&self) -> Option<crate::arrangement::Across> {
        use crate::arrangement::Told;
        if self.config.across.is_empty() {
            return None;
        }
        // (where every panel stands: the whole config.toml, the first panel's first)
        let loaded;
        let full = match &self.beside {
            None => &self.config,
            Some(_) => {
                loaded = lp_config::load_config(&self.config_file, lp_config::platform()).ok()?;
                &loaded
            }
        };
        let first = match &self.beside {
            Some(beside) => Arc::clone(&beside.first),
            None => Arc::clone(&self.status),
        };
        let told = |status: &RunStatus| Told {
            model: status.model.clone(),
            size: status.size,
        };
        let (own, more) = {
            let status = first.lock().ok()?;
            let more: Vec<Option<Arc<Mutex<RunStatus>>>> = (0..full.panels.len())
                .map(|n| status.more.get(&n).map(Arc::clone))
                .collect();
            (told(&status), more)
        };
        let mut statuses = vec![Some(own)];
        for each in more {
            statuses.push(each.and_then(|each| each.lock().ok().map(|s| told(&s))));
        }
        let places = crate::arrangement::places(full, &statuses);
        let index = self.beside.as_ref().map_or(0, |beside| beside.n + 1);
        let folder = self
            .config_file
            .parent()
            .map_or_else(lp_config::dirs::config_dir, std::path::Path::to_path_buf);
        crate::arrangement::across_of(full, &folder, &places, index)
    }

    /// The part of the picture across the panels followed: a new one (another picture, a
    /// panel placed or moved, its size said) makes the renderer again.
    fn follow_across(&mut self, now: f64) {
        if now - self.across_watched < WATCH_S {
            return;
        }
        self.across_watched = now;
        let across = self.across_now();
        if across != self.across {
            self.across = across;
            self.relook = true;
        }
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
        crate::plugins::attach(&mut renderer);
        // under the theme: this panel's part of the picture across the panels
        if let Some(backdrop) = crate::arrangement::backdrop(self.across.as_ref()) {
            renderer.set_backdrop(Some(backdrop)).map_err(|e| e.0)?;
        }
        renderer.place = config.weather.latitude.zip(config.weather.longitude);
        // (videos in themes: ffmpeg found as Python's renderer finds it, else none)
        renderer.ffmpeg = lp_devices::h264::find_ffmpeg("").ok();
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
    fn fitted(&self, frame: Rc<Image>, renderer: &Renderer) -> Result<Rc<Image>, String> {
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
                frame = Rc::new(
                    frame
                        .pad(size, Filter::Lanczos, Some(&colour), (0.5, 0.5))
                        .map_err(|e| e.to_string())?,
                );
            }
        }
        Ok(frame)
    }

    /// _layered (the main loop's choice): whether this frame may go as its layers, the engine
    /// composing it: a panel that takes them, and nothing here that needs its pixels (a
    /// transition, a message, the alarm's edge, plugins, the live picture, the colour check,
    /// the panel's number, a frame fitted to another size).
    fn layered_now(&self, renderer: &Renderer, size: (usize, usize), now: f64) -> bool {
        if !self.panel.as_ref().is_some_and(Display::takes_scenes) {
            return false;
        }
        let watched = self.status.lock().is_ok_and(|status| {
            status
                .live_asked
                .is_some_and(|at| at.elapsed().as_secs_f64() < LIVE_HOLD_S)
        });
        let number = self.beside.as_ref().map_or(1, |beside| beside.n + 2) as i64;
        let theme = (
            renderer.theme.width.max(1) as usize,
            renderer.theme.height.max(1) as usize,
        );
        self.blend.is_none()
            && !self.toasts.as_ref().is_some_and(ToastLayer::active)
            && !self.alarm.showing(now)
            && renderer.plugins.is_none()
            && !watched
            && theme == size
            && crate::colour_show::now(number).is_none()
            && crate::transition_show::asked().is_none()
            && self.showing.is_none()
            && !crate::identify::showing()
    }

    /// The frame as its layers to the panel, the engine composing it (engine_scene): upside
    /// down on a panel mounted so.
    fn show_scene(&mut self, scene: lp_render::scene::SceneFrame, now: f64) {
        let half =
            self.panel.as_ref().is_some_and(Display::on_panel) && self.config.device.rotate == 180;
        self.last_frame = None;
        self.shown_frame = None;
        if let Ok(mut status) = self.status.lock() {
            status.shown = None; // (no one looks: the frame composed here would be its pixels)
        }
        if let Some(panel) = self.panel.as_mut() {
            panel.set_range(None);
        }
        let shown = lp_render::prof::part("show", || match &mut self.panel {
            Some(panel) => panel.show_scene(&scene, now, half),
            None => Ok(()),
        });
        match shown {
            Ok(()) => {
                self.frames += 1;
                if let Ok(mut status) = self.status.lock() {
                    status.state = "showing";
                    status.sequence.clone_from(&self.sequence_at);
                    status.size = Some(scene.size);
                    status.frames = self.frames;
                    status.theme.clone_from(&self.shown);
                }
            }
            Err(Failed::Frame(why)) => say(&format!("frame skipped: {why}")),
            Err(Failed::Panel(why)) => {
                self.close_panel();
                self.failed(&why, now);
            }
        }
        if scene_check() {
            self.check_scene(&scene);
        }
        self.last_scene = Some(scene);
    }

    /// LP_SCENE_CHECK: the frame the engine composed of the layers against the one composed
    /// here of the same pieces, pixel for pixel; a frame that differs is said, and how many
    /// were the same now and then.
    fn check_scene(&mut self, scene: &lp_render::scene::SceneFrame) {
        let Some(theirs) = self
            .panel
            .as_ref()
            .and_then(|panel| panel.shown(Duration::from_secs(2)))
        else {
            say("scene check: the engine gave no frame");
            return;
        };
        let ours = match scene.compose() {
            Ok(ours) => ours,
            Err(why) => return say(&format!("scene check: not composed here: {}", why.0)),
        };
        if ours.size() != theirs.size() {
            return say(&format!(
                "scene check: {:?} here, {:?} by the engine",
                ours.size(),
                theirs.size()
            ));
        }
        let (a, b) = (ours.as_bytes(), theirs.as_bytes());
        if a == b {
            self.scenes_same += 1;
            if self.scenes_same % 100 == 1 {
                say(&format!(
                    "scene check: {} frames the same as composed here",
                    self.scenes_same
                ));
            }
            return;
        }
        let differ: Vec<usize> = (0..a.len() / 3)
            .filter(|&i| a[i * 3..i * 3 + 3] != b[i * 3..i * 3 + 3])
            .collect();
        let first = differ[0];
        let (x, y) = (first % ours.width(), first / ours.width());
        let most = a
            .iter()
            .zip(b)
            .map(|(p, q)| p.abs_diff(*q))
            .max()
            .unwrap_or(0);
        say(&format!(
            "scene check: the engine's frame differs in {} pixels by up to {most}, the first at ({x}, {y}): {:?} here, {:?} by the engine",
            differ.len(),
            &a[first * 3..first * 3 + 3],
            &b[first * 3..first * 3 + 3]
        ));
    }

    /// The frame on the panel kept for the live picture, while it is asked for.
    fn keep_shown(&self, frame: &Image) {
        if let Ok(mut status) = self.status.lock() {
            let asked = status.live_asked;
            let wanted = asked.is_some_and(|at| at.elapsed().as_secs_f64() < LIVE_HOLD_S);
            status.shown = wanted.then(|| Arc::new((*frame).clone()));
        }
    }

    /// _show_transition: the frame while a transition is shown to see it
    /// (transition_show.rs), else `frame`.
    fn transition_shown(&mut self, frame: Rc<Image>, renderer: &Renderer, now: f64) -> Rc<Image> {
        let Some((number, name, label)) = crate::transition_show::asked() else {
            self.showing = None;
            return frame;
        };
        if self.showing.as_ref().map(|show| show.number) != Some(number) {
            let size = (frame.width(), frame.height());
            let card = match lp_render::transitions::show_card(&label, size, renderer) {
                Ok(card) => card,
                Err(why) => {
                    say(&format!("transition {name} not shown: {}", why.0));
                    crate::transition_show::done(number);
                    return frame;
                }
            };
            // (a calm panel: the card alone; else the transition each way, as a switch makes
            // it: at the speed set, one of the list where it is "random")
            let calm = self.calm();
            let (moment, _) = moment();
            let mut made = |pick: usize| {
                if calm {
                    return None;
                }
                let mut params = lp_py::Dict::default();
                if name == "entrance" {
                    params.insert("title".into(), Value::Str(label.clone()));
                }
                let asked = lp_services::host::Transition {
                    name: name.clone(),
                    params: (name == "entrance").then_some(params),
                };
                self.blends += 1;
                switch::handover(
                    &self.config,
                    "",
                    false,
                    Some(asked),
                    renderer,
                    self.snapshot.as_ref().map(|snapshot| &snapshot.readings),
                    &renderer.theme.name,
                    &moment,
                    pick + self.blends as usize,
                )
            };
            let picked = now.to_bits() as usize;
            let (out, back) = (made(picked), made(picked.wrapping_add(1)));
            self.showing = Some(crate::transition_show::Show::new(
                number, &frame, card, out, back,
            ));
        }
        let Some(show) = self.showing.as_mut() else {
            return frame;
        };
        match show.frame(&frame, now, renderer) {
            Ok(Some(shown)) => Rc::new(shown),
            Ok(None) => {
                crate::transition_show::done(number);
                self.showing = None;
                frame
            }
            Err(why) => {
                say(&format!("transition {name} failed: {why}"));
                crate::transition_show::done(number);
                self.showing = None;
                frame
            }
        }
    }

    /// The frame as it goes out: upside down on a panel mounted so.
    fn turned(&self, frame: Rc<Image>) -> Rc<Image> {
        let on_panel = self.panel.as_ref().is_some_and(Display::on_panel);
        match on_panel && self.config.device.rotate == 180 {
            true => Rc::new(frame.transpose(Transpose::Rotate180)),
            false => frame,
        }
    }

    /// The frame as the panel shows it: the transition under way, the message on show and the
    /// alarm's edge over the theme's own.
    fn dressed_frame(&mut self, frame: Rc<Image>, renderer: &Renderer, now: f64) -> Rc<Image> {
        let mut frame = frame;
        if let Some(mut blend) = self.blend.take() {
            let (shown, goes_on, failed) = blend.frame(Rc::unwrap_or_clone(frame), now, renderer);
            frame = Rc::new(shown);
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
        // (the frame is shared: what the panel showed last is kept for the transitions and
        // the plugins without a copy)
        if let Some(toasts) = &mut self.toasts {
            match toasts.draw(&frame, now, hidden, renderer) {
                Ok(Some(with)) => {
                    self.last_frame = Some(frame);
                    let with = Rc::new(with);
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
        let still = self.config.fps <= 1 || self.calm();
        let frame = match self.alarm.draw(&frame, now, crit, still) {
            Ok(Some(edged)) => Rc::new(edged),
            _ => frame,
        };
        // the colour check's picture (Settings → Panel), its video made in the range its
        // moment says
        let number = self.beside.as_ref().map_or(1, |beside| beside.n + 2) as i64;
        let streaming = self.panel.as_ref().is_some_and(|panel| panel.streams());
        let checking = streaming.then(|| crate::colour_show::now(number)).flatten();
        if let Some(panel) = self.panel.as_mut() {
            let wanted = checking.filter(|(_, kind, _)| *kind == "range");
            panel.set_range(wanted.map(|(_, _, value)| value));
        }
        let frame = match checking {
            Some((letter, ..)) => {
                let env = lp_theme::Env {
                    fonts_dir: self.home.fonts(),
                };
                let size = (frame.width(), frame.height());
                match crate::colour_show::picture(&env, size, letter) {
                    Ok(picture) => Rc::new(picture),
                    Err(why) => {
                        say(&format!("the colour check's picture not drawn: {why}"));
                        frame
                    }
                }
            }
            None => frame,
        };
        let frame = self.transition_shown(frame, renderer, now);
        // which panel is which: its number instead, a moment
        if crate::identify::showing() {
            let accent = (renderer.theme.palette.get("accent").and_then(Value::as_str))
                .filter(|accent| !accent.is_empty());
            let number = self.beside.as_ref().map_or(1, |beside| beside.n + 2);
            let env = lp_theme::Env {
                fonts_dir: self.home.fonts(),
            };
            let size = (frame.width(), frame.height());
            let colour = accent.unwrap_or(crate::identify::ACCENT);
            match crate::identify::picture(&env, number, size, colour) {
                Ok(numbered) => return Rc::new(numbered),
                Err(why) => say(&format!("the panel's number not drawn: {why}")),
            }
        }
        frame
    }

    /// One frame: config.toml followed, the theme that is due drawn of the readings and
    /// shown. How long to wait before the next.
    pub fn step(&mut self) -> Duration {
        let began = std::time::Instant::now();
        crate::heap::tick();
        let wait = self.step_inner();
        lp_render::prof::add("step", began.elapsed());
        wait
    }

    fn step_inner(&mut self) -> Duration {
        let started = self.clock();
        let heading = lp_render::prof::Span::new("loop head");
        let following = lp_render::prof::Span::new("head follow");
        self.follow_config(started);
        self.follow_across(started);
        for note in self.host.notes() {
            say(&note);
        }
        self.follow_pages(started);
        drop(following);
        let restarting = lp_devices::turzx::restart_under_way();
        let there = lp_render::prof::part("head panel", || self.ensure_panel(started));
        self.follow_link(restarting);
        if !there {
            if let Ok(mut status) = self.status.lock() {
                status.state = "waiting";
            }
            return Duration::from_millis(250);
        }
        let name = lp_render::prof::part("head theme", || self.theme_name(started));
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
        let began_in = self.began_in.clone();
        let policy = self.blend.as_mut().map(|blend| {
            let (policy, failed) = blend.policy(&name, mode.as_deref(), began_in.as_deref());
            if let Some(why) = failed {
                let said = format!("transition {}: {why}", lp_py::repr(blend.kind.name));
                if self.failed_switches.insert(said.clone()) {
                    crate::logs::say(
                        "libre_panel.render.overlays",
                        &format!("transition {said} failed to say how to switch"),
                    );
                }
            }
            policy
        });
        let may_switch = policy != Some("wait");
        let rebuild = self.renderer.is_none() || self.relook;
        if may_switch && (rebuild || name != self.shown) {
            let refused = (self.refused.as_ref()).is_some_and(|(theme, _)| *theme == name);
            let made = match refused {
                true => None,
                false => match self.renderer_of(&name) {
                    Ok(renderer) => Some((renderer, name.clone(), String::new())),
                    Err(why) if self.renderer.is_some() && !self.shown.is_empty() => {
                        // (a half-written file, a theme taken away: the one on show stays)
                        say(&format!(
                            "keeping theme {}: {why}",
                            lp_py::repr(&self.shown)
                        ));
                        self.refused = Some((name.clone(), why));
                        None
                    }
                    Err(why) => self.instead(&name, why),
                },
            };
            let due = name.clone();
            if let Some((renderer, name, missing)) = made {
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
                if name == due {
                    self.refused = None;
                }
                self.shown = name;
                self.relook = false;
                self.next_sample = started; // (its readings at once)
                if let Ok(mut status) = self.status.lock() {
                    status.missing_theme = missing;
                    status.theme.clone_from(&self.shown);
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
        drop(heading);
        let snapshotting = lp_render::prof::Span::new("snapshot");
        let mut sampled = false; // (fresh readings: Renderer.new_sample)
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
                sampled = true;
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
        drop(snapshotting);
        let middle = lp_render::prof::Span::new("loop mid");
        lp_render::prof::part("rewant", || self.rewant(&renderer));
        if sampled {
            renderer.new_sample(started, every);
        }
        renderer.continuous = self.panel.as_ref().is_some_and(Display::streams);
        renderer.left_out_windows = self.panel.as_ref().is_some_and(Display::cuts_windows);
        renderer.native_rings = self.panel.as_ref().is_some_and(Display::makes_rings);
        renderer.native_bars = self.panel.as_ref().is_some_and(Display::makes_bars);
        renderer.native_fans = self.panel.as_ref().is_some_and(Display::makes_fans);
        // (graphs that rest: frames that rest)
        renderer.graph_steps = self.config.graph_steps;

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
            renderer.toast = toasts.showing(started); // (screens see a toast as it begins)
        }
        if renderer.plugins.is_some() {
            // what the panel showed last, for the plugins (context.shown)
            renderer.shown = self
                .shown_frame
                .as_ref()
                .or(self.last_frame.as_ref())
                .cloned();
        }
        let alarm = alarm_in(Some(&snapshot));
        self.alarm.follow(alarm, started);

        let away = crate::presence::away();
        let level = lp_render::prof::part("brightness", || {
            lp_config::night::brightness_now(&self.config.device, &now, away, None)
        });
        if self.brightness != Some(level) {
            if let Some(panel) = &mut self.panel {
                match panel.set_brightness(level) {
                    Ok(()) => self.brightness = Some(level),
                    Err(why) => say(&format!("brightness: {why}")),
                }
            }
        }

        drop(middle);
        let drawing = self.clock();
        // the app's Player: instead of the theme, on the panels it plays on
        let number = self.beside.as_ref().map_or(1, |beside| beside.n + 2) as i64;
        // the panel as a monitor: instead of the theme (mirror.rs)
        let mirrored = crate::mirror::now(number);
        let playing = match mirrored {
            None => crate::player::now(number),
            Some(_) => None,
        };
        let size = match self.model() {
            Some(model) => {
                let (w, h) = model.size(renderer.theme.orientation());
                (w as usize, h as usize)
            }
            None => (
                renderer.theme.width.max(1) as usize,
                renderer.theme.height.max(1) as usize,
            ),
        };
        // the frame as its layers, for the engine to compose, where nothing here needs its
        // pixels (render/scene.py; the main loop's choice of devices/turzx_video.py)
        let layered =
            mirrored.is_none() && playing.is_none() && self.layered_now(&renderer, size, started);
        let mut scene = None;
        let drawn = match (&mirrored, &playing) {
            (Some(source), _) => Ok(Rc::new(self.mirror.frame(source, size, renderer.fps))),
            (None, Some(showing)) => Ok(Rc::new(self.player.frame(showing, size, renderer.fps))),
            (None, None) if layered => {
                match lp_render::prof::part("render", || renderer.render_scene(snapshot, started)) {
                    Ok((Drawing::Scene(layers), _)) => {
                        scene = Some(layers);
                        Err(String::new()) // (the frame goes as its layers below)
                    }
                    Ok((Drawing::Frame(frame), _)) => self.fitted(frame, &renderer),
                    Err(e) => Err(format!("not drawn by this build yet: {}", e.0)),
                }
            }
            (None, None) => {
                lp_render::prof::part("render", || renderer.render_shared(snapshot, started))
                    .map_err(|e| format!("not drawn by this build yet: {}", e.0))
                    .and_then(|(frame, _)| self.fitted(frame, &renderer))
            }
        };
        if self.playing && playing.is_none() {
            self.player.close(); // (it ended: ffmpeg stops)
        }
        self.playing = playing.is_some();
        if self.mirrored && mirrored.is_none() {
            self.mirror.close();
        }
        self.mirrored = mirrored.is_some();
        let (moving, due) = if let Some(scene) = scene {
            self.meter.drew(self.clock() - drawing);
            self.shown_drawn = None;
            let (moving, due) = (renderer.moving, renderer.due);
            self.renderer = Some(renderer);
            self.show_scene(scene, started);
            (moving, due)
        } else {
            self.last_scene = None;
            // the theme's frame as drawn: where it changed since the one shown before, if that
            // was the frame the same renderer drew just before it
            let mut drawn_as = None;
            let drawn = drawn
                .map(|frame| {
                    let theirs =
                        (mirrored.is_none() && playing.is_none()).then(|| Rc::clone(&frame));
                    let dressed = lp_render::prof::part("dress", || {
                        self.dressed_frame(frame, &renderer, started)
                    });
                    if theirs.is_some_and(|theirs| Rc::ptr_eq(&theirs, &dressed)) {
                        drawn_as = Some((renderer.serial, renderer.renders));
                    }
                    dressed
                })
                .map(|frame| {
                    self.keep_shown(&frame);
                    let turned = self.turned(frame.clone());
                    if !Rc::ptr_eq(&turned, &frame) {
                        drawn_as = None;
                    }
                    turned
                });
            let follows = match (drawn_as, self.shown_drawn) {
                (Some((serial, count)), Some((before, then))) => {
                    serial == before && count == then + 1
                }
                _ => false,
            };
            let changed = renderer.changed.clone().filter(|_| follows);
            self.shown_drawn = None;
            if drawn.is_ok() {
                self.meter.drew(self.clock() - drawing);
            }
            let (mut moving, due) = (renderer.moving, renderer.due);
            moving |= self.playing
                || self.mirrored
                || self.blend.is_some()
                || self.showing.is_some()
                || self.toasts.as_ref().is_some_and(ToastLayer::active)
                || self.alarm.showing(started);
            self.renderer = Some(renderer);
            match drawn {
                Ok(frame) => {
                    let size = frame.size();
                    let shown = lp_render::prof::part("show", || match &mut self.panel {
                        Some(panel) => panel.show_changed(frame, started, changed.as_deref()),
                        None => Ok(false),
                    });
                    match shown {
                        Ok(found) => {
                            self.frames += 1;
                            bug_for_the_test(self.frames);
                            self.shown_drawn = drawn_as.filter(|_| !found);
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
                                self.relook = true;
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
            (moving, due)
        };

        let _tail = lp_render::prof::Span::new("loop tail");
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

    /// The loop, until `stop` is set or the frames asked for are shown. `ending`: why it
    /// ends (Libre Panel quits, the computer sleeps: the panel may go dark), asked when it
    /// ends.
    pub fn run(&mut self, stop: &AtomicBool, ending: &dyn Fn() -> Ending) {
        while !stop.load(Ordering::Relaxed) {
            let mut wait = self.step();
            let drawn = (self.asked.frames).is_some_and(|frames| self.frames >= frames);
            if drawn || self.fatal.is_some() {
                break;
            }
            // (in short naps, so that a stop is followed at once)
            while !wait.is_zero() && !stop.load(Ordering::Relaxed) {
                let nap = wait.min(Duration::from_millis(50));
                std::thread::sleep(nap);
                wait -= nap;
            }
        }
        self.close(ending());
    }

    /// The panel let go: dark where config.toml says so for why the loop ends
    /// (`[device] dark_on_quit`, `dark_on_sleep`).
    pub fn close(&mut self, ending: Ending) {
        // (on the first panel's readings, which close with it)
        if let Some(mut more) = self.more.take() {
            more.close(ending);
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
        let device = &self.config.device;
        let dark = match ending {
            Ending::Quit => device.dark_on_quit,
            Ending::Sleep => device.dark_on_sleep,
            Ending::No => false,
        };
        if dark {
            if let Some(panel) = &mut self.panel {
                let _ = panel.set_brightness(0);
            }
        }
        self.close_panel();
    }
}
