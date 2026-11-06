//! Python's arithmetic on the numbers a JSON file holds (bool, int of any size, float): `+ - *
//! / // %`, `<` and `>`, `min()` and `max()` of two, `round()`, `float()` and `math.log()`, as
//! Python 3.12 does them: the same results to the bit and the same errors with the same
//! messages (TypeError, ZeroDivisionError, OverflowError, ValueError).
//!
//! The app's checks turn most of a theme's values into numbers before anything counts with
//! them. Where the app counts with what a file holds as it is (the grid a version of a theme was
//! made on), a value may be a string, a list, NaN or an int of 400 digits, and Python's answer,
//! a number or an error, decides what the app does next.

use std::cmp::Ordering;

use num_bigint::{BigInt, BigUint, Sign};
use num_integer::Integer;
use num_traits::{FromPrimitive, ToPrimitive, Zero};

use crate::value::{compare, Value};

/// The class of the exception Python raises.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ErrorKind {
    Type,
    ZeroDivision,
    Overflow,
    Value,
    /// What Python counts with values that are no numbers (`"a" + "b"`, `[1] * 2`, `"a" < "b"`,
    /// `"%d" % 1`), which this module does not do: the caller has values of that kind where
    /// Python would go on.
    Unsupported,
}

impl ErrorKind {
    /// The class's name, as Python's messages give it.
    pub fn name(self) -> &'static str {
        match self {
            ErrorKind::Type => "TypeError",
            ErrorKind::ZeroDivision => "ZeroDivisionError",
            ErrorKind::Overflow => "OverflowError",
            ErrorKind::Value => "ValueError",
            ErrorKind::Unsupported => "Unsupported",
        }
    }
}

/// An exception: its class and its message (`str(e)`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Error {
    pub kind: ErrorKind,
    pub msg: String,
}

pub type Result<T> = std::result::Result<T, Error>;

fn fail<T>(kind: ErrorKind, msg: impl Into<String>) -> Result<T> {
    Err(Error {
        kind,
        msg: msg.into(),
    })
}

/// A number as Python counts with it: a bool is an int.
enum Num {
    Int(BigInt),
    Float(f64),
}

fn num(v: &Value) -> Option<Num> {
    match v {
        Value::Bool(b) => Some(Num::Int(BigInt::from(u8::from(*b)))),
        Value::Int(n) => Some(Num::Int(n.clone())),
        Value::Float(f) => Some(Num::Float(*f)),
        _ => None,
    }
}

/// float(n) of an int: the nearest float (halfway: the even one); OverflowError past the
/// largest float.
pub fn int_to_float(n: &BigInt) -> Result<f64> {
    match n.to_f64() {
        Some(f) if f.is_finite() => Ok(f),
        _ => fail(ErrorKind::Overflow, "int too large to convert to float"),
    }
}

/// float(x) of a number (bool, int, float).
pub fn to_float(v: &Value) -> Result<f64> {
    match num(v) {
        Some(Num::Int(n)) => int_to_float(&n),
        Some(Num::Float(f)) => Ok(f),
        None => fail(
            ErrorKind::Unsupported,
            format!("float() of a {}", v.type_name()),
        ),
    }
}

/// `a / b` of two ints: the float nearest the exact quotient (halfway: the even one), down to
/// the smallest float; ZeroDivisionError for 0, OverflowError past the largest float.
pub fn int_true_div(a: &BigInt, b: &BigInt) -> Result<f64> {
    if b.is_zero() {
        return fail(ErrorKind::ZeroDivision, "division by zero");
    }
    let negative = (a.sign() == Sign::Minus) != (b.sign() == Sign::Minus);
    let (x, y) = (a.magnitude(), b.magnitude());
    let q = if x.is_zero() {
        0.0
    } else if x.bits() <= 53 && y.bits() <= 53 {
        // (both exact as floats: one rounding)
        x.to_f64().expect("53 bits") / y.to_f64().expect("53 bits")
    } else {
        rounded_quotient(x, y)?
    };
    Ok(if negative { -q } else { q })
}

/// x / y (both above 0) rounded once to a float.
fn rounded_quotient(x: &BigUint, y: &BigUint) -> Result<f64> {
    let too_large = || {
        fail(
            ErrorKind::Overflow,
            "integer division result too large for a float",
        )
    };
    // e: 2^e <= x / y < 2^(e + 1)
    let d = x.bits() as i64 - y.bits() as i64;
    let at_least = if d >= 0 {
        *x >= (y << d as u64)
    } else {
        (x << (-d) as u64) >= *y
    };
    let e = if at_least { d } else { d - 1 };
    if e >= 1024 {
        return too_large();
    }
    // the place of the last bit kept: 53 bits, fewer below the smallest normal float
    let last = (e - 52).max(-1074);
    let (top, bottom) = if last <= 0 {
        (x << (-last) as u64, y.clone())
    } else {
        (x.clone(), y << last as u64)
    };
    let (mut n, rest) = top.div_rem(&bottom);
    match (rest << 1u8).cmp(&bottom) {
        Ordering::Greater => n += 1u8,
        Ordering::Equal if n.is_odd() => n += 1u8,
        _ => {}
    }
    let n = n.to_u64().expect("at most 2^53");
    match compose(n, last) {
        Some(f) => Ok(f),
        None => too_large(),
    }
}

