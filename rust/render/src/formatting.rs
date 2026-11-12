//! Format strings of shared themes (render/formatting.py). Themes come from strangers, so
//! str.format is locked down: only the fields `value`, `unit` and `label` exist, what is no
//! such field is refused, and a width of four digits or more too. The app's own specs:
//! `{value:bytes}` (1.2 MB), `{value:duration}` (3d 5h), `{value:clock}` (3:07), `{value:gb}`
//! (a size in GB from the reading's own unit), `{value:rate}` with `{value:rate_unit}` (519
//! and KB/s, from 1 MB/s 1.2 and MB/s) and `{value:upper}` (a text in capitals). Whether a
//! format shows its value as a number (counts(), which the modules ask) is lp-theme's
//! modules::counts.

use lp_py::format::{self as py, FmtError, Kind};
use lp_py::Value;
use num_bigint::BigInt;
use num_integer::Integer;
use num_traits::Zero;

/// The longest text a format makes (longer ones are cut).
pub const MAX_OUTPUT: usize = 200;

/// Why safe_format gives no text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FormatError {
    /// FormatError: the renderer shows the widget's fallback (or the text itself)
    Format(String),
    /// what Python does not catch there (an OverflowError): the frame fails, as Python's
    Uncaught(Kind, String),
}

fn fixed(x: f64, digits: &str) -> String {
    py::format(&Value::Float(x), digits).expect("a float in a fixed format")
}

/// human_bytes: 1.2 MB (powers of 1000).
pub fn human_bytes(value: &Value) -> Result<String, FmtError> {
    let mut value = py::float_of(value)?;
    for unit in ["B", "KB", "MB", "GB", "TB"] {
        if value.abs() < 1000.0 || unit == "TB" {
            let number = fixed(value, if unit == "B" { ".0f" } else { ".1f" });
            return Ok(format!("{number} {unit}"));
        }
        value /= 1000.0;
    }
    unreachable!("TB ends the loop")
}

/// human_duration: 3d 5h, 2h 7m, 4m.
pub fn human_duration(value: &Value) -> Result<String, FmtError> {
    let seconds = py::int_of(value)?;
    let (days, rest) = seconds.div_mod_floor(&BigInt::from(86400));
    let (hours, rest) = rest.div_mod_floor(&BigInt::from(3600));
    let minutes = rest.div_floor(&BigInt::from(60));
    Ok(if !days.is_zero() {
        format!("{days}d {hours}h")
    } else if !hours.is_zero() {
        format!("{hours}h {minutes}m")
    } else {
        format!("{minutes}m")
    })
}

/// clock_duration: a length the way players show it: 3:07, 1:02:45.
pub fn clock_duration(value: &Value) -> Result<String, FmtError> {
    let seconds = py::int_of(value)?.max(BigInt::zero());
    let (hours, rest) = seconds.div_mod_floor(&BigInt::from(3600));
    let (minutes, seconds) = rest.div_mod_floor(&BigInt::from(60));
    Ok(if !hours.is_zero() {
        format!("{hours}:{minutes:0>2}:{seconds:0>2}")
    } else {
        format!("{minutes}:{seconds:0>2}")
    })
}

/// rate_parts: a data rate (bytes a second) as a number and its unit: whole KB/s below
/// 1 MB/s, MB/s with a decimal above.
pub fn rate_parts(value: &Value) -> Result<(String, &'static str), FmtError> {
    let kilobytes = py::float_of(value)? / 1024.0;
    Ok(if kilobytes < 1024.0 {
        (fixed(kilobytes, ".0f"), "KB/s")
    } else {
        (fixed(kilobytes / 1024.0, ".1f"), "MB/s")
    })
}

/// Bytes of a unit a reading may come in (binary, as the sources count); GB where it has none
/// of these.
fn size_of(unit: &str) -> f64 {
    let unit = unit.strip_suffix("/s").unwrap_or(unit);
    let k = 1024.0f64;
    match unit {
        "B" => 1.0,
        "KB" | "KiB" => k,
        "MB" | "MiB" => k * k,
        "TB" | "TiB" => k * k * k * k,
        _ => k * k * k,
    }
}

/// auto_format: a reading shown the usual way for its unit: 45%, 61°, 2.4 MB/s, 3h 5m (its
/// errors Python's, which its caller does not catch).
pub fn auto_format(value: &Value, unit: &str) -> Result<String, FmtError> {
    match value {
        Value::Str(s) => return Ok(s.clone()),
        Value::None | Value::Bool(_) => return Ok("--".into()),
        _ => {}
    }
    let fmt = |spec: &str| py::format(value, spec);
    match unit {
        "B/s" => return Ok(human_bytes(value)? + "/s"),
        "B" => return human_bytes(value),
        "s" => return human_duration(value),
        "GiB" | "GB" => {
            let small = lp_py::num::lt(value, &Value::int(100)).map_err(|e| FmtError {
                kind: Kind::Type,
                msg: e.msg,
            })?;
            return Ok(format!("{} GB", fmt(if small { ".1f" } else { ".0f" })?));
        }
        "%" | "°C" | "°F" | "°" => return Ok(format!("{}{unit}", fmt(".0f")?)),
        _ => {}
    }
    let x = py::float_of(value)?;
    // (abs(value) < 10 and value != int(value): a float with a fraction, an int never)
    let fraction = matches!(value, Value::Float(_)) && x.abs() < 10.0 && x != x.trunc();
    let number = fmt(if fraction { ".1f" } else { ".0f" })?;
    Ok(if unit.is_empty() {
        number
    } else {
        format!("{number} {unit}")
    })
}

