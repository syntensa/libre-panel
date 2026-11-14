//! Python's values as the app's JSON files hold them (None, bool, int, float, str, list, dict),
//! and what Python does with them that the app's checks and messages depend on: `==` (1 == 1.0
//! == True), `<` between numbers, repr() and str().
//!
//! Ints are Python's: of any size (a theme may hold 1e300 where a whole number belongs, and
//! int() of it is a 301-digit number). A dict keeps its keys in the order they came; one given
//! twice keeps its first place and its last value.

use std::cmp::Ordering;

use indexmap::IndexMap;
use num_bigint::BigInt;
use num_traits::{FromPrimitive, ToPrimitive, Zero};

use crate::{float_repr, repr as str_repr};

/// A dict as JSON gives it: str keys, in their order (its keys hashed by foldhash: a widget's
/// settings are looked up by name many times a frame, and the order is the dict's own).
pub type Dict = IndexMap<String, Value, foldhash::fast::RandomState>;

/// One of Python's values, as JSON gives them.
#[derive(Clone, Debug)]
pub enum Value {
    None,
    Bool(bool),
    Int(BigInt),
    Float(f64),
    Str(String),
    List(Vec<Value>),
    Dict(Dict),
}

impl Value {
    /// An int from a machine one.
    pub fn int(n: i64) -> Value {
        Value::Int(BigInt::from(n))
    }

    /// The name of its type, as Python's messages give it (`type(x).__name__`).
    pub fn type_name(&self) -> &'static str {
        match self {
            Value::None => "NoneType",
            Value::Bool(_) => "bool",
            Value::Int(_) => "int",
            Value::Float(_) => "float",
            Value::Str(_) => "str",
            Value::List(_) => "list",
            Value::Dict(_) => "dict",
        }
    }

    /// Whether it is an int or a float (`isinstance(x, (int, float))`, bool left out as the
    /// app leaves it out).
    pub fn is_number(&self) -> bool {
        matches!(self, Value::Int(_) | Value::Float(_))
    }

    /// Python's truth of it (`if x:`).
    pub fn truthy(&self) -> bool {
        match self {
            Value::None => false,
            Value::Bool(b) => *b,
            Value::Int(n) => !n.is_zero(),
            Value::Float(f) => *f != 0.0,
            Value::Str(s) => !s.is_empty(),
            Value::List(l) => !l.is_empty(),
            Value::Dict(d) => !d.is_empty(),
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_dict(&self) -> Option<&Dict> {
        match self {
            Value::Dict(d) => Some(d),
            _ => None,
        }
    }

    pub fn as_list(&self) -> Option<&[Value]> {
        match self {
            Value::List(l) => Some(l),
            _ => None,
        }
    }

    /// float(x) of a number (bool as 0 or 1); None for an int too large for a float (Python:
    /// OverflowError) and for anything else.
    pub fn to_f64(&self) -> Option<f64> {
        match self {
            Value::Bool(b) => Some(f64::from(u8::from(*b))),
            Value::Int(n) => n.to_f64().filter(|f| f.is_finite()),
            Value::Float(f) => Some(*f),
            _ => None,
        }
    }

    /// repr(): as Python writes the value back (`'a'`, `1.0`, `True`, `None`, `[1, 2]`,
    /// `{'a': 1}`).
    pub fn repr(&self) -> String {
        let mut out = String::new();
        self.write_repr(&mut out);
        out
    }

    fn write_repr(&self, out: &mut String) {
        match self {
            Value::None => out.push_str("None"),
            Value::Bool(true) => out.push_str("True"),
            Value::Bool(false) => out.push_str("False"),
            Value::Int(n) => out.push_str(&n.to_string()),
            Value::Float(f) => out.push_str(&float_repr(*f)),
            Value::Str(s) => out.push_str(&str_repr(s)),
            Value::List(items) => {
                out.push('[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    item.write_repr(out);
                }
                out.push(']');
            }
            Value::Dict(dict) => {
                out.push('{');
                for (i, (key, item)) in dict.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    out.push_str(&str_repr(key));
                    out.push_str(": ");
                    item.write_repr(out);
                }
                out.push('}');
            }
        }
    }

    /// str(): a str as it is, anything else as repr() has it.
    pub fn str(&self) -> String {
        match self {
            Value::Str(s) => s.clone(),
            other => other.repr(),
        }
    }
}

