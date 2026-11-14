//! Start Libre Panel in the background when the user logs in (autostart.py).
//!
//! - Windows: a value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
//! - Linux and other XDG desktops: `~/.config/autostart/libre-panel.desktop`
//!
//! Everything is per user and needs no administrator rights. Turning it off removes exactly
//! what turning it on created. On any other system there is no autostart. On Windows,
//! `enable(..., elevated=true)` sets up a Task Scheduler task that starts Libre Panel at
//! login with the highest rights, instead of the Run value; setting it up and removing it
//! needs an administrator and is never done without being asked.

use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Instant;

use lp_py::Value;

pub const APP_NAME: &str = "Libre Panel";
const DESKTOP_FILE: &str = "libre-panel.desktop";
pub const WINDOWS_RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
/// the Task Scheduler task for an elevated start
pub const WINDOWS_TASK: &str = "Libre Panel";
/// The release build's program without a console window (Windows).
const WINDOWED_EXE: &str = "LibrePanel.exe";

/// Autostart could not be changed; the message says what to do.
#[derive(Debug)]
pub struct AutostartError(pub String);

impl std::fmt::Display for AutostartError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

type Result<T> = std::result::Result<T, AutostartError>;

fn said(text: &str, values: &[(&str, &str)]) -> String {
    let values: Vec<(&str, Value)> = values
        .iter()
        .map(|(name, value)| (*name, Value::Str((*value).to_string())))
        .collect();
    lp_i18n::t_format(text, &values).unwrap_or_else(|why| why.msg)
}

fn not_supported() -> AutostartError {
    AutostartError(lp_i18n::t("Autostart is not supported on this system."))
}

/// launch_command: the command the system runs at login: the tray app, without opening the
/// app's window.
pub fn launch_command(config: Option<&Path>) -> Vec<String> {
    let mut options = Vec::new();
    if let Some(config) = config {
        let config = std::path::absolute(config).unwrap_or_else(|_| config.to_path_buf());
        options = vec!["--config".to_string(), config.display().to_string()];
    }
    let program = std::env::current_exe().unwrap_or_default();
    let appimage = std::env::var("APPIMAGE").unwrap_or_default();
    let first = if crate::settings::packaged() && !appimage.is_empty() && cfg!(target_os = "linux")
    {
        appimage // (the .AppImage file, not its temporary mount)
    } else {
        let windowed = program.with_file_name(WINDOWED_EXE);
        match cfg!(windows) && windowed.exists() {
            true => windowed.display().to_string(),
            false => program.display().to_string(),
        }
    };
    let mut command = vec![first];
    command.extend(options);
    command.extend(["tray".to_string(), "--background".to_string()]);
    command
}

/// subprocess.list2cmdline: a command line as the Microsoft C runtime reads it back.
#[cfg_attr(not(windows), allow(dead_code))] // (Windows' Run value and task)
pub fn list2cmdline(args: &[String]) -> String {
    let mut line = String::new();
    for arg in args {
        if !line.is_empty() {
            line.push(' ');
        }
        let quote = arg.is_empty() || arg.contains([' ', '\t']);
        if quote {
            line.push('"');
        }
        let mut slashes = 0;
        for ch in arg.chars() {
            match ch {
                '\\' => slashes += 1,
                '"' => {
                    line.push_str(&"\\".repeat(slashes * 2));
                    slashes = 0;
                    line.push_str("\\\"");
                }
                other => {
                    line.push_str(&"\\".repeat(slashes));
                    slashes = 0;
                    line.push(other);
                }
            }
        }
        line.push_str(&"\\".repeat(slashes));
        if quote {
            line.push_str(&"\\".repeat(slashes));
            line.push('"');
        }
    }
    line
}

/// What Task Scheduler says of a task: whether it is on, and the program it starts.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TaskInfo {
    pub enabled: bool,
    pub command: String,
}

/// The text of the first element `name` within `xml` (without a namespace prefix).
#[cfg_attr(not(windows), allow(dead_code))] // (Windows' Run value and task)
fn element<'a>(xml: &'a str, name: &str) -> Option<&'a str> {
    let open = xml.find(&format!("<{name}"))?;
    let rest = &xml[open + name.len() + 1..];
    if !rest.starts_with(['>', ' ', '\t', '\r', '\n', '/']) {
        return element(&xml[open + 1..], name);
    }
    let body = &rest[rest.find('>')? + 1..];
    let close = body.find(&format!("</{name}>"))?;
    Some(&body[..close])
}

