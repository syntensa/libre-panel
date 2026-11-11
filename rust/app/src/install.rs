//! The update itself on a click (editor/updates.py's Install): "Update to 0.9.0" fetches the
//! download for this kind of install from the release's own files
//! (github.com/syntensa/libre-panel/releases/download/, over HTTPS only) and checks it
//! against the release's SHA256SUMS (a release from before has only its size checked).
//! Nothing is installed until the second click, "Install and restart": the Windows setup then
//! runs without questions, the portable zip is unpacked into a new folder beside this one with
//! the settings, the AppImage takes the running one's place; each starts once this one has
//! quit. The Linux tarball is only fetched and its folder shown, to unpack by hand as the
//! first time.

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use lp_i18n::t;
use lp_py::{Dict, Value};
use sha2::{Digest, Sha256};

use crate::updates::{newer, RELEASE_FILES};

/// how each kind of install is updated ("open": its folder is shown)
const HOW: [(&str, &str); 4] = [
    ("setup", "setup"),
    ("portable", "portable"),
    ("appimage", "appimage"),
    ("linux", "open"),
];
/// Inno Setup without questions, as packaging/windows/test_installer.py runs it; it keeps the
/// folder, the tasks and the way (for all users or for me only) of the version before
const SETUP_FLAGS: [&str; 3] = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"];
const CHUNK: usize = 256 * 1024;
/// a checksum file is a few lines
const SUMS_LIMIT: usize = 1 << 20;
const BUSY: [&str; 3] = ["downloading", "verifying", "installing"];
/// the shell waits for this Libre Panel to have quit (a minute at most), then starts the new one
pub const WAIT_THEN_START: &str = "i=0; while kill -0 \"$1\" 2>/dev/null && [ \"$i\" -lt 120 ]; \
                                   do sleep 0.5; i=$((i+1)); done; exec \"$2\"";
/// The program without a console window (Windows), which the start menu and the setup start.
const WINDOWED_EXE: &str = "LibrePanel.exe";
/// the tests' own: a folder whose files stand for the release's (named as on the release)
pub const FILES_ENV: &str = "LP_RELEASE_FILES_DIR";
/// the tests' own: how Libre Panel came, instead of what it finds
pub const KIND_ENV: &str = "LP_INSTALL_KIND";
/// the tests' own: a file the command that would start the new version is written to (JSON),
/// instead of it being started
pub const SPAWN_ENV: &str = "LP_INSTALL_SPAWN";

/// What went wrong: a refusal of Libre Panel's own (UpdateError), said as it is, or a failure
/// on the way (the network, the disk), said with what it was.
enum Failed {
    Told(String),
    Other(String),
}

impl<E: std::fmt::Display> From<E> for Failed {
    fn from(why: E) -> Failed {
        Failed::Other(why.to_string())
    }
}

fn say(message: &str) {
    crate::logs::say("libre_panel.editor.updates", message);
}

fn said(text: &str, values: &[(&str, Value)]) -> String {
    lp_i18n::t_format(text, values).unwrap_or_else(|why| why.msg)
}

/// IDLE: the state before anything is fetched.
pub fn idle() -> Dict {
    let mut state = Dict::new();
    for (key, value) in [
        ("state", Value::Str("idle".into())),
        ("version", Value::Str(String::new())),
        ("how", Value::Str(String::new())),
        ("file", Value::Str(String::new())),
        ("where", Value::Str(String::new())),
        ("done", Value::int(0)),
        ("total", Value::int(0)),
        ("checked", Value::Bool(false)),
        ("error", Value::Str(String::new())),
    ] {
        state.insert(key.into(), value);
    }
    state
}

fn text(state: &Dict, key: &str) -> String {
    match state.get(key) {
        Some(Value::Str(text)) => text.clone(),
        _ => String::new(),
    }
}

/// install_kind (or what the tests say it is).
fn kind() -> String {
    crate::updates::install_kind().to_string()
}

