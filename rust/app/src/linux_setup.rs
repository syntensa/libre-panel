//! One click for what Linux needs an administrator for (linux_setup.py, linux_rights.py): the
//! panel's USB permission, the mainboard's fan chip, the processor's power and the SATA drives'
//! temperatures, run as root through pkexec (polkit's own password dialog) or sudo.
//!
//! - **the panel** (`udev`): a rule that lets the person at the computer use the USB panels,
//!   applied to a panel that is plugged in;
//! - **the mainboard's fans and temperatures** (`modules`): the kernel's drivers for the Super
//!   I/O chips that fit the board, kept where one finds a chip; never forced;
//! - **the SATA drives' temperatures** (`drives`): the `drivetemp` driver;
//! - **the processor's power** (`rapl`), only if asked for: a rule that opens the energy
//!   counters to every user (the kernel keeps them to root since 2020: CVE-2020-8694).
//!
//! [`plan`] says what has to be done from the [`Facts`] that [`gather`] collects; [`apply`]
//! does it and reports each step. Nothing is written where it is there already.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use lp_py::{Dict, Value};

/// the USB vendor ids the udev rule is for: TURZX USB panels, the JONSBO ZC-360's screens
pub const USB_VENDORS: [&str; 3] = ["1cbe", "43a8", "345f"];
/// and other makers' screens, with their product ids
pub const USB_PRODUCTS: [(&str, &str); 10] = [
    ("0416", "5302"),
    ("0416", "5408"),
    ("4e58", "1001"),
    ("1e71", "3008"),
    ("1e71", "300e"),
    ("1e71", "3012"),
    ("1e71", "3014"),
    ("264a", "2347"),
    ("303a", "2986"),
    ("303a", "2987"),
];
pub const UDEV_RULES: &str = "/etc/udev/rules.d/60-libre-panel.rules";
pub const RAPL_RULES: &str = "/etc/udev/rules.d/60-libre-panel-rapl.rules";
pub const MODULES: &str = "/etc/modules-load.d/libre-panel.conf";
pub const OWN_FILES: [&str; 3] = [UDEV_RULES, RAPL_RULES, MODULES];
const RULE_FOLDERS: [&str; 5] = [
    "/etc/udev/rules.d",
    "/run/udev/rules.d",
    "/usr/local/lib/udev/rules.d",
    "/usr/lib/udev/rules.d",
    "/lib/udev/rules.d",
];
const SYSTEM_PATH: &str = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
const POWERCAP: &str = "/sys/class/powercap";
const DRIVE_MODULE: &str = "drivetemp";
/// The kernel drivers for the chips on a mainboard, tried in this order for its maker.
const BOARD_DRIVERS: [(&[&str], &[&str]); 8] = [
    (&["asus"], &["nct6775"]),
    (&["gigabyte", "aorus"], &["it87", "nct6775"]),
    (&["micro-star", "msi"], &["nct6775", "nct6683", "it87"]),
    (&["asrock"], &["nct6775", "it87", "nct6683"]),
    (&["biostar"], &["nct6775", "it87"]),
    (&["evga"], &["nct6775"]),
    (&["supermicro"], &["nct6775"]),
    (&["intel"], &["nct6683"]),
];
/// What `sensors-detect` may have recorded that this setup loads for the person.
const RECORDED_DRIVERS: [&str; 8] = [
    "nct6775",
    "nct6683",
    "it87",
    "w83627ehf",
    "w83627hf",
    "f71882fg",
    "dme1737",
    "sch5627",
];
/// DMI chassis types of portables: an embedded controller where a desktop has a Super I/O chip.
const PORTABLE_CHASSIS: [i64; 8] = [8, 9, 10, 11, 14, 30, 31, 32];
const VIRTUAL_MACHINES: [&str; 8] = [
    "virtual",
    "vmware",
    "qemu",
    "kvm",
    "xen",
    "bochs",
    "parallels",
    "bhyve",
];
const LOADED: &str = "loaded";
const ABSENT: &str = "none";
const BUSY: &str = "busy";
const MISSING: &str = "missing";
const FAILED: &str = "failed";

fn known_modules() -> Vec<&'static str> {
    let mut found: Vec<&str> = Vec::new();
    for (_, names) in BOARD_DRIVERS {
        for name in names {
            if !found.contains(name) {
                found.push(name);
            }
        }
    }
    for name in RECORDED_DRIVERS {
        if !found.contains(&name) {
            found.push(name);
        }
    }
    found
}

/// What the person asked for: `rapl` true opens the counters, false closes them, none leaves
/// them; `remove` takes out everything the setup installed.
#[derive(Clone, Copy, Default)]
pub struct Options {
    pub rapl: Option<bool>,
    pub remove: bool,
}

/// What this computer is.
#[derive(Clone, Default)]
pub struct Facts {
    pub dmi: BTreeMap<String, String>,
    pub loaded: Vec<String>,
    pub available: Vec<String>,
    pub hwmon: Vec<String>,
    pub recorded: Vec<String>,
    pub disks: bool,
    pub rapl: Vec<String>,
    pub rapl_closed: Vec<String>,
    /// a panel that is plugged in, which this user may not open
    pub panel_denied: bool,
    pub rule_elsewhere: bool,
    pub files: BTreeMap<String, Option<String>>,
    pub tools: BTreeMap<String, String>,
}

impl Facts {
    fn file(&self, path: &str) -> Option<&str> {
        self.files.get(path).and_then(|f| f.as_deref())
    }

    fn tool(&self, name: &str, default: &str) -> String {
        self.tools
            .get(name)
            .cloned()
            .unwrap_or_else(|| default.into())
    }
}

/// udev_rules_text: the rule for the panels (the app's assets/60-libre-panel.rules).
pub fn udev_rules_text() -> String {
    crate::home::Home::find()
        .ok()
        .and_then(|home| {
            std::fs::read_to_string(home.data.join("assets").join("60-libre-panel.rules")).ok()
        })
        .unwrap_or_default()
}

pub fn rapl_rules_text(chmod: &str) -> String {
    format!(
        "# Libre Panel: lets every user read the processor's energy counters, for its power\n\
         # (cpu.power). Since 2020 the kernel keeps them to root: they leak what a processor\n\
         # computes (CVE-2020-8694, PLATYPUS). Leave this out on a PC where you do not trust\n\
         # every program you run.\n\
         # Written by `libre-panel system-setup --linux --rapl`, taken out by `--remove`.\n\
         SUBSYSTEM==\"powercap\", ACTION==\"add|change\", TEST==\"energy_uj\", \
         RUN+=\"{chmod} a+r /sys%p/energy_uj\"\n"
    )
}

