//! libre-panel: Libre Panel as one program of Rust (docs/RUST.md, R5 and R9). It reads
//! config.toml, draws the theme's frames of the computer's readings and shows them on the
//! panel, or writes them to a PNG file.

mod accent;
mod access;
mod ai;
mod arrangement;
mod autostart;
mod background;
mod backup;
mod beside;
mod capture;
mod client;
mod colour_show;
mod colours;
mod community;
mod company;
mod consoles;
mod display;
mod distribute;
mod doctor;
mod drawings;
mod editing;
mod experiment;
mod extend;
mod fan_names;
mod hardware;
pub mod heap;
mod history;
mod home;
mod hotkeys;
mod http;
mod identify;
mod install;
mod instance;
mod linux_setup;
mod load;
#[cfg(target_os = "linux")]
mod logind;
mod logs;
mod mainloop;
mod meter;
mod mirror;
mod modes;
mod news;
mod notify;
mod osinfo;
mod pages;
mod photos;
mod player;
mod plugins;
mod presence;
mod previews;
mod psutil;
mod sensors;
mod server;
mod services;
mod settings;
mod share;
mod spawn;
mod specs;
mod start_screen;
mod switch;
mod system;
mod system_setup;
mod transition_show;
mod tray;
mod updates;
mod wayland;
mod wayland_feed;
mod window;
mod winsession;
mod zip;

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::atomic::AtomicBool;

use home::Home;
use mainloop::{Asked, MainLoop};

/// set by Ctrl+C and by the system's request to end: the loop ends after its frame
static STOP: AtomicBool = AtomicBool::new(false);

const USAGE: &str = "\
Libre Panel

  libre-panel run [options]     show the theme on the panel until stopped (Ctrl+C); --once:
                                one frame, --driver NAME: this driver instead of config.toml's
  libre-panel start [options]   the same as a background app: with the keyboard shortcuts
                                of config.toml, the panel paused and resumed by them (the
                                command when none is given)
  libre-panel render [options]  draw one frame into a PNG file (--output, else frame.png)
  libre-panel plugins           the plugins installed, and whether they load
  libre-panel preview [THEME] [-o FILE] [--live]
                                a theme drawn into a PNG (demo readings, or this PC's)
  libre-panel editor [options]  the app's server alone (its address, with the token, is
                                printed)
  libre-panel tray [--background]  the background app, as the start at login runs it
  libre-panel window URL [--view VIEW]
                                the app's window (one at a time: an open one comes to the front)
  libre-panel autostart enable|disable|status [--elevated]
                                start Libre Panel when you log in (--elevated, Windows:
                                with the highest rights, through Task Scheduler)
  libre-panel sensors           the readings and their keys (--check-service: whether the
                                sensor service answers, on Windows; --check-lhm: whether
                                LibreHardwareMonitor loads with the download's libraries)
  libre-panel themes            the themes that are installed
  libre-panel models            the panels Libre Panel knows
  libre-panel ports             the serial ports and what is on them
  libre-panel doctor [--report FILE] [--no-questions] [--frames N]
                                check a connected panel and write a report
  libre-panel system-setup [--service] [--pawnio] [--perf-log-users] [--remove] [--status]
                                as administrator: what the sensors need (Windows), and on
                                Linux the panel's access, the fan chip's driver and, with
                                --rapl, the processor's power (--linux, --dry-run, --json)
  libre-panel config [path | init [--force]]
                                where config.toml is, and whether it is right (path: where
                                it belongs; init: a new one with the defaults)
  libre-panel quit [--timeout S]  quit the running app (and wait until it is gone)
  libre-panel notify TEXT [--title T] [--seconds S] [--icon NAME] [--level info|warning|error]
                                a notice on the panel of the running app
  libre-panel devices           the drivers, serial ports and USB panels there are
  libre-panel udev-rules        the Linux udev rule for the panels' access
  libre-panel location NAME     latitude and longitude for the weather
  libre-panel version

Options:
  --config FILE    another config.toml
  --theme NAME     this theme instead of config.toml's
  --demo           readings made up instead of the computer's
  --output FILE    frames into this PNG file instead of the panel
  --frames N       end after N frames
  --port N         the port of the app's server (8765; another if it is taken)
";

/// between the two readings of `sensors`
const SENSORS_SETTLE_SECONDS: f64 = 1.0;
/// how long `sensors` waits for a source's first readings
const READY_SECONDS: f64 = 20.0;

/// unlock_offered: whether "Unlock all sensors" is a thing here: a Windows download that
/// carries the sensor service (sensors\libre-panel-sensors.exe beside the program), and the
/// service not installed (or switched off).
/// `libre-panel preview [THEME] [-o FILE] [--live] [--config FILE]` (cli.py's _cmd_preview): a
/// theme, by its name or its folder, drawn once into a PNG, of the demo's readings or (--live)
/// this computer's.
fn preview_command(args: &[String]) -> Result<(), String> {
    let (mut theme, mut output, mut live, mut config) =
        (None, "preview.png".to_string(), false, None);
    let mut rest = args.iter();
    while let Some(arg) = rest.next() {
        let mut value = |name: &str| {
            rest.next()
                .cloned()
                .ok_or_else(|| format!("argument {name}: expected one argument"))
        };
        match arg.as_str() {
            "-o" | "--output" => output = value("-o/--output")?,
            "--live" => live = true,
            "--config" => config = Some(PathBuf::from(value("--config")?)),
            other if theme.is_none() && !other.starts_with('-') => theme = Some(other.to_string()),
            other => return Err(format!("unrecognized arguments: {other}")),
        }
    }
    lp_text::set_locale_from_environment();
    lp_text::fallback::set_language(Box::new(|| lp_i18n::language().code().to_string()));
    let home = Home::find()?;
    let file = config.unwrap_or_else(|| home.config_file());
    let config = lp_config::load_config(&file, lp_config::platform()).map_err(|e| e.msg)?;
    let theme = theme.unwrap_or_else(|| "libre-default".into());
    let folder = if std::path::Path::new(&theme).is_dir() {
        PathBuf::from(&theme)
    } else {
        lp_theme::versions::find_theme(&theme, &home.places()).map_err(|e| e.msg)?
    };
    let env = lp_theme::Env {
        fonts_dir: home.fonts(),
    };
    let theme = lp_theme::load_theme(&folder, &env).map_err(|e| e.msg)?;
    let options = lp_render::Options {
        preview: true,
        animate: false,
        dither: false,
    };
    let mut renderer = lp_render::Renderer::new(theme.clone(), &env, options).map_err(|e| e.0)?;
    plugins::attach(&mut renderer);
    renderer.symbols = config.symbols.clone();
    let snapshot = if live {
        let (mut hub, _missing) = sensors::Hub::of(&config, false);
        hub.want(0, renderer.reads());
        // (rates, of the network and the drives, need two samples)
        hub.snapshot(mainloop::moment().0, false);
        hub.wait_ready(READY_SECONDS);
        let (now, fold) = mainloop::moment();
        let snapshot = hub.snapshot(now, fold);
        drop(hub);
        snapshot
    } else {
        let since = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH);
        let utc = lp_py::zones::Zone::utc();
        let zone = lp_py::zones::local().unwrap_or(&utc);
        let mut snapshot = lp_services::demo::demo_snapshot(
            since.map_or(0.0, |since| since.as_secs_f64()),
            lp_services::demo::HISTORY_SAMPLES,
            zone,
        )?;
        snapshot.images = lp_services::demo::pictures(&env.fonts_dir)?;
        snapshot
    };
    let drawn = renderer.render(snapshot, lp_render::video::monotonic());
    let warnings = renderer.warnings();
    renderer.close();
    let (frame, _boxes) = drawn.map_err(|why| format!("not drawn here yet: {}", why.0))?;
    let data = lp_devices::png::encode_with(&frame, lp_devices::png::Compression::new(6))?;
    std::fs::write(&output, data).map_err(|why| format!("{output}: {why}"))?;
    for warning in theme.warnings.iter().chain(&warnings) {
        eprintln!("warning: {warning}");
    }
    println!("wrote {output} ({}x{})", theme.width, theme.height);
    Ok(())
}