/// A name of a release's file, which an update keeps as the name of its download.
fn file_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    (1..=128).contains(&bytes.len())
        && bytes[0].is_ascii_alphanumeric()
        && bytes
            .iter()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(b))
}

/// open_https: what `url` answers (its length where it says one), or the file of the tests'
/// folder that stands for it.
fn open_url(url: &str) -> Result<(Option<u64>, Box<dyn Read + Send>), String> {
    if let Some(folder) = std::env::var_os(FILES_ENV) {
        let name = url.rsplit('/').next().unwrap_or_default();
        let file = std::fs::File::open(Path::new(&folder).join(name))
            .map_err(|why| format!("HTTP Error 404: Not Found ({why})"))?;
        let length = file.metadata().ok().map(|meta| meta.len());
        return Ok((length, Box::new(file)));
    }
    let agent = format!("libre-panel/{}", lp_py::VERSION);
    lp_net::open_https(url, &[("User-Agent", &agent)], 30.0).map_err(|why| why.to_string())
}

/// _words: what went wrong, as the person is told.
fn words(why: &str) -> String {
    said(
        "The download did not work: {error}",
        &[("error", Value::Str(why.to_string()))],
    )
}

/// _quoted: `text` as a string of PowerShell, which takes the typographic quotes for ' too.
fn quoted(text: &str) -> String {
    let mut out = String::from("'");
    for c in text.chars() {
        if matches!(c, '\'' | '\u{2018}' | '\u{2019}' | '\u{201a}' | '\u{201b}') {
            out.push(c);
        }
        out.push(c);
    }
    out.push('\'');
    out
}

/// _powershell: Windows PowerShell running `script`, given encoded: no path is quoted on a
/// command line.
fn powershell(script: &str) -> Vec<String> {
    let root = std::env::var("SystemRoot")
        .ok()
        .filter(|root| !root.is_empty())
        .unwrap_or_else(|| r"C:\Windows".to_string());
    let root = root.trim_end_matches(['\\', '/']);
    let program = format!(r"{root}\System32\WindowsPowerShell\v1.0\powershell.exe");
    let utf16: Vec<u8> = script.encode_utf16().flat_map(u16::to_le_bytes).collect();
    let encoded = crate::drawings::base64(&utf16);
    [
        program.as_str(),
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-EncodedCommand",
        &encoded,
    ]
    .map(str::to_string)
    .to_vec()
}

/// The folder of a Windows path (ntpath.dirname, as PurePath.parent gives it).
fn windows_parent(path: &str) -> String {
    match path.rfind(['\\', '/']) {
        Some(0) => path[..1].to_string(),
        Some(at) if at == 2 && path.as_bytes().get(1) == Some(&b':') => path[..3].to_string(),
        Some(at) => path[..at].to_string(),
        None => String::new(),
    }
}

/// restart_command: what starts `program` once the process `pid` (this Libre Panel) has
/// quit, run on its own: on Windows PowerShell, which first runs the `setup` without
/// questions where there is one and it is still the file that was checked (`sha256`: Windows
/// may give it administrator rights), then starts the program in its folder (the new one, or
/// the one there was where the setup was cancelled) and removes the setup's folder; on Linux
/// the shell.
pub fn restart_command(
    how: &str,
    pid: u32,
    program: &str,
    setup: Option<&str>,
    sha256: &str,
) -> Vec<String> {
    if how != "setup" && how != "portable" {
        return [
            "/bin/sh",
            "-c",
            WAIT_THEN_START,
            "libre-panel-update",
            &pid.to_string(),
            program,
        ]
        .map(str::to_string)
        .to_vec();
    }
    let mut lines = vec![
        "$ErrorActionPreference = 'SilentlyContinue'".to_string(),
        format!("Wait-Process -Id {pid} -Timeout 60"),
    ];
    if let Some(setup) = setup {
        let flags: Vec<String> = SETUP_FLAGS.iter().map(|flag| quoted(flag)).collect();
        let (file, digest) = (quoted(setup), quoted(sha256));
        let run = format!(
            "Start-Process -FilePath {file} -ArgumentList {} -Wait",
            flags.join(",")
        );
        lines.push(format!(
            "if ((Get-FileHash -Algorithm SHA256 -LiteralPath {file}).Hash -eq {digest}) {{ {run} }}"
        ));
    }
    let folder = quoted(&windows_parent(program));
    lines.push(format!(
        "Start-Process -FilePath {} -WorkingDirectory {folder}",
        quoted(program)
    ));
    if let Some(setup) = setup {
        lines.push(format!(
            "Remove-Item -LiteralPath {} -Recurse -Force",
            quoted(&windows_parent(setup))
        ));
    }
    powershell(&lines.join("\n"))
}

