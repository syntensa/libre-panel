//! What of this computer's screen a panel can show as a monitor (capture.py, for mirror.rs),
//! and how ffmpeg captures it: the monitors and the windows, each with an id that names it
//! again, and the input ffmpeg reads its pictures from.
//!
//! - **Windows**: the monitors as Windows lists them (EnumDisplayMonitors, in physical pixels:
//!   listed per-monitor DPI aware), named by their device (`\\.\DISPLAY1`), a virtual one
//!   (Virtual Display Driver, extend.rs) marked so; the windows a person sees (visible, not
//!   cloaked, not a tool window), by their handle. ffmpeg captures through
//!   Windows.Graphics.Capture (`gfxcapture`, ffmpeg 8.1), else through GDI (`gdigrab`).
//! - **Linux on X11**: the monitors `xrandr --listmonitors` names (a virtual one of extend.rs
//!   among them), the windows of `_NET_CLIENT_LIST` (`xprop`); ffmpeg's `x11grab` captures a
//!   part of the screen or a window.
//! - **Linux on Wayland**: nothing of the screen is open to a program; the desktop shares it.
//!
//! A part of a monitor (`region`: x, y, width and height within it) is cropped as it is
//! captured. The pictures go on in ffmpeg: fitted into the panel's frame (mirror.rs).

use std::collections::HashMap;
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use lp_py::{Dict, Value};

const RUN_TIMEOUT_S: f64 = 5.0;
/// a virtual monitor's adapter names itself so (lowercase)
#[cfg_attr(not(windows), allow(dead_code))]
const VIRTUAL_MARKS: [&str; 1] = ["virtual display"];

/// A monitor of this computer: `id` names it again (a device on Windows, an output on X11),
/// its place and size on the desktop in pixels, whether it is the main one and whether it is a
/// virtual one (a monitor of its own for a panel, extend.rs).
#[derive(Clone, Debug, PartialEq)]
pub struct Monitor {
    pub id: String,
    pub name: String,
    pub x: i64,
    pub y: i64,
    pub width: i64,
    pub height: i64,
    pub primary: bool,
    pub virtual_: bool,
}

impl Monitor {
    pub fn value(&self) -> Value {
        let mut told = Dict::default();
        told.insert("id".into(), Value::Str(self.id.clone()));
        told.insert("name".into(), Value::Str(self.name.clone()));
        told.insert("x".into(), Value::int(self.x));
        told.insert("y".into(), Value::int(self.y));
        told.insert("width".into(), Value::int(self.width));
        told.insert("height".into(), Value::int(self.height));
        told.insert("primary".into(), Value::Bool(self.primary));
        told.insert("virtual".into(), Value::Bool(self.virtual_));
        Value::Dict(told)
    }
}

/// A window a person sees: `id` (its handle) names it again; its title and program.
#[derive(Clone, Debug, PartialEq)]
pub struct Window {
    pub id: String,
    pub title: String,
    pub program: String,
}

impl Window {
    pub fn value(&self) -> Value {
        let mut told = Dict::default();
        told.insert("id".into(), Value::Str(self.id.clone()));
        told.insert("title".into(), Value::Str(self.title.clone()));
        told.insert("program".into(), Value::Str(self.program.clone()));
        Value::Dict(told)
    }
}

/// How ffmpeg gets the pictures of a source: its input (`-f ... -i ...`), the filters that
/// bring them to the processor (`hwdownload`) and crop them, and where a program feeds it raw
/// frames (Wayland: `feeder`, writing to ffmpeg's input).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Capture {
    pub inputs: Vec<String>,
    pub prefilter: String,
    pub feeder: Vec<String>,
    pub label: String,
}

/// A source that cannot be captured here, said for the person.
#[derive(Debug)]
pub struct CaptureError(pub String);

