//! libre-panel: Libre Panel as one program of Rust (docs/RUST.md, R5 and R9). It reads
//! config.toml, draws the theme's frames of the computer's readings and shows them on the
//! panel, or writes them to a PNG file.

mod display;
mod home;
mod mainloop;
mod osinfo;
mod psutil;
mod sensors;
mod system;

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::atomic::AtomicBool;

use home::Home;
use mainloop::{Asked, MainLoop};

/// set by Ctrl+C and by the system's request to end: the loop ends after its frame
static STOP: AtomicBool = AtomicBool::new(false);

const USAGE: &str = "\
Libre Panel

  libre-panel run [options]     show the theme on the panel until stopped (Ctrl+C)
  libre-panel render [options]  draw one frame into a PNG file (--output, else frame.png)
  libre-panel sensors           the readings and their keys (--check-service: whether the
                                sensor service answers, on Windows)
  libre-panel themes            the themes that are installed
  libre-panel models            the panels Libre Panel knows
  libre-panel ports             the serial ports and what is on them
  libre-panel config            where config.toml is, and whether it is right
  libre-panel version

Options:
  --config FILE    another config.toml
  --theme NAME     this theme instead of config.toml's
  --demo           readings made up instead of the computer's
  --output FILE    frames into this PNG file instead of the panel
  --frames N       end after N frames
";

/// between the two readings of `sensors`
const SENSORS_SETTLE_SECONDS: f64 = 1.0;
/// how long `sensors` waits for a source's first readings
const READY_SECONDS: f64 = 20.0;

/// unlock_offered: whether "Unlock all sensors" is a thing here: a Windows download that
/// carries the sensor service (sensors\libre-panel-sensors.exe beside the program), and the
/// service not installed (or switched off).
fn unlock_offered() -> bool {
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
            "--demo" => asked.demo = true,
            other => return Err(format!("unknown option {other}")),
        }
    }
    Ok(asked)
}

fn run(command: &str, args: &[String]) -> Result<(), String> {
    match command {
        "version" | "--version" | "-V" => println!("libre-panel {}", env!("CARGO_PKG_VERSION")),
        "help" | "--help" | "-h" => print!("{USAGE}"),
        "models" => {
            let majesty = std::iter::once(&lp_devices::hongtai::MAJESTY);
            for model in lp_devices::models::MODELS.iter().chain(majesty) {
                println!("{:<28} {}", model.id, model.label());
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
            let home = Home::find()?;
            let places = home.places();
            for folder in [&places.user, &places.builtin] {
                let mut names: Vec<String> = std::fs::read_dir(folder)
                    .into_iter()
                    .flatten()
                    .flatten()
                    .filter(|entry| entry.path().join("theme.json").is_file())
                    .filter_map(|entry| entry.file_name().into_string().ok())
                    .collect();
                names.sort();
                for name in names {
                    println!("{name}");
                }
            }
        }
        "config" => {
            let asked = asked(args)?;
            let file = match asked.config {
                Some(file) => file,
                None => Home::find()?.config_file(),
            };
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
            let (mut hub, missing) = sensors::Hub::of(&config, asked.demo);
            // Some sources read only while a theme shows their keys: asked for as a theme
            // would, so they are listed ("lhm:*" is every sensor of LibreHardwareMonitor).
            let mut keys = ["cpu.load", "gpu.load", "audio.*", "lhm:*", "temp.*"]
                .map(String::from)
                .to_vec();
            // (the tests' own: more keys asked for, as a theme that shows them would)
            if let Ok(more) = std::env::var("LP_SENSORS_WANT") {
                keys.extend(more.split(',').map(str::to_string));
            }
            hub.want(keys);
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
                );
                if let Some(hint) = hint {
                    eprintln!("\nnote: {hint}");
                }
            }
            if !missing.is_empty() {
                eprintln!(
                    "libre-panel: sensor sources not in this build yet: {}",
                    missing.join(", ")
                );
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
            MainLoop::new(Home::find()?, asked)?.run(&STOP);
        }
        other => return Err(format!("unknown command {other}\n\n{USAGE}")),
    }
    Ok(())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (command, rest) = match args.split_first() {
        Some((command, rest)) => (command.as_str(), rest),
        None => ("help", &[][..]),
    };
    match run(command, rest) {
        Ok(()) => ExitCode::SUCCESS,
        Err(why) => {
            eprintln!("libre-panel: {why}");
            ExitCode::FAILURE
        }
    }
}
