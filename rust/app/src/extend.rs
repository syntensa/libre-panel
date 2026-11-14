//! A monitor of the panel's own (extend.py): the computer extends its desktop to it, windows
//! go there as to any monitor, and the panel shows it (mirror.rs, `virtual`). It is on the
//! desktop while a panel shows it, and taken off again after, so the mouse never wanders onto
//! a monitor nobody sees.
//!
//! - **Windows**: a monitor needs a display driver; the Virtual Display Driver (MIT, signed,
//!   VirtualDrivers/Virtual-Display-Driver) gives one. Setting it up ([`setup`]) installs the
//!   driver as the driver's own silent install does: its release's package and NefCon (MIT,
//!   nefarius/nefcon, which makes the device the driver is for), both downloaded and checked
//!   against their SHA-256, the driver's signer trusted and the device made, its settings
//!   (`vdd_settings.xml`) with the panel's size among its resolutions; all of it in one step
//!   with the administrator's rights, so Windows asks once. The driver reads its settings again
//!   when told so through its pipe. Showing it puts its monitor on the desktop at the panel's
//!   size, right of the others (ChangeDisplaySettingsEx, no rights needed); stopping takes it
//!   off.
//! - **Linux on X11**: a part of the screen beyond the monitors, as a monitor of its own
//!   (`xrandr --fb` and `--setmonitor ... none`). Nothing is installed.
//! - **Linux on Wayland**: the desktop makes the monitor as the panel's capture starts.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use lp_py::{Dict, Value};
use sha2::{Digest, Sha256};

use crate::capture::{self, Monitor};

pub const VDD_PIPE: &str = r"\\.\pipe\MTTVirtualDisplayPipe";
const VDD_FOLDER: &str = r"C:\VirtualDisplayDriver";
/// its adapter's name, lowercase
const VDD_NAME: &str = "virtual display driver";
/// the device its INF is for, made by NefCon
const VDD_HARDWARE_ID: &str = r"Root\MttVDD";
const RELEASES: &str =
    "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/download/25.7.23/";
/// The driver alone, for Windows' own machine (its x64 package is named x86), and its SHA-256
fn vdd_package(machine: &str) -> (String, &'static str) {
    if machine == "ARM64" {
        (
            format!("{RELEASES}VirtualDisplayDriver-ARM64.Driver.Only.zip"),
            "dcd487161469c726bab60f174a7e7f70ee02afd437cad0d5c0e6755953de5a25",
        )
    } else {
        (
            format!("{RELEASES}VirtualDisplayDriver-x86.Driver.Only.zip"),
            "e24210692b442b39af763536330ce78b423f19342b7a7792c26de3944e418b3a",
        )
    }
}
const NEFCON: (&str, &str) = (
    "https://github.com/nefarius/nefcon/releases/download/v1.21.0/nefcon_v1.21.0.zip",
    "4c8e68f6b663598d77b7c3f76459b16fad00994e53ea8af0bd106a805dd5c18b",
);
const DOWNLOAD_LIMIT: usize = 16 * 1024 * 1024;
const DOWNLOAD_TIMEOUT_S: f64 = 60.0;
const INSTALL_TIMEOUT_S: f64 = 300.0;
/// ERROR_SUCCESS_REBOOT_REQUIRED
const REBOOT_REQUIRED: i64 = 3010;
/// the X11 monitor's name
pub const X11_NAME: &str = "LibrePanel";
const REFRESH: i64 = 60;

/// What keeps the panel's own monitor from being set up or shown, said for the person.
#[derive(Debug)]
pub struct ExtendError(pub String);

fn say(message: &str) {
    crate::logs::log(crate::logs::Level::Info, "libre_panel.extend", message);
}

fn warn(message: &str) {
    crate::logs::log(crate::logs::Level::Warning, "libre_panel.extend", message);
}

