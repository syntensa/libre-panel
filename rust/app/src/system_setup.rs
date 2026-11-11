//! `libre-panel system-setup` on Windows, and "Unlock all sensors" (sensorservice/setup.py):
//! what Windows has to give the sensors, set up by an administrator.
//!
//! - `--service`: the sensor service's files go to `%ProgramFiles%\Libre Panel\sensors`, a
//!   folder only administrators may change, and the service LibrePanelSensors is created or
//!   brought up to date, then started: LocalSystem, started by Windows a while after it starts,
//!   started again when it fails, and startable (not stoppable) by everyone signed in.
//! - `--pawnio`: the PawnIO driver, from the official installer the download carries, unless
//!   version 2 or newer is there.
//! - `--perf-log-users`: the person who started it (`--user-sid`, else the one signed in to
//!   this session) joins Performance Log Users (S-1-5-32-559), which game frame rates need.
//!
//! `--remove` takes away again what these did. `--status` says what is in place, and needs no
//! rights. The app runs the command with Windows' question for administrator rights.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use lp_py::{Dict, Value};
use sha2::{Digest, Sha256};

pub const SERVICE_NAME: &str = "LibrePanelSensors";
const DISPLAY_NAME: &str = "Libre Panel sensors";
const DESCRIPTION: &str = "Reads temperatures, power, fan speeds, memory and drives for Libre \
                           Panel with the rights this needs. It only reads: it changes no \
                           hardware setting and runs no other program.";
pub const SERVICE_EXE: &str = "libre-panel-sensors.exe";
/// that folder, next to libre-panel.exe
pub const FOLDER: &str = "sensors";
/// the group "Performance Log Users", in any language
const PERF_LOG_USERS: &str = "S-1-5-32-559";
const PAWNIO_ARGUMENTS: [&str; 2] = ["-install", "-silent"];
/// ERROR_SUCCESS_REBOOT_REQUIRED: installed, Windows wants a restart
const RESTART_NEEDED: i64 = 3010;
const EXIT_FAILED: u8 = 1;
const EXIT_NOT_ADMIN: u8 = 3;
/// after a failure: 5 s, 10 s, a minute
const RESTARTS: &str = "restart/5000/restart/10000/restart/60000";
/// how long a start or a stop of the service may take
const STATE_SECONDS: f64 = 30.0;
const SC_EXISTS: i64 = 1073;
const SC_RUNNING: i64 = 1056;
const SC_NOT_STARTED: i64 = 1062;
const SC_MISSING: i64 = 1060;
/// HKLM: what system-setup gave away, to take it back
#[cfg_attr(not(windows), allow(dead_code))]
const SETUP_KEY: &str = r"SOFTWARE\Libre Panel";
#[cfg_attr(not(windows), allow(dead_code))]
const ADDED_VALUE: &str = "PerfLogUsersAdded";
/// The service's own security: as Windows gives it, and everyone signed in may also start it.
const SERVICE_SDDL: &str = "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)\
                            (A;;CCLCSWRPLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)";
/// Who may change the service's folder: the system and administrators; users may read it.
const FOLDER_GRANTS: [&str; 3] = [
    "*S-1-5-18:(OI)(CI)F",
    "*S-1-5-32-544:(OI)(CI)F",
    "*S-1-5-32-545:(OI)(CI)RX",
];
const PAWNIO_SETUP: &str = "PawnIO_setup.exe";
const PAWNIO_SETUP_SHA256: &str =
    "1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032";
/// the service's start type "Disabled"
const DISABLED: u32 = 4;

/// A step that did not work; the text says what to do.
#[derive(Debug)]
pub struct SetupError(pub String);

fn error(text: impl Into<String>) -> SetupError {
    SetupError(text.into())
}

/// What Windows says of the service now.
#[derive(Clone, Debug)]
pub struct ServiceInfo {
    pub state: String,
    pub pid: u32,
    pub program: String,
}

/// What system-setup does to Windows (`win::Real`); the tests give a fake.
pub trait System {
    fn program_files(&self) -> Result<PathBuf, SetupError>;
    fn tool(&self, name: &str) -> String;
    fn service(&self) -> Option<ServiceInfo>;
    fn run(&self, args: &[String], timeout: f64) -> i64;
    fn pawnio_version(&self) -> Option<String>;
    fn group_add(&self, group: &str, member: &str) -> Result<bool, SetupError>;
    fn group_remove(&self, group: &str, member: &str) -> Result<bool, SetupError>;
    fn group_members(&self, group: &str) -> Result<Vec<String>, SetupError>;
    fn session_user(&self) -> Option<String>;
    fn added(&self) -> Vec<String>;
    fn set_added(&self, sids: &[String]) -> Result<(), SetupError>;
    fn alive(&self, pid: u32) -> bool;
    fn sleep(&self, seconds: f64);
}

fn is_sid(text: &str) -> bool {
    // r"S-1-\d+(?:-\d+){1,14}"
    let Some(rest) = text.strip_prefix("S-1-") else {
        return false;
    };
    let parts: Vec<&str> = rest.split('-').collect();
    (2..=15).contains(&parts.len())
        && parts
            .iter()
            .all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()))
}

// -- what came with this program ----------------------------------------------------------------

fn program_folder() -> Option<PathBuf> {
    let program = std::env::current_exe().ok()?;
    let program = std::fs::canonicalize(&program).unwrap_or(program);
    program.parent().map(Path::to_path_buf)
}

/// bundled_service: the folder with the sensor service's files that came with this program.
pub fn bundled_service() -> Option<PathBuf> {
    let folder = program_folder()?.join(FOLDER);
    folder.join(SERVICE_EXE).is_file().then_some(folder)
}

/// pawnio_installer: PawnIO's installer as the download carries it, in `tools`, and only the
/// file that was fetched (its SHA-256): it runs with administrator rights.
pub fn pawnio_installer() -> Option<PathBuf> {
    let path = program_folder()?.join("tools").join(PAWNIO_SETUP);
    let data = std::fs::read(&path).ok()?;
    let digest: String = Sha256::digest(&data)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    (digest == PAWNIO_SETUP_SHA256).then_some(path)
}

/// cli_program: the download's command-line program, which the question for rights starts.
pub fn cli_program() -> Option<PathBuf> {
    let program = program_folder()?.join(if cfg!(windows) {
        "libre-panel.exe"
    } else {
        "libre-panel"
    });
    program.is_file().then_some(program)
}

// -- the commands it runs -----------------------------------------------------------------------

