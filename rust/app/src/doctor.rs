//! The panel test (doctor.py and doctor_session.py): checks a connected panel end to end with
//! the display commands normal operation uses (sync, brightness, whole frames). It shows test
//! cards both ways up, asks what the panel shows, measures the speed and writes a report
//! without personal data that can go with an issue. In the app (Status → Diagnose) it runs in
//! a thread of its own while the panel loop gives the panel to it, its questions answered with
//! the app's buttons: each has a key the app knows it by and a kind (confirm, choose, number,
//! wait). A question nobody answers for ten minutes ends the test, as does a cancel; the panel
//! goes back to the loop either way.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use lp_devices::models::{models_for_usb, PanelModel};
use lp_devices::turzx::{brightness_arg, encode_frame, to_native, FileLink, CMD_BRIGHTNESS};
use lp_pixels::Image;
use lp_py::{Dict, Value};
use lp_render::doctor_cards::{ruler_card, test_card, RULER_EDGES};

const QUESTION_TIMEOUT_S: f64 = 600.0;
/// whole frames for the speed: fewer than in a terminal, as a slow serial panel takes up to
/// half a minute for one, and the app waits with the person
pub const FRAMES: usize = 8;
const REPORT_FORM: &str =
    "https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml";
/// the form's answer for a panel that works
const WORKS: &str = "The panel shows the theme correctly";
const CMD_UPLOAD_PNG: u8 = 102;

/// What ends a test before its end.
#[derive(Debug)]
pub enum Stop {
    /// it was ended from the app, or nobody answered
    Cancelled,
    /// the panel stopped answering
    Device(String),
}

impl From<String> for Stop {
    fn from(why: String) -> Stop {
        Stop::Device(why)
    }
}

/// One check of the report.
#[derive(Clone, Debug)]
pub struct Step {
    pub name: String,
    /// "ok", "fail", "skip", "info"
    pub result: &'static str,
    pub detail: String,
}

/// The report of a test.
#[derive(Clone, Debug, Default)]
pub struct Report {
    pub steps: Vec<Step>,
    pub model: Option<&'static PanelModel>,
    /// pixels the glass hides per edge (landscape), from the ruler card
    pub hidden: Vec<(&'static str, Option<i64>)>,
}

impl Report {
    pub fn passed(&self) -> bool {
        !self.steps.is_empty() && self.steps.iter().all(|s| matches!(s.result, "ok" | "info"))
    }

    pub fn failed(&self) -> bool {
        self.steps.is_empty() || self.steps.iter().any(|s| s.result == "fail")
    }

    pub fn summary(&self) -> &'static str {
        if self.passed() {
            "all checks passed"
        } else if self.failed() {
            "problems found"
        } else {
            "no problems found, but the visual checks were skipped"
        }
    }

    fn add(&mut self, name: &str, result: &'static str, detail: &str) {
        self.steps.push(Step {
            name: name.to_string(),
            result,
            detail: detail.to_string(),
        });
    }

    /// The report as text, for an issue.
    pub fn text(&self) -> String {
        let [year, month, day, hour, minute, _] = crate::share::local_time(crate::share::now());
        let (system, release, machine) = crate::settings::platform();
        let panel = match self.model {
            Some(model) => format!("{} — {}", model.id, model.label()),
            None => "none found".to_string(),
        };
        let mut lines = vec![
            format!(
                "Libre Panel doctor report — {year:04}-{month:02}-{day:02} {hour:02}:{minute:02}"
            ),
            format!(
                "libre-panel {} (Rust), {system} {release} ({machine})",
                lp_py::VERSION
            ),
            format!("panel: {panel}"),
            String::new(),
        ];
        for step in &self.steps {
            lines.push(format!("[{:>4}] {}", step.result.to_uppercase(), step.name));
            for line in step.detail.lines() {
                lines.push(format!("       {line}"));
            }
        }
        lines.push(String::new());
        lines.push(format!("RESULT: {}", self.summary()));
        lines.join("\n") + "\n"
    }
}

/// Who answers the test's questions (doctor.py's Terminal, or the app's AppQuestions).
pub trait Questions: Send + Sync {
    /// yes or no, and with a no what the person sees instead
    fn confirm(&self, key: &str, question: &str) -> Result<(bool, String), Stop>;
    /// a whole number; None where the person cannot tell
    fn number(&self, key: &str, question: &str) -> Result<Option<i64>, Stop>;
    /// until the person is ready (watching the panel)
    fn wait(&self, key: &str, text: &str) -> Result<(), Stop>;
}