pub fn modules_text(names: &[String]) -> String {
    let mut text =
        "# Written by `libre-panel system-setup --linux`: drivers for the sensors of this PC.\n\
                    # `libre-panel system-setup --linux --remove` takes this file out again.\n"
            .to_string();
    for name in names {
        text.push_str(name);
        text.push('\n');
    }
    text
}

/// persisted: the modules a modules-load.d file names.
pub fn persisted(content: Option<&str>) -> Vec<String> {
    content
        .unwrap_or("")
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .map(str::to_string)
        .collect()
}

// -- the plan -----------------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub enum Action {
    Write {
        path: String,
        content: String,
        mode: u32,
    },
    Remove {
        path: String,
    },
    Run {
        argv: Vec<String>,
    },
    /// Try these kernel modules (`first`: stop at the first that finds its chip); those that do
    /// are written into `persist`.
    Load {
        modules: Vec<String>,
        first: bool,
        persist: String,
    },
}

#[derive(Clone, Debug)]
pub struct Step {
    pub id: &'static str,
    pub actions: Vec<Action>,
    pub modules: Vec<String>,
}

fn write(path: &str, content: String) -> Action {
    Action::Write {
        path: path.into(),
        content,
        mode: 0o644,
    }
}

fn load(modules: &[String], first: bool) -> Action {
    Action::Load {
        modules: modules.to_vec(),
        first,
        persist: MODULES.into(),
    }
}

fn run(argv: Vec<String>) -> Action {
    Action::Run { argv }
}

fn board_chip(names: &[String]) -> bool {
    names
        .iter()
        .any(|name| lp_sensors::hwmon::kind_of(name) == lp_sensors::hwmon::ChipKind::Board)
}

/// board_drivers: the drivers worth trying for the fans and temperatures on this board.
pub fn board_drivers(facts: &Facts) -> Vec<String> {
    if board_chip(&facts.hwmon) {
        return Vec::new();
    }
    let found: Vec<String> = facts
        .recorded
        .iter()
        .filter(|name| facts.available.contains(name))
        .cloned()
        .collect();
    if !found.is_empty() {
        return found; // the person ran sensors-detect: its word counts more than a guess
    }
    let dmi = |key: &str| {
        facts
            .dmi
            .get(key)
            .map(|v| v.to_lowercase())
            .unwrap_or_default()
    };
    let chassis = dmi("chassis_type").trim().parse::<i64>().unwrap_or(0);
    let machine = format!("{} {}", dmi("sys_vendor"), dmi("product_name"));
    if PORTABLE_CHASSIS.contains(&chassis) || VIRTUAL_MACHINES.iter().any(|w| machine.contains(w)) {
        return Vec::new();
    }
    let maker = dmi("board_vendor");
    for (words, names) in BOARD_DRIVERS {
        if words.iter().any(|word| maker.contains(word)) {
            return names
                .iter()
                .filter(|name| facts.available.iter().any(|a| a == *name))
                .map(|name| name.to_string())
                .collect();
        }
    }
    Vec::new()
}

/// plan: what has to be done to bring the person what they asked for, on this computer.
pub fn plan(facts: &Facts, options: Options) -> Vec<Step> {
    if options.remove {
        removal(facts)
    } else {
        installation(facts, options)
    }
}

fn installation(facts: &Facts, options: Options) -> Vec<Step> {
    let mut steps = Vec::new();
    let mut changed: Vec<&str> = Vec::new();
    let rules = udev_rules_text();
    let ours = facts.file(UDEV_RULES);
    if ours != Some(rules.as_str()) && !(ours.is_none() && facts.rule_elsewhere) {
        steps.push(Step {
            id: "udev",
            actions: vec![write(UDEV_RULES, rules)],
            modules: Vec::new(),
        });
        changed.push("usb");
    }
    let drivers = board_drivers(facts);
    if !drivers.is_empty() {
        steps.push(Step {
            id: "modules",
            actions: vec![load(&drivers, true)],
            modules: drivers,
        });
    }
    let drive = DRIVE_MODULE.to_string();
    if facts.disks
        && facts.available.contains(&drive)
        && !facts.loaded.contains(&drive)
        && !persisted(facts.file(MODULES)).contains(&drive)
    {
        steps.push(Step {
            id: "drives",
            actions: vec![load(std::slice::from_ref(&drive), false)],
            modules: vec![drive],
        });
    }
    if options.rapl == Some(true) && !facts.rapl.is_empty() {
        let mut actions = Vec::new();
        let rule = rapl_rules_text(&facts.tool("chmod", "/usr/bin/chmod"));
        if facts.file(RAPL_RULES) != Some(rule.as_str()) {
            actions.push(write(RAPL_RULES, rule));
            changed.push("powercap");
        }
        if !facts.rapl_closed.is_empty() {
            let mut argv = vec![facts.tool("chmod", "chmod"), "a+r".into()];
            argv.extend(facts.rapl_closed.iter().cloned());
            actions.push(run(argv));
        }
        if !actions.is_empty() {
            steps.push(Step {
                id: "rapl",
                actions,
                modules: Vec::new(),
            });
        }
    } else if options.rapl == Some(false) && facts.file(RAPL_RULES).is_some() {
        steps.push(Step {
            id: "rapl",
            actions: close_rapl(facts),
            modules: Vec::new(),
        });
        changed.push("powercap");
    }
    if !changed.is_empty() {
        steps.push(reload(facts, &changed));
    }
    steps
}

fn close_rapl(facts: &Facts) -> Vec<Action> {
    let mut actions = vec![Action::Remove {
        path: RAPL_RULES.into(),
    }];
    let opened: Vec<String> = facts
        .rapl
        .iter()
        .filter(|path| !facts.rapl_closed.contains(path))
        .cloned()
        .collect();
    if !opened.is_empty() {
        let mut argv = vec![facts.tool("chmod", "chmod"), "0400".into()];
        argv.extend(opened);
        actions.push(run(argv));
    }
    actions
}

/// removal: take out what the setup put in (drivers stay loaded until the next start).
fn removal(facts: &Facts) -> Vec<Step> {
    let mut steps = Vec::new();
    let mut changed = Vec::new();
    if facts.file(UDEV_RULES).is_some() {
        steps.push(Step {
            id: "udev",
            actions: vec![Action::Remove {
                path: UDEV_RULES.into(),
            }],
            modules: Vec::new(),
        });
        changed.push("usb");
    }
    if facts.file(MODULES).is_some() {
        steps.push(Step {
            id: "modules",
            actions: vec![Action::Remove {
                path: MODULES.into(),
            }],
            modules: Vec::new(),
        });
    }
    if facts.file(RAPL_RULES).is_some() {
        steps.push(Step {
            id: "rapl",
            actions: close_rapl(facts),
            modules: Vec::new(),
        });
        changed.push("powercap");
    }
    if !changed.is_empty() {
        steps.push(reload(facts, &changed));
    }
    steps
}

