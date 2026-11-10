//! Volume and mute of the default output device, while a theme shows them; and whether the
//! default microphone is muted (sensors/audio.py).
//!
//! Readings: `audio.volume` (0-100 %, 0 while muted) and `audio.muted` (1 or 0);
//! `audio.mic.muted` (1 or 0) and `audio.mic.volume` (its level, 0-100 %), read only while a
//! theme or a service asks for `audio.mic.*`. They are read on every frame, so a volume bar
//! follows the volume keys at once. `toggle_microphone` mutes the microphone or turns it on
//! again (a keyboard shortcut).
//!
//! - Windows: Core Audio, no admin rights. Windows reports every change itself (an
//!   IAudioEndpointVolumeCallback made by hand); a slower poll is the safety net, and every
//!   two seconds the default device is looked at again, so a headset plugged in or speakers
//!   chosen are followed.
//! - Linux: `wpctl` (PipeWire), else `pactl` (PulseAudio), asked four times a second. Without
//!   either tool there are no readings.
//!
//! The thread runs only while a theme on show reads `audio.*`, and ends when the source
//! closes. Where the volume cannot be read there are simply no readings, and the log says why
//! once.

#[cfg(not(windows))]
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use lp_py::regex::Regex;
use lp_py::Value;
use lp_render::Reading;

use crate::lhm::Readings;
use crate::service::{wanted, Wanted};

/// Windows tells about changes: the poll is only a safety net
pub const EVENT_POLL_SECONDS: f64 = 0.25;
/// without events
pub const POLL_SECONDS: f64 = 0.02;
/// has the default output device changed?
pub const DEVICE_CHECK_SECONDS: f64 = 2.0;
pub const LINUX_POLL_SECONDS: f64 = 0.25;
/// a tool that keeps failing is asked less often
pub const SLOW_POLL_SECONDS: f64 = 5.0;
pub const FAILURES_BEFORE_SLOW: u32 = 5;
/// no device or tool: look again now and then
pub const RETRY_SECONDS: f64 = 5.0;
/// a reading older than this is dropped, not shown frozen
pub const STALE_SECONDS: f64 = 2.0;
/// nobody looked for this long: the thread ends, and starts on demand
pub const IDLE_SECONDS: f64 = 60.0;
pub const COMMAND_TIMEOUT: f64 = 2.0;

// -- the tools of Linux -------------------------------------------------------------------------

fn first_group(pattern: &str, text: &str) -> Option<String> {
    let chars: Vec<char> = text.chars().collect();
    let found = Regex::new(pattern, false).ok()?.search(&chars)?;
    found.group(&chars, 1)
}

/// parse_wpctl: `Volume: 0.37` or `Volume: 0.37 [MUTED]`, as `wpctl get-volume` prints it.
pub fn parse_wpctl(text: &str) -> Option<(f64, bool)> {
    let level = lp_py::float_of_str(&first_group(r"Volume:\s*(\d*\.?\d+)", text)?)?;
    Some((level * 100.0, text.contains("[MUTED]")))
}

/// parse_pactl_volume: the first percentage of `Volume: front-left: 24248 /  37% / ...`.
pub fn parse_pactl_volume(text: &str) -> Option<f64> {
    lp_py::float_of_str(&first_group(r"(\d+)%", text)?)
}

/// parse_pactl_mute: `Mute: yes` or `Mute: no`.
pub fn parse_pactl_mute(text: &str) -> Option<bool> {
    Some(first_group(r"Mute:\s*(yes|no)", text)? == "yes")
}

#[cfg(not(windows))]
/// shutil.which: the program of this name on the PATH.
fn which(name: &str) -> Option<PathBuf> {
    let paths = std::env::var_os("PATH")?;
    std::env::split_paths(&paths)
        .map(|folder| folder.join(name))
        .find(|path| runs(path))
}