fn service_folder(system: &dyn System) -> Result<PathBuf, SetupError> {
    Ok(system.program_files()?.join("Libre Panel").join(FOLDER))
}

fn strings(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|p| p.to_string()).collect()
}

/// sc_create: `sc.exe create` (or `config`) for the service; the program's path quoted inside
/// the value, so Windows never reads a part of it as a program of its own.
pub fn sc_create(sc: &str, program: &Path, verb: &str) -> Vec<String> {
    let mut args = strings(&[sc, verb, SERVICE_NAME, "binPath="]);
    args.push(format!("\"{}\"", program.display()));
    args.extend(strings(&[
        "start=",
        "delayed-auto",
        "obj=",
        "LocalSystem",
        "DisplayName=",
        DISPLAY_NAME,
    ]));
    args
}

/// sc_settings: the description, a restart after a failure, and who may start it.
pub fn sc_settings(sc: &str) -> Vec<Vec<String>> {
    vec![
        strings(&[sc, "description", SERVICE_NAME, DESCRIPTION]),
        strings(&[
            sc,
            "failure",
            SERVICE_NAME,
            "reset=",
            "86400",
            "actions=",
            RESTARTS,
        ]),
        strings(&[sc, "failureflag", SERVICE_NAME, "1"]),
        strings(&[sc, "sdset", SERVICE_NAME, SERVICE_SDDL]),
    ]
}

pub fn icacls_protect(icacls: &str, folder: &Path) -> Vec<String> {
    let mut args = vec![
        icacls.to_string(),
        folder.display().to_string(),
        "/inheritance:r".into(),
        "/grant:r".into(),
    ];
    args.extend(FOLDER_GRANTS.iter().map(|g| g.to_string()));
    args.push("/Q".into());
    args
}

// -- the steps ----------------------------------------------------------------------------------

#[derive(Default)]
pub struct Outcome {
    pub lines: Vec<String>,
    pub failed: bool,
    /// newly: a running service has to start again to see it
    pub pawnio_installed: bool,
}

impl Outcome {
    fn say(&mut self, text: impl Into<String>) {
        let text = text.into();
        crate::logs::log(
            crate::logs::Level::Info,
            "libre_panel.sensorservice.setup",
            &text,
        );
        self.lines.push(text);
    }

    fn fail(&mut self, text: &str) {
        crate::logs::log(
            crate::logs::Level::Error,
            "libre_panel.sensorservice.setup",
            text,
        );
        self.lines.push(format!("error: {text}"));
        self.failed = true;
    }
}

fn same(one: &Path, other: &Path) -> bool {
    match (std::fs::canonicalize(one), std::fs::canonicalize(other)) {
        (Ok(a), Ok(b)) => a.to_string_lossy().to_lowercase() == b.to_string_lossy().to_lowercase(),
        _ => false,
    }
}

/// Wait until the service is in the state `wanted` (None: gone).
fn wait_for(system: &dyn System, wanted: Option<&str>, seconds: f64) -> bool {
    let mut waited = 0.0;
    loop {
        let state = system.service().map(|info| info.state);
        if state.as_deref() == wanted {
            return true;
        }
        if waited >= seconds {
            return false;
        }
        system.sleep(0.5);
        waited += 0.5;
    }
}

fn copy_tree(source: &Path, target: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(target)?;
    for entry in std::fs::read_dir(source)? {
        let entry = entry?;
        let to = target.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_tree(&entry.path(), &to)?;
        } else {
            std::fs::copy(entry.path(), &to)?;
        }
    }
    Ok(())
}

/// unblock_folder: the mark of the internet (`Zone.Identifier`) off every file that has it.
fn unblock_folder(folder: &Path) {
    let Ok(entries) = std::fs::read_dir(folder) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            unblock_folder(&path);
        } else {
            let mut stream = path.as_os_str().to_owned();
            stream.push(":Zone.Identifier");
            let _ = std::fs::remove_file(PathBuf::from(stream));
        }
    }
}

/// `target` becomes a copy of `source`, whole: copied beside it first, then swapped.
fn replace_folder(source: &Path, target: &Path) -> std::io::Result<()> {
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let name = target.file_name().unwrap_or_default().to_string_lossy();
    let new = target.with_file_name(format!("{name}.new"));
    let old = target.with_file_name(format!("{name}.old"));
    for leftover in [&new, &old] {
        if leftover.exists() {
            std::fs::remove_dir_all(leftover)?;
        }
    }
    copy_tree(source, &new)?;
    if cfg!(windows) {
        unblock_folder(&new); // (a copy keeps the mark of the internet: .NET would refuse it)
    }
    if target.exists() {
        std::fs::rename(target, &old)?;
    }
    std::fs::rename(&new, target)?;
    let _ = std::fs::remove_dir_all(&old);
    Ok(())
}

/// stop_service: stop the service, and wait until its process has let go of its files.
fn stop_service(system: &dyn System, out: &mut Outcome) -> Result<(), SetupError> {
    let Some(info) = system.service() else {
        return Ok(());
    };
    if info.state == "stopped" {
        return Ok(());
    }
    let code = system.run(
        &strings(&[&system.tool("sc.exe"), "stop", SERVICE_NAME]),
        120.0,
    );
    if code != 0 && code != SC_NOT_STARTED && system.service().is_some() {
        return Err(error(format!(
            "the sensor service did not stop (sc.exe: {code})"
        )));
    }
    if !wait_for(system, Some("stopped"), STATE_SECONDS) {
        return Err(error("the sensor service did not stop in time"));
    }
    let mut waited = 0.0;
    while info.pid != 0 && system.alive(info.pid) && waited < STATE_SECONDS {
        system.sleep(0.5);
        waited += 0.5;
    }
    out.say("stopped the sensor service");
    Ok(())
}