/// _linux_hint: what is missing on Linux that `system-setup --linux` brings, in a sentence
/// and with the command that does it; none when nothing is.
fn linux_hint() -> Option<String> {
    let program = linux_setup::program();
    let command =
        linux_setup::terminal_command(linux_setup::Options::default(), program.as_deref());
    let said = linux_setup::hint(&linux_setup::gather(&linux_setup::root()), &command);
    (!said.is_empty()).then_some(said)
}

pub fn unlock_offered() -> bool {
    if !cfg!(windows) {
        return false;
    }
    let bundled = std::env::current_exe().ok().is_some_and(|exe| {
        exe.with_file_name("sensors")
            .join("libre-panel-sensors.exe")
            .is_file()
    });
    bundled && !(lp_sensors::provider::System::own().installed)()
}

/// `sensors --check-service`: does the sensor service answer this account with readings?
/// `sensors --check-lhm` (cli.py's _check_lhm): whether LibreHardwareMonitor loads and opens
/// with the libraries the download carries, as Libre Panel reads them, through the sensor
/// service's own program (its `--check`): no administrator rights needed, and the
/// processor's sensors being out of reach is no failure.
fn check_lhm() -> Result<(), String> {
    if !cfg!(windows) {
        return Err("LibreHardwareMonitor is for Windows only.".into());
    }
    let folder = system_setup::bundled_service()
        .ok_or("this download carries no sensor service (sensors\\libre-panel-sensors.exe)")?;
    let program = folder.join(system_setup::SERVICE_EXE);
    let status = std::process::Command::new(&program)
        .arg("--check")
        .status()
        .map_err(|why| format!("{}: {why}", program.display()))?;
    match status.success() {
        true => Ok(()),
        false => Err(String::new()),
    }
}

fn check_service() -> Result<(), String> {
    if !cfg!(windows) {
        return Err("The sensor service is for Windows only.".into());
    }
    let system = lp_sensors::provider::System::own();
    let open = system.open.clone();
    let mut open: lp_sensors::service::Open = Box::new(move || open());
    let sleep = |seconds: f64| std::thread::sleep(std::time::Duration::from_secs_f64(seconds));
    let (worked, lines) = lp_sensors::service::check(&mut open, 15.0, &sleep);
    if worked {
        println!("{}", lines.join("\n"));
        Ok(())
    } else {
        Err(lines.join("\n"))
    }
}

#[cfg(unix)]
fn follow_signals() {
    extern "C" fn stop(_signal: libc::c_int) {
        STOP.store(true, std::sync::atomic::Ordering::Relaxed);
    }
    let handler = stop as extern "C" fn(libc::c_int) as libc::sighandler_t;
    unsafe {
        libc::signal(libc::SIGINT, handler);
        libc::signal(libc::SIGTERM, handler);
    }
}

#[cfg(windows)]
fn follow_signals() {
    use windows_sys::Win32::System::Console::SetConsoleCtrlHandler;
    // (Ctrl+C, Ctrl+Break, the console's window closed, the session ending)
    unsafe extern "system" fn stop(_event: u32) -> windows_sys::core::BOOL {
        STOP.store(true, std::sync::atomic::Ordering::Relaxed);
        1
    }
    unsafe {
        SetConsoleCtrlHandler(Some(stop), 1);
    }
}

#[cfg(not(any(unix, windows)))]
fn follow_signals() {}

/// The options after the command; what is none of them is an error.
fn asked(args: &[String]) -> Result<Asked, String> {
    let mut asked = Asked::default();
    let mut rest = args.iter();
    while let Some(option) = rest.next() {
        let mut value = |name: &str| {
            rest.next()
                .cloned()
                .ok_or_else(|| format!("{name} needs a value"))
        };
        match option.as_str() {
            "--config" => asked.config = Some(PathBuf::from(value("--config")?)),
            "--theme" => asked.theme = Some(value("--theme")?),
            "--output" => asked.output = Some(PathBuf::from(value("--output")?)),
            "--frames" => {
                let frames = value("--frames")?;
                asked.frames = Some(
                    frames
                        .parse()
                        .map_err(|_| format!("--frames: {frames:?} is no number"))?,
                );
            }
            "--port" => {
                let port = value("--port")?;
                asked.port = Some(
                    port.parse()
                        .map_err(|_| format!("--port: {port:?} is no port"))?,
                );
            }
            "--demo" => asked.demo = true,
            "--once" => asked.frames = Some(1), // (cli.py's `run --once`)
            "--driver" => asked.driver = Some(value("--driver")?),
            other => return Err(format!("unknown option {other}")),
        }
    }
    Ok(asked)
}