#[cfg(not(windows))]
#[cfg(unix)]
fn runs(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

#[cfg(not(any(unix, windows)))]
fn runs(path: &Path) -> bool {
    path.is_file()
}

#[cfg(not(windows))]
/// What a command prints, or none if it cannot run, fails or takes too long. Its texts are
/// English.
fn run(program: &Path, arguments: &[&str]) -> Option<String> {
    use std::io::Read;
    use std::process::{Command, Stdio};
    let mut child = Command::new(program)
        .args(arguments)
        .env("LC_ALL", "C")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let end = Instant::now() + Duration::from_secs_f64(COMMAND_TIMEOUT);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < end => std::thread::sleep(Duration::from_millis(5)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    };
    let mut out = String::new();
    child.stdout.take()?.read_to_string(&mut out).ok()?;
    status.success().then_some(out)
}

#[cfg(not(windows))]
#[derive(Clone, Copy, PartialEq)]
enum Tool {
    Wpctl,
    Pactl,
}

#[cfg(not(windows))]
impl Tool {
    fn name(self) -> &'static str {
        match self {
            Tool::Wpctl => "wpctl",
            Tool::Pactl => "pactl",
        }
    }

    fn sample(self, path: &Path, mic: bool) -> Option<(f64, bool)> {
        match (self, mic) {
            (Tool::Wpctl, false) => {
                parse_wpctl(&run(path, &["get-volume", "@DEFAULT_AUDIO_SINK@"])?)
            }
            (Tool::Wpctl, true) => {
                parse_wpctl(&run(path, &["get-volume", "@DEFAULT_AUDIO_SOURCE@"])?)
            }
            (Tool::Pactl, false) => {
                let out = run(path, &["get-sink-volume", "@DEFAULT_SINK@"])?;
                let level = parse_pactl_volume(&out)?;
                let muted = run(path, &["get-sink-mute", "@DEFAULT_SINK@"])
                    .and_then(|out| parse_pactl_mute(&out));
                Some((level, muted == Some(true)))
            }
            (Tool::Pactl, true) => {
                let out = run(path, &["get-source-mute", "@DEFAULT_SOURCE@"])?;
                let muted = parse_pactl_mute(&out)?;
                let level = run(path, &["get-source-volume", "@DEFAULT_SOURCE@"])
                    .and_then(|out| parse_pactl_volume(&out));
                Some((level.unwrap_or(0.0), muted))
            }
        }
    }
}

/// One way to read the volume; made, used and dropped in the source's thread.
trait Way {
    /// changes are reported as they happen
    fn events(&self) -> bool;
    /// seconds until the next `sample`
    fn interval(&self) -> f64;
    fn sample(&mut self) -> Option<(f64, bool)>;
    fn check_device(&mut self) -> bool;
}

#[cfg(not(windows))]
/// The volume from a tool on the PATH, asked again and again.
struct Commands {
    tools: Vec<(PathBuf, Tool)>,
    mic: bool,
    failures: u32,
}

#[cfg(not(windows))]
impl Commands {
    /// An error if none of the tools is installed.
    fn new(mic: bool) -> Result<Commands, String> {
        let tools: Vec<(PathBuf, Tool)> = [Tool::Wpctl, Tool::Pactl]
            .into_iter()
            .filter_map(|tool| Some((which(tool.name())?, tool)))
            .collect();
        if tools.is_empty() {
            return Err("needs wpctl or pactl".into());
        }
        Ok(Commands {
            tools,
            mic,
            failures: 0,
        })
    }
}

#[cfg(not(windows))]
impl Way for Commands {
    fn events(&self) -> bool {
        false
    }

    fn interval(&self) -> f64 {
        if self.failures < FAILURES_BEFORE_SLOW {
            LINUX_POLL_SECONDS
        } else {
            SLOW_POLL_SECONDS
        }
    }

