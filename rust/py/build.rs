//! Libre Panel's version, as `src/libre_panel/__init__.py` names it (`__version__`): the
//! program says the version of the release it belongs to, whichever language a part is in.

use std::path::Path;

fn main() {
    let source = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../src/libre_panel/__init__.py");
    println!("cargo:rerun-if-changed={}", source.display());
    let text = std::fs::read_to_string(&source).unwrap_or_default();
    let named = text.lines().find_map(|line| {
        let value = line
            .strip_prefix("__version__")?
            .trim_start()
            .strip_prefix('=')?;
        let value = value.trim();
        let quote = value.chars().next().filter(|c| *c == '"' || *c == '\'')?;
        Some(value[1..].split(quote).next()?.to_string())
    });
    let version = named.unwrap_or_else(|| {
        println!("cargo:warning=no __version__ in {}", source.display());
        env!("CARGO_PKG_VERSION").to_string()
    });
    println!("cargo:rustc-env=LIBRE_PANEL_VERSION={version}");
}