fn run(command: &str, args: &[String]) -> Result<(), String> {
    match command {
        "version" | "--version" | "-V" => println!("libre-panel {}", lp_py::VERSION),
        "help" | "--help" | "-h" => print!("{USAGE}"),
        "models" => {
            // (_cmd_models' table: the sizes, how far the driver is, the protocol)
            println!(
                "{:<18} {:<42} {:>10} {:>10}  {:<12} protocol",
                "model", "panel", "landscape", "portrait", "driver"
            );
            let majesty = std::iter::once(&lp_devices::hongtai::MAJESTY);
            for model in lp_devices::models::MODELS.iter().chain(majesty) {
                let (lw, lh) = model.size("landscape");
                let (pw, ph) = model.size("portrait");
                let label = model.label();
                let named = label.split_once(' ').map_or("", |(_, rest)| rest);
                let protocol = (lp_devices::models::PROTOCOLS.iter())
                    .find(|(id, _)| *id == model.protocol)
                    .map_or(model.protocol, |(_, name)| name);
                println!(
                    "{:<18} {:<42} {:>10} {:>10}  {:<12} {protocol}",
                    model.id,
                    format!("{} {named}", model.vendor),
                    format!("{lw}x{lh}"),
                    format!("{pw}x{ph}"),
                    model.driver,
                );
            }
        }
        "ports" => {
            for port in lp_devices::serial::ports() {
                let ids = match port.vid.zip(port.pid) {
                    Some((vid, pid)) => format!("{vid:04x}:{pid:04x}"),
                    None => "-".to_string(),
                };
                let models = port.vid.zip(port.pid).map_or(Vec::new(), |(vid, pid)| {
                    lp_devices::models::models_for_usb(vid, pid, port.serial_number.as_deref())
                });
                let names: Vec<String> = models.iter().map(|m| m.label()).collect();
                println!("{:<16} {ids:<10} {}", port.device, names.join(", "));
            }
        }
        "themes" => {
            // (_cmd_themes: each once, with where it comes from and where it is)
            let home = Home::find()?;
            for theme in lp_theme::versions::list_themes(&home.places()) {
                let text = |key: &str| match theme.get(key) {
                    Some(lp_py::Value::Str(text)) => text.clone(),
                    _ => String::new(),
                };
                println!("{:<24} {:<9} {}", text("id"), text("source"), text("path"));
            }
        }
        "config" => {
            // (_cmd_config's actions: `path`, where it belongs, and `init [--force]`, a new
            // one with the defaults; with neither, where it is and what it says)
            let (action, args) = match args.split_first() {
                Some((first, rest)) if first == "path" || first == "init" => (first.as_str(), rest),
                _ => ("", args),
            };
            if action == "path" {
                println!(
                    "{}",
                    lp_config::dirs::config_dir().join("config.toml").display()
                );
                return Ok(());
            }
            let force = args.iter().any(|arg| arg == "--force");
            let rest: Vec<String> =
                (args.iter().filter(|arg| *arg != "--force").cloned()).collect();
            let asked = asked(&rest)?;
            let file = match asked.config {
                Some(file) => file,
                None => Home::find()?.config_file(),
            };
            if action == "init" {
                return match lp_config::edit::write_default_config(
                    &file,
                    force,
                    lp_config::platform(),
                ) {
                    Ok(()) => {
                        println!("wrote {}", file.display());
                        Ok(())
                    }
                    Err(why) if why.class == "FileExistsError" => Err(format!(
                        "{} already exists (use --force to overwrite)",
                        file.display()
                    )),
                    Err(why) => Err(why.msg),
                };
            }
            println!("{}", file.display());
            let config = lp_config::load_config(&file, lp_config::platform()).map_err(|e| e.msg)?;
            let theme = if config.theme_given {
                config.theme.as_str()
            } else {
                "(the panel's own)"
            };
            println!("theme: {theme}");
            println!("panel: {} ({})", config.device.model, config.device.driver);
            println!("sensors: {}", config.sensors.providers.join(", "));
        }
        "sensors" => {
            if let Some(at) = args.iter().position(|arg| arg == "--check-lhm") {
                let mut rest = args.to_vec();
                rest.remove(at);
                asked(&rest)?;
                return check_lhm();
            }
            if let Some(at) = args.iter().position(|arg| arg == "--check-service") {
                let mut rest = args.to_vec();
                rest.remove(at);
                asked(&rest)?;
                return check_service();
            }
            let asked = asked(args)?;
            let file = match &asked.config {
                Some(file) => file.clone(),
                None => Home::find()?.config_file(),
            };
            let config = lp_config::load_config(&file, lp_config::platform()).map_err(|e| e.msg)?;
            let (mut hub, _) = sensors::Hub::of(&config, asked.demo);
            // Some sources read only while a theme shows their keys: asked for as a theme
            // would, so they are listed ("lhm:*" is every sensor of LibreHardwareMonitor).
            let mut keys = ["cpu.load", "gpu.load", "audio.*", "lhm:*", "temp.*"]
                .map(String::from)
                .to_vec();
            // (the tests' own: more keys asked for, as a theme that shows them would)
            if let Ok(more) = std::env::var("LP_SENSORS_WANT") {
                keys.extend(more.split(',').map(str::to_string));
            }
            hub.want(0, keys);
            let moment = lp_render::Moment::date(1970, 1, 1);
            hub.snapshot(moment, false);
            // (rate counters and pollers need time)
            std::thread::sleep(std::time::Duration::from_secs_f64(SENSORS_SETTLE_SECONDS));
            hub.wait_ready(READY_SECONDS);
            let snapshot = hub.snapshot(moment, false);
            let mut keys: Vec<&String> = snapshot.readings.keys().collect();
            keys.sort_by(|a, b| a.chars().cmp(b.chars()));
            for key in keys {
                let r = &snapshot.readings[key];
                let value = match &r.value {
                    lp_py::Value::Float(f) => format!("{f:.2}"),
                    other => other.str(),
                };
                println!("{key:<40} {value:>14} {:<5} {}", r.unit, r.label);
            }
            for note in hub.notes() {
                eprintln!("libre-panel: {note}");
            }
            if !asked.demo {
                let url = match config.sensors.options.get("librehardwaremonitor") {
                    Some(table) => match table.get("url") {
                        Some(lp_py::toml::Toml::Str(url)) => url.clone(),
                        _ => lp_sensors::provider::DEFAULT_URL.to_string(),
                    },
                    None => lp_sensors::provider::DEFAULT_URL.to_string(),
                };
                let hint = lp_sensors::hints::hardware_hint(
                    &snapshot.readings,
                    &config.sensors.providers,
                    &url,
                    cfg!(windows),
                    hub.lhm_status().as_ref(),
                    unlock_offered(),
                )
                .or_else(|| cfg!(target_os = "linux").then(linux_hint).flatten());
                if let Some(hint) = hint {
                    eprintln!("\nnote: {hint}");
                }
            }
        }
        "run" | "render" => {
            let mut asked = asked(args)?;
            if command == "render" {
                asked.frames = Some(1);
                asked
                    .output
                    .get_or_insert_with(|| PathBuf::from("frame.png"));
            }
            // (as Python does when it starts: HarfBuzz's language of a text without one; the
            // fallback fonts of the East Asian languages go by the app's language)
            lp_text::set_locale_from_environment();
            lp_text::fallback::set_language(Box::new(|| lp_i18n::language().code().to_string()));
            follow_signals();
            let home = Home::find()?;
            // (one Libre Panel at a panel; a frame into a file takes none)
            let _lock = match command {
                "run" => Some(
                    instance::InstanceLock::acquire(&home.settings).map_err(|why| why.message())?,
                ),
                _ => None,
            };
            loop {
                let mut made = MainLoop::new(home.clone(), asked.clone())?;
                let ran = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    made.run(&STOP, &|| mainloop::Ending::Quit);
                }));
                match ran {
                    Ok(()) => match made.fatal.take() {
                        Some(why) => return Err(why),
                        None => break,
                    },
                    // (a bug in a frame: `run` starts its loop again, as the app in the
                    // background does; a single frame asked for has failed, and the bug's
                    // word is said already)
                    Err(_) if command == "run" => {
                        drop(made);
                        logs::say(
                            "libre_panel.app",
                            "the panel's loop crashed; starting again",
                        );
                        let until = std::time::Instant::now() + std::time::Duration::from_secs(5);
                        while !STOP.load(std::sync::atomic::Ordering::Relaxed)
                            && std::time::Instant::now() < until
                        {
                            std::thread::sleep(std::time::Duration::from_millis(100));
                        }
                        if STOP.load(std::sync::atomic::Ordering::Relaxed) {
                            break;
                        }
                    }
                    Err(_) => return Err(String::new()),
                }
            }
        }
        "preview" => preview_command(args)?,
        "plugins" => {
            let home = Home::find()?;
            if !plugins::list(&home.settings) {
                return Err(String::new());
            }
        }
        "system-setup" => std::process::exit(i32::from(system_setup_command(args)?)),
        "quit" => std::process::exit(quit_command(args)?),
        "notify" => std::process::exit(notify_command(args)?),
        "devices" => devices_command(),
        "udev-rules" => print!("{}", linux_setup::udev_rules_text()),
        "location" => std::process::exit(location_command(args)?),
        // (the app's own: its capture program on Wayland, wayland.rs)
        "wayland-feed" => std::process::exit(wayland_feed::run(args)),
        "doctor" => {
            // libre-panel doctor [--report FILE] [--no-questions] [--frames N] [--config FILE]
            let (mut report, mut questions, mut frames, mut config) = (None, true, 20, None);
            let mut rest = args.iter();
            while let Some(arg) = rest.next() {
                let mut value = |name: &str| {
                    rest.next()
                        .cloned()
                        .ok_or_else(|| format!("{name} needs a value"))
                };
                match arg.as_str() {
                    "--report" => report = Some(PathBuf::from(value("--report")?)),
                    "--no-questions" => questions = false,
                    "--frames" => {
                        frames = value("--frames")?
                            .parse()
                            .map_err(|_| "--frames: expected a whole number".to_string())?;
                    }
                    "--config" => config = Some(PathBuf::from(value("--config")?)),
                    other => return Err(format!("unrecognized argument: {other}")),
                }
            }
            let home = Home::find()?;
            let file = config.unwrap_or_else(|| home.config_file());
            // (one Libre Panel at a panel: the test has it alone)
            let _lock =
                instance::InstanceLock::acquire(&home.settings).map_err(|why| why.message())?;
            if doctor::run_doctor(report, questions, frames, &file)? {
                return Err(String::new());
            }
        }
        "editor" => {
            let asked = asked(args)?;
            follow_signals();
            let home = Home::find()?;
            let file = asked.config.clone().unwrap_or_else(|| home.config_file());
            // (the app's language as config.toml sets it; a file that cannot be read: the
            // system's)
            let setting = lp_config::load_config(&file, lp_config::platform())
                .map_or_else(|_| "auto".to_string(), |config| config.language);
            let code = lp_i18n::resolve(Some(&setting), lp_i18n::system_language_here);
            match lp_i18n::Language::load(&home.locale(), &code) {
                Ok(language) => lp_i18n::set_language(language),
                Err(why) => eprintln!("libre-panel: language {code}: {why}"),
            }
            server::serve_until(asked.port.unwrap_or(server::PORT), &home, &file, &STOP)?;
        }
        "autostart" => return autostart(args),
        "window" => {
            // `window <url> [--view VIEW]`: the app's window, in a process of its own
            let mut url = None;
            let mut view = None;
            let mut rest = args.iter();
            while let Some(arg) = rest.next() {
                match arg.as_str() {
                    "--view" => view = rest.next().cloned(),
                    "--config" => drop(rest.next()), // (the app's; the window keeps its own)
                    other if url.is_none() && !other.starts_with("--") => {
                        url = Some(other.to_string());
                    }
                    other => return Err(format!("window: unknown argument {other}")),
                }
            }
            let url = url.ok_or("window: the app's address")?;
            let view = view.filter(|view| window::VIEWS.contains(&view.as_str()));
            let code = window::run_program(&url, view.as_deref());
            std::process::exit(code);
        }
        "start" | "tray" => {
            let args_given = args;
            // (the start at login: `tray --background`, as the Python app wrote it too)
            let args: Vec<String> = args
                .iter()
                .filter(|arg| match command {
                    "tray" => !["--background", "--no-icon"].contains(&arg.as_str()),
                    _ => arg.as_str() != "--no-browser",
                })
                .cloned()
                .collect();
            // (the background app logs to a file: there is no terminal to read)
            if command == "tray" && !args_given.iter().any(|arg| arg == "--no-icon") {
                match logs::to_file() {
                    // (LP_PROFILE's lines into it too: a program with a window has no standard
                    // error to read)
                    Ok(_) => lp_render::prof::say_also(|line| {
                        logs::log(logs::Level::Info, "libre_panel.profile", line)
                    }),
                    Err(why) => eprintln!("libre-panel: the log: {why}"),
                }
            }
            let asked = asked(&args)?;
            lp_text::set_locale_from_environment();
            lp_text::fallback::set_language(Box::new(|| lp_i18n::language().code().to_string()));
            follow_signals();
            let home = Home::find()?;
            // (the app's window opens where the start asks for it: `start`, or `tray` without
            // --background)
            let open_editor = match command {
                "start" => !args_given.iter().any(|arg| arg == "--no-browser"),
                _ => !args_given.iter().any(|arg| arg == "--background"),
            };
            let _lock = match instance::InstanceLock::acquire(&home.settings) {
                Ok(lock) => lock,
                Err(instance::NotLocked::Running(why)) => {
                    // a second click while the first start is still on its way: its app is
                    // there in a moment
                    let wait = if open_editor {
                        instance::STARTING_S
                    } else {
                        0.0
                    };
                    let Some(url) = instance::editor_of_running_app(&home.settings, wait) else {
                        return Err(why);
                    };
                    let port = url
                        .rsplit(':')
                        .next()
                        .and_then(|rest| rest.trim_end_matches('/').parse::<u16>().ok());
                    let token = access::read_token(&home.settings, port);
                    let address = access::with_token(&url, token.as_deref());
                    println!(
                        "{}",
                        t_with(
                            "Libre Panel is already running. Theme editor: {url}",
                            &[("url", &address)]
                        )
                    );
                    if open_editor {
                        let token = token.as_deref();
                        window::open_window(&home.settings, &url, Some("home"), false, token);
                    }
                    return Ok(());
                }
                Err(why) => return Err(why.message()),
            };
            let settings = home.settings.clone();
            let app = background::BackgroundApp::new(home, asked.clone());
            // (Windows: stopped in order as the session ends; the panel sleeps with the
            // computer and dims while the person is away)
            let stopped = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
            let mut session = follow_session(&app, &stopped);
            let icon = command == "tray" && !args_given.iter().any(|arg| arg == "--no-icon");
            let started = app.start();
            if let Some(address) = &started {
                println!("Libre Panel theme editor running at {address}  (Ctrl+C to stop)");
                if open_editor {
                    window::open_window(&settings, address, Some("home"), false, None);
                }
            }
            // (the tray icon, a program of its own: it shows the app's menu, where config.toml
            // asks for it: tray_icon, off unless turned on)
            let mut tray = TrayFollower::new(started.filter(|_| icon));
            // (the tests' own: the controls by lines on the standard input)
            if std::env::var_os("LIBRE_PANEL_CONTROL").is_some_and(|how| how == "stdin") {
                app.follow_input();
            }
            let shown = |app: &background::BackgroundApp| {
                let frames = app
                    .panel
                    .state()
                    .get("frames")
                    .and_then(lp_py::Value::to_f64);
                asked
                    .frames
                    .is_some_and(|asked| frames.is_some_and(|f| f >= asked as f64))
            };
            let quit = |app: &background::BackgroundApp| {
                app.quit_requested
                    .load(std::sync::atomic::Ordering::Relaxed)
            };
            while !STOP.load(std::sync::atomic::Ordering::Relaxed) && !shown(&app) && !quit(&app) {
                tray.follow(app.config_file());
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            // (the icon goes first: its menu would act on an app that is ending)
            tray.close();
            app.shutdown();
            stopped.store(true, std::sync::atomic::Ordering::Relaxed);
            session.stop();
        }
        other => {
            // (a usage error ends with 2, as argparse's)
            eprintln!("libre-panel: unknown command {other}\n\n{USAGE}");
            std::process::exit(2);
        }
    }
    Ok(())
}