    fn sample(&mut self) -> Option<(f64, bool)> {
        for position in 0..self.tools.len() {
            let (path, tool) = &self.tools[position];
            if let Some(state) = tool.sample(path, self.mic) {
                let works = self.tools.remove(position);
                self.tools.insert(0, works); // the one that works goes first
                self.failures = 0;
                return Some(state);
            }
        }
        self.failures += 1;
        None
    }

    fn check_device(&mut self) -> bool {
        false // the tools ask for "the default" every time
    }
}

// -- Windows: Core Audio --------------------------------------------------------------------------

#[cfg(windows)]
mod core_audio {
    use std::ffi::c_void;

    use windows_sys::core::GUID;
    use windows_sys::Win32::System::Com::{
        CoCreateInstance, CoInitializeEx, CoTaskMemFree, CoUninitialize, CLSCTX_ALL,
        COINIT_MULTITHREADED,
    };

    use super::{Publish, Way, EVENT_POLL_SECONDS, POLL_SECONDS};

    /// EDataFlow: the default device for playing, and for recording (the microphone)
    pub const E_RENDER: i32 = 0;
    pub const E_CAPTURE: i32 = 1;
    /// ERole
    const E_CONSOLE: i32 = 0;
    const E_NOINTERFACE: i32 = 0x8000_4002_u32 as i32;
    /// MMDeviceEnumerator, IMMDeviceEnumerator, IAudioEndpointVolume, IUnknown,
    /// IAudioEndpointVolumeCallback
    const CLSID_ENUMERATOR: GUID = GUID::from_u128(0xBCDE0395_E52F_467C_8E3D_C4579291692E);
    const IID_ENUMERATOR: GUID = GUID::from_u128(0xA95664D2_9614_4F35_A746_DE8DB63617E6);
    const IID_ENDPOINT_VOLUME: GUID = GUID::from_u128(0x5CDF2C82_841E_4546_9722_0CF74078229A);
    const IID_UNKNOWN: GUID = GUID::from_u128(0x00000000_0000_0000_C000_000000000046);
    const IID_VOLUME_CALLBACK: GUID = GUID::from_u128(0x657804FA_D6AD_4496_8A60_352752AF4F89);
    // the slots of the methods in the interfaces' tables
    const ENUMERATOR_GET_DEFAULT_ENDPOINT: usize = 4;
    const DEVICE_ACTIVATE: usize = 3;
    const DEVICE_GET_ID: usize = 5;
    const VOLUME_REGISTER: usize = 3;
    const VOLUME_UNREGISTER: usize = 4;
    const VOLUME_GET_LEVEL: usize = 9; // GetMasterVolumeLevelScalar
    const VOLUME_SET_MUTE: usize = 14;
    const VOLUME_GET_MUTE: usize = 15;

    type Object = *mut c_void;

    /// Method `slot` of the COM object at `object`, as a function of the type `F`.
    ///
    /// SAFETY: `object` is a live COM object whose table has the slot, and `F` is the
    /// method's own type (a function pointer taking the object first).
    unsafe fn method<F: Copy>(object: Object, slot: usize) -> F {
        let table = *object.cast::<*const *const c_void>();
        std::mem::transmute_copy(&*table.add(slot))
    }

    fn check(hr: i32) -> Result<(), String> {
        if hr < 0 {
            return Err(format!("COM call failed (HRESULT {:#010x})", hr as u32));
        }
        Ok(())
    }

    /// IUnknown::Release (slot 2). A pointer that is never released is a leak.
    fn release(object: Object) {
        if !object.is_null() {
            // SAFETY: a live COM object's Release.
            unsafe {
                let release: unsafe extern "system" fn(Object) -> u32 = method(object, 2);
                release(object);
            }
        }
    }

    /// AUDIO_VOLUME_NOTIFICATION_DATA
    #[repr(C)]
    struct NotificationData {
        event_context: GUID,
        muted: i32,
        master_volume: f32,
        channels: u32,
        channel_volumes: [f32; 1],
    }

