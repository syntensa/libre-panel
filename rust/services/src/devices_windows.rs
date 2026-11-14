//! What Windows can tell about drives, USB devices, game controllers and storage: the
//! Windows half of the `devices` service (services/devices_windows.py).
//!
//! Everything here only asks. No device is opened for data, nothing is written, and the USB
//! devices are listed from the system's own table, not opened (the panel's own device is
//! among them, and Libre Panel never opens it for this).

use std::collections::HashSet;

use lp_py::{Dict, Value};

use crate::devices::{Notifier, Readers};

/// a fixed drive on one of these buses is an external one (a USB SSD says it is "fixed");
/// the internal ones (SATA, NVMe: 17) are not told about
pub fn external_bus(bus: Option<u32>) -> Option<&'static str> {
    match bus? {
        7 => Some("USB"),
        12 => Some("SD"),
        13 => Some("MMC"),
        _ => None,
    }
}

/// hubs, mass storage (the drives are told by the drive readers) and the drivers of Xbox
/// controllers (XInput tells those) are not USB devices to tell about
const SKIPPED_SERVICES: [&str; 6] = [
    "usbhub", "usbhub3", "usbstor", "uaspstor", "xusb22", "xboxgip",
];

fn u32_at(raw: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_le_bytes(raw.get(at..at + 4)?.try_into().ok()?))
}

fn i16_at(raw: &[u8], at: usize) -> Option<i16> {
    Some(i16::from_le_bytes(raw.get(at..at + 2)?.try_into().ok()?))
}

/// parse_device: (bus type, "Maker Product") from a STORAGE_DEVICE_DESCRIPTOR.
pub fn parse_device(raw: &[u8]) -> (Option<u32>, String) {
    if raw.len() < 36 {
        return (None, String::new());
    }
    let text = |offset: Option<u32>| -> String {
        let offset = offset.unwrap_or(0) as usize;
        if offset == 0 || offset >= raw.len() {
            return String::new();
        }
        let end = raw[offset..].iter().position(|byte| *byte == 0);
        let bytes = &raw[offset..end.map_or(raw.len(), |end| offset + end)];
        // (ASCII with what is none replaced)
        let text: String = bytes
            .iter()
            .map(|byte| {
                if byte.is_ascii() {
                    *byte as char
                } else {
                    '\u{fffd}'
                }
            })
            .collect();
        lp_py::strip(&text).to_string()
    };
    let parts = [text(u32_at(raw, 12)), text(u32_at(raw, 16))];
    let named: Vec<&str> = (parts.iter().map(String::as_str))
        .filter(|part| !part.is_empty())
        .collect();
    (u32_at(raw, 28), named.join(" "))
}

/// The temperature, the warning limit and the critical limit of a drive, in °C.
pub type Heat = (Option<f64>, Option<f64>, Option<f64>);

/// parse_temperature: (temperature, warning limit, critical limit) in °C from a
/// STORAGE_TEMPERATURE_DATA_DESCRIPTOR; None for what it does not say.
pub fn parse_temperature(raw: &[u8]) -> Heat {
    if raw.len() < 40 {
        return (None, None, None);
    }
    let critical = i16_at(raw, 8).unwrap_or(0);
    let warning = i16_at(raw, 10).unwrap_or(0);
    let count = i16_at(raw, 12).unwrap_or(0) as u16;
    let mut highest: Option<i16> = None;
    for i in 0..usize::from(count) {
        // STORAGE_TEMPERATURE_INFO, 16 bytes each
        let offset = 24 + 16 * i;
        if offset + 4 > raw.len() {
            break;
        }
        let value = i16_at(raw, offset + 2).unwrap_or(0);
        if -40 < value && value < 150 && highest.is_none_or(|highest| value > highest) {
            highest = Some(value);
        }
    }
    let limit = |value: i16| (0 < value && value < 150).then(|| f64::from(value));
    (highest.map(f64::from), limit(warning), limit(critical))
}

