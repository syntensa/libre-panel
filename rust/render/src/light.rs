//! The theme colours light takes (render/light.py's ROLES, role_color and mix).

use lp_py::{Dict, Value};

/// The Libre look's palette (libre_panel.design), where a theme has none of a role's names.
const LIBRE_BG: &str = "#0A0E13";
const LIBRE_TEXT: &str = "#E8EEF4";
const LIBRE_ACCENT: &str = "#2EE6D6";
const LIBRE_WARN: &str = "#F5C46B";

/// ROLES: the palette names themes use for a role (the first one a theme has wins), and the
/// colour where a theme has none of them.
fn role(name: &str) -> (&'static [&'static str], &'static str) {
    match name {
        "ground" => (&["bg", "base"], LIBRE_BG),
        "text" => (&["text", "tx_1"], LIBRE_TEXT),
        "info" => (&["accent", "primary", "cpu"], LIBRE_ACCENT),
        "warning" => (&["warn", "warning"], LIBRE_WARN),
        _ => (&[], "#000000"),
    }
}

/// role_color: the theme's colour for a role as RGB: the first of its names the palette has
/// (and not empty), else the Libre look's; a colour Pillow cannot read, the Libre look's.
pub(crate) fn role_colour(palette: &Dict, name: &str) -> [i64; 3] {
    let (names, fallback) = role(name);
    let given = names.iter().find_map(|n| match palette.get(*n) {
        Some(Value::Str(s)) if !s.is_empty() => Some(s.as_str()),
        _ => None,
    });
    let rgb = given
        .and_then(|v| lp_pixels::colour::getrgb(v).ok())
        .or_else(|| lp_pixels::colour::getrgb(fallback).ok())
        .unwrap_or_default();
    [0, 1, 2].map(|i| rgb.get(i).copied().unwrap_or(0))
}

/// mix: `a` over `b` by `share` (0: all `b`, 1: all `a`), each channel rounded as Python's
/// round rounds (half to even).
pub(crate) fn mix(a: [i64; 3], b: [i64; 3], share: f64) -> [i64; 3] {
    [0, 1, 2].map(|i| lp_py::round(b[i] as f64 + (a[i] - b[i]) as f64 * share) as i64)
}