/// udev reads the rules again, and a panel that is plugged in is looked at again.
fn reload(facts: &Facts, changed: &[&str]) -> Step {
    let udevadm = facts.tool("udevadm", "udevadm");
    let mut runs = vec![run(vec![
        udevadm.clone(),
        "control".into(),
        "--reload-rules".into(),
    ])];
    if changed.contains(&"usb") {
        for vendor in USB_VENDORS {
            runs.push(run(vec![
                udevadm.clone(),
                "trigger".into(),
                "--subsystem-match=usb".into(),
                format!("--attr-match=idVendor={vendor}"),
            ]));
        }
        for (vendor, product) in USB_PRODUCTS {
            runs.push(run(vec![
                udevadm.clone(),
                "trigger".into(),
                "--subsystem-match=usb".into(),
                format!("--attr-match=idVendor={vendor}"),
                format!("--attr-match=idProduct={product}"),
            ]));
        }
        // the hidraw nodes too: a Kraken's screen takes its commands through its own
        runs.push(run(vec![
            udevadm.clone(),
            "trigger".into(),
            "--subsystem-match=hidraw".into(),
        ]));
    }
    if changed.contains(&"powercap") {
        runs.push(run(vec![
            udevadm,
            "trigger".into(),
            "--subsystem-match=powercap".into(),
        ]));
    }
    Step {
        id: "reload",
        actions: runs,
        modules: Vec::new(),
    }
}

// -- doing it -----------------------------------------------------------------------------------

/// What the setup does to a computer (a test gives its own).
pub trait System {
    fn read(&self, path: &str) -> Option<String>;
    fn write(&self, path: &str, content: &str, mode: u32) -> std::io::Result<()>;
    fn remove(&self, path: &str) -> std::io::Result<bool>;
    fn run(&self, argv: &[String]) -> std::io::Result<(i32, String)>;
    fn loaded(&self, module: &str) -> bool;
    fn tool(&self, name: &str) -> Option<String>;
    fn chips(&self) -> Vec<String>;
}

/// How one step went: `done` or `failed`, and per file or module what became of it.
#[derive(Clone, Debug)]
pub struct Outcome {
    pub id: &'static str,
    pub status: &'static str,
    pub items: Vec<(String, String)>,
    pub detail: String,
}

impl Outcome {
    fn set(&mut self, key: &str, value: &str) {
        match self.items.iter_mut().find(|(k, _)| k == key) {
            Some(item) => item.1 = value.to_string(),
            None => self.items.push((key.to_string(), value.to_string())),
        }
    }

    fn get(&self, key: &str) -> &str {
        self.items
            .iter()
            .find(|(k, _)| k == key)
            .map_or("", |(_, v)| v)
    }
}

/// classify: what modprobe's answer means for a driver that looks for a chip.
pub fn classify(code: i32, output: &str) -> &'static str {
    if code == 0 {
        return LOADED;
    }
    let text = output.to_lowercase();
    if text.contains("no such device") {
        ABSENT
    } else if text.contains("resource busy") {
        BUSY
    } else if text.contains("not found") || text.contains("no such file") {
        MISSING
    } else {
        FAILED
    }
}

/// shlex.quote
pub fn shlex_quote(text: &str) -> String {
    if text.is_empty() {
        return "''".into();
    }
    let safe = text
        .chars()
        .all(|c| c.is_alphanumeric() || c == '_' || "@%+=:,./-".contains(c));
    if safe {
        return text.into();
    }
    format!("'{}'", text.replace('\'', "'\"'\"'"))
}

/// shlex.join
pub fn shlex_join(parts: &[String]) -> String {
    parts
        .iter()
        .map(|p| shlex_quote(p))
        .collect::<Vec<_>>()
        .join(" ")
}

/// apply: do the steps, one after the other; a step that fails does not stop the others.
pub fn apply(steps: &[Step], system: &dyn System, log_line: &dyn Fn(&str)) -> Vec<Outcome> {
    let mut results = Vec::new();
    for step in steps {
        let mut result = Outcome {
            id: step.id,
            status: "done",
            items: Vec::new(),
            detail: String::new(),
        };
        for action in &step.actions {
            if let Err(why) = act(action, system, &mut result, log_line) {
                result.status = "failed";
                result.detail = why;
                log_line(&format!("{}: {}", step.id, result.detail));
                break;
            }
        }
        results.push(result);
    }
    results
}

fn act(
    action: &Action,
    system: &dyn System,
    result: &mut Outcome,
    log_line: &dyn Fn(&str),
) -> Result<(), String> {
    match action {
        Action::Write {
            path,
            content,
            mode,
        } => {
            if system.read(path).as_deref() == Some(content.as_str()) {
                result.set(path, "unchanged");
            } else {
                system
                    .write(path, content, *mode)
                    .map_err(|why| os_error(&why))?;
                result.set(path, "written");
            }
            log_line(&format!("{} {path}", result.get(path)));
        }
        Action::Remove { path } => {
            let removed = system.remove(path).map_err(|why| os_error(&why))?;
            result.set(path, if removed { "removed" } else { "unchanged" });
            log_line(&format!("{} {path}", result.get(path)));
        }
        Action::Run { argv } => {
            let (code, output) = system.run(argv).map_err(|why| os_error(&why))?;
            log_line(&format!("ran {} -> {code}", shlex_join(argv)));
            if code != 0 {
                let head = shlex_join(&argv[..argv.len().min(2)]);
                return Err(format!(
                    "RuntimeError: {head} answered {code}: {}",
                    output.trim()
                ));
            }
        }
        Action::Load {
            modules,
            first,
            persist,
        } => load_modules(modules, *first, persist, system, result, log_line)
            .map_err(|why| os_error(&why))?,
    }
    Ok(())
}

fn os_error(why: &std::io::Error) -> String {
    let kind = match why.kind() {
        std::io::ErrorKind::PermissionDenied => "PermissionError",
        std::io::ErrorKind::NotFound => "FileNotFoundError",
        _ => "OSError",
    };
    format!("{kind}: {why}")
}