/// _spawn: `command` started on its own in `cwd`, without what this program's bundle put in
/// the environment (the AppImage's): what it starts is another program.
fn spawn(command: &[String], cwd: &Path) -> Result<(), String> {
    if let Some(file) = std::env::var_os(SPAWN_ENV) {
        let told = Value::List(vec![
            Value::List(command.iter().cloned().map(Value::Str).collect()),
            Value::Str(cwd.to_string_lossy().into_owned()),
        ]);
        let text = lp_py::json::dumps(&told, &lp_py::json::Dumps::default());
        return std::fs::write(file, text).map_err(|why| why.to_string());
    }
    let mut process = std::process::Command::new(&command[0]);
    process
        .args(&command[1..])
        .current_dir(cwd)
        .env_clear()
        .envs(crate::linux_setup::clean_environment())
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        process.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        process.process_group(0);
    }
    process.spawn().map(|_| ()).map_err(|why| why.to_string())
}

/// Whether the zip's `name` would land outside the folder it is unpacked into.
fn outside(name: &str) -> bool {
    let name = name.replace('\\', "/");
    name.starts_with('/')
        || name.as_bytes().get(1) == Some(&b':')
        || name.split('/').any(|part| part == "..")
}

/// unpack_portable: the portable zip unpacked into a new folder beside the folder of
/// `program` (the running copy's), `libre-panel-0.9.0` (`-2` where that is taken), with the
/// settings of its `data` folder: Windows keeps the files of a running program, so they are
/// not replaced. The running copy stays as it is, to delete once the new one runs. The new
/// folder.
fn unpack_portable(archive: &Path, program: &Path, version: &str) -> Result<PathBuf, Failed> {
    let not_portable = || {
        Failed::Told(t(
            "The zip is not a portable Libre Panel: it is not installed.",
        ))
    };
    let running = std::fs::canonicalize(program)
        .unwrap_or_else(|_| program.to_path_buf())
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(not_portable)?;
    let above = running.parent().unwrap_or(&running).to_path_buf();
    let mut target = above.join(format!("libre-panel-{version}"));
    let mut number = 2;
    while target.exists() {
        target = above.join(format!("libre-panel-{version}-{number}"));
        number += 1;
    }
    let mut name = target.file_name().unwrap_or_default().to_os_string();
    name.push(".part");
    let part = target.with_file_name(name);
    let _ = std::fs::remove_dir_all(&part);
    let unpacked = (|| -> Result<(), Failed> {
        let data = std::fs::read(archive)?;
        let zipped = crate::zip::Archive::new(&data).map_err(zip_failed)?;
        if zipped.infolist.iter().any(|info| outside(&info.filename)) {
            return Err(not_portable());
        }
        for info in &zipped.infolist {
            let path = part.join(info.filename.replace('\\', "/"));
            if info.is_dir() {
                std::fs::create_dir_all(&path)?;
                continue;
            }
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            let content = zipped.read(info).map_err(zip_failed)?;
            std::fs::write(&path, content)?;
        }
        if !part.join(WINDOWED_EXE).is_file()
            || !part.join(lp_config::dirs::PORTABLE_MARK).is_file()
        {
            return Err(not_portable());
        }
        match lp_config::dirs::portable_dir(Some(program)) {
            // (this copy keeps its settings in the user's folder: the new one too)
            None => std::fs::remove_file(part.join(lp_config::dirs::PORTABLE_MARK))?,
            // (the running app's lock and address are its own)
            Some(data) if data.is_dir() => {
                let name = data.file_name().unwrap_or_default();
                copy_settings(&data, &part.join(name))?;
            }
            Some(_) => {}
        }
        std::fs::rename(&part, &target)?;
        Ok(())
    })();
    if let Err(why) = unpacked {
        let _ = std::fs::remove_dir_all(&part);
        return Err(why);
    }
    Ok(target)
}