/// XML's own five entities and character references, as a parser reads them.
#[cfg_attr(not(windows), allow(dead_code))] // (Windows' Run value and task)
fn unescape(text: &str) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(at) = rest.find('&') {
        out.push_str(&rest[..at]);
        let tail = &rest[at..];
        let end = tail.find(';').unwrap_or(0);
        let named = match &tail[..end + 1] {
            "&amp;" => Some('&'),
            "&lt;" => Some('<'),
            "&gt;" => Some('>'),
            "&quot;" => Some('"'),
            "&apos;" => Some('\''),
            entity => {
                let number = entity.strip_prefix("&#").and_then(|e| e.strip_suffix(';'));
                number
                    .and_then(|n| match n.strip_prefix(['x', 'X']) {
                        Some(hex) => u32::from_str_radix(hex, 16).ok(),
                        None => n.parse().ok(),
                    })
                    .and_then(char::from_u32)
            }
        };
        match named {
            Some(ch) if end > 0 => {
                out.push(ch);
                rest = &tail[end + 1..];
            }
            _ => {
                out.push('&');
                rest = &tail[1..];
            }
        }
    }
    out.push_str(rest);
    out
}

/// parse_task: a task's state from its XML (`schtasks /Query /XML`): `Settings/Enabled`
/// (true when not given) and the program of its first `Exec`.
#[cfg_attr(not(windows), allow(dead_code))] // (Windows' Run value and task)
pub fn parse_task(xml: &str) -> TaskInfo {
    let enabled = element(xml, "Settings").and_then(|settings| element(settings, "Enabled"));
    let enabled = enabled.map_or("true".to_string(), unescape);
    let command = element(xml, "Actions")
        .and_then(|actions| element(actions, "Exec"))
        .and_then(|exec| element(exec, "Command"))
        .map(unescape)
        .unwrap_or_default();
    TaskInfo {
        enabled: lp_py::strip(&enabled).to_lowercase() != "false",
        command: lp_py::strip(&command).to_string(),
    }
}

/// ntpath.normpath: a Windows path without `.`, `..` and doubled separators.
fn normpath(path: &str) -> String {
    let path = path.replace('/', "\\");
    let (drive, rest) = match path.get(1..2) {
        Some(":") => path.split_at(2),
        _ => ("", path.as_str()),
    };
    let rooted = rest.starts_with('\\');
    let mut parts: Vec<&str> = Vec::new();
    for part in rest.split('\\') {
        match part {
            "" | "." => {}
            ".." => match parts.last() {
                Some(&last) if last != ".." => {
                    parts.pop();
                }
                _ if rooted => {}
                _ => parts.push(".."),
            },
            other => parts.push(other),
        }
    }
    let joined = parts.join("\\");
    match (rooted, joined.is_empty() && drive.is_empty()) {
        (true, _) => format!("{drive}\\{joined}"),
        (false, true) => ".".to_string(),
        (false, false) => format!("{drive}{joined}"),
    }
}

/// os.path.expandvars on Windows: `%NAME%` (and `$NAME`) where the variable is set.
fn expandvars(path: &str) -> String {
    let mut out = String::new();
    let mut rest = path;
    while let Some(at) = rest.find('%') {
        out.push_str(&rest[..at]);
        let tail = &rest[at + 1..];
        match tail.find('%') {
            Some(end) if end > 0 => match std::env::var(&tail[..end]) {
                Ok(value) => {
                    out.push_str(&value);
                    rest = &tail[end + 1..];
                }
                Err(_) => {
                    out.push_str(&rest[at..at + end + 2]);
                    rest = &tail[end + 1..];
                }
            },
            _ => {
                out.push('%');
                rest = tail;
            }
        }
    }
    out.push_str(rest);
    out
}

/// same_program: whether two Windows program paths name the same file (case, quotes,
/// slashes).
pub fn same_program(one: &str, other: &str) -> bool {
    let norm = |path: &str| {
        let path = lp_py::strip(path).trim_matches('"');
        normpath(&expandvars(path)).to_lowercase()
    };
    !lp_py::strip(one).is_empty() && norm(one) == norm(other)
}

/// Whether this process runs with administrator rights (Windows).
pub fn is_admin() -> bool {
    cfg!(windows) && lp_devices::rights::elevated()
}

