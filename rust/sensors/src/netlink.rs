//! How the PC is connected to the network (sensors/netlink.py): by cable (LAN) or Wi-Fi, how
//! fast, and how strong the Wi-Fi's signal is.
//!
//! The connection is the one the PC reaches the internet through (its default route), not
//! every adapter there is:
//! - Linux: the default route's interface (`/proc/net/route`); Wi-Fi where it has a `wireless`
//!   folder in `/sys/class/net`, its signal from `/proc/net/wireless` (the link's quality, of
//!   70) and its rate from `iw dev <name> link` where `iw` is installed; a cable's speed from
//!   `/sys/class/net/<name>/speed`. Nothing is started but `iw`.
//! - Windows: the adapter Windows would send to the internet by (`GetBestInterface`), its type
//!   and speed (`GetIfEntry`: IEEE 802.11 is Wi-Fi); the Wi-Fi's signal and rate from the WLAN
//!   service. No program is started.

pub const LAN: &str = "lan";
pub const WLAN: &str = "wlan";

#[derive(Clone, Debug, PartialEq)]
pub struct Link {
    /// LAN or WLAN
    pub kind: &'static str,
    /// Mbit/s
    pub speed: Option<f64>,
    /// %, Wi-Fi only
    pub signal: Option<f64>,
    /// the interface's
    pub name: String,
}

/// speed_text: a link's speed as one says it: 866 Mbit/s, 2.5 Gbit/s.
pub fn speed_text(mbit: f64) -> String {
    if mbit >= 1000.0 {
        return format!("{:.1} Gbit/s", mbit / 1000.0).replace(".0 ", " ");
    }
    format!("{mbit:.0} Mbit/s")
}

/// str.split(): the parts between runs of Python's white space.
fn fields(line: &str) -> Vec<&str> {
    line.split(lp_py::isspace)
        .filter(|part| !part.is_empty())
        .collect()
}

/// default_interface: the interface of the default route in `/proc/net/route`'s text (the
/// lowest metric).
pub fn default_interface(route: &str) -> Option<String> {
    let mut best: Option<(i128, &str)> = None;
    for line in lp_py::splitlines(route).into_iter().skip(1) {
        let fields = fields(line);
        if fields.len() >= 7 && fields[1] == "00000000" {
            // (str.isdigit, then int: digits of any script; a metric that is none is 0)
            let digits = !fields[6].is_empty() && fields[6].chars().all(lp_py::isdigit);
            let metric = if digits {
                lp_py::int_of_digits(fields[6])?
            } else {
                0
            };
            if best.is_none_or(|(least, _)| metric < least) {
                best = Some((metric, fields[0]));
            }
        }
    }
    best.map(|(_, name)| name.to_string())
}

/// wireless_quality: the link quality of `name` in `/proc/net/wireless`'s text, in % (of 70);
/// an error where the quality is no number (Python raises there, and no link is told).
pub fn wireless_quality(text: &str, name: &str) -> Result<Option<f64>, String> {
    for line in lp_py::splitlines(text).into_iter().skip(2) {
        let (head, rest) = line.split_once(':').unwrap_or((line, ""));
        if lp_py::strip(head) == name {
            let fields = fields(rest);
            if fields.len() >= 2 {
                let said = fields[1].trim_end_matches('.');
                let quality = lp_py::float_of_str(said)
                    .ok_or_else(|| format!("could not convert string to float: {said:?}"))?;
                return Ok(Some((quality * 100.0 / 70.0).clamp(0.0, 100.0)));
            }
        }
    }
    Ok(None)
}

/// The rate in `iw dev <name> link`'s words ("tx bitrate: 866.7 MBit/s"), whatever the case
/// of its letters.
pub fn iw_rate(out: &str) -> Option<f64> {
    use std::sync::LazyLock;
    static RATE: LazyLock<lp_py::regex::Regex> = LazyLock::new(|| {
        lp_py::regex::Regex::new(r"tx bitrate:\s*([\d.]+)\s*MBit/s", true).expect("the pattern")
    });
    let chars: Vec<char> = out.chars().collect();
    let found = RATE.search(&chars)?;
    lp_py::float_of_str(&found.group(&chars, 1)?)
}