/// install_service: the service's files in Program Files, the service created or brought up
/// to date, and started (again, after PawnIO came).
fn install_service(
    system: &dyn System,
    out: &mut Outcome,
    source: Option<&Path>,
) -> Result<(), SetupError> {
    let target = service_folder(system)?;
    let program = target.join(SERVICE_EXE);
    if source.is_none() && !program.is_file() {
        return Err(error(
            "the sensor service is not part of this program: it comes with the Windows \
             downloads (setup or zip)",
        ));
    }
    let info = system.service();
    if let Some(source) = source.filter(|source| !same(source, &target)) {
        stop_service(system, out)?;
        replace_folder(source, &target).map_err(|why| {
            error(format!(
                "could not copy the sensor service to {}: {why}",
                target.display()
            ))
        })?;
        out.say(format!("copied the sensor service to {}", target.display()));
    }
    if system.run(&icacls_protect(&system.tool("icacls.exe"), &target), 120.0) != 0 {
        return Err(error(format!(
            "could not limit who may change {}",
            target.display()
        )));
    }
    let sc = system.tool("sc.exe");
    let verb = if info.is_none() { "create" } else { "config" };
    let mut code = system.run(&sc_create(&sc, &program, verb), 120.0);
    if code == SC_EXISTS {
        code = system.run(&sc_create(&sc, &program, "config"), 120.0); // (it came meanwhile)
    }
    if code != 0 {
        return Err(error(format!(
            "could not register the sensor service (sc.exe: {code})"
        )));
    }
    for args in sc_settings(&sc) {
        if system.run(&args, 120.0) != 0 {
            out.say(format!("warning: sc.exe {} did not work", args[1]));
        }
    }
    out.say(if info.is_none() {
        "registered the sensor service"
    } else {
        "updated the sensor service"
    });
    let info = system.service();
    if out.pawnio_installed && info.is_some_and(|info| info.state == "running") {
        stop_service(system, out)?; // LibreHardwareMonitor looks for PawnIO when it starts
    }
    let code = system.run(&strings(&[&sc, "start", SERVICE_NAME]), 120.0);
    if code != 0 && code != SC_RUNNING {
        return Err(error(format!(
            "the sensor service did not start (sc.exe: {code})"
        )));
    }
    if !wait_for(system, Some("running"), STATE_SECONDS) {
        return Err(error(format!(
            "the sensor service did not start in time; its log: {}",
            target.join("logs").display()
        )));
    }
    out.say("the sensor service runs");
    Ok(())
}

/// remove_service: the service stopped and deleted; its files removed where the setup does
/// not own them.
fn remove_service(system: &dyn System, out: &mut Outcome) -> Result<(), SetupError> {
    if system.service().is_some() {
        stop_service(system, out)?;
        let code = system.run(
            &strings(&[&system.tool("sc.exe"), "delete", SERVICE_NAME]),
            120.0,
        );
        if code != 0 && code != SC_MISSING {
            return Err(error(format!(
                "could not remove the sensor service (sc.exe: {code})"
            )));
        }
        out.say("removed the sensor service");
    }
    let target = service_folder(system)?;
    let owned = target
        .parent()
        .is_some_and(|parent| parent.join("unins000.exe").exists());
    if target.exists() && !owned {
        for _ in 0..10 {
            let _ = std::fs::remove_dir_all(&target);
            if !target.exists() {
                break;
            }
            system.sleep(0.5);
        }
        if target.exists() {
            return Err(error(format!("could not remove {}", target.display())));
        }
        out.say(format!("removed {}", target.display()));
        if let Some(parent) = target.parent() {
            let _ = std::fs::remove_dir(parent);
        }
    }
    Ok(())
}

fn old(version: &str) -> bool {
    let digits: String = version.chars().take_while(char::is_ascii_digit).collect();
    digits.parse::<u64>().is_ok_and(|major| major < 2)
}

fn install_pawnio(
    system: &dyn System,
    out: &mut Outcome,
    installer: Option<&Path>,
) -> Result<(), SetupError> {
    if let Some(version) = system.pawnio_version().filter(|v| !old(v)) {
        out.say(if version.is_empty() {
            "PawnIO is installed".to_string()
        } else {
            format!("PawnIO {version} is installed")
        });
        return Ok(());
    }
    let installer = installer.ok_or_else(|| {
        error(
            "PawnIO's installer is not part of this download: install PawnIO from \
             https://pawnio.eu",
        )
    })?;
    let mut args = vec![installer.display().to_string()];
    args.extend(strings(&PAWNIO_ARGUMENTS));
    let code = system.run(&args, 600.0);
    if code != 0 && code != RESTART_NEEDED {
        return Err(error(format!("PawnIO's installer ended with {code}")));
    }
    out.pawnio_installed = true;
    out.say(if code != 0 {
        "installed PawnIO: Windows needs a restart to finish"
    } else {
        "installed PawnIO"
    });
    Ok(())
}

fn join_group(
    system: &dyn System,
    out: &mut Outcome,
    user: Option<&str>,
) -> Result<(), SetupError> {
    let sid = user
        .map(str::to_string)
        .or_else(|| system.session_user())
        .ok_or_else(|| error("could not tell who is signed in: give --user-sid"))?;
    if system.group_add(PERF_LOG_USERS, &sid)? {
        let mut added = system.added();
        added.push(sid);
        system.set_added(&added)?;
        out.say("joined Performance Log Users: game frame rates count from the next sign-in");
    } else {
        out.say("already in Performance Log Users");
    }
    Ok(())
}

/// leave_group: only the memberships this setup gave: whoever was in the group before stays.
fn leave_group(system: &dyn System, out: &mut Outcome) -> Result<(), SetupError> {
    let added = system.added();
    for sid in &added {
        if system.group_remove(PERF_LOG_USERS, sid)? {
            out.say("left Performance Log Users");
        }
    }
    if !added.is_empty() {
        system.set_added(&[])?;
    }
    Ok(())
}

/// What `system-setup` is asked to do.
#[derive(Default)]
pub struct Asked<'a> {
    pub service: bool,
    pub pawnio: bool,
    pub perf_log_users: bool,
    pub remove: bool,
    pub user: Option<&'a str>,
    pub source: Option<&'a Path>,
    pub installer: Option<&'a Path>,
}

type Step<'a> = (
    &'static str,
    Box<dyn Fn(&mut Outcome) -> Result<(), SetupError> + 'a>,
);

/// apply: each step asked for, in an order that works (PawnIO before the service that uses
/// it); one that fails does not keep the others from being tried.
pub fn apply(system: &dyn System, asked: &Asked) -> Outcome {
    let mut out = Outcome::default();
    let mut steps: Vec<Step> = Vec::new();
    if asked.remove {
        if asked.service {
            steps.push((
                "the sensor service",
                Box::new(|o| remove_service(system, o)),
            ));
        }
        if asked.perf_log_users {
            steps.push((
                "Performance Log Users",
                Box::new(|o| leave_group(system, o)),
            ));
        }
        if asked.pawnio {
            out.say("PawnIO stays: other programs may use it; remove it under Settings → Apps");
        }
    } else {
        if asked.pawnio {
            steps.push((
                "PawnIO",
                Box::new(|o| install_pawnio(system, o, asked.installer)),
            ));
        }
        if asked.service {
            steps.push((
                "the sensor service",
                Box::new(|o| install_service(system, o, asked.source)),
            ));
        }
        if asked.perf_log_users {
            steps.push((
                "Performance Log Users",
                Box::new(|o| join_group(system, o, asked.user)),
            ));
        }
    }
    for (name, step) in steps {
        if let Err(SetupError(why)) = step(&mut out) {
            out.fail(&format!("{name}: {why}"));
        }
    }
    out
}

