//! Where the frames go (devices/auto.py, devices/turzx.py's TurzxDisplay and
//! devices/virtual.py): a TURZX USB panel, which Libre Panel's engine has, a panel of the
//! Hongtai family or the MAJESTY on a serial port, or a PNG file, which stands in for a
//! panel. With `driver = "auto"` a panel that is there is used, otherwise the frames go into
//! the PNG file while a panel is looked for, so that one plugged in later is picked up; a
//! panel that was in use and is gone is not replaced by the file: it is waited for.

use std::path::{Path, PathBuf};
use std::rc::Rc;

use lp_config::DeviceConfig;
use lp_devices::changes::{changed_region, changed_regions};
use lp_devices::driver::Driver;
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
    /// a serial panel or another maker's USB screen, and the frame it was sent last
    Other {
        driver: Box<dyn Driver>,
        previous: Option<Image>,
    },
}

/// The driver of a protocol of the panels that are neither TURZX USB nor the Hongtai family
/// (turzx.py's SERIAL_DRIVERS and OTHER_DRIVERS), opened; None for a protocol this build has
/// no driver for.
fn other(
    device: &DeviceConfig,
    model: Option<&'static PanelModel>,
    protocol: &str,
) -> Option<Result<Panel, String>> {
    let opened: Result<Box<dyn Driver>, String> = match protocol {
        "usb-udisp" => {
            let mut driver = lp_devices::udisp::Udisp::new(
                lp_devices::udisp::Options {
                    jpeg: device.jpeg.clone(),
                    jpeg_quality: device.jpeg_quality,
                    fps: device.video.fps,
                },
                model,
            );
            driver.open().map(|()| Box::new(driver) as Box<dyn Driver>)
        }
        "usb-beada" => {
            let mut driver = lp_devices::beada::Beada::new(model, device.brightness);
            driver.open().map(|()| Box::new(driver) as Box<dyn Driver>)
        }
        "usb-thermaltake" => {
            let mut driver = lp_devices::thermaltake::Thermaltake::new(model);
            driver.open().map(|()| Box::new(driver) as Box<dyn Driver>)
        }
        "usb-thermalright" => {
            let mut driver = lp_devices::thermalright::Trofeo::new(model);
            driver.open().map(|()| Box::new(driver) as Box<dyn Driver>)
        }
        "usb-nzxt" => {
            let mut driver = lp_devices::nzxt::Kraken::new(model, device.brightness);
            driver.open().map(|()| Box::new(driver) as Box<dyn Driver>)
        }
        "usb-jonsbo" => {
            let mut driver =
                lp_devices::jonsbo_zc360::Zc360::new(model, &device.port, device.brightness);
            driver.numbers = Some(Box::new(zc360_numbers));
            driver.open().map(|()| Box::new(driver) as Box<dyn Driver>)
        }
        // (only where device.model names it: never by auto, USB-to-HDMI adapters have the chip)
        "usb-ms9132" => {
            let mut driver = lp_devices::ms9132::Ms9132::new(model);
            driver.open().map(|()| Box::new(driver) as Box<dyn Driver>)
        }
        "usb-lianli" => {
            let mut driver = lp_devices::lianli::LianLi::new(model);
            driver.open().map(|()| Box::new(driver) as Box<dyn Driver>)
        }
        _ => {
            return serial(device, model, protocol, true).map(|opened| {
                opened
                    .map(|driver| Panel::Other {
                        driver,
                        previous: None,
                    })
                    .map_err(|e| e.0)
            })
        }
    }
    .map_err(|e| e.0);
    Some(opened.map(|driver| Panel::Other {
        driver,
        previous: None,
    }))
}

/// "Which is which?" on the ZC-360's three screens: each its number, while it shows.
fn zc360_numbers(size: (usize, usize)) -> Option<Vec<Image>> {
    if !crate::identify::showing() {
        return None;
    }
    let home = crate::home::Home::find().ok()?;
    let env = lp_theme::Env {
        fonts_dir: home.fonts(),
    };
    (1..=lp_devices::jonsbo_zc360::SCREENS)
        .map(|n| crate::identify::picture(&env, n, size, crate::identify::ACCENT).ok())
        .collect()
}

