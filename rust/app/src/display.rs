//! Where the frames go (devices/auto.py, devices/turzx.py's TurzxDisplay and
//! devices/virtual.py): a TURZX USB panel, which Libre Panel's engine has, a panel of the
//! Hongtai family or the MAJESTY on a serial port, or a PNG file, which stands in for a
//! panel. With `driver = "auto"` a panel that is there is used, otherwise the frames go into
//! the PNG file while a panel is looked for, so that one plugged in later is picked up; a
//! panel that was in use and is gone is not replaced by the file: it is waited for.

use std::path::{Path, PathBuf};

use lp_config::DeviceConfig;
use lp_devices::hongtai::{HongtaiDisplay, Kind, Timing, BAUD};
use lp_devices::models::{find_model, models_for_usb, PanelModel, PROTOCOLS};
use lp_devices::rate::VideoState;
use lp_devices::serial::{ports, SerialPort};
use lp_devices::turzx::{Setup, Turzx, Video};
use lp_pixels::Image;

/// The drivers this build has; config.toml's `driver` names one.
pub const DRIVERS: [&str; 4] = ["auto", "virtual", "turzx", "hongtai"];
/// While frames go into the PNG file, a panel is looked for this often.
const PROBE_S: f64 = 5.0;

enum Panel {
    /// frames written to a PNG file (none: kept nowhere)
    File {
        path: Option<PathBuf>,
    },
    Hongtai {
        display: Box<HongtaiDisplay<SerialPort>>,
        port: String,
        model: Option<&'static PanelModel>,
    },
    Turzx(Turzx),
}

/// output_path: the file of `[device] output`; a relative one is inside the settings folder.
fn output_path(output: &str, settings: &Path) -> Option<PathBuf> {
    if output.is_empty() {
        return None;
    }
    let path = PathBuf::from(output);
    Some(if path.is_absolute() {
        path
    } else {
        settings.join(path)
    })
}

/// A panel of the Hongtai family on a port: the port config.toml names, else the first whose
/// USB ids are the family's.
fn hongtai_port(device: &DeviceConfig) -> Option<(String, Kind, Option<&'static PanelModel>)> {
    for port in ports() {
        if !device.port.is_empty() && port.device != device.port {
            continue;
        }
        let Some(ids) = port.vid.zip(port.pid) else {
            continue;
        };
        let Some(kind) = Kind::of_usb(ids) else {
            continue;
        };
        // (the MAJESTY is no model of the catalog yet: it is known by its USB ids)
        let model = find_model(&device.model).or_else(|| match kind {
            Kind::Majesty => Some(&lp_devices::hongtai::MAJESTY),
            Kind::Family => models_for_usb(ids.0, ids.1, port.serial_number.as_deref())
                .into_iter()
                .next(),
        });
        return Some((port.device, kind, model));
    }
    None
}

fn hongtai(device: &DeviceConfig) -> Result<Panel, String> {
    let Some((port, kind, model)) = hongtai_port(device) else {
        return Err("no panel of the Hongtai family was found on a serial port".into());
    };
    let link = SerialPort::open(&port, BAUD, false).map_err(|e| e.to_string())?;
    let native = model.map(|m| (m.native_width, m.native_height));
    let (display, _identity) = HongtaiDisplay::open(link, kind, native, &port, Timing::PANEL)
        .map_err(|e| e.to_string())?;
    Ok(Panel::Hongtai {
        display: Box::new(display),
        port,
        model,
    })
}

/// The TURZX USB panel `[device]` and `[video]` name, opened by Libre Panel's engine.
fn turzx(device: &DeviceConfig, settings: &Path, brightness: i64) -> Result<Panel, String> {
    let Some(engine) = lp_devices::engine::find_engine() else {
        return Err(
            "Libre Panel's engine (libre-panel-engine) is not here: it has the TURZX panels".into(),
        );
    };
    if !lp_devices::engine::can_send(&engine) {
        return Err(format!(
            "{} cannot have a panel (an engine of before)",
            engine.display()
        ));
    }
    let video = &device.video;
    // (the tests' simulated panel instead of USB, whether it is on the bus, and more
    // arguments for the engine)
    let env = |name: &str| std::env::var(name).ok().filter(|value| !value.is_empty());
    let setup = Setup {
        engine,
        model: find_model(&device.model),
        beside: device.beside.clone(),
        video: Video {
            mode: video.mode.clone(),
            fps: video.fps,
            device_fps: video.device_fps,
            crf: video.crf,
            preset: video.preset.clone(),
            keyframe_s: video.keyframe_s,
            maxrate: video.maxrate.clone(),
            profile: video.profile.clone(),
            engine_encoder: video.engine_encoder.clone(),
            local_clip: video.local_clip.clone(),
            video_range: device.video_range.clone(),
        },
        still: device.still.clone(),
        brightness,
        state: Some(VideoState::in_folder(settings)),
        test_link: env("LP_ENGINE_LINK"),
        test_present: env("LP_ENGINE_PRESENT").map(PathBuf::from),
        test_args: env("LP_ENGINE_ARGS")
            .map(|args| args.split_whitespace().map(str::to_string).collect())
            .unwrap_or_default(),
    };
    Turzx::open(setup)
        .map(Panel::Turzx)
        .map_err(|e| e.to_string())
}

