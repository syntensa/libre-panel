//! The fields of the installed plugins' parts (a widget type's `spec`, a screen's or a toast
//! style's `options`), as theme/model.py looks them up in the plugin registry: the app tells
//! them here once its plugin host has said them, and themes are checked against them.

use std::collections::HashMap;
use std::sync::{OnceLock, RwLock};

use lp_py::Value;

/// One field of a plugin's part: its name, kind (`int`, `color?`, `enum:a|b`, ...) and default.
#[derive(Clone, Debug, PartialEq)]
pub struct PluginField {
    pub name: String,
    pub kind: String,
    pub default: Value,
}

type Known = HashMap<(String, String), Vec<PluginField>>;

fn known() -> &'static RwLock<Known> {
    static KNOWN: OnceLock<RwLock<Known>> = OnceLock::new();
    KNOWN.get_or_init(|| RwLock::new(HashMap::new()))
}

/// The parts the plugins have, by kind (`widgets`, `screens`, `toasts`) and name, with their
/// fields; what was said before goes.
pub fn set(parts: Vec<(String, String, Vec<PluginField>)>) {
    if let Ok(mut held) = known().write() {
        held.clear();
        for (kind, name, fields) in parts {
            held.insert((kind, name), fields);
        }
    }
}

/// The fields of the plugin part `name` of `kind`, None where no plugin has it.
pub fn fields(kind: &str, name: &str) -> Option<Vec<PluginField>> {
    known()
        .read()
        .ok()?
        .get(&(kind.to_string(), name.to_string()))
        .cloned()
}