/// Whether a spec has four digits in a row (of any script): too large a width.
fn huge_number(spec: &str) -> bool {
    let mut run = 0;
    for ch in spec.chars() {
        run = if lp_py::decimal(ch).is_some() {
            run + 1
        } else {
            0
        };
        if run >= 4 {
            return true;
        }
    }
    false
}

fn refused(msg: impl Into<String>) -> FmtError {
    FmtError {
        kind: Kind::Value,
        msg: msg.into(),
    }
}

/// _SafeFormatter.format_field
fn format_field(value: &Value, spec: &str, unit: &str) -> Result<String, FmtError> {
    if huge_number(spec) {
        return Err(refused("format width too large"));
    }
    match spec {
        "bytes" => human_bytes(value),
        "duration" => human_duration(value),
        "clock" => clock_duration(value),
        "rate" => Ok(rate_parts(value)?.0),
        "rate_unit" => Ok(rate_parts(value)?.1.to_string()),
        "upper" => Ok(lp_py::upper(&py::str_of(value))),
        "gb" => {
            let gb = py::float_of(value)? * size_of(unit) / (1024.0f64 * 1024.0 * 1024.0);
            Ok(fixed(gb, ".1f"))
        }
        _ => py::format(value, spec),
    }
}

/// What a text is kept by (_kept): the format, the value with its type (and the sign of a
/// zero), the unit and the label.
#[derive(PartialEq, Eq, Hash)]
enum Kept {
    None,
    Bool(bool),
    Int(String),
    Float(u64),
    Str(String),
}

/// How many texts are kept (_kept's lru_cache).
const TEXTS_KEPT: usize = 1024;

thread_local! {
    static TEXTS: std::cell::RefCell<std::collections::HashMap<(String, Kept, String, String), String>> =
        std::cell::RefCell::new(std::collections::HashMap::new());
}

/// safe_format: `fmt` with the reading's `value`, `unit` and `label`. A text depends on
/// nothing else, so the last ones made are kept (_kept): a steady value, a list's names, the
/// same few numbers of a value that glides back and forth are made once.
pub fn safe_format(
    fmt: &str,
    value: &Value,
    unit: &str,
    label: &str,
) -> Result<String, FormatError> {
    let kept = match value {
        Value::None => Kept::None,
        Value::Bool(b) => Kept::Bool(*b),
        Value::Int(n) => Kept::Int(n.to_string()),
        // (the bits: 0.0 and -0.0 apart, as the sign of a zero keeps them)
        Value::Float(x) => Kept::Float(x.to_bits()),
        Value::Str(text) => Kept::Str(text.clone()),
        // (a value that is no key, e.g. a list: made each time)
        _ => return made(fmt, value, unit, label),
    };
    let key = (fmt.to_string(), kept, unit.to_string(), label.to_string());
    if let Some(text) = TEXTS.with(|texts| texts.borrow().get(&key).cloned()) {
        return Ok(text);
    }
    let text = made(fmt, value, unit, label)?;
    TEXTS.with(|texts| {
        let mut texts = texts.borrow_mut();
        if texts.len() >= TEXTS_KEPT {
            texts.clear();
        }
        texts.insert(key, text.clone());
    });
    Ok(text)
}

/// _made: the text of safe_format, made (errors are not kept).
fn made(fmt: &str, value: &Value, unit: &str, label: &str) -> Result<String, FormatError> {
    let get = |name: &str| -> Result<Value, FmtError> {
        match name {
            "value" => Ok(value.clone()),
            "unit" => Ok(Value::Str(unit.into())),
            "label" => Ok(Value::Str(label.into())),
            _ => Err(refused(format!(
                "unknown field {{{name}}}; use value, unit or label"
            ))),
        }
    };
    let field = |v: &Value, spec: &str| format_field(v, spec, unit);
    match py::vformat(fmt, &get, &field) {
        Ok(text) => Ok(text.chars().take(MAX_OUTPUT).collect()),
        Err(e) if matches!(e.kind, Kind::Value | Kind::Type) => Err(FormatError::Format(e.msg)),
        Err(e) => Err(FormatError::Uncaught(e.kind, e.msg)),
    }
}