/// A serial panel of a family the driver of which is SerialPanel's (turzx.py's SERIAL_DRIVERS
/// but the Hongtai family and the DS916): `configured`, as config.toml names its model, or
/// found by auto.
fn serial(
    device: &DeviceConfig,
    model: Option<&'static PanelModel>,
    protocol: &str,
    configured: bool,
) -> Option<Result<Box<dyn Driver>, lp_devices::base::DeviceError>> {
    use lp_devices::serial_panel::{RevA, RevB, RevD, SerialPanel, WeAct};
    macro_rules! opened {
        ($family:expr) => {{
            let mut panel = SerialPanel::new($family, model, configured, &device.port);
            Some(panel.open().map(|()| Box::new(panel) as Box<dyn Driver>))
        }};
    }
    match protocol {
        "serial-a" => opened!(RevA),
        "serial-b" => opened!(RevB::default()),
        "serial-d" => opened!(RevD),
        "serial-weact" => opened!(WeAct),
        "serial-c" => {
            use lp_devices::turing_rev_c;
            let mut panel = turing_rev_c::panel(model, configured, &device.port);
            let mut opening = |port: &str| {
                lp_devices::serial::SerialPort::open(port, 115_200, true)
                    .map(|link| Box::new(link) as Box<dyn lp_devices::link::Link>)
            };
            let opened = turing_rev_c::open(
                &mut panel,
                &device.port,
                &lp_devices::serial::ports,
                &mut opening,
            );
            Some(opened.map(|()| Box::new(panel) as Box<dyn Driver>))
        }
        "serial-jonsbo" => {
            let mut panel = lp_devices::jonsbo_ds916::Ds916::new(model, &device.port);
            Some(panel.open().map(|()| Box::new(panel) as Box<dyn Driver>))
        }
        _ => None,
    }
}