/// XML's escape of `&`, `<` and `>` (xml.sax.saxutils.escape).
#[cfg_attr(not(windows), allow(dead_code))] // (Windows' Run value and task)
fn escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// task_xml: a task that starts `command` when `user` logs in, with the highest rights, at
/// normal priority and without a time limit.
#[cfg_attr(not(windows), allow(dead_code))] // (Windows' Run value and task)
pub fn task_xml(command: &[String], user: &str) -> String {
    let program = escape(&command[0]);
    let arguments = escape(&list2cmdline(&command[1..]));
    let user = escape(user);
    format!(
        r#"<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts {APP_NAME} at login with the rights its sensors need.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>PT5S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{program}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"#
    )
}

/// desktop_exec: a command quoted for the `Exec` key of a .desktop file.
pub fn desktop_exec(args: &[String]) -> std::result::Result<String, String> {
    const RESERVED: &str = " \t\"'\\><~|&;$*?#()`";
    let mut args = args.to_vec();
    if args.first().is_some_and(|first| first.contains('%')) {
        // GLib looks the program up before it expands %%, so pass the path through env
        args.insert(0, "env".into());
    }
    let mut quoted = Vec::new();
    for arg in &args {
        if arg.contains(['\n', '\r']) {
            return Err("line breaks cannot be used in a desktop entry command".into());
        }
        let mut arg = arg.replace('%', "%%");
        if arg.is_empty() || arg.contains(|c| RESERVED.contains(c)) {
            let mut inner = String::new();
            for ch in arg.chars() {
                if "\"`$\\".contains(ch) {
                    inner.push('\\');
                }
                inner.push(ch);
            }
            arg = format!("\"{inner}\"");
        }
        quoted.push(arg);
    }
    // the string-value escaping of desktop files applies on top of the quoting
    Ok(quoted.join(" ").replace('\\', "\\\\"))
}

/// writable_by_user: whether a program lies where the user (and so anything the user runs)
/// can replace it: then starting it with the highest rights hands those rights to whatever
/// replaced it.
pub fn writable_by_user(program: &Path, home: &Path) -> bool {
    match (std::fs::canonicalize(program), std::fs::canonicalize(home)) {
        (Ok(program), Ok(home)) => program.starts_with(home),
        // (strict=False: what is not there is taken as it is written)
        _ => {
            let program = std::path::absolute(program).unwrap_or_else(|_| program.into());
            let home = std::fs::canonicalize(home)
                .or_else(|_| std::path::absolute(home))
                .unwrap_or_else(|_| home.into());
            program.starts_with(home)
        }
    }
}

/// The user's folder (Path.home()).
pub fn user_home() -> PathBuf {
    let named = match cfg!(windows) {
        true => std::env::var_os("USERPROFILE"),
        false => std::env::var_os("HOME"),
    };
    named.map(PathBuf::from).unwrap_or_default()
}

#[cfg(windows)]
mod windows {
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{
        RegDeleteKeyValueW, RegGetValueW, RegSetKeyValueW, HKEY_CURRENT_USER, REG_SZ, RRF_RT_REG_SZ,
    };

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(Some(0)).collect()
    }

    pub fn get(key: &str, name: &str) -> Option<String> {
        let (key, name) = (wide(key), wide(name));
        let mut size = 0u32;
        // SAFETY: the strings end in NUL; first the size, then the value into a buffer of it.
        unsafe {
            let first = RegGetValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                RRF_RT_REG_SZ,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut size,
            );
            if first != ERROR_SUCCESS {
                return None;
            }
            let mut buffer = vec![0u16; (size as usize).div_ceil(2) + 1];
            let read = RegGetValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                RRF_RT_REG_SZ,
                std::ptr::null_mut(),
                buffer.as_mut_ptr().cast(),
                &mut size,
            );
            if read != ERROR_SUCCESS {
                return None;
            }
            let end = buffer.iter().position(|c| *c == 0).unwrap_or(buffer.len());
            Some(String::from_utf16_lossy(&buffer[..end]))
        }
    }

    pub fn set(key: &str, name: &str, value: &str) -> std::io::Result<()> {
        let (key, name, value) = (wide(key), wide(name), wide(value));
        // SAFETY: the strings end in NUL; the size is the value's in bytes, NUL and all.
        let done = unsafe {
            RegSetKeyValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                REG_SZ,
                value.as_ptr().cast(),
                (value.len() * 2) as u32,
            )
        };
        match done {
            ERROR_SUCCESS => Ok(()),
            code => Err(std::io::Error::from_raw_os_error(code as i32)),
        }
    }

    pub fn delete(key: &str, name: &str) {
        let (key, name) = (wide(key), wide(name));
        // SAFETY: the strings end in NUL. (A value that is not there is no error here.)
        unsafe {
            RegDeleteKeyValueW(HKEY_CURRENT_USER, key.as_ptr(), name.as_ptr());
        }
    }
}