/// The panel the test talks to: a TURZX USB panel through the engine, or a serial panel
/// through its driver.
enum Opened {
    Usb {
        link: Box<FileLink>,
        setup: Box<lp_devices::turzx::Setup>,
    },
    Serial(Box<crate::display::Display>),
}

/// The temperatures-and-power step: (result, what to say), or None where nothing is to be said.
pub type Sensors = Box<dyn Fn() -> Option<(&'static str, String)> + Send>;

/// How a test is set up: config.toml's `[device]` and where its settings are.
pub struct Options {
    pub brightness: i64,
    pub device: Option<lp_config::DeviceConfig>,
    pub settings: PathBuf,
    pub frames: usize,
    pub seconds_per_card: f64,
    pub speed_seconds: f64,
    /// the temperatures-and-power step (None: not asked)
    pub sensors: Option<Sensors>,
}

pub struct Doctor<'a> {
    questions: Option<&'a dyn Questions>,
    options: Options,
    panel: Option<Opened>,
    pub report: Report,
    shared: Option<Arc<Shared>>,
}

impl<'a> Doctor<'a> {
    pub fn new(questions: Option<&'a dyn Questions>, options: Options) -> Doctor<'a> {
        Doctor {
            questions,
            options,
            panel: None,
            report: Report::default(),
            shared: None,
        }
    }

    fn step(&mut self, name: &str, result: &'static str, detail: &str) {
        self.report.add(name, result, detail);
        if let Some(shared) = &self.shared {
            if let Ok(mut inner) = shared.inner.lock() {
                inner.steps = self.report.steps.clone();
            }
        }
        let shown = if detail.is_empty() {
            String::new()
        } else {
            format!(" — {detail}")
        };
        crate::logs::log(
            crate::logs::Level::Debug,
            "libre_panel.doctor_session",
            &format!("panel test: [{result:>4}] {name}{shown}"),
        );
    }

    fn pause(seconds: f64) {
        std::thread::sleep(Duration::from_secs_f64(seconds));
    }

    /// Whether the person saw it so; None where nobody is asked.
    fn question(&mut self, key: &str, name: &str, question: &str) -> Result<Option<bool>, Stop> {
        let Some(questions) = self.questions else {
            Self::pause(self.options.seconds_per_card);
            self.step(name, "skip", "not checked (no questions asked)");
            return Ok(None);
        };
        let (seen, note) = questions.confirm(key, question)?;
        if seen {
            self.step(name, "ok", "confirmed by looking at the panel");
        } else {
            let note = if note.is_empty() {
                "not as expected".to_string()
            } else {
                note
            };
            self.step(name, "fail", &note);
        }
        Ok(Some(seen))
    }

    /// Show a whole frame; (seconds it took, bytes sent).
    fn send(&mut self, image: &Image) -> Result<(f64, usize), Stop> {
        match self.panel.as_mut() {
            Some(Opened::Usb { link, .. }) => {
                let png = encode_frame(&to_native(image)).map_err(|e| e.to_string())?;
                let started = Instant::now();
                let length = (png.len() as u32).to_be_bytes();
                link.command(CMD_UPLOAD_PNG, &length, &png, 5000)
                    .map_err(|e| e.to_string())?;
                Ok((started.elapsed().as_secs_f64(), png.len()))
            }
            Some(Opened::Serial(display)) => {
                let started = Instant::now();
                let now = lp_render::video::monotonic();
                display.show(image.clone(), now).map_err(|why| match why {
                    crate::display::Failed::Frame(why) | crate::display::Failed::Panel(why) => why,
                })?;
                Ok((
                    started.elapsed().as_secs_f64(),
                    image.width() * image.height() * 2,
                ))
            }
            None => Err(Stop::Device("no panel".into())),
        }
    }

    fn set_brightness(&mut self, percent: i64) -> Result<(), Stop> {
        match self.panel.as_mut() {
            Some(Opened::Usb { link, .. }) => link
                .command(CMD_BRIGHTNESS, &[brightness_arg(percent)], &[], 2000)
                .map(drop)
                .map_err(|e| Stop::Device(e.to_string())),
            Some(Opened::Serial(display)) => display.set_brightness(percent).map_err(Stop::Device),
            None => Err(Stop::Device("no panel".into())),
        }
    }

    /// run: every check, the report at the end (a test ended from the app: Stop::Cancelled).
    pub fn run(&mut self) -> Result<Report, Stop> {
        let engine =
            lp_devices::engine::find_engine().map_or("missing".to_string(), |_| "here".to_string());
        self.step(
            "environment",
            "info",
            &format!("Rust build, engine {engine}"),
        );
        self.sensors_step();
        if !self.find_and_open() && !self.find_serial() {
            return Ok(self.report.clone());
        }
        let checked = self.checks();
        self.close();
        match checked {
            Ok(()) => {}
            Err(Stop::Device(why)) => self.step("panel stopped answering", "fail", &why),
            Err(Stop::Cancelled) => return Err(Stop::Cancelled),
        }
        Ok(self.report.clone())
    }

    fn close(&mut self) {
        if let Some(Opened::Serial(mut display)) = self.panel.take() {
            display.close();
        }
    }

    fn sensors_step(&mut self) {
        let Some(sensors) = &self.options.sensors else {
            return;
        };
        if let Some((result, detail)) = sensors() {
            let home = crate::autostart::user_home();
            let detail = crate::logs::without_folders(&detail, Some(&home.to_string_lossy()));
            self.step("temperature and power readings", result, &detail);
        }
    }

    fn find_and_open(&mut self) -> bool {
        let opened = crate::display::files_setup(None, &[]).and_then(|setup| {
            FileLink::open(&setup, 0)
                .map(|link| (link, setup))
                .map_err(|e| e.to_string())
        });
        let (link, setup) = match opened {
            Ok(opened) => opened,
            Err(why) => {
                self.step("find and open a USB panel (VID 1CBE)", "info", &why);
                return false;
            }
        };
        let pid = link.pid();
        self.report.model = models_for_usb(0x1CBE, pid, None).into_iter().next();
        let label = self
            .report
            .model
            .map_or("unknown size".to_string(), PanelModel::label);
        self.panel = Some(Opened::Usb {
            link: Box::new(link),
            setup: Box::new(setup),
        });
        self.step(
            "find and open a USB panel",
            "ok",
            &format!("1cbe:{pid:04x} — {label}"),
        );
        true
    }

    /// The serial panel of config.toml's `[device]` (a panel of the Hongtai family), opened.
    fn find_serial(&mut self) -> bool {
        let Some(device) = self.options.device.clone() else {
            self.no_panel();
            return false;
        };
        let device = lp_config::DeviceConfig {
            driver: "hongtai".into(),
            ..device
        };
        let now = lp_render::video::monotonic();
        match crate::display::Display::open(
            &device,
            &self.options.settings,
            self.options.brightness,
            false,
            now,
        ) {
            Ok(display) if display.on_panel() => {
                self.report.model = display.model();
                let label = display
                    .model()
                    .map_or_else(|| display.describe(), PanelModel::label);
                self.panel = Some(Opened::Serial(Box::new(display)));
                self.step(
                    "find and open a USB serial panel (virtual COM port)",
                    "ok",
                    &label,
                );
                true
            }
            _ => {
                self.no_panel();
                false
            }
        }
    }

    fn no_panel(&mut self) {
        self.step(
            "find a panel",
            "fail",
            "no USB panel and no USB serial panel (virtual COM port) with a driver",
        );
        self.report_serial_ports();
    }

    /// The ports with a panel's USB ids that were not opened, and why.
    fn report_serial_ports(&mut self) {
        for port in lp_devices::serial::ports() {
            let Some(ids) = port.vid.zip(port.pid) else {
                continue;
            };
            let models = models_for_usb(ids.0, ids.1, port.serial_number.as_deref());
            if models.is_empty() {
                continue;
            }
            let names: Vec<&str> = models.iter().map(|m| m.id).collect();
            let why = if lp_devices::ports::GADGET_IDS.contains(&ids) {
                let number = if port.serial_number.as_deref().unwrap_or("").is_empty() {
                    "none"
                } else {
                    "another (not shown)"
                };
                let quoted: Vec<String> = models.iter().map(|m| format!("\"{}\"", m.id)).collect();
                format!(
                    "serial number {number}; these USB ids are other USB devices' too, so Libre \
                     Panel talks to it only once the panel is chosen: Settings → Panel → Another \
                     panel (device.model = {})",
                    quoted.join(" or ")
                )
            } else {
                "not opened".to_string()
            };
            let detail = format!(
                "{} {:04x}:{:04x} — {}; {why}",
                port.device,
                ids.0,
                ids.1,
                names.join(", ")
            );
            self.step("USB serial panel found", "info", &detail);
        }
    }

    /// Measure the strip the glass hides at each edge (landscape).
    fn ruler(&mut self, model: &PanelModel, step: i64) -> Result<(), Stop> {
        let (w, h) = model.size("landscape");
        let card = ruler_card(w, h, step, 40)?;
        self.send(&card)?;
        let Some(questions) = self.questions else {
            Self::pause(self.options.seconds_per_card);
            self.step(
                "hidden edges (ruler)",
                "skip",
                "not measured (no questions asked)",
            );
            return Ok(());
        };
        let mut hidden = Vec::new();
        for edge in RULER_EDGES {
            let mut capital = edge.to_string();
            capital[..1].make_ascii_uppercase();
            let seen = questions.number(
                &format!("ruler-{edge}"),
                &format!(
                    "{capital} edge: smallest number whose yellow bar you can see (Enter = \
                     cannot tell)? "
                ),
            )?;
            // bar k shows, bar k - step does not: the glass hides k - step pixels (up to k - 1)
            hidden.push((edge, seen.map(|seen| (seen - step).max(0))));
        }
        let parts: Vec<String> = hidden
            .iter()
            .map(|(edge, v)| match v {
                Some(v) => format!("{edge} {v} px"),
                None => format!("{edge} ?"),
            })
            .collect();
        self.report.hidden = hidden;
        self.step(
            "hidden edges (ruler)",
            "info",
            &format!("hidden: {}", parts.join(", ")),
        );
        Ok(())
    }

    fn handshake(&mut self) -> bool {
        let Some(Opened::Usb { link, .. }) = &self.panel else {
            return true;
        };
        let dropped = link.drain();
        let head: Vec<String> = link
            .reply()
            .iter()
            .take(2)
            .map(|b| format!("{b:02x}"))
            .collect();
        let pid = link.pid();
        let mut detail = format!("reply {}", head.join(" "));
        if dropped > 0 {
            detail += &format!(", cleared {dropped} stale replies");
        }
        self.step("handshake (command 10)", "ok", &detail);
        if self.report.model.is_none() {
            self.step(
                "panel size",
                "fail",
                &format!("unknown product id {pid:04x}"),
            );
            return false;
        }
        true
    }

    fn checks(&mut self) -> Result<(), Stop> {
        let usb = matches!(self.panel, Some(Opened::Usb { .. }));
        if usb && !self.handshake() {
            return Ok(());
        }
        let Some(model) = self.report.model else {
            return Ok(());
        };
        let round_panel = model.shape == "round";
        let ways: &[&str] = if round_panel {
            &["landscape"]
        } else {
            &["landscape", "portrait"]
        };
        for (n, way) in ways.iter().enumerate() {
            let n = n + 1;
            let (w, h) = model.size(way);
            let subtitle = format!("{} · {w}x{h} {way} · card {n}", model.label());
            let hidden = if round_panel {
                [0; 4]
            } else {
                model.hidden_edges(way)
            };
            let card = test_card(w, h, "Libre Panel", &subtitle, round_panel, hidden)?;
            self.send(&card)?;
            self.question(
                &format!("card-{way}"),
                &format!("test card, {way}"),
                "Is the white frame visible on all four edges, is 'UP' at the top edge, is the \
                 text readable (not mirrored) and are RED/GREEN/BLUE the right colours?",
            )?;
        }
        if !round_panel {
            self.ruler(model, 2)?;
        }
        if let Some(questions) = self.questions {
            questions.wait(
                "brightness-watch",
                "\nBrightness: the panel goes dark, then bright. Watch it and press Enter. ",
            )?;
        }
        for percent in [10, 100] {
            self.set_brightness(percent)?;
            Self::pause(1.5);
        }
        self.set_brightness(self.options.brightness)?;
        let name = if usb {
            "brightness (command 14)"
        } else {
            "brightness"
        };
        self.question("brightness", name, "Did the panel go dark and then bright?")?;
        let (w, h) = model.size("landscape");
        let cards = [
            test_card(
                w,
                h,
                "Libre Panel",
                "speed test · frame 1",
                round_panel,
                [0; 4],
            )?,
            test_card(
                w,
                h,
                "Libre Panel",
                "speed test · frame 2",
                round_panel,
                [0; 4],
            )?,
        ];
        let (mut times, mut sizes) = (Vec::new(), Vec::new());
        for i in 0..self.options.frames {
            let (seconds, size) = self.send(&cards[i % 2])?;
            times.push(seconds);
            sizes.push(size);
            if times.len() >= 2 && times.iter().sum::<f64>() >= self.options.speed_seconds {
                break;
            }
        }
        if !times.is_empty() {
            let average = times.iter().sum::<f64>() / times.len() as f64;
            let worst = times.iter().copied().fold(0.0, f64::max);
            let kind = if usb { "PNG" } else { "RGB565" };
            let kilobytes = sizes.iter().sum::<usize>() / sizes.len() / 1024;
            self.step(
                &format!("speed ({} full frames)", times.len()),
                "ok",
                &format!(
                    "{:.0} ms per frame = {:.1} fps, worst {:.0} ms, {kind} {kilobytes} KB",
                    average * 1000.0,
                    1.0 / average,
                    worst * 1000.0,
                ),
            );
        }
        self.reconnect()?;
        self.step("close and reconnect", "ok", "");
        let last = test_card(
            w,
            h,
            "Test finished",
            "you can close this window",
            round_panel,
            [0; 4],
        )?;
        self.send(&last)?;
        if usb {
            self.video_step(model);
        }
        Ok(())
    }

    fn reconnect(&mut self) -> Result<(), Stop> {
        match self.panel.take() {
            Some(Opened::Usb { link, mut setup }) => {
                let pid = link.pid();
                drop(link);
                setup.model = lp_devices::usb::model_of(pid).or(setup.model);
                let link = FileLink::open(&setup, 0).map_err(|e| e.to_string())?;
                self.panel = Some(Opened::Usb {
                    link: Box::new(link),
                    setup,
                });
                Ok(())
            }
            Some(Opened::Serial(mut display)) => {
                display.close();
                let device = self.options.device.clone().unwrap_or_default();
                let device = lp_config::DeviceConfig {
                    driver: "hongtai".into(),
                    ..device
                };
                let now = lp_render::video::monotonic();
                let display = crate::display::Display::open(
                    &device,
                    &self.options.settings,
                    self.options.brightness,
                    true,
                    now,
                )?;
                self.panel = Some(Opened::Serial(Box::new(display)));
                Ok(())
            }
            None => Err(Stop::Device("no panel".into())),
        }
    }

    fn video_step(&mut self, model: &'static PanelModel) {
        let Some(device) = &self.options.device else {
            return;
        };
        let Some(Opened::Usb { setup, .. }) = &self.panel else {
            return;
        };
        let mut video = setup.video.clone();
        video.mode = device.video.mode.clone();
        video.local_clip = device.video.local_clip.clone();
        let fps = device.video.fps;
        let (on, missing) = lp_devices::turzx::video_mode(&video, Some(model));
        let detail = if on {
            format!("on, {fps} fps")
        } else if !missing.is_empty() {
            format!("off: {}", missing.join("; "))
        } else {
            format!("off (mode = \"{}\")", video.mode)
        };
        self.step("video mode", "info", &detail);
    }
}

// -- the test in the app (doctor_session.py) -----------------------------------------------------

/// What the app sees of the test.
#[derive(Default)]
struct Inner {
    /// idle, running, asking, done, cancelled, failed
    state: &'static str,
    question: Option<Dict>,
    /// the number of the last question: an answer says which it answers
    asked: i64,
    error: String,
    steps: Vec<Step>,
    report: Option<Report>,
    /// what is plugged in, for the report form
    devices: Option<String>,
    /// what Libre Panel cost the PC before the test, as text (load.rs), for the report
    load_text: String,
}

struct Shared {
    inner: Mutex<Inner>,
    cancel: AtomicBool,
    answers: Mutex<Option<Sender<Dict>>>,
}

/// A refusal the app reads.
pub struct Refused(pub String);

/// The doctor's questions put to the app: each waits for its answer from `Session::answer`.
struct AppQuestions {
    shared: Arc<Shared>,
    answers: Mutex<Receiver<Dict>>,
}

impl AppQuestions {
    fn ask(&self, mut question: Dict) -> Result<Dict, Stop> {
        if self.shared.cancel.load(Ordering::Relaxed) {
            return Err(Stop::Cancelled);
        }
        if let Ok(mut inner) = self.shared.inner.lock() {
            inner.asked += 1;
            question.insert("id".into(), Value::int(inner.asked));
            inner.question = Some(question);
            inner.state = "asking";
        }
        let answer = self
            .answers
            .lock()
            .map_err(|_| Stop::Cancelled)?
            .recv_timeout(Duration::from_secs_f64(QUESTION_TIMEOUT_S));
        if let Ok(mut inner) = self.shared.inner.lock() {
            inner.question = None;
            if inner.state == "asking" {
                inner.state = "running";
            }
        }
        let answer = match answer {
            Ok(answer) => answer,
            Err(RecvTimeoutError::Timeout | RecvTimeoutError::Disconnected) => {
                return Err(Stop::Cancelled)
            }
        };
        if self.shared.cancel.load(Ordering::Relaxed) {
            return Err(Stop::Cancelled);
        }
        Ok(answer)
    }
}

fn question(pairs: Vec<(&str, Value)>) -> Dict {
    pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect()
}

fn whole(value: Option<&Value>) -> Option<i64> {
    match value {
        Some(Value::Int(n)) => i64::try_from(n).ok(),
        _ => None,
    }
}

impl Questions for AppQuestions {
    fn confirm(&self, key: &str, text: &str) -> Result<(bool, String), Stop> {
        let answer = self.ask(question(vec![
            ("key", Value::from(key)),
            ("kind", Value::from("confirm")),
            ("text", Value::from(text)),
        ]))?;
        let yes = answer.get("yes").is_some_and(Value::truthy);
        let note = match answer.get("note") {
            None | Some(Value::None) => String::new(),
            Some(note) => lp_py::format::str_of(note),
        };
        let note: String = lp_py::strip(&note).chars().take(500).collect();
        Ok((yes, note))
    }