fn load_modules(
    modules: &[String],
    first: bool,
    persist: &str,
    system: &dyn System,
    result: &mut Outcome,
    log_line: &dyn Fn(&str),
) -> std::io::Result<()> {
    let mut found = Vec::new();
    let modprobe = system.tool("modprobe");
    for name in modules {
        let mut answer = if system.loaded(name) {
            LOADED
        } else if let Some(modprobe) = &modprobe {
            let (code, output) = system.run(&[modprobe.clone(), name.clone()])?;
            classify(code, &output)
        } else {
            FAILED
        };
        if answer == LOADED && first && !board_chip(&system.chips()) {
            answer = ABSENT; // loaded (built in, say), but no chip came of it
        }
        result.set(name, answer);
        log_line(&format!("driver {name}: {answer}"));
        if answer == LOADED {
            found.push(name.clone());
            if first {
                break;
            }
        }
    }
    if found.is_empty() {
        return Ok(());
    }
    let mut names = persisted(system.read(persist).as_deref());
    for name in found {
        if !names.contains(&name) {
            names.push(name);
        }
    }
    let text = modules_text(&names);
    if system.read(persist).as_deref() != Some(text.as_str()) {
        system.write(persist, &text, 0o644)?;
        log_line(&format!("written {persist}"));
    }
    Ok(())
}

/// The computer this runs on, as root.
pub struct Real;

impl System for Real {
    fn read(&self, path: &str) -> Option<String> {
        std::fs::read_to_string(path).ok()
    }

    fn write(&self, path: &str, content: &str, mode: u32) -> std::io::Result<()> {
        let target = Path::new(path);
        let folder = target.parent().unwrap_or(Path::new("/"));
        std::fs::create_dir_all(folder)?;
        let mut bytes = [0u8; 6];
        let _ = getrandom::getrandom(&mut bytes);
        let name: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
        let temporary = folder.join(format!(".libre-panel-{name}"));
        let done = (|| {
            std::fs::write(&temporary, content)?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                std::fs::set_permissions(&temporary, std::fs::Permissions::from_mode(mode))?;
            }
            let _ = mode;
            std::fs::rename(&temporary, target) // a rules file is never half written
        })();
        if done.is_err() {
            let _ = std::fs::remove_file(&temporary);
        }
        done
    }

    fn remove(&self, path: &str) -> std::io::Result<bool> {
        match std::fs::remove_file(path) {
            Ok(()) => Ok(true),
            Err(why) if why.kind() == std::io::ErrorKind::NotFound => Ok(false),
            Err(why) => Err(why),
        }
    }

    fn run(&self, argv: &[String]) -> std::io::Result<(i32, String)> {
        let done = std::process::Command::new(&argv[0])
            .args(&argv[1..])
            .env_clear()
            .env("PATH", SYSTEM_PATH)
            .env("LC_ALL", "C") // modprobe's words are the same everywhere
            .stdin(std::process::Stdio::null())
            .output()?;
        let mut said = String::from_utf8_lossy(&done.stdout).into_owned();
        said.push_str(&String::from_utf8_lossy(&done.stderr));
        Ok((done.status.code().unwrap_or(-1), said))
    }

    fn loaded(&self, module: &str) -> bool {
        Path::new("/sys/module")
            .join(module.replace('-', "_"))
            .is_dir()
    }

    fn tool(&self, name: &str) -> Option<String> {
        which_system(name)
    }

    fn chips(&self) -> Vec<String> {
        lp_sensors::hwmon::scan(Path::new("/"))
            .into_iter()
            .map(|chip| chip.name)
            .collect()
    }
}

/// shutil.which(name, path=SYSTEM_PATH)
fn which_system(name: &str) -> Option<String> {
    SYSTEM_PATH
        .split(':')
        .map(|f| Path::new(f).join(name))
        .find_map(|path| {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let runs = std::fs::metadata(&path)
                    .is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0);
                runs.then(|| path.to_string_lossy().into_owned())
            }
            #[cfg(not(unix))]
            {
                path.is_file().then(|| path.to_string_lossy().into_owned())
            }
        })
}

// -- looking at the computer --------------------------------------------------------------------

fn text(path: &Path) -> Option<String> {
    let bytes = std::fs::read(path).ok()?;
    Some(String::from_utf8_lossy(&bytes).trim().to_string())
}

fn module_names(text: &str) -> Vec<String> {
    // re.findall(r"[A-Za-z0-9_-]+")
    text.split(|c: char| !(c.is_ascii_alphanumeric() || c == '_' || c == '-'))
        .filter(|w| !w.is_empty())
        .map(|w| w.replace('-', "_"))
        .collect()
}

/// Whether `text` lists the module `name` as modules.dep or modules.builtin do.
fn lists_module(text: &str, name: &str) -> bool {
    // (?m)(?:^|/)name\.ko(?:\.\w+)?(?::|$)
    for line in text.lines() {
        let mut from = 0;
        while let Some(at) = line[from..].find(&format!("{name}.ko")) {
            let start = from + at;
            let before_ok = start == 0 || line.as_bytes()[start - 1] == b'/';
            let mut rest = &line[start + name.len() + 3..];
            if let Some(after) = rest.strip_prefix('.') {
                let word: String = after
                    .chars()
                    .take_while(|c| c.is_alphanumeric() || *c == '_')
                    .collect();
                if !word.is_empty() {
                    rest = &after[word.len()..];
                }
            }
            if before_ok && (rest.is_empty() || rest.starts_with(':')) {
                return true;
            }
            from = start + 1;
        }
    }
    false
}

fn available(root: &Path, release: &str, loaded: &[String]) -> Vec<String> {
    let mut wanted = known_modules();
    wanted.push(DRIVE_MODULE);
    let mut found: Vec<String> = wanted
        .iter()
        .filter(|name| {
            loaded.iter().any(|l| l == *name) || root.join("sys/module").join(name).is_dir()
        })
        .map(|name| name.to_string())
        .collect();
    for base in ["lib/modules", "usr/lib/modules"] {
        for listing in ["modules.dep", "modules.builtin"] {
            let listed = if release.is_empty() {
                None
            } else {
                text(&root.join(base).join(release).join(listing))
            };
            let Some(listed) = listed else { continue };
            for name in &wanted {
                if !found.iter().any(|f| f == name) && lists_module(&listed, name) {
                    found.push(name.to_string());
                }
            }
        }
    }
    found
}

