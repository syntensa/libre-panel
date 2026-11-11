//! The Windows programs' resources (../winres.rs): their version, icon and manifest.

include!("../winres.rs");

fn main() {
    windows_resources(&[
        ("libre-panel-window", "Libre Panel window"),
        ("libre-panel-tray", "Libre Panel tray icon"),
    ]);
}