    fn number(&self, key: &str, text: &str) -> Result<Option<i64>, Stop> {
        let answer = self.ask(question(vec![
            ("key", Value::from(key)),
            ("kind", Value::from("number")),
            ("text", Value::from(text)),
        ]))?;
        Ok(whole(answer.get("number")).filter(|n| (0..=10_000).contains(n)))
    }

    fn wait(&self, key: &str, text: &str) -> Result<(), Stop> {
        self.ask(question(vec![
            ("key", Value::from(key)),
            ("kind", Value::from("wait")),
            ("text", Value::from(lp_py::strip(text))),
        ]))
        .map(drop)
    }
}

/// One panel test at a time (doctor_session.py's Session).
pub struct Session {
    shared: Arc<Shared>,
    thread: Mutex<Option<std::thread::JoinHandle<()>>>,
}

impl Default for Session {
    fn default() -> Session {
        Session {
            shared: Arc::new(Shared {
                inner: Mutex::new(Inner {
                    state: "idle",
                    ..Inner::default()
                }),
                cancel: AtomicBool::new(false),
                answers: Mutex::new(None),
            }),
            thread: Mutex::new(None),
        }
    }
}

/// What a test needs from the app: its panel loop (None in a plain editor), config.toml and
/// what is plugged in and what Libre Panel costs the PC, for the report form.
pub struct Start {
    pub app: Option<Arc<crate::background::BackgroundApp>>,
    pub config_file: PathBuf,
    pub devices: Option<String>,
    /// what Libre Panel costs the PC now, as text (load.rs's report); measured before the
    /// test holds the panel
    pub load: Option<Box<dyn FnOnce() -> String + Send>>,
}

impl Session {
    /// The test's state, for the app.
    pub fn status(&self) -> Value {
        let Ok(inner) = self.shared.inner.lock() else {
            return Value::None;
        };
        let steps = match &inner.report {
            Some(report) => report.steps.clone(),
            None => inner.steps.clone(),
        };
        let mut out = Dict::new();
        out.insert("state".into(), Value::from(inner.state));
        out.insert(
            "question".into(),
            inner.question.clone().map_or(Value::None, Value::Dict),
        );
        out.insert("error".into(), Value::Str(inner.error.clone()));
        let steps = steps
            .iter()
            .map(|s| {
                Value::Dict(question(vec![
                    ("name", Value::Str(s.name.clone())),
                    ("result", Value::from(s.result)),
                    ("detail", Value::Str(s.detail.clone())),
                ]))
            })
            .collect();
        out.insert("steps".into(), Value::List(steps));
        if let Some(report) = &inner.report {
            // the doctor's report, and what Libre Panel cost the PC before the test
            let text = match inner.load_text.as_str() {
                "" => report.text(),
                load => format!("{}\n{load}", report.text()),
            };
            let model = report.model.map_or(Value::None, |model| {
                Value::Dict(question(vec![
                    ("id", Value::from(model.id)),
                    ("label", Value::Str(model.label())),
                ]))
            });
            let mut told = Dict::new();
            told.insert("text".into(), Value::Str(text.clone()));
            told.insert("passed".into(), Value::Bool(report.passed()));
            told.insert("failed".into(), Value::Bool(report.failed()));
            told.insert("summary".into(), Value::from(report.summary()));
            told.insert("model".into(), model);
            let devices = inner.devices.as_deref();
            told.insert("form".into(), Value::Str(form(report, &text, devices)));
            out.insert("report".into(), Value::Dict(told));
        }
        Value::Dict(out)
    }