/// status: what is in place (no rights needed).
pub fn status(system: &dyn System, user: Option<&str>) -> Dict {
    let info = system.service();
    let sid = user.map(str::to_string).or_else(|| system.session_user());
    let member = sid.and_then(|sid| {
        system
            .group_members(PERF_LOG_USERS)
            .ok()
            .map(|members| members.contains(&sid))
    });
    let mut told = Dict::new();
    told.insert(
        "service".into(),
        info.map_or(Value::None, |info| {
            let mut s = Dict::new();
            s.insert("state".into(), Value::Str(info.state));
            s.insert("pid".into(), Value::int(i64::from(info.pid)));
            s.insert("program".into(), Value::Str(info.program));
            Value::Dict(s)
        }),
    );
    told.insert(
        "pawnio".into(),
        system.pawnio_version().map_or(Value::None, Value::Str),
    );
    told.insert(
        "perf_log_users".into(),
        member.map_or(Value::None, Value::Bool),
    );
    let shown = |path: Option<PathBuf>| path.map_or(String::new(), |p| p.display().to_string());
    told.insert("service_files".into(), Value::Str(shown(bundled_service())));
    told.insert(
        "pawnio_installer".into(),
        Value::Str(shown(pawnio_installer())),
    );
    told
}

/// The options of `system-setup` (cli.py's), as given.
#[derive(Default)]
pub struct Options {
    pub service: bool,
    pub pawnio: bool,
    pub perf_log_users: bool,
    pub user_sid: Option<String>,
    pub remove: bool,
    pub status: bool,
}

/// run_command: `libre-panel system-setup` on Windows; prints what it did, gives the exit
/// code.
pub fn run_command(options: &mut Options, system: &dyn System, admin: bool) -> u8 {
    if let Some(user) = options.user_sid.as_deref().filter(|user| !is_sid(user)) {
        eprintln!("error: {user} is not a SID (S-1-5-21-...)");
        return EXIT_FAILED;
    }
    let user = options.user_sid.as_deref();
    if options.status {
        let how = lp_py::json::Dumps {
            indent: Some("  ".into()),
            ..lp_py::json::Dumps::default()
        };
        println!(
            "{}",
            lp_py::json::dumps(&Value::Dict(status(system, user)), &how)
        );
        return 0;
    }
    if !(options.service || options.pawnio || options.perf_log_users) {
        if !options.remove {
            eprintln!("error: say what to set up: --service, --pawnio, --perf-log-users");
            return EXIT_FAILED;
        }
        options.service = true; // --remove alone: the service
    }
    if !admin {
        eprintln!(
            "error: this needs administrator rights: run it in a terminal opened with 'Run as \
             administrator' (the app asks Windows for them by itself)"
        );
        return EXIT_NOT_ADMIN;
    }
    let (source, installer) = (bundled_service(), pawnio_installer());
    let out = apply(
        system,
        &Asked {
            service: options.service,
            pawnio: options.pawnio,
            perf_log_users: options.perf_log_users,
            remove: options.remove,
            user,
            source: source.as_deref(),
            installer: installer.as_deref(),
        },
    );
    for line in &out.lines {
        if line.starts_with("error:") {
            eprintln!("{line}");
        } else {
            println!("{line}");
        }
    }
    if out.failed {
        EXIT_FAILED
    } else {
        0
    }
}

// -- "Unlock all sensors" in the app ------------------------------------------------------------

const UNLOCK: [&str; 3] = ["--service", "--pawnio", "--perf-log-users"];
const REMOVE: [&str; 3] = ["--remove", "--service", "--perf-log-users"];

#[derive(Clone)]
struct Job {
    action: String,
    running: bool,
    code: Option<i64>,
    cancelled: bool,
    error: String,
}

impl Job {
    fn value(&self) -> Value {
        let mut told = Dict::new();
        told.insert("action".into(), Value::Str(self.action.clone()));
        told.insert("running".into(), Value::Bool(self.running));
        told.insert("code".into(), self.code.map_or(Value::None, Value::int));
        told.insert("cancelled".into(), Value::Bool(self.cancelled));
        told.insert("error".into(), Value::Str(self.error.clone()));
        Value::Dict(told)
    }
}

static JOB: Mutex<Option<Job>> = Mutex::new(None);

fn job() -> std::sync::MutexGuard<'static, Option<Job>> {
    JOB.lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// Elevator.start: `libre-panel system-setup` with Windows' question for rights, one at a
/// time, in a thread; how the last one ended is kept.
pub fn start(action: &str, program: &Path, user: Option<&str>) {
    let flags: &[&str] = if action == "unlock" { &UNLOCK } else { &REMOVE };
    {
        let mut kept = job();
        if kept.as_ref().is_some_and(|job| job.running) {
            return;
        }
        *kept = Some(Job {
            action: action.to_string(),
            running: true,
            code: None,
            cancelled: false,
            error: String::new(),
        });
    }
    let mut arguments = vec!["system-setup".to_string()];
    arguments.extend(flags.iter().map(|f| f.to_string()));
    if let Some(user) = user {
        arguments.extend(["--user-sid".to_string(), user.to_string()]);
    }
    let parameters = list2cmdline(&arguments);
    let program = program.display().to_string();
    let work = move || {
        let done = win::run_elevated(&program, &parameters, Some(900.0));
        let mut kept = job();
        if let Some(job) = kept.as_mut() {
            match done {
                Ok(code) => job.code = Some(code),
                Err(win::Elevated::Cancelled) => job.cancelled = true,
                Err(win::Elevated::Failed(why)) => {
                    crate::logs::log(
                        crate::logs::Level::Warning,
                        "libre_panel.sensorservice.setup",
                        &format!("system-setup could not be started: {why}"),
                    );
                    job.error = why;
                }
            }
            job.running = false;
        }
    };
    if std::thread::Builder::new()
        .name("system-setup".into())
        .spawn(work)
        .is_err()
    {
        if let Some(job) = job().as_mut() {
            job.running = false;
            job.error = "the setup could not be started".into();
        }
    }
}