/// platform: where Libre Panel runs: `windows`, `x11`, `wayland` or `other`.
pub fn platform() -> &'static str {
    if cfg!(windows) {
        return "windows";
    }
    if !cfg!(target_os = "linux") {
        return "other";
    }
    let set = |name: &str| std::env::var_os(name).is_some_and(|v| !v.is_empty());
    if set("WAYLAND_DISPLAY") || std::env::var("XDG_SESSION_TYPE").as_deref() == Ok("wayland") {
        return "wayland";
    }
    if set("DISPLAY") {
        "x11"
    } else {
        "other"
    }
}

/// A command without a window of its own (Windows).
pub fn quiet(command: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
}

/// What a program printed, if it ended well within the time given (stdout, stderr, code).
fn output(command: &mut Command, timeout: f64) -> Option<(String, String, i32)> {
    let mut child = quiet(command)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .ok()?;
    let (mut out, mut err) = (child.stdout.take()?, child.stderr.take()?);
    let reading = std::thread::spawn(move || {
        let mut text = Vec::new();
        let _ = std::io::Read::read_to_end(&mut out, &mut text);
        text
    });
    let errors = std::thread::spawn(move || {
        let mut text = Vec::new();
        let _ = std::io::Read::read_to_end(&mut err, &mut text);
        text
    });
    let end = std::time::Instant::now() + Duration::from_secs_f64(timeout);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if std::time::Instant::now() < end => {
                std::thread::sleep(Duration::from_millis(10));
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    };
    let text = String::from_utf8_lossy(&reading.join().unwrap_or_default()).into_owned();
    let said = String::from_utf8_lossy(&errors.join().unwrap_or_default()).into_owned();
    Some((text, said, status.code().unwrap_or(-1)))
}

// -- ffmpeg ---------------------------------------------------------------------------------

/// each ffmpeg's filters and input devices
type Known = HashMap<String, (Vec<String>, Vec<String>)>;
static CAN: Mutex<Option<Known>> = Mutex::new(None);

/// ffmpeg_can: whether `ffmpeg` has the filter or input device `name` (gfxcapture, ...).
pub fn ffmpeg_can(ffmpeg: &str, name: &str) -> bool {
    let mut kept = CAN
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let known = kept.get_or_insert_with(HashMap::new);
    let (filters, devices) = known.entry(ffmpeg.to_string()).or_insert_with(|| {
        let listed = |what: &str| {
            output(
                Command::new(ffmpeg).args(["-hide_banner", what]),
                RUN_TIMEOUT_S,
            )
            .map(|(text, _, _)| text)
            .unwrap_or_default()
        };
        let filters = listed("-filters")
            .lines()
            .filter_map(|line| {
                let words: Vec<&str> = line.split_whitespace().collect();
                (words.len() > 2).then(|| words[1].to_string())
            })
            .collect();
        let devices = listed("-devices")
            .lines()
            .filter_map(|line| {
                let mut words = line.split_whitespace();
                let flags = words.next()?;
                (flags.starts_with('D')).then(|| words.next().map(str::to_string))?
            })
            .collect();
        (filters, devices)
    });
    filters.iter().any(|f| f == name) || devices.iter().any(|d| d == name)
}

/// _crop: `region` within a monitor of width x height, kept inside it, even-sized (as an
/// encoder takes it); the whole monitor without one.
pub fn crop(region: Option<(i64, i64, i64, i64)>, width: i64, height: i64) -> (i64, i64, i64, i64) {
    let Some((x, y, w, h)) = region else {
        return (0, 0, width, height);
    };
    let (x, y) = (x.min(width - 2).max(0), y.min(height - 2).max(0));
    let (w, h) = (w.min(width - x).max(2), h.min(height - y).max(2));
    (x, y, w - w.rem_euclid(2), h - h.rem_euclid(2))
}

fn strings(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|part| part.to_string()).collect()
}