    #[repr(C)]
    struct Table {
        query_interface: unsafe extern "system" fn(*mut Callback, *const GUID, *mut Object) -> i32,
        add_ref: unsafe extern "system" fn(*mut Callback) -> u32,
        release: unsafe extern "system" fn(*mut Callback) -> u32,
        on_notify: unsafe extern "system" fn(*mut Callback, *const NotificationData) -> i32,
    }

    /// An IAudioEndpointVolumeCallback as a raw COM object, so that Windows can tell about
    /// changes: a pointer to a table of four functions. The reference counting is a
    /// pretence: the object lives as long as the source that made it, which keeps it until
    /// it has unregistered it.
    #[repr(C)]
    struct Callback {
        table: &'static Table,
        publish: Publish,
    }

    fn same(a: &GUID, b: &GUID) -> bool {
        a.data1 == b.data1 && a.data2 == b.data2 && a.data3 == b.data3 && a.data4 == b.data4
    }

    unsafe extern "system" fn query_interface(
        this: *mut Callback,
        iid: *const GUID,
        out: *mut Object,
    ) -> i32 {
        if out.is_null() {
            return E_NOINTERFACE;
        }
        if !iid.is_null() && (same(&*iid, &IID_UNKNOWN) || same(&*iid, &IID_VOLUME_CALLBACK)) {
            *out = this.cast();
            return 0;
        }
        *out = std::ptr::null_mut();
        E_NOINTERFACE
    }

    unsafe extern "system" fn count(_this: *mut Callback) -> u32 {
        1
    }

    /// Runs in a thread of the audio service.
    unsafe extern "system" fn on_notify(this: *mut Callback, data: *const NotificationData) -> i32 {
        if !this.is_null() && !data.is_null() {
            let level = (f64::from((*data).master_volume) * 100.0).clamp(0.0, 100.0);
            ((*this).publish)(level, (*data).muted != 0);
        }
        0
    }

    static TABLE: Table = Table {
        query_interface,
        add_ref: count,
        release: count,
        on_notify,
    };

    /// The volume of the default output device (or, with `flow` E_CAPTURE, of the default
    /// microphone) through Core Audio.
    ///
    /// COM belongs to the thread that starts it: it is made, asked and dropped in one
    /// thread, which joins the multi-threaded apartment (so Windows calls back without a
    /// message loop). Every interface pointer it gets is released again, and COM is ended.
    pub struct CoreAudio {
        events: bool,
        publish: Publish,
        flow: i32,
        /// CoInitializeEx worked, so CoUninitialize is due
        com: bool,
        enumerator: Object,
        volume: Object,
        device_id: Option<Vec<u16>>,
        callback: Option<Box<Callback>>,
        /// the last read failed: reconnect even to the same device
        broken: bool,
    }

    impl CoreAudio {
        /// An error if there is no such device.
        pub fn new(publish: Publish, flow: i32) -> Result<CoreAudio, String> {
            let mut made = CoreAudio {
                events: false,
                publish,
                flow,
                com: false,
                enumerator: std::ptr::null_mut(),
                volume: std::ptr::null_mut(),
                device_id: None,
                callback: None,
                broken: false,
            };
            // SAFETY: COM is started for this thread and ended by drop; the enumerator is
            // written by CoCreateInstance and released by drop.
            unsafe {
                let hr = CoInitializeEx(std::ptr::null(), COINIT_MULTITHREADED as u32);
                made.com = hr == 0 || hr == 1; // S_OK, S_FALSE: one CoUninitialize each
                check(CoCreateInstance(
                    &CLSID_ENUMERATOR,
                    std::ptr::null_mut(),
                    CLSCTX_ALL,
                    &IID_ENUMERATOR,
                    &mut made.enumerator,
                ))?;
            }
            made.connect()?;
            Ok(made)
        }