/// A zip that cannot be read, said as str() of Python's exception says it.
fn zip_failed(why: crate::zip::ZipError) -> Failed {
    let told = why.told();
    Failed::Other(
        told.split_once(": ")
            .map_or(told.clone(), |(_, m)| m.to_string()),
    )
}

/// The settings folder copied, without the running app's lock and address.
fn copy_settings(source: &Path, target: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(target)?;
    for entry in std::fs::read_dir(source)? {
        let entry = entry?;
        let name = entry.file_name();
        if name == "libre-panel.lock" || name == "instance.json" {
            continue;
        }
        if entry.file_type()?.is_dir() {
            copy_settings(&entry.path(), &target.join(&name))?;
        } else {
            std::fs::copy(entry.path(), target.join(&name))?;
        }
    }
    Ok(())
}

/// move_autostart: a portable copy that starts at login: Windows' Run value names the new
/// folder's program from now on. (A start with the highest rights needs an administrator to
/// change: the log says so.)
pub fn move_autostart(old: &Path, new: &Path, autostart: &crate::autostart::Autostart) {
    if autostart.kind() != "windows" {
        return;
    }
    let value = autostart.run_value().unwrap_or_default();
    let value = lp_py::strip(&value);
    let (named, rest) = match value.strip_prefix('"') {
        Some(inner) => match inner.split_once('"') {
            Some((named, rest)) => (named.to_string(), rest.to_string()),
            None => (inner.to_string(), String::new()),
        },
        None => match value.split_once(' ') {
            Some((named, rest)) => (named.to_string(), format!(" {rest}")),
            None => (value.to_string(), String::new()),
        },
    };
    let old_program = old.join(WINDOWED_EXE).to_string_lossy().into_owned();
    if !value.is_empty() && crate::autostart::same_program(&named, &old_program) {
        let program = new.join(WINDOWED_EXE).to_string_lossy().into_owned();
        let command = crate::autostart::list2cmdline(&[program]) + &rest;
        if let Err(why) = autostart.set_run_value(&command) {
            say(&format!("autostart still names {}: {why}", old.display()));
        }
    } else if autostart.elevated(false) {
        say(&format!(
            "the start with the highest rights still names {}: set it up again",
            old.display()
        ));
    }
}

struct Inner {
    state: Dict,
    /// the download, once it is checked
    file: Option<PathBuf>,
    /// its SHA-256
    sha256: String,
}

/// Install: the button "Update to 0.9.0": the download of a newer version for this kind of
/// install, checked, and on a second click put in place. `state()`: idle, downloading (`done`
/// of `total` bytes), verifying, ready (`checked`: against the release's SHA256SUMS),
/// installing or error (`error`, in words).
#[derive(Clone)]
pub struct Install {
    inner: Arc<Mutex<Inner>>,
}

impl Default for Install {
    fn default() -> Self {
        Install::new()
    }
}

