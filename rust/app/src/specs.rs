//! What the editor knows of themes (editor/server.py `_specs`, editor/presets.py and
//! drawings.py's `plugin_specs` and `preset_previews`): every widget type's fields, the common
//! and the effect fields, the look's blocks and ranges, the finishes, the panels, the fonts,
//! icons and modules, and the building blocks drawn in a theme's palette.

use lp_py::{Dict, Value};
use lp_theme::specs::Field;
use lp_theme::tables;
use lp_theme::versions::Places;
use lp_theme::Env;

/// editor/presets.py's building blocks and colour tokens, as presets_for_editor() gives them
/// (written by `json.dumps(presets_for_editor(), indent=1, ensure_ascii=False)`; the test of
/// /api/specs keeps the two the same).
const PRESETS: &str = include_str!("presets.json");

fn presets() -> Dict {
    match lp_py::json::loads(PRESETS) {
        Ok(Value::Dict(d)) => d,
        _ => Dict::default(),
    }
}

fn fields(table: &[Field]) -> Value {
    Value::Dict(
        table
            .iter()
            .map(|f| {
                let spec = Value::List(vec![Value::from(f.kind), f.default.value()]);
                (f.name.to_string(), spec)
            })
            .collect(),
    )
}

fn strs(items: &[&str]) -> Value {
    Value::List(items.iter().map(|&s| Value::from(s)).collect())
}

/// plugin_specs: the plugins' widget types, screens and toast styles, and the built-in toast
/// styles besides the card (this build has no plugins yet: the band alone).
fn plugin_specs() -> Dict {
    let language = lp_i18n::language();
    let label = match language.code() {
        "de" => "Band",
        "fr" => "Bande",
        "es" => "Banda",
        "pl" => "Pasmo",
        "pt" => "Faixa",
        "it" => "Fascia",
        "ru" => "Лента",
        "ja" => "帯",
        _ => "Band",
    };
    let built_in = lp_i18n::t_format("{name} (built in)", &[("name", Value::from(label))])
        .unwrap_or_else(|why| why.msg);
    let mut options = Dict::default();
    options.insert(
        "roll_in".into(),
        Value::List(vec![Value::from("number"), Value::Float(0.45)]),
    );
    options.insert(
        "roll_out".into(),
        Value::List(vec![Value::from("number"), Value::Float(0.32)]),
    );
    let mut band = Dict::default();
    band.insert("label".into(), Value::Str(built_in));
    band.insert("options".into(), Value::Dict(options));
    let mut toasts = Dict::default();
    toasts.insert("band".into(), Value::Dict(band));
    let mut out = Dict::default();
    out.insert("widgets".into(), Value::Dict(Dict::default()));
    out.insert("widget_labels".into(), Value::Dict(Dict::default()));
    out.insert("presets".into(), Value::List(Vec::new()));
    out.insert("screens".into(), Value::Dict(Dict::default()));
    out.insert("toasts".into(), Value::Dict(toasts));
    out
}

/// _finish_types: the widget types each finish draws (any other is drawn flat); a module
/// counts where its finish reaches one of its parts.
fn finish_types(types: &[String]) -> Value {
    let mut found = Dict::default();
    for (name, drawn) in tables::FINISH_TYPES {
        let mut kinds: Vec<Value> = types
            .iter()
            .filter(|kind| *kind != "module" && drawn.contains(&kind.as_str()))
            .map(|kind| Value::Str(kind.clone()))
            .collect();
        if tables::PART_TYPES.iter().any(|part| drawn.contains(part)) {
            kinds.push(Value::from("module"));
        }
        found.insert((*name).to_string(), Value::List(kinds));
    }
    Value::Dict(found)
}

