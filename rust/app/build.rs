//! The Windows program's resources (../winres.rs): its version, icon and manifest.

include!("../winres.rs");

fn main() {
    windows_resources(&[("libre-panel", "Libre Panel (command line)")]);
}
