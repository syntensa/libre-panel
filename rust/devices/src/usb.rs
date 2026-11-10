//! The TURZX USB panels on the bus, and which of them a panel takes (devices/turzx_usb.py's
//! _pick, present_model, free_models and UsbTransport.present).

use nusb::MaybeFuture;

use crate::base::place_taken;
use crate::models::{find_model, PanelModel};
use crate::turzx::{usb_models, VENDOR_ID};

/// A TURZX USB panel that is on the bus.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Found {
    pub pid: u16,
    /// the bus as the system names it
    pub bus: String,
    /// its number, where the system numbers its buses (Linux, as libusb does)
    pub busnum: Option<u8>,
    pub address: u8,
    /// its port numbers from the root hub
    pub ports: Vec<u8>,
}

impl Found {
    /// _place: where it is on the bus, for take_place.
    pub fn place(&self) -> String {
        format!("usb:{}.{}", self.bus, self.address)
    }
}

#[cfg(target_os = "linux")]
fn busnum(info: &nusb::DeviceInfo) -> Option<u8> {
    Some(info.busnum())
}

#[cfg(not(target_os = "linux"))]
fn busnum(_info: &nusb::DeviceInfo) -> Option<u8> {
    None
}

/// The TURZX panels on the bus, as the system lists them; none where it cannot list.
pub fn list() -> Vec<Found> {
    let Ok(devices) = nusb::list_devices().wait() else {
        return Vec::new();
    };
    devices
        .filter(|device| device.vendor_id() == VENDOR_ID)
        .map(|device| {
            let number = busnum(&device);
            Found {
                pid: device.product_id(),
                bus: number.map_or_else(|| device.bus_id().to_string(), |n| n.to_string()),
                busnum: number,
                address: device.device_address(),
                ports: device.port_chain().to_vec(),
            }
        })
        .collect()
}

/// UsbTransport.present: whether a panel with this product id is on the bus.
pub fn present(pid: u16) -> bool {
    list().iter().any(|device| device.pid == pid)
}

/// _pick: the device a panel takes among `devices`: of `pid`, else of any TURZX model
/// ("auto"), one that no panel of this process has open (`free`; else any there is). With
/// "auto" the models other panels name (`beside`) come last, and such a one is left to them
/// unless the bus has more of it than they name: the 12.3" set to "auto" beside a 9.2" never
/// takes the 9.2". `skip` passes over so many that would do first.
pub fn pick<'a>(
    pid: Option<u16>,
    beside: &[String],
    devices: &'a [Found],
    free: bool,
    skip: usize,
) -> Option<&'a Found> {
    let mut named: Vec<u16> = Vec::new();
    for model_id in beside {
        if let Some(model) = find_model(model_id) {
            named.extend(model.usb_ids.iter().map(|(_, pid)| *pid));
        }
    }
    let count = |pid: u16| named.iter().filter(|other| **other == pid).count();
    let pids: Vec<u16> = match pid {
        Some(pid) => vec![pid],
        None => {
            let mut all: Vec<u16> = Vec::new();
            for (pid, _) in usb_models() {
                if !all.contains(&pid) {
                    all.push(pid);
                }
            }
            // (those other panels name last; the others in the catalog's order)
            all.sort_by_key(|pid| count(*pid) > 0);
            all
        }
    };
    let mut skip = skip;
    for candidate in pids {
        let found: Vec<&Found> = devices.iter().filter(|d| d.pid == candidate).collect();
        if pid.is_none() && found.len() <= count(candidate) {
            continue; // another panel's
        }
        // one that another panel of this process has open is passed by ([[panels]])
        let open: Vec<&Found> = found
            .into_iter()
            .filter(|device| !(free && place_taken(&device.place())))
            .collect();
        if skip >= open.len() {
            skip -= open.len();
            continue;
        }
        return Some(open[skip]);
    }
    None
}

/// The model a product id is (USB_PIDS).
pub fn model_of(pid: u16) -> Option<&'static PanelModel> {
    usb_models()
        .find(|(id, _)| *id == pid)
        .map(|(_, model)| model)
}

/// present_model: the model of the USB panel a panel set to "auto" would take (its own may
/// be open), without opening it.
pub fn present_model(beside: &[String], skip: usize) -> Option<&'static PanelModel> {
    pick(None, beside, &list(), false, skip).and_then(|device| model_of(device.pid))
}

/// free_models: the USB panels on the bus that no panel of this process has open.
pub fn free_models() -> Vec<&'static PanelModel> {
    let devices = list();
    let mut pids: Vec<u16> = Vec::new();
    for (pid, _) in usb_models() {
        if !pids.contains(&pid) {
            pids.push(pid);
        }
    }
    let mut found = Vec::new();
    for pid in pids {
        for device in devices.iter().filter(|d| d.pid == pid) {
            if !place_taken(&device.place()) {
                found.extend(model_of(pid));
            }
        }
    }
    found
}