/// _specs: what the editor knows of themes.
pub fn specs(env: &Env, places: &Places) -> Value {
    let mut presets = presets();
    if let Some(Value::List(list)) = presets.get_mut("presets") {
        for preset in list.iter_mut() {
            if let Value::Dict(p) = preset {
                if let Some(Value::Str(name)) = p.get("name") {
                    let said = lp_i18n::t(name);
                    p.insert("name".into(), Value::Str(said));
                }
            }
        }
    }
    let models: Vec<Value> = lp_devices::models::MODELS
        .iter()
        .map(|m| {
            let Value::Dict(mut d) = m.to_dict() else {
                return Value::None;
            };
            let default = lp_theme::defaults::default_theme(Some(m), places);
            d.insert("default_theme".into(), Value::from(default));
            for said in ["notes", "limits"] {
                if let Some(Value::Str(text)) = d.get(said) {
                    if !text.is_empty() {
                        let text = lp_i18n::t(text);
                        d.insert(said.into(), Value::Str(text));
                    }
                }
            }
            Value::Dict(d)
        })
        .collect();
    let mut widgets: Dict = tables::WIDGETS
        .iter()
        .map(|(kind, table)| ((*kind).to_string(), fields(table)))
        .collect();
    let plugins = plugin_specs();
    if let Some(Value::Dict(own)) = plugins.get("widgets") {
        widgets.extend(own.clone());
    }
    if let (Some(Value::List(list)), Some(Value::List(more))) =
        (presets.get_mut("presets"), plugins.get("presets"))
    {
        list.extend(more.iter().cloned());
    }
    let types: Vec<String> = widgets.keys().cloned().collect();
    let mut enum_labels: Dict = lp_render::fan_looks::labels()
        .iter()
        .map(|(name, label)| ((*name).to_string(), Value::from(*label)))
        .collect();
    enum_labels.insert("theme".into(), Value::from("the theme's"));
    enum_labels.insert("own".into(), Value::from("the look's own"));
    let primary: Dict = tables::PRIMARY_FIELDS
        .iter()
        .map(|(kind, keys)| ((*kind).to_string(), strs(keys)))
        .collect();
    let look: Dict = tables::LOOK_SPECS
        .iter()
        .map(|(name, table)| ((*name).to_string(), fields(table)))
        .collect();
    let mut look_ranges = Dict::default();
    for (block, key, low, high) in tables::LOOK_RANGES {
        let entry = look_ranges
            .entry((*block).to_string())
            .or_insert_with(|| Value::Dict(Dict::default()));
        if let Value::Dict(d) = entry {
            d.insert(
                (*key).to_string(),
                Value::List(vec![Value::int(*low), Value::int(*high)]),
            );
        }
    }
    let protocols: Dict = lp_devices::models::PROTOCOLS
        .iter()
        .map(|(id, label)| ((*id).to_string(), Value::from(*label)))
        .collect();
    let fonts: Vec<Value> = env
        .builtin_fonts()
        .into_iter()
        .map(|(name, _)| Value::Str(format!("{}{name}", tables::BUILTIN_PREFIX)))
        .collect();
    let mut out = Dict::default();
    out.insert("version".into(), Value::from(lp_py::VERSION));
    out.insert("widgets".into(), Value::Dict(widgets));
    for key in ["widget_labels", "screens", "toasts"] {
        out.insert(key.into(), plugins.get(key).cloned().unwrap_or(Value::None));
    }
    out.insert("toast_anchors".into(), strs(tables::TOAST_ANCHORS));
    out.insert("common".into(), fields(tables::COMMON));
    out.insert("effect_fields".into(), strs(tables::EFFECT_FIELDS));
    out.insert("enum_labels".into(), Value::Dict(enum_labels));
    out.insert("primary".into(), Value::Dict(primary));
    out.insert("finishes".into(), finish_types(&types));
    out.insert("look".into(), Value::Dict(look));
    out.insert("look_ranges".into(), Value::Dict(look_ranges));
    out.insert("models".into(), Value::List(models));
    out.insert("protocols".into(), Value::Dict(protocols));
    out.insert("fonts".into(), Value::List(fonts));
    out.insert("default_font".into(), Value::from(tables::DEFAULT_FONT));
    out.insert("icons".into(), strs(tables::ICON_NAMES));
    out.insert(
        "modules".into(),
        Value::Dict(lp_theme::modules::info::module_info()),
    );
    out.extend(presets);
    Value::Dict(out)
}