/// monitor_capture: ffmpeg's input for `monitor` (or a `region` of it) at up to `fps` frames
/// a second, as `place` (platform()) captures it.
pub fn monitor_capture(
    ffmpeg: &str,
    monitor: &Monitor,
    fps: i64,
    cursor: bool,
    region: Option<(i64, i64, i64, i64)>,
    place: &str,
) -> Result<Capture, CaptureError> {
    let (x, y, w, h) = crop(region, monitor.width, monitor.height);
    let mouse = if cursor { "1" } else { "0" };
    if place == "windows" {
        if ffmpeg_can(ffmpeg, "gfxcapture") {
            let pick = match win::monitor_handle(&monitor.id) {
                Some(handle) => format!("hmonitor={handle:#x}"),
                None => format!("monitor_idx={}", index_of(monitor)),
            };
            let cut = if region.is_some() {
                format!(
                    ":crop_left={x}:crop_top={y}:crop_right={}:crop_bottom={}",
                    monitor.width - x - w,
                    monitor.height - y - h
                )
            } else {
                String::new()
            };
            let source =
                format!("gfxcapture={pick}:max_framerate={fps}:capture_cursor={mouse}{cut}");
            return Ok(Capture {
                inputs: vec!["-f".into(), "lavfi".into(), "-i".into(), source],
                prefilter: "hwdownload,format=bgra".into(),
                label: monitor.name.clone(),
                ..Capture::default()
            });
        }
        let inputs = vec![
            "-f".into(),
            "gdigrab".into(),
            "-framerate".into(),
            fps.to_string(),
            "-draw_mouse".into(),
            mouse.into(),
            "-offset_x".into(),
            (monitor.x + x).to_string(),
            "-offset_y".into(),
            (monitor.y + y).to_string(),
            "-video_size".into(),
            format!("{w}x{h}"),
            "-i".into(),
            "desktop".into(),
        ];
        return Ok(Capture {
            inputs,
            label: monitor.name.clone(),
            ..Capture::default()
        });
    }
    if place == "x11" {
        let display = std::env::var("DISPLAY").unwrap_or_else(|_| ":0".into());
        let mut inputs = strings(&["-f", "x11grab", "-framerate"]);
        inputs.extend([
            fps.to_string(),
            "-draw_mouse".into(),
            mouse.into(),
            "-video_size".into(),
            format!("{w}x{h}"),
            "-i".into(),
            format!("{display}+{},{}", monitor.x + x, monitor.y + y),
        ]);
        return Ok(Capture {
            inputs,
            label: monitor.name.clone(),
            ..Capture::default()
        });
    }
    Err(CaptureError(
        "the desktop shares its screen itself here (Wayland)".into(),
    ))
}

/// window_capture: ffmpeg's input for `window` at up to `fps` frames a second.
pub fn window_capture(
    ffmpeg: &str,
    window: &Window,
    fps: i64,
    cursor: bool,
    place: &str,
) -> Result<Capture, CaptureError> {
    let mouse = if cursor { "1" } else { "0" };
    if place == "windows" {
        let handle = parse_handle(&window.id).unwrap_or(0);
        if ffmpeg_can(ffmpeg, "gfxcapture") {
            let source =
                format!("gfxcapture=hwnd={handle:#x}:max_framerate={fps}:capture_cursor={mouse}");
            return Ok(Capture {
                inputs: vec!["-f".into(), "lavfi".into(), "-i".into(), source],
                prefilter: "hwdownload,format=bgra".into(),
                label: window.title.clone(),
                ..Capture::default()
            });
        }
        let mut inputs = strings(&["-f", "gdigrab", "-framerate"]);
        inputs.extend([
            fps.to_string(),
            "-draw_mouse".into(),
            mouse.into(),
            "-i".into(),
            format!("hwnd={handle:#x}"),
        ]);
        return Ok(Capture {
            inputs,
            label: window.title.clone(),
            ..Capture::default()
        });
    }
    if place == "x11" {
        let display = std::env::var("DISPLAY").unwrap_or_else(|_| ":0".into());
        let mut inputs = strings(&["-f", "x11grab", "-framerate"]);
        inputs.extend([
            fps.to_string(),
            "-draw_mouse".into(),
            mouse.into(),
            "-window_id".into(),
            window.id.clone(),
            "-i".into(),
            display,
        ]);
        return Ok(Capture {
            inputs,
            label: window.title.clone(),
            ..Capture::default()
        });
    }
    Err(CaptureError(
        "the desktop shares its windows itself here (Wayland)".into(),
    ))
}