        /// (IMMDevice, its id) of the default device; the caller releases the device.
        fn default_device(&self) -> Result<(Object, Option<Vec<u16>>), String> {
            // SAFETY: the enumerator is live; the device and its id are written by it, the
            // id's memory is Windows' and freed here.
            unsafe {
                let mut device: Object = std::ptr::null_mut();
                let get: unsafe extern "system" fn(Object, i32, i32, *mut Object) -> i32 =
                    method(self.enumerator, ENUMERATOR_GET_DEFAULT_ENDPOINT);
                check(get(self.enumerator, self.flow, E_CONSOLE, &mut device))?;
                if device.is_null() {
                    return Err(if self.flow == E_RENDER {
                        "no output device".into()
                    } else {
                        "no microphone".into()
                    });
                }
                let mut text: *mut u16 = std::ptr::null_mut();
                let get_id: unsafe extern "system" fn(Object, *mut *mut u16) -> i32 =
                    method(device, DEVICE_GET_ID);
                let mut id = None;
                if get_id(device, &mut text) >= 0 && !text.is_null() {
                    let mut length = 0;
                    while *text.add(length) != 0 {
                        length += 1;
                    }
                    id = Some(std::slice::from_raw_parts(text, length).to_vec());
                    CoTaskMemFree(text.cast());
                }
                Ok((device, id))
            }
        }

        /// (Re)connect to the default device.
        fn connect(&mut self) -> Result<(), String> {
            let (device, device_id) = self.default_device()?;
            let mut volume: Object = std::ptr::null_mut();
            // SAFETY: the device is live until released below; the volume control is
            // written by Activate.
            let activated = unsafe {
                let activate: unsafe extern "system" fn(
                    Object,
                    *const GUID,
                    u32,
                    *mut c_void,
                    *mut Object,
                ) -> i32 = method(device, DEVICE_ACTIVATE);
                check(activate(
                    device,
                    &IID_ENDPOINT_VOLUME,
                    CLSCTX_ALL,
                    std::ptr::null_mut(),
                    &mut volume,
                ))
            };
            release(device);
            activated?;
            if volume.is_null() {
                return Err("the output device has no volume control".into());
            }
            self.unregister();
            release(self.volume);
            self.volume = volume;
            self.device_id = device_id;
            self.broken = false;
            self.register();
            Ok(())
        }

        /// Mute the device, or turn it on again.
        pub fn set_mute(&mut self, muted: bool) -> Result<(), String> {
            if self.volume.is_null() {
                return Err("no device".into());
            }
            // SAFETY: the volume control is live.
            unsafe {
                let set: unsafe extern "system" fn(Object, i32, *const GUID) -> i32 =
                    method(self.volume, VOLUME_SET_MUTE);
                check(set(self.volume, i32::from(muted), std::ptr::null()))
            }
        }

        fn register(&mut self) {
            let callback = self.callback.get_or_insert_with(|| {
                Box::new(Callback {
                    table: &TABLE,
                    publish: self.publish.clone(),
                })
            });
            let pointer: *mut Callback = &mut **callback;
            // SAFETY: the callback object lives in its box until this source has
            // unregistered it (unregister, drop).
            self.events = unsafe {
                let register: unsafe extern "system" fn(Object, *mut Callback) -> i32 =
                    method(self.volume, VOLUME_REGISTER);
                register(self.volume, pointer) >= 0
            };
        }

        fn unregister(&mut self) {
            if let (false, true, Some(callback)) =
                (self.volume.is_null(), self.events, self.callback.as_mut())
            {
                let pointer: *mut Callback = &mut **callback;
                // SAFETY: the volume control is live, the callback the one registered.
                unsafe {
                    let unregister: unsafe extern "system" fn(Object, *mut Callback) -> i32 =
                        method(self.volume, VOLUME_UNREGISTER);
                    unregister(self.volume, pointer);
                }
            }
            self.events = false;
        }
    }

    impl Way for CoreAudio {
        fn events(&self) -> bool {
            self.events
        }

