//! A widget's fields as the renderer reads them. theme/model.py has made each of its kind: an
//! int an int, a number an int or a float as the file has it, a colour a str or None.

use lp_py::{Dict, Value};
use num_bigint::Sign;
use num_traits::ToPrimitive;

/// An int field; one past an i64 its end (a box that far lies off every panel, a radius that
/// large is cut to the box's anyway).
pub fn int(w: &Dict, key: &str, default: i64) -> i64 {
    match w.get(key) {
        Some(Value::Int(n)) => n.to_i64().unwrap_or(if n.sign() == Sign::Minus {
            i64::MIN
        } else {
            i64::MAX
        }),
        Some(Value::Bool(on)) => i64::from(*on),
        Some(Value::Float(x)) => *x as i64,
        _ => default,
    }
}

/// A number field as a float (an int past a float's range: an infinity of its sign).
pub fn number(w: &Dict, key: &str, default: f64) -> f64 {
    match w.get(key) {
        Some(Value::Float(x)) => *x,
        Some(Value::Int(n)) => n.to_f64().unwrap_or(if n.sign() == Sign::Minus {
            f64::NEG_INFINITY
        } else {
            f64::INFINITY
        }),
        Some(Value::Bool(on)) => f64::from(u8::from(*on)),
        _ => default,
    }
}

/// A text field ("" where there is none).
pub fn text<'a>(w: &'a Dict, key: &str) -> &'a str {
    match w.get(key) {
        Some(Value::Str(s)) => s,
        _ => "",
    }
}

/// An optional text field (a colour): None where there is none.
pub fn optional<'a>(w: &'a Dict, key: &str) -> Option<&'a str> {
    match w.get(key) {
        Some(Value::Str(s)) => Some(s),
        _ => None,
    }
}

/// Python's truth of a field (none: false).
pub fn truthy(w: &Dict, key: &str) -> bool {
    w.get(key).is_some_and(Value::truthy)
}

/// A bool field as Python's widget.get(key, default) gives it, taken for its truth.
pub fn flag(w: &Dict, key: &str, default: bool) -> bool {
    w.get(key).map_or(default, Value::truthy)
}
