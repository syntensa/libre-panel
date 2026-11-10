//! Where the frames go: a panel on a serial port (the Hongtai family and the MAJESTY, as
//! devices/auto.py finds them) or a PNG file (devices/virtual.py), which stands in for one.

use std::path::{Path, PathBuf};

use lp_config::DeviceConfig;
use lp_devices::hongtai::{HongtaiDisplay, Kind, Timing, BAUD};
use lp_devices::models::{find_model, models_for_usb, PanelModel};
use lp_devices::serial::{ports, SerialPort};
use lp_pixels::Image;

/// The drivers this build has; config.toml's `driver` names one, or "auto".
pub const DRIVERS: [&str; 3] = ["auto", "virtual", "hongtai"];

pub enum Panel {
    /// frames written to a PNG file (none: kept nowhere)
    File { path: Option<PathBuf> },
    Hongtai {
        display: Box<HongtaiDisplay<SerialPort>>,
        port: String,
        model: Option<&'static PanelModel>,
    },
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

/// A panel of the family on a port: the port config.toml names, else the first whose USB ids
/// are the family's.
fn hongtai_port(device: &DeviceConfig) -> Option<(String, Kind, Option<&'static PanelModel>)> {
    let found = ports();
    for port in &found {
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
        return Some((port.device.clone(), kind, model));
    }
    None
}

impl Panel {
    /// The panel config.toml's `[device]` names: its driver's, or with "auto" a panel that is
    /// there, else the PNG file.
    pub fn open(device: &DeviceConfig, settings: &Path) -> Result<Panel, String> {
        let file = || Panel::File {
            path: output_path(&device.output, settings),
        };
        match device.driver.as_str() {
            "virtual" => Ok(file()),
            "auto" | "hongtai" => {
                let Some((port, kind, model)) = hongtai_port(device) else {
                    return if device.driver == "auto" {
                        Ok(file())
                    } else {
                        Err("no panel of the Hongtai family was found on a serial port".into())
                    };
                };
                let link = SerialPort::open(&port, BAUD, false).map_err(|e| e.to_string())?;
                let native = model.map(|m| (m.native_width, m.native_height));
                let (display, _identity) =
                    HongtaiDisplay::open(link, kind, native, &port, Timing::PANEL)
                        .map_err(|e| e.to_string())?;
                Ok(Panel::Hongtai {
                    display: Box::new(display),
                    port,
                    model,
                })
            }
            other => Err(format!(
                "the driver {other:?} is not in this build yet (it has: {})",
                DRIVERS.join(", ")
            )),
        }
    }

    /// Whether frames go to a panel: only a panel is turned, never the PNG file that stands
    /// in for one.
    pub fn on_panel(&self) -> bool {
        matches!(self, Panel::Hongtai { .. })
    }

    /// The model of the panel that is there, where it is known.
    pub fn model(&self) -> Option<&'static PanelModel> {
        match self {
            Panel::File { .. } => None,
            Panel::Hongtai { model, .. } => *model,
        }
    }

    pub fn describe(&self) -> String {
        match self {
            Panel::File { path: Some(path) } => format!("PNG file {}", path.display()),
            Panel::File { path: None } => "virtual display".to_string(),
            Panel::Hongtai { port, model, .. } => match model {
                Some(model) => format!("{} on {port}", model.label()),
                None => format!("panel on {port}"),
            },
        }
    }

    pub fn show(&mut self, frame: &Image) -> Result<(), String> {
        match self {
            Panel::File { path: Some(path) } => crate::png::save(frame, path),
            Panel::File { path: None } => Ok(()),
            Panel::Hongtai { display, .. } => display.show(frame).map_err(|e| e.to_string()),
        }
    }

    pub fn set_brightness(&mut self, percent: i64) -> Result<(), String> {
        match self {
            Panel::File { .. } => Ok(()),
            Panel::Hongtai { display, .. } => {
                display.set_brightness(percent).map_err(|e| e.to_string())
            }
        }
    }

    pub fn close(&mut self) {
        if let Panel::Hongtai { display, .. } = self {
            display.close();
        }
    }
}