/// n * 2^last as a float, n at most 2^53 and the product a float exactly (or past the largest
/// one: None).
fn compose(n: u64, last: i64) -> Option<f64> {
    if n == 0 {
        return Some(0.0);
    }
    let (mut m, mut e) = (n, last);
    if m == 1 << 53 {
        m >>= 1;
        e += 1;
    }
    if m >= 1 << 52 {
        let biased = e + 52 + 1023;
        if biased >= 2047 {
            return None;
        }
        Some(f64::from_bits(((biased as u64) << 52) | (m - (1 << 52))))
    } else {
        debug_assert_eq!(e, -1074, "below 2^52 only where the float is subnormal");
        Some(f64::from_bits(m))
    }
}

/// CPython's _float_div_mod: the floor of vx / wx and the remainder with wx's sign.
fn float_div_mod(vx: f64, wx: f64) -> (f64, f64) {
    let mut rest = vx % wx;
    let mut div = (vx - rest) / wx;
    if rest != 0.0 {
        // (NaN too, as C's `if (mod)` has it)
        if (wx < 0.0) != (rest < 0.0) {
            rest += wx;
            div -= 1.0;
        }
    } else {
        rest = 0.0f64.copysign(wx);
    }
    let floor = if div != 0.0 {
        let mut floor = div.floor();
        if div - floor > 0.5 {
            floor += 1.0;
        }
        floor
    } else {
        0.0f64.copysign(vx / wx)
    };
    (floor, rest)
}

/// CPython's float_rem.
fn float_rem(vx: f64, wx: f64) -> f64 {
    let mut rest = vx % wx;
    if rest != 0.0 {
        if (wx < 0.0) != (rest < 0.0) {
            rest += wx;
        }
    } else {
        rest = 0.0f64.copysign(wx);
    }
    rest
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Op {
    Add,
    Sub,
    Mul,
    TrueDiv,
    FloorDiv,
    Mod,
}

impl Op {
    fn symbol(self) -> &'static str {
        match self {
            Op::Add => "+",
            Op::Sub => "-",
            Op::Mul => "*",
            Op::TrueDiv => "/",
            Op::FloorDiv => "//",
            Op::Mod => "%",
        }
    }
}

fn binary(op: Op, a: &Value, b: &Value) -> Result<Value> {
    let (Some(x), Some(y)) = (num(a), num(b)) else {
        return Err(no_numbers(op, a, b));
    };
    match (x, y) {
        (Num::Int(x), Num::Int(y)) => match op {
            Op::Add => Ok(Value::Int(x + y)),
            Op::Sub => Ok(Value::Int(x - y)),
            Op::Mul => Ok(Value::Int(x * y)),
            Op::TrueDiv => int_true_div(&x, &y).map(Value::Float),
            Op::FloorDiv if y.is_zero() => fail(
                ErrorKind::ZeroDivision,
                "integer division or modulo by zero",
            ),
            Op::FloorDiv => Ok(Value::Int(x.div_floor(&y))),
            Op::Mod if y.is_zero() => fail(ErrorKind::ZeroDivision, "integer modulo by zero"),
            Op::Mod => Ok(Value::Int(x.mod_floor(&y))),
        },
        (x, y) => {
            // a float and a number: both as floats, the left one first
            let float = |n: Num| match n {
                Num::Int(n) => int_to_float(&n),
                Num::Float(f) => Ok(f),
            };
            let (x, y) = (float(x)?, float(y)?);
            match op {
                Op::Add => Ok(Value::Float(x + y)),
                Op::Sub => Ok(Value::Float(x - y)),
                Op::Mul => Ok(Value::Float(x * y)),
                Op::TrueDiv if y == 0.0 => fail(ErrorKind::ZeroDivision, "float division by zero"),
                Op::TrueDiv => Ok(Value::Float(x / y)),
                Op::FloorDiv if y == 0.0 => {
                    fail(ErrorKind::ZeroDivision, "float floor division by zero")
                }
                Op::FloorDiv => Ok(Value::Float(float_div_mod(x, y).0)),
                Op::Mod if y == 0.0 => fail(ErrorKind::ZeroDivision, "float modulo"),
                Op::Mod => Ok(Value::Float(float_rem(x, y))),
            }
        }
    }
}