/// What a top-level USB device is called: `USB\VID_xxxx&PID_xxxx\<serial or port>`; the
/// parts of a composite device (...&MI_00) have more in the middle and do not match. Its
/// vendor and product ids in upper case.
pub fn top_level(ident: &str) -> Option<(String, String)> {
    let chars: Vec<char> = ident.chars().collect();
    let word = |at: usize, word: &str| {
        let given = chars.get(at..at + word.len())?;
        let same = given
            .iter()
            .zip(word.chars())
            .all(|(a, b)| a.eq_ignore_ascii_case(&b));
        same.then_some(at + word.len())
    };
    let hex = |at: usize| -> Option<(String, usize)> {
        let digits = chars.get(at..at + 4)?;
        let all = digits.iter().all(char::is_ascii_hexdigit);
        all.then(|| {
            (
                digits.iter().collect::<String>().to_ascii_uppercase(),
                at + 4,
            )
        })
    };
    let (vid, at) = hex(word(0, "USB\\VID_")?)?;
    let (pid, at) = hex(word(at, "&PID_")?)?;
    let rest = chars.get(word(at, "\\")?..)?;
    (!rest.is_empty() && !rest.contains(&'\\')).then_some((vid, pid))
}

/// keep_usb: whether a USB device is one to tell about: not a hub, not mass storage or an
/// Xbox controller (others tell those), and not on the list of what to leave out
/// (`VID:PID`).
pub fn keep_usb(entry: &Dict, ignore: &HashSet<String>) -> bool {
    let text = |key: &str| match entry.get(key) {
        Some(value) if value.truthy() => value.str(),
        _ => String::new(),
    };
    let of = |key: &str| entry.get(key).map(Value::str).unwrap_or_default();
    if ignore.contains(&format!("{}:{}", of("vid"), of("pid"))) {
        return false;
    }
    if SKIPPED_SERVICES.contains(&lp_py::lower(&text("service")).as_str()) {
        return false;
    }
    !(lp_py::lower(&text("name")).contains("hub") && lp_py::upper(&text("klass")) == "USB")
}

/// The readers and the notifier of this system; none where it is not Windows.
pub fn system() -> Option<(Box<dyn Readers>, Box<dyn Notifier>)> {
    #[cfg(windows)]
    return Some((
        Box::new(native::System),
        Box::new(native::ChangeNotifier::new()),
    ));
    #[cfg(not(windows))]
    None
}

#[cfg(windows)]
mod native {
    use std::collections::HashSet;
    use std::ffi::c_void;

    use indexmap::IndexMap;
    use lp_py::{Dict, Value};

    use super::{external_bus, keep_usb, parse_device, parse_temperature, top_level};
    use crate::devices::{Notifier, Readers};

    /// no "there is no disk in the drive" box for a card reader
    const SEM_FAILCRITICALERRORS: u32 = 0x0001;
    const DRIVE_REMOVABLE: u32 = 2;
    const DRIVE_FIXED: u32 = 3;
    const FILE_SHARE_READ_WRITE: u32 = 3;
    const OPEN_EXISTING: u32 = 3;
    const IOCTL_STORAGE_QUERY_PROPERTY: u32 = 0x2D1400;
    const STORAGE_DEVICE_PROPERTY: u32 = 0;
    /// the temperature, and the limits the drive sets
    const STORAGE_ADAPTER_TEMPERATURE_PROPERTY: u32 = 51;
    const XINPUT_FLAG_GAMEPAD: u32 = 1;
    const XINPUT_CAPS_WIRELESS: u16 = 0x0002;
    const BATTERY_DEVTYPE_GAMEPAD: u8 = 0;
    const DIGCF_PRESENT: u32 = 0x2;
    const DIGCF_ALLCLASSES: u32 = 0x4;
    const SPDRP_DEVICEDESC: u32 = 0x00;
    const SPDRP_SERVICE: u32 = 0x04;
    const SPDRP_CLASS: u32 = 0x07;
    const SPDRP_FRIENDLYNAME: u32 = 0x0C;
    const CM_NOTIFY_FILTER_FLAG_ALL_INTERFACE_CLASSES: u32 = 0x1;
    const CM_NOTIFY_FILTER_TYPE_DEVICEINTERFACE: i32 = 0;

