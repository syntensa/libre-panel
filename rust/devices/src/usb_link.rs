//! What the drivers of the USB screens of other makers share (devices/usb_link.py): finding a
//! screen by its USB ids, taking it for one panel of this process, claiming its interfaces,
//! finding their endpoints, and HID reports sent and read.
//!
//! A driver talks to its screen through [`UsbIo`]: on the bus that is [`NusbIo`] (usbfs on
//! Linux, WinUSB on Windows, without libusb, as the engine has the TURZX panels); in the tests
//! a screen they script.

use std::collections::HashMap;
use std::fmt;
use std::time::Duration;

use nusb::transfer::{Buffer, Bulk, ControlIn, ControlOut, ControlType, In, Interrupt, Out};
use nusb::transfer::{Recipient, TransferError};
use nusb::MaybeFuture;

use crate::base::{leave_place, take_place, tf, DeviceError, Result};

/// the interface class of a HID interface
pub const HID_CLASS: u8 = 3;

/// One endpoint of an interface, as its descriptor says.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EndpointInfo {
    pub address: u8,
    /// wMaxPacketSize's packet size (0 where it says none)
    pub max_packet: usize,
    /// an interrupt endpoint (else bulk)
    pub interrupt: bool,
}

/// One interface of the device's configuration.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InterfaceInfo {
    pub number: u8,
    pub class: u8,
    pub endpoints: Vec<EndpointInfo>,
}

/// What a transfer ended in where it did not go (usb.core.USBError).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum UsbError {
    /// no answer in time (usb.core.USBTimeoutError)
    Timeout,
    /// the device is busy: another program has it (errno 16)
    Busy,
    /// no right to it (errno 13)
    Denied,
    Other(String),
}

impl fmt::Display for UsbError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            UsbError::Timeout => f.write_str("[Errno 110] Operation timed out"),
            UsbError::Busy => f.write_str("[Errno 16] Resource busy"),
            UsbError::Denied => f.write_str("[Errno 13] Access denied (insufficient permissions)"),
            UsbError::Other(why) => f.write_str(why),
        }
    }
}

pub type UsbResult<T> = std::result::Result<T, UsbError>;

/// What a driver asks of its USB screen (pyusb's device as the drivers use it).
pub trait UsbIo: Send {
    /// the product string (none where it cannot be asked)
    fn product(&mut self) -> String;
    /// the interfaces of its configuration
    fn interfaces(&self) -> Vec<InterfaceInfo>;
    /// `interfaces` claimed, a kernel driver on them let go first
    fn claim(&mut self, interfaces: &[u8]) -> UsbResult<()>;
    /// `data` to the endpoint at `endpoint`, one transfer
    fn write(&mut self, endpoint: u8, data: &[u8], timeout_ms: u64) -> UsbResult<()>;
    /// up to `size` bytes from the endpoint at `endpoint`, one transfer
    fn read(&mut self, endpoint: u8, size: usize, timeout_ms: u64) -> UsbResult<Vec<u8>>;
    /// a control transfer to the device (ctrl_transfer with data)
    fn control_out(
        &mut self,
        request_type: u8,
        request: u8,
        value: u16,
        index: u16,
        data: &[u8],
        timeout_ms: u64,
    ) -> UsbResult<()>;
    /// a control transfer from the device (ctrl_transfer with a length)
    fn control_in(
        &mut self,
        request_type: u8,
        request: u8,
        value: u16,
        index: u16,
        length: u16,
        timeout_ms: u64,
    ) -> UsbResult<Vec<u8>>;
    /// the device let go; safe to call twice
    fn release(&mut self);
}

/// access_hint: the usual reasons a panel cannot be opened, per operating system.
pub fn access_hint(error: &UsbError) -> Option<String> {
    let text = error.to_string().to_lowercase();
    let busy = *error == UsbError::Busy || text.contains("busy");
    let denied =
        *error == UsbError::Denied || text.contains("access") || text.contains("permission");
    if cfg!(windows) && (busy || denied) {
        return Some(lp_i18n::t(
            "the panel is in use by another program, usually the TURZX app. Quit it (tray icon -> \
             Exit) and try again.",
        ));
    }
    if busy {
        return Some(lp_i18n::t(
            "the panel is in use by another program (TURZX or other monitor software).",
        ));
    }
    if denied && cfg!(target_os = "linux") {
        return Some(lp_i18n::t(
            "no permission to use the panel. Install the udev rule: libre-panel udev-rules | \
             sudo tee /etc/udev/rules.d/60-libre-panel.rules && sudo udevadm control \
             --reload-rules, then replug the panel.",
        ));
    }
    if denied {
        return Some(lp_i18n::t("no permission to use the panel."));
    }
    None
}