/// x < y between an int and a float, exactly (Python compares them without rounding the int).
fn cmp_int_float(n: &BigInt, f: f64) -> Option<Ordering> {
    if f.is_nan() {
        return None;
    }
    if f.is_infinite() {
        return Some(if f > 0.0 {
            Ordering::Less
        } else {
            Ordering::Greater
        });
    }
    let whole = f.trunc();
    let order = n.cmp(&BigInt::from_f64(whole).expect("a finite float"));
    if order != Ordering::Equal {
        return Some(order);
    }
    // the same whole part: the float's fraction decides
    Some(0.0f64.partial_cmp(&(f - whole)).expect("a finite fraction"))
}

/// Python's order of two numbers (bool, int, float; None for NaN and for what is no number).
pub fn compare(a: &Value, b: &Value) -> Option<Ordering> {
    let int = |v: &Value| match v {
        Value::Bool(x) => Some(BigInt::from(u8::from(*x))),
        Value::Int(n) => Some(n.clone()),
        _ => None,
    };
    match (a, b) {
        (Value::Float(x), Value::Float(y)) => x.partial_cmp(y),
        (Value::Float(x), other) => {
            int(other).and_then(|n| cmp_int_float(&n, *x).map(Ordering::reverse))
        }
        (other, Value::Float(y)) => int(other).and_then(|n| cmp_int_float(&n, *y)),
        _ => Some(int(a)?.cmp(&int(b)?)),
    }
}

impl PartialEq for Value {
    /// Python's `==`: numbers by value whatever their type (1 == 1.0 == True; NaN equals
    /// nothing), lists item by item, dicts key by key in any order.
    fn eq(&self, other: &Value) -> bool {
        let number = |v: &Value| matches!(v, Value::Bool(_) | Value::Int(_) | Value::Float(_));
        match (self, other) {
            (a, b) if number(a) && number(b) => compare(a, b) == Some(Ordering::Equal),
            (Value::None, Value::None) => true,
            (Value::Str(a), Value::Str(b)) => a == b,
            (Value::List(a), Value::List(b)) => a == b,
            (Value::Dict(a), Value::Dict(b)) => {
                a.len() == b.len() && a.iter().all(|(k, v)| b.get(k) == Some(v))
            }
            _ => false,
        }
    }
}

impl From<&str> for Value {
    fn from(s: &str) -> Value {
        Value::Str(s.to_string())
    }
}

impl From<String> for Value {
    fn from(s: String) -> Value {
        Value::Str(s)
    }
}

impl From<bool> for Value {
    fn from(b: bool) -> Value {
        Value::Bool(b)
    }
}

impl From<i64> for Value {
    fn from(n: i64) -> Value {
        Value::int(n)
    }
}

impl From<f64> for Value {
    fn from(f: f64) -> Value {
        Value::Float(f)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numbers_compare_and_equal_as_python_s() {
        let big = Value::Int(BigInt::from(2u64.pow(53)) + 1);
        assert!(big != Value::Float(2f64.powi(53))); // (exactly: 2**53 + 1 != 2.0**53)
        assert_eq!(
            compare(&big, &Value::Float(2f64.powi(53))),
            Some(Ordering::Greater)
        );
        assert!(Value::int(1) == Value::Float(1.0) && Value::Bool(true) == Value::int(1));
        assert!(Value::Float(f64::NAN) != Value::Float(f64::NAN));
        assert_eq!(
            compare(&Value::int(0), &Value::Float(0.5)),
            Some(Ordering::Less)
        );
        assert_eq!(
            compare(&Value::int(-1), &Value::Float(-0.5)),
            Some(Ordering::Less)
        );
        assert_eq!(
            compare(&Value::int(3), &Value::Float(f64::INFINITY)),
            Some(Ordering::Less)
        );
        assert_eq!(compare(&Value::from("a"), &Value::int(1)), None);
    }

    #[test]
    fn repr_and_str_as_python_s() {
        let mut dict = Dict::default();
        dict.insert(
            "k".into(),
            Value::List(vec![Value::None, Value::Bool(true), 1.5.into()]),
        );
        let v = Value::Dict(dict);
        assert_eq!(v.repr(), "{'k': [None, True, 1.5]}");
        assert_eq!(Value::from("it's").repr(), "\"it's\"");
        assert_eq!(Value::from("x").str(), "x");
        assert_eq!(Value::Float(1e16).str(), "1e+16");
    }
}