/// int(text, 0): "0x1a2b" or "6699"
fn parse_handle(text: &str) -> Option<i64> {
    match text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")) {
        Some(hex) => i64::from_str_radix(hex, 16).ok(),
        None => text.parse().ok(),
    }
}

// -- what there is ----------------------------------------------------------------------------

/// monitors: the monitors of this computer, the main one first; none where none can be listed.
pub fn monitors(place: &str) -> Vec<Monitor> {
    let mut found = match place {
        "windows" => win::monitors(),
        "x11" => match run(&["xrandr", "--listmonitors"]) {
            Ok(text) => parse_xrandr(&text),
            Err(why) => {
                say(&format!("capture: no monitors listed ({why})"));
                Vec::new()
            }
        },
        // (GNOME's; elsewhere the desktop's dialog lists them)
        "wayland" => crate::wayland::monitors(),
        _ => Vec::new(),
    };
    // (sorted: the main one first, real before virtual, then by place)
    found.sort_by_key(|m| (!m.primary, m.virtual_, m.x, m.y));
    found
}

/// windows: the windows a person sees, as the desktop lists them; none where none can be.
pub fn windows(place: &str) -> Vec<Window> {
    match place {
        "windows" => win::windows(),
        "x11" => x11_windows().unwrap_or_else(|why| {
            say(&format!("capture: no windows listed ({why})"));
            Vec::new()
        }),
        _ => Vec::new(),
    }
}

pub fn find_monitor(id: &str, place: &str) -> Option<Monitor> {
    monitors(place).into_iter().find(|m| m.id == id)
}

pub fn find_window(id: &str, place: &str) -> Option<Window> {
    windows(place).into_iter().find(|w| w.id == id)
}

fn say(message: &str) {
    crate::logs::log(crate::logs::Level::Info, "libre_panel.capture", message);
}

/// shutil.which: the program of this name on the PATH.
pub fn which(name: &str) -> Option<std::path::PathBuf> {
    let paths = std::env::var_os("PATH")?;
    let names: Vec<String> = if cfg!(windows) && !name.to_lowercase().ends_with(".exe") {
        vec![format!("{name}.exe"), name.to_string()]
    } else {
        vec![name.to_string()]
    };
    std::env::split_paths(&paths)
        .flat_map(|folder| {
            names
                .iter()
                .map(move |n| folder.join(n))
                .collect::<Vec<_>>()
        })
        .find(|path| runs(path))
}