        fn interval(&self) -> f64 {
            if self.events {
                EVENT_POLL_SECONDS
            } else {
                POLL_SECONDS
            }
        }

        /// `(level 0-100, muted)`, or none if the device cannot be asked now. The level is
        /// the one that is set, also while muted: "muted" says more than "0", and after
        /// unmuting the old level is there again.
        fn sample(&mut self) -> Option<(f64, bool)> {
            if self.volume.is_null() {
                return None;
            }
            let (mut muted, mut level) = (0i32, 0f32);
            // SAFETY: the volume control is live; two numbers of this frame are written.
            let read = unsafe {
                let get_mute: unsafe extern "system" fn(Object, *mut i32) -> i32 =
                    method(self.volume, VOLUME_GET_MUTE);
                let get_level: unsafe extern "system" fn(Object, *mut f32) -> i32 =
                    method(self.volume, VOLUME_GET_LEVEL);
                get_mute(self.volume, &mut muted) >= 0 && get_level(self.volume, &mut level) >= 0
            };
            self.broken = !read; // (the device may be gone, or back)
            read.then(|| ((f64::from(level) * 100.0).clamp(0.0, 100.0), muted != 0))
        }

        /// Has Windows chosen another default device (a headset plugged in, speakers
        /// picked)? Then connect to it: the callback and the reads hang on the old one
        /// otherwise. Also reconnects to the same device after a failed read (unplugged and
        /// back). True if it connected anew.
        fn check_device(&mut self) -> bool {
            if self.enumerator.is_null() {
                return false;
            }
            let Ok((device, device_id)) = self.default_device() else {
                return false; // no device at the moment: look again in two seconds
            };
            release(device);
            if device_id.is_none() || (device_id == self.device_id && !self.broken) {
                return false;
            }
            self.connect().is_ok()
        }
    }

    impl Drop for CoreAudio {
        fn drop(&mut self) {
            self.unregister();
            release(self.volume);
            release(self.enumerator);
            self.volume = std::ptr::null_mut();
            self.enumerator = std::ptr::null_mut();
            if self.com {
                // SAFETY: one CoUninitialize for the CoInitializeEx that worked.
                unsafe { CoUninitialize() };
            }
        }
    }
}

type Publish = Arc<dyn Fn(f64, bool) + Send + Sync>;

/// The way to read the volume (or, with `mic`, the microphone) on this machine; an error if
/// there is none.
fn open_way(publish: Publish, mic: bool) -> Result<Box<dyn Way>, String> {
    #[cfg(windows)]
    {
        let flow = if mic {
            core_audio::E_CAPTURE
        } else {
            core_audio::E_RENDER
        };
        Ok(Box::new(core_audio::CoreAudio::new(publish, flow)?))
    }
    #[cfg(not(windows))]
    {
        let _ = publish;
        Ok(Box::new(Commands::new(mic)?))
    }
}

/// toggle_microphone: mute the default microphone, or turn it on again (a keyboard
/// shortcut). An error where it cannot be done (no microphone, no tool).
pub fn toggle_microphone() -> Result<(), String> {
    #[cfg(windows)]
    {
        // (COM in a thread of its own, made and ended there)
        let worker = std::thread::spawn(|| -> Result<(), String> {
            let mut mic = core_audio::CoreAudio::new(Arc::new(|_, _| {}), core_audio::E_CAPTURE)?;
            let (_, muted) = mic.sample().ok_or("the microphone cannot be read")?;
            mic.set_mute(!muted)
        });
        worker
            .join()
            .unwrap_or_else(|_| Err("the microphone cannot be muted".into()))
    }
    #[cfg(not(windows))]
    {
        let ways: [(&str, [&str; 3]); 2] = [
            ("wpctl", ["set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"]),
            ("pactl", ["set-source-mute", "@DEFAULT_SOURCE@", "toggle"]),
        ];
        for (name, arguments) in ways {
            if which(name).is_some_and(|path| run(&path, &arguments).is_some()) {
                return Ok(());
            }
        }
        Err("needs wpctl or pactl".into())
    }
}

