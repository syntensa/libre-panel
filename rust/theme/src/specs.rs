//! A table's fields: their kind and default, as theme/model.py's tables give them as
//! `(kind, default)` (kinds: int, number, number?, string, text, color, color?, bool, sensor,
//! format, font, asset, rules, icon, box?, "enum:a|b|c").

use lp_py::Value;

/// A field's default as the table has it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Given {
    None,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(&'static str),
    /// an empty list (color_rules)
    List,
}

impl Given {
    /// The default as a value of its own (deepcopy(default)).
    pub fn value(self) -> Value {
        match self {
            Given::None => Value::None,
            Given::Bool(b) => Value::Bool(b),
            Given::Int(n) => Value::int(n),
            Given::Float(f) => Value::Float(f),
            Given::Str(s) => Value::from(s),
            Given::List => Value::List(Vec::new()),
        }
    }
}

/// One field of a table.
#[derive(Debug)]
pub struct Field {
    pub name: &'static str,
    pub kind: &'static str,
    pub default: Given,
}

pub(crate) const fn f(name: &'static str, kind: &'static str, default: Given) -> Field {
    Field {
        name,
        kind,
        default,
    }
}
