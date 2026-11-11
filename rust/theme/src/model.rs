//! Theme format `libre-panel-theme/1` as theme/model.py reads it: a folder with a theme.json
//! and its pictures and fonts. Everything in a theme is untrusted: what is wrong is refused
//! with theme/model.py's message, and what Python itself would raise on the way (an int()
//! of NaN, a font that is no str) is raised as Python raises it, of the same kind and in its
//! words.

use std::cmp::Ordering;
use std::collections::HashSet;
use std::path::{Path, PathBuf};

use lp_py::value::compare;
use lp_py::{Dict, Value};
use num_bigint::BigInt;
use num_traits::{FromPrimitive, Zero};

use crate::paths;
use crate::plugins::PluginField;
use crate::specs::Field;
use crate::tables::*;

/// What Python raises, by its class.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Kind {
    /// ThemeError: the theme is refused, in theme/model.py's words
    Theme,
    Value,
    Overflow,
    Type,
    Attribute,
    Recursion,
    UnicodeDecode,
    Runtime,
    Key,
    ZeroDivision,
    /// MemoryError: what Python would make is too large to make (a set of more pairs than a
    /// computer holds)
    Memory,
    /// OSError, by its class: PermissionError, IsADirectoryError ...
    Os(&'static str),
    /// where Python counts with values that are no numbers ("a" + "b") and lp-py does not
    Unsupported,
}

impl Kind {
    /// The name of Python's class.
    pub fn name(&self) -> &'static str {
        match self {
            Kind::Theme => "ThemeError",
            Kind::Value => "ValueError",
            Kind::Overflow => "OverflowError",
            Kind::Type => "TypeError",
            Kind::Attribute => "AttributeError",
            Kind::Recursion => "RecursionError",
            Kind::UnicodeDecode => "UnicodeDecodeError",
            Kind::Runtime => "RuntimeError",
            Kind::Key => "KeyError",
            Kind::ZeroDivision => "ZeroDivisionError",
            Kind::Memory => "MemoryError",
            Kind::Os(name) => name,
            Kind::Unsupported => "Unsupported",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Error {
    pub kind: Kind,
    pub msg: String,
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.msg)
    }
}

impl From<lp_py::num::Error> for Error {
    fn from(e: lp_py::num::Error) -> Error {
        use lp_py::num::ErrorKind as K;
        let kind = match e.kind {
            K::Type => Kind::Type,
            K::ZeroDivision => Kind::ZeroDivision,
            K::Overflow => Kind::Overflow,
            K::Value => Kind::Value,
            K::Unsupported => Kind::Unsupported,
        };
        Error { kind, msg: e.msg }
    }
}

pub type Result<T> = std::result::Result<T, Error>;

fn refuse<T>(msg: impl Into<String>) -> Result<T> {
    Err(Error {
        kind: Kind::Theme,
        msg: msg.into(),
    })
}

fn raise<T>(kind: Kind, msg: impl Into<String>) -> Result<T> {
    Err(Error {
        kind,
        msg: msg.into(),
    })
}

/// Where the app's own files are (the bundled fonts), for checking the fonts a theme names.
#[derive(Clone, Debug)]
pub struct Env {
    pub fonts_dir: PathBuf,
}

impl Env {
    /// builtin_fonts(): the bundled font files by their stem, in the order of their paths.
    pub fn builtin_fonts(&self) -> Vec<(String, PathBuf)> {
        let mut found: Vec<PathBuf> = std::fs::read_dir(&self.fonts_dir)
            .map(|dir| {
                dir.filter_map(|e| e.ok().map(|e| e.path()))
                    .filter(|p| {
                        let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
                        if cfg!(windows) {
                            name.to_lowercase().ends_with(".ttf")
                        } else {
                            name.ends_with(".ttf")
                        }
                    })
                    .collect()
            })
            .unwrap_or_default();
        found.sort();
        found
            .into_iter()
            .map(|p| {
                let stem = p
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("")
                    .to_string();
                (stem, p)
            })
            .collect()
    }

    /// builtin_font_path: the bundled file a "builtin:Name" font is.
    pub fn builtin_font_path(&self, reference: &str) -> Option<PathBuf> {
        let name = reference.strip_prefix(BUILTIN_PREFIX)?;
        self.builtin_fonts()
            .into_iter()
            .find(|(stem, _)| stem == name)
            .map(|(_, p)| p)
    }
}

/// A theme as theme/model.py's Theme has it.
#[derive(Clone, Debug)]
pub struct Theme {
    pub name: String,
    pub width: i64,
    pub height: i64,
    pub widgets: Vec<Dict>,
    pub author: String,
    pub license: String,
    pub description: String,
    pub collection: String,
    pub background_color: String,
    pub background_image: Option<String>,
    pub background_gradient: Option<(String, String)>,
    pub background_direction: String,
    /// an int, of any size from 100 up
    pub refresh_ms: Value,
    pub model: String,
    pub palette: Dict,
    pub font: String,
    pub smoothing_ms: i64,
    pub screen: Option<Dict>,
    pub toast: Dict,
    pub grid: Option<Dict>,
    pub style: Option<Dict>,
    pub look: Dict,
    pub switches: Dict,
    pub detached: Dict,
    pub root: Option<PathBuf>,
    pub warnings: Vec<String>,
}

/// TOAST_DEFAULTS
pub fn toast_defaults() -> Dict {
    let mut d = Dict::new();
    d.insert("anchor".into(), "top-right".into());
    d.insert("seconds".into(), 4.0.into());
    d.insert("queue".into(), true.into());
    d.insert("off".into(), Value::List(Vec::new()));
    d.insert("style".into(), "".into());
    d.insert("options".into(), Value::Dict(Dict::new()));
    d
}

/// STYLE_DEFAULTS
pub fn style_defaults() -> Dict {
    STYLE_DEFAULTS
        .iter()
        .map(|(k, g)| (k.to_string(), g.value()))
        .collect()
}