/// state: for the app: whether a monitor of the panel's own can be had here (`how`: "vdd",
/// "xrandr", "wayland", or ""), whether it is set up (`ready`) and what is missing.
pub fn state(size: Option<(i64, i64)>, place: &str) -> Dict {
    let mut told = Dict::default();
    if place == "wayland" {
        // (the desktop makes it as the capture starts: wayland.rs)
        told.insert("how".into(), Value::from("wayland"));
        told.extend(crate::wayland::virtual_state());
        return told;
    }
    let (how, ready, missing) = match place {
        "windows" => {
            let installed = vdd_installed();
            let sized = installed && size.is_some_and(|size| vdd_has_resolution(size, None));
            let missing = if sized {
                ""
            } else if installed {
                "resolution"
            } else {
                "driver"
            };
            ("vdd", sized, missing)
        }
        "x11" => {
            let found = capture::which("xrandr").is_some();
            ("xrandr", found, if found { "" } else { "xrandr" })
        }
        _ => ("", false, ""),
    };
    told.insert("how".into(), Value::from(how));
    told.insert("ready".into(), Value::Bool(ready));
    told.insert("missing".into(), Value::from(missing));
    told
}

/// virtual_monitor: the panel's own monitor while it is on the desktop (Windows, X11).
pub fn virtual_monitor(place: &str) -> Option<Monitor> {
    capture::monitors(place).into_iter().find(|m| m.virtual_)
}

/// attach: the panel's own monitor on the desktop, `size` large.
pub fn attach(size: (i64, i64), place: &str) -> Result<(), ExtendError> {
    match place {
        "windows" => vdd_attach(size),
        "x11" => x11_attach(size),
        _ => Ok(()), // (Wayland: the capture makes it)
    }
}

/// detach: the panel's own monitor off the desktop again (nothing where there is none).
pub fn detach(place: &str) {
    let done = match place {
        "windows" => {
            vdd_detach();
            Ok(())
        }
        "x11" => x11_detach(),
        _ => Ok(()),
    };
    if let Err(ExtendError(why)) = done {
        warn(&format!("monitor: the panel's own monitor stays ({why})"));
    }
}

// -- Windows: the Virtual Display Driver -------------------------------------------------------

/// vdd_folder: where the driver reads its settings (its VDDPATH, else its default).
pub fn vdd_folder() -> PathBuf {
    win::vdd_path().map_or_else(|| PathBuf::from(VDD_FOLDER), PathBuf::from)
}

/// vdd_installed: whether the driver's adapter is there (its monitor on the desktop or not).
pub fn vdd_installed() -> bool {
    cfg!(windows)
        && capture::win::adapters()
            .values()
            .any(|name| name.to_lowercase().contains(VDD_NAME))
}

/// The (width, height) of each `<resolution>` of the driver's settings.
fn resolutions(text: &str) -> Vec<(String, String)> {
    let mut found = Vec::new();
    let mut rest = text;
    while let Some(at) = rest.find("<resolution>") {
        rest = &rest[at + "<resolution>".len()..];
        let tag = |text: &str, name: &str| -> Option<(String, usize)> {
            let text_trim = text.trim_start();
            let skipped = text.len() - text_trim.len();
            let open = format!("<{name}>");
            let inner = text_trim.strip_prefix(&open)?;
            let inner_trim = inner.trim_start();
            let digits: String = inner_trim
                .chars()
                .take_while(char::is_ascii_digit)
                .collect();
            if digits.is_empty() {
                return None;
            }
            let after = inner_trim[digits.len()..].trim_start();
            let close = format!("</{name}>");
            let after = after.strip_prefix(&close)?;
            Some((digits, text.len() - after.len() - skipped + skipped))
        };
        if let Some((width, used)) = tag(rest, "width") {
            if let Some((height, _)) = tag(&rest[used..], "height") {
                found.push((width, height));
            }
        }
    }
    found
}

/// vdd_has_resolution: whether the driver's settings name `size` among its resolutions.
pub fn vdd_has_resolution(size: (i64, i64), text: Option<&str>) -> bool {
    let read;
    let text = match text {
        Some(text) => text,
        None => {
            let Ok(found) = std::fs::read_to_string(vdd_folder().join("vdd_settings.xml")) else {
                return false;
            };
            read = found;
            &read
        }
    };
    resolutions(text).contains(&(size.0.to_string(), size.1.to_string()))
}