/// claim: `interfaces` claimed, as a DeviceError where they cannot be.
pub fn claim(io: &mut dyn UsbIo, interfaces: &[u8]) -> Result<()> {
    io.claim(interfaces).map_err(|e| {
        DeviceError(access_hint(&e).unwrap_or_else(|| format!("cannot open the panel: {e}")))
    })
}

/// hid_interface: the number of the device's HID interface; None for none.
pub fn hid_interface(io: &dyn UsbIo) -> Option<u8> {
    io.interfaces()
        .into_iter()
        .find(|interface| interface.class == HID_CLASS)
        .map(|interface| interface.number)
}

/// packet_size: the largest packet of the endpoint at `address`; `default` where the device
/// does not say.
pub fn packet_size(io: &dyn UsbIo, address: u8, default: usize) -> usize {
    for interface in io.interfaces() {
        for endpoint in interface.endpoints {
            if endpoint.address == address {
                let size = endpoint.max_packet & 0x7FF;
                return if size == 0 { default } else { size };
            }
        }
    }
    default
}

/// endpoints: the first OUT and the first IN endpoint of interface `number` (their addresses).
pub fn endpoints(io: &dyn UsbIo, number: u8) -> (Option<u8>, Option<u8>) {
    for interface in io.interfaces() {
        if interface.number == number {
            let addresses: Vec<u8> = interface.endpoints.iter().map(|e| e.address).collect();
            let out = addresses.iter().copied().find(|a| a & 0x80 == 0);
            let into = addresses.iter().copied().find(|a| a & 0x80 != 0);
            return (out, into);
        }
    }
    (None, None)
}

/// Reports of one size to and from a device's HID interface (HidReports): written to its
/// interrupt OUT endpoint (with SET_REPORT where it has none), read from its interrupt IN
/// endpoint. A device without report ids (`ids` false) takes them as they are; one with ids
/// has its id in byte 0.
#[derive(Clone, Debug)]
pub struct HidReports {
    pub interface: u8,
    pub size: usize,
    pub ids: bool,
    pub out: Option<u8>,
    pub into: Option<u8>,
}

impl HidReports {
    pub fn new(io: &dyn UsbIo, interface: u8, size: usize, ids: bool) -> HidReports {
        let (out, into) = endpoints(io, interface);
        HidReports {
            interface,
            size,
            ids,
            out,
            into,
        }
    }

    pub fn write(&self, io: &mut dyn UsbIo, report: &[u8], timeout_ms: u64) -> UsbResult<()> {
        let mut data = report.to_vec();
        if data.len() < self.size {
            data.resize(self.size, 0);
        }
        match self.out {
            Some(out) => io.write(out, &data, timeout_ms),
            None => {
                // SET_REPORT, an output report
                let id = if self.ids { u16::from(data[0]) } else { 0 };
                let index = u16::from(self.interface);
                io.control_out(0x21, 0x09, 0x0200 | id, index, &data, timeout_ms)
            }
        }
    }

    /// The next input report; None where none came in time.
    pub fn read(&self, io: &mut dyn UsbIo, timeout_ms: u64) -> UsbResult<Option<Vec<u8>>> {
        let Some(into) = self.into else {
            return Ok(None);
        };
        match io.read(into, self.size, timeout_ms) {
            Ok(data) => Ok(Some(data)),
            Err(UsbError::Timeout) => Ok(None),
            Err(e) => Err(e),
        }
    }
}

// -- on the bus ----------------------------------------------------------------------------

/// A device on the bus with one of the ids looked for.
#[derive(Clone, Debug)]
pub struct Found {
    pub info: nusb::DeviceInfo,
    pub product_id: u16,
}

impl Found {
    /// place: where it is on the bus, for take_place ("usb:<bus>.<address>", as for the TURZX
    /// panels).
    pub fn place(&self) -> String {
        #[cfg(target_os = "linux")]
        let bus = self.info.busnum().to_string();
        #[cfg(not(target_os = "linux"))]
        let bus = self.info.bus_id().to_string();
        format!("usb:{bus}.{}", self.info.device_address())
    }