/// subprocess.list2cmdline: arguments as Windows' C runtime reads them back.
pub fn list2cmdline(arguments: &[String]) -> String {
    let mut line = String::new();
    for (n, argument) in arguments.iter().enumerate() {
        if n > 0 {
            line.push(' ');
        }
        let quote = argument.is_empty() || argument.contains([' ', '\t']);
        if quote {
            line.push('"');
        }
        let mut backslashes = 0;
        for c in argument.chars() {
            match c {
                '\\' => backslashes += 1,
                '"' => {
                    line.push_str(&"\\".repeat(backslashes * 2 + 1));
                    line.push('"');
                    backslashes = 0;
                }
                _ => {
                    line.push_str(&"\\".repeat(backslashes));
                    backslashes = 0;
                    line.push(c);
                }
            }
        }
        line.push_str(&"\\".repeat(if quote { backslashes * 2 } else { backslashes }));
        if quote {
            line.push('"');
        }
    }
    line
}

/// current_user: the SID of the account this program runs as (who unlocks).
pub fn current_user() -> Option<String> {
    win::token_user_sid()
}

/// service_state: what the app shows of the service: None where it is not installed.
fn service_state() -> Value {
    let Some((_program, start)) = win::registration() else {
        return Value::None;
    };
    let info = win::Real.service();
    let mut told = Dict::new();
    told.insert(
        "state".into(),
        Value::Str(info.map_or("unknown".into(), |info| info.state)),
    );
    told.insert("disabled".into(), Value::Bool(start == Some(DISABLED)));
    Value::Dict(told)
}

/// frame_rate_rights: "yes" (in Performance Log Users, or an administrator), "sign-in" (in the
/// group from the next sign-in), "no"; None where it cannot be told.
fn frame_rate_rights() -> Value {
    if win::token_has(PERF_LOG_USERS) == Some(true) {
        return Value::from("yes");
    }
    let (Some(me), Ok(members)) = (win::token_user_sid(), win::group_members(PERF_LOG_USERS))
    else {
        return Value::None;
    };
    Value::from(if members.contains(&me) {
        "sign-in"
    } else {
        "no"
    })
}

/// sys.platform's word for this system.
pub fn platform_word() -> &'static str {
    if cfg!(windows) {
        "win32"
    } else if cfg!(target_os = "macos") {
        "darwin"
    } else {
        "linux"
    }
}

/// unlock_state: what "Unlock all sensors" would set up, what is in place, and the last run.
pub fn unlock_state() -> Dict {
    let windows = cfg!(windows);
    let mut told = Dict::new();
    told.insert("platform".into(), Value::from(platform_word()));
    told.insert(
        "available".into(),
        Value::Bool(windows && bundled_service().is_some() && cli_program().is_some()),
    );
    told.insert(
        "service".into(),
        if windows {
            service_state()
        } else {
            Value::None
        },
    );
    told.insert(
        "pawnio".into(),
        if windows {
            win::Real.pawnio_version().map_or(Value::None, Value::Str)
        } else {
            Value::None
        },
    );
    told.insert(
        "frame_rates".into(),
        if windows {
            frame_rate_rights()
        } else {
            Value::None
        },
    );
    told.insert("job".into(), job().as_ref().map_or(Value::None, Job::value));
    told
}