/// What hears the session: Windows' window, or logind on Linux.
enum Session {
    None,
    #[cfg(windows)]
    Windows(winsession::Watcher),
    #[cfg(target_os = "linux")]
    Linux(logind::Watcher),
}

impl Session {
    fn stop(&mut self) {
        match self {
            Session::None => {}
            #[cfg(windows)]
            Session::Windows(watcher) => watcher.stop(),
            #[cfg(target_os = "linux")]
            Session::Linux(watcher) => watcher.stop(),
        }
    }
}

/// _stop_with_windows: Windows' end of the session stops the app in order (Windows waits for
/// it a moment), and the panel sleeps with the computer and wakes with it (_sleep_and_wake);
/// on Linux logind tells (_follow_logind).
fn follow_session(
    app: &std::sync::Arc<background::BackgroundApp>,
    stopped: &std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> Session {
    let say = |message: &str| logs::log(logs::Level::Info, "libre_panel.tray", message);
    let asleep = std::sync::Arc::clone(app);
    let sleeps = Box::new(move || {
        if asleep.panel.sleep() {
            say("the computer went to sleep; the panel stopped until it wakes");
        }
    });
    let awake = std::sync::Arc::clone(app);
    let wakes = Box::new(move || {
        let quitting = awake
            .quit_requested
            .load(std::sync::atomic::Ordering::Relaxed);
        if !quitting && awake.panel.wake() {
            say("the computer woke; the panel starts again");
        }
    });
    #[cfg(windows)]
    {
        /// Windows asks the user about programs that take longer than about 5 s
        const SESSION_END_S: f64 = 4.5;
        let (ending, stopped) = (std::sync::Arc::clone(app), std::sync::Arc::clone(stopped));
        let on_end = Box::new(move || {
            say("Windows is ending the session; stopping the panel");
            ending.quit();
            let until =
                std::time::Instant::now() + std::time::Duration::from_secs_f64(SESSION_END_S);
            while !stopped.load(std::sync::atomic::Ordering::Relaxed)
                && std::time::Instant::now() < until
            {
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
        });
        winsession::shut_down_early();
        let handlers = winsession::Handlers {
            on_end,
            on_sleep: sleeps,
            on_wake: wakes,
        };
        match winsession::Watcher::start(handlers) {
            Ok(watcher) => Session::Windows(watcher),
            Err(why) => {
                logs::say(
                    "libre_panel.tray",
                    &format!("no clean stop at shutdown: {why}"),
                );
                Session::None
            }
        }
    }
    #[cfg(target_os = "linux")]
    {
        let _ = stopped;
        logind::follow(sleeps, wakes).map_or(Session::None, Session::Linux)
    }
    #[cfg(not(any(windows, target_os = "linux")))]
    {
        let _ = (stopped, sleeps, wakes);
        Session::None
    }
}

/// The tray icon as config.toml's `tray_icon` asks for it: started when it is turned on,
/// ended when it is turned off, looked at once a second (when the file changed). An icon
/// whose program ends by itself is started again: at once where it had its place (the
/// desktop's tray restarted under it), after a while and a few times where it ended at once
/// (the app was slow to answer it at the computer's start, the desktop's tray not there yet).
struct TrayFollower {
    /// the app's address (None: no icon in this run)
    address: Option<String>,
    child: Option<std::process::Child>,
    /// when the icon's program was started
    started: Option<std::time::Instant>,
    /// when the file was written, as last read
    stamp: Option<std::time::SystemTime>,
    next: std::time::Instant,
    /// how often running the icon's program ended at once (no tray on this desktop)
    ended_at_once: u32,
    /// when it is tried again after it ended at once
    again: Option<std::time::Instant>,
}

/// An icon's program that ends sooner than this after its start found no tray to sit in; one
/// that ends later had an icon (the desktop's tray restarted, Explorer with it): started again.
const TRAY_SETTLE_S: f64 = 10.0;
/// One that ended at once is tried again this long after, this many times in all; then not
/// before the icon is turned off and on.
const TRAY_AGAIN_S: f64 = 20.0;
const TRAY_TRIES: u32 = 4;

impl TrayFollower {
    fn new(address: Option<String>) -> TrayFollower {
        TrayFollower {
            address,
            child: None,
            started: None,
            stamp: None,
            next: std::time::Instant::now(),
            ended_at_once: 0,
            again: None,
        }
    }

    fn follow(&mut self, file: &std::path::Path) {
        let Some(address) = &self.address else {
            return;
        };
        let now = std::time::Instant::now();
        if now < self.next {
            return;
        }
        self.next = now + std::time::Duration::from_secs(1);
        if let Some(child) = &mut self.child {
            if let Ok(Some(status)) = child.try_wait() {
                self.child = None;
                let ran = self.started.map_or(0.0, |at| at.elapsed().as_secs_f64());
                let next = if ran >= TRAY_SETTLE_S {
                    // (it had its icon: started again at once, as the setting still asks)
                    self.ended_at_once = 0;
                    self.stamp = None;
                    ": started again".to_string()
                } else {
                    self.ended_at_once += 1;
                    match self.ended_at_once < TRAY_TRIES {
                        true => {
                            let wait = std::time::Duration::from_secs_f64(TRAY_AGAIN_S);
                            self.again = Some(now + wait);
                            format!(": tried again in {TRAY_AGAIN_S:.0} s")
                        }
                        false => ": not tried again until the icon is turned off and on".into(),
                    }
                };
                logs::say(
                    "libre_panel.tray",
                    &format!("the tray icon's program ended ({status}) after {ran:.0} s{next}"),
                );
            }
        }
        if self.again.is_some_and(|due| now >= due) {
            self.again = None;
            self.stamp = None; // (the setting read again: started again if it is still on)
        }
        let stamp = std::fs::metadata(file).and_then(|m| m.modified()).ok();
        if stamp == self.stamp && self.stamp.is_some() {
            return;
        }
        self.stamp = stamp;
        let wanted = lp_config::load_config(file, lp_config::platform())
            .is_ok_and(|config| config.tray_icon);
        // (one that ended at once waits for its next try, or was tried often enough)
        let held = self.again.is_some() || self.ended_at_once >= TRAY_TRIES;
        match (wanted, self.child.is_some()) {
            (true, false) if !held => {
                self.child = window::start_tray(address);
                self.started = Some(now);
            }
            (false, true) => self.close(),
            (false, false) => {
                // (asked again later: tried anew)
                self.ended_at_once = 0;
                self.again = None;
            }
            _ => {}
        }
    }

    fn close(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// The app's language as config.toml sets it ("auto", or a file that cannot be read: the
/// system's), for what the command line says.
fn speak(home: &Home, file: &std::path::Path) {
    let setting = lp_config::load_config(file, lp_config::platform())
        .map_or_else(|_| "auto".to_string(), |config| config.language);
    let code = lp_i18n::resolve(Some(&setting), lp_i18n::system_language_here);
    match lp_i18n::Language::load(&home.locale(), &code) {
        Ok(language) => lp_i18n::set_language(language),
        Err(why) => eprintln!("libre-panel: language {code}: {why}"),
    }
}

fn t_with(text: &str, values: &[(&str, &str)]) -> String {
    let values: Vec<(&str, lp_py::Value)> = values
        .iter()
        .map(|(name, value)| (*name, lp_py::Value::Str((*value).to_string())))
        .collect();
    lp_i18n::t_format(text, &values).unwrap_or_else(|why| why.msg)
}

/// running_instance: what the running app wrote about itself (its server's address, its
/// process), where it did.
fn running_instance(settings: &std::path::Path) -> lp_py::Dict {
    let text = std::fs::read_to_string(settings.join("instance.json")).unwrap_or_default();
    match lp_py::json::loads(&text) {
        Ok(lp_py::Value::Dict(told)) => told,
        _ => lp_py::Dict::default(),
    }
}

/// The value after an option (`--timeout 15`); Err where there is none.
fn option_value<'a>(
    rest: &mut std::slice::Iter<'a, String>,
    name: &str,
) -> Result<&'a str, String> {
    rest.next()
        .map(String::as_str)
        .ok_or_else(|| format!("{name}: expected one argument"))
}

/// `quit [--timeout S]` (cli.py's _cmd_quit): the running app asked to quit, e.g. before a
/// setup replaces it, and waited for until its process is gone too (an uninstaller then
/// finds no file in use).
fn quit_command(args: &[String]) -> Result<i32, String> {
    let mut timeout = 15.0;
    let mut rest = args.iter();
    while let Some(arg) = rest.next() {
        match arg.as_str() {
            "--timeout" => {
                let value = option_value(&mut rest, "--timeout")?;
                timeout = value
                    .parse::<f64>()
                    .map_err(|_| format!("--timeout: invalid float value: '{value}'"))?;
            }
            other => return Err(format!("quit: unrecognized argument {other}")),
        }
    }
    let home = Home::find()?;
    let settings = &home.settings;
    if !instance::InstanceLock::held_elsewhere(settings) {
        println!("{}", lp_i18n::t("Libre Panel is not running."));
        return Ok(0);
    }
    let info = running_instance(settings);
    let pid = match info.get("pid") {
        Some(lp_py::Value::Int(pid)) => i64::try_from(pid).ok(),
        _ => None,
    };
    if let Some(lp_py::Value::Str(url)) = info.get("editor") {
        let port = url
            .rsplit(':')
            .next()
            .and_then(|rest| rest.trim_end_matches('/').parse::<u16>().ok());
        let token = access::read_token(settings, port);
        let headers = [(access::HEADER, token.as_deref().unwrap_or(""))];
        let body = br#"{"action": "quit"}"#;
        if let Err(why) = client::post(url, "/api/app", &headers, body, 5.0) {
            let missing = if token.is_some() {
                ""
            } else {
                " (no access token to send)"
            };
            logs::say(
                "libre_panel",
                &format!("quit request failed: {why}{missing}"),
            );
        }
    }
    let own = i64::from(std::process::id());
    let gone = || {
        !instance::InstanceLock::held_elsewhere(settings)
            && !pid.is_some_and(|pid| pid != own && access::pid_exists(pid) && !ended(pid))
    };
    let until = std::time::Instant::now() + std::time::Duration::from_secs_f64(timeout.max(0.0));
    while std::time::Instant::now() < until {
        if gone() {
            println!("{}", lp_i18n::t("Libre Panel has quit."));
            return Ok(0);
        }
        std::thread::sleep(std::time::Duration::from_millis(200));
    }
    eprintln!(
        "{}",
        lp_i18n::t("Libre Panel did not quit (it may run in a terminal).")
    );
    Ok(1)
}

/// ended: whether the process `pid` has ended and only waits for its parent to hear of it (a
/// zombie, Linux): its files are no longer in use.
fn ended(pid: i64) -> bool {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).unwrap_or_default();
    // (the state after the program's name in brackets, which may hold spaces)
    stat.rsplit_once(')')
        .and_then(|(_, rest)| rest.split_whitespace().next())
        == Some("Z")
}

/// `notify TEXT [--title T] [--seconds S] [--icon NAME] [--level info|warning|error]`
/// (cli.py's _cmd_notify): a notice on the panel of the running app, e.g. at the end of a
/// script.
fn notify_command(args: &[String]) -> Result<i32, String> {
    let (mut text, mut title, mut seconds, mut icon, mut level) =
        (None, String::new(), 5.0, String::new(), "info".to_string());
    let mut rest = args.iter();
    while let Some(arg) = rest.next() {
        match arg.as_str() {
            "--title" => title = option_value(&mut rest, "--title")?.to_string(),
            "--icon" => icon = option_value(&mut rest, "--icon")?.to_string(),
            "--seconds" => {
                let value = option_value(&mut rest, "--seconds")?;
                seconds = value
                    .parse::<f64>()
                    .map_err(|_| format!("--seconds: invalid float value: '{value}'"))?;
            }
            "--level" => {
                let value = option_value(&mut rest, "--level")?;
                if !["info", "warning", "error"].contains(&value) {
                    return Err(format!(
                        "--level: invalid choice: '{value}' (choose from 'info', 'warning', \
                         'error')"
                    ));
                }
                level = value.to_string();
            }
            other if text.is_none() && !other.starts_with("--") => text = Some(other.to_string()),
            other => return Err(format!("notify: unrecognized argument {other}")),
        }
    }
    let text = text.ok_or("notify: the following arguments are required: text")?;
    let home = Home::find()?;
    let url = match instance::InstanceLock::held_elsewhere(&home.settings) {
        true => match running_instance(&home.settings).get("editor") {
            Some(lp_py::Value::Str(url)) if !url.is_empty() => Some(url.clone()),
            _ => None,
        },
        false => None,
    };
    let Some(url) = url else {
        eprintln!("{}", lp_i18n::t("Libre Panel is not running."));
        return Ok(1);
    };
    let mut body = lp_py::Dict::default();
    body.insert("text".into(), lp_py::Value::Str(text));
    body.insert("seconds".into(), lp_py::Value::Float(seconds));
    body.insert("level".into(), lp_py::Value::Str(level));
    if !title.is_empty() {
        body.insert("title".into(), lp_py::Value::Str(title));
    }
    if !icon.is_empty() {
        body.insert("icon".into(), lp_py::Value::Str(icon));
    }
    let body = lp_py::json::dumps(&lp_py::Value::Dict(body), &lp_py::json::Dumps::default());
    let key = notify::read_key();
    let headers = [(notify::HEADER, key.as_str())];
    let (status, answer) = match client::post(&url, "/api/notify", &headers, body.as_bytes(), 10.0)
    {
        Ok(answered) => answered,
        Err(why) => {
            eprintln!("{why}");
            return Ok(1);
        }
    };
    let answer = lp_py::json::loads(&String::from_utf8_lossy(&answer)).ok();
    let field = |name: &str| match &answer {
        Some(lp_py::Value::Dict(answer)) => answer.get(name).cloned(),
        _ => None,
    };
    if status >= 400 {
        let said = match field("error") {
            Some(error) if error.truthy() => error.str(),
            _ => format!("HTTP Error {status}"),
        };
        eprintln!("{said}");
        return Ok(1);
    }
    if !field("shown").is_some_and(|shown| shown.truthy()) {
        let why = match field("reason") {
            Some(reason) if reason.truthy() => reason.str(),
            _ => "?".to_string(),
        };
        eprintln!("{}", t_with("Not shown: {why}.", &[("why", &why)]));
        return Ok(2);
    }
    Ok(0)
}

/// " -> model / model" where the ids are a panel's the catalogue knows (cli.py's
/// _describe_candidates).
fn candidates(vid: Option<u16>, pid: Option<u16>, serial: Option<&str>) -> String {
    let Some((vid, pid)) = vid.zip(pid) else {
        return String::new();
    };
    let found = lp_devices::models::models_for_usb(vid, pid, serial);
    if found.is_empty() {
        return String::new();
    }
    let ids: Vec<&str> = found.iter().map(|model| model.id).collect();
    format!("  -> {}", ids.join(" / "))
}

/// `devices` (cli.py's _cmd_devices): the drivers, and the serial ports and USB panels there
/// are, each with the panels its ids may be.
fn devices_command() {
    println!("drivers: auto, turzx, virtual");
    let ports = lp_devices::serial::ports();
    for port in &ports {
        let ids = match port.vid.zip(port.pid) {
            Some((vid, pid)) => format!("{vid:04x}:{pid:04x}"),
            _ => "----:----".to_string(),
        };
        let serial = port.serial_number.as_deref();
        println!(
            "{:<16} {ids}  {}  serial={}{}",
            port.device,
            port.description,
            serial.unwrap_or("None"),
            candidates(port.vid, port.pid, serial)
        );
    }
    // (list_usb_devices: the plain USB panels by their makers' ids, and the catalogue's other
    // USB screens by their own)
    const VENDORS: [u16; 3] = [0x1CBE, 0x43A8, 0x345F];
    let mut products: Vec<(u16, u16)> = lp_devices::models::MODELS
        .iter()
        .filter(|model| model.protocol.starts_with("usb"))
        .flat_map(|model| model.usb_ids.iter().copied())
        .filter(|(vid, _)| !VENDORS.contains(vid))
        .collect();
    products.sort_unstable();
    products.dedup();
    let present = lp_devices::usb::all_ids();
    let mut usb = Vec::new();
    for vendor in VENDORS {
        usb.extend(present.iter().filter(|(vid, _)| *vid == vendor));
    }
    for ids in &products {
        usb.extend(present.iter().filter(|found| *found == ids));
    }
    for (vid, pid) in &usb {
        println!(
            "{:<16} {vid:04x}:{pid:04x}{}",
            "usb",
            candidates(Some(*vid), Some(*pid), None)
        );
    }
    if ports.is_empty() && usb.is_empty() {
        println!("no panels found");
    }
}

/// `location NAME` (cli.py's _cmd_location): latitude and longitude of the places of that
/// name, for `[weather]`.
fn location_command(args: &[String]) -> Result<i32, String> {
    let [name] = args else {
        return Err("location: the following arguments are required: name".into());
    };
    let agent = format!("libre-panel/{}", lp_py::VERSION);
    let found = lp_sensors::weather::geocode(name, 5, "en", &agent)?;
    if found.is_empty() {
        println!("no matches");
        return Ok(1);
    }
    for place in found {
        let parts: Vec<&str> = [&place.name, &place.region, &place.country]
            .into_iter()
            .map(String::as_str)
            .filter(|part| !part.is_empty())
            .collect();
        println!(
            "{}\n  latitude = {}\n  longitude = {}",
            parts.join(", "),
            lp_py::format::str_of(&place.latitude),
            lp_py::format::str_of(&place.longitude)
        );
    }
    println!("\nWeather data by Open-Meteo.com (CC BY 4.0)");
    Ok(0)
}

/// `autostart enable|disable|status [--elevated]` (cli.py's _cmd_autostart).
fn autostart(args: &[String]) -> Result<(), String> {
    let mut action = None;
    let mut elevated = false;
    let mut rest = Vec::new();
    for arg in args {
        match arg.as_str() {
            "--elevated" => elevated = true,
            "enable" | "disable" | "status" if action.is_none() => action = Some(arg.clone()),
            _ => rest.push(arg.clone()),
        }
    }
    let Some(action) = action else {
        return Err("autostart: enable, disable or status".into());
    };
    let asked = asked(&rest)?;
    let home = Home::find()?;
    let file = asked.config.clone().unwrap_or_else(|| home.config_file());
    speak(&home, &file);
    let command = autostart::launch_command(asked.config.as_deref());
    let started = autostart::Autostart::new(&command[0]);
    let icon = home.data.join("assets").join("libre-panel.png");
    let done = match action.as_str() {
        "enable" => {
            if elevated
                && autostart::writable_by_user(
                    std::path::Path::new(&command[0]),
                    &autostart::user_home(),
                )
            {
                eprintln!("{}", t_with(
                    "Warning: {program} is in your user folder, where any program you run could \
                     replace it and so get administrator rights at the next login. Safer: \
                     install Libre Panel under Program Files.",
                    &[("program", &command[0])],
                ));
            }
            let why = match elevated {
                true => access::folder_risk(&home.settings, None),
                false => String::new(),
            };
            if !why.is_empty() {
                let folder = home.settings.display().to_string();
                eprintln!("{}", t_with(
                    "Warning: with the highest rights Libre Panel runs what config.toml and the \
                     plugins in {folder} name, and {why}: any user who can change them can run \
                     a program with administrator rights. Keep the settings in your user folder \
                     (LIBRE_PANEL_HOME), or take the other users' rights away.",
                    &[("folder", &folder), ("why", &why)],
                ));
            }
            started.enable(&command, elevated, &icon).map(|place| {
                println!(
                    "{}",
                    t_with(
                        "Libre Panel starts in the background when you log in ({where}).",
                        &[("where", &place)],
                    )
                );
            })
        }
        "disable" => started.disable().map(|existed| match existed {
            true => println!("{}", lp_i18n::t("Autostart removed.")),
            false => println!("{}", lp_i18n::t("Autostart was not enabled.")),
        }),
        _ => started.is_enabled().map(|on| match on {
            true => println!(
                "{}",
                t_with(
                    "Autostart is enabled ({where}).",
                    &[("where", &started.location())],
                )
            ),
            false => println!("{}", lp_i18n::t("Autostart is disabled.")),
        }),
    };
    if let Err(why) = done {
        eprintln!("{why}");
        return Err(String::new());
    }
    if action != "enable" {
        let hint = started.task_hint();
        if !hint.is_empty() {
            eprintln!("{hint}"); // (a leftover task of the same name)
        }
    }
    Ok(())
}

/// `libre-panel system-setup`: what the system needs an administrator for, one command on
/// both systems (cli.py's _cmd_system_setup). Windows: the sensor service, the PawnIO driver
/// and the group game frame rates need. Linux: the panel's permission, the mainboard's sensor
/// driver and the processor's power (`--linux`, taken for granted on Linux unless a flag of
/// Windows' is given).
fn system_setup_command(args: &[String]) -> Result<u8, String> {
    let mut windows_options = system_setup::Options::default();
    let mut linux_options = linux_setup::CliOptions::default();
    let mut linux = false;
    let mut rest = args.iter();
    while let Some(arg) = rest.next() {
        match arg.as_str() {
            "--service" => windows_options.service = true,
            "--pawnio" => windows_options.pawnio = true,
            "--perf-log-users" => windows_options.perf_log_users = true,
            "--user-sid" => {
                let sid = rest.next().ok_or("--user-sid needs a value")?;
                windows_options.user_sid = Some(sid.clone());
            }
            "--linux" => linux = true,
            "--rapl" => linux_options.rapl = true,
            "--no-rapl" => linux_options.no_rapl = true,
            "--remove" => {
                windows_options.remove = true;
                linux_options.remove = true;
            }
            "--status" => {
                windows_options.status = true;
                linux_options.status = true;
            }
            "--dry-run" => linux_options.dry_run = true,
            "--json" => linux_options.json = true,
            other => return Err(format!("system-setup: unknown option {other}")),
        }
    }
    if linux_options.rapl && linux_options.no_rapl {
        return Err("system-setup: --rapl and --no-rapl, not both".into());
    }
    let windows: Vec<&str> = [
        ("--service", windows_options.service),
        ("--pawnio", windows_options.pawnio),
        ("--perf-log-users", windows_options.perf_log_users),
        ("--user-sid", windows_options.user_sid.is_some()),
    ]
    .iter()
    .filter(|(_, given)| *given)
    .map(|(flag, _)| *flag)
    .collect();
    if linux || (cfg!(target_os = "linux") && windows.is_empty()) {
        if !windows.is_empty() {
            eprintln!(
                "error: {} is for Windows, not for --linux",
                windows.join(", ")
            );
            return Ok(2);
        }
        return Ok(linux_setup::run_cli(&linux_options));
    }
    let only_linux: Vec<&str> = [
        ("--rapl", linux_options.rapl),
        ("--no-rapl", linux_options.no_rapl),
        ("--dry-run", linux_options.dry_run),
        ("--json", linux_options.json),
    ]
    .iter()
    .filter(|(_, given)| *given)
    .map(|(flag, _)| *flag)
    .collect();
    if !only_linux.is_empty() {
        eprintln!("error: {} is for Linux (--linux)", only_linux.join(", "));
        return Ok(2);
    }
    if !cfg!(windows) {
        eprintln!("system-setup is for Windows: elsewhere the sensors need no service.");
        return Ok(1);
    }
    let admin = autostart::is_admin();
    Ok(system_setup::run_command(
        &mut windows_options,
        &system_setup::win::Real,
        admin,
    ))
}

/// _double_clicked: whether this console program (libre-panel.exe of the Windows downloads)
/// was started with a double click: Windows then made a console for it alone, not a terminal
/// it runs in.
#[cfg(windows)]
fn double_clicked() -> bool {
    use windows_sys::Win32::System::Console::GetConsoleProcessList;
    if !crate::settings::packaged() {
        return false;
    }
    let mut ids = [0u32; 4];
    // SAFETY: the list is written into `ids`, of the length given
    unsafe { GetConsoleProcessList(ids.as_mut_ptr(), ids.len() as u32) == 1 }
}

#[cfg(not(windows))]
fn double_clicked() -> bool {
    false
}

/// (here, not in window.rs, which the desktop programs share: only this program hands over)
/// _hand_over_to_the_app: a double click on libre-panel.exe starts the app as LibrePanel.exe
/// does (LibrePanel.exe started, on its own), so that the console goes at once. Whether it
/// was handed over.
pub fn hand_over() -> bool {
    if !double_clicked() {
        return false;
    }
    let Ok(windowed) = window::sibling("LibrePanel") else {
        return false;
    };
    if !windowed.is_file() {
        return false;
    }
    let mut command = std::process::Command::new(windowed);
    window::detached(&mut command);
    command.spawn().is_ok()
}

/// main: the program: the command its arguments name, else `default` ("start" for
/// libre-panel, "tray" for LibrePanel.exe, as the Python downloads' programs have it).
/// The handles of the console or the pipes this process was given are its own, not passed on
/// to every program it starts: Windows hands a new process all handles that may be inherited,
/// and a program that outlives this one (the window, a browser) would hold whoever started the
/// app waiting for the end of its output. Python's subprocess passes on only the three a child
/// is given; Rust's `Stdio::inherit` makes its own copy of them, which still works.
fn own_handles() {
    #[cfg(windows)]
    {
        use windows_sys::Win32::Foundation::{SetHandleInformation, HANDLE_FLAG_INHERIT};
        use windows_sys::Win32::System::Console::{
            GetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
        };
        for which in [STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE] {
            // SAFETY: the process's own standard handles; a missing one is null and refused.
            unsafe {
                let handle = GetStdHandle(which);
                if !handle.is_null() && handle as isize != -1 {
                    SetHandleInformation(handle, HANDLE_FLAG_INHERIT, 0);
                }
            }
        }
    }
}

pub fn main(default: &str) -> ExitCode {
    own_handles();
    logs::log_panics();
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    // the options before the command (`--config FILE tray --background`, the start at login
    // that the Python app wrote): they go after it
    let mut leading = Vec::new();
    while args.first().is_some_and(|first| first == "--config") && args.len() >= 2 {
        leading.extend(args.drain(..2));
    }
    // a double click on libre-panel.exe, the command line's program, starts the app as
    // LibrePanel.exe does, and its console goes at once (it would else stay open as long as
    // the app runs, and closing it would end the app)
    if default == "start" && args.is_empty() && leading.is_empty() && hand_over() {
        return ExitCode::SUCCESS;
    }
    // (`--version`, as cli.py's parser has it)
    if args.first().is_some_and(|first| first == "--version") {
        args[0] = "version".to_string();
    }
    // (LibrePanel.exe: `--background` alone means `tray --background`)
    let options_only = args
        .first()
        .is_some_and(|first| first.starts_with('-') && !["--help", "-h"].contains(&first.as_str()));
    let (command, rest) = match args.split_first() {
        Some(_) if options_only && default != "help" => (default.to_string(), args.clone()),
        Some((command, rest)) => (command.clone(), rest.to_vec()),
        None => (default.to_string(), Vec::new()),
    };
    let rest: Vec<String> = rest.into_iter().chain(leading).collect();
    // (`libre-panel run --help`: the help, whatever the command; a notice's own words stay)
    if command != "notify" && rest.iter().any(|arg| arg == "--help" || arg == "-h") {
        print!("{USAGE}");
        return ExitCode::SUCCESS;
    }
    // a portable copy's first start takes over the settings of the user's own folder; and
    // what an earlier version kept in the cache and this one does not goes
    match lp_config::dirs::take_over_into_portable() {
        Ok(Some(came)) => logs::say(
            "libre_panel.cli",
            &format!("portable: took over the settings of {}", came.display()),
        ),
        Ok(None) => {}
        Err(why) => logs::say("libre_panel.config", &why),
    }
    lp_config::dirs::forget_gone_caches();
    match run(&command, &rest) {
        Ok(()) => ExitCode::SUCCESS,
        Err(why) => {
            if !why.is_empty() {
                eprintln!("libre-panel: {why}");
            }
            ExitCode::FAILURE
        }
    }
}
