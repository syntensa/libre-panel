//! NVIDIA's management library (NVML), loaded from the driver (sensors/nvml.py).
//!
//! The proprietary driver registers no hwmon device and keeps its numbers to itself, but it
//! brings `libnvidia-ml.so.1`: temperature, fan, power, load, memory and clocks of the card,
//! to anyone, without rights. The library is found where the loader looks and in the usual
//! folders; where there is none (a PC without an NVIDIA card, the open nouveau driver) there
//! is simply nothing.
//!
//! `Nvml` takes the library as an `Api`, so that a test can give it one of its own. Every
//! call that fails (a card that has no fan, an older driver without a function) leaves its
//! reading out. Nothing here starts a thread or keeps a card awake: asking a card that is
//! powered down wakes it, so the caller asks only cards that are on (gpus::suspended).

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Instant;

pub const LIBRARY: &str = "libnvidia-ml.so.1";
/// Where a driver that is not in the loader's cache keeps it (NixOS, WSL, 64-bit Debian,
/// Fedora).
pub const FOLDERS: [&str; 5] = [
    "/run/opengl-driver/lib",
    "/usr/lib/x86_64-linux-gnu",
    "/usr/lib64",
    "/usr/lib",
    "/usr/lib/wsl/lib",
];
/// a library that did not start is tried again after this long
pub const RETRY_SECONDS: f64 = 60.0;
const MIB: f64 = 1024.0 * 1024.0;

/// nvmlClockType_t
pub const CLOCK_GRAPHICS: i32 = 0;
pub const CLOCK_MEMORY: i32 = 2;

/// A card's handle, as the library gives it.
pub type Handle = usize;

/// The calls of the library that are made; each gives its value, or None when the library
/// says no or has no such function.
pub trait Api: Send + Sync {
    /// The result code of starting it; None if it has no such function.
    fn init(&self) -> Option<i32>;
    fn shutdown(&self);
    fn count(&self) -> u32;
    fn handle_by_slot(&self, slot: &str) -> Option<Handle>;
    fn handle_by_index(&self, index: u32) -> Option<Handle>;
    /// °C
    fn temperature(&self, card: Handle) -> Option<u32>;
    /// percent
    fn fan_speed(&self, card: Handle) -> Option<u32>;
    /// RPM, from drivers that tell it (555 and later)
    fn fan_rpm(&self, card: Handle) -> Option<u32>;
    /// milliwatts
    fn power(&self, card: Handle) -> Option<u32>;
    /// percent of the last sample period the card (and its memory) was busy
    fn utilization(&self, card: Handle) -> Option<(u32, u32)>;
    /// (total, free, used) bytes
    fn memory(&self, card: Handle) -> Option<(u64, u64, u64)>;
    /// MHz
    fn clock(&self, card: Handle, kind: i32) -> Option<u32>;
}

/// What a card tells, in the units people use; those the card or the driver does not give
/// are not there.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Values {
    /// °C
    pub temp: Option<f64>,
    pub fan_percent: Option<f64>,
    pub fan_rpm: Option<f64>,
    /// W
    pub power: Option<f64>,
    /// %
    pub load: Option<f64>,
    /// MB
    pub mem_used: Option<f64>,
    pub mem_total: Option<f64>,
    /// %
    pub mem_load: Option<f64>,
    /// MHz
    pub freq: Option<f64>,
    pub mem_freq: Option<f64>,
}

/// One NVIDIA card.
#[derive(Clone)]
pub struct Device {
    nvml: Arc<Nvml>,
    handle: Handle,
}

impl Device {
    /// The card's load in percent: the share of the last sample period it was busy.
    pub fn utilization(&self) -> Option<f64> {
        let (card, _) = self.nvml.api.utilization(self.handle)?;
        Some(card as f64)
    }

    pub fn read(&self) -> Values {
        let (api, card) = (&self.nvml.api, self.handle);
        let mut values = Values {
            temp: api.temperature(card).map(|value| value as f64),
            fan_percent: api.fan_speed(card).map(|value| value as f64),
            fan_rpm: api.fan_rpm(card).map(|value| value as f64),
            power: api.power(card).map(|value| value as f64 / 1000.0),
            load: self.utilization(),
            freq: api.clock(card, CLOCK_GRAPHICS).map(|value| value as f64),
            mem_freq: api.clock(card, CLOCK_MEMORY).map(|value| value as f64),
            ..Values::default()
        };
        if let Some((total, _, used)) = api.memory(card).filter(|(total, ..)| *total != 0) {
            values.mem_used = Some(used as f64 / MIB);
            values.mem_total = Some(total as f64 / MIB);
            values.mem_load = Some(100.0 * used as f64 / total as f64);
        }
        values
    }
}

/// A started NVML.
pub struct Nvml {
    api: Box<dyn Api>,
    devices: Mutex<HashMap<String, Handle>>,
}