    /// The product string as the system listed it, else asked of the device.
    pub fn product(&self) -> String {
        if let Some(product) = self.info.product_string() {
            return product.to_string();
        }
        match self.info.get_product_string().wait() {
            Ok(Some(product)) => product,
            _ => String::new(),
        }
    }
}

/// find: the devices on the bus with one of these ids, each with its product id, in the order
/// of `products`.
pub fn find(vendor: u16, products: &[u16]) -> Result<Vec<Found>> {
    let devices: Vec<nusb::DeviceInfo> = nusb::list_devices()
        .wait()
        .map_err(|e| DeviceError(format!("the USB devices cannot be listed: {e}")))?
        .collect();
    let mut found = Vec::new();
    for &product in products {
        for info in &devices {
            if info.vendor_id() == vendor && info.product_id() == product {
                found.push(Found {
                    info: info.clone(),
                    product_id: product,
                });
            }
        }
    }
    Ok(found)
}

/// present: the product id of the first such device on the bus (none is opened); None for
/// none.
pub fn present(vendor: u16, products: &[u16]) -> Option<u16> {
    find(vendor, products)
        .ok()?
        .first()
        .map(|found| found.product_id)
}

/// take: the first such device that no panel of this process has open, taken for this one and
/// opened: the device, its product id and its place (left again by its release).
pub fn take(vendor: u16, products: &[u16], label: &str) -> Result<(NusbIo, u16)> {
    let found = find(vendor, products)?;
    if found.is_empty() {
        return Err(DeviceError(tf(
            "no {panel} on the USB bus",
            &[("panel", label)],
        )));
    }
    for candidate in found {
        let place = candidate.place();
        if !take_place(&place) {
            continue;
        }
        return match NusbIo::open(&candidate, place.clone()) {
            Ok(io) => Ok((io, candidate.product_id)),
            Err(e) => {
                leave_place(&place);
                Err(DeviceError(
                    access_hint(&e).unwrap_or_else(|| format!("cannot open the panel: {e}")),
                ))
            }
        };
    }
    Err(DeviceError(tf(
        "another panel of Libre Panel has the {panel} open",
        &[("panel", label)],
    )))
}

fn usb_error(e: nusb::Error) -> UsbError {
    match e.kind() {
        nusb::ErrorKind::Busy => UsbError::Busy,
        nusb::ErrorKind::PermissionDenied => UsbError::Denied,
        _ => UsbError::Other(e.to_string()),
    }
}

fn transfer_error(e: TransferError) -> UsbError {
    match e {
        TransferError::Cancelled => UsbError::Timeout,
        other => UsbError::Other(other.to_string()),
    }
}

enum Endpoint {
    BulkOut(nusb::Endpoint<Bulk, Out>),
    BulkIn(nusb::Endpoint<Bulk, In>),
    InterruptOut(nusb::Endpoint<Interrupt, Out>),
    InterruptIn(nusb::Endpoint<Interrupt, In>),
}

/// A screen on the bus, opened through nusb.
pub struct NusbIo {
    product: String,
    device: Option<nusb::Device>,
    interfaces: Vec<InterfaceInfo>,
    claimed: HashMap<u8, nusb::Interface>,
    endpoints: HashMap<u8, Endpoint>,
    place: String,
}

impl NusbIo {
    fn open(found: &Found, place: String) -> UsbResult<NusbIo> {
        let product = found.product();
        let device = found.info.open().wait().map_err(usb_error)?;
        let interfaces = match device.active_configuration() {
            Ok(config) => config
                .interfaces()
                .filter_map(|group| {
                    let alt = group.alt_settings().next()?;
                    Some(InterfaceInfo {
                        number: alt.interface_number(),
                        class: alt.class(),
                        endpoints: alt
                            .endpoints()
                            .map(|e| EndpointInfo {
                                address: e.address(),
                                max_packet: e.max_packet_size(),
                                interrupt: e.transfer_type()
                                    == nusb::descriptors::TransferType::Interrupt,
                            })
                            .collect(),
                    })
                })
                .collect(),
            Err(_) => Vec::new(),
        };
        Ok(NusbIo {
            product,
            device: Some(device),
            interfaces,
            claimed: HashMap::new(),
            endpoints: HashMap::new(),
            place,
        })
    }

