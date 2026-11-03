//! The wire to a panel: the bulk endpoints of a TURZX panel's interface 0 (nusb), or, for the
//! tests, a TCP socket to the simulated panel of tests/fake_panel.py (tests/engine_link.py).
//!
//! Both behave as pyusb does for devices/turzx_usb.py: a write is one bulk OUT transfer, a
//! read one bulk IN transfer of up to the length asked, and only a read that got nothing in
//! time is a timeout of its own (a write that times out has failed).

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;

use nusb::transfer::{Buffer, Bulk, In, Out, TransferError};
use nusb::MaybeFuture;

pub const VENDOR_ID: u16 = 0x1CBE;

#[derive(Debug)]
pub enum LinkError {
    /// A read that got nothing in time (pyusb's USBTimeoutError).
    Timeout,
    /// Anything else: the panel is gone, a write did not get through, a stalled endpoint.
    Failed(String),
}

impl std::fmt::Display for LinkError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LinkError::Timeout => write!(f, "timed out"),
            LinkError::Failed(why) => write!(f, "{why}"),
        }
    }
}

pub trait Link: Send {
    /// One bulk OUT transfer: the command packet and its payload together.
    fn write(&mut self, data: Vec<u8>, timeout_ms: u32) -> Result<(), LinkError>;
    /// One bulk IN transfer of up to `len` bytes (a whole number of packets).
    fn read(&mut self, len: usize, timeout_ms: u32) -> Result<Vec<u8>, LinkError>;
}

fn millis(timeout_ms: u32) -> Duration {
    Duration::from_millis(u64::from(timeout_ms.max(1)))
}

/// A TURZX panel on USB, opened as devices/turzx_usb.py opens it.
pub struct UsbLink {
    out: nusb::Endpoint<Bulk, Out>,
    input: nusb::Endpoint<Bulk, In>,
    _interface: nusb::Interface,
    /// Where it is, for the log: the bus and its ports.
    pub place: String,
}

fn ports_text(ports: &[u8]) -> String {
    ports
        .iter()
        .map(u8::to_string)
        .collect::<Vec<_>>()
        .join(".")
}

impl UsbLink {
    /// The panel of `pid` that Libre Panel picked: the one at `ports` (its port numbers from
    /// the root hub, pyusb's ``port_numbers``) or at `address` on bus `bus` (Linux numbers them
    /// as libusb does), else the only one of `pid` there is. Never one it cannot tell apart.
    pub fn open(
        pid: u16,
        ports: Option<&[u8]>,
        bus: Option<u8>,
        address: Option<u8>,
    ) -> Result<UsbLink, String> {
        let found: Vec<nusb::DeviceInfo> = nusb::list_devices()
            .wait()
            .map_err(|e| format!("cannot list the USB devices: {e}"))?
            .filter(|d| d.vendor_id() == VENDOR_ID && d.product_id() == pid)
            .collect();
        let at_ports: Vec<&nusb::DeviceInfo> = match ports {
            Some(ports) => found.iter().filter(|d| d.port_chain() == ports).collect(),
            None => Vec::new(),
        };
        let at_address: Vec<&nusb::DeviceInfo> = match address {
            Some(address) => found
                .iter()
                .filter(|d| d.device_address() == address && bus.is_none_or(|b| busnum(d) == b))
                .collect(),
            None => Vec::new(),
        };
        let info = if at_ports.len() == 1 {
            at_ports[0]
        } else if at_address.len() == 1 {
            at_address[0]
        } else if found.len() == 1 {
            &found[0]
        } else if found.is_empty() {
            return Err(format!("no panel with the product id {pid:04x} on USB"));
        } else {
            return Err(format!(
                "{} panels with the product id {pid:04x} and none at the place asked for",
                found.len()
            ));
        };
        let place = format!(
            "bus {} ports {} address {}",
            bus_text(info),
            ports_text(info.port_chain()),
            info.device_address()
        );
        let device = info
            .open()
            .wait()
            .map_err(|e| format!("cannot open the panel: {e}"))?;
        // As devices/turzx_usb.py: the kernel's driver let go (Linux), the first configuration
        // set (on Linux that resets the device's endpoints, as libusb does; Windows does not
        // let a program set it, as there), then interface 0 claimed for this program alone.
        let _ = device.detach_kernel_driver(0);
        if let Some(value) = device
            .configurations()
            .next()
            .map(|c| c.configuration_value())
        {
            let _ = device.set_configuration(value).wait();
        }
        let interface = device
            .claim_interface(0)
            .wait()
            .map_err(|e| format!("cannot open the panel: {e}"))?;
        let (mut out_address, mut in_address) = (None, None);
        if let Some(descriptor) = interface.descriptor() {
            for endpoint in descriptor.endpoints() {
                if endpoint.transfer_type() != nusb::descriptors::TransferType::Bulk {
                    continue;
                }
                match endpoint.direction() {
                    nusb::transfer::Direction::Out if out_address.is_none() => {
                        out_address = Some(endpoint.address())
                    }
                    nusb::transfer::Direction::In if in_address.is_none() => {
                        in_address = Some(endpoint.address())
                    }
                    _ => {}
                }
            }
        }
        let (Some(out_address), Some(in_address)) = (out_address, in_address) else {
            return Err("bulk endpoints not found".to_string());
        };
        let out = interface
            .endpoint::<Bulk, Out>(out_address)
            .map_err(|e| format!("cannot open the panel's endpoint: {e}"))?;
        let input = interface
            .endpoint::<Bulk, In>(in_address)
            .map_err(|e| format!("cannot open the panel's endpoint: {e}"))?;
        Ok(UsbLink {
            out,
            input,
            _interface: interface,
            place,
        })
    }
}