impl Theme {
    /// orientation_of(width, height)
    pub fn orientation(&self) -> &'static str {
        lp_devices::models::orientation_of(self.width, self.height)
    }

    fn background(&self) -> Value {
        let mut b = Dict::new();
        b.insert("color".into(), self.background_color.clone().into());
        b.insert(
            "image".into(),
            self.background_image
                .clone()
                .map_or(Value::None, Value::from),
        );
        if let Some((from, to)) = &self.background_gradient {
            b.insert(
                "gradient".into(),
                Value::List(vec![from.clone().into(), to.clone().into()]),
            );
            if self.background_direction != "vertical" {
                b.insert(
                    "gradient_direction".into(),
                    self.background_direction.clone().into(),
                );
            }
        }
        Value::Dict(b)
    }

    /// to_dict(): the theme as theme.json holds it, as the app saves it.
    pub fn to_dict(&self) -> Value {
        let mut d = Dict::new();
        d.insert("format".into(), THEME_FORMAT.into());
        d.insert("name".into(), self.name.clone().into());
        d.insert("author".into(), self.author.clone().into());
        d.insert("license".into(), self.license.clone().into());
        d.insert("description".into(), self.description.clone().into());
        if !self.collection.is_empty() {
            d.insert("collection".into(), self.collection.clone().into());
        }
        let mut display = Dict::new();
        display.insert("model".into(), self.model.clone().into());
        display.insert("orientation".into(), self.orientation().into());
        display.insert("width".into(), self.width.into());
        display.insert("height".into(), self.height.into());
        d.insert("display".into(), Value::Dict(display));
        d.insert("palette".into(), Value::Dict(self.palette.clone()));
        d.insert("font".into(), self.font.clone().into());
        d.insert("background".into(), self.background());
        d.insert("refresh_ms".into(), self.refresh_ms.clone());
        let mut animation = Dict::new();
        animation.insert("smoothing_ms".into(), self.smoothing_ms.into());
        d.insert("animation".into(), Value::Dict(animation));
        let widgets = self.widgets.iter().cloned().map(Value::Dict).collect();
        d.insert("widgets".into(), Value::List(widgets));
        if let Some(grid) = &self.grid {
            d.insert("grid".into(), Value::Dict(grid.clone()));
        }
        if let Some(style) = &self.style {
            d.insert("style".into(), Value::Dict(style.clone()));
        }
        d.insert("look".into(), Value::Dict(self.look.clone()));
        if let Some(screen) = &self.screen {
            d.insert("screen".into(), Value::Dict(screen.clone()));
        }
        if !self.switches.is_empty() {
            d.insert("switches".into(), Value::Dict(self.switches.clone()));
        }
        if !self.detached.is_empty() {
            d.insert("detached".into(), Value::Dict(self.detached.clone()));
        }
        let defaults = toast_defaults();
        let toast: Dict = self
            .toast
            .iter()
            .filter(|(k, v)| defaults.get(*k) != Some(*v))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        if !toast.is_empty() {
            d.insert("toast".into(), Value::Dict(toast));
        }
        Value::Dict(d)
    }
}

/// `low <= value <= high` as Python compares (False for NaN).
fn between(value: &Value, low: &Value, high: &Value) -> bool {
    matches!(compare(low, value), Some(Ordering::Less | Ordering::Equal))
        && matches!(compare(value, high), Some(Ordering::Less | Ordering::Equal))
}

fn int(n: i64) -> Value {
    Value::int(n)
}

/// `re.match(r"^<first><rest>{0,most}$", text)`: Python's `$` also before a last line end.
fn matches_name(
    text: &str,
    first: impl Fn(char) -> bool,
    rest: impl Fn(char) -> bool,
    most: usize,
) -> bool {
    let body = text.strip_suffix('\n').unwrap_or(text);
    let mut chars = body.chars();
    match chars.next() {
        Some(c) if first(c) => {}
        _ => return false,
    }
    let tail: Vec<char> = chars.collect();
    tail.len() <= most && tail.iter().all(|&c| rest(c))
}

/// _PALETTE_NAME: `^[a-z][a-z0-9_-]{0,31}$`
fn palette_name(name: &str) -> bool {
    matches_name(
        name,
        |c| c.is_ascii_lowercase(),
        |c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-',
        31,
    )
}

/// valid_theme_name: `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` and no "..".
pub fn valid_theme_name(name: &str) -> bool {
    matches_name(
        name,
        |c| c.is_ascii_alphanumeric(),
        |c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'),
        63,
    ) && !name.contains("..")
}

/// resolve_asset: the asset `rel` in the theme folder `root`, refused if it is not there.
pub fn resolve_asset(root: &Path, rel: &Value) -> Result<PathBuf> {
    let rel = match rel {
        Value::Str(s) if !s.is_empty() => s,
        _ => return refuse("empty asset path"),
    };
    let second = rel.chars().nth(1);
    if second == Some(':') || rel.starts_with('/') || rel.starts_with('\\') {
        return refuse(format!(
            "asset path must be relative to the theme folder: {}",
            lp_py::repr(rel)
        ));
    }
    let base = paths::resolve(root)?;
    let target = paths::resolve(&paths::join(&base, rel))?;
    if !target.starts_with(&base) {
        return refuse(format!(
            "asset path leaves the theme folder: {}",
            lp_py::repr(rel)
        ));
    }
    Ok(target)
}

/// _check_color
fn check_color(value: Value, place: &str, optional: bool, palette: Option<&Dict>) -> Result<Value> {
    if matches!(value, Value::None) && optional {
        return Ok(Value::None);
    }
    let Value::Str(text) = &value else {
        return refuse(format!("{place}: expected a color string like '#22d3ee'"));
    };
    if text == COVER_COLOR {
        return Ok(value);
    }
    if let Some(name) = text.strip_prefix('@') {
        let Some(palette) = palette else {
            return refuse(format!(
                "{place}: palette references are not allowed here, use a colour"
            ));
        };
        if !palette.contains_key(name) {
            return refuse(format!(
                "{place}: {} is not in the theme palette",
                lp_py::repr(text)
            ));
        }
        return Ok(value);
    }
    if lp_pixels::colour::getrgb(text).is_err() {
        return refuse(format!("{place}: invalid color {}", lp_py::repr(text)));
    }
    Ok(value)
}

/// _check_font (`value` must be a str: Python's own error for anything else, as the app gets
/// it when a plugin's widget that is not installed names a font that is no str).
fn check_font(value: &Value, place: &str, root: Option<&Path>, env: &Env) -> Result<String> {
    let Value::Str(text) = value else {
        return raise(
            Kind::Attribute,
            format!(
                "'{}' object has no attribute 'startswith'",
                value.type_name()
            ),
        );
    };
    if text.starts_with(BUILTIN_PREFIX) {
        if env.builtin_font_path(text).is_none() {
            let names: Vec<String> = env
                .builtin_fonts()
                .into_iter()
                .map(|(n, _)| format!("{BUILTIN_PREFIX}{n}"))
                .collect();
            return refuse(format!(
                "{place}: unknown built-in font {} (available: {})",
                lp_py::repr(text),
                names.join(", ")
            ));
        }
    } else if !text.is_empty() {
        if let Some(root) = root {
            resolve_asset(root, value)?;
        }
    }
    Ok(text.clone())
}