    type Handle = *mut c_void;

    #[repr(C)]
    struct Guid {
        data1: u32,
        data2: u16,
        data3: u16,
        data4: [u8; 8],
    }

    /// SP_DEVINFO_DATA
    #[repr(C)]
    struct DeviceInfo {
        size: u32,
        class: Guid,
        instance: u32,
        reserved: usize,
    }

    /// DEVPROPKEY
    #[repr(C)]
    struct PropertyKey {
        fmtid: Guid,
        pid: u32,
    }

    /// DEVPKEY_Device_BusReportedDeviceDesc: the name the device itself reports
    const BUS_REPORTED_NAME: PropertyKey = PropertyKey {
        fmtid: Guid {
            data1: 0x540B_947E,
            data2: 0x8B40,
            data3: 0x45BC,
            data4: [0xA8, 0xA2, 0x6A, 0x0B, 0x89, 0x4C, 0xBD, 0xA2],
        },
        pid: 4,
    };

    /// XINPUT_CAPABILITIES
    #[repr(C)]
    #[derive(Default)]
    struct Capabilities {
        kind: u8,
        sub_type: u8,
        flags: u16,
        gamepad: [u8; 12],
        vibration: [u16; 2],
    }

    /// XINPUT_BATTERY_INFORMATION
    #[repr(C)]
    #[derive(Default)]
    struct Battery {
        kind: u8,
        level: u8,
    }

    /// CM_NOTIFY_FILTER: 16 bytes of header, then a union whose biggest member is 200
    /// characters. Windows checks `size` against exactly these 416 bytes.
    #[repr(C)]
    struct Filter {
        size: u32,
        flags: u32,
        filter_type: i32,
        reserved: u32,
        union: [u8; 400],
    }

    type Notice = unsafe extern "system" fn(Handle, *mut c_void, i32, *mut c_void, u32) -> u32;
    type GetCapabilities = unsafe extern "system" fn(u32, u32, *mut Capabilities) -> u32;
    type GetBattery = unsafe extern "system" fn(u32, u8, *mut Battery) -> u32;

    #[link(name = "kernel32")]
    extern "system" {
        fn GetLogicalDrives() -> u32;
        fn GetDriveTypeW(root: *const u16) -> u32;
        fn CreateFileW(
            name: *const u16,
            access: u32,
            share: u32,
            security: *mut c_void,
            creation: u32,
            flags: u32,
            template: Handle,
        ) -> Handle;
        fn DeviceIoControl(
            device: Handle,
            code: u32,
            input: *const c_void,
            input_size: u32,
            output: *mut c_void,
            output_size: u32,
            returned: *mut u32,
            overlapped: *mut c_void,
        ) -> i32;
        fn CloseHandle(handle: Handle) -> i32;
        fn GetDiskFreeSpaceExW(
            root: *const u16,
            free: *mut u64,
            total: *mut u64,
            total_free: *mut u64,
        ) -> i32;
        fn GetVolumeInformationW(
            root: *const u16,
            name: *mut u16,
            name_size: u32,
            serial: *mut u32,
            component: *mut u32,
            flags: *mut u32,
            system: *mut u16,
            system_size: u32,
        ) -> i32;
        fn SetErrorMode(mode: u32) -> u32;
        fn LoadLibraryW(name: *const u16) -> Handle;
        fn GetProcAddress(module: Handle, name: *const u8) -> *mut c_void;
    }