    /// start: the test begins in a thread of its own.
    pub fn start(&self, start: Start) -> Result<Value, Refused> {
        {
            let Ok(mut inner) = self.shared.inner.lock() else {
                return Err(Refused("the panel test is running already".into()));
            };
            if matches!(inner.state, "running" | "asking") {
                return Err(Refused("the panel test is running already".into()));
            }
            *inner = Inner {
                state: "running",
                asked: inner.asked,
                devices: start.devices.clone(),
                ..Inner::default()
            };
        }
        let (send, receive) = channel();
        if let Ok(mut answers) = self.shared.answers.lock() {
            *answers = Some(send);
        }
        self.shared.cancel.store(false, Ordering::Relaxed);
        let shared = Arc::clone(&self.shared);
        let thread = std::thread::Builder::new()
            .name("panel test".into())
            .spawn(move || run(shared, receive, start))
            .map_err(|why| Refused(why.to_string()))?;
        if let Ok(mut slot) = self.thread.lock() {
            *slot = Some(thread);
        }
        Ok(self.status())
    }

    /// The answer to the question shown; with "id", only to that one (a second click on the
    /// same button answers nothing).
    pub fn answer(&self, answer: Option<&Value>) -> Result<Value, Refused> {
        let Some(Value::Dict(answer)) = answer else {
            return Err(Refused("an answer is an object".into()));
        };
        {
            let Ok(mut inner) = self.shared.inner.lock() else {
                return Err(Refused("the panel test asks nothing now".into()));
            };
            let Some(asked) = inner.question.as_ref().filter(|_| inner.state == "asking") else {
                return Err(Refused("the panel test asks nothing now".into()));
            };
            if let Some(id) = answer.get("id") {
                if Some(id) != asked.get("id") {
                    return Err(Refused("that question was answered already".into()));
                }
            }
            inner.question = None;
            inner.state = "running";
        }
        if let Ok(answers) = self.shared.answers.lock() {
            if let Some(send) = answers.as_ref() {
                let _ = send.send(answer.clone());
            }
        }
        Ok(self.status())
    }