/// int(x) of a float, as Python takes it (NaN and the infinities refused as Python refuses).
fn int_of_float(f: f64) -> Result<BigInt> {
    if f.is_nan() {
        return raise(Kind::Value, "cannot convert float NaN to integer");
    }
    if f.is_infinite() {
        return raise(Kind::Overflow, "cannot convert float infinity to integer");
    }
    Ok(BigInt::from_f64(f.trunc()).expect("a finite float"))
}

/// _coerce: the value a field of `kind` holds, or the theme refused.
pub fn coerce(kind: &str, value: Value, place: &str, palette: Option<&Dict>) -> Result<Value> {
    let optional = kind.ends_with('?');
    let base = kind.trim_end_matches('?');
    if optional && matches!(value, Value::None) {
        return Ok(Value::None);
    }
    match base {
        "int" => match value {
            Value::Int(n) => {
                // (math.isfinite of it: an int too large for a float is Python's OverflowError)
                if !num_traits::ToPrimitive::to_f64(&n).is_some_and(f64::is_finite) {
                    return raise(Kind::Overflow, "int too large to convert to float");
                }
                Ok(Value::Int(n))
            }
            Value::Float(f) if !f.is_finite() => refuse(format!(
                "{place}: expected an integer, not {}",
                lp_py::float_repr(f)
            )),
            Value::Float(f) => Ok(Value::Int(int_of_float(f)?)),
            _ => refuse(format!("{place}: expected an integer")),
        },
        "number" => match value {
            Value::Int(_) | Value::Float(_) => Ok(value),
            _ => refuse(format!("{place}: expected a number")),
        },
        "bool" => match value {
            Value::Bool(_) => Ok(value),
            _ => refuse(format!("{place}: expected true/false")),
        },
        "color" => check_color(value, place, optional, palette),
        "icon" => {
            if ICON_NAMES.iter().any(|&n| value == Value::from(n)) {
                Ok(value)
            } else {
                refuse(format!("{place}: unknown icon {}", value.repr()))
            }
        }
        _ if base.starts_with("enum:") => {
            let options: Vec<&str> = base[5..].split('|').collect();
            if options.iter().any(|&o| value == Value::from(o)) {
                Ok(value)
            } else {
                refuse(format!("{place}: must be one of {}", options.join(", ")))
            }
        }
        "box" => {
            let four = matches!(&value, Value::List(items) if items.len() == 4);
            if !four
                || value
                    .as_list()
                    .is_some_and(|items| items.iter().any(|v| !v.is_number()))
            {
                return refuse(format!("{place}: expected [x, y, w, h]"));
            }
            let mut boxed = Vec::with_capacity(4);
            for v in value.as_list().expect("a list") {
                boxed.push(match v {
                    Value::Int(n) => n.clone(),
                    Value::Float(f) => int_of_float(*f)?,
                    _ => unreachable!("checked above"),
                });
            }
            if boxed[2] <= BigInt::zero() || boxed[3] <= BigInt::zero() {
                return refuse(format!("{place}: its width and height must be above 0"));
            }
            Ok(Value::List(boxed.into_iter().map(Value::Int).collect()))
        }
        "rules" => {
            let Value::List(items) = value else {
                return refuse(format!("{place}: expected a list of rules"));
            };
            let mut rules = Vec::with_capacity(items.len());
            for (i, rule) in items.into_iter().enumerate() {
                let ok = matches!(&rule, Value::Dict(d) if d.contains_key("above") && d.contains_key("color"));
                if !ok {
                    return refuse(format!("{place}[{i}]: rule needs 'above' and 'color'"));
                }
                let Value::Dict(mut rule) = rule else {
                    unreachable!()
                };
                let above = coerce(
                    "number",
                    rule.shift_remove("above").expect("above"),
                    &format!("{place}[{i}].above"),
                    None,
                )?;
                let color = check_color(
                    rule.shift_remove("color").expect("color"),
                    &format!("{place}[{i}].color"),
                    false,
                    palette,
                )?;
                let mut kept = Dict::new();
                kept.insert("above".into(), above);
                kept.insert("color".into(), color);
                rules.push(Value::Dict(kept));
            }
            Ok(Value::List(rules))
        }
        // string, text, sensor, format, font, asset
        _ => match value {
            Value::Str(_) => Ok(value),
            _ => refuse(format!("{place}: expected a string")),
        },
    }
}

fn widget_fields(wtype: &str) -> Option<&'static [Field]> {
    WIDGETS
        .iter()
        .find(|(t, _)| *t == wtype)
        .map(|(_, fields)| *fields)
}