impl Install {
    pub fn new() -> Install {
        Install {
            inner: Arc::new(Mutex::new(Inner {
                state: idle(),
                file: None,
                sha256: String::new(),
            })),
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn state(&self) -> Dict {
        self.lock().state.clone()
    }

    fn set(&self, changes: &[(&str, Value)]) {
        let mut inner = self.lock();
        for (key, value) in changes {
            inner.state.insert((*key).into(), value.clone());
        }
    }

    /// _folder: where the download goes: beside the running AppImage (it is renamed into its
    /// place), the tarball into Downloads (to unpack from), the rest into a folder of its own.
    fn folder(how: &str) -> Result<PathBuf, String> {
        if how == "appimage" {
            let image = std::env::var("APPIMAGE").unwrap_or_default();
            let folder = (!image.is_empty())
                .then(|| Path::new(&image).parent().map(Path::to_path_buf))
                .flatten();
            let writable = folder.as_ref().is_some_and(|folder| writable(folder));
            return match (folder, writable) {
                (Some(folder), true) => Ok(folder),
                (folder, _) => Err(said(
                    "The AppImage's folder {folder} cannot be written to: download the new one \
                     from its page and put it there yourself.",
                    &[(
                        "folder",
                        Value::Str(folder.map_or("?".into(), |f| f.to_string_lossy().into_owned())),
                    )],
                )),
            };
        }
        let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
            .map(PathBuf::from)
            .unwrap_or_default();
        let downloads = home.join("Downloads");
        if how == "open" && downloads.is_dir() && writable(&downloads) {
            return Ok(downloads);
        }
        let base = std::env::temp_dir();
        for _ in 0..100 {
            let mut bytes = [0u8; 6];
            let _ = getrandom::getrandom(&mut bytes);
            let tail: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
            let folder = base.join(format!("libre-panel-update-{tail}"));
            match std::fs::create_dir(&folder) {
                Ok(()) => return Ok(folder),
                Err(why) if why.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(why) => return Err(words(&why.to_string())),
            }
        }
        Err(words("no temporary folder"))
    }

    /// download: the file of `latest` (Updates.state's, None where nothing is newer) for this
    /// kind of install fetched in the background; `state()` says how far it is.
    pub fn download(&self, latest: Option<&Dict>) -> Result<Dict, String> {
        let kind = kind();
        let how = HOW
            .iter()
            .find(|(name, _)| *name == kind)
            .map_or("", |(_, how)| *how);
        let version = latest
            .map(|latest| text(latest, "version"))
            .unwrap_or_default();
        let Some(latest) = latest.filter(|_| newer(&version, lp_py::VERSION)) else {
            return Err(t("There is no newer version to update to."));
        };
        let asset =
            match latest.get("asset") {
                Some(Value::Dict(asset)) if !how.is_empty() => asset.clone(),
                _ => return Err(said(
                    "There is no download of Libre Panel {version} for this kind of install: get \
                     it from its page.",
                    &[("version", Value::Str(version))],
                )),
            };
        let own = [
            format!("{RELEASE_FILES}v{version}/"),
            format!("{RELEASE_FILES}{version}/"),
        ];
        let (url, name) = (text(&asset, "url"), text(&asset, "name"));
        if !(own.iter().any(|own| url.starts_with(own.as_str())) && file_name(&name)) {
            return Err(t(
                "That download is not one of Libre Panel's releases on GitHub: it is not fetched.",
            ));
        }
        let sums = text(latest, "sums");
        let sums = match own.iter().any(|own| sums.starts_with(own.as_str())) {
            true => sums,
            false => String::new(),
        };
        let total = match asset.get("size") {
            Some(Value::Int(size)) => u64::try_from(size).unwrap_or(0),
            _ => 0,
        };
        let started = {
            // (one download at a time: a second click is told the first one's)
            let mut inner = self.lock();
            if BUSY.contains(&text(&inner.state, "state").as_str()) {
                return Ok(inner.state.clone());
            }
            let folder = Install::folder(how)?;
            Install::forget(&mut inner);
            let mut state = idle();
            state.insert("state".into(), Value::Str("downloading".into()));
            state.insert("version".into(), Value::Str(version));
            state.insert("how".into(), Value::Str(how.into()));
            state.insert("file".into(), Value::Str(name.clone()));
            state.insert(
                "where".into(),
                Value::Str(folder.to_string_lossy().into_owned()),
            );
            state.insert("total".into(), Value::int(total as i64));
            inner.state = state.clone();
            (state, folder)
        };
        let (state, folder) = started;
        let this = self.clone();
        std::thread::Builder::new()
            .name("update".into())
            .spawn(move || this.fetch(&url, &sums, &folder, &name, total))
            .map_err(|why| why.to_string())?;
        Ok(state)
    }

    /// _forget: an earlier download that was not installed goes (not the tarball in
    /// Downloads).
    fn forget(inner: &mut Inner) {
        let Some(earlier) = inner.file.take() else {
            return;
        };
        match text(&inner.state, "how").as_str() {
            "setup" | "portable" => {
                if let Some(folder) = earlier.parent() {
                    let _ = std::fs::remove_dir_all(folder);
                }
            }
            "appimage" => {
                let _ = std::fs::remove_file(&earlier);
            }
            _ => {}
        }
    }

    fn fetch(&self, url: &str, sums: &str, folder: &Path, name: &str, size: u64) {
        let part = folder.join(format!(".{name}.part"));
        let fetched = (|| -> Result<(PathBuf, String), Failed> {
            let (length, mut response) = open_url(url).map_err(Failed::Other)?;
            let total = if size > 0 { size } else { length.unwrap_or(0) };
            self.set(&[("total", Value::int(total as i64))]);
            let mut out = std::fs::File::create(&part)?;
            let mut digest = Sha256::new();
            let mut done = 0u64;
            let mut chunk = vec![0u8; CHUNK];
            loop {
                let n = response.read(&mut chunk)?;
                if n == 0 {
                    break;
                }
                done += n as u64;
                if total > 0 && done > total {
                    return Err(Failed::Told(t(
                        "The download is longer than GitHub says: it is not installed.",
                    )));
                }
                out.write_all(&chunk[..n])?;
                digest.update(&chunk[..n]);
                self.set(&[("done", Value::int(done as i64))]);
            }
            out.flush()?;
            drop(out);
            self.set(&[("state", Value::Str("verifying".into()))]);
            if total > 0 && done != total {
                return Err(Failed::Told(said(
                    "The download broke off ({done} of {total} bytes): try again.",
                    &[
                        ("done", Value::int(done as i64)),
                        ("total", Value::int(total as i64)),
                    ],
                )));
            }
            let hex: String = digest
                .finalize()
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect();
            if !sums.is_empty() && sum_of(sums, name)? != hex {
                return Err(Failed::Told(t(
                    "The download is not the file the release names: its SHA-256 differs. It is \
                     not installed; try again.",
                )));
            }
            let mut last = folder.join(name);
            if text(&self.state(), "how") == "appimage" {
                let image = std::env::var_os("APPIMAGE").map(PathBuf::from);
                if image.as_deref() == Some(last.as_path()) {
                    // (the running one is replaced only on a click)
                    last = folder.join(format!("{name}.new"));
                }
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    std::fs::set_permissions(&part, std::fs::Permissions::from_mode(0o755))?;
                }
            }
            std::fs::rename(&part, &last)?;
            Ok((last, hex))
        })();
        match fetched {
            Ok((file, hex)) => {
                let mut inner = self.lock();
                inner.file = Some(file);
                inner.sha256 = hex;
                inner
                    .state
                    .insert("state".into(), Value::Str("ready".into()));
                inner
                    .state
                    .insert("checked".into(), Value::Bool(!sums.is_empty()));
            }
            Err(failed) => {
                let told = match failed {
                    Failed::Told(why) => why,
                    Failed::Other(why) => words(&why),
                };
                say(&format!("the update was not downloaded: {told}"));
                let _ = std::fs::remove_file(&part);
                self.set(&[
                    ("state", Value::Str("error".into())),
                    ("error", Value::Str(told)),
                ]);
            }
        }
    }

    /// install: the checked download put in place and started, after `quit` (the running
    /// app's): the setup, the portable zip and the AppImage; the tarball's folder is only
    /// shown.
    pub fn install(&self, quit: Option<Box<dyn FnOnce() + Send>>) -> Result<Dict, String> {
        let (state, file, sha256) = {
            // (a second click finds it installing)
            let mut inner = self.lock();
            let state = inner.state.clone();
            let Some(file) = inner
                .file
                .clone()
                .filter(|_| text(&state, "state") == "ready")
            else {
                return Err(t("Nothing is downloaded to install yet."));
            };
            let how = text(&state, "how");
            if how != "open" && quit.is_none() {
                return Err(t(
                    "Only the running app installs an update: start Libre Panel and update from \
                     there.",
                ));
            }
            if how != "open" {
                inner
                    .state
                    .insert("state".into(), Value::Str("installing".into()));
            }
            (state, file, inner.sha256.clone())
        };
        if text(&state, "how") == "open" {
            let folder = file.parent().unwrap_or(&file);
            crate::settings::open_path(folder).map_err(|why| why.to_string())?;
            return Ok(state);
        }
        let this = self.clone();
        let quit = quit.expect("checked above");
        let thread_state = state.clone();
        std::thread::Builder::new()
            .name("update".into())
            .spawn(move || this.put_in_place(&thread_state, &file, &sha256, quit))
            .map_err(|why| why.to_string())?;
        let mut told = state;
        told.insert("state".into(), Value::Str("installing".into()));
        Ok(told)
    }

    fn put_in_place(
        &self,
        state: &Dict,
        file: &Path,
        sha256: &str,
        quit: Box<dyn FnOnce() + Send>,
    ) {
        let program = std::env::current_exe().unwrap_or_default();
        let pid = std::process::id();
        let placed = (|| -> Result<(Vec<String>, PathBuf), Failed> {
            match text(state, "how").as_str() {
                "setup" => {
                    // (PowerShell runs outside the folder it removes)
                    let app = program.with_file_name(WINDOWED_EXE);
                    let command = restart_command(
                        "setup",
                        pid,
                        &app.to_string_lossy(),
                        Some(&file.to_string_lossy()),
                        sha256,
                    );
                    let cwd = file
                        .parent()
                        .and_then(Path::parent)
                        .unwrap_or(file)
                        .to_path_buf();
                    Ok((command, cwd))
                }
                "portable" => {
                    let cwd = unpack_portable(file, &program, &text(state, "version"))?;
                    if let Some(folder) = file.parent() {
                        let _ = std::fs::remove_dir_all(folder);
                    }
                    let running = std::fs::canonicalize(&program)
                        .unwrap_or_else(|_| program.clone())
                        .parent()
                        .map(Path::to_path_buf)
                        .unwrap_or_default();
                    let autostart = crate::autostart::Autostart::new(&program.to_string_lossy());
                    move_autostart(&running, &cwd, &autostart);
                    let command = restart_command(
                        "portable",
                        pid,
                        &cwd.join(WINDOWED_EXE).to_string_lossy(),
                        None,
                        "",
                    );
                    self.set(&[("where", Value::Str(cwd.to_string_lossy().into_owned()))]);
                    Ok((command, cwd))
                }
                _ => {
                    let image = PathBuf::from(std::env::var_os("APPIMAGE").unwrap_or_default());
                    if image.file_name().is_none() {
                        return Err(Failed::Told(t("Nothing is downloaded to install yet.")));
                    }
                    // the running one keeps its own until it quits
                    std::fs::rename(file, &image)?;
                    let command =
                        restart_command("appimage", pid, &image.to_string_lossy(), None, "");
                    let cwd = image.parent().unwrap_or(&image).to_path_buf();
                    Ok((command, cwd))
                }
            }
        })();
        let started = placed.and_then(|(command, cwd)| {
            spawn(&command, &cwd).map_err(Failed::Other)?;
            Ok(command)
        });
        match started {
            Ok(command) => {
                say(&format!(
                    "updating to {}: {}",
                    text(state, "version"),
                    command[0]
                ));
                quit();
            }
            Err(failed) => {
                let told = match failed {
                    Failed::Told(why) => why,
                    Failed::Other(why) => said(
                        "The update could not be put in place: {error}",
                        &[("error", Value::Str(why))],
                    ),
                };
                say(&format!("the update was not put in place: {told}"));
                self.set(&[
                    ("state", Value::Str("error".into())),
                    ("error", Value::Str(told)),
                ]);
            }
        }
    }
}

/// Whether this program may write into `folder` (os.access W_OK).
fn writable(folder: &Path) -> bool {
    let probe = folder.join(format!(".libre-panel-write-{}", std::process::id()));
    match std::fs::File::create(&probe) {
        Ok(_) => {
            let _ = std::fs::remove_file(probe);
            true
        }
        Err(_) => false,
    }
}

/// _sum: the SHA-256 the release's checksums at `url` give `name`.
fn sum_of(url: &str, name: &str) -> Result<String, Failed> {
    let (_, response) = open_url(url).map_err(Failed::Other)?;
    let mut text = Vec::new();
    response
        .take(SUMS_LIMIT as u64 + 1)
        .read_to_end(&mut text)?;
    text.truncate(SUMS_LIMIT);
    for line in String::from_utf8_lossy(&text).lines() {
        let line = lp_py::strip(line);
        // ([0-9a-fA-F]{64}) [ *](.+)
        if line.len() > 66 && line.is_char_boundary(64) {
            let (hex, rest) = line.split_at(64);
            let mut rest = rest.chars();
            let separated = rest.next() == Some(' ') && matches!(rest.next(), Some(' ' | '*'));
            let named = rest.as_str();
            if hex.bytes().all(|b| b.is_ascii_hexdigit()) && separated && named == name {
                return Ok(hex.to_lowercase());
            }
        }
    }
    Err(Failed::Told(said(
        "The release's checksums do not name {name}: it is not installed.",
        &[("name", Value::Str(name.to_string()))],
    )))
}

#[cfg(test)]
mod tests {
    use super::{outside, quoted, restart_command, windows_parent};

    #[test]
    fn powershell_strings_keep_their_quotes() {
        assert_eq!(
            quoted("D:\\Kim\u{2019}s 'tools'"),
            "'D:\\Kim\u{2019}\u{2019}s ''tools'''"
        );
    }

    #[test]
    fn a_windows_path_has_its_folder() {
        assert_eq!(
            windows_parent(r"C:\Program Files\Libre Panel\LibrePanel.exe"),
            r"C:\Program Files\Libre Panel"
        );
        assert_eq!(windows_parent(r"C:\x.exe"), r"C:\");
    }

    #[test]
    fn the_appimage_is_started_by_the_shell() {
        let command = restart_command("appimage", 4321, "/a/b.AppImage", None, "");
        assert_eq!(command[..2], ["/bin/sh".to_string(), "-c".to_string()]);
        assert_eq!(
            command[3..],
            ["libre-panel-update", "4321", "/a/b.AppImage"]
        );
    }

    #[test]
    fn a_zip_s_names_stay_inside() {
        assert!(outside("../x") && outside("/etc/x") && outside("a/../../x") && outside("C:x"));
        assert!(!outside("libre-panel/LibrePanel.exe"));
    }
}