/// with_resolution: the driver's settings with `size` among its resolutions.
pub fn with_resolution(text: &str, size: (i64, i64), refresh: i64) -> Result<String, ExtendError> {
    if vdd_has_resolution(size, Some(text)) {
        return Ok(text.to_string());
    }
    let (width, height) = size;
    let inner = " ".repeat(12);
    let entry = format!(
        "\n        <resolution>\n{inner}<width>{width}</width>\n{inner}<height>{height}</height>\n\
         {inner}<refresh_rate>{refresh}</refresh_rate>\n        </resolution>"
    );
    if let Some(at) = text.find("</resolutions>") {
        // (after the last, before the list's end and its indent)
        let start = text[..at].trim_end_matches(char::is_whitespace).len();
        return Ok(format!(
            "{}{entry}{}{}",
            &text[..start],
            &text[start..at],
            &text[at..]
        ));
    }
    if let Some(at) = text.find("</vdd_settings>") {
        let block = format!("    <resolutions>{entry}\n    </resolutions>\n");
        return Ok(format!("{}{block}{}", &text[..at], &text[at..]));
    }
    Err(ExtendError(
        "the driver's settings file is not what it should be".into(),
    ))
}

/// setup: Windows: the driver installed and the panel's size among its resolutions; Windows
/// asks for the administrator's consent once. What was done, said for the person.
pub fn setup(size: (i64, i64)) -> Result<String, ExtendError> {
    if !cfg!(windows) {
        return Err(ExtendError("only on Windows".into()));
    }
    let mut done = Vec::new();
    let installed = !vdd_installed();
    if installed {
        let restart = vdd_install(size)? == REBOOT_REQUIRED;
        done.push(if restart {
            "driver (Windows wants a restart)"
        } else {
            "driver"
        });
        if !wait_for(vdd_installed, 20.0) {
            return Err(ExtendError(
                "the driver is installed, but Windows shows no display of it yet: restart \
                 Windows, then set it up here again"
                    .into(),
            ));
        }
    }
    if !vdd_has_resolution(size, None) {
        vdd_write_resolution(size)?;
        done.push("resolution");
    }
    if let Err(why) = vdd_reload(5) {
        if !installed {
            return Err(why);
        }
        say("monitor: the new driver read its settings as it started");
    }
    vdd_detach(); // (the driver puts its monitor on the desktop: off until a panel shows it)
    Ok(if done.is_empty() {
        "nothing".into()
    } else {
        done.join(", ")
    })
}