/// {**_COMMON, **WIDGET_SPECS[wtype]}: the common fields, then the type's own (one of the same
/// name in the common one's place).
fn spec_of(own: &'static [Field]) -> Vec<&'static Field> {
    let mut spec: Vec<&Field> = COMMON.iter().collect();
    for field in own {
        match spec.iter().position(|f| f.name == field.name) {
            Some(i) => spec[i] = field,
            None => spec.push(field),
        }
    }
    spec
}

/// A table's field as a plugin's is held.
fn owned(field: &Field) -> PluginField {
    PluginField {
        name: field.name.to_string(),
        kind: field.kind.to_string(),
        default: field.default.value(),
    }
}

/// _check_paths: fonts and assets named in a plugin's fields stay inside the theme folder too,
/// and a built-in font is one there is.
fn check_paths(
    spec: &[PluginField],
    values: &Dict,
    place: &str,
    root: Option<&Path>,
    env: &Env,
) -> Result<()> {
    for field in spec {
        let Some(value @ Value::Str(text)) = values.get(&field.name) else {
            continue;
        };
        if text.is_empty() {
            continue;
        }
        match field.kind.trim_end_matches('?') {
            "font" => {
                check_font(value, &format!("{place}.{}", field.name), root, env)?;
            }
            "asset" => {
                if let Some(root) = root {
                    resolve_asset(root, value)?;
                }
            }
            _ => {}
        }
    }
    Ok(())
}

fn get(d: &Dict, key: &str, default: Value) -> Value {
    d.get(key).cloned().unwrap_or(default)
}

/// normalize_widget: the widget `raw` as theme/model.py completes it, and its warnings.
pub fn normalize_widget(
    raw: &Value,
    index: usize,
    palette: Option<&Dict>,
    root: Option<&Path>,
    env: &Env,
) -> Result<(Dict, Vec<String>)> {
    let place = format!("widgets[{index}]");
    let Value::Dict(raw) = raw else {
        return refuse(format!("{place}: expected an object"));
    };
    let wtype = raw.get("type").cloned().unwrap_or(Value::None);
    let mut plugin = None;
    let spec: Vec<PluginField> = match &wtype {
        Value::Str(t) => match widget_fields(t) {
            Some(own) => spec_of(own).into_iter().map(owned).collect(),
            // a plugin's widget type
            None if t.contains('.') => match crate::plugins::fields("widgets", t) {
                Some(own) => {
                    let mut spec: Vec<PluginField> = COMMON.iter().map(owned).collect();
                    for field in &own {
                        match spec.iter().position(|f| f.name == field.name) {
                            Some(i) => spec[i] = field.clone(),
                            None => spec.push(field.clone()),
                        }
                    }
                    plugin = Some(own);
                    spec
                }
                None => return missing_plugin_widget(raw, t, index, &place, palette),
            },
            None => return refuse(format!("{place}: unknown widget type {}", wtype.repr())),
        },
        _ => return refuse(format!("{place}: unknown widget type {}", wtype.repr())),
    };
    let wtype = wtype.as_str().expect("a str").to_string();
    let mut widget = Dict::new();
    widget.insert("type".into(), wtype.clone().into());
    for field in &spec {
        let value = get(raw, &field.name, field.default.clone());
        let coerced = coerce(
            &field.kind,
            value,
            &format!("{place}.{}", field.name),
            palette,
        )?;
        widget.insert(field.name.clone(), coerced);
    }
    let field = |name: &str| widget.get(name).expect("a field of every widget");
    if !between(field("opacity"), &int(0), &int(1)) {
        return refuse(format!("{place}.opacity: must be between 0 and 1"));
    }
    if !between(field("glow"), &int(0), &int(1)) {
        return refuse(format!("{place}.glow: must be between 0 and 1"));
    }
    if !between(field("rotate"), &int(-360), &int(360)) {
        return refuse(format!("{place}.rotate: must be between -360 and 360"));
    }
    for key in ["glow_radius", "shadow_blur", "backdrop_blur", "blur"] {
        if let Some(v) = widget.get(key) {
            if !between(v, &int(0), &int(BLUR_MAX)) {
                return refuse(format!("{place}.{key}: must be between 0 and {BLUR_MAX}"));
            }
        }
    }
    if wtype == "bar" && !between(field("rising"), &int(0), &int(1)) {
        return refuse(format!("{place}.rising: must be between 0 and 1"));
    }
    // (`max_rpm <= 0`: NaN is not, so it passes, as in Python)
    if wtype == "fan"
        && matches!(
            compare(field("max_rpm"), &int(0)),
            Some(Ordering::Less | Ordering::Equal)
        )
    {
        return refuse(format!("{place}.max_rpm: must be above 0"));
    }
    if !field("id").truthy() {
        widget.insert("id".into(), format!("{wtype}-{}", index + 1).into());
    }
    if let Some(own) = &plugin {
        check_paths(own, &widget, &place, root, env)?;
    }
    let gone = GONE_FIELDS
        .iter()
        .find(|(t, _)| *t == wtype)
        .map_or(&[][..], |(_, g)| *g);
    let mut unknown: Vec<&String> = raw
        .keys()
        .filter(|k| {
            k.as_str() != "type"
                && !spec.iter().any(|f| f.name == k.as_str())
                && !gone.contains(&k.as_str())
        })
        .collect();
    unknown.sort();
    let warnings = unknown
        .into_iter()
        .map(|k| format!("{place}: ignoring unknown field {}", lp_py::repr(k)))
        .collect();
    Ok((widget, warnings))
}

/// _missing_plugin_widget: kept as it is, so saving the theme loses nothing; it is not drawn.
fn missing_plugin_widget(
    raw: &Dict,
    wtype: &str,
    index: usize,
    place: &str,
    palette: Option<&Dict>,
) -> Result<(Dict, Vec<String>)> {
    let mut widget = raw.clone();
    for field in COMMON {
        let value = get(raw, field.name, field.default.value());
        let coerced = coerce(
            field.kind,
            value,
            &format!("{place}.{}", field.name),
            palette,
        )?;
        widget.insert(field.name.into(), coerced);
    }
    if !widget["id"].truthy() {
        widget.insert("id".into(), format!("{wtype}-{}", index + 1).into());
    }
    let warning = format!(
        "{place}: widget type {} needs a plugin that is not installed",
        lp_py::repr(wtype)
    );
    Ok((widget, vec![warning]))
}

/// _plugin_options: a plugin's options with its defaults, checked against its schema. The
/// toast styles that come with the app are the only plugins here (R10 brings the others).
fn plugin_options(
    kind: &str,
    name: &str,
    given: Value,
    place: &str,
    palette: &Dict,
    warnings: &mut Vec<String>,
) -> Result<Dict> {
    let Value::Dict(given) = given else {
        return refuse(format!("{place}.options must be an object"));
    };
    let schema: Option<Vec<PluginField>> = match kind {
        "toasts" => TOAST_STYLES
            .iter()
            .find(|(n, _)| *n == name)
            .map(|(_, o)| o.iter().map(owned).collect()),
        _ => None,
    }
    .or_else(|| crate::plugins::fields(kind, name));
    let Some(schema) = schema else {
        warnings.push(format!(
            "{place} {} needs a plugin that is not installed",
            lp_py::repr(name)
        ));
        return Ok(given);
    };
    let mut options = Dict::new();
    for field in schema {
        let value = get(&given, &field.name, field.default);
        let coerced = coerce(
            &field.kind,
            value,
            &format!("{place}.options.{}", field.name),
            Some(palette),
        )?;
        options.insert(field.name, coerced);
    }
    let mut unknown: Vec<&String> = given.keys().filter(|k| !options.contains_key(*k)).collect();
    unknown.sort();
    for k in unknown {
        warnings.push(format!(
            "{place}.options: ignoring unknown option {}",
            lp_py::repr(k)
        ));
    }
    Ok(options)
}

/// `raw.get(key, {}) or {}`
fn object_or_empty(raw: &Dict, key: &str) -> Value {
    match raw.get(key) {
        Some(v) if v.truthy() => v.clone(),
        _ => Value::Dict(Dict::new()),
    }
}

fn parse_toast(raw: Option<&Value>, palette: &Dict, warnings: &mut Vec<String>) -> Result<Dict> {
    let mut toast = toast_defaults();
    let raw = match raw {
        None | Some(Value::None) => return Ok(toast),
        Some(Value::Dict(d)) => d,
        Some(_) => return refuse("toast must be an object"),
    };
    let anchors = format!("enum:{}", TOAST_ANCHORS.join("|"));
    let anchor = coerce(
        &anchors,
        get(raw, "anchor", "top-right".into()),
        "toast.anchor",
        None,
    )?;
    toast.insert("anchor".into(), anchor);
    let seconds = coerce(
        "number",
        get(raw, "seconds", 4.0.into()),
        "toast.seconds",
        None,
    )?;
    if !between(&seconds, &Value::Float(0.5), &int(60)) {
        return refuse("toast.seconds: must be between 0.5 and 60");
    }
    toast.insert("seconds".into(), seconds);
    let queue = coerce("bool", get(raw, "queue", true.into()), "toast.queue", None)?;
    toast.insert("queue".into(), queue);
    let off = get(raw, "off", Value::List(Vec::new()));
    match &off {
        Value::List(kinds) if kinds.iter().all(|k| matches!(k, Value::Str(_))) => {}
        _ => return refuse("toast.off: expected a list of kinds, e.g. [\"music\"]"),
    }
    toast.insert("off".into(), off);
    let style = coerce("string", get(raw, "style", "".into()), "toast.style", None)?;
    toast.insert("style".into(), style.clone());
    if style.truthy() {
        let given = object_or_empty(raw, "options");
        let name = style.as_str().expect("a str");
        let options = plugin_options("toasts", name, given, "toast", palette, warnings)?;
        toast.insert("options".into(), Value::Dict(options));
    }
    Ok(toast)
}

fn parse_screen(
    raw: Option<&Value>,
    palette: &Dict,
    warnings: &mut Vec<String>,
) -> Result<Option<Dict>> {
    let raw = match raw {
        None | Some(Value::None) => return Ok(None),
        Some(Value::Dict(d)) => d,
        Some(_) => return refuse("screen must be an object with a name"),
    };
    let name = coerce("string", get(raw, "name", "".into()), "screen.name", None)?;
    if !name.truthy() {
        return refuse("screen.name: missing");
    }
    let given = object_or_empty(raw, "options");
    let options = plugin_options(
        "screens",
        name.as_str().expect("a str"),
        given,
        "screen",
        palette,
        warnings,
    )?;
    let mut screen = Dict::new();
    screen.insert("name".into(), name);
    screen.insert("options".into(), Value::Dict(options));
    Ok(Some(screen))
}

fn parse_grid(raw: Option<&Value>) -> Result<Option<Dict>> {
    let raw = match raw {
        None | Some(Value::None) => return Ok(None),
        Some(Value::Dict(d)) => d,
        Some(_) => return refuse("grid must be an object"),
    };
    let mut grid = Dict::new();
    for (key, default) in GRID_DEFAULTS {
        let value = coerce(
            "int",
            get(raw, key, int(*default)),
            &format!("grid.{key}"),
            None,
        )?;
        let most = if matches!(*key, "columns" | "rows") {
            GRID_CELLS
        } else {
            200
        };
        if !between(&value, &int(0), &int(most)) {
            return refuse(format!("grid.{key}: out of range"));
        }
        grid.insert(key.to_string(), value);
    }
    Ok(Some(grid))
}

fn parse_style(raw: Option<&Value>, root: Option<&Path>, env: &Env) -> Result<Option<Dict>> {
    let raw = match raw {
        None | Some(Value::None) => return Ok(None),
        Some(Value::Dict(d)) => d,
        Some(_) => return refuse("style must be an object"),
    };
    let defaults = style_defaults();
    let cards = format!("enum:{}", CARDS.join("|"));
    let backdrops = format!("enum:{}", BACKDROPS.join("|"));
    let kinds: [(&str, &str); 7] = [
        ("card", &cards),
        ("radius", "int"),
        ("glow", "number"),
        ("backdrop", &backdrops),
        ("display_font", "string"),
        ("text_font", "string"),
        ("card_opacity", "number"),
    ];
    let mut style = Dict::new();
    for (key, kind) in kinds {
        let value = get(raw, key, defaults[key].clone());
        style.insert(
            key.into(),
            coerce(kind, value, &format!("style.{key}"), None)?,
        );
    }
    if !between(&style["glow"], &int(0), &int(1)) {
        return refuse("style.glow: must be between 0 and 1");
    }
    if !between(
        &style["card_opacity"],
        &Value::Float(CARD_OPACITY_MIN),
        &int(1),
    ) {
        return refuse(format!(
            "style.card_opacity: must be between {} and 1",
            lp_py::float_repr(CARD_OPACITY_MIN)
        ));
    }
    if !between(&style["radius"], &int(0), &int(100)) {
        return refuse("style.radius: must be between 0 and 100");
    }
    for key in ["display_font", "text_font"] {
        let given = &style[key];
        let font = if given.truthy() {
            given.clone()
        } else {
            DEFAULT_FONT.into()
        };
        let checked = check_font(&font, &format!("style.{key}"), root, env)?;
        style.insert(key.into(), checked.into());
    }
    Ok(Some(style))
}

fn parse_look(raw: Option<&Value>, palette: &Dict, warnings: &mut Vec<String>) -> Result<Dict> {
    let raw = match raw {
        None | Some(Value::None) => return Ok(Dict::new()),
        Some(Value::Dict(d)) => d,
        Some(_) => return refuse("look must be an object"),
    };
    let mut look = Dict::new();
    for (name, given) in raw {
        let spec = LOOK_SPECS.iter().find(|(n, _)| n == name).map(|(_, s)| *s);
        if GONE_LOOK.contains(&name.as_str()) {
            continue;
        }
        let Some(spec) = spec else {
            warnings.push(format!(
                "look: ignoring unknown block {}",
                lp_py::repr(name)
            ));
            look.insert(name.clone(), given.clone());
            continue;
        };
        let given = match given {
            Value::None | Value::Bool(false) => continue,
            Value::Bool(true) => Dict::new(),
            Value::Dict(d) => d.clone(),
            _ => return refuse(format!("look.{name} must be an object")),
        };
        let mut block = Dict::new();
        for field in spec {
            let value = get(&given, field.name, field.default.value());
            let value = coerce(
                field.kind,
                value,
                &format!("look.{name}.{}", field.name),
                Some(palette),
            )?;
            let range = LOOK_RANGES
                .iter()
                .find(|(b, k, _, _)| b == name && *k == field.name);
            if let Some((_, _, low, high)) = range {
                if !between(&value, &int(*low), &int(*high)) {
                    return refuse(format!(
                        "look.{name}.{}: must be between {low} and {high}",
                        field.name
                    ));
                }
            }
            block.insert(field.name.into(), value);
        }
        let mut unknown: Vec<&String> = given
            .keys()
            .filter(|k| !spec.iter().any(|f| f.name == k.as_str()))
            .collect();
        unknown.sort();
        for k in unknown {
            warnings.push(format!(
                "look.{name}: ignoring unknown field {}",
                lp_py::repr(k)
            ));
        }
        look.insert(name.clone(), Value::Dict(block));
    }
    Ok(look)
}

/// The finishes that draw a widget of this type (render/finish.py's registry).
fn finish_draws(name: &Value, widget_type: &str) -> bool {
    FINISH_TYPES
        .iter()
        .any(|(f, types)| *name == Value::from(*f) && types.contains(&widget_type))
}

/// Python hashes a finish's name to look it up: a list or a dict cannot be.
fn hashable(name: &Value) -> Result<()> {
    match name {
        Value::List(_) | Value::Dict(_) => raise(
            Kind::Type,
            format!("unhashable type: '{}'", name.type_name()),
        ),
        _ => Ok(()),
    }
}

/// render.finish.supports: whether the widget is drawn with the finish it names.
pub fn supports(widget: &Dict) -> Result<bool> {
    let name = get(widget, "finish", "flat".into());
    if name == Value::from("flat") || name == Value::from(AUTO) {
        return Ok(true);
    }
    hashable(&name)?;
    let wtype = widget.get("type").and_then(Value::as_str).unwrap_or("");
    if wtype == "module" {
        return Ok(PART_TYPES.iter().any(|part| finish_draws(&name, part)));
    }
    if !finish_draws(&name, wtype) {
        return Ok(false);
    }
    // the lit icon: weather icons only
    if name == Value::from("lit") && wtype == "icon" {
        let icon = get(widget, "icon", Value::None);
        return Ok(WEATHER_ICONS.iter().any(|&w| icon == Value::from(w)));
    }
    Ok(true)
}

/// _finish_warnings: one warning per widget type (or icon) whose finish is not there.
fn finish_warnings(widgets: &[Dict], warnings: &mut Vec<String>) -> Result<()> {
    let plain = |w: &Dict| {
        let name = get(w, "finish", "flat".into());
        name == Value::from("flat") || name == Value::from(AUTO)
    };
    if widgets.iter().all(plain) {
        return Ok(());
    }
    let mut told: Vec<(Value, String)> = Vec::new();
    for widget in widgets {
        if plain(widget) || supports(widget)? {
            continue;
        }
        let name = get(widget, "finish", "flat".into());
        let wtype = widget.get("type").map(Value::str).unwrap_or_default();
        let what = if wtype == "icon" {
            format!("the icon {}", widget["icon"].repr())
        } else {
            wtype
        };
        if !told.iter().any(|(n, w)| *n == name && *w == what) {
            warnings.push(format!(
                "finish {} is not available for {what}; drawn flat",
                name.repr()
            ));
            told.push((name, what));
        }
    }
    Ok(())
}

fn parse_switches(raw: Option<&Value>) -> Result<Dict> {
    let raw = match raw {
        None | Some(Value::None) => return Ok(Dict::new()),
        Some(Value::Dict(d)) if d.len() <= MAX_SWITCHES => d,
        Some(_) => {
            return refuse(format!(
                "switches: an object of at most {MAX_SWITCHES} name: {{label, on}}"
            ))
        }
    };
    let mut switches = Dict::new();
    for (name, spec) in raw {
        if !palette_name(name) {
            return refuse(format!(
                "switches: invalid name {} (use a-z, 0-9, - and _)",
                lp_py::repr(name)
            ));
        }
        let Value::Dict(spec) = spec else {
            return refuse(format!(
                "switches.{name}: {{\"label\": \"...\", \"on\": true}}"
            ));
        };
        let label = coerce(
            "string",
            get(spec, "label", name.clone().into()),
            &format!("switches.{name}.label"),
            None,
        )?;
        let on = coerce(
            "bool",
            get(spec, "on", true.into()),
            &format!("switches.{name}.on"),
            None,
        )?;
        let mut switch = Dict::new();
        switch.insert("label".into(), label);
        switch.insert("on".into(), on);
        switches.insert(name.clone(), Value::Dict(switch));
    }
    Ok(switches)
}

fn parse_detached(
    raw: Option<&Value>,
    palette: &Dict,
    root: Option<&Path>,
    env: &Env,
    warnings: &mut Vec<String>,
) -> Result<Dict> {
    let Some(Value::Dict(raw)) = raw else {
        return Ok(Dict::new());
    };
    let mut kept = Dict::new();
    for (name, module) in raw.iter().take(MAX_DETACHED) {
        let is_module =
            matches!(module, Value::Dict(d) if d.get("type") == Some(&Value::from("module")));
        if !is_module {
            continue;
        }
        match normalize_widget(module, 0, Some(palette), root, env) {
            Ok((widget, _)) => {
                kept.insert(name.clone(), Value::Dict(widget));
            }
            Err(e) if e.kind == Kind::Theme => warnings.push(format!("detached.{name}: {}", e.msg)),
            Err(e) => return Err(e),
        }
    }
    Ok(kept)
}

fn string_field(data: &Dict, key: &str, default: &str) -> Result<String> {
    let value = coerce("string", get(data, key, default.into()), key, None)?;
    Ok(value.as_str().expect("a str").to_string())
}

/// parse_theme: a theme from theme.json's data, its folder `root` for its pictures and fonts.
pub fn parse_theme(data: &Value, root: Option<&Path>, env: &Env) -> Result<Theme> {
    let Value::Dict(data) = data else {
        return refuse("theme.json must contain an object");
    };
    let format = get(data, "format", Value::None);
    if format != Value::from(THEME_FORMAT) {
        return refuse(format!(
            "unsupported theme format {}, expected {THEME_FORMAT}",
            format.repr()
        ));
    }
    let display = match get(data, "display", Value::Dict(Dict::new())) {
        Value::Dict(d) => d,
        _ => return refuse("display must be an object"),
    };
    let model = coerce(
        "string",
        get(&display, "model", "custom".into()),
        "display.model",
        None,
    )?;
    let model_id = model.as_str().expect("a str").to_string();
    let panel = lp_devices::models::find_model(&model_id);
    if model_id != "custom" && panel.is_none() {
        return refuse(format!(
            "display.model: unknown panel model {}",
            lp_py::repr(&model_id)
        ));
    }
    let (width, height) = match panel {
        Some(panel) => {
            let orientation = coerce(
                "enum:landscape|portrait",
                get(&display, "orientation", "landscape".into()),
                "display.orientation",
                None,
            )?;
            let orientation = orientation.as_str().expect("a str");
            let (width, height) = panel.size(orientation);
            for (key, expected) in [("width", width), ("height", height)] {
                if let Some(given) = display.get(key) {
                    if *given != int(expected) {
                        return refuse(format!(
                            "display.{key} is {} but {} in {orientation} is {width}x{height}",
                            given.str(),
                            panel.label()
                        ));
                    }
                }
            }
            (int(width), int(height))
        }
        None => (
            coerce(
                "int",
                get(&display, "width", Value::None),
                "display.width",
                None,
            )?,
            coerce(
                "int",
                get(&display, "height", Value::None),
                "display.height",
                None,
            )?,
        ),
    };
    if !(between(&width, &int(16), &int(4096)) && between(&height, &int(16), &int(4096))) {
        return refuse("display size must be between 16 and 4096 pixels");
    }
    let small = |v: &Value| v.to_f64().expect("a checked size") as i64;
    let (width, height) = (small(&width), small(&height));
    let raw_palette = object_or_empty(data, "palette");
    let Value::Dict(raw_palette) = raw_palette else {
        return refuse("palette must be an object of name: color");
    };
    let mut palette = Dict::new();
    for (name, color) in &raw_palette {
        if !palette_name(name) {
            return refuse(format!(
                "palette: invalid name {} (use a-z, 0-9, - and _)",
                lp_py::repr(name)
            ));
        }
        let checked = check_color(color.clone(), &format!("palette.{name}"), false, None)?;
        palette.insert(name.clone(), checked);
    }
    let font = coerce(
        "string",
        get(data, "font", DEFAULT_FONT.into()),
        "font",
        None,
    )?;
    let font = if font.truthy() {
        font
    } else {
        DEFAULT_FONT.into()
    };
    let font = check_font(&font, "font", root, env)?;
    let animation = match object_or_empty(data, "animation") {
        Value::Dict(d) => d,
        _ => return refuse("animation must be an object"),
    };
    let smoothing = coerce(
        "int",
        get(&animation, "smoothing_ms", int(400)),
        "animation.smoothing_ms",
        None,
    )?;
    if !between(&smoothing, &int(0), &int(5000)) {
        return refuse("animation.smoothing_ms: must be between 0 and 5000");
    }
    let smoothing_ms = small(&smoothing);
    let background = match object_or_empty(data, "background") {
        Value::Dict(d) => d,
        _ => return refuse("background must be an object, e.g. {\"color\": \"#101418\"}"),
    };
    let bg_color = check_color(
        get(&background, "color", "#000000".into()),
        "background.color",
        false,
        Some(&palette),
    )?;
    let bg_image = match background.get("image") {
        None | Some(Value::None) => None,
        Some(image) => {
            let image = coerce("string", image.clone(), "background.image", None)?;
            if let Some(root) = root {
                resolve_asset(root, &image)?;
            }
            Some(image.as_str().expect("a str").to_string())
        }
    };
    let bg_gradient = match background.get("gradient") {
        None | Some(Value::None) => None,
        Some(Value::List(pair)) if pair.len() == 2 => {
            let from = check_color(
                pair[0].clone(),
                "background.gradient",
                false,
                Some(&palette),
            )?;
            let to = check_color(
                pair[1].clone(),
                "background.gradient",
                false,
                Some(&palette),
            )?;
            Some((from.str(), to.str()))
        }
        Some(_) => {
            return refuse("background.gradient: two colours, e.g. [\"#0A0E13\", \"#06090D\"]")
        }
    };
    let direction = get(&background, "gradient_direction", "vertical".into());
    if direction != Value::from("vertical") && direction != Value::from("horizontal") {
        return refuse("background.gradient_direction: \"vertical\" or \"horizontal\"");
    }
    let refresh_ms = coerce(
        "int",
        get(data, "refresh_ms", int(1000)),
        "refresh_ms",
        None,
    )?;
    if compare(&refresh_ms, &int(100)) == Some(Ordering::Less) {
        return refuse("refresh_ms must be at least 100");
    }

    let raw_widgets = match get(data, "widgets", Value::List(Vec::new())) {
        Value::List(items) => items,
        _ => return refuse("widgets must be a list"),
    };
    let mut widgets = Vec::with_capacity(raw_widgets.len());
    let mut warnings = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for (i, raw) in raw_widgets.iter().enumerate() {
        let (widget, found) = normalize_widget(raw, i, Some(&palette), root, env)?;
        let id = widget["id"].str();
        if seen.contains(&id) {
            return refuse(format!(
                "widgets[{i}]: duplicate id {}",
                widget["id"].repr()
            ));
        }
        seen.insert(id);
        if let Some(font) = widget.get("font").filter(|f| f.truthy()) {
            check_font(font, &format!("widgets[{i}].font"), root, env)?;
        }
        if let (Some(root), Some(src)) = (root, widget.get("src").filter(|s| s.truthy())) {
            let Value::Str(path) = src else {
                return raise(
                    Kind::Attribute,
                    format!("'{}' object has no attribute 'startswith'", src.type_name()),
                );
            };
            if !path.starts_with('@') {
                resolve_asset(root, src)?; // "@media.cover": a picture a sensor has
            }
        }
        widgets.push(widget);
        warnings.extend(found);
    }
    finish_warnings(&widgets, &mut warnings)?;

    let name = string_field(data, "name", "Untitled")?;
    let author = string_field(data, "author", "")?;
    let license = string_field(data, "license", "")?;
    let description = string_field(data, "description", "")?;
    let collection = string_field(data, "collection", "")?;
    let grid = parse_grid(data.get("grid"))?;
    let style = parse_style(data.get("style"), root, env)?;
    let screen = parse_screen(data.get("screen"), &palette, &mut warnings)?;
    let toast = parse_toast(data.get("toast"), &palette, &mut warnings)?;
    let look = parse_look(data.get("look"), &palette, &mut warnings)?;
    let switches = parse_switches(data.get("switches"))?;
    let detached = parse_detached(data.get("detached"), &palette, root, env, &mut warnings)?;
    Ok(Theme {
        name,
        author,
        license,
        description,
        collection,
        width,
        height,
        background_color: bg_color.str(),
        background_image: bg_image,
        background_gradient: bg_gradient,
        background_direction: direction.str(),
        refresh_ms,
        model: model_id,
        palette,
        font,
        smoothing_ms,
        widgets,
        grid,
        style,
        screen,
        toast,
        look,
        switches,
        detached,
        root: root.map(Path::to_path_buf),
        warnings,
    })
}

/// The OSError Python raises where opening `path` failed so: `[Errno 21] Is a directory:
/// '/x/theme.json'`, of the class of its errno.
fn os_error(e: &std::io::Error, path: &Path) -> Error {
    let (class, msg) = os_error_of(e, path);
    Error {
        kind: Kind::Os(class),
        msg,
    }
}

/// The text of the ValueError Python's resolve_asset raises for a name with a NUL in it
/// (Path.resolve's os.lstat, os.stat on Windows), as CPython 3.12 words it now (its older
/// releases said "embedded null byte").
pub const NUL_IN_PATH: &str = if cfg!(windows) {
    "stat: embedded null character in path"
} else {
    "lstat: embedded null character in path"
};

/// The class and the text of the OSError Python's open(path) raises where opening it failed so
/// (`[Errno 2] No such file or directory: '/x/a.png'`). On Windows, the C runtime's errno for
/// the system's error, as _wopen sets it, and its text there (a folder opened is "access is
/// denied" to the system: errno 13).
pub fn os_error_of(e: &std::io::Error, path: &Path) -> (&'static str, String) {
    let code = e.raw_os_error().unwrap_or(0);
    let (errno, text) = if cfg!(windows) {
        let errno = crt_errno(code);
        (errno, crt_strerror(errno).to_string())
    } else {
        let full = std::io::Error::from_raw_os_error(code).to_string();
        let text = full.split(" (os error").next().unwrap_or(&full).to_string();
        (code, text)
    };
    let class = match errno {
        2 => "FileNotFoundError",
        13 => "PermissionError",
        20 => "NotADirectoryError",
        21 => "IsADirectoryError",
        _ => "OSError",
    };
    let msg = format!(
        "[Errno {errno}] {text}: {}",
        lp_py::repr(&path.to_string_lossy())
    );
    (class, msg)
}

/// The Universal C runtime's errno for a Windows error (__acrt_errno_map_os_error: its table,
/// then the ranges it gives EACCES and ENOEXEC, else EINVAL).
fn crt_errno(code: i32) -> i32 {
    match code {
        2 | 3 | 15 | 18 | 53 | 67 | 161 | 206 => 2, // ENOENT
        5 | 16 | 19..=36 | 65 | 82 | 83 | 108 | 132 | 158 | 167 => 13, // EACCES
        4 => 24,                                    // EMFILE
        6 | 114 | 130 => 9,                         // EBADF
        7..=9 | 1816 => 12,                         // ENOMEM
        10 => 7,                                    // E2BIG
        11 | 188..=202 => 8,                        // ENOEXEC
        17 => 18,                                   // EXDEV
        80 | 183 => 17,                             // EEXIST
        89 | 164 | 215 => 11,                       // EAGAIN
        109 => 32,                                  // EPIPE
        112 => 28,                                  // ENOSPC
        128 | 129 => 10,                            // ECHILD
        145 => 41,                                  // ENOTEMPTY
        _ => 22,                                    // EINVAL
    }
}

/// The C runtime's text of an errno (_sys_errlist), which CPython gives an OSError on Windows.
fn crt_strerror(errno: i32) -> &'static str {
    const TEXTS: [&str; 43] = [
        "No error",
        "Operation not permitted",
        "No such file or directory",
        "No such process",
        "Interrupted function call",
        "Input/output error",
        "No such device or address",
        "Arg list too long",
        "Exec format error",
        "Bad file descriptor",
        "No child processes",
        "Resource temporarily unavailable",
        "Not enough space",
        "Permission denied",
        "Bad address",
        "Unknown error",
        "Resource device",
        "File exists",
        "Improper link",
        "No such device",
        "Not a directory",
        "Is a directory",
        "Invalid argument",
        "Too many open files in system",
        "Too many open files",
        "Inappropriate I/O control operation",
        "Unknown error",
        "File too large",
        "No space left on device",
        "Invalid seek",
        "Read-only file system",
        "Too many links",
        "Broken pipe",
        "Domain error",
        "Result too large",
        "Unknown error",
        "Resource deadlock avoided",
        "Unknown error",
        "Filename too long",
        "No locks available",
        "Function not implemented",
        "Directory not empty",
        "Illegal byte sequence",
    ];
    usize::try_from(errno)
        .ok()
        .and_then(|i| TEXTS.get(i))
        .copied()
        .unwrap_or("Unknown error")
}