/// What the start screen opens a TURZX USB panel with for its files (panel_files.py's
/// open_panel): the engine, the model asked for and the models the other panels name; the
/// tests' simulated panel as for the video.
pub fn files_setup(
    model: Option<&'static lp_devices::models::PanelModel>,
    beside: &[String],
) -> Result<Setup, String> {
    let Some(engine) = lp_devices::engine::find_engine() else {
        return Err(
            "Libre Panel's engine (libre-panel-engine) is not here: it has the TURZX panels".into(),
        );
    };
    let env = |name: &str| std::env::var(name).ok().filter(|value| !value.is_empty());
    let text = |t: &str| t.to_string();
    Ok(Setup {
        engine,
        model,
        beside: beside.to_vec(),
        video: Video {
            mode: text("off"),
            fps: 0,
            device_fps: 0,
            crf: 0,
            preset: String::new(),
            keyframe_s: 0.0,
            maxrate: String::new(),
            profile: String::new(),
            engine_encoder: String::new(),
            local_clip: String::new(),
            video_range: text("auto"),
        },
        still: text("auto"),
        brightness: 0,
        state: None,
        test_link: env("LP_ENGINE_LINK"),
        test_present: env("LP_ENGINE_PRESENT").map(PathBuf::from),
        test_args: env("LP_ENGINE_ARGS")
            .map(|args| args.split_whitespace().map(str::to_string).collect())
            .unwrap_or_default(),
    })
}

/// TurzxDisplay: the panel `[device]` names by its model's protocol; with the model "auto"
/// a TURZX USB panel, else one of the Hongtai family on a serial port.
fn any_panel(device: &DeviceConfig, settings: &Path, brightness: i64) -> Result<Panel, String> {
    match find_model(&device.model).map(|model| (model, model.protocol)) {
        Some((_, "usb-turing")) => turzx(device, settings, brightness),
        Some((_, "serial-hongtai")) => hongtai(device),
        Some((model, protocol)) => {
            let name = PROTOCOLS
                .iter()
                .find(|(id, _)| *id == protocol)
                .map_or(protocol, |(_, name)| name);
            Err(format!(
                "{}: the {name} protocol is not in this build yet. Use driver = \"virtual\" \
                 meanwhile",
                model.label()
            ))
        }
        None => match turzx(device, settings, brightness) {
            Ok(panel) => Ok(panel),
            Err(why) => hongtai(device).map_err(|_| why),
        },
    }
}

/// Why a frame was not shown.
pub enum Failed {
    /// the frame cannot be shown (too detailed for the panel): it is skipped
    Frame(String),
    /// the panel failed: it is opened again
    Panel(String),
}

/// The panel of config.toml's `[device]`, as its driver finds it.
pub struct Display {
    device: DeviceConfig,
    settings: PathBuf,
    panel: Panel,
    brightness: i64,
    /// `driver = "auto"` writing the PNG file: when a panel is looked for again
    next_probe: f64,
    notes: Vec<String>,
}

impl Display {
    /// The panel opened at `now` (the loop's clock), at `brightness`. `had_panel`: one was in
    /// use before (`driver = "auto"` waits for it then, where it would write the file).
    pub fn open(
        device: &DeviceConfig,
        settings: &Path,
        brightness: i64,
        had_panel: bool,
        now: f64,
    ) -> Result<Display, String> {
        let file = || Panel::File {
            path: output_path(&device.output, settings),
        };
        let mut notes = Vec::new();
        let panel = match device.driver.as_str() {
            "virtual" => file(),
            "turzx" => any_panel(device, settings, brightness)?,
            "hongtai" => hongtai(device)?,
            "auto" => match any_panel(device, settings, brightness) {
                Ok(panel) => panel,
                Err(why) if had_panel => return Err(why), // (as the panel said it)
                Err(why) => {
                    notes.push(format!(
                        "no panel in use ({why}); rendering to {} and looking for a panel \
                         every {PROBE_S} s",
                        device.output
                    ));
                    file()
                }
            },
            other => {
                return Err(format!(
                    "the driver {other:?} is not in this build yet (it has: {})",
                    DRIVERS.join(", ")
                ))
            }
        };
        Ok(Display {
            device: device.clone(),
            settings: settings.to_path_buf(),
            panel,
            brightness,
            next_probe: now + PROBE_S,
            notes,
        })
    }