    #[link(name = "setupapi")]
    extern "system" {
        fn SetupDiGetClassDevsW(
            class: *const Guid,
            enumerator: *const u16,
            parent: Handle,
            flags: u32,
        ) -> Handle;
        fn SetupDiEnumDeviceInfo(set: Handle, index: u32, info: *mut DeviceInfo) -> i32;
        fn SetupDiGetDeviceInstanceIdW(
            set: Handle,
            info: *mut DeviceInfo,
            id: *mut u16,
            id_size: u32,
            required: *mut u32,
        ) -> i32;
        fn SetupDiGetDeviceRegistryPropertyW(
            set: Handle,
            info: *mut DeviceInfo,
            property: u32,
            kind: *mut u32,
            buffer: *mut u8,
            buffer_size: u32,
            required: *mut u32,
        ) -> i32;
        fn SetupDiGetDevicePropertyW(
            set: Handle,
            info: *mut DeviceInfo,
            key: *const PropertyKey,
            kind: *mut u32,
            buffer: *mut u8,
            buffer_size: u32,
            required: *mut u32,
            flags: u32,
        ) -> i32;
        fn SetupDiDestroyDeviceInfoList(set: Handle) -> i32;
    }

    #[link(name = "cfgmgr32")]
    extern "system" {
        fn CM_Register_Notification(
            filter: *const Filter,
            context: *mut c_void,
            callback: Notice,
            handle: *mut Handle,
        ) -> u32;
        fn CM_Unregister_Notification(handle: Handle) -> u32;
    }

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain([0]).collect()
    }

    fn text_of(buffer: &[u16]) -> String {
        let end = buffer.iter().position(|unit| *unit == 0);
        String::from_utf16_lossy(&buffer[..end.unwrap_or(buffer.len())])
    }

    fn invalid(handle: Handle) -> bool {
        handle.is_null() || handle as isize == -1
    }

    /// What IOCTL_STORAGE_QUERY_PROPERTY says about the drive: the volume is opened with no
    /// access rights at all (it reads nothing off the disk), or None.
    fn query(letter: char, property: u32) -> Option<Vec<u8>> {
        let path = wide(&format!("\\\\.\\{letter}:"));
        // STORAGE_PROPERTY_QUERY
        let mut asked = [0u8; 12];
        asked[..4].copy_from_slice(&property.to_le_bytes());
        let mut out = vec![0u8; 1024];
        let mut got = 0u32;
        // SAFETY: the path ends with a zero; the buffers are as long as they are said to be;
        // the handle is closed before it is forgotten.
        unsafe {
            let handle = CreateFileW(
                path.as_ptr(),
                0,
                FILE_SHARE_READ_WRITE,
                std::ptr::null_mut(),
                OPEN_EXISTING,
                0,
                std::ptr::null_mut(),
            );
            if invalid(handle) {
                return None;
            }
            let done = DeviceIoControl(
                handle,
                IOCTL_STORAGE_QUERY_PROPERTY,
                asked.as_ptr().cast(),
                12,
                out.as_mut_ptr().cast(),
                1024,
                &mut got,
                std::ptr::null_mut(),
            );
            CloseHandle(handle);
            if done == 0 {
                return None;
            }
        }
        out.truncate(got as usize);
        Some(out)
    }

    fn drives_of(kinds: &[u32]) -> Vec<(char, u32)> {
        // SAFETY: no arguments, and a root that ends with a zero.
        let mask = unsafe { GetLogicalDrives() };
        let mut found = Vec::new();
        for (i, letter) in ('A'..='Z').enumerate() {
            if mask >> i & 1 == 1 {
                let root = wide(&format!("{letter}:\\"));
                let kind = unsafe { GetDriveTypeW(root.as_ptr()) };
                if kinds.contains(&kind) {
                    found.push((letter, kind));
                }
            }
        }
        found
    }

    /// (free, total) bytes of the drive, None for a card reader without a card.
    fn space(letter: char) -> Option<(u64, u64)> {
        let root = wide(&format!("{letter}:\\"));
        let (mut free, mut total) = (0u64, 0u64);
        // SAFETY: the root ends with a zero and the two numbers are written into.
        let done = unsafe {
            GetDiskFreeSpaceExW(root.as_ptr(), &mut free, &mut total, std::ptr::null_mut())
        };
        (done != 0).then_some((free, total))
    }

    /// Runs `read` with no "there is no disk" boxes.
    fn quietly<T>(read: impl FnOnce() -> T) -> T {
        // SAFETY: the mode is a set of flags, set back afterwards.
        let before = unsafe { SetErrorMode(SEM_FAILCRITICALERRORS) };
        let found = read();
        unsafe { SetErrorMode(before) };
        found
    }

    fn whole(n: u64) -> Value {
        Value::Int(n.into())
    }

    fn table(pairs: Vec<(&str, Value)>) -> Dict {
        pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect()
    }

    /// read_drives: the drives that come and go, by letter: `{name, label, total, free,
    /// bus}`. Drives of the removable kind, and fixed ones on a USB, SD or MMC bus; an empty
    /// card reader has no entry until it has a card.
    fn read_drives() -> IndexMap<String, Dict> {
        quietly(|| {
            let mut found = IndexMap::new();
            for (letter, kind) in drives_of(&[DRIVE_REMOVABLE, DRIVE_FIXED]) {
                let raw = query(letter, STORAGE_DEVICE_PROPERTY);
                let (bus, name) = raw.map_or((None, String::new()), |raw| parse_device(&raw));
                if kind == DRIVE_FIXED && external_bus(bus).is_none() {
                    continue;
                }
                let Some((free, total)) = space(letter) else {
                    continue;
                };
                let root = wide(&format!("{letter}:\\"));
                let mut label = [0u16; 64];
                // SAFETY: the root ends with a zero; the label has room for 64 units.
                unsafe {
                    GetVolumeInformationW(
                        root.as_ptr(),
                        label.as_mut_ptr(),
                        64,
                        std::ptr::null_mut(),
                        std::ptr::null_mut(),
                        std::ptr::null_mut(),
                        std::ptr::null_mut(),
                        0,
                    );
                }
                let entry = table(vec![
                    ("name", Value::Str(name)),
                    ("label", Value::Str(text_of(&label))),
                    ("free", whole(free)),
                    ("total", whole(total)),
                    ("bus", Value::Str(external_bus(bus).unwrap_or("USB").into())),
                ]);
                found.insert(letter.to_string(), entry);
            }
            found
        })
    }

    /// read_storage: the fixed drives, by letter: `{name, total, free, temp, warn, crit}`.
    /// The temperature and its limits are None where the drive does not tell.
    fn read_storage() -> IndexMap<String, Dict> {
        quietly(|| {
            let mut found = IndexMap::new();
            for (letter, _kind) in drives_of(&[DRIVE_FIXED]) {
                let Some((free, total)) = space(letter) else {
                    continue;
                };
                let raw = query(letter, STORAGE_DEVICE_PROPERTY);
                let name = raw.map_or(String::new(), |raw| parse_device(&raw).1);
                let raw = query(letter, STORAGE_ADAPTER_TEMPERATURE_PROPERTY);
                let (temp, warn, crit) =
                    raw.map_or((None, None, None), |raw| parse_temperature(&raw));
                let float = |value: Option<f64>| value.map_or(Value::None, Value::Float);
                let entry = table(vec![
                    ("name", Value::Str(name)),
                    ("free", whole(free)),
                    ("total", whole(total)),
                    ("temp", float(temp)),
                    ("warn", float(warn)),
                    ("crit", float(crit)),
                ]);
                found.insert(letter.to_string(), entry);
            }
            found
        })
    }

    /// XInput's two questions, where this Windows has it.
    fn xinput() -> Option<(GetCapabilities, GetBattery)> {
        static FOUND: std::sync::OnceLock<Option<(usize, usize)>> = std::sync::OnceLock::new();
        let found = FOUND.get_or_init(|| {
            ["xinput1_4", "xinput1_3"].iter().find_map(|name| {
                // SAFETY: the names end with a zero; what is found stays loaded.
                unsafe {
                    let module = LoadLibraryW(wide(name).as_ptr());
                    if module.is_null() {
                        return None;
                    }
                    let caps = GetProcAddress(module, c"XInputGetCapabilities".as_ptr().cast());
                    let battery =
                        GetProcAddress(module, c"XInputGetBatteryInformation".as_ptr().cast());
                    (!caps.is_null() && !battery.is_null())
                        .then_some((caps as usize, battery as usize))
                }
            })
        });
        let (caps, battery) = (*found)?;
        // SAFETY: the two addresses are those of functions of these signatures.
        unsafe {
            Some((
                std::mem::transmute::<usize, GetCapabilities>(caps),
                std::mem::transmute::<usize, GetBattery>(battery),
            ))
        }
    }

    /// read_controllers: the connected controllers, by slot (0 to 3): `{wireless, battery,
    /// level}`. The battery is 1 for a cable, 2 or 3 for a battery (`level` 0 empty, 1 low,
    /// 2 medium, 3 full), else None.
    fn read_controllers() -> IndexMap<i64, Dict> {
        let mut found = IndexMap::new();
        let Some((capabilities, battery_of)) = xinput() else {
            return found; // no XInput on this Windows
        };
        for slot in 0..4u32 {
            let mut caps = Capabilities::default();
            // SAFETY: both structs are XInput's, written into by it.
            if unsafe { capabilities(slot, XINPUT_FLAG_GAMEPAD, &mut caps) } != 0 {
                continue;
            }
            let mut battery = Battery::default();
            let told = unsafe { battery_of(slot, BATTERY_DEVTYPE_GAMEPAD, &mut battery) } == 0;
            let of = |value: u8| match told {
                true => Value::int(i64::from(value)),
                false => Value::None,
            };
            let entry = table(vec![
                (
                    "wireless",
                    Value::Bool(caps.flags & XINPUT_CAPS_WIRELESS != 0),
                ),
                ("battery", of(battery.kind)),
                ("level", of(battery.level)),
            ]);
            found.insert(i64::from(slot), entry);
        }
        found
    }

    /// read_usb: the USB devices that plug straight into the machine, by instance id:
    /// `{vid, pid, name, klass, service}`. `ignore` holds `VID:PID` in upper case. The
    /// system's list is read; no device is opened.
    fn read_usb(ignore: &HashSet<String>) -> IndexMap<String, Dict> {
        let mut found = IndexMap::new();
        let enumerator = wide("USB");
        // SAFETY: the set is asked for, walked and destroyed here; every buffer is as long
        // as it is said to be, and the texts end with a zero.
        unsafe {
            let set = SetupDiGetClassDevsW(
                std::ptr::null(),
                enumerator.as_ptr(),
                std::ptr::null_mut(),
                DIGCF_PRESENT | DIGCF_ALLCLASSES,
            );
            if invalid(set) {
                return found;
            }
            let registry_text = |info: &mut DeviceInfo, field: u32| -> String {
                let (mut buffer, mut kind) = ([0u16; 512], 0u32);
                let done = SetupDiGetDeviceRegistryPropertyW(
                    set,
                    info,
                    field,
                    &mut kind,
                    buffer.as_mut_ptr().cast(),
                    1024,
                    std::ptr::null_mut(),
                );
                match done != 0 {
                    true => text_of(&buffer),
                    false => String::new(),
                }
            };
            let mut index = 0;
            loop {
                let mut info: DeviceInfo = std::mem::zeroed();
                info.size = std::mem::size_of::<DeviceInfo>() as u32;
                if SetupDiEnumDeviceInfo(set, index, &mut info) == 0 {
                    break;
                }
                index += 1;
                let mut ident = [0u16; 512];
                let named = SetupDiGetDeviceInstanceIdW(
                    set,
                    &mut info,
                    ident.as_mut_ptr(),
                    512,
                    std::ptr::null_mut(),
                );
                let ident = text_of(&ident);
                let Some((vid, pid)) = top_level(&ident).filter(|_| named != 0) else {
                    continue;
                };
                let (mut name, mut kind) = ([0u16; 512], 0u32);
                let told = SetupDiGetDevicePropertyW(
                    set,
                    &mut info,
                    &BUS_REPORTED_NAME,
                    &mut kind,
                    name.as_mut_ptr().cast(),
                    1024,
                    std::ptr::null_mut(),
                    0,
                );
                let mut name = match told != 0 {
                    true => text_of(&name),
                    false => String::new(),
                };
                for field in [SPDRP_FRIENDLYNAME, SPDRP_DEVICEDESC] {
                    if name.is_empty() {
                        name = registry_text(&mut info, field);
                    }
                }
                let entry = table(vec![
                    ("vid", Value::Str(vid)),
                    ("pid", Value::Str(pid)),
                    ("name", Value::Str(lp_py::strip(&name).to_string())),
                    ("klass", Value::Str(registry_text(&mut info, SPDRP_CLASS))),
                    (
                        "service",
                        Value::Str(registry_text(&mut info, SPDRP_SERVICE)),
                    ),
                ]);
                if keep_usb(&entry, ignore) {
                    found.insert(ident, entry);
                }
            }
            SetupDiDestroyDeviceInfoList(set);
        }
        found
    }

    /// What the watcher asks: the four readers above, as the system gives them.
    pub struct System;

    impl Readers for System {
        fn drives(&self) -> IndexMap<String, Dict> {
            read_drives()
        }

        fn storage(&self) -> IndexMap<String, Dict> {
            read_storage()
        }

        fn controllers(&self) -> IndexMap<i64, Dict> {
            read_controllers()
        }

        fn usb(&self, ignore: &HashSet<String>) -> IndexMap<String, Dict> {
            read_usb(ignore)
        }
    }

    type OnChange = Box<dyn Fn() + Send + Sync>;

    /// In a thread of Windows: nothing may panic, nothing may be slow.
    unsafe extern "system" fn notice(
        _handle: Handle,
        context: *mut c_void,
        action: i32,
        _data: *mut c_void,
        _size: u32,
    ) -> u32 {
        // (0: a device interface arrived, 1: one was removed)
        if (action == 0 || action == 1) && !context.is_null() {
            // SAFETY: the context is the function the notifier keeps for as long as it is
            // registered.
            let on_change = unsafe { &*context.cast::<OnChange>() };
            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(on_change));
        }
        0
    }

    /// Windows' "tell me when a device comes or goes" (`CM_Register_Notification`): the
    /// function given to `start` is called for every device interface that appears or goes,
    /// from a thread of Windows. A plugged device brings a dozen of them in a row.
    pub struct ChangeNotifier {
        handle: Handle,
        /// stays here: Windows calls it for as long as it is registered
        on_change: Option<Box<OnChange>>,
    }

    // SAFETY: the handle is only given back to Windows, from the thread that stops.
    unsafe impl Send for ChangeNotifier {}

    impl ChangeNotifier {
        pub fn new() -> ChangeNotifier {
            ChangeNotifier {
                handle: std::ptr::null_mut(),
                on_change: None,
            }
        }
    }

    impl Notifier for ChangeNotifier {
        /// Register. False where Windows will not (before Windows 8): the poll then carries
        /// on alone.
        fn start(&mut self, on_change: OnChange) -> bool {
            let kept = Box::new(on_change);
            let context = (&*kept as *const OnChange).cast_mut().cast::<c_void>();
            let filter = Filter {
                size: std::mem::size_of::<Filter>() as u32,
                flags: CM_NOTIFY_FILTER_FLAG_ALL_INTERFACE_CLASSES,
                filter_type: CM_NOTIFY_FILTER_TYPE_DEVICEINTERFACE,
                reserved: 0,
                union: [0; 400],
            };
            let mut handle: Handle = std::ptr::null_mut();
            // SAFETY: the filter is read during the call; the context outlives the
            // registration (it is kept until `stop` has unregistered).
            let code = unsafe { CM_Register_Notification(&filter, context, notice, &mut handle) };
            if code != 0 || handle.is_null() {
                return false;
            }
            (self.handle, self.on_change) = (handle, Some(kept));
            true
        }

        /// Unregister (this waits for a notice that is under way, so not from a notice).
        fn stop(&mut self) {
            let handle = std::mem::replace(&mut self.handle, std::ptr::null_mut());
            if !handle.is_null() {
                // SAFETY: the handle is the one Windows gave at the registration.
                unsafe { CM_Unregister_Notification(handle) };
            }
            self.on_change = None;
        }
    }
}