#[cfg(target_os = "linux")]
fn system() -> Option<Link> {
    use std::path::Path;
    let name = default_interface(&std::fs::read_to_string("/proc/net/route").ok()?)?;
    let net = Path::new("/sys/class/net").join(&name);
    if net.join("wireless").exists() {
        let signal = match std::fs::read_to_string("/proc/net/wireless") {
            Ok(text) => wireless_quality(&text, &name).ok()?,
            Err(_) => None,
        };
        let rate = std::process::Command::new("iw")
            .args(["dev", &name, "link"])
            .env("LC_ALL", "C")
            .stdin(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .output()
            .ok()
            .and_then(|done| iw_rate(&String::from_utf8_lossy(&done.stdout)));
        return Some(Link {
            kind: WLAN,
            speed: rate,
            signal,
            name,
        });
    }
    let speed = std::fs::read_to_string(net.join("speed"))
        .ok()
        .and_then(|text| lp_py::float_of_str(lp_py::strip(&text)))
        .filter(|speed| *speed > 0.0);
    Some(Link {
        kind: LAN,
        speed,
        signal: None,
        name,
    })
}

#[cfg(windows)]
fn system() -> Option<Link> {
    use windows_sys::Win32::NetworkManagement::IpHelper::{
        GetBestInterface, GetIfEntry, MIB_IFROW,
    };
    /// 8.8.8.8: only the route to it is looked up, nothing is sent
    const INTERNET: u32 = 0x0808_0808;
    const IF_TYPE_IEEE80211: u32 = 71;
    // SAFETY: the index and the row are written into this frame's own.
    let row = unsafe {
        let mut index = 0u32;
        if GetBestInterface(INTERNET, &mut index) != 0 {
            return None;
        }
        let mut row: MIB_IFROW = std::mem::zeroed();
        row.dwIndex = index;
        if GetIfEntry(&mut row) != 0 {
            return None;
        }
        row
    };
    let speed = (0 < row.dwSpeed && row.dwSpeed < u32::MAX).then(|| f64::from(row.dwSpeed) / 1e6);
    if row.dwType != IF_TYPE_IEEE80211 {
        return Some(Link {
            kind: LAN,
            speed,
            signal: None,
            name: String::new(),
        });
    }
    let (signal, rate) = wlan().unwrap_or((None, None));
    Some(Link {
        kind: WLAN,
        speed: rate.filter(|rate| *rate != 0.0).or(speed),
        signal,
        name: String::new(),
    })
}

/// The connected Wi-Fi's signal (%) and receive rate (Mbit/s), from the WLAN service.
#[cfg(windows)]
fn wlan() -> Option<(Option<f64>, Option<f64>)> {
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::NetworkManagement::WiFi::{
        wlan_interface_state_connected, wlan_intf_opcode_current_connection, WlanCloseHandle,
        WlanEnumInterfaces, WlanFreeMemory, WlanOpenHandle, WlanQueryInterface,
        WLAN_CONNECTION_ATTRIBUTES, WLAN_INTERFACE_INFO_LIST,
    };
    // SAFETY: the list and the attributes are the WLAN service's, read while they live and
    // freed with WlanFreeMemory; the handle is closed at the end.
    unsafe {
        let mut handle: HANDLE = std::ptr::null_mut();
        let mut version = 0u32;
        if WlanOpenHandle(2, std::ptr::null(), &mut version, &mut handle) != 0 {
            return None;
        }
        let mut listed: *mut WLAN_INTERFACE_INFO_LIST = std::ptr::null_mut();
        let mut found = None;
        if WlanEnumInterfaces(handle, std::ptr::null(), &mut listed) == 0 && !listed.is_null() {
            let count = (*listed).dwNumberOfItems as usize;
            let infos = std::slice::from_raw_parts((*listed).InterfaceInfo.as_ptr(), count);
            for info in infos {
                if info.isState != wlan_interface_state_connected {
                    continue;
                }
                let mut size = 0u32;
                let mut data: *mut core::ffi::c_void = std::ptr::null_mut();
                if WlanQueryInterface(
                    handle,
                    &info.InterfaceGuid,
                    wlan_intf_opcode_current_connection,
                    std::ptr::null(),
                    &mut size,
                    &mut data,
                    std::ptr::null_mut(),
                ) != 0
                    || data.is_null()
                {
                    continue;
                }
                let association =
                    &(*data.cast::<WLAN_CONNECTION_ATTRIBUTES>()).wlanAssociationAttributes;
                found = Some((
                    Some(f64::from(association.wlanSignalQuality)),
                    Some(f64::from(association.ulRxRate) / 1000.0),
                ));
                WlanFreeMemory(data);
                break;
            }
        }
        if !listed.is_null() {
            WlanFreeMemory(listed.cast());
        }
        WlanCloseHandle(handle, std::ptr::null());
        Some(found.unwrap_or((None, None)))
    }
}

#[cfg(not(any(target_os = "linux", windows)))]
fn system() -> Option<Link> {
    None
}

/// read: the connection now, none where it cannot be told (no network, another system).
pub fn read() -> Option<Link> {
    system()
}
