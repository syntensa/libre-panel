//! libre-panel: Libre Panel as one program of Rust (docs/RUST.md, R5 and R9). It reads
//! config.toml, draws the theme's frames of the computer's readings and shows them on the
//! panel, or writes them to a PNG file.

mod display;
mod home;
mod mainloop;
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