/// schtasks.exe with the arguments, without a window: its exit code and its output.
#[cfg(windows)]
fn schtasks(args: &[&str]) -> std::io::Result<std::process::Output> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    std::process::Command::new("schtasks")
        .args(args)
        .creation_flags(CREATE_NO_WINDOW)
        .output()
}

#[cfg(windows)]
fn said_by(output: &std::process::Output) -> String {
    let error = String::from_utf8_lossy(&output.stderr).trim().to_string();
    match error.is_empty() {
        true => String::from_utf8_lossy(&output.stdout).trim().to_string(),
        false => error,
    }
}

pub struct Autostart {
    /// what Libre Panel's own task starts (launch_command's)
    program: String,
    home: PathBuf,
    /// the task's state and when it was asked: schtasks runs at most every few seconds
    elevated: Mutex<Option<(Instant, &'static str)>>,
}

impl Autostart {
    pub fn new(program: &str) -> Autostart {
        Autostart {
            program: program.to_string(),
            home: user_home(),
            elevated: Mutex::new(None),
        }
    }

    /// "windows", "xdg" (Linux, the BSDs) or "unsupported".
    pub fn kind(&self) -> &'static str {
        if cfg!(windows) {
            "windows"
        } else if cfg!(any(
            target_os = "linux",
            target_os = "freebsd",
            target_os = "openbsd",
            target_os = "netbsd",
            target_os = "dragonfly"
        )) {
            "xdg"
        } else {
            "unsupported"
        }
    }

    #[cfg(windows)]
    fn query(&self) -> Option<TaskInfo> {
        let output = schtasks(&["/Query", "/TN", WINDOWS_TASK, "/XML"]).ok()?;
        let text = String::from_utf8_lossy(&output.stdout);
        output.status.success().then(|| parse_task(&text))
    }

    #[cfg(not(windows))]
    fn query(&self) -> Option<TaskInfo> {
        None
    }