// -- the source ---------------------------------------------------------------------------------

#[derive(Default)]
struct Held {
    /// (when, readings) of the output device, and of the microphone
    state: Option<(Instant, Readings)>,
    mic: Option<(Instant, Readings)>,
}

struct Shared {
    held: Mutex<Held>,
    /// (stop, the thread runs)
    flags: Mutex<(bool, bool)>,
    changed: Condvar,
    notes: Mutex<Vec<String>>,
    wanted: Wanted,
}

fn reading(value: Value, unit: &str, label: &str) -> Reading {
    Reading {
        unit: unit.into(),
        label: label.into(),
        ..Reading::of(value)
    }
}

/// The readings of a level and a mute: the output device's, or the microphone's.
pub fn readings_of(level: f64, muted: bool, mic: bool) -> Readings {
    let level = lp_py::round_digits(level.clamp(0.0, 100.0), 1);
    let flag = Value::int(i64::from(muted));
    let mut out = Readings::new();
    if mic {
        out.insert(
            "audio.mic.muted".into(),
            reading(flag, "", "Microphone muted"),
        );
        out.insert(
            "audio.mic.volume".into(),
            reading(Value::Float(level), "%", "Microphone level"),
        );
    } else {
        let shown = if muted { 0.0 } else { level };
        out.insert(
            "audio.volume".into(),
            reading(Value::Float(shown), "%", "Volume"),
        );
        out.insert("audio.muted".into(), reading(flag, "", "Muted"));
    }
    out
}

impl Shared {
    fn wants(&self, prefix: &str) -> bool {
        wanted(&self.wanted.keys(), prefix)
    }

    fn note(&self, line: String) {
        if let Ok(mut notes) = self.notes.lock() {
            notes.push(line);
        }
    }

    /// New values: from the thread's reads, or from Windows' own notice.
    fn publish(&self, level: f64, muted: bool, mic: bool) {
        let readings = readings_of(level, muted, mic);
        if let Ok(mut held) = self.held.lock() {
            let slot = if mic { &mut held.mic } else { &mut held.state };
            *slot = Some((Instant::now(), readings));
        }
    }

    /// Waits `seconds`, or until the source is closed: true then.
    fn wait(&self, seconds: f64) -> bool {
        let Ok(flags) = self.flags.lock() else {
            return true;
        };
        self.changed
            .wait_timeout_while(flags, Duration::from_secs_f64(seconds), |flags| !flags.0)
            .map(|(flags, _)| flags.0)
            .unwrap_or(true)
    }
}