    pub fn cancel(&self) -> Value {
        let running = self
            .shared
            .inner
            .lock()
            .is_ok_and(|inner| matches!(inner.state, "running" | "asking"));
        if running {
            self.shared.cancel.store(true, Ordering::Relaxed);
            if let Ok(answers) = self.shared.answers.lock() {
                if let Some(send) = answers.as_ref() {
                    let _ = send.send(Dict::new());
                }
            }
        }
        self.status()
    }
}

/// The report form on GitHub with this test filled in (the person sends it).
fn form(report: &Report, text: &str, devices: Option<&str>) -> String {
    let mut fields = vec![("doctor", text.to_string())];
    if let Some(model) = report.model {
        fields.push(("product", model.label()));
    }
    if report.passed() {
        fields.push(("result", WORKS.to_string()));
    }
    if let Some(devices) = devices {
        fields.push(("devices", devices.to_string()));
    }
    let encoded: Vec<String> = fields
        .iter()
        .map(|(k, v)| {
            format!(
                "{}={}",
                crate::notify::quote_plus(k),
                crate::notify::quote_plus(v)
            )
        })
        .collect();
    format!("{REPORT_FORM}&{}", encoded.join("&"))
}

fn say(message: &str) {
    crate::logs::log(
        crate::logs::Level::Info,
        "libre_panel.doctor_session",
        message,
    );
}

/// The test's thread: the panel held from the loop, the doctor run, the panel given back.
fn run(shared: Arc<Shared>, answers: Receiver<Dict>, mut start: Start) {
    let questions = AppQuestions {
        shared: Arc::clone(&shared),
        answers: Mutex::new(answers),
    };
    // before the test holds the panel: Libre Panel as it runs
    if let Some(load) = start.load.take() {
        let text = load();
        if let Ok(mut inner) = shared.inner.lock() {
            inner.load_text = text;
        }
    }
    let mut held = None;
    if let Some(app) = &start.app {
        match app.panel.hold() {
            Ok(ran) => held = Some(ran),
            Err(why) => {
                if let Ok(mut inner) = shared.inner.lock() {
                    inner.state = "failed";
                    inner.error = why;
                }
                return;
            }
        }
    }
    let config = lp_config::load_config(&start.config_file, lp_config::platform()).ok();
    let settings = start
        .config_file
        .parent()
        .map_or_else(lp_config::dirs::config_dir, Path::to_path_buf);
    let device = config.as_ref().map(|config| {
        let mut device = config.device.clone();
        if device.model == "auto" {
            device.model = String::new();
        }
        device
    });
    let options = Options {
        brightness: device.as_ref().map_or(60, |device| device.brightness),
        device,
        settings,
        frames: FRAMES,
        seconds_per_card: 6.0,
        speed_seconds: 20.0,
        sensors: None,
    };
    let mut doctor = Doctor::new(Some(&questions), options);
    doctor.shared = Some(Arc::clone(&shared));
    let done = doctor.run();
    if let Ok(mut inner) = shared.inner.lock() {
        inner.question = None;
        match done {
            Ok(report) => {
                say(&format!("panel test: {}", report.summary()));
                inner.state = "done";
                inner.report = Some(report);
            }
            Err(Stop::Cancelled) => {
                say("panel test ended before its end");
                inner.state = "cancelled";
            }
            Err(Stop::Device(why)) => {
                inner.state = "failed";
                inner.error = why;
            }
        }
    }
    if let (Some(app), Some(ran)) = (&start.app, held) {
        app.panel.release(ran);
    }
}

// -- the panel test in a terminal (doctor.py's Terminal and run_doctor) --------------------------

const ISSUE_URL: &str =
    "https://github.com/syntensa/libre-panel/issues/new?template=panel_support.yml";

/// How the doctor asks in a terminal.
struct Terminal;

impl Terminal {
    fn ask(prompt: &str) -> String {
        use std::io::Write;
        print!("{prompt}");
        let _ = std::io::stdout().flush();
        let mut line = String::new();
        let _ = std::io::stdin().read_line(&mut line);
        line.trim_end_matches(['\r', '\n']).to_string()
    }
}

impl Questions for Terminal {
    fn confirm(&self, _key: &str, question: &str) -> Result<(bool, String), Stop> {
        let answer = Terminal::ask(&format!("{question} [y/n] "));
        let answer = answer.trim().to_lowercase();
        if answer.starts_with('y') || answer.starts_with('j') {
            return Ok((true, String::new()));
        }
        let note = Terminal::ask("What do you see instead? (Enter to skip) ");
        Ok((false, note.trim().to_string()))
    }