impl Nvml {
    /// A library that is started already (or a test's own).
    pub fn of(api: Box<dyn Api>) -> Arc<Nvml> {
        Arc::new(Nvml {
            api,
            devices: Mutex::new(HashMap::new()),
        })
    }

    /// The driver's library, started; None where there is none or it does not start (no
    /// NVIDIA driver, a library of another version than the driver).
    pub fn open() -> Option<Arc<Nvml>> {
        #[cfg(target_os = "linux")]
        {
            let api = driver::Driver::load()?;
            if api.init()? != 0 {
                return None; // the library did not start
            }
            Some(Nvml::of(Box::new(api)))
        }
        #[cfg(not(target_os = "linux"))]
        None
    }

    pub fn count(&self) -> u32 {
        self.api.count()
    }

    fn device(
        self: &Arc<Nvml>,
        key: String,
        find: impl FnOnce() -> Option<Handle>,
    ) -> Option<Device> {
        let mut devices = self.devices.lock().ok()?;
        let handle = match devices.get(&key) {
            Some(handle) => *handle,
            None => {
                let handle = find()?;
                devices.insert(key, handle);
                handle
            }
        };
        Some(Device {
            nvml: self.clone(),
            handle,
        })
    }

    /// The card at a PCI address ("0000:01:00.0"), the way the kernel lists it.
    pub fn by_slot(self: &Arc<Nvml>, slot: &str) -> Option<Device> {
        if !slot.is_ascii() {
            return None;
        }
        self.device(slot.to_string(), || self.api.handle_by_slot(slot))
    }

    /// The card with that number (a PC with one NVIDIA card has only the first).
    pub fn by_index(self: &Arc<Nvml>, index: u32) -> Option<Device> {
        self.device(format!("#{index}"), || self.api.handle_by_index(index))
    }

    pub fn close(&self) {
        self.api.shutdown();
        if let Ok(mut devices) = self.devices.lock() {
            devices.clear();
        }
    }
}

/// The one NVML of the program (the readings and the load source both ask it). A library
/// that did not start is tried again after a minute: the driver may load after the login.
pub fn shared() -> Option<Arc<Nvml>> {
    static SHARED: Mutex<(Option<Arc<Nvml>>, Option<Instant>)> = Mutex::new((None, None));
    let mut held = SHARED.lock().ok()?;
    let due = held
        .1
        .is_none_or(|tried| tried.elapsed().as_secs_f64() >= RETRY_SECONDS);
    if held.0.is_none() && due {
        *held = (Nvml::open(), Some(Instant::now()));
    }
    held.0.clone()
}

/// The driver's own library, through the loader.
#[cfg(target_os = "linux")]
mod driver {
    use std::ffi::{c_char, c_int, c_uint, c_ulonglong, c_void, CString};

    use libloading::Library;

    use super::{Api, Handle, FOLDERS, LIBRARY};

    const SUCCESS: c_int = 0;
    /// nvmlTemperatureSensors_t
    const TEMPERATURE_GPU: c_int = 0;

    /// nvmlUtilization_t
    #[repr(C)]
    #[derive(Default)]
    struct Utilization {
        gpu: c_uint,
        memory: c_uint,
    }

    /// nvmlMemory_t
    #[repr(C)]
    #[derive(Default)]
    struct Memory {
        total: c_ulonglong,
        free: c_ulonglong,
        used: c_ulonglong,
    }

    /// nvmlFanSpeedInfo_t: asked with its version (size | 1 << 24)
    #[repr(C)]
    struct FanSpeed {
        version: c_uint,
        fan: c_uint,
        speed: c_uint,
    }

    type Start = unsafe extern "C" fn() -> c_int;
    type Count = unsafe extern "C" fn(*mut c_uint) -> c_int;
    type BySlot = unsafe extern "C" fn(*const c_char, *mut *mut c_void) -> c_int;
    type ByIndex = unsafe extern "C" fn(c_uint, *mut *mut c_void) -> c_int;
    type Number = unsafe extern "C" fn(*mut c_void, *mut c_uint) -> c_int;
    type Chosen = unsafe extern "C" fn(*mut c_void, c_int, *mut c_uint) -> c_int;
    type Fan = unsafe extern "C" fn(*mut c_void, *mut FanSpeed) -> c_int;
    type Busy = unsafe extern "C" fn(*mut c_void, *mut Utilization) -> c_int;
    type Bytes = unsafe extern "C" fn(*mut c_void, *mut Memory) -> c_int;

    pub struct Driver(Library);

    impl Driver {
        pub fn load() -> Option<Driver> {
            let folders = FOLDERS.iter().map(|folder| format!("{folder}/{LIBRARY}"));
            for name in std::iter::once(LIBRARY.to_string()).chain(folders) {
                // SAFETY: the driver's own library, whose start-up code is its maker's.
                if let Ok(library) = unsafe { Library::new(&name) } {
                    return Some(Driver(library));
                }
            }
            None
        }