    fn interface_of(&self, address: u8) -> Option<(u8, bool)> {
        self.interfaces.iter().find_map(|interface| {
            interface
                .endpoints
                .iter()
                .find(|e| e.address == address)
                .map(|e| (interface.number, e.interrupt))
        })
    }

    fn endpoint(&mut self, address: u8) -> UsbResult<&mut Endpoint> {
        if !self.endpoints.contains_key(&address) {
            let Some((number, interrupt)) = self.interface_of(address) else {
                return Err(UsbError::Other(format!("no endpoint 0x{address:02x}")));
            };
            let Some(interface) = self.claimed.get(&number) else {
                return Err(UsbError::Other(format!("interface {number} not claimed")));
            };
            let into = address & 0x80 != 0;
            let opened = match (interrupt, into) {
                (false, false) => interface
                    .endpoint::<Bulk, Out>(address)
                    .map(Endpoint::BulkOut),
                (false, true) => interface
                    .endpoint::<Bulk, In>(address)
                    .map(Endpoint::BulkIn),
                (true, false) => interface
                    .endpoint::<Interrupt, Out>(address)
                    .map(Endpoint::InterruptOut),
                (true, true) => interface
                    .endpoint::<Interrupt, In>(address)
                    .map(Endpoint::InterruptIn),
            };
            self.endpoints.insert(address, opened.map_err(usb_error)?);
        }
        Ok(self.endpoints.get_mut(&address).expect("just put there"))
    }

    /// Where a control transfer goes: a claimed interface (Windows has no other way), else
    /// the device.
    fn control_interface(&self) -> Option<&nusb::Interface> {
        self.claimed.values().next()
    }
}

fn control_type(request_type: u8) -> (ControlType, Recipient) {
    let kind = match (request_type >> 5) & 3 {
        0 => ControlType::Standard,
        1 => ControlType::Class,
        _ => ControlType::Vendor,
    };
    let recipient = match request_type & 0x1F {
        0 => Recipient::Device,
        1 => Recipient::Interface,
        2 => Recipient::Endpoint,
        _ => Recipient::Other,
    };
    (kind, recipient)
}

impl UsbIo for NusbIo {
    fn product(&mut self) -> String {
        self.product.clone()
    }

    fn interfaces(&self) -> Vec<InterfaceInfo> {
        self.interfaces.clone()
    }

    fn claim(&mut self, interfaces: &[u8]) -> UsbResult<()> {
        let Some(device) = self.device.as_ref() else {
            return Err(UsbError::Other("the device is let go".into()));
        };
        for &number in interfaces {
            if self.claimed.contains_key(&number) {
                continue;
            }
            let interface = device
                .detach_and_claim_interface(number)
                .wait()
                .map_err(usb_error)?;
            self.claimed.insert(number, interface);
        }
        Ok(())
    }

    fn write(&mut self, endpoint: u8, data: &[u8], timeout_ms: u64) -> UsbResult<()> {
        let timeout = Duration::from_millis(timeout_ms);
        let mut buffer = Buffer::new(data.len());
        buffer.extend_from_slice(data);
        let done = match self.endpoint(endpoint)? {
            Endpoint::BulkOut(e) => e.transfer_blocking(buffer, timeout),
            Endpoint::InterruptOut(e) => e.transfer_blocking(buffer, timeout),
            _ => {
                return Err(UsbError::Other(format!(
                    "0x{endpoint:02x} is no OUT endpoint"
                )))
            }
        };
        done.status.map_err(transfer_error)
    }

    fn read(&mut self, endpoint: u8, size: usize, timeout_ms: u64) -> UsbResult<Vec<u8>> {
        let timeout = Duration::from_millis(timeout_ms);
        let done = match self.endpoint(endpoint)? {
            Endpoint::BulkIn(e) => {
                let packet = e.max_packet_size().max(1);
                e.transfer_blocking(Buffer::new(size.max(1).div_ceil(packet) * packet), timeout)
            }
            Endpoint::InterruptIn(e) => {
                let packet = e.max_packet_size().max(1);
                e.transfer_blocking(Buffer::new(size.max(1).div_ceil(packet) * packet), timeout)
            }
            _ => {
                return Err(UsbError::Other(format!(
                    "0x{endpoint:02x} is no IN endpoint"
                )))
            }
        };
        let mut data = done.into_result().map_err(transfer_error)?.into_vec();
        data.truncate(size);
        Ok(data)
    }