fn recorded(root: &Path) -> Vec<String> {
    let mut names = Vec::new();
    let sysconfig = text(&root.join("etc/sysconfig/lm_sensors")).unwrap_or_default();
    for line in sysconfig.lines() {
        let line = line.trim_start();
        if let Some(rest) = line.strip_prefix("HWMON_MODULES=\"") {
            if let Some(end) = rest.find('"') {
                names.extend(module_names(&rest[..end]));
            }
        }
    }
    let modules = text(&root.join("etc/modules")).unwrap_or_default();
    if let Some((_, after)) = modules.split_once("# Chip drivers") {
        names.extend(module_names(&after.lines().collect::<Vec<_>>().join("\n")));
    }
    for line in text(&root.join("etc/modules-load.d/lm_sensors.conf"))
        .unwrap_or_default()
        .lines()
    {
        names.extend(module_names(line.split('#').next().unwrap_or("")));
    }
    let mut found = Vec::new();
    for name in names {
        if RECORDED_DRIVERS.contains(&name.as_str()) && !found.contains(&name) {
            found.push(name);
        }
    }
    found
}

fn may_use(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let Ok(name) = std::ffi::CString::new(path.as_os_str().as_bytes()) else {
            return false;
        };
        // SAFETY: access asks about a path given as a C string.
        unsafe { libc::access(name.as_ptr(), libc::R_OK | libc::W_OK) == 0 }
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        true
    }
}

fn panel(root: &Path) -> (bool, bool) {
    let folder = root.join("sys/bus/usb/devices");
    let Ok(entries) = std::fs::read_dir(&folder) else {
        return (false, false);
    };
    let mut names: Vec<String> = entries
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    let (mut present, mut denied) = (false, false);
    for entry in names {
        let at = folder.join(&entry);
        let vendor = text(&at.join("idVendor"))
            .unwrap_or_default()
            .to_lowercase();
        let product = text(&at.join("idProduct"))
            .unwrap_or_default()
            .to_lowercase();
        let ours = USB_VENDORS.contains(&vendor.as_str())
            || USB_PRODUCTS.contains(&(vendor.as_str(), product.as_str()));
        if !ours {
            continue;
        }
        present = true;
        let (bus, number) = (text(&at.join("busnum")), text(&at.join("devnum")));
        if let (Some(bus), Some(number)) = (bus, number) {
            if let (Ok(bus), Ok(number)) = (bus.parse::<u32>(), number.parse::<u32>()) {
                let node = root.join(format!("dev/bus/usb/{bus:03}/{number:03}"));
                if node.exists() && !may_use(&node) {
                    denied = true;
                }
            }
        }
    }
    (present, denied)
}

fn rule_elsewhere(root: &Path) -> bool {
    let ours = "60-libre-panel.rules";
    for folder in RULE_FOLDERS {
        let place = root.join(folder.trim_start_matches('/'));
        let Ok(entries) = std::fs::read_dir(&place) else {
            continue;
        };
        let mut names: Vec<String> = entries
            .flatten()
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .collect();
        names.sort();
        for name in names {
            if !name.ends_with(".rules") || name == ours {
                continue;
            }
            let found = text(&place.join(&name)).unwrap_or_default();
            let low = found.to_lowercase();
            let covered = USB_VENDORS
                .iter()
                .all(|v| low.contains(&format!("idvendor}}==\"{v}\"")))
                && USB_PRODUCTS
                    .iter()
                    .all(|(_, p)| low.contains(&format!("idproduct}}==\"{p}\"")));
            if covered && (found.contains("uaccess") || found.contains("MODE")) {
                return true;
            }
        }
    }
    false
}

fn energy_files(root: &Path) -> Vec<PathBuf> {
    let folder = root.join(POWERCAP.trim_start_matches('/'));
    let Ok(entries) = std::fs::read_dir(&folder) else {
        return Vec::new();
    };
    let mut zones: Vec<String> = entries
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    zones.sort();
    zones
        .into_iter()
        .map(|zone| folder.join(zone).join("energy_uj"))
        .filter(|path| path.exists())
        .collect()
}

fn release() -> String {
    #[cfg(unix)]
    {
        // SAFETY: uname fills the struct; release is a C string in it.
        unsafe {
            let mut name: libc::utsname = std::mem::zeroed();
            if libc::uname(&mut name) == 0 {
                return std::ffi::CStr::from_ptr(name.release.as_ptr())
                    .to_string_lossy()
                    .into_owned();
            }
        }
    }
    String::new()
}

/// gather: look at this computer (`root`: where its files are; a test makes one).
pub fn gather(root: &Path) -> Facts {
    let mut dmi = BTreeMap::new();
    for name in [
        "board_vendor",
        "board_name",
        "sys_vendor",
        "product_name",
        "chassis_type",
    ] {
        if let Some(found) =
            text(&root.join("sys/class/dmi/id").join(name)).filter(|f| !f.is_empty())
        {
            dmi.insert(name.to_string(), found);
        }
    }
    let loaded: Vec<String> = text(&root.join("proc/modules"))
        .unwrap_or_default()
        .lines()
        .filter_map(|line| line.split_whitespace().next().map(str::to_string))
        .collect();
    let energy = energy_files(root);
    let closed = |path: &PathBuf| {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::metadata(path).is_ok_and(|m| m.permissions().mode() & 0o004 == 0)
        }
        #[cfg(not(unix))]
        {
            let _ = path;
            false
        }
    };
    let sata = std::fs::read_dir(root.join("sys/block")).is_ok_and(|entries| {
        entries.flatten().any(|e| {
            let disk = e.file_name().to_string_lossy().into_owned();
            disk.starts_with("sd")
                && text(&root.join("sys/block").join(&disk).join("device/vendor")).as_deref()
                    == Some("ATA")
        })
    });
    let (_present, denied) = panel(root);
    let files = OWN_FILES
        .iter()
        .map(|path| {
            (
                path.to_string(),
                text(&root.join(path.trim_start_matches('/'))),
            )
        })
        .collect();
    let tools = ["udevadm", "modprobe", "chmod"]
        .iter()
        .filter_map(|name| which_system(name).map(|found| (name.to_string(), found)))
        .collect();
    Facts {
        dmi,
        available: available(root, &release(), &loaded),
        loaded,
        hwmon: lp_sensors::hwmon::scan(root)
            .into_iter()
            .map(|chip| chip.name)
            .collect(),
        recorded: recorded(root),
        disks: sata,
        rapl: energy.iter().map(|p| p.display().to_string()).collect(),
        rapl_closed: energy
            .iter()
            .filter(|p| closed(p))
            .map(|p| p.display().to_string())
            .collect(),
        panel_denied: denied,
        rule_elsewhere: rule_elsewhere(root),
        files,
        tools,
    }
}

// -- what the app and the command line tell -----------------------------------------------------

/// A thing the setup could bring: `usb`, `fans`, `drives` or `power`.
pub struct Offer {
    pub id: &'static str,
    pub needed: bool,
    pub modules: Vec<String>,
    pub opt_in: bool,
}