/// The building blocks' widgets with their colour tokens made the palette's entries, else the
/// defaults.
fn resolved(value: &Value, palette: &Dict, tokens: &Dict) -> Value {
    match value {
        Value::Str(s) if s.starts_with('$') => {
            let name = &s[1..];
            match palette.contains_key(name) {
                true => Value::Str(format!("@{name}")),
                false => tokens.get(name).cloned().unwrap_or(Value::from("#ffffff")),
            }
        }
        Value::List(l) => Value::List(l.iter().map(|v| resolved(v, palette, tokens)).collect()),
        Value::Dict(d) => Value::Dict(
            d.iter()
                .map(|(k, v)| (k.clone(), resolved(v, palette, tokens)))
                .collect(),
        ),
        other => other.clone(),
    }
}

/// A whole number of a block's (0 for anything else).
fn small(value: &Value) -> i64 {
    match value {
        Value::Int(n) => i64::try_from(n).unwrap_or(0),
        Value::Float(f) => *f as i64,
        Value::Bool(b) => i64::from(*b),
        _ => 0,
    }
}

/// preset_previews: each building block drawn in the given palette, for the library (PNGs,
/// base64); a block whose theme does not parse is left out.
pub fn preset_previews(
    palette: &Value,
    symbols: &str,
    env: &Env,
) -> Result<Value, crate::drawings::Refused> {
    let palette = match palette {
        Value::Dict(d) => d.clone(),
        _ => Dict::default(),
    };
    let all = presets();
    let tokens = match all.get("tokens") {
        Some(Value::Dict(d)) => d.clone(),
        _ => Dict::default(),
    };
    let list = match all.get("presets") {
        Some(Value::List(l)) => l.clone(),
        _ => Vec::new(),
    };
    let pad = 8i64;
    let mut previews = Dict::default();
    for preset in &list {
        let Value::Dict(preset) = preset else {
            continue;
        };
        let size: Vec<i64> = match preset.get("size") {
            Some(Value::List(l)) => l.iter().map(small).map(|v| (v + 2 * pad).max(16)).collect(),
            _ => vec![16, 16],
        };
        let widgets: Vec<Value> = match preset
            .get("widgets")
            .map(|w| resolved(w, &palette, &tokens))
        {
            Some(Value::List(l)) => l
                .into_iter()
                .map(|w| match w {
                    Value::Dict(mut d) => {
                        for axis in ["x", "y"] {
                            let at = d.get(axis).map_or(0, small);
                            d.insert(axis.into(), Value::int(at + pad));
                        }
                        Value::Dict(d)
                    }
                    other => other,
                })
                .collect(),
            _ => Vec::new(),
        };
        let mut display = Dict::default();
        display.insert("width".into(), Value::int(size[0]));
        display.insert("height".into(), Value::int(size[1]));
        let mut background = Dict::default();
        let ground = if palette.contains_key("bg") {
            "@bg"
        } else {
            "#0b1016"
        };
        background.insert("color".into(), Value::from(ground));
        let mut data = Dict::default();
        data.insert("format".into(), Value::from(tables::THEME_FORMAT));
        data.insert("display".into(), Value::Dict(display));
        data.insert("palette".into(), Value::Dict(palette.clone()));
        data.insert("background".into(), Value::Dict(background));
        data.insert("widgets".into(), Value::List(widgets));
        let Ok(theme) = lp_theme::parse_theme(&Value::Dict(data), None, env) else {
            continue; // (no preview for it)
        };
        let id = preset.get("id").map_or(String::new(), Value::str);
        previews.insert(id, crate::drawings::still_picture(theme, symbols, env)?);
    }
    Ok(Value::Dict(previews))
}