/// load_theme: the theme in `folder`, read as Python reads its theme.json (as text: its line
/// ends made "\n", its UTF-8 decoded strictly).
pub fn load_theme(folder: &Path, env: &Env) -> Result<Theme> {
    let path = folder.join(THEME_FILENAME);
    let bytes = match std::fs::read(&path) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return refuse(format!("no {THEME_FILENAME} in {}", folder.display()))
        }
        Err(e) => return Err(os_error(&e, &path)),
    };
    let text = paths::read_text(&bytes).map_err(|msg| Error {
        kind: Kind::UnicodeDecode,
        msg,
    })?;
    let data = match lp_py::json::loads(&text) {
        Ok(data) => data,
        Err(e) => {
            let kind = match e.kind {
                lp_py::json::ErrorKind::Decode { .. } => {
                    return refuse(format!("{}: {e}", path.display()))
                }
                lp_py::json::ErrorKind::Recursion => Kind::Recursion,
                lp_py::json::ErrorKind::Value => Kind::Value,
            };
            return raise(kind, e.to_string());
        }
    };
    parse_theme(&data, Some(folder), env)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_as_python_s_patterns_take_them() {
        // ($ also before a last line end, as Python's re has it)
        assert!(palette_name("accent") && palette_name("ok\n") && !palette_name("ok\n\n"));
        assert!(
            !palette_name("Ab") && !palette_name(&"a".repeat(33)) && palette_name(&"a".repeat(32))
        );
        assert!(
            valid_theme_name("lumen-1920x720")
                && !valid_theme_name("a..b")
                && !valid_theme_name("-a")
        );
    }

    #[test]
    fn fields_take_what_python_s_coerce_takes() {
        let place = "widgets[0].x";
        assert_eq!(
            coerce("int", Value::Float(-2.7), place, None).unwrap(),
            Value::int(-2)
        );
        let error = coerce("int", Value::Float(f64::NAN), place, None).unwrap_err();
        assert_eq!(error.msg, "widgets[0].x: expected an integer, not nan");
        assert_eq!(
            coerce("int", Value::Bool(true), place, None)
                .unwrap_err()
                .kind,
            Kind::Theme
        );
        let boxed = Value::List(vec![
            0.into(),
            0.into(),
            Value::Float(f64::INFINITY),
            1.into(),
        ]);
        assert_eq!(
            coerce("box?", boxed, place, None).unwrap_err().kind,
            Kind::Overflow
        );
        assert_eq!(
            coerce("number?", Value::None, place, None).unwrap(),
            Value::None
        );
        let error = coerce("enum:a|b", "c".into(), place, None).unwrap_err();
        assert_eq!(error.msg, "widgets[0].x: must be one of a, b");
    }
}
