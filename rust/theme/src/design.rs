// design.py's tables (and the palette roles of theme/modules.py): made from the Python app's
// own once, kept here since; tests/test_rust_theme.py compares them.

use std::sync::OnceLock;

use lp_py::{Dict, Value};

/// Type and room in a module by the shorter side of its box: the share of it, the least and
/// the most in px.
pub const MODULE_LABEL: (f64, i64, i64) = (0.075, 9, 22);
pub const MODULE_PADDING: (f64, i64, i64) = (0.1, 6, 26);
pub const MODULE_PADDING_BARE: (f64, i64, i64) = (0.04, 0, 65536);
pub const DEFAULT_LOOK: &str = "arctic";
/// Palette roles the modules paint with.
pub static ROLES: &[&str] = &[
    "bg", "bg2", "surface", "line", "track", "text", "text2", "text3", "accent", "cpu", "gpu",
    "mem", "disk", "net", "warn", "crit",
];

pub const LOOKS_JSON: &str = r###"{"arctic": {"name": "Arctic", "palette": {"bg": "#0A1119", "bg2": "#060A0F", "surface": "#14202E", "line": "#1F3147", "track": "#162332", "text": "#EEF6FF", "text2": "#B8CAE0", "text3": "#8CA0B8", "accent": "#13E5D7", "cpu": "#13E5D7", "gpu": "#A3AEFF", "mem": "#E0A8FF", "disk": "#8FD3FF", "net": "#7EE8A6", "warn": "#FFCB6B", "crit": "#FF7A85"}, "style": {"card": "glass", "radius": 18, "glow": 0.6}}, "libre": {"name": "Libre", "palette": {"bg": "#0A0E13", "bg2": "#06090D", "surface": "#0D1219", "line": "#1F2A36", "track": "#18212C", "text": "#E8EEF4", "text2": "#C9D3DE", "text3": "#93A1B2", "dim": "#5F6E80", "accent": "#2EE6D6", "cpu": "#2EE6D6", "gpu": "#7F9CFF", "mem": "#C792FF", "disk": "#5FB6FF", "net": "#34D399", "warn": "#F5C46B", "crit": "#F87171"}, "style": {"card": "glass", "radius": 14, "glow": 0.6, "backdrop": "flat"}}, "neon": {"name": "Neon", "palette": {"bg": "#07060F", "bg2": "#020107", "surface": "#110F26", "line": "#2D2760", "track": "#1B1838", "text": "#F7F5FF", "text2": "#CBC4F5", "text3": "#9089C6", "accent": "#FF4FD8", "cpu": "#00F0FF", "gpu": "#FF4FD8", "mem": "#B26BFF", "disk": "#FFE45C", "net": "#4CFF9A", "warn": "#FFB020", "crit": "#FF3B5C"}, "style": {"card": "outline", "radius": 14, "glow": 1.0}}, "graphite": {"name": "Graphite", "palette": {"bg": "#141516", "bg2": "#0C0D0E", "surface": "#1E2023", "line": "#2C2F33", "track": "#2A2D31", "text": "#F2F2F2", "text2": "#C6C9CE", "text3": "#8D9298", "accent": "#FF8A3D", "cpu": "#FF8A3D", "gpu": "#5EB1FF", "mem": "#C792EA", "disk": "#9CCC65", "net": "#4DD0E1", "warn": "#FFC857", "crit": "#FF5C5C"}, "style": {"card": "flat", "radius": 10, "glow": 0.15}}, "paper": {"name": "Paper", "palette": {"bg": "#E9EEF3", "bg2": "#DCE3EA", "surface": "#FFFFFF", "line": "#D3DBE3", "track": "#E2E8EF", "text": "#15212C", "text2": "#3E4C5A", "text3": "#687887", "accent": "#0B8C84", "cpu": "#0B8C84", "gpu": "#4B57C9", "mem": "#9B4FC4", "disk": "#2F7FB8", "net": "#2E9E5B", "warn": "#C98200", "crit": "#D6434E"}, "style": {"card": "flat", "radius": 16, "glow": 0.0}}, "sunset": {"name": "Sunset", "palette": {"bg": "#170D16", "bg2": "#0B0609", "surface": "#24151F", "line": "#3D2434", "track": "#2F1B28", "text": "#FFF2EB", "text2": "#F2CBBA", "text3": "#B98F82", "accent": "#FF7E5F", "cpu": "#FF7E5F", "gpu": "#FEB47B", "mem": "#EC6FAA", "disk": "#FFD27A", "net": "#7FD8BE", "warn": "#FFC857", "crit": "#FF4E6A"}, "style": {"card": "glass", "radius": 20, "glow": 0.5}}, "mono": {"name": "Mono", "palette": {"bg": "#000000", "bg2": "#000000", "surface": "#0B0B0B", "line": "#2A2A2A", "track": "#1E1E1E", "text": "#FFFFFF", "text2": "#C2C2C2", "text3": "#7D7D7D", "accent": "#FFFFFF", "cpu": "#FFFFFF", "gpu": "#FFFFFF", "mem": "#FFFFFF", "disk": "#FFFFFF", "net": "#FFFFFF", "warn": "#FFC400", "crit": "#FF3D3D"}, "style": {"card": "outline", "radius": 4, "glow": 0.0, "backdrop": "flat", "display_font": "builtin:JetBrainsMono-Bold", "text_font": "builtin:JetBrainsMono-Medium"}}, "contrast": {"name": "High Contrast", "palette": {"bg": "#000000", "bg2": "#000000", "surface": "#000000", "line": "#FFFFFF", "track": "#3A3A3A", "text": "#FFFFFF", "text2": "#F2F2F2", "text3": "#E6E6E6", "dim": "#BDBDBD", "accent": "#FFD60A", "cpu": "#FFD60A", "gpu": "#00E5FF", "mem": "#FFFFFF", "disk": "#FFFFFF", "net": "#00FF85", "warn": "#FF9F0A", "crit": "#FF453A"}, "style": {"card": "outline", "radius": 8, "glow": 0.0, "backdrop": "flat", "display_font": "builtin:Barlow-Bold", "text_font": "builtin:Barlow-SemiBold"}}}"###;
pub const ALIASES_JSON: &str = r###"{"base": "bg", "base_low": "bg2", "surf_1": "surface", "tx_1": "text", "tx_2": "text2", "tx_3": "text3", "krit": "crit"}"###;
pub const BLOCK_COLOURS_JSON: &str = r###"{"accent": "#2EE6D6", "accent_deep": "#0F6B64", "text": "#E8EEF4", "muted": "#93A1B2", "dim": "#5F6E80", "card": "#111822", "card2": "#0C1219", "edge": "#1F2A36", "good": "#34D399", "up": "#F472B6", "warn": "#F5C46B", "crit": "#F87171"}"###;
pub const FAMILIES_JSON: &str = r###"{"lumen": {"look": "libre", "colours": {}, "cards": {"card_opacity": 0.7}, "why": ""}, "almanac": {"look": "libre", "colours": {}, "cards": {"card_opacity": 0.7}, "why": ""}, "nimbus": {"look": "libre", "colours": {}, "cards": {"card_opacity": 0.7}, "why": ""}, "encore": {"look": "libre", "colours": {}, "cards": {"card_opacity": 0.9}, "why": ""}, "terra": {"look": "libre", "colours": {"accent": "#5FC8FF"}, "cards": {"card_opacity": 0.6}, "why": ""}, "libre-default": {"look": "libre", "colours": {}, "cards": {"card": "flat", "radius": 12, "glow": 0.5}, "why": ""}, "column": {"look": "libre", "colours": {}, "cards": {}, "why": ""}, "orbit": {"look": "libre", "colours": {}, "cards": {}, "why": ""}, "slate": {"look": "libre", "colours": {}, "cards": {}, "why": ""}, "pico": {"look": "libre", "colours": {}, "cards": {}, "why": ""}, "arena": {"look": "arctic", "colours": {}, "cards": {"card_opacity": 0.8}, "why": ""}, "round-report": {"look": "arctic", "colours": {}, "cards": {"card_opacity": 0.8}, "why": "it follows a game on Arena: in its design"}, "agents": {"look": "arctic", "colours": {}, "cards": {"card_opacity": 0.85}, "why": ""}, "studio": {"look": "arctic", "colours": {"line": "#32475E", "disk": "#7EE8A6"}, "cards": {"radius": 12, "backdrop": "flat", "card_opacity": 0.85}, "why": ""}, "contrast": {"look": "contrast", "colours": {}, "cards": {}, "why": ""}}"###;

fn parsed(text: &str) -> Dict {
    match lp_py::json::loads(text) {
        Ok(Value::Dict(d)) => d,
        _ => unreachable!("a table of the app's own"),
    }
}

/// LOOKS, as design.py has it
pub fn looks() -> &'static Dict {
    static MADE: OnceLock<Dict> = OnceLock::new();
    MADE.get_or_init(|| parsed(LOOKS_JSON))
}

/// ALIASES, as design.py has it
pub fn aliases() -> &'static Dict {
    static MADE: OnceLock<Dict> = OnceLock::new();
    MADE.get_or_init(|| parsed(ALIASES_JSON))
}

/// BLOCK_COLOURS, as design.py has it
pub fn block_colours() -> &'static Dict {
    static MADE: OnceLock<Dict> = OnceLock::new();
    MADE.get_or_init(|| parsed(BLOCK_COLOURS_JSON))
}

/// FAMILIES, as design.py has it
pub fn families() -> &'static Dict {
    static MADE: OnceLock<Dict> = OnceLock::new();
    MADE.get_or_init(|| parsed(FAMILIES_JSON))
}
