//! The serial panels on the system's list of ports, found by their USB ids without opening
//! any (devices/serial_link.py's find_port, free_panels and gadget_ports).

use lp_py::Value;

use crate::base::{place_taken, DeviceError, Result};
use crate::models::{models_for_usb, PanelModel, PROTOCOLS};
use crate::serial::PortInfo;

pub const SERIAL_PROTOCOLS: [&str; 7] = [
    "serial-a",
    "serial-b",
    "serial-c",
    "serial-d",
    "serial-weact",
    "serial-jonsbo",
    "serial-hongtai",
];
/// Generic Linux USB gadget serial ids, which awake rev. C panels use, and so do other
/// devices (a Raspberry Pi as a USB gadget, development boards). Without a model in the
/// config such a port counts only with the panels' serial number.
const GADGET_IDS: [(u16, u16); 3] = [(0x0525, 0xA4A7), (0x1D6B, 0x0121), (0x1D6B, 0x0106)];
const GADGET_SERIAL: &str = "20080411";

/// A port a serial panel is on.
#[derive(Clone, Debug)]
pub struct FoundPort {
    /// "COM3", "/dev/ttyACM0"
    pub device: String,
    pub model: &'static PanelModel,
    pub serial_number: Option<String>,
    /// every model the port's ids and serial number fit; more than one: the size is not
    /// known from them (the driver, the frame or the user decide)
    pub candidates: Vec<&'static PanelModel>,
}

impl FoundPort {
    pub fn sure(&self) -> bool {
        self.candidates.len() <= 1
    }
}

fn ids(info: &PortInfo) -> Option<(u16, u16)> {
    info.vid.zip(info.pid)
}

fn generic(info: &PortInfo) -> bool {
    ids(info).is_some_and(|ids| GADGET_IDS.contains(&ids))
        && info.serial_number.as_deref() != Some(GADGET_SERIAL)
}

fn known(info: &PortInfo) -> Vec<&'static PanelModel> {
    match ids(info) {
        Some((vid, pid)) => models_for_usb(vid, pid, info.serial_number.as_deref()),
        None => Vec::new(),
    }
}

fn found(info: &PortInfo, models: Vec<&'static PanelModel>) -> Option<FoundPort> {
    Some(FoundPort {
        device: info.device.clone(),
        model: models.first()?,
        serial_number: info.serial_number.clone(),
        candidates: models,
    })
}

/// _sibling: whether `model` speaks the protocol of the panel on `info`, whose serial number
/// names other models: the UsbPCMonitor 5" and 7" say USB35INCHIPSV2 like the Turing 3.5",
/// and the driver asks them their size.
fn sibling(model: &PanelModel, info: &PortInfo, known: &[&PanelModel]) -> bool {
    ids(info).is_some_and(|ids| model.usb_ids.contains(&ids))
        && known.iter().any(|m| m.protocol == model.protocol)
}

/// _another_kind: why `model` cannot be on `port`, and which models can.
fn another_kind(port: &str, model: &PanelModel, known: &[&PanelModel]) -> String {
    let protocol = known[0].protocol;
    let family = PROTOCOLS
        .iter()
        .find(|(name, _)| *name == protocol)
        .map_or(protocol, |(_, family)| family);
    let ids: Vec<String> = known.iter().map(|m| format!("\"{}\"", m.id)).collect();
    let said = lp_i18n::t_format(
        "{port} is a panel of another kind ({family}), not {model}: set device.model to {ids}",
        &[
            ("port", Value::Str(port.into())),
            ("family", Value::Str(family.into())),
            ("model", Value::Str(model.label())),
            ("ids", Value::Str(ids.join(" or "))),
        ],
    );
    said.unwrap_or_else(|why| why.msg)
}

/// find_port among `ports`: the port of a connected serial panel: the first whose USB ids
/// (and serial number, where the vendor set one) fit `model`, or any known serial panel when
/// `model` is None. `port` (device.port) limits the search to that port, and is trusted for
/// `model` even when its ids say nothing.
pub fn find_among(
    ports: &[PortInfo],
    model: Option<&'static PanelModel>,
    port: &str,
) -> Result<Option<FoundPort>> {
    let wanted = |m: &PanelModel| match model {
        Some(model) => std::ptr::eq(m, model),
        None => SERIAL_PROTOCOLS.contains(&m.protocol),
    };
    for info in ports.iter().filter(|info| !place_taken(&info.device)) {
        if !port.is_empty() && info.device != port {
            continue;
        }
        if model.is_none() && port.is_empty() && generic(info) {
            continue; // some other gadget: never talked to unasked
        }
        // a serial number that names a model settles it; else every model of the chip
        let known = known(info);
        let mut candidates: Vec<&'static PanelModel> =
            known.iter().copied().filter(|m| wanted(m)).collect();
        if let Some(model) = model {
            if candidates.is_empty() && sibling(model, info, &known) {
                candidates = vec![model]; // its serial number names another size of the family
            }
        }
        if let Some(found) = found(info, candidates) {
            return Ok(Some(found));
        }
        if let Some(model) = model {
            if !port.is_empty() && !known.is_empty() && !generic(info) {
                return Err(DeviceError(another_kind(port, model, &known)));
            }
        }
    }
    if port.is_empty() {
        return Ok(None);
    }
    match model {
        Some(model) => Ok(Some(FoundPort {
            device: port.to_string(),
            model,
            serial_number: None,
            candidates: Vec::new(),
        })),
        None => {
            let said = lp_i18n::t_format(
                "device.port {port}: set device.model as well",
                &[("port", Value::Str(port.into()))],
            );
            Err(DeviceError(said.unwrap_or_else(|why| why.msg)))
        }
    }
}

/// find_port: [`find_among`] the ports the system has.
pub fn find_port(model: Option<&'static PanelModel>, port: &str) -> Result<Option<FoundPort>> {
    find_among(&crate::serial::ports(), model, port)
}

/// free_panels among `ports`: the serial panels plugged in that no panel of this process has
/// open, by their USB ids (not the generic ones of a gadget), without opening any.
pub fn free_among(ports: &[PortInfo]) -> Vec<FoundPort> {
    ports
        .iter()
        .filter(|info| !place_taken(&info.device) && !generic(info))
        .filter_map(|info| found(info, known(info)))
        .collect()
}

pub fn free_panels() -> Vec<FoundPort> {
    free_among(&crate::serial::ports())
}

/// gadget_ports among `ports`: the ports that find_port leaves alone without a model: the
/// generic gadget ids of an awake rev. C panel, without its serial number (Windows may not
/// tell it). It may be the panel or another device; the person says which.
pub fn gadgets_among(ports: &[PortInfo]) -> Vec<FoundPort> {
    ports
        .iter()
        .filter(|info| generic(info))
        .filter_map(|info| found(info, known(info)))
        .collect()
}

pub fn gadget_ports() -> Vec<FoundPort> {
    gadgets_among(&crate::serial::ports())
}
