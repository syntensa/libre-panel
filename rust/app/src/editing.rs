//! The editor's writing routes (editor/server.py `_save`, `_make_version`): a theme saved into
//! the person's themes folder, and a version of a theme made for a panel size.

use std::path::Path;

use lp_py::{Dict, Value};
use lp_theme::versions::{self, Places};
use lp_theme::{Env, Kind};
use num_bigint::BigInt;

/// What a route answers where it does not save: the message and the status.
pub struct Refused {
    pub message: String,
    pub status: u16,
}

fn refused(message: impl Into<String>, status: u16) -> Refused {
    Refused {
        message: message.into(),
        status,
    }
}

/// An error of the theme's: ThemeError, OSError (and ValueError where it is caught) are said
/// as they are (400), anything else as Python tells an exception it does not catch (500).
fn said(e: lp_theme::Error, caught_value: bool) -> Refused {
    match e.kind {
        Kind::Theme | Kind::Os(_) => refused(e.msg, 400),
        Kind::Value | Kind::UnicodeDecode if caught_value => refused(e.msg, 400),
        kind => refused(format!("{}: {}", kind.name(), e.msg), 500),
    }
}

/// `payload.get(key)`: AttributeError where the payload is no dict.
fn get<'a>(payload: &'a Value, key: &str) -> Result<Option<&'a Value>, Refused> {
    match payload {
        Value::Dict(d) => Ok(d.get(key)),
        other => Err(refused(
            format!(
                "AttributeError: '{}' object has no attribute 'get'",
                other.type_name()
            ),
            500,
        )),
    }
}

/// _save: `{"theme": ..., "source": "<theme copied from>"}` saved as `theme_id`.
pub fn save(theme_id: &str, payload: &Value, places: &Places, env: &Env) -> Result<Value, Refused> {
    let source = match get(payload, "source")? {
        Some(Value::Str(s)) => Some(s.as_str()),
        _ => None,
    };
    let data = get(payload, "theme")?.cloned().unwrap_or(Value::None);
    let folder =
        versions::save_theme(theme_id, &data, source, places, env).map_err(|e| said(e, false))?;
    let mut told = Dict::new();
    told.insert("ok".into(), Value::Bool(true));
    told.insert("id".into(), Value::Str(theme_id.into()));
    told.insert(
        "path".into(),
        Value::Str(folder.to_string_lossy().into_owned()),
    );
    Ok(Value::Dict(told))
}

/// int(value) as Python takes a width: None where it raises TypeError or ValueError;
/// OverflowError (an infinity) as Python tells it.
fn whole(value: Option<&Value>) -> Result<Option<BigInt>, Refused> {
    Ok(match value {
        Some(Value::Int(n)) => Some(n.clone()),
        Some(Value::Bool(b)) => Some(BigInt::from(u8::from(*b))),
        Some(Value::Float(f)) if f.is_nan() => None,
        Some(Value::Float(f)) if f.is_infinite() => {
            return Err(refused(
                "OverflowError: cannot convert float infinity to integer",
                500,
            ))
        }
        Some(Value::Float(f)) => num_bigint::ToBigInt::to_bigint(&f.trunc()),
        Some(Value::Str(s)) => lp_py::int_of_str(s),
        _ => None,
    })
}

/// _make_version: `{"width", "height"}`: a version of the theme made for that size, in the
/// person's themes folder as `<theme>-<width>x<height>`, begun as panels of that size show the
/// theme now: its version for the nearest shape laid out on it, or the theme scaled into it.
pub fn make_version(
    theme_id: &str,
    payload: &Value,
    places: &Places,
    env: &Env,
) -> Result<Value, Refused> {
    let (width, height) = (
        whole(get(payload, "width")?)?,
        whole(get(payload, "height")?)?,
    );
    let (Some(width), Some(height)) = (width, height) else {
        return Err(refused("width and height: whole numbers", 400));
    };
    if versions::find_theme(theme_id, places).is_err() {
        return Err(refused("not found", 404));
    }
    let stem = versions::version_stem(theme_id);
    let name = format!("{stem}-{width}x{height}");
    let within = |n: &BigInt| *n >= BigInt::from(16) && *n <= BigInt::from(4096);
    if !(within(&width) && within(&height)) || !lp_theme::model::valid_theme_name(&name) {
        return Err(refused(
            "display size must be between 16 and 4096 pixels",
            400,
        ));
    }
    if versions::find_theme(&name, places).is_ok() {
        let message = lp_i18n::t_format(
            "It has a version for this size already: {name}.",
            &[("name", Value::Str(name.clone()))],
        )
        .unwrap_or_else(|why| why.msg);
        return Err(refused(message, 409));
    }
    let (w, h) = (
        i64::try_from(&width).unwrap_or(0),
        i64::try_from(&height).unwrap_or(0),
    );
    let way = if h > w { "portrait" } else { "landscape" };
    let made = || -> Result<std::path::PathBuf, lp_theme::Error> {
        let shown = versions::theme_for(stem, way, Some((w, h)), places);
        let theme = lp_theme::load_theme(&versions::find_theme(&shown, places)?, env)?;
        let fitted =
            lp_theme::adapt::laid_out(&theme, Some((w, h)), "custom", &places.builtin, env)?;
        let folder_name = |root: &Path| root.file_name().map(|n| n.to_string_lossy().into_owned());
        let (mut data, source) = match &fitted.root {
            Some(root) if (fitted.width, fitted.height) == (w, h) => {
                (fitted.to_dict(), folder_name(root))
            }
            // (scaled into the panel, as it was shown)
            _ => {
                let data = lp_theme::adapt::adapt_theme(
                    &theme.to_dict(),
                    "custom",
                    way,
                    "scale",
                    &width,
                    &height,
                    env,
                )?;
                (data, theme.root.as_deref().and_then(folder_name))
            }
        };
        let shown_name = match theme.name.is_empty() {
            true => stem.to_string(),
            false => theme.name.clone(),
        };
        if let Value::Dict(d) = &mut data {
            d.insert(
                "name".into(),
                Value::Str(format!("{shown_name} {w}\u{d7}{h}")),
            );
        }
        versions::save_theme(&name, &data, source.as_deref(), places, env)
    };
    let folder = made().map_err(|e| said(e, true))?;
    let mut told = Dict::new();
    told.insert("id".into(), Value::Str(name));
    told.insert(
        "path".into(),
        Value::Str(folder.to_string_lossy().into_owned()),
    );
    Ok(Value::Dict(told))
}