    fn number(&self, _key: &str, question: &str) -> Result<Option<i64>, Stop> {
        let answer = Terminal::ask(question);
        let answer = answer.trim();
        let digits = !answer.is_empty() && answer.bytes().all(|b| b.is_ascii_digit());
        Ok(digits.then(|| answer.parse().ok()).flatten())
    }

    fn wait(&self, _key: &str, text: &str) -> Result<(), Stop> {
        Terminal::ask(text);
        Ok(())
    }
}

/// `libre-panel doctor`: the test in a terminal, its report written to `report` (else
/// libre-panel-doctor-<model>.txt here). Whether a problem was found.
pub fn run_doctor(
    report: Option<PathBuf>,
    ask_questions: bool,
    frames: usize,
    config_file: &Path,
) -> Result<bool, String> {
    use std::io::IsTerminal;
    println!("Libre Panel doctor — close the TURZX app before starting.\n");
    let config = lp_config::load_config(config_file, lp_config::platform()).ok();
    let device = config.as_ref().map(|config| {
        let mut device = config.device.clone();
        if device.model == "auto" {
            device.model = String::new();
        }
        device
    });
    let settings = config_file
        .parent()
        .map_or_else(lp_config::dirs::config_dir, Path::to_path_buf);
    let options = Options {
        brightness: device.as_ref().map_or(60, |device| device.brightness),
        device,
        settings,
        frames,
        seconds_per_card: 6.0,
        speed_seconds: 20.0,
        sensors: None,
    };
    let terminal = Terminal;
    let questions: Option<&dyn Questions> =
        (ask_questions && std::io::stdin().is_terminal()).then_some(&terminal);
    let found = Doctor::new(questions, options)
        .run()
        .map_err(|_| "the panel test ended before its end".to_string())?;
    let name = found.model.map_or("no-panel", |model| model.id);
    let path = report.unwrap_or_else(|| PathBuf::from(format!("libre-panel-doctor-{name}.txt")));
    std::fs::write(&path, found.text()).map_err(|why| format!("{}: {why}", path.display()))?;
    println!();
    println!("Result: {}.", found.summary());
    if !found.passed() && !found.failed() {
        println!("Run `libre-panel doctor` in a terminal to answer the visual checks.");
    }
    let shown = std::fs::canonicalize(&path).unwrap_or(path);
    println!("Report: {}", shown.display());
    println!("Please attach it to a hardware report: {ISSUE_URL}");
    Ok(found.failed())
}
