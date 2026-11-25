//! Format strings of shared themes (render/formatting.py). Themes come from strangers, so
//! str.format is locked down: only the fields `value`, `unit` and `label` exist, what is no
//! such field is refused, and a width of four digits or more too. The app's own specs:
//! `{value:bytes}` (1.2 MB; of a reading that comes in GB or MB, 16.4 GB), `{value:duration}`
//! (3d 5h), `{value:clock}` (3:07), `{value:gb}` (a size in GB from the reading's own unit),
//! `{value:rate}` with `{value:rate_unit}` (519 and KB/s, from 1 MB/s 1.2 and MB/s),
//! `{value:upper}` (a text in capitals), `{value:mb}` (an amount in MB from the reading's own
//! unit), `{value:count}` (a count said short: 987, 48.2k, 2.4M) and `{value:auto}` (the
//! number as auto_format writes it, without the unit: 7.3, 83). Where the app says
//! amounts of data in MB alone (`data_unit = "mb"`, lp_i18n::data_in_mb), `bytes` and `rate`
//! say a count of bytes in MB too. Whether a format shows its value as a number (counts(),
//! which the modules ask) is lp-theme's modules::counts.

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

/// human_bytes: an amount of data in the unit that fits it, 1.2 MB. A count of bytes goes up
/// in steps of 1000; a reading that comes in a larger unit (`unit`: the memory's GB, a graphics
/// card's MB) is counted from there, in that unit's own steps of 1024: 16.4 GB reads "16.4 GB".
/// Where amounts of data are said in MB alone, a count of bytes is: 0.4 MB, 2400 MB.
pub fn human_bytes(value: &Value, unit: &str) -> Result<String, FmtError> {
    bytes_as(value, unit, lp_i18n::data_in_mb())
}