    /// task_state: the task named like Libre Panel's elevated start (Windows): "none";
    /// "ours", on and starting this program; "off", ours but switched off; or "other", one
    /// that starts another program. Asked often: schtasks runs at most every few seconds,
    /// unless `fresh`.
    pub fn task_state(&self, fresh: bool) -> &'static str {
        if self.kind() != "windows" {
            return "none";
        }
        let mut kept = self.elevated.lock().unwrap_or_else(|e| e.into_inner());
        let old = kept.is_none_or(|(at, _)| at.elapsed().as_secs_f64() > 5.0);
        if fresh || old {
            let state = match self.query() {
                None => "none",
                Some(info) if !same_program(&info.command, &self.program) => "other",
                Some(info) if info.enabled => "ours",
                Some(_) => "off",
            };
            *kept = Some((Instant::now(), state));
        }
        kept.map_or("none", |(_, state)| state)
    }

    fn forget_task(&self) {
        *self.elevated.lock().unwrap_or_else(|e| e.into_inner()) = None;
    }

    /// Whether Libre Panel starts through its elevated task (Windows): one that is on and
    /// starts this program.
    pub fn elevated(&self, fresh: bool) -> bool {
        self.task_state(fresh) == "ours"
    }

    /// What to do about a task named like Libre Panel's that is off or not its own.
    pub fn task_hint(&self) -> String {
        if !["off", "other"].contains(&self.task_state(false)) {
            return String::new();
        }
        said(
            "A task \"{name}\" in Task Scheduler is switched off or starts another program; \
             Libre Panel uses its normal start at login instead. To remove the task, run as \
             administrator: schtasks /Delete /TN \"{name}\" /F",
            &[("name", WINDOWS_TASK)],
        )
    }

    /// Whether this process may set up the start with the highest rights: it runs as
    /// administrator on Windows.
    pub fn can_elevate(&self) -> bool {
        self.kind() == "windows" && is_admin()
    }

    /// The desktop entry's file (XDG); None where there is no such file.
    pub fn path(&self) -> Option<PathBuf> {
        if self.kind() != "xdg" {
            return None;
        }
        let base = std::env::var_os("XDG_CONFIG_HOME")
            .filter(|base| !base.is_empty())
            .map_or_else(|| self.home.join(".config"), PathBuf::from);
        Some(base.join("autostart").join(DESKTOP_FILE))
    }

    /// Where the entry lives, or (on an unsupported system) that there is none.
    pub fn location(&self) -> String {
        match self.kind() {
            "windows" if self.elevated(false) => said(
                "Task Scheduler: {name}, with the highest rights",
                &[("name", WINDOWS_TASK)],
            ),
            "windows" => format!("HKEY_CURRENT_USER\\{WINDOWS_RUN_KEY}\\{APP_NAME}"),
            "unsupported" => not_supported().0,
            _ => self.path().unwrap_or_default().display().to_string(),
        }
    }

    /// The command Windows' Run key starts at login for Libre Panel (Windows), where it has
    /// one.
    #[cfg(windows)]
    pub fn run_value(&self) -> Option<String> {
        windows::get(WINDOWS_RUN_KEY, APP_NAME)
    }

    #[cfg(not(windows))]
    pub fn run_value(&self) -> Option<String> {
        None
    }

    /// The Run key's command for Libre Panel set to `value` (Windows).
    #[cfg(windows)]
    pub fn set_run_value(&self, value: &str) -> std::io::Result<()> {
        windows::set(WINDOWS_RUN_KEY, APP_NAME, value)
    }

    #[cfg(not(windows))]
    pub fn set_run_value(&self, _value: &str) -> std::io::Result<()> {
        Ok(())
    }

    pub fn is_enabled(&self) -> Result<bool> {
        match self.kind() {
            "windows" => Ok(self.run_value().is_some() || self.elevated(false)),
            "unsupported" => Err(not_supported()),
            _ => {
                // (the file may go between the look and the read)
                let Some(text) = self.path().and_then(|path| std::fs::read(path).ok()) else {
                    return Ok(false);
                };
                let text = String::from_utf8_lossy(&text);
                let hidden = text.split('\n').any(|line| {
                    let line = line.strip_suffix('\r').unwrap_or(line);
                    let Some((key, value)) = line.split_once('=') else {
                        return false;
                    };
                    let blank = |c: char| c == ' ' || c == '\t' || c == '\x0b' || c == '\x0c';
                    key.trim_end_matches(blank).eq_ignore_ascii_case("hidden")
                        && value.trim_matches(blank).eq_ignore_ascii_case("true")
                });
                Ok(!hidden)
            }
        }
    }

    /// Start `command` at login; `elevated` (Windows): with the highest rights.
    pub fn enable(&self, command: &[String], elevated: bool, icon: &Path) -> Result<String> {
        if self.kind() == "unsupported" {
            return Err(not_supported());
        }
        if elevated {
            return self.enable_elevated(command);
        }
        if self.kind() == "windows" {
            if self.elevated(true) {
                return Err(AutostartError(lp_i18n::t(
                    "Libre Panel already starts with the highest rights. To start it without, \
                     first run as administrator: libre-panel autostart disable",
                )));
            }
            #[cfg(windows)]
            windows::set(WINDOWS_RUN_KEY, APP_NAME, &list2cmdline(command))
                .map_err(|why| AutostartError(why.to_string()))?;
            return Ok(self.location());
        }
        let path = self.path().unwrap_or_default();
        let exec = desktop_exec(command).map_err(AutostartError)?;
        let icon = match icon.exists() {
            true => format!("Icon={}", icon.display()),
            false => String::new(),
        };
        let lines = [
            "[Desktop Entry]".to_string(),
            "Type=Application".into(),
            format!("Name={APP_NAME}"),
            "GenericName=System monitor panel".into(),
            "GenericName[de]=Systemmonitor-Panel".into(),
            "Comment=Shows system sensors on a USB monitor panel".into(),
            "Comment[de]=Zeigt Systemsensoren auf einem USB-Monitor-Panel".into(),
            format!("Exec={exec}"),
            icon,
            "Terminal=false".into(),
            "Categories=System;Monitor;".into(),
            "X-GNOME-Autostart-enabled=true".into(),
            // give the desktop a moment to bring up its tray first
            "X-GNOME-Autostart-Delay=5".into(),
        ];
        let text: Vec<String> = lines.into_iter().filter(|line| !line.is_empty()).collect();
        let written = path
            .parent()
            .map_or(Ok(()), std::fs::create_dir_all)
            .and_then(|()| std::fs::write(&path, text.join("\n") + "\n"));
        written.map_err(|why| AutostartError(why.to_string()))?;
        Ok(self.location())
    }

    fn enable_elevated(&self, command: &[String]) -> Result<String> {
        if self.kind() != "windows" {
            return Err(AutostartError(lp_i18n::t(
                "Starting with the highest rights is for Windows only.",
            )));
        }
        if !is_admin() {
            return Err(AutostartError(lp_i18n::t(
                "Setting up a start with the highest rights needs an administrator: open a \
                 terminal with 'Run as administrator' and run the command there.",
            )));
        }
        #[cfg(windows)]
        {
            let user = std::env::var("USERNAME").unwrap_or_default();
            let domain = std::env::var("USERDOMAIN").unwrap_or_default();
            let user = match domain.is_empty() {
                true => user,
                false => format!("{domain}\\{user}"),
            };
            let xml = task_xml(command, &user);
            // (UTF-16 with its mark, as the XML declares)
            let mut bytes = vec![0xFF, 0xFE];
            bytes.extend(xml.encode_utf16().flat_map(u16::to_le_bytes));
            let folder =
                std::env::temp_dir().join(format!("libre-panel-task-{}", std::process::id()));
            let file = folder.join("task.xml");
            let made = std::fs::create_dir_all(&folder).and_then(|()| std::fs::write(&file, bytes));
            made.map_err(|why| AutostartError(why.to_string()))?;
            let place = file.display().to_string();
            let output = schtasks(&["/Create", "/TN", WINDOWS_TASK, "/XML", &place, "/F"]);
            let _ = std::fs::remove_dir_all(&folder);
            let output = output.map_err(|why| AutostartError(why.to_string()))?;
            if !output.status.success() {
                return Err(AutostartError(said_by(&output)));
            }
            self.forget_task();
            windows::delete(WINDOWS_RUN_KEY, APP_NAME); // one start at login, not two
        }
        #[cfg(not(windows))]
        let _ = command;
        Ok(self.location())
    }

    /// Remove the entry; whether there was one.
    pub fn disable(&self) -> Result<bool> {
        match self.kind() {
            "unsupported" => Err(not_supported()),
            "windows" => {
                let mut existed = self.run_value().is_some();
                #[cfg(windows)]
                windows::delete(WINDOWS_RUN_KEY, APP_NAME);
                if self.elevated(true) {
                    #[cfg(windows)]
                    {
                        let output = schtasks(&["/Delete", "/TN", WINDOWS_TASK, "/F"]);
                        let failed = match &output {
                            Ok(output) if output.status.success() => None,
                            Ok(output) => Some(said_by(output)),
                            Err(why) => Some(why.to_string()),
                        };
                        if let Some(reason) = failed {
                            return Err(AutostartError(said(
                                "The start with the highest rights can only be removed by an \
                                 administrator ({reason}): open a terminal with 'Run as \
                                 administrator' and run: libre-panel autostart disable",
                                &[("reason", &reason)],
                            )));
                        }
                    }
                    self.forget_task();
                    existed = true;
                }
                Ok(existed)
            }
            _ => {
                let path = self.path().unwrap_or_default();
                match path.exists() {
                    true => std::fs::remove_file(&path)
                        .map(|()| true)
                        .map_err(|why| AutostartError(why.to_string())),
                    false => Ok(false),
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_lines_and_tasks_read_as_pythons() {
        let line =
            |args: &[&str]| list2cmdline(&args.iter().map(|a| a.to_string()).collect::<Vec<_>>());
        assert_eq!(line(&["C:\\a b\\x.exe", "tray"]), "\"C:\\a b\\x.exe\" tray");
        assert_eq!(
            line(&["a\\\\", "b c\\", "\"q\"", ""]),
            "a\\\\ \"b c\\\\\" \\\"q\\\" \"\""
        );
        let xml = task_xml(&["C:\\P\\LibrePanel.exe".into(), "tray".into()], "D\\u&v");
        let info = parse_task(&xml);
        assert_eq!(
            info,
            TaskInfo {
                enabled: true,
                command: "C:\\P\\LibrePanel.exe".into()
            }
        );
        let off = xml.replace("<Priority>5</Priority>", "<Enabled>false</Enabled>");
        assert!(!parse_task(&off).enabled);
        assert_eq!(parse_task("nonsense").command, "");
        assert!(same_program(
            "\"c:/p/x/../LibrePanel.exe\"",
            "C:\\P\\LibrePanel.exe"
        ));
        assert!(!same_program("  ", ""));
        assert_eq!(normpath("C:\\a\\.\\b\\..\\c"), "C:\\a\\c");
        assert_eq!(
            desktop_exec(&["/a b/x%".into(), "tray".into()]).unwrap(),
            "env \"/a b/x%%\" tray".replace('\\', "\\\\")
        );
    }
}