pub fn offers(facts: &Facts) -> Vec<Offer> {
    let steps = plan(
        facts,
        Options {
            rapl: Some(true),
            remove: false,
        },
    );
    let step = |id: &str| steps.iter().find(|s| s.id == id);
    let mut found = Vec::new();
    if step("udev").is_some() {
        found.push(Offer {
            id: "usb",
            needed: facts.panel_denied,
            modules: Vec::new(),
            opt_in: false,
        });
    }
    if let Some(s) = step("modules") {
        found.push(Offer {
            id: "fans",
            needed: true,
            modules: s.modules.clone(),
            opt_in: false,
        });
    }
    if let Some(s) = step("drives") {
        found.push(Offer {
            id: "drives",
            needed: false,
            modules: s.modules.clone(),
            opt_in: false,
        });
    }
    if step("rapl").is_some() && !facts.rapl_closed.is_empty() {
        found.push(Offer {
            id: "power",
            needed: true,
            modules: Vec::new(),
            opt_in: true,
        });
    }
    found
}

/// hint: one sentence for a person who sees readings missing, saying what is the matter and
/// the command that mends it; empty when the setup has nothing that is needed.
pub fn hint(facts: &Facts, command: &str) -> String {
    let needed: Vec<&str> = offers(facts)
        .into_iter()
        .filter(|offer| offer.needed)
        .map(|offer| offer.id)
        .collect();
    if needed.is_empty() {
        return String::new();
    }
    let mut missing = Vec::new();
    if needed.contains(&"usb") {
        missing.push(t("Your user may not use the USB panel yet."));
    }
    if needed.contains(&"fans") {
        missing.push(t(
            "The driver for the fan and temperature chip of the mainboard is not loaded.",
        ));
    }
    if needed.contains(&"power") {
        missing.push(t(
            "The processor's power needs permission to read its energy counters.",
        ));
    }
    format!(
        "{} {}",
        missing.join(" "),
        tf("This mends it: {command}", &[("command", command)])
    )
}

fn t(text: &str) -> String {
    lp_i18n::t(text)
}

fn tf(text: &str, values: &[(&str, &str)]) -> String {
    let values: Vec<(&str, Value)> = values.iter().map(|(k, v)| (*k, Value::from(*v))).collect();
    lp_i18n::t_format(text, &values).unwrap_or_else(|why| why.msg)
}

/// describe: what a step does, in a sentence, for the person who is about to allow it.
pub fn describe(step: &Step) -> String {
    let removing = step
        .actions
        .iter()
        .any(|a| matches!(a, Action::Remove { .. }));
    let names = step.modules.join(", ");
    match (step.id, removing) {
        ("udev", true) => tf(
            "Remove the rule that lets your user use the USB panels: {path}",
            &[("path", UDEV_RULES)],
        ),
        ("udev", false) => tf(
            "Install the rule that lets your user use the USB panels: {path}",
            &[("path", UDEV_RULES)],
        ),
        ("modules", true) => tf(
            "Remove the list of drivers Libre Panel loads: {path} (they stay loaded until the next start)",
            &[("path", MODULES)],
        ),
        ("modules", false) => tf(
            "Load the driver for the fan and temperature chip of the mainboard ({names}, the first that finds its chip) and keep it for every start: {path}",
            &[("names", &names), ("path", MODULES)],
        ),
        ("drives", _) => tf(
            "Load the driver for the temperatures of SATA drives ({names}) and keep it for every start: {path}",
            &[("names", &names), ("path", MODULES)],
        ),
        ("rapl", true) => tf(
            "Close the processor's energy counters again and remove {path}",
            &[("path", RAPL_RULES)],
        ),
        ("rapl", false) => tf(
            "Let every user read the processor's energy counters, which the kernel keeps to root since 2020 because they leak what the processor computes: {path}",
            &[("path", RAPL_RULES)],
        ),
        _ => t("Make udev read its rules again"),
    }
}

fn driver_answer(answer: &str) -> String {
    t(match answer {
        LOADED => "loaded and kept for every start",
        ABSENT => "no such chip on this board",
        BUSY => "the firmware keeps the chip for itself; Libre Panel does not force it",
        MISSING => "not part of this kernel",
        _ => "did not load",
    })
}

pub fn report_lines(steps: &[Step], results: &[Outcome]) -> Vec<String> {
    let mut lines = Vec::new();
    for result in results {
        let Some(step) = steps.iter().find(|s| s.id == result.id) else {
            continue;
        };
        let mark = if result.status == "done" { "- " } else { "! " };
        lines.push(format!("{mark}{}", describe(step)));
        if result.status != "done" {
            lines.push(format!("    {}", result.detail));
        }
        for (name, answer) in &result.items {
            if !name.contains('/') {
                lines.push(format!("    {name}: {}", driver_answer(answer)));
            }
        }
    }
    lines
}

pub fn report(results: &[Outcome]) -> Dict {
    let mut told = Dict::new();
    told.insert(
        "ok".into(),
        Value::Bool(results.iter().all(|r| r.status == "done")),
    );
    let listed = results
        .iter()
        .map(|r| {
            let mut each = Dict::new();
            each.insert("id".into(), Value::from(r.id));
            each.insert("status".into(), Value::from(r.status));
            let mut items = Dict::new();
            for (k, v) in &r.items {
                items.insert(k.clone(), Value::Str(v.clone()));
            }
            each.insert("items".into(), Value::Dict(items));
            each.insert("detail".into(), Value::Str(r.detail.clone()));
            Value::Dict(each)
        })
        .collect();
    told.insert("results".into(), Value::List(listed));
    told
}

// -- the rights (linux_rights.py) ---------------------------------------------------------------

/// arguments: the command line of the setup for these options.
pub fn arguments(options: Options, json_output: bool, dry_run: bool) -> Vec<String> {
    let mut found = vec!["system-setup".to_string(), "--linux".to_string()];
    match options.rapl {
        Some(true) => found.push("--rapl".into()),
        Some(false) => found.push("--no-rapl".into()),
        None => {}
    }
    if options.remove {
        found.push("--remove".into());
    }
    if json_output {
        found.push("--json".into());
    }
    if dry_run {
        found.push("--dry-run".into());
    }
    found
}

/// program: the command that starts this Libre Panel, as root can run it: the AppImage file,
/// else the program itself.
pub fn program() -> Option<Vec<String>> {
    if let Ok(appimage) = std::env::var("APPIMAGE") {
        if !appimage.is_empty() {
            let path = Path::new(&appimage);
            return (path.is_absolute() && path.is_file()).then(|| vec![appimage.clone()]);
        }
    }
    let program = std::env::current_exe().ok()?;
    program
        .is_absolute()
        .then(|| vec![program.to_string_lossy().into_owned()])
}