fn run_thread(shared: &Arc<Shared>) {
    let publisher = |mic: bool| -> Publish {
        let shared = Arc::downgrade(shared);
        Arc::new(move |level, muted| {
            if let Some(shared) = shared.upgrade() {
                shared.publish(level, muted, mic);
            }
        })
    };
    let way_of = |way: &dyn Way| if way.events() { "events" } else { "polling" };
    let mut source: Option<Box<dyn Way>> = None;
    let mut mic: Option<Box<dyn Way>> = None;
    let mut mic_retry = Instant::now();
    let mut looked = Instant::now();
    let mut device_at = Instant::now();
    let mut warned = false;
    loop {
        if shared.flags.lock().map(|flags| flags.0).unwrap_or(true) {
            break;
        }
        let now = Instant::now();
        if shared.wants("audio.") {
            looked = now;
        } else if now.duration_since(looked).as_secs_f64() > IDLE_SECONDS {
            break;
        }
        if shared.wants("audio.mic") {
            // one look at the microphone, while its readings are wanted
            if mic.is_none() && now >= mic_retry {
                mic = open_way(publisher(true), true).ok();
                if mic.is_none() {
                    mic_retry = now + Duration::from_secs_f64(RETRY_SECONDS);
                }
            }
            if let Some((level, muted)) = mic.as_mut().and_then(|mic| mic.sample()) {
                shared.publish(level, muted, true);
            }
        }
        let Some(way) = source.as_mut() else {
            match open_way(publisher(false), false) {
                Ok(way) => {
                    device_at = now + Duration::from_secs_f64(DEVICE_CHECK_SECONDS);
                    // how it comes in, at the start
                    if warned {
                        warned = false;
                        shared.note(format!(
                            "audio: the volume can be read now, by {}",
                            way_of(way.as_ref())
                        ));
                    } else {
                        shared.note(format!(
                            "audio: the volume is read by {}",
                            way_of(way.as_ref())
                        ));
                    }
                    source = Some(way);
                }
                Err(why) => {
                    if !warned {
                        warned = true;
                        shared.note(format!("audio: the volume cannot be read: {why}"));
                    }
                    if shared.wait(RETRY_SECONDS) {
                        break;
                    }
                }
            }
            continue;
        };
        if let Some((level, muted)) = way.sample() {
            shared.publish(level, muted, false);
        }
        if now >= device_at {
            device_at = now + Duration::from_secs_f64(DEVICE_CHECK_SECONDS);
            if way.check_device() {
                shared.note(format!(
                    "audio: the output device changed; the volume follows it (by {})",
                    way_of(way.as_ref())
                ));
                if let Some((level, muted)) = way.sample() {
                    shared.publish(level, muted, false);
                }
            }
        }
        if shared.wait(way.interval()) {
            break;
        }
    }
    if let Ok(mut held) = shared.held.lock() {
        *held = Held::default();
    }
    drop(source); // (in this thread: COM belongs to it)
    drop(mic);
    if let Ok(mut flags) = shared.flags.lock() {
        flags.1 = false;
    }
}

/// `audio.volume` and `audio.muted` while a theme shows them.
pub struct AudioSource {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl AudioSource {
    pub fn new(wanted: Wanted) -> AudioSource {
        AudioSource {
            shared: Arc::new(Shared {
                held: Mutex::new(Held::default()),
                flags: Mutex::new((false, false)),
                changed: Condvar::new(),
                notes: Mutex::new(Vec::new()),
                wanted,
            }),
            thread: None,
        }
    }

    /// The volume read last (none older than `STALE_SECONDS`); the thread is started where
    /// a theme reads it and it does not run.
    pub fn read(&mut self) -> Readings {
        let (stopped, runs) = self
            .shared
            .flags
            .lock()
            .map(|f| *f)
            .unwrap_or((true, false));
        if !runs {
            if stopped || !self.shared.wants("audio.") {
                return Readings::new();
            }
            if let Some(ended) = self.thread.take() {
                let _ = ended.join();
            }
            if let Ok(mut flags) = self.shared.flags.lock() {
                flags.1 = true;
            }
            let shared = self.shared.clone();
            self.thread = std::thread::Builder::new()
                .name("sensors-audio".into())
                .spawn(move || run_thread(&shared))
                .ok();
        }
        let mut out = Readings::new();
        if let Ok(held) = self.shared.held.lock() {
            for (at, readings) in [&held.state, &held.mic].into_iter().flatten() {
                if at.elapsed().as_secs_f64() <= STALE_SECONDS {
                    out.extend(readings.clone());
                }
            }
        }
        out
    }

    /// What the source noted for the log since it was last asked.
    pub fn notes(&self) -> Vec<String> {
        self.shared
            .notes
            .lock()
            .map(|mut notes| std::mem::take(&mut *notes))
            .unwrap_or_default()
    }

    pub fn close(&mut self) {
        if let Ok(mut flags) = self.shared.flags.lock() {
            flags.0 = true;
        }
        self.shared.changed.notify_all();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for AudioSource {
    fn drop(&mut self) {
        self.close();
    }
}