#[cfg(windows)]
pub mod win {
    use super::{ServiceInfo, SetupError, System};
    use std::path::PathBuf;
    use windows_sys::core::{BOOL, PWSTR};
    use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, LocalFree, HANDLE};
    use windows_sys::Win32::NetworkManagement::NetManagement::{
        NetApiBufferFree, NetLocalGroupAddMembers, NetLocalGroupDelMembers,
        NetLocalGroupGetMembers, LOCALGROUP_MEMBERS_INFO_0, MAX_PREFERRED_LENGTH,
    };
    use windows_sys::Win32::Security::Authorization::{
        ConvertSidToStringSidW, ConvertStringSidToSidW,
    };
    use windows_sys::Win32::Security::{
        CheckTokenMembership, GetTokenInformation, LookupAccountNameW, LookupAccountSidW,
        TokenUser, PSID, TOKEN_QUERY, TOKEN_USER,
    };
    use windows_sys::Win32::System::Registry::{
        RegCloseKey, RegDeleteKeyExW, RegDeleteKeyValueW, RegGetValueW, RegOpenKeyExW,
        RegSetKeyValueW, HKEY, HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_32KEY, KEY_WOW64_64KEY,
        REG_MULTI_SZ, RRF_RT_REG_DWORD, RRF_RT_REG_EXPAND_SZ, RRF_RT_REG_MULTI_SZ, RRF_RT_REG_SZ,
        RRF_SUBKEY_WOW6432KEY, RRF_SUBKEY_WOW6464KEY,
    };
    use windows_sys::Win32::System::RemoteDesktop::{
        ProcessIdToSessionId, WTSDomainName, WTSFreeMemory, WTSQuerySessionInformationW,
        WTSUserName,
    };
    use windows_sys::Win32::System::SystemInformation::GetSystemDirectoryW;
    use windows_sys::Win32::System::Threading::{
        GetCurrentProcess, GetCurrentProcessId, GetExitCodeProcess, OpenProcessToken,
        WaitForSingleObject,
    };
    use windows_sys::Win32::UI::Shell::{
        ShellExecuteExW, SEE_MASK_FLAG_NO_UI, SEE_MASK_NOASYNC, SEE_MASK_NOCLOSEPROCESS,
        SHELLEXECUTEINFOW,
    };

    const PAWNIO_KEY: &str = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO";
    const SERVICES_KEY: &str = r"SYSTEM\CurrentControlSet\Services";
    const ERROR_MORE_DATA: u32 = 234;
    const ERROR_MEMBER_IN_ALIAS: u32 = 1378;
    const ERROR_MEMBER_NOT_IN_ALIAS: u32 = 1377;
    const ERROR_CANCELLED: u32 = 1223;

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    fn text_of(pointer: *const u16) -> String {
        if pointer.is_null() {
            return String::new();
        }
        // SAFETY: a string Windows ends with a 0.
        unsafe {
            let mut end = 0;
            while *pointer.add(end) != 0 {
                end += 1;
            }
            String::from_utf16_lossy(std::slice::from_raw_parts(pointer, end))
        }
    }

    fn failed(what: &str, code: u32) -> SetupError {
        SetupError(format!(
            "{what}: {}",
            std::io::Error::from_raw_os_error(code as i32)
        ))
    }

    /// A value of HKLM as text, a number or a list of texts; None where it is not there.
    fn reg_get(key: &str, name: &str, flags: u32) -> Option<Vec<u8>> {
        let (key, name) = (wide(key), wide(name));
        let mut size = 0u32;
        // SAFETY: first the size, then the value into a buffer of that size.
        unsafe {
            let call = |data: *mut u8, size: &mut u32| {
                RegGetValueW(
                    HKEY_LOCAL_MACHINE,
                    key.as_ptr(),
                    name.as_ptr(),
                    flags,
                    std::ptr::null_mut(),
                    data.cast(),
                    size,
                )
            };
            if call(std::ptr::null_mut(), &mut size) != 0 {
                return None;
            }
            let mut data = vec![0u8; size as usize + 2];
            let mut size = data.len() as u32;
            if call(data.as_mut_ptr(), &mut size) != 0 {
                return None;
            }
            data.truncate(size as usize);
            Some(data)
        }
    }

    /// Whether a key of HKLM is there (in the registry's view given: 64 or 32 bits, or none).
    fn key_exists(key: &str, view: u32) -> bool {
        let key = wide(key);
        let mut handle: HKEY = std::ptr::null_mut();
        // SAFETY: the key opened for reading only, closed at once.
        unsafe {
            if RegOpenKeyExW(
                HKEY_LOCAL_MACHINE,
                key.as_ptr(),
                0,
                KEY_READ | view,
                &mut handle,
            ) != 0
            {
                return false;
            }
            RegCloseKey(handle);
        }
        true
    }

    fn words(data: &[u8]) -> Vec<u16> {
        data.chunks_exact(2)
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect()
    }

    fn reg_text(key: &str, name: &str, flags: u32) -> Option<String> {
        let data = words(&reg_get(key, name, flags)?);
        let end = data.iter().position(|c| *c == 0).unwrap_or(data.len());
        Some(String::from_utf16_lossy(&data[..end]))
    }

    /// The service's ImagePath and Start as Windows has them on record; None: not installed.
    pub fn registration() -> Option<(Option<String>, Option<u32>)> {
        let key = format!(r"{SERVICES_KEY}\{}", super::SERVICE_NAME);
        if !key_exists(&key, 0) {
            return None;
        }
        // (as winreg reads it: an expandable text as it is written, not expanded)
        let program = reg_text(
            &key,
            "ImagePath",
            RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ | 0x1000_0000,
        );
        let start = reg_get(&key, "Start", RRF_RT_REG_DWORD)
            .filter(|d| d.len() >= 4)
            .map(|d| u32::from_le_bytes([d[0], d[1], d[2], d[3]]));
        Some((program, start))
    }

    pub struct Real;

    impl System for Real {
        fn program_files(&self) -> Result<PathBuf, SetupError> {
            reg_text(
                r"SOFTWARE\Microsoft\Windows\CurrentVersion",
                "ProgramFilesDir",
                RRF_RT_REG_SZ | RRF_SUBKEY_WOW6464KEY,
            )
            .map(PathBuf::from)
            .ok_or_else(|| SetupError("Program Files is not on record".into()))
        }

        fn tool(&self, name: &str) -> String {
            let mut buffer = vec![0u16; 300];
            // SAFETY: a buffer of the size given.
            let length = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) };
            let folder = String::from_utf16_lossy(&buffer[..length as usize]);
            format!(r"{folder}\{name}")
        }

        fn service(&self) -> Option<ServiceInfo> {
            let (state, pid) = lp_sensors::winlink::service_status(super::SERVICE_NAME).ok()??;
            let names = [
                (1, "stopped"),
                (2, "starting"),
                (3, "stopping"),
                (4, "running"),
                (5, "continuing"),
                (6, "pausing"),
                (7, "paused"),
            ];
            let state = names
                .iter()
                .find(|(n, _)| *n == state)
                .map_or(state.to_string(), |(_, name)| name.to_string());
            let program = registration().and_then(|(p, _)| p).unwrap_or_default();
            Some(ServiceInfo {
                state,
                pid,
                program,
            })
        }

        fn run(&self, args: &[String], timeout: f64) -> i64 {
            let mut command = std::process::Command::new(&args[0]);
            command.args(&args[1..]);
            let done = crate::capture::quiet(&mut command)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .spawn();
            let Ok(mut child) = done else {
                return -1;
            };
            let end = std::time::Instant::now() + std::time::Duration::from_secs_f64(timeout);
            loop {
                match child.try_wait() {
                    Ok(Some(status)) => return i64::from(status.code().unwrap_or(-1)),
                    Ok(None) if std::time::Instant::now() < end => {
                        std::thread::sleep(std::time::Duration::from_millis(50));
                    }
                    _ => {
                        let _ = child.kill();
                        let _ = child.wait();
                        return -1;
                    }
                }
            }
        }

        fn pawnio_version(&self) -> Option<String> {
            let views = [
                (KEY_WOW64_64KEY, RRF_SUBKEY_WOW6464KEY),
                (KEY_WOW64_32KEY, RRF_SUBKEY_WOW6432KEY),
            ];
            for (open, view) in views {
                if key_exists(PAWNIO_KEY, open) {
                    return Some(
                        reg_text(PAWNIO_KEY, "DisplayVersion", RRF_RT_REG_SZ | view)
                            .unwrap_or_default(),
                    );
                }
            }
            None
        }

        fn group_add(&self, group: &str, member: &str) -> Result<bool, SetupError> {
            change_group(true, group, member)
        }

        fn group_remove(&self, group: &str, member: &str) -> Result<bool, SetupError> {
            change_group(false, group, member)
        }

        fn group_members(&self, group: &str) -> Result<Vec<String>, SetupError> {
            group_members(group)
        }

        fn session_user(&self) -> Option<String> {
            session_user_sid().or_else(token_user_sid)
        }

        fn added(&self) -> Vec<String> {
            let Some(data) = reg_get(
                super::SETUP_KEY,
                super::ADDED_VALUE,
                RRF_RT_REG_MULTI_SZ | RRF_SUBKEY_WOW6464KEY,
            ) else {
                return Vec::new();
            };
            String::from_utf16_lossy(&words(&data))
                .split('\0')
                .filter(|sid| super::is_sid(sid))
                .map(str::to_string)
                .collect()
        }

        fn set_added(&self, sids: &[String]) -> Result<(), SetupError> {
            let key = wide(super::SETUP_KEY);
            let name = wide(super::ADDED_VALUE);
            let mut unique: Vec<&String> = Vec::new();
            for sid in sids {
                if !unique.contains(&sid) {
                    unique.push(sid);
                }
            }
            // SAFETY: the key and value names and the data, of the sizes given.
            unsafe {
                if unique.is_empty() {
                    RegDeleteKeyValueW(HKEY_LOCAL_MACHINE, key.as_ptr(), name.as_ptr());
                    RegDeleteKeyExW(HKEY_LOCAL_MACHINE, key.as_ptr(), KEY_WOW64_64KEY, 0);
                    return Ok(());
                }
                let mut data: Vec<u16> = Vec::new();
                for sid in unique {
                    data.extend(sid.encode_utf16());
                    data.push(0);
                }
                data.push(0);
                let done = RegSetKeyValueW(
                    HKEY_LOCAL_MACHINE,
                    key.as_ptr(),
                    name.as_ptr(),
                    REG_MULTI_SZ,
                    data.as_ptr().cast(),
                    (data.len() * 2) as u32,
                );
                if done != 0 {
                    return Err(failed("RegSetKeyValue", done));
                }
            }
            Ok(())
        }

        fn alive(&self, pid: u32) -> bool {
            crate::access::pid_exists(i64::from(pid))
        }

        fn sleep(&self, seconds: f64) {
            std::thread::sleep(std::time::Duration::from_secs_f64(seconds));
        }
    }

    fn sid_text(sid: PSID) -> Option<String> {
        let mut out: PWSTR = std::ptr::null_mut();
        // SAFETY: Windows writes the text and gives it to free.
        unsafe {
            if ConvertSidToStringSidW(sid, &mut out) == 0 {
                return None;
            }
            let text = text_of(out);
            LocalFree(out.cast());
            Some(text)
        }
    }

    /// The binary SID of "S-1-…", freed with LocalFree.
    fn sid_of(text: &str) -> Option<PSID> {
        let mut sid: PSID = std::ptr::null_mut();
        let wide = wide(text);
        // SAFETY: Windows writes the SID and gives it to free.
        let done = unsafe { ConvertStringSidToSidW(wide.as_ptr(), &mut sid) };
        (done != 0).then_some(sid)
    }

    pub fn token_user_sid() -> Option<String> {
        // SAFETY: this process's token, asked for its user into a buffer of the size it
        // says, then closed.
        unsafe {
            let mut token: HANDLE = std::ptr::null_mut();
            if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
                return None;
            }
            let mut size = 0u32;
            GetTokenInformation(token, TokenUser, std::ptr::null_mut(), 0, &mut size);
            let mut buffer = vec![0u8; (size as usize).max(size_of::<TOKEN_USER>())];
            let done = GetTokenInformation(
                token,
                TokenUser,
                buffer.as_mut_ptr().cast(),
                buffer.len() as u32,
                &mut size,
            );
            CloseHandle(token);
            if done == 0 {
                return None;
            }
            let user = &*(buffer.as_ptr() as *const TOKEN_USER);
            sid_text(user.User.Sid)
        }
    }

    pub fn token_has(sid: &str) -> Option<bool> {
        let binary = sid_of(sid)?;
        let mut member: BOOL = 0;
        // SAFETY: the SID made above, freed after.
        let done = unsafe { CheckTokenMembership(std::ptr::null_mut(), binary, &mut member) };
        unsafe { LocalFree(binary) };
        (done != 0).then_some(member != 0)
    }

    fn account_sid(account: &str) -> Option<String> {
        let name = wide(account);
        let (mut sid_size, mut domain_size, mut kind) = (0u32, 0u32, 0i32);
        // SAFETY: first the sizes, then into buffers of those sizes.
        unsafe {
            LookupAccountNameW(
                std::ptr::null(),
                name.as_ptr(),
                std::ptr::null_mut(),
                &mut sid_size,
                std::ptr::null_mut(),
                &mut domain_size,
                &mut kind,
            );
            if sid_size == 0 {
                return None;
            }
            let mut sid = vec![0u8; sid_size as usize];
            let mut domain = vec![0u16; domain_size.max(1) as usize];
            if LookupAccountNameW(
                std::ptr::null(),
                name.as_ptr(),
                sid.as_mut_ptr().cast(),
                &mut sid_size,
                domain.as_mut_ptr(),
                &mut domain_size,
                &mut kind,
            ) == 0
            {
                return None;
            }
            sid_text(sid.as_mut_ptr().cast())
        }
    }

    /// The name of the group with this SID, in the language of this Windows.
    fn account_name(sid: &str) -> Result<String, SetupError> {
        let binary =
            sid_of(sid).ok_or_else(|| SetupError(format!("ConvertStringSidToSid {sid}")))?;
        let (mut name_size, mut domain_size, mut kind) = (0u32, 0u32, 0i32);
        // SAFETY: as account_sid; the SID freed after.
        let found = unsafe {
            LookupAccountSidW(
                std::ptr::null(),
                binary,
                std::ptr::null_mut(),
                &mut name_size,
                std::ptr::null_mut(),
                &mut domain_size,
                &mut kind,
            );
            let mut name = vec![0u16; name_size.max(1) as usize];
            let mut domain = vec![0u16; domain_size.max(1) as usize];
            let done = LookupAccountSidW(
                std::ptr::null(),
                binary,
                name.as_mut_ptr(),
                &mut name_size,
                domain.as_mut_ptr(),
                &mut domain_size,
                &mut kind,
            );
            let code = GetLastError();
            LocalFree(binary);
            if done == 0 {
                Err(failed(&format!("LookupAccountSid {sid}"), code))
            } else {
                Ok(text_of(name.as_ptr()))
            }
        };
        found
    }

    fn session_text(session: u32, kind: i32) -> String {
        let mut buffer: PWSTR = std::ptr::null_mut();
        let mut size = 0u32;
        // SAFETY: Windows writes the text and gives it to free.
        unsafe {
            if WTSQuerySessionInformationW(
                std::ptr::null_mut(),
                session,
                kind,
                &mut buffer,
                &mut size,
            ) == 0
            {
                return String::new();
            }
            let text = text_of(buffer);
            WTSFreeMemory(buffer.cast());
            text
        }
    }

    /// The SID of the person signed in to the session this process runs in.
    fn session_user_sid() -> Option<String> {
        let mut session = 0u32;
        // SAFETY: a number written by the call.
        if unsafe { ProcessIdToSessionId(GetCurrentProcessId(), &mut session) } == 0 {
            return None;
        }
        let user = session_text(session, WTSUserName);
        if user.is_empty() {
            return None;
        }
        let domain = session_text(session, WTSDomainName);
        account_sid(&if domain.is_empty() {
            user
        } else {
            format!("{domain}\\{user}")
        })
    }

    pub fn group_members(group_sid: &str) -> Result<Vec<String>, SetupError> {
        let group = wide(&account_name(group_sid)?);
        let mut buffer: *mut u8 = std::ptr::null_mut();
        let (mut read, mut total, mut resume) = (0u32, 0u32, 0usize);
        // SAFETY: Windows gives the members in a buffer it frees with NetApiBufferFree.
        unsafe {
            let status = NetLocalGroupGetMembers(
                std::ptr::null(),
                group.as_ptr(),
                0,
                &mut buffer,
                MAX_PREFERRED_LENGTH,
                &mut read,
                &mut total,
                &mut resume,
            );
            if status != 0 && status != ERROR_MORE_DATA {
                return Err(failed("NetLocalGroupGetMembers", status));
            }
            let entries = std::slice::from_raw_parts(
                buffer as *const LOCALGROUP_MEMBERS_INFO_0,
                read as usize,
            );
            let found = entries
                .iter()
                .filter_map(|e| sid_text(e.lgrmi0_sid))
                .collect();
            if !buffer.is_null() {
                NetApiBufferFree(buffer.cast());
            }
            Ok(found)
        }
    }

    fn change_group(add: bool, group_sid: &str, member_sid: &str) -> Result<bool, SetupError> {
        let group = wide(&account_name(group_sid)?);
        let member = sid_of(member_sid)
            .ok_or_else(|| SetupError(format!("ConvertStringSidToSid {member_sid}")))?;
        let info = LOCALGROUP_MEMBERS_INFO_0 { lgrmi0_sid: member };
        // SAFETY: one entry naming the member's SID, freed after.
        let status = unsafe {
            let entry = (&raw const info).cast::<u8>();
            let status = if add {
                NetLocalGroupAddMembers(std::ptr::null(), group.as_ptr(), 0, entry, 1)
            } else {
                NetLocalGroupDelMembers(std::ptr::null(), group.as_ptr(), 0, entry, 1)
            };
            LocalFree(member);
            status
        };
        if status == 0 {
            return Ok(true);
        }
        let none = if add {
            ERROR_MEMBER_IN_ALIAS
        } else {
            ERROR_MEMBER_NOT_IN_ALIAS
        };
        if status == none {
            return Ok(false);
        }
        Err(failed(
            if add {
                "NetLocalGroupAddMembers"
            } else {
                "NetLocalGroupDelMembers"
            },
            status,
        ))
    }

    /// How a start with Windows' question for rights went wrong.
    pub enum Elevated {
        /// the person said no
        Cancelled,
        Failed(String),
    }

    /// run_elevated: `program` with administrator rights (Windows asks the person first,
    /// unless this process has them), waited for; its exit code.
    pub fn run_elevated(
        program: &str,
        parameters: &str,
        timeout: Option<f64>,
    ) -> Result<i64, Elevated> {
        let (verb, file, params) = (wide("runas"), wide(program), wide(parameters));
        // SAFETY: the info's size and strings set; the process handle closed once waited for.
        unsafe {
            let mut info: SHELLEXECUTEINFOW = std::mem::zeroed();
            info.cbSize = size_of::<SHELLEXECUTEINFOW>() as u32;
            info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI;
            info.lpVerb = verb.as_ptr();
            info.lpFile = file.as_ptr();
            info.lpParameters = params.as_ptr();
            info.nShow = 0;
            if ShellExecuteExW(&mut info) == 0 {
                let code = GetLastError();
                if code == ERROR_CANCELLED {
                    return Err(Elevated::Cancelled);
                }
                return Err(Elevated::Failed(format!(
                    "ShellExecuteEx {program}: {}",
                    std::io::Error::from_raw_os_error(code as i32)
                )));
            }
            if info.hProcess.is_null() {
                return Ok(0);
            }
            let wait = timeout.map_or(u32::MAX, |t| (t * 1000.0) as u32);
            let result = if WaitForSingleObject(info.hProcess, wait) == 0x102 {
                Err(Elevated::Failed(format!(
                    "{program} did not end within {:.0} s",
                    timeout.unwrap_or(0.0)
                )))
            } else {
                let mut code = 0u32;
                GetExitCodeProcess(info.hProcess, &mut code);
                Ok(i64::from(code))
            };
            CloseHandle(info.hProcess);
            result
        }
    }
}