/// terminal_command: what a person types in a terminal for the same: `sudo` and the command.
pub fn terminal_command(options: Options, command: Option<&[String]>) -> String {
    let mut parts = vec!["sudo".to_string()];
    match command {
        None => parts.push("libre-panel".into()),
        Some(command) => parts.extend(command.iter().cloned()),
    }
    parts.extend(arguments(options, false, false));
    shlex_join(&parts)
}

/// How the setup is started with the rights it needs.
#[derive(Clone)]
pub struct Elevation {
    pub method: &'static str,
    pub argv: Vec<String>,
    pub terminal: String,
}

fn which(name: &str) -> Option<String> {
    crate::capture::which(name).map(|p| p.to_string_lossy().into_owned())
}

/// elevation: how `command` is run with the rights: pkexec where there is a desktop to show
/// polkit's dialog, in a terminal also sudo.
pub fn elevation(
    options: Options,
    command: Option<&[String]>,
    terminal: bool,
    json_output: bool,
) -> Elevation {
    let text = terminal_command(options, command);
    let Some(command) = command else {
        return Elevation {
            method: "",
            argv: Vec::new(),
            terminal: text,
        };
    };
    let mut tail = command.to_vec();
    tail.extend(arguments(options, json_output, false));
    let set = |name: &str| std::env::var_os(name).is_some_and(|v| !v.is_empty());
    let graphical = set("DISPLAY") || set("WAYLAND_DISPLAY");
    let pkexec = which("pkexec");
    if let (Some(pkexec), true) = (&pkexec, graphical) {
        let mut argv = vec![pkexec.clone(), "--disable-internal-agent".into()];
        argv.extend(tail);
        return Elevation {
            method: "pkexec",
            argv,
            terminal: text,
        };
    }
    if terminal {
        if let Some(sudo) = which("sudo") {
            let mut argv = vec![sudo];
            argv.extend(tail);
            return Elevation {
                method: "sudo",
                argv,
                terminal: text,
            };
        }
        if let Some(pkexec) = pkexec {
            let mut argv = vec![pkexec];
            argv.extend(tail);
            return Elevation {
                method: "pkexec",
                argv,
                terminal: text,
            };
        }
    }
    Elevation {
        method: "",
        argv: Vec::new(),
        terminal: text,
    }
}

const STRIPPED: [&str; 5] = [
    "LD_PRELOAD",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "_MEIPASS2",
];
const APP_MARKS: [&str; 6] = ["_PYI_", "PYI_", "APPDIR", "APPIMAGE", "ARGV0", "OWD"];

/// clean_environment: the environment pkexec is started in: none of what the bundle put
/// there for its own libraries.
pub fn clean_environment() -> Vec<(String, String)> {
    let mut env: Vec<(String, String)> = std::env::vars()
        .filter(|(key, _)| {
            !STRIPPED.contains(&key.as_str())
                && !APP_MARKS.iter().any(|mark| key.starts_with(mark))
                && key != "LD_LIBRARY_PATH"
        })
        .collect();
    if let Some(at) = env.iter().position(|(k, _)| k == "LD_LIBRARY_PATH_ORIG") {
        let (_, original) = env.remove(at);
        if !original.is_empty() {
            env.push(("LD_LIBRARY_PATH".into(), original));
        }
    }
    env
}

/// run_elevated: the command (pkexec or sudo with the setup): (exit code, stdout, stderr).
fn run_elevated(argv: &[String], timeout: f64) -> (i32, String, String) {
    let child = std::process::Command::new(&argv[0])
        .args(&argv[1..])
        .env_clear()
        .envs(clean_environment())
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn();
    let mut child = match child {
        Ok(child) => child,
        Err(why) if why.kind() == std::io::ErrorKind::NotFound => {
            return (127, String::new(), why.to_string())
        }
        Err(why) => return (1, String::new(), why.to_string()),
    };
    let (mut out, mut err) = (child.stdout.take(), child.stderr.take());
    let reading = std::thread::spawn(move || {
        let mut text = Vec::new();
        if let Some(out) = out.as_mut() {
            let _ = std::io::Read::read_to_end(out, &mut text);
        }
        text
    });
    let errors = std::thread::spawn(move || {
        let mut text = Vec::new();
        if let Some(err) = err.as_mut() {
            let _ = std::io::Read::read_to_end(err, &mut text);
        }
        text
    });
    let end = std::time::Instant::now() + std::time::Duration::from_secs_f64(timeout);
    let code = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status.code().unwrap_or(1),
            Ok(None) if std::time::Instant::now() < end => {
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return (126, String::new(), "nobody answered the dialog".into());
            }
        }
    };
    let out = String::from_utf8_lossy(&reading.join().unwrap_or_default()).into_owned();
    let err = String::from_utf8_lossy(&errors.join().unwrap_or_default()).into_owned();
    (code, out, err)
}

/// parse_result: the report the setup prints with `--json`: the last line that is one.
pub fn parse_result(stdout: &str) -> Option<Dict> {
    for line in stdout.trim().lines().rev() {
        if line.starts_with('{') {
            if let Ok(Value::Dict(found)) = lp_py::json::loads(line) {
                return Some(found);
            }
        }
    }
    None
}

struct JobState {
    state: &'static str,
    action: String,
    code: Option<i32>,
    result: Option<Dict>,
    detail: String,
    method: &'static str,
}

static JOB: Mutex<JobState> = Mutex::new(JobState {
    state: "idle",
    action: String::new(),
    code: None,
    result: None,
    detail: String::new(),
    method: "",
});

fn job() -> std::sync::MutexGuard<'static, JobState> {
    JOB.lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// Job.start: begin; false if a run is going on already or there is no way to ask.
pub fn start(way: &Elevation, action: &str) -> bool {
    {
        let mut kept = job();
        if kept.state == "running" || way.argv.is_empty() {
            return false;
        }
        kept.state = "running";
        kept.result = None;
        kept.detail = String::new();
        kept.code = None;
        kept.action = action.to_string();
        kept.method = way.method;
    }
    let argv = way.argv.clone();
    let work = move || {
        let (code, out, err) = run_elevated(&argv, 900.0);
        let report = parse_result(&out);
        let mut kept = job();
        kept.code = Some(code);
        kept.state = match &report {
            Some(found) if found.get("ok").is_some_and(Value::truthy) => "done",
            Some(_) => "failed",
            None if code == 126 => "cancelled",
            None if code == 127 => "unavailable",
            None => "failed",
        };
        kept.result = report;
        kept.detail = if matches!(kept.state, "failed" | "unavailable") {
            let trimmed = err.trim();
            let start = trimmed
                .char_indices()
                .rev()
                .nth(1999)
                .map_or(0, |(at, _)| at);
            trimmed[start..].to_string()
        } else {
            String::new()
        };
        crate::logs::log(
            crate::logs::Level::Info,
            "libre_panel.linux_rights",
            &format!("linux setup: {} (exit {code})", kept.state),
        );
    };
    if std::thread::Builder::new()
        .name("linux-setup".into())
        .spawn(work)
        .is_err()
    {
        job().state = "failed";
        return false;
    }
    true
}