    fn control_out(
        &mut self,
        request_type: u8,
        request: u8,
        value: u16,
        index: u16,
        data: &[u8],
        timeout_ms: u64,
    ) -> UsbResult<()> {
        let (control_type, recipient) = control_type(request_type);
        let transfer = ControlOut {
            control_type,
            recipient,
            request,
            value,
            index,
            data,
        };
        let timeout = Duration::from_millis(timeout_ms);
        if let Some(interface) = self.control_interface() {
            return interface
                .control_out(transfer, timeout)
                .wait()
                .map_err(transfer_error);
        }
        #[cfg(not(windows))]
        if let Some(device) = self.device.as_ref() {
            return device
                .control_out(transfer, timeout)
                .wait()
                .map_err(transfer_error);
        }
        Err(UsbError::Other(
            "no interface claimed for a control transfer".into(),
        ))
    }

    fn control_in(
        &mut self,
        request_type: u8,
        request: u8,
        value: u16,
        index: u16,
        length: u16,
        timeout_ms: u64,
    ) -> UsbResult<Vec<u8>> {
        let (control_type, recipient) = control_type(request_type);
        let transfer = ControlIn {
            control_type,
            recipient,
            request,
            value,
            index,
            length,
        };
        let timeout = Duration::from_millis(timeout_ms);
        if let Some(interface) = self.control_interface() {
            return interface
                .control_in(transfer, timeout)
                .wait()
                .map_err(transfer_error);
        }
        #[cfg(not(windows))]
        if let Some(device) = self.device.as_ref() {
            return device
                .control_in(transfer, timeout)
                .wait()
                .map_err(transfer_error);
        }
        Err(UsbError::Other(
            "no interface claimed for a control transfer".into(),
        ))
    }

    fn release(&mut self) {
        self.endpoints.clear();
        self.claimed.clear();
        self.device = None;
        if !self.place.is_empty() {
            leave_place(&std::mem::take(&mut self.place));
        }
    }
}

impl Drop for NusbIo {
    fn drop(&mut self) {
        self.release();
    }
}

/// Where a screen's pictures go: an endpoint, or HID reports.
#[derive(Clone, Debug)]
pub enum Target {
    Endpoint(u8),
    Hid(HidReports),
}

/// What a keeper guards for a USB screen that shows its own picture again when it gets none
/// for a while: the link, and the messages of the last picture, sent again as they went.
pub struct Screen {
    pub io: Option<Box<dyn UsbIo>>,
    pub label: String,
    pub counters: crate::base::Counters,
    pub target: Target,
    pub timeout_ms: u64,
    pub last: Vec<Vec<u8>>,
}

impl Screen {
    pub fn new(label: String, target: Target, timeout_ms: u64) -> Screen {
        Screen {
            io: None,
            label,
            counters: crate::base::Counters::default(),
            target,
            timeout_ms,
            last: Vec::new(),
        }
    }

    /// The error of a link that failed while showing (the main loop connects again).
    pub fn stopped(&mut self, e: &UsbError) -> DeviceError {
        self.counters.usb_errors += 1;
        DeviceError(tf(
            "the {panel} stopped answering: {error}",
            &[("panel", &self.label), ("error", &e.to_string())],
        ))
    }

    /// One message to the target.
    pub fn write(&mut self, message: &[u8]) -> UsbResult<()> {
        let Some(io) = self.io.as_mut() else {
            return Err(UsbError::Other("the device is let go".into()));
        };
        match &self.target {
            Target::Endpoint(endpoint) => io.write(*endpoint, message, self.timeout_ms),
            Target::Hid(reports) => reports.write(io.as_mut(), message, self.timeout_ms),
        }
    }

    /// A picture's messages, in order, counted and kept to go again.
    pub fn picture(&mut self, messages: Vec<Vec<u8>>) -> Result<()> {
        for message in &messages {
            if let Err(e) = self.write(message) {
                return Err(self.stopped(&e));
            }
        }
        self.counters.bytes += messages.iter().map(|m| m.len() as u64).sum::<u64>();
        self.last = messages;
        Ok(())
    }

    /// The last picture again (the keeper's).
    pub fn again(screen: &mut Screen) -> Result<()> {
        if screen.io.is_some() && !screen.last.is_empty() {
            let last = screen.last.clone();
            return screen.picture(last);
        }
        Ok(())
    }
}