/// Python's TypeError for an operation between values that are not both numbers (Unsupported
/// where Python counts with them all the same).
fn no_numbers(op: Op, a: &Value, b: &Value) -> Error {
    let (ta, tb) = (a.type_name(), b.type_name());
    let sequence = |v: &Value| matches!(v, Value::Str(_) | Value::List(_));
    let index = |v: &Value| matches!(v, Value::Bool(_) | Value::Int(_));
    let (kind, msg) = match op {
        Op::Add => match (a, b) {
            (Value::Str(_), Value::Str(_)) | (Value::List(_), Value::List(_)) => {
                (ErrorKind::Unsupported, format!("{ta} + {tb}"))
            }
            (Value::Str(_) | Value::List(_), _) => (
                ErrorKind::Type,
                format!("can only concatenate {ta} (not \"{tb}\") to {ta}"),
            ),
            _ => (
                ErrorKind::Type,
                format!("unsupported operand type(s) for +: '{ta}' and '{tb}'"),
            ),
        },
        Op::Mul if (sequence(a) && index(b)) || (index(a) && sequence(b)) => {
            (ErrorKind::Unsupported, format!("{ta} * {tb}"))
        }
        Op::Mul if sequence(a) => (
            ErrorKind::Type,
            format!("can't multiply sequence by non-int of type '{tb}'"),
        ),
        Op::Mul if sequence(b) => (
            ErrorKind::Type,
            format!("can't multiply sequence by non-int of type '{ta}'"),
        ),
        Op::Mod if matches!(a, Value::Str(_)) => (ErrorKind::Unsupported, format!("{ta} % {tb}")),
        _ => (
            ErrorKind::Type,
            format!(
                "unsupported operand type(s) for {}: '{ta}' and '{tb}'",
                op.symbol()
            ),
        ),
    };
    Error { kind, msg }
}

/// a + b
pub fn add(a: &Value, b: &Value) -> Result<Value> {
    binary(Op::Add, a, b)
}

/// a - b
pub fn sub(a: &Value, b: &Value) -> Result<Value> {
    binary(Op::Sub, a, b)
}

/// a * b
pub fn mul(a: &Value, b: &Value) -> Result<Value> {
    binary(Op::Mul, a, b)
}

/// a / b
pub fn true_div(a: &Value, b: &Value) -> Result<Value> {
    binary(Op::TrueDiv, a, b)
}

/// a // b
pub fn floor_div(a: &Value, b: &Value) -> Result<Value> {
    binary(Op::FloorDiv, a, b)
}

/// a % b
pub fn rem(a: &Value, b: &Value) -> Result<Value> {
    binary(Op::Mod, a, b)
}

/// The order of a and b for `symbol` ("<", ">"): None where they are unordered (NaN).
fn order(symbol: &str, a: &Value, b: &Value) -> Result<Option<Ordering>> {
    if let (Some(_), Some(_)) = (num(a), num(b)) {
        return Ok(compare(a, b));
    }
    match (a, b) {
        (Value::Str(_), Value::Str(_)) | (Value::List(_), Value::List(_)) => fail(
            ErrorKind::Unsupported,
            format!("{} {symbol} {}", a.type_name(), b.type_name()),
        ),
        _ => fail(
            ErrorKind::Type,
            format!(
                "'{symbol}' not supported between instances of '{}' and '{}'",
                a.type_name(),
                b.type_name()
            ),
        ),
    }
}

/// a < b
pub fn lt(a: &Value, b: &Value) -> Result<bool> {
    Ok(order("<", a, b)? == Some(Ordering::Less))
}

/// a > b
pub fn gt(a: &Value, b: &Value) -> Result<bool> {
    Ok(order(">", a, b)? == Some(Ordering::Greater))
}

/// max(a, b): b only where `b > a`.
pub fn max(a: &Value, b: &Value) -> Result<Value> {
    Ok(if gt(b, a)? { b.clone() } else { a.clone() })
}

/// min(a, b): b only where `b < a`.
pub fn min(a: &Value, b: &Value) -> Result<Value> {
    Ok(if lt(b, a)? { b.clone() } else { a.clone() })
}

/// round(x) of a float: the nearest int, halfway the even one.
pub fn round_float(x: f64) -> Result<BigInt> {
    if x.is_nan() {
        return fail(ErrorKind::Value, "cannot convert float NaN to integer");
    }
    if x.is_infinite() {
        return fail(
            ErrorKind::Overflow,
            "cannot convert float infinity to integer",
        );
    }
    Ok(BigInt::from_f64(crate::round(x)).expect("a finite float"))
}

/// round(x): an int (of an int, the int; of a bool, 0 or 1).
pub fn round(v: &Value) -> Result<Value> {
    match num(v) {
        Some(Num::Int(n)) => Ok(Value::Int(n)),
        Some(Num::Float(f)) => round_float(f).map(Value::Int),
        None => fail(
            ErrorKind::Type,
            format!("type {} doesn't define __round__ method", v.type_name()),
        ),
    }
}