fn wait_for(ready: fn() -> bool, seconds: f64) -> bool {
    let end = std::time::Instant::now() + std::time::Duration::from_secs_f64(seconds);
    loop {
        if ready() {
            return true;
        }
        if std::time::Instant::now() >= end {
            return false;
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
}

/// windows_machine: Windows' own machine, "AMD64" or "ARM64".
pub fn windows_machine() -> &'static str {
    match win::native_machine() {
        Some(machine) => machine,
        None if cfg!(target_arch = "aarch64") => "ARM64",
        None => "AMD64",
    }
}

/// download: `url` into `folder`, if it is the file `sha256` names.
fn download(url: &str, sha256: &str, folder: &Path) -> Result<PathBuf, ExtendError> {
    let name = url.rsplit('/').next().unwrap_or("download");
    let target = folder.join(name);
    let agent = format!("libre-panel/{}", lp_py::VERSION);
    let data = lp_net::get(
        url,
        &[("User-Agent", agent.as_str())],
        DOWNLOAD_TIMEOUT_S,
        DOWNLOAD_LIMIT + 1,
    )
    .map_err(|why| ExtendError(format!("{name} could not be downloaded ({})", why.text)))?;
    if data.len() > DOWNLOAD_LIMIT || hex(&Sha256::digest(&data)) != sha256 {
        return Err(ExtendError(format!("{name} is not the file it should be")));
    }
    std::fs::write(&target, &data).map_err(|why| ExtendError(why.to_string()))?;
    Ok(target)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// package_settings: the driver's own settings, as its package brings them.
fn package_settings(package: &Path) -> Result<String, ExtendError> {
    let data = std::fs::read(package).map_err(|why| ExtendError(why.to_string()))?;
    let archive = crate::zip::Archive::new(&data).map_err(|why| ExtendError(why.told()))?;
    let info = archive
        .infolist
        .iter()
        .find(|info| info.filename == "VirtualDisplayDriver/vdd_settings.xml")
        .ok_or_else(|| ExtendError("the driver's settings are not in its package".into()))?;
    let bytes = archive.read(info).map_err(|why| ExtendError(why.told()))?;
    let text = String::from_utf8_lossy(&bytes).into_owned();
    Ok(text.strip_prefix('\u{feff}').unwrap_or(&text).to_string())
}

/// _ps_quote: `text` as a PowerShell string that is taken as it is.
pub fn ps_quote(text: &str) -> String {
    format!("'{}'", text.replace('\'', "''"))
}

/// install_script: what runs with the administrator's rights to install the driver
/// (PowerShell), as extend.py writes it: each file checked again as it is read, what runs or
/// is installed taken from those bytes into a folder only administrators may change.
#[allow(clippy::too_many_arguments)]
pub fn install_script(
    nefcon: &Path,
    nefcon_sha: &str,
    nefcon_program: &str,
    package: &Path,
    package_sha: &str,
    settings: &Path,
    settings_sha: &str,
    target: &Path,
    said: &Path,
) -> String {
    let q = ps_quote;
    let p = |path: &Path| q(&path.to_string_lossy());
    include_str!("vdd_install.ps1")
        .replace("{NEFCON}", &p(nefcon))
        .replace("{NEFCON_SHA}", &q(nefcon_sha))
        .replace("{PACKAGE}", &p(package))
        .replace("{PACKAGE_SHA}", &q(package_sha))
        .replace("{SETTINGS}", &p(settings))
        .replace("{SETTINGS_SHA}", &q(settings_sha))
        .replace("{NEFCON_PROGRAM}", &q(nefcon_program))
        .replace("{TARGET}", &p(target))
        .replace("{HARDWARE_ID}", &q(VDD_HARDWARE_ID))
        .replace("{SAID}", &p(said))
}

/// powershell: Windows' own PowerShell and its parameters to run `script`: given encoded,
/// and without a window.
pub fn powershell(script: &str) -> (String, String) {
    let root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
    let program = format!(r"{root}\System32\WindowsPowerShell\v1.0\powershell.exe");
    let wide: Vec<u8> = script.encode_utf16().flat_map(u16::to_le_bytes).collect();
    let encoded = crate::drawings::base64(&wide);
    (
        program,
        format!(
            "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden \
             -EncodedCommand {encoded}"
        ),
    )
}

fn scratch() -> Result<PathBuf, ExtendError> {
    let mut bytes = [0u8; 6];
    let _ = getrandom::getrandom(&mut bytes);
    let folder = std::env::temp_dir().join(format!("libre-panel-vdd-{}", hex(&bytes)));
    std::fs::create_dir_all(&folder).map_err(|why| ExtendError(why.to_string()))?;
    Ok(folder)
}

/// _vdd_install: the driver installed, with the panel's size among its resolutions (one
/// question of Windows): NefCon's exit code, 0 or REBOOT_REQUIRED.
fn vdd_install(size: (i64, i64)) -> Result<i64, ExtendError> {
    let machine = windows_machine();
    let folder = scratch()?;
    let done = (|| {
        let (url, sha) = vdd_package(machine);
        let package = download(&url, sha, &folder)?;
        let nefcon = download(NEFCON.0, NEFCON.1, &folder)?;
        // (settings that are there already are kept, with the panel's size)
        let current = match std::fs::read_to_string(vdd_folder().join("vdd_settings.xml")) {
            Ok(text) => text.trim_start_matches('\u{feff}').to_string(),
            Err(_) => package_settings(&package)?,
        };
        let settings = folder.join("vdd_settings.xml");
        let bytes = with_resolution(&current, size, REFRESH)?.into_bytes();
        std::fs::write(&settings, &bytes).map_err(|why| ExtendError(why.to_string()))?;
        let said = folder.join("said.txt");
        let nefcon_folder = if machine == "ARM64" { "ARM64" } else { "x64" };
        let script = install_script(
            &nefcon,
            NEFCON.1,
            &format!("{nefcon_folder}/nefconc.exe"),
            &package,
            sha,
            &settings,
            &hex(&Sha256::digest(&bytes)),
            &vdd_folder(),
            &said,
        );
        let (program, parameters) = powershell(&script);
        let code = run_elevated(&program, &parameters, INSTALL_TIMEOUT_S)?;
        let told = std::fs::read(&said)
            .map(|b| String::from_utf8_lossy(&b).trim().to_string())
            .unwrap_or_default();
        Ok((code, told))
    })();
    let _ = std::fs::remove_dir_all(&folder);
    let (code, told) = done?;
    if code == 0 || code == REBOOT_REQUIRED {
        say(&format!(
            "monitor: the Virtual Display Driver installed ({code})"
        ));
        return Ok(code);
    }
    let reason = match told.strip_prefix("error: ") {
        Some(reason) => reason.to_string(),
        None => format!("NefCon said {code}"),
    };
    Err(ExtendError(format!(
        "the driver was not installed ({reason})"
    )))
}

struct SetupState {
    running: bool,
    said: String,
    error: String,
}

static SETUP: Mutex<SetupState> = Mutex::new(SetupState {
    running: false,
    said: String::new(),
    error: String::new(),
});

/// start_setup: setup in the background (the downloads and the driver take a while):
/// setup_state says how it goes. Nothing new while one runs.
pub fn start_setup(size: (i64, i64)) {
    {
        let mut state = SETUP
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if state.running {
            return;
        }
        *state = SetupState {
            running: true,
            said: String::new(),
            error: String::new(),
        };
    }
    let work = move || {
        let done = setup(size);
        let mut state = SETUP
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        match done {
            Ok(said) => {
                say(&format!("monitor: the panel's own monitor set up ({said})"));
                state.said = said;
                state.error = String::new();
            }
            Err(ExtendError(why)) => {
                warn(&format!(
                    "monitor: the panel's own monitor was not set up ({why})"
                ));
                state.said = String::new();
                state.error = why;
            }
        }
        state.running = false;
    };
    let started = std::thread::Builder::new()
        .name("monitor-setup".into())
        .spawn(work);
    if started.is_err() {
        SETUP
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .running = false;
    }
}

/// setup_state: how the last setup went: running, what it did (said) or why not (error).
pub fn setup_state() -> Dict {
    let state = SETUP
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let mut told = Dict::default();
    told.insert("running".into(), Value::Bool(state.running));
    told.insert("said".into(), Value::Str(state.said.clone()));
    told.insert("error".into(), Value::Str(state.error.clone()));
    told
}

fn vdd_write_resolution(size: (i64, i64)) -> Result<(), ExtendError> {
    let target = vdd_folder().join("vdd_settings.xml");
    let text = match std::fs::read_to_string(&target) {
        Ok(text) => text.trim_start_matches('\u{feff}').to_string(),
        Err(why) if why.kind() == std::io::ErrorKind::NotFound => {
            // (a driver installed without them: its own, from its package)
            let folder = scratch()?;
            let (url, sha) = vdd_package(windows_machine());
            let found = download(&url, sha, &folder).and_then(|p| package_settings(&p));
            let _ = std::fs::remove_dir_all(&folder);
            found?
        }
        Err(why) => {
            return Err(ExtendError(format!(
                "the driver's settings cannot be read ({why})"
            )))
        }
    };
    let changed = with_resolution(&text, size, REFRESH)?;
    let staged = std::env::temp_dir().join("libre-panel-vdd_settings.xml");
    std::fs::write(&staged, &changed).map_err(|why| ExtendError(why.to_string()))?;
    if std::fs::write(&target, &changed).is_ok() {
        return Ok(()); // (where the person may write it)
    }
    elevated_copy(&staged, &target)?;
    if !vdd_has_resolution(size, None) {
        return Err(ExtendError(
            "the panel's size did not get into the driver's settings".into(),
        ));
    }
    Ok(())
}

/// _elevated_copy: `source` over `target` as the administrator (Windows asks).
fn elevated_copy(source: &Path, target: &Path) -> Result<(), ExtendError> {
    let root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
    let command = format!(r"{root}\System32\cmd.exe");
    let folder = target.parent().unwrap_or(target);
    let (folder, source, target) = (
        folder.display().to_string(),
        source.display().to_string(),
        target.display().to_string(),
    );
    let parameters = format!(
        "/c (if not exist \"{folder}\" mkdir \"{folder}\") & copy /y \"{source}\" \"{target}\""
    );
    run_elevated(&command, &parameters, 30.0).map(|_| ())
}

/// _run_elevated: `program` with `parameters` as the administrator, Windows asking the
/// person first; its exit code once it has ended.
fn run_elevated(program: &str, parameters: &str, timeout: f64) -> Result<i64, ExtendError> {
    win::run_elevated(program, parameters, timeout).map_err(ExtendError)
}

/// _vdd_reload: the driver reads its settings again (its pipe: RELOAD_DRIVER); a driver that
/// has just started opens its pipe a moment later, so it is tried `tries` times.
fn vdd_reload(tries: u32) -> Result<(), ExtendError> {
    for attempt in 0..tries {
        let sent = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(VDD_PIPE)
            .and_then(|mut pipe| {
                let wide: Vec<u8> = "RELOAD_DRIVER"
                    .encode_utf16()
                    .flat_map(u16::to_le_bytes)
                    .collect();
                std::io::Write::write_all(&mut pipe, &wide)
            });
        match sent {
            Ok(()) => break,
            Err(why) if attempt + 1 >= tries => {
                return Err(ExtendError(format!("the driver did not answer ({why})")))
            }
            Err(_) => std::thread::sleep(std::time::Duration::from_secs(1)),
        }
    }
    std::thread::sleep(std::time::Duration::from_millis(1500));
    Ok(())
}

/// _vdd_device: the display device of the driver's monitor (`\\.\DISPLAY7`).
fn vdd_device() -> Option<String> {
    capture::win::adapters()
        .into_iter()
        .find(|(_, adapter)| adapter.to_lowercase().contains(VDD_NAME))
        .map(|(name, _)| name)
}

fn vdd_attach(size: (i64, i64)) -> Result<(), ExtendError> {
    let device = vdd_device()
        .ok_or_else(|| ExtendError("the Virtual Display Driver is not set up".into()))?;
    if !vdd_has_resolution(size, None) {
        return Err(ExtendError(
            "the driver does not know the panel's size yet: set it up again".into(),
        ));
    }
    let right = capture::monitors("windows")
        .iter()
        .filter(|m| m.id != device)
        .map(|m| m.x + m.width)
        .max()
        .unwrap_or(0);
    win::place_display(&device, Some((size, (right, 0)))).map_err(|said| {
        ExtendError(format!(
            "Windows did not put the panel's monitor on the desktop ({said})"
        ))
    })?;
    say(&format!(
        "monitor: the panel's own monitor {device} on the desktop, {}x{}",
        size.0, size.1
    ));
    Ok(())
}

fn vdd_detach() {
    if let Some(device) = vdd_device() {
        let _ = win::place_display(&device, None);
        say("monitor: the panel's own monitor off the desktop");
    }
}

// -- X11: xrandr ----------------------------------------------------------------------------

fn xrandr(args: &[&str]) -> Result<String, ExtendError> {
    let mut command = vec!["xrandr"];
    command.extend_from_slice(args);
    capture::run(&command).map_err(ExtendError)
}

/// x11_plan: where the panel's own monitor goes on X11: right of the others, at the top; the
/// screen's size it needs. `(screen size, (x, y))`.
pub fn x11_plan(
    screen: (i64, i64),
    monitors: &[Monitor],
    size: (i64, i64),
) -> ((i64, i64), (i64, i64)) {
    let real: Vec<&Monitor> = monitors.iter().filter(|m| !m.virtual_).collect();
    let right = real.iter().map(|m| m.x + m.width).max().unwrap_or(0);
    let bottom = real.iter().map(|m| m.y + m.height).max().unwrap_or(0);
    let (width, height) = size;
    let screen_h = if real.is_empty() { screen.1 } else { 0 };
    (
        (right + width, bottom.max(height).max(screen_h)),
        (right, 0),
    )
}

/// "current 1920 x 1080"
fn current_screen(text: &str) -> (i64, i64) {
    let Some(at) = text.find("current ") else {
        return (0, 0);
    };
    let rest = &text[at + "current ".len()..];
    let number = |s: &str| -> Option<(i64, usize)> {
        let digits: String = s.chars().take_while(char::is_ascii_digit).collect();
        Some((digits.parse().ok()?, digits.len()))
    };
    let parse = || -> Option<(i64, i64)> {
        let (w, used) = number(rest)?;
        let rest = rest[used..].strip_prefix(" x ")?;
        let (h, _) = number(rest)?;
        Some((w, h))
    };
    parse().unwrap_or((0, 0))
}

/// Python's round() of a float: half to even
fn round_half_even(x: f64) -> i64 {
    let r = x.round();
    if (x - x.trunc()).abs() == 0.5 {
        (2.0 * (x / 2.0).round()) as i64
    } else {
        r as i64
    }
}

fn x11_attach(size: (i64, i64)) -> Result<(), ExtendError> {
    x11_detach()?;
    let said = xrandr(&["-q"])?;
    let screen = current_screen(&said);
    let ((total_w, total_h), (x, y)) = x11_plan(screen, &capture::monitors("x11"), size);
    let (width, height) = size;
    let made = (|| {
        if total_w > screen.0 || total_h > screen.1 {
            // (room beyond the monitors)
            let fb = format!("{}x{}", total_w.max(screen.0), total_h.max(screen.1));
            xrandr(&["--fb", &fb])?;
        }
        // (its millimetres: about 96 dots an inch, as a desktop monitor)
        let geometry = format!(
            "{width}/{}x{height}/{}+{x}+{y}",
            round_half_even(width as f64 * 0.2646),
            round_half_even(height as f64 * 0.2646)
        );
        xrandr(&["--setmonitor", X11_NAME, &geometry, "none"])?;
        Ok(())
    })();
    made.map_err(|ExtendError(why)| {
        ExtendError(format!(
            "X11 did not make room for the panel's monitor ({why})"
        ))
    })?;
    say(&format!(
        "monitor: the panel's own monitor at {x},{y}, {width}x{height} (X11)"
    ));
    Ok(())
}

fn x11_detach() -> Result<(), ExtendError> {
    let said = xrandr(&["--listmonitors"])?;
    if !said.contains(X11_NAME) {
        return Ok(());
    }
    xrandr(&["--delmonitor", X11_NAME])?;
    let real: Vec<Monitor> = capture::parse_xrandr(&said)
        .into_iter()
        .filter(|m| !m.virtual_)
        .collect();
    if !real.is_empty() {
        // the screen as large as the monitors again
        let width = real.iter().map(|m| m.x + m.width).max().unwrap_or(0);
        let height = real.iter().map(|m| m.y + m.height).max().unwrap_or(0);
        if let Err(ExtendError(why)) = xrandr(&["--fb", &format!("{width}x{height}")]) {
            say(&format!("monitor: the screen stays as large ({why})"));
        }
    }
    Ok(())
}

#[cfg(windows)]
mod win {
    use windows_sys::Win32::Foundation::{CloseHandle, GetLastError};
    use windows_sys::Win32::Graphics::Gdi::{
        ChangeDisplaySettingsExW, CDS_NORESET, CDS_UPDATEREGISTRY, DEVMODEW, DM_PELSHEIGHT,
        DM_PELSWIDTH, DM_POSITION,
    };
    use windows_sys::Win32::System::Registry::{RegGetValueW, HKEY_LOCAL_MACHINE, RRF_RT_REG_SZ};
    use windows_sys::Win32::System::Threading::{
        GetCurrentProcess, GetExitCodeProcess, IsWow64Process2, WaitForSingleObject,
    };
    use windows_sys::Win32::UI::Shell::{
        ShellExecuteExW, SEE_MASK_NOCLOSEPROCESS, SHELLEXECUTEINFOW,
    };

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    /// The driver's VDDPATH, where it is set.
    pub fn vdd_path() -> Option<String> {
        let key = wide(r"SOFTWARE\MikeTheTech\VirtualDisplayDriver");
        let name = wide("VDDPATH");
        let mut buffer = vec![0u16; 1024];
        let mut size = (buffer.len() * 2) as u32;
        // SAFETY: the buffer and its size in bytes, as the call wants.
        let done = unsafe {
            RegGetValueW(
                HKEY_LOCAL_MACHINE,
                key.as_ptr(),
                name.as_ptr(),
                RRF_RT_REG_SZ,
                std::ptr::null_mut(),
                buffer.as_mut_ptr().cast(),
                &mut size,
            )
        };
        if done != 0 {
            return None;
        }
        let end = buffer.iter().position(|c| *c == 0).unwrap_or(buffer.len());
        let value = String::from_utf16_lossy(&buffer[..end]);
        (!value.is_empty()).then_some(value)
    }

    pub fn native_machine() -> Option<&'static str> {
        let (mut process, mut native) = (0u16, 0u16);
        // SAFETY: two numbers written by the call (Windows 10 1709 and later).
        let done = unsafe { IsWow64Process2(GetCurrentProcess(), &mut process, &mut native) };
        (done != 0).then_some(if native == 0xAA64 { "ARM64" } else { "AMD64" })
    }

    /// The display `device` on the desktop at (size, place), or off it (None).
    pub fn place_display(device: &str, at: Option<((i64, i64), (i64, i64))>) -> Result<(), i32> {
        let name = wide(device);
        // SAFETY: a DEVMODEW of its size, the fields named set; the second call applies all
        // that was noted.
        unsafe {
            let mut mode: DEVMODEW = std::mem::zeroed();
            mode.dmSize = size_of::<DEVMODEW>() as u16;
            mode.dmFields = DM_POSITION | DM_PELSWIDTH | DM_PELSHEIGHT;
            if let Some(((width, height), (x, y))) = at {
                mode.dmPelsWidth = width as u32;
                mode.dmPelsHeight = height as u32;
                mode.Anonymous1.Anonymous2.dmPosition.x = x as i32;
                mode.Anonymous1.Anonymous2.dmPosition.y = y as i32;
            }
            let said = ChangeDisplaySettingsExW(
                name.as_ptr(),
                &mode,
                std::ptr::null_mut(),
                CDS_UPDATEREGISTRY | CDS_NORESET,
                std::ptr::null(),
            );
            if said != 0 && at.is_some() {
                return Err(said);
            }
            ChangeDisplaySettingsExW(
                std::ptr::null(),
                std::ptr::null(),
                std::ptr::null_mut(),
                0,
                std::ptr::null(),
            );
        }
        Ok(())
    }

    pub fn run_elevated(program: &str, parameters: &str, timeout: f64) -> Result<i64, String> {
        let (verb, file, params) = (wide("runas"), wide(program), wide(parameters));
        // SAFETY: the info's size and strings are set; the process handle it gives is
        // closed once waited for.
        unsafe {
            let mut info: SHELLEXECUTEINFOW = std::mem::zeroed();
            info.cbSize = size_of::<SHELLEXECUTEINFOW>() as u32;
            info.fMask = SEE_MASK_NOCLOSEPROCESS;
            info.lpVerb = verb.as_ptr();
            info.lpFile = file.as_ptr();
            info.lpParameters = params.as_ptr();
            info.nShow = 0;
            if ShellExecuteExW(&mut info) == 0 {
                // ERROR_CANCELLED: the person said no
                return Err(if GetLastError() == 1223 {
                    "Windows was not given the permission".into()
                } else {
                    "Windows did not start the setup".into()
                });
            }
            let process = info.hProcess;
            let waited = WaitForSingleObject(process, (timeout * 1000.0).round() as u32);
            let result = if waited != 0 {
                Err("the setup did not end in time".into())
            } else {
                let mut code = 0u32;
                GetExitCodeProcess(process, &mut code);
                Ok(i64::from(code))
            };
            CloseHandle(process);
            result
        }
    }
}

#[cfg(not(windows))]
mod win {
    pub fn vdd_path() -> Option<String> {
        None
    }
    pub fn native_machine() -> Option<&'static str> {
        None
    }
    pub fn place_display(_device: &str, _at: Option<((i64, i64), (i64, i64))>) -> Result<(), i32> {
        Ok(())
    }
    pub fn run_elevated(_program: &str, _parameters: &str, _timeout: f64) -> Result<i64, String> {
        Err("only on Windows".into())
    }
}