#[cfg(not(windows))]
pub mod win {
    use super::{ServiceInfo, SetupError, System};
    use std::path::PathBuf;

    pub fn registration() -> Option<(Option<String>, Option<u32>)> {
        None
    }

    pub struct Real;

    impl System for Real {
        fn program_files(&self) -> Result<PathBuf, SetupError> {
            Err(SetupError("only on Windows".into()))
        }
        fn tool(&self, name: &str) -> String {
            name.to_string()
        }
        fn service(&self) -> Option<ServiceInfo> {
            None
        }
        fn run(&self, _args: &[String], _timeout: f64) -> i64 {
            -1
        }
        fn pawnio_version(&self) -> Option<String> {
            None
        }
        fn group_add(&self, _group: &str, _member: &str) -> Result<bool, SetupError> {
            Err(SetupError("only on Windows".into()))
        }
        fn group_remove(&self, _group: &str, _member: &str) -> Result<bool, SetupError> {
            Err(SetupError("only on Windows".into()))
        }
        fn group_members(&self, _group: &str) -> Result<Vec<String>, SetupError> {
            Err(SetupError("only on Windows".into()))
        }
        fn session_user(&self) -> Option<String> {
            None
        }
        fn added(&self) -> Vec<String> {
            Vec::new()
        }
        fn set_added(&self, _sids: &[String]) -> Result<(), SetupError> {
            Ok(())
        }
        fn alive(&self, _pid: u32) -> bool {
            false
        }
        fn sleep(&self, seconds: f64) {
            std::thread::sleep(std::time::Duration::from_secs_f64(seconds));
        }
    }

    pub fn token_user_sid() -> Option<String> {
        None
    }
    pub fn token_has(_sid: &str) -> Option<bool> {
        None
    }
    pub fn group_members(_group_sid: &str) -> Result<Vec<String>, SetupError> {
        Err(SetupError("only on Windows".into()))
    }

    #[allow(dead_code)] // (a question for rights is Windows')
    pub enum Elevated {
        Cancelled,
        Failed(String),
    }

    pub fn run_elevated(
        _program: &str,
        _parameters: &str,
        _timeout: Option<f64>,
    ) -> Result<i64, Elevated> {
        Err(Elevated::Failed("only on Windows".into()))
    }
}