/// other_usb_model: the USB screen of another maker on the bus, as `auto` finds it, in this
/// order (turzx.py's: Lian Li, Thermalright, BeadaPanel, NZXT, Thermaltake, udisp), without
/// opening it.
pub fn other_usb_model() -> Option<&'static PanelModel> {
    lp_devices::lianli::present_model()
        .or_else(lp_devices::thermalright::present_model)
        .or_else(lp_devices::beada::present_model)
        .or_else(lp_devices::nzxt::present_model)
        .or_else(lp_devices::thermaltake::present_model)
        .or_else(lp_devices::udisp::present_model)
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
            engine_layers: video.engine_layers,
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
            engine_layers: false,
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
            if let Some(opened) = other(device, Some(model), protocol) {
                return opened;
            }
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
            Err(why) => {
                // another maker's USB screen, else a panel on a serial port
                if let Some(model) = other_usb_model() {
                    if let Some(Ok(panel)) = other(device, Some(model), model.protocol) {
                        return Ok(panel);
                    }
                }
                // a JONSBO ZC-360 with its three screens free
                if lp_devices::jonsbo_zc360::all_free() {
                    let model = find_model(lp_devices::jonsbo_zc360::MODEL_ID);
                    if let Some(opened) = other(device, model, "usb-jonsbo") {
                        return opened;
                    }
                }
                // else a panel on a serial port
                match lp_devices::ports::find_port(None, &device.port) {
                    Ok(Some(found)) if found.model.protocol != "serial-hongtai" => {
                        let protocol = found.model.protocol;
                        match serial(device, Some(found.model), protocol, false) {
                            Some(opened) => opened
                                .map(|driver| Panel::Other {
                                    driver,
                                    previous: None,
                                })
                                .map_err(|e| e.0),
                            None => hongtai(device).map_err(|_| why),
                        }
                    }
                    Err(e) if !device.port.is_empty() => Err(e.0),
                    _ => hongtai(device).map_err(|_| why),
                }
            }
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
            Panel::Other { driver, .. } => driver.model(),
        }
    }

    /// A slow link (pixels over a serial port): the motion is kept calm with `[device] motion`
    /// "auto", and the parts that changed go one by one.
    pub fn slow(&self) -> bool {
        matches!(&self.panel, Panel::Other { driver, .. } if driver.slow())
    }

    /// The frames a second of a panel that wants every frame where its driver says (a udisp
    /// display: fewer than `[video] fps` where it takes fewer); None: `[video] fps`.
    pub fn stream_fps(&self) -> Option<i64> {
        match &self.panel {
            Panel::Other { driver, .. } if driver.streaming() => Some(driver.stream_fps()),
            _ => None,
        }
    }

    /// Whether the panel shows video: it is drawn for at `[video] fps`.
    pub fn streams(&self) -> bool {
        match &self.panel {
            Panel::Turzx(panel) => panel.streams(),
            Panel::Other { driver, .. } => driver.streaming(),
            _ => false,
        }
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
            Panel::Other { driver, .. } => driver.describe(),
        }
    }

    /// The frame shown; `now`: the time it was drawn for, on the loop's clock (a panel
    /// showing video gets it on its time). While `driver = "auto"` writes the PNG file, a
    /// panel is looked for every few seconds and takes over when one is there: true then
    /// (the frame went to it, and what is shown is to be drawn for it from now on).
    pub fn show(&mut self, frame: Rc<Image>, now: f64) -> Result<bool, Failed> {
        self.show_changed(frame, now, None)
    }

    /// show, the frame differing from the one shown before only in `changed` (x0, y0, x1, y1;
    /// None: anywhere): a panel showing video compares the two only there.
    pub fn show_changed(
        &mut self,
        frame: Rc<Image>,
        now: f64,
        changed: Option<&[(i64, i64, i64, i64)]>,
    ) -> Result<bool, Failed> {
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
            Panel::Turzx(panel) => panel
                .show_changed(frame, Some(now), changed)
                .map_err(failed),
            Panel::Other { driver, previous } => {
                // app.py's loop: a streaming display every frame whole; a slow one the parts
                // that changed, each on its own; the others the box around what changed
                let parts: Vec<Option<lp_devices::driver::Region>> = if driver.streaming() {
                    vec![None]
                } else if driver.slow() {
                    let parts = changed_regions(previous.as_ref(), &frame, 24, 4);
                    if parts.is_empty() {
                        return Ok(found);
                    }
                    parts.into_iter().map(Some).collect()
                } else {
                    match changed_region(previous.as_ref(), &frame) {
                        Some(region) => vec![Some(region)],
                        None => return Ok(found),
                    }
                };
                let shown = parts
                    .into_iter()
                    .try_for_each(|part| driver.show(&frame, part))
                    .map_err(failed);
                *previous = match (&shown, driver.streaming()) {
                    (Ok(()), false) => Some(Rc::unwrap_or_clone(frame)),
                    _ => None,
                };
                shown
            }
        }?;
        Ok(found)
    }

    /// set_range: the range a colour check compares on a panel showing video (None: its
    /// own again).
    /// Whether a frame may come as its layers (`show_scene`): video through an engine that
    /// composes them.
    pub fn takes_scenes(&self) -> bool {
        matches!(&self.panel, Panel::Turzx(panel) if panel.takes_scenes())
    }

    /// show_scene: the frame `scene` as its layers, drawn for `now`, turned half a turn where
    /// `half`: the pieces the engine has from the frame before go as their key alone.
    pub fn show_scene(
        &mut self,
        scene: &lp_render::scene::SceneFrame,
        now: f64,
        half: bool,
    ) -> Result<(), Failed> {
        use lp_devices::scene::{Entry, Ground, How, Layer};
        let Panel::Turzx(panel) = &mut self.panel else {
            return Err(Failed::Frame("this panel takes no frames of layers".into()));
        };
        let ground = match &scene.ground {
            lp_render::scene::Ground::Colour(rgb) => Ground::Colour(*rgb),
            lp_render::scene::Ground::Picture(picture) => Ground::Picture(picture),
        };
        let entries: Vec<Entry<'_>> = scene
            .pieces
            .iter()
            .map(|(_, piece)| {
                let key = Rc::as_ptr(piece) as usize;
                if panel.knows_piece(key) {
                    return Entry::Known(key);
                }
                // (as Renderer._composite lays them on the opaque frame)
                let mut layers = Vec::with_capacity(2);
                if piece.add {
                    layers.push(Layer {
                        image: &piece.layer,
                        x: piece.x,
                        y: piece.y,
                        how: How::Add,
                    });
                } else {
                    layers.push(Layer {
                        image: &piece.layer,
                        x: piece.x,
                        y: piece.y,
                        how: How::Over,
                    });
                    if let Some(light) = &piece.light {
                        layers.push(Layer {
                            image: &light.layer,
                            x: light.x,
                            y: light.y,
                            how: How::Add,
                        });
                    }
                }
                let keep: Rc<dyn std::any::Any> = Rc::clone(piece) as Rc<dyn std::any::Any>;
                Entry::New { key, keep, layers }
            })
            .collect();
        let failed = |e: lp_devices::base::DeviceError| {
            if e.is_frame() {
                Failed::Frame(e.0)
            } else {
                Failed::Panel(e.0)
            }
        };
        panel
            .show_scene(&ground, entries, scene.size, Some(now), half)
            .map_err(failed)
    }

    /// The pixels of the frame that went last as its layers: what stays on the panel when it
    /// closes.
    pub fn keep_last(&mut self, frame: Rc<Image>) {
        if let Panel::Turzx(panel) = &mut self.panel {
            panel.keep_last(frame);
        }
    }

    /// The frame of layers the engine composed last, whole (for the checks of LP_SCENE_CHECK).
    pub fn shown(&self, timeout: std::time::Duration) -> Option<Image> {
        match &self.panel {
            Panel::Turzx(panel) => panel.shown(timeout),
            _ => None,
        }
    }

    pub fn set_range(&mut self, range: Option<&str>) {
        if let Panel::Turzx(panel) = &mut self.panel {
            panel.set_range(range.map(str::to_string));
        }
    }

    pub fn set_brightness(&mut self, percent: i64) -> Result<(), String> {
        self.brightness = percent;
        match &mut self.panel {
            Panel::File { .. } => Ok(()),
            Panel::Hongtai { display, .. } => {
                display.set_brightness(percent).map_err(|e| e.to_string())
            }
            Panel::Turzx(panel) => panel.set_brightness(percent).map_err(|e| e.to_string()),
            Panel::Other { driver, .. } => driver.set_brightness(percent).map_err(|e| e.0),
        }
    }

    /// What the panel's driver counted since it was opened (none for the PNG file).
    pub fn counters(&self) -> lp_devices::base::Counters {
        match &self.panel {
            Panel::File { .. } => lp_devices::base::Counters::default(),
            Panel::Hongtai { display, .. } => display.counters(),
            Panel::Turzx(panel) => panel.counters(),
            Panel::Other { driver, .. } => driver.counters(),
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
            Panel::Other { driver, .. } => driver.close(),
        }
    }
}