fn snapshot() -> Value {
    let kept = job();
    let failing = matches!(kept.state, "failed" | "unavailable");
    let mut told = Dict::new();
    told.insert("action".into(), Value::Str(kept.action.clone()));
    told.insert("running".into(), Value::Bool(kept.state == "running"));
    told.insert(
        "code".into(),
        kept.code.map_or(Value::None, |c| Value::int(i64::from(c))),
    );
    told.insert("cancelled".into(), Value::Bool(kept.state == "cancelled"));
    told.insert(
        "error".into(),
        Value::Str(if failing {
            kept.detail.clone()
        } else {
            String::new()
        }),
    );
    told.insert("state".into(), Value::from(kept.state));
    told.insert(
        "result".into(),
        kept.result.clone().map_or(Value::None, Value::Dict),
    );
    told.insert("detail".into(), Value::Str(kept.detail.clone()));
    told.insert("method".into(), Value::from(kept.method));
    Value::Dict(told)
}

/// status: what the app shows about the setup.
pub fn status(facts: &Facts, with_job: bool) -> Dict {
    let command = program();
    let way = elevation(Options::default(), command.as_deref(), false, true);
    let mut told = Dict::new();
    told.insert("platform".into(), Value::from("linux"));
    told.insert("available".into(), Value::Bool(true));
    let listed = offers(facts)
        .into_iter()
        .map(|offer| {
            let mut each = Dict::new();
            each.insert("id".into(), Value::from(offer.id));
            each.insert("needed".into(), Value::Bool(offer.needed));
            each.insert(
                "modules".into(),
                Value::List(offer.modules.into_iter().map(Value::Str).collect()),
            );
            each.insert("opt_in".into(), Value::Bool(offer.opt_in));
            Value::Dict(each)
        })
        .collect();
    told.insert("offers".into(), Value::List(listed));
    told.insert(
        "installed".into(),
        Value::List(
            OWN_FILES
                .iter()
                .filter(|path| facts.file(path).is_some())
                .map(|path| Value::from(*path))
                .collect(),
        ),
    );
    told.insert(
        "granted".into(),
        Value::Bool(facts.file(RAPL_RULES).is_some()),
    );
    told.insert("method".into(), Value::from(way.method));
    told.insert("command".into(), Value::Str(way.terminal));
    told.insert(
        "removal".into(),
        Value::Str(terminal_command(
            Options {
                rapl: None,
                remove: true,
            },
            command.as_deref(),
        )),
    );
    if with_job {
        told.insert("job".into(), snapshot());
    }
    told
}

/// Where the computer's files are: `/`, or the folder the tests make one in (LP_SETUP_ROOT,
/// as linux_setup.gather's `root`).
pub fn root() -> PathBuf {
    std::env::var_os("LP_SETUP_ROOT").map_or_else(|| PathBuf::from("/"), PathBuf::from)
}

/// The options of `system-setup --linux`, as given.
#[derive(Default)]
pub struct CliOptions {
    pub rapl: bool,
    pub no_rapl: bool,
    pub remove: bool,
    pub status: bool,
    pub dry_run: bool,
    pub json: bool,
}

fn is_root() -> bool {
    #[cfg(unix)]
    {
        // SAFETY: geteuid reads a number.
        unsafe { libc::geteuid() == 0 }
    }
    #[cfg(not(unix))]
    {
        false
    }
}

/// run_cli: `libre-panel system-setup --linux`: say what it will do, ask for the rights, do
/// it, say what became of each step.
pub fn run_cli(args: &CliOptions) -> u8 {
    if !cfg!(target_os = "linux") {
        eprintln!("{}", t("This setup is for Linux."));
        return 2;
    }
    let how = lp_py::json::Dumps {
        indent: Some("  ".into()),
        ..lp_py::json::Dumps::default()
    };
    if args.status {
        let found = status(&gather(&root()), false);
        println!("{}", lp_py::json::dumps(&Value::Dict(found), &how));
        return 0;
    }
    let options = Options {
        rapl: if args.rapl {
            Some(true)
        } else if args.no_rapl {
            Some(false)
        } else {
            None
        },
        remove: args.remove,
    };
    // with --json standard output holds the report, only
    let say = |line: &str| {
        if args.json {
            eprintln!("{line}");
        } else {
            println!("{line}");
        }
    };
    let compact = lp_py::json::Dumps::default();
    let facts = gather(&root());
    let steps = plan(&facts, options);
    if steps.is_empty() {
        say(&t("Nothing to do: this computer is set up as asked."));
        if args.json {
            println!(
                "{}",
                lp_py::json::dumps(&Value::Dict(report(&[])), &compact)
            );
        }
        return 0;
    }
    let root = is_root();
    if args.dry_run || !root {
        for step in &steps {
            say(&format!("- {}", describe(step)));
        }
    }
    if args.dry_run {
        return 0;
    }
    if !root {
        let way = elevation(options, program().as_deref(), true, args.json);
        if way.argv.is_empty() {
            eprintln!(
                "{}",
                t("Administrator rights are needed. Run this in a terminal:")
            );
            eprintln!("{}", way.terminal);
            return 1;
        }
        say(&tf(
            "Asking for administrator rights with {method}.",
            &[("method", way.method)],
        ));
        let done = std::process::Command::new(&way.argv[0])
            .args(&way.argv[1..])
            .env_clear()
            .envs(clean_environment())
            .status();
        return match done {
            Ok(status) => u8::try_from(status.code().unwrap_or(1)).unwrap_or(1),
            Err(why) => {
                eprintln!("{}: {why}", way.argv[0]);
                1
            }
        };
    }
    let log_line = |line: &str| {
        crate::logs::log(crate::logs::Level::Info, "libre_panel.linux_setup", line);
    };
    let results = apply(&steps, &Real, &log_line);
    for line in report_lines(&steps, &results) {
        say(&line);
    }
    let summary = report(&results);
    let ok = summary.get("ok").is_some_and(Value::truthy);
    if args.json {
        println!("{}", lp_py::json::dumps(&Value::Dict(summary), &compact));
    }
    if ok {
        0
    } else {
        1
    }
}