    /// A display that writes its frames to `path` and nothing else.
    pub fn file(device: &DeviceConfig, path: PathBuf) -> Display {
        Display {
            device: DeviceConfig {
                driver: "virtual".into(),
                ..device.clone()
            },
            settings: PathBuf::new(),
            panel: Panel::File { path: Some(path) },
            brightness: device.brightness,
            next_probe: f64::INFINITY,
            notes: Vec::new(),
        }
    }

    /// Whether frames go to a panel: only a panel is turned, never the PNG file that stands
    /// in for one.
    pub fn on_panel(&self) -> bool {
        !matches!(self.panel, Panel::File { .. })
    }

    /// The model of the panel that is there, where it is known.
    pub fn model(&self) -> Option<&'static PanelModel> {
        match &self.panel {
            Panel::File { .. } => None,
            Panel::Hongtai { model, .. } => *model,
            Panel::Turzx(panel) => panel.model(),
        }
    }

    /// Whether the panel shows video: it is drawn for at `[video] fps`.
    pub fn streams(&self) -> bool {
        matches!(&self.panel, Panel::Turzx(panel) if panel.streams())
    }

    pub fn describe(&self) -> String {
        match &self.panel {
            Panel::File { path: Some(path) } => format!("PNG file {}", path.display()),
            Panel::File { path: None } => "virtual display".to_string(),
            Panel::Hongtai { port, model, .. } => match model {
                Some(model) => format!("{} on {port}", model.label()),
                None => format!("panel on {port}"),
            },
            Panel::Turzx(panel) => panel.describe(),
        }
    }

    /// The frame shown; `now`: the time it was drawn for, on the loop's clock (a panel
    /// showing video gets it on its time). While `driver = "auto"` writes the PNG file, a
    /// panel is looked for every few seconds and takes over when one is there: true then
    /// (the frame went to it, and what is shown is to be drawn for it from now on).
    pub fn show(&mut self, frame: Image, now: f64) -> Result<bool, Failed> {
        let mut found = false;
        if self.device.driver == "auto" && !self.on_panel() && now >= self.next_probe {
            self.next_probe = now + PROBE_S;
            if let Ok(panel) = any_panel(&self.device, &self.settings, self.brightness) {
                self.panel = panel;
                self.notes.push(format!("panel found: {}", self.describe()));
                found = true;
            }
        }
        let failed = |e: lp_devices::base::DeviceError| {
            if e.is_frame() {
                Failed::Frame(e.0)
            } else {
                Failed::Panel(e.0)
            }
        };
        match &mut self.panel {
            Panel::File { path: Some(path) } => {
                lp_devices::png::save(&frame, path).map_err(Failed::Panel)
            }
            Panel::File { path: None } => Ok(()),
            Panel::Hongtai { display, .. } => display.show(&frame).map_err(failed),
            Panel::Turzx(panel) => panel.show(frame, Some(now)).map_err(failed),
        }?;
        Ok(found)
    }

    pub fn set_brightness(&mut self, percent: i64) -> Result<(), String> {
        self.brightness = percent;
        match &mut self.panel {
            Panel::File { .. } => Ok(()),
            Panel::Hongtai { display, .. } => {
                display.set_brightness(percent).map_err(|e| e.to_string())
            }
            Panel::Turzx(panel) => panel.set_brightness(percent).map_err(|e| e.to_string()),
        }
    }

    /// What the panel's driver counted since it was opened (none for the PNG file).
    pub fn counters(&self) -> lp_devices::base::Counters {
        match &self.panel {
            Panel::File { .. } => lp_devices::base::Counters::default(),
            Panel::Hongtai { display, .. } => display.counters(),
            Panel::Turzx(panel) => panel.counters(),
        }
    }

    /// The video's bit rate cap in force, Mbit/s (0: no video).
    pub fn bit_rate(&self) -> f64 {
        match &self.panel {
            Panel::Turzx(panel) => panel.bit_rate(),
            _ => 0.0,
        }
    }

    /// What the panel's driver noted for the log since it was last asked.
    pub fn notes(&mut self) -> Vec<String> {
        let mut notes = std::mem::take(&mut self.notes);
        if let Panel::Turzx(panel) = &mut self.panel {
            notes.extend(panel.notes());
        }
        notes
    }

    pub fn close(&mut self) {
        match &mut self.panel {
            Panel::File { .. } => {}
            Panel::Hongtai { display, .. } => display.close(),
            Panel::Turzx(panel) => panel.close(),
        }
    }
}