/// human_bytes, `in_mb` saying whether amounts of data are said in MB alone.
fn bytes_as(value: &Value, unit: &str, in_mb_alone: bool) -> Result<String, FmtError> {
    let mut value = py::float_of(value)?;
    let size = known_size(unit).unwrap_or(1.0);
    if size == 1.0 && in_mb_alone {
        return Ok(format!("{} MB", in_mb(value / 1e6)));
    }
    let step = if size > 1.0 { 1024.0 } else { 1000.0 };
    if size > 1.0 {
        value *= size;
    }
    for name in ["B", "KB", "MB", "GB", "TB"] {
        if value.abs() < 1000.0 || name == "TB" {
            let number = fixed(value, if name == "B" { ".0f" } else { ".1f" });
            return Ok(format!("{number} {name}"));
        }
        value /= step;
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
/// 1 MB/s, MB/s with a decimal above; MB/s always where amounts of data are said in MB alone.
pub fn rate_parts(value: &Value) -> Result<(String, &'static str), FmtError> {
    rate_as(value, lp_i18n::data_in_mb())
}

/// rate_parts, `in_mb_alone` saying whether amounts of data are said in MB alone.
fn rate_as(value: &Value, in_mb_alone: bool) -> Result<(String, &'static str), FmtError> {
    let kilobytes = py::float_of(value)? / 1024.0;
    if in_mb_alone {
        return Ok((in_mb(kilobytes / 1024.0), "MB/s"));
    }
    Ok(if kilobytes < 1024.0 {
        (fixed(kilobytes, ".0f"), "KB/s")
    } else {
        (fixed(kilobytes / 1024.0, ".1f"), "MB/s")
    })
}

/// An amount in MB as it is said: a decimal below 100, none from there (0.4, 12.3, 2400).
fn in_mb(mb: f64) -> String {
    fixed(mb, if mb.abs() < 100.0 { ".1f" } else { ".0f" })
}

/// `{value:count}`: a count said short, the same in every language: as it is below a thousand
/// (987), then in thousands and millions, with a decimal below 100 (48.2k, 312k, 2.4M, 1234M).
fn count_of(value: &Value) -> Result<String, FmtError> {
    let n = py::float_of(value)?;
    let short = |x: f64, unit: &str| {
        let digits = if x.abs() < 99.95 { ".1f" } else { ".0f" };
        format!("{}{unit}", fixed(x, digits))
    };
    Ok(if n.is_nan() || n.abs() < 999.5 {
        fixed(n, ".0f") // (and what is no number: nan)
    } else if n.abs() < 999_500.0 {
        short(n / 1e3, "k")
    } else {
        short(n / 1e6, "M")
    })
}

/// `{value:mb}`: an amount (or a rate) in MB, from the reading's own unit; of a count of bytes
/// the MB that `bytes` says (steps of 1000), of a larger unit its own steps of 1024 (a graphics
/// card's 6234 MB reads 6234, the memory's 16 GB 16384), of any other unit the value itself.
fn mb_of(value: &Value, unit: &str) -> Result<String, FmtError> {
    let value = py::float_of(value)?;
    Ok(in_mb(match known_size(unit) {
        Some(size) if size > 1.0 => value * size / (1024.0 * 1024.0),
        Some(_) => value / 1e6,
        None => value,
    }))
}

/// Bytes of a unit a reading may come in (_SIZES: binary, as the sources count), a rate's
/// "/s" aside; None of a unit that is none of these.
fn known_size(unit: &str) -> Option<f64> {
    let unit = unit.strip_suffix("/s").unwrap_or(unit);
    let k = 1024.0f64;
    Some(match unit {
        "B" => 1.0,
        "KB" | "KiB" => k,
        "MB" | "MiB" => k * k,
        "GB" | "GiB" => k * k * k,
        "TB" | "TiB" => k * k * k * k,
        _ => return None,
    })
}

/// The same for `{value:gb}`: GB where the unit is none of them.
fn size_of(unit: &str) -> f64 {
    known_size(unit).unwrap_or(1024.0f64 * 1024.0 * 1024.0)
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
        "B/s" => return Ok(human_bytes(value, "")? + "/s"),
        "B" => return human_bytes(value, ""),
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
    let number = auto_number(value)?;
    Ok(if unit.is_empty() {
        number
    } else {
        format!("{number} {unit}")
    })
}

/// auto_number: a reading's number as auto_format writes it, without its unit (`{value:auto}`):
/// a float with a fraction under 10 with one decimal, any other number without; a text as it
/// is, none as "--".
fn auto_number(value: &Value) -> Result<String, FmtError> {
    match value {
        Value::Str(s) => return Ok(s.clone()),
        Value::None | Value::Bool(_) => return Ok("--".into()),
        _ => {}
    }
    let x = py::float_of(value)?;
    // (abs(value) < 10 and value != int(value): a float with a fraction, an int never)
    let fraction = matches!(value, Value::Float(_)) && x.abs() < 10.0 && x != x.trunc();
    py::format(value, if fraction { ".1f" } else { ".0f" })
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
        "bytes" => human_bytes(value, unit),
        "duration" => human_duration(value),
        "clock" => clock_duration(value),
        "rate" => Ok(rate_parts(value)?.0),
        "rate_unit" => Ok(rate_parts(value)?.1.to_string()),
        "upper" => Ok(lp_py::upper(&py::str_of(value))),
        "mb" => mb_of(value, unit),
        "count" => count_of(value),
        "auto" => auto_number(value),
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

/// What a kept text is found by: the format, the value, the unit, the label, and whether amounts
/// of data are said in MB alone.
type TextKey = (String, Kept, String, String, bool);

/// How many texts are kept (_kept's lru_cache).
const TEXTS_KEPT: usize = 1024;

thread_local! {
    static TEXTS: std::cell::RefCell<std::collections::HashMap<TextKey, String>> =
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
    // (and whether amounts of data are said in MB alone, which changes what `bytes` says)
    let key = (
        fmt.to_string(),
        kept,
        unit.to_string(),
        label.to_string(),
        lp_i18n::data_in_mb(),
    );
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_number_alone_as_libre_panel_writes_it() {
        let auto = |v: Value, unit: &str| safe_format("{value:auto} {unit}", &v, unit, "").unwrap();
        assert_eq!(auto(Value::Float(83.0), "%"), "83 %");
        assert_eq!(auto(Value::Float(7.34), "GB"), "7.3 GB");
        assert_eq!(auto(Value::Float(12.6), ""), "13 ");
        assert_eq!(auto(Value::int(4107206656), "B"), "4107206656 B");
        assert_eq!(auto(Value::Str("ok".into()), ""), "ok ");
        assert_eq!(auto(Value::None, ""), "-- ");
        // (as auto_format writes the number of a reading in a unit of none of its own)
        assert_eq!(auto_format(&Value::Float(7.34), "V").unwrap(), "7.3 V");
    }

    #[test]
    fn amounts_of_data_in_mb_alone_and_as_they_fit() {
        let f = Value::Float;
        let bytes = |v: f64, unit: &str, mb: bool| bytes_as(&f(v), unit, mb).unwrap();
        // as they fit (the default): what it always said
        assert_eq!(bytes(850_000.0, "B/s", false), "850.0 KB");
        assert_eq!(bytes(2_400_000.0, "B/s", false), "2.4 MB");
        assert_eq!(bytes(6234.0, "MB", false), "6.1 GB");
        // in MB alone: a count of bytes in MB, a decimal below 100
        assert_eq!(bytes(860_000.0, "B/s", true), "0.9 MB");
        assert_eq!(bytes(2_400_000.0, "", true), "2.4 MB");
        assert_eq!(bytes(250_000_000.0, "B", true), "250 MB");
        // (a reading in a larger unit stays as it fits: the memory's GB)
        assert_eq!(bytes(16.4, "GB", true), "16.4 GB");
        let rate = |v: f64, mb: bool| rate_as(&f(v), mb).unwrap();
        assert_eq!(rate(500.0 * 1024.0, false), ("500".into(), "KB/s"));
        assert_eq!(rate(1.5 * 1024.0 * 1024.0, false), ("1.5".into(), "MB/s"));
        assert_eq!(rate(500.0 * 1024.0, true), ("0.5".into(), "MB/s"));
        assert_eq!(rate(150.0 * 1024.0 * 1024.0, true), ("150".into(), "MB/s"));
        // {value:mb}: from the reading's own unit
        let mb = |v: f64, unit: &str| format_field(&f(v), "mb", unit).unwrap();
        assert_eq!(mb(6234.0, "MB"), "6234");
        assert_eq!(mb(16.0, "GB"), "16384");
        assert_eq!(mb(2_400_000.0, "B/s"), "2.4");
        assert_eq!(mb(42.5, ""), "42.5");
    }

    #[test]
    fn a_count_is_said_short() {
        let count = |value: Value| format_field(&value, "count", "").unwrap();
        let f = Value::Float;
        assert_eq!(count(Value::int(0)), "0");
        assert_eq!(count(Value::int(987)), "987");
        assert_eq!(count(f(999.4)), "999");
        assert_eq!(count(f(999.5)), "1.0k"); // (never "1000")
        assert_eq!(count(f(48_213.0)), "48.2k");
        assert_eq!(count(f(99_949.0)), "99.9k");
        assert_eq!(count(f(99_950.0)), "100k");
        assert_eq!(count(f(312_400.0)), "312k");
        assert_eq!(count(f(999_499.0)), "999k");
        assert_eq!(count(f(999_500.0)), "1.0M");
        assert_eq!(count(f(2_350_000.0)), "2.4M");
        assert_eq!(count(f(1_234_000_000.0)), "1234M");
        assert_eq!(count(f(-48_213.0)), "-48.2k");
        assert_eq!(count(f(f64::NAN)), "nan");
        assert!(format_field(&Value::Str("x".into()), "count", "").is_err());
        assert!(crate::formatting::safe_format("{value:count}", &f(5.0), "", "").is_ok());
    }
}
