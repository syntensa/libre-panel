//! libre-panel-window: Libre Panel's own window (window.py), the app in WebView2 (Windows) or
//! WebKitGTK (Linux), one at a time: `libre-panel-window URL [--view VIEW]`. The app and
//! every start open it through rust/app's window.rs; the files it shares with the app are
//! taken in from there.

// (no console window of its own on Windows: what it says goes to logs/window.log)
#![cfg_attr(windows, windows_subsystem = "windows")]

#[allow(dead_code)]
#[path = "../../app/src/access.rs"]
mod access;
#[allow(dead_code)]
#[path = "../../app/src/home.rs"]
mod home;
#[allow(dead_code)]
#[path = "../../app/src/instance.rs"]
mod instance;
#[allow(dead_code)]
#[path = "../../app/src/logs.rs"]
mod logs;
mod show;
#[allow(dead_code)]
#[path = "../../app/src/window.rs"]
mod window;

/// What home.rs takes from the app's plugins.rs: the window looks for no themes.
mod plugins {
    pub fn theme_folders(_settings: &std::path::Path) -> Vec<std::path::PathBuf> {
        Vec::new()
    }
}

/// What logs.rs takes from the app's autostart.rs.
mod autostart {
    /// The person's own folder.
    pub fn user_home() -> std::path::PathBuf {
        let named = match cfg!(windows) {
            true => std::env::var_os("USERPROFILE"),
            false => std::env::var_os("HOME"),
        };
        named.map(std::path::PathBuf::from).unwrap_or_default()
    }
}

/// The app's language as config.toml sets it (the system's where it cannot be read), for the
/// window's questions.
fn speak(home: &home::Home) {
    let setting = lp_config::load_config(&home.config_file(), lp_config::platform())
        .map_or_else(|_| "auto".to_string(), |config| config.language);
    let env = |name: &str| std::env::var(name).unwrap_or_default();
    let code = lp_i18n::resolve(Some(&setting), || lp_i18n::system_language(&env, None));
    if let Ok(language) = lp_i18n::Language::load(&home.locale(), &code) {
        lp_i18n::set_language(language);
    }
}

fn main() {
    let mut url = None;
    let mut view = None;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--view" => view = args.next(),
            "--config" => drop(args.next()), // (the app's; the window keeps its own)
            other if url.is_none() && !other.starts_with("--") => url = Some(other.to_string()),
            other => {
                eprintln!("libre-panel-window: unknown argument {other}");
                std::process::exit(2);
            }
        }
    }
    let Some(url) = url else {
        eprintln!("usage: libre-panel-window URL [--view VIEW]");
        std::process::exit(2);
    };
    let view = view.filter(|view| window::VIEWS.contains(&view.as_str()));
    let home = match home::Home::find() {
        Ok(home) => home,
        Err(why) => {
            eprintln!("libre-panel-window: {why}");
            std::process::exit(1);
        }
    };
    speak(&home);
    let code = show::run_window(&home.settings, &url, view.as_deref());
    // (its state is saved and its lock let go: the process ends here, before the web view's
    // library is taken apart)
    std::process::exit(code);
}