#[cfg(target_os = "linux")]
fn busnum(info: &nusb::DeviceInfo) -> u8 {
    info.busnum()
}

#[cfg(not(target_os = "linux"))]
fn busnum(_info: &nusb::DeviceInfo) -> u8 {
    0
}

#[cfg(target_os = "linux")]
fn bus_text(info: &nusb::DeviceInfo) -> String {
    info.busnum().to_string()
}

#[cfg(not(target_os = "linux"))]
fn bus_text(info: &nusb::DeviceInfo) -> String {
    info.bus_id().to_string()
}

fn failed(error: TransferError) -> LinkError {
    LinkError::Failed(match error {
        TransferError::Cancelled => "timed out".to_string(),
        other => other.to_string(),
    })
}

impl Link for UsbLink {
    fn write(&mut self, data: Vec<u8>, timeout_ms: u32) -> Result<(), LinkError> {
        let len = data.len();
        let done = self
            .out
            .transfer_blocking(Buffer::from(data), millis(timeout_ms));
        match done.status {
            Ok(()) if done.actual_len == len => Ok(()),
            Ok(()) => Err(LinkError::Failed(format!(
                "only {} of {len} bytes went out",
                done.actual_len
            ))),
            Err(TransferError::Stall) => {
                // libusb has WinUSB clear a stall by itself (AUTO_CLEAR_STALL); so here
                let _ = self.out.clear_halt().wait();
                Err(failed(TransferError::Stall))
            }
            Err(e) => Err(failed(e)),
        }
    }

    fn read(&mut self, len: usize, timeout_ms: u32) -> Result<Vec<u8>, LinkError> {
        let done = self
            .input
            .transfer_blocking(Buffer::new(len), millis(timeout_ms));
        match done.status {
            Ok(()) => {
                let mut data = done.buffer.into_vec();
                data.truncate(done.actual_len);
                Ok(data)
            }
            Err(TransferError::Cancelled) => Err(LinkError::Timeout),
            Err(TransferError::Stall) => {
                let _ = self.input.clear_halt().wait();
                Err(failed(TransferError::Stall))
            }
            Err(e) => Err(failed(e)),
        }
    }
}

/// The simulated panel of the tests, behind a socket: "W" len timeout data -> "K" or "E" len
/// text; "R" len timeout -> "D" len data, "T" (nothing in time) or "E" len text (u32 little
/// endian each).
pub struct TcpLink {
    stream: TcpStream,
}

impl TcpLink {
    pub fn connect(address: &str) -> Result<TcpLink, String> {
        let stream =
            TcpStream::connect(address).map_err(|e| format!("cannot reach {address}: {e}"))?;
        let _ = stream.set_nodelay(true);
        Ok(TcpLink { stream })
    }

    fn answer(&mut self) -> Result<(u8, Vec<u8>), LinkError> {
        let broken = |e: std::io::Error| LinkError::Failed(format!("the test link: {e}"));
        let mut kind = [0u8; 1];
        self.stream.read_exact(&mut kind).map_err(broken)?;
        if kind[0] == b'K' || kind[0] == b'T' {
            return Ok((kind[0], Vec::new()));
        }
        let mut len = [0u8; 4];
        self.stream.read_exact(&mut len).map_err(broken)?;
        let mut data = vec![0u8; u32::from_le_bytes(len) as usize];
        self.stream.read_exact(&mut data).map_err(broken)?;
        Ok((kind[0], data))
    }

    fn ask(&mut self, head: [u8; 9], data: &[u8]) -> Result<(u8, Vec<u8>), LinkError> {
        let broken = |e: std::io::Error| LinkError::Failed(format!("the test link: {e}"));
        self.stream.write_all(&head).map_err(broken)?;
        self.stream.write_all(data).map_err(broken)?;
        self.answer()
    }
}

fn head(kind: u8, len: usize, timeout_ms: u32) -> [u8; 9] {
    let mut head = [0u8; 9];
    head[0] = kind;
    head[1..5].copy_from_slice(&(len as u32).to_le_bytes());
    head[5..9].copy_from_slice(&timeout_ms.to_le_bytes());
    head
}

impl Link for TcpLink {
    fn write(&mut self, data: Vec<u8>, timeout_ms: u32) -> Result<(), LinkError> {
        match self.ask(head(b'W', data.len(), timeout_ms), &data)? {
            (b'K', _) => Ok(()),
            (_, text) => Err(LinkError::Failed(String::from_utf8_lossy(&text).into())),
        }
    }

    fn read(&mut self, len: usize, timeout_ms: u32) -> Result<Vec<u8>, LinkError> {
        match self.ask(head(b'R', len, timeout_ms), &[])? {
            (b'D', data) => Ok(data),
            (b'T', _) => Err(LinkError::Timeout),
            (_, text) => Err(LinkError::Failed(String::from_utf8_lossy(&text).into())),
        }
    }
}