#[cfg(unix)]
fn runs(path: &std::path::Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

#[cfg(not(unix))]
fn runs(path: &std::path::Path) -> bool {
    path.is_file()
}

/// _run: what a program found on PATH printed (an error where it is not there or failed).
pub fn run(command: &[&str]) -> Result<String, String> {
    let program = which(command[0]).ok_or_else(|| format!("no {}", command[0]))?;
    let (text, said, code) = output(Command::new(program).args(&command[1..]), RUN_TIMEOUT_S)
        .ok_or_else(|| format!("{}: it did not end", command[0]))?;
    if code != 0 {
        let said: String = said.trim().chars().take(200).collect();
        let said = if said.is_empty() {
            code.to_string()
        } else {
            said
        };
        return Err(format!("{}: {said}", command[0]));
    }
    Ok(text)
}

/// parse_xrandr: the monitors of `xrandr --listmonitors`:
/// ` 0: +*DP-1 2560/597x1440/336+0+0  DP-1`; one without an output of its own (`none`) is a
/// virtual one (extend.rs).
pub fn parse_xrandr(text: &str) -> Vec<Monitor> {
    let mut found = Vec::new();
    for line in text.lines() {
        if let Some(monitor) = xrandr_line(line) {
            found.push(monitor);
        }
    }
    found
}

fn xrandr_line(line: &str) -> Option<Monitor> {
    let rest = line.trim_start();
    let (number, rest) = rest.split_once(':')?;
    if number.is_empty() || !number.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let rest = rest.strip_prefix(|c: char| c.is_whitespace())?.trim_start();
    let flags_len = rest
        .find(|c: char| c != '+' && c != '*')
        .unwrap_or(rest.len());
    let (flags, rest) = rest.split_at(flags_len);
    let name_len = rest.find(char::is_whitespace)?;
    let (name, rest) = rest.split_at(name_len);
    if name.is_empty() {
        return None;
    }
    let rest = rest.trim_start();
    let geometry_len = rest.find(char::is_whitespace).unwrap_or(rest.len());
    let (geometry, outputs) = rest.split_at(geometry_len);
    // "2560/597x1440/336+0+0"
    let (w, rest) = geometry.split_once('/')?;
    let (_, rest) = rest.split_once('x')?;
    let (h, rest) = rest.split_once('/')?;
    let rest = rest.trim_start_matches(|c: char| c.is_ascii_digit());
    let rest = rest.strip_prefix('+')?;
    let (x, y) = split_place(rest)?;
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    if !digits(w) || !digits(h) {
        return None;
    }
    let outputs: Vec<&str> = outputs.split_whitespace().collect();
    let virtual_ =
        outputs.is_empty() || outputs == ["none"] || name.to_lowercase().starts_with("librepanel");
    Some(Monitor {
        id: name.into(),
        name: name.into(),
        x,
        y,
        width: w.parse().ok()?,
        height: h.parse().ok()?,
        primary: flags.contains('*'),
        virtual_,
    })
}

/// "0+0", "-1920+0": the x and the y after the first `+`
fn split_place(text: &str) -> Option<(i64, i64)> {
    let start = usize::from(text.starts_with('-'));
    let at = text[start..].find('+')? + start;
    let number = |s: &str| {
        let body = s.strip_prefix('-').unwrap_or(s);
        (!body.is_empty() && body.bytes().all(|b| b.is_ascii_digit()))
            .then(|| s.parse::<i64>().ok())
            .flatten()
    };
    Some((number(&text[..at])?, number(&text[at + 1..])?))
}

fn x11_windows() -> Result<Vec<Window>, String> {
    let listed = run(&["xprop", "-root", "_NET_CLIENT_LIST"])?;
    let after = listed
        .split_once('=')
        .map_or(listed.as_str(), |(_, rest)| rest);
    let mut found = Vec::new();
    for handle in hex_numbers(after) {
        let Ok(said) = run(&[
            "xprop",
            "-id",
            &handle,
            "_NET_WM_NAME",
            "WM_CLASS",
            "_NET_WM_STATE",
        ]) else {
            continue;
        };
        if said.contains("_NET_WM_STATE_SKIP_TASKBAR") || said.contains("_NET_WM_STATE_HIDDEN") {
            continue;
        }
        let title = quoted_after(&said, "_NET_WM_NAME(UTF8_STRING) = \"", true);
        let program = said
            .find("WM_CLASS(STRING) = \"")
            .and_then(|at| {
                let rest = &said[at + "WM_CLASS(STRING) = \"".len()..];
                let close = rest.find('"')?;
                let rest = rest[close + 1..].strip_prefix(", \"")?;
                Some(rest[..rest.find('"')?].to_string())
            })
            .unwrap_or_default();
        if let Some(title) = title.filter(|t| !t.trim().is_empty()) {
            found.push(Window {
                id: handle,
                title,
                program,
            });
        }
    }
    Ok(found)
}

/// re.findall(r"0x[0-9a-fA-F]+")
fn hex_numbers(text: &str) -> Vec<String> {
    let bytes = text.as_bytes();
    let mut found = Vec::new();
    let mut at = 0;
    while at + 2 < bytes.len() + 1 {
        if bytes.get(at) == Some(&b'0') && bytes.get(at + 1) == Some(&b'x') {
            let mut end = at + 2;
            while end < bytes.len() && bytes[end].is_ascii_hexdigit() {
                end += 1;
            }
            if end > at + 2 {
                found.push(text[at..end].to_string());
                at = end;
                continue;
            }
        }
        at += 1;
    }
    found
}

/// re.search(prefix + "(.*)\"") on one line: the text up to the last quote of the line
fn quoted_after(text: &str, prefix: &str, greedy: bool) -> Option<String> {
    let at = text.find(prefix)?;
    let rest = &text[at + prefix.len()..];
    let line = rest.split('\n').next().unwrap_or(rest);
    let close = if greedy {
        line.rfind('"')?
    } else {
        line.find('"')?
    };
    Some(line[..close].to_string())
}

fn index_of(monitor: &Monitor) -> i64 {
    let digits: String = monitor
        .id
        .chars()
        .rev()
        .take_while(char::is_ascii_digit)
        .collect::<Vec<char>>()
        .into_iter()
        .rev()
        .collect();
    digits.parse::<i64>().map_or(0, |n| (n - 1).max(0))
}

// -- Windows ----------------------------------------------------------------------------------

#[cfg(windows)]
pub mod win {
    use super::{Monitor, Window, VIRTUAL_MARKS};
    use std::collections::HashMap;
    use windows_sys::core::BOOL;
    use windows_sys::Win32::Foundation::{HWND, LPARAM, RECT};
    use windows_sys::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_CLOAKED};
    use windows_sys::Win32::Graphics::Gdi::{
        EnumDisplayDevicesW, EnumDisplayMonitors, GetMonitorInfoW, DISPLAY_DEVICEW, HDC, HMONITOR,
        MONITORINFOEXW,
    };
    use windows_sys::Win32::UI::HiDpi::{
        SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetWindow, GetWindowLongW, GetWindowTextLengthW, GetWindowTextW,
        GetWindowThreadProcessId, IsWindowVisible, GWL_EXSTYLE, GW_OWNER, WS_EX_TOOLWINDOW,
    };

    fn text(wide: &[u16]) -> String {
        let end = wide.iter().position(|c| *c == 0).unwrap_or(wide.len());
        String::from_utf16_lossy(&wide[..end])
    }

    /// `{"\\.\DISPLAY1": "the graphics adapter's name"}` (a virtual one names itself)
    pub fn adapters() -> HashMap<String, String> {
        let mut found = HashMap::new();
        // SAFETY: the device's size is set as the call wants; it fills the rest.
        unsafe {
            let mut index = 0;
            loop {
                let mut device: DISPLAY_DEVICEW = std::mem::zeroed();
                device.cb = size_of::<DISPLAY_DEVICEW>() as u32;
                if EnumDisplayDevicesW(std::ptr::null(), index, &mut device, 0) == 0 {
                    return found;
                }
                found.insert(text(&device.DeviceName), text(&device.DeviceString));
                index += 1;
            }
        }
    }

    struct Found {
        all: Vec<(HMONITOR, MONITORINFOEXW)>,
    }

    unsafe extern "system" fn each_monitor(
        handle: HMONITOR,
        _dc: HDC,
        _rect: *mut RECT,
        data: LPARAM,
    ) -> BOOL {
        // SAFETY: `data` is the Found the caller lent for the call.
        let found = unsafe { &mut *(data as *mut Found) };
        let mut info: MONITORINFOEXW = unsafe { std::mem::zeroed() };
        info.monitorInfo.cbSize = size_of::<MONITORINFOEXW>() as u32;
        if unsafe { GetMonitorInfoW(handle, (&raw mut info).cast()) } != 0 {
            found.all.push((handle, info));
        }
        1
    }

    fn all_monitors() -> Vec<(HMONITOR, MONITORINFOEXW)> {
        let mut found = Found { all: Vec::new() };
        // SAFETY: per-monitor DPI aware while listing (places and sizes in physical pixels),
        // as it was after; the callback gets the Found above.
        unsafe {
            let before = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
            EnumDisplayMonitors(
                std::ptr::null_mut(),
                std::ptr::null(),
                Some(each_monitor),
                (&raw mut found) as LPARAM,
            );
            if !before.is_null() {
                SetThreadDpiAwarenessContext(before);
            }
        }
        found.all
    }

    pub fn monitors() -> Vec<Monitor> {
        let adapters = adapters();
        all_monitors()
            .into_iter()
            .map(|(_, info)| {
                let device = text(&info.szDevice);
                let adapter = adapters.get(&device).cloned().unwrap_or_default();
                let digits: String = device
                    .chars()
                    .rev()
                    .take_while(char::is_ascii_digit)
                    .collect::<String>()
                    .chars()
                    .rev()
                    .collect();
                let name = if digits.is_empty() {
                    device.clone()
                } else {
                    format!("Display {digits}")
                };
                let virtual_ = VIRTUAL_MARKS
                    .iter()
                    .any(|mark| adapter.to_lowercase().contains(mark));
                let r = info.monitorInfo.rcMonitor;
                Monitor {
                    id: device,
                    name,
                    x: i64::from(r.left),
                    y: i64::from(r.top),
                    width: i64::from(r.right - r.left),
                    height: i64::from(r.bottom - r.top),
                    primary: info.monitorInfo.dwFlags & 1 != 0,
                    virtual_,
                }
            })
            .collect()
    }

    /// the HMONITOR of the monitor named `device` now (handles change as monitors come)
    pub fn monitor_handle(device: &str) -> Option<usize> {
        all_monitors()
            .into_iter()
            .find(|(_, info)| text(&info.szDevice) == device)
            .map(|(handle, _)| handle as usize)
    }

    unsafe extern "system" fn each_window(handle: HWND, data: LPARAM) -> BOOL {
        // SAFETY: `data` is the Vec the caller lent; the calls ask about a window by its
        // handle, into buffers of the sizes given.
        let found = unsafe { &mut *(data as *mut Vec<Window>) };
        unsafe {
            if IsWindowVisible(handle) == 0 || !GetWindow(handle, GW_OWNER).is_null() {
                return 1;
            }
            if GetWindowLongW(handle, GWL_EXSTYLE) as u32 & WS_EX_TOOLWINDOW != 0 {
                return 1;
            }
            let mut cloaked = 0u32;
            DwmGetWindowAttribute(
                handle,
                DWMWA_CLOAKED as u32,
                (&raw mut cloaked).cast(),
                size_of::<u32>() as u32,
            );
            if cloaked != 0 {
                return 1; // (another virtual desktop's, a suspended app's)
            }
            let length = GetWindowTextLengthW(handle);
            if length <= 0 {
                return 1;
            }
            let mut title = vec![0u16; length as usize + 1];
            GetWindowTextW(handle, title.as_mut_ptr(), length + 1);
            let mut pid = 0u32;
            GetWindowThreadProcessId(handle, &mut pid);
            if pid == std::process::id() {
                return 1;
            }
            found.push(Window {
                id: format!("{:#x}", handle as usize),
                title: text(&title),
                program: crate::load::program_name(pid),
            });
        }
        1
    }

    pub fn windows() -> Vec<Window> {
        let mut found: Vec<Window> = Vec::new();
        // SAFETY: the callback gets the Vec above for the call.
        unsafe { EnumWindows(Some(each_window), (&raw mut found) as LPARAM) };
        found
    }
}

#[cfg(not(windows))]
pub mod win {
    use super::{Monitor, Window};
    use std::collections::HashMap;

    pub fn adapters() -> HashMap<String, String> {
        HashMap::new()
    }
    pub fn monitors() -> Vec<Monitor> {
        Vec::new()
    }
    pub fn monitor_handle(_device: &str) -> Option<usize> {
        None
    }
    pub fn windows() -> Vec<Window> {
        Vec::new()
    }
}