        /// The function of that name, if the driver has one.
        fn function<T: Copy>(&self, name: &str) -> Option<T> {
            // SAFETY: each name is asked for with the type NVML's header gives it.
            unsafe { self.0.get::<T>(name.as_bytes()).ok().map(|found| *found) }
        }

        /// The first of `names` the driver has, called: its result code.
        fn first<T: Copy>(&self, names: [&str; 2], call: impl FnOnce(T) -> c_int) -> Option<c_int> {
            names
                .iter()
                .find_map(|name| self.function::<T>(name))
                .map(call)
        }

        fn number(&self, name: &str, card: Handle) -> Option<u32> {
            let function: Number = self.function(name)?;
            let mut value: c_uint = 0;
            // SAFETY: the handle is one the library gave; the value is this frame's.
            let code = unsafe { function(card as *mut c_void, &mut value) };
            (code == SUCCESS).then_some(value)
        }

        fn chosen(&self, name: &str, card: Handle, which: c_int) -> Option<u32> {
            let function: Chosen = self.function(name)?;
            let mut value: c_uint = 0;
            // SAFETY: as above.
            let code = unsafe { function(card as *mut c_void, which, &mut value) };
            (code == SUCCESS).then_some(value)
        }
    }

    impl Api for Driver {
        fn init(&self) -> Option<i32> {
            // SAFETY: the library's own start, with no arguments.
            self.first::<Start>(["nvmlInit_v2", "nvmlInit"], |start| unsafe { start() })
        }

        fn shutdown(&self) {
            if let Some(stop) = self.function::<Start>("nvmlShutdown") {
                // SAFETY: the library's own end, with no arguments.
                unsafe {
                    stop();
                }
            }
        }

        fn count(&self) -> u32 {
            let mut number: c_uint = 0;
            let names = ["nvmlDeviceGetCount_v2", "nvmlDeviceGetCount"];
            // SAFETY: the number is this frame's.
            let code = self.first::<Count>(names, |count| unsafe { count(&mut number) });
            match code {
                Some(SUCCESS) => number,
                _ => 0,
            }
        }

        fn handle_by_slot(&self, slot: &str) -> Option<Handle> {
            let slot = CString::new(slot).ok()?;
            let mut handle: *mut c_void = std::ptr::null_mut();
            let names = [
                "nvmlDeviceGetHandleByPciBusId_v2",
                "nvmlDeviceGetHandleByPciBusId",
            ];
            // SAFETY: the text ends with a zero; the handle is this frame's.
            let code =
                self.first::<BySlot>(names, |find| unsafe { find(slot.as_ptr(), &mut handle) });
            (code == Some(SUCCESS)).then_some(handle as Handle)
        }

        fn handle_by_index(&self, index: u32) -> Option<Handle> {
            let mut handle: *mut c_void = std::ptr::null_mut();
            let names = [
                "nvmlDeviceGetHandleByIndex_v2",
                "nvmlDeviceGetHandleByIndex",
            ];
            // SAFETY: the handle is this frame's.
            let code = self.first::<ByIndex>(names, |find| unsafe { find(index, &mut handle) });
            (code == Some(SUCCESS)).then_some(handle as Handle)
        }

        fn temperature(&self, card: Handle) -> Option<u32> {
            self.chosen("nvmlDeviceGetTemperature", card, TEMPERATURE_GPU)
        }

        fn fan_speed(&self, card: Handle) -> Option<u32> {
            self.number("nvmlDeviceGetFanSpeed", card)
        }

        fn fan_rpm(&self, card: Handle) -> Option<u32> {
            let function: Fan = self.function("nvmlDeviceGetFanSpeedRPM")?;
            let mut info = FanSpeed {
                version: size_of::<FanSpeed>() as c_uint | (1 << 24),
                fan: 0,
                speed: 0,
            };
            // SAFETY: the structure is this frame's, with its version set.
            let code = unsafe { function(card as *mut c_void, &mut info) };
            (code == SUCCESS).then_some(info.speed)
        }

        fn power(&self, card: Handle) -> Option<u32> {
            self.number("nvmlDeviceGetPowerUsage", card)
        }

        fn utilization(&self, card: Handle) -> Option<(u32, u32)> {
            let function: Busy = self.function("nvmlDeviceGetUtilizationRates")?;
            let mut found = Utilization::default();
            // SAFETY: the structure is this frame's.
            let code = unsafe { function(card as *mut c_void, &mut found) };
            (code == SUCCESS).then_some((found.gpu, found.memory))
        }

        fn memory(&self, card: Handle) -> Option<(u64, u64, u64)> {
            let function: Bytes = self.function("nvmlDeviceGetMemoryInfo")?;
            let mut found = Memory::default();
            // SAFETY: the structure is this frame's.
            let code = unsafe { function(card as *mut c_void, &mut found) };
            (code == SUCCESS).then_some((found.total, found.free, found.used))
        }

        fn clock(&self, card: Handle, kind: i32) -> Option<u32> {
            self.chosen("nvmlDeviceGetClockInfo", card, kind)
        }
    }
}