/// math.log(x) of a float.
pub fn log(x: f64) -> Result<f64> {
    if x.is_nan() || x == f64::INFINITY {
        return Ok(x);
    }
    if x > 0.0 {
        return Ok(x.ln());
    }
    fail(ErrorKind::Value, "math domain error")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn int(n: i64) -> Value {
        Value::int(n)
    }

    fn big(text: &str) -> BigInt {
        text.parse().expect("digits")
    }

    #[test]
    fn ints_divide_to_the_nearest_float() {
        let two = BigInt::from(2);
        let q = |a: &BigInt, b: &BigInt| int_true_div(a, b).map(f64::to_bits);
        // 2^53 + 1 is no float: the even neighbour
        let a = two.pow(53) + 1;
        assert_eq!(q(&a, &BigInt::from(1)), Ok(9007199254740992f64.to_bits()));
        assert_eq!(int_true_div(&a, &BigInt::from(3)), Ok(3002399751580331.0));
        // the smallest float, and half of it (even: 0)
        assert_eq!(int_true_div(&BigInt::from(1), &two.pow(1074)), Ok(5e-324));
        assert_eq!(int_true_div(&BigInt::from(1), &two.pow(1075)), Ok(0.0));
        assert_eq!(int_true_div(&BigInt::from(3), &two.pow(1075)), Ok(1e-323));
        assert_eq!(
            int_true_div(&(two.pow(1024) - two.pow(971)), &BigInt::from(1)),
            Ok(f64::MAX)
        );
        assert_eq!(
            int_true_div(&(two.pow(1024) - two.pow(970)), &BigInt::from(1)).map_err(|e| e.kind),
            Err(ErrorKind::Overflow)
        );
        assert_eq!(
            int_true_div(&BigInt::from(0), &BigInt::from(-5)).map(f64::to_bits),
            Ok((-0.0f64).to_bits())
        );
        assert_eq!(
            int_true_div(&big(&"9".repeat(400)), &big(&"9".repeat(399))),
            Ok(10.0)
        );
    }

    #[test]
    fn floor_and_rest_as_python() {
        let f = Value::Float;
        let v = |r: Result<Value>| r.map(|v| v.repr()).unwrap_or_else(|e| e.msg);
        assert_eq!(v(floor_div(&int(5), &int(-2))), "-3");
        assert_eq!(v(rem(&int(-5), &int(3))), "1");
        assert_eq!(v(rem(&f(5.5), &int(-2))), "-0.5");
        assert_eq!(v(floor_div(&f(-0.0), &int(1))), "-0.0");
        assert_eq!(v(floor_div(&f(f64::INFINITY), &int(5))), "nan");
        assert_eq!(v(rem(&int(-5), &f(f64::INFINITY))), "inf");
        assert_eq!(v(floor_div(&f(1e308), &f(1e-308))), "inf");
        assert_eq!(v(floor_div(&int(-1), &f(f64::INFINITY))), "-1.0");
        assert_eq!(
            v(floor_div(&int(1), &int(0))),
            "integer division or modulo by zero"
        );
        assert_eq!(v(rem(&int(1), &int(0))), "integer modulo by zero");
        assert_eq!(v(rem(&f(0.0), &f(0.0))), "float modulo");
        assert_eq!(
            v(floor_div(&f(1.5), &int(0))),
            "float floor division by zero"
        );
    }

    #[test]
    fn messages_of_what_is_no_number() {
        let s = Value::from("a");
        let m = |r: Result<Value>| r.unwrap_err().msg;
        assert_eq!(
            m(add(&int(1), &s)),
            "unsupported operand type(s) for +: 'int' and 'str'"
        );
        assert_eq!(
            m(add(&s, &int(1))),
            "can only concatenate str (not \"int\") to str"
        );
        assert_eq!(
            m(sub(&Value::None, &int(1))),
            "unsupported operand type(s) for -: 'NoneType' and 'int'"
        );
        assert_eq!(
            m(mul(&s, &Value::Float(1.5))),
            "can't multiply sequence by non-int of type 'float'"
        );
        assert_eq!(
            gt(&s, &int(48)).unwrap_err().msg,
            "'>' not supported between instances of 'str' and 'int'"
        );
        assert_eq!(max(&int(1), &Value::Float(f64::NAN)).unwrap().repr(), "1");
        assert_eq!(max(&Value::Bool(true), &int(1)).unwrap().repr(), "True");
        assert_eq!(round(&Value::Float(2.5)).unwrap().repr(), "2");
        assert_eq!(
            round(&s).unwrap_err().msg,
            "type str doesn't define __round__ method"
        );
        assert_eq!(add(&s, &s).unwrap_err().kind, ErrorKind::Unsupported);
    }
}
