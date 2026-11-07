//! Python's format() of its values and str.format's reading of a format string, as Python 3.12
//! has them (Python/formatter_unicode.c, Python/pystrtod.c, Objects/stringlib/
//! unicode_format.h): every presentation type of ints, floats and strs with fill, alignment,
//! sign, z, #, 0, width, grouping and precision ('n' in the C locale, as Python starts), the
//! digits of floats as dtoa makes them; and the fields of a format string, their conversions
//! and their nested specs, as string.Formatter goes through them.

use num_bigint::{BigInt, Sign};
use num_traits::{Signed, ToPrimitive};

use crate::{decimal, float_repr, repr, Value};

/// What Python raises.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// ValueError
    Value,
    /// TypeError
    Type,
    /// OverflowError
    Overflow,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FmtError {
    pub kind: Kind,
    pub msg: String,
}

pub type Result<T> = std::result::Result<T, FmtError>;

fn fail(kind: Kind, msg: impl Into<String>) -> FmtError {
    FmtError {
        kind,
        msg: msg.into(),
    }
}

fn value_error<T>(msg: impl Into<String>) -> Result<T> {
    Err(fail(Kind::Value, msg))
}

// -- str(), repr(), ascii(), float() and int() of a value ------------------------------------

fn type_name(v: &Value) -> &'static str {
    match v {
        Value::None => "NoneType",
        Value::Bool(_) => "bool",
        Value::Int(_) => "int",
        Value::Float(_) => "float",
        Value::Str(_) => "str",
        Value::List(_) => "list",
        Value::Dict(_) => "dict",
    }
}

/// repr(value)
pub fn repr_of(v: &Value) -> String {
    match v {
        Value::None => "None".into(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        Value::Int(n) => n.to_string(),
        Value::Float(f) => float_repr(*f),
        Value::Str(s) => repr(s),
        Value::List(items) => {
            let parts: Vec<String> = items.iter().map(repr_of).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Dict(d) => {
            let parts: Vec<String> = d
                .iter()
                .map(|(k, v)| format!("{}: {}", repr(k), repr_of(v)))
                .collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

/// str(value)
pub fn str_of(v: &Value) -> String {
    match v {
        Value::Str(s) => s.clone(),
        other => repr_of(other),
    }
}

/// ascii(value): repr() with what is not ASCII escaped.
pub fn ascii_of(v: &Value) -> String {
    let mut out = String::new();
    for ch in repr_of(v).chars() {
        let c = ch as u32;
        if c < 0x80 {
            out.push(ch);
        } else if c < 0x100 {
            out.push_str(&format!("\\x{c:02x}"));
        } else if c < 0x10000 {
            out.push_str(&format!("\\u{c:04x}"));
        } else {
            out.push_str(&format!("\\U{c:08x}"));
        }
    }
    out
}

/// float(value)
pub fn float_of(v: &Value) -> Result<f64> {
    match v {
        Value::Float(f) => Ok(*f),
        Value::Bool(b) => Ok(if *b { 1.0 } else { 0.0 }),
        Value::Int(n) => int_to_float(n),
        Value::Str(s) => crate::float_of_str(s).ok_or_else(|| {
            fail(
                Kind::Value,
                format!("could not convert string to float: {}", repr(s)),
            )
        }),
        other => Err(fail(
            Kind::Type,
            format!(
                "float() argument must be a string or a real number, not '{}'",
                type_name(other)
            ),
        )),
    }
}

fn int_to_float(n: &BigInt) -> Result<f64> {
    crate::num::int_to_float(n)
        .map_err(|_| fail(Kind::Overflow, "int too large to convert to float"))
}

/// int(value)
pub fn int_of(v: &Value) -> Result<BigInt> {
    match v {
        Value::Int(n) => Ok(n.clone()),
        Value::Bool(b) => Ok(BigInt::from(*b as i64)),
        Value::Float(f) if f.is_nan() => value_error("cannot convert float NaN to integer"),
        Value::Float(f) if f.is_infinite() => Err(fail(
            Kind::Overflow,
            "cannot convert float infinity to integer",
        )),
        Value::Float(f) => Ok(num_traits::FromPrimitive::from_f64(f.trunc()).unwrap_or_default()),
        Value::Str(s) => crate::int_of_str(s).ok_or_else(|| {
            fail(
                Kind::Value,
                format!("invalid literal for int() with base 10: {}", repr(s)),
            )
        }),
        other => Err(fail(
            Kind::Type,
            format!(
                "int() argument must be a string, a bytes-like object or a real number, not '{}'",
                type_name(other)
            ),
        )),
    }
}

// -- the format spec ---------------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq)]
enum Grouping {
    No,
    Comma,
    Under,
    UnderFour,
}

struct Spec {
    fill: char,
    align: char,
    sign: char,
    no_neg_0: bool,
    alternate: bool,
    width: i64,
    grouping: Grouping,
    precision: i64,
    ty: char,
}

/// get_integer: the decimal digits (of any script) at `pos`; how many there were.
fn get_integer(spec: &[char], pos: &mut usize) -> Result<(usize, i64)> {
    let mut n: i64 = 0;
    let mut count = 0;
    while let Some(d) = spec.get(*pos).and_then(|&c| decimal(c)) {
        n = n
            .checked_mul(10)
            .and_then(|n| n.checked_add(d as i64))
            .ok_or_else(|| fail(Kind::Value, "Too many decimal digits in format string"))?;
        *pos += 1;
        count += 1;
    }
    Ok((count, n))
}

/// parse_internal_render_format_spec
fn parse_spec(spec: &str, default_type: char, default_align: char, name: &str) -> Result<Spec> {
    let s: Vec<char> = spec.chars().collect();
    let end = s.len();
    let mut pos = 0;
    let align_token = |c: char| matches!(c, '<' | '>' | '=' | '^');
    let mut out = Spec {
        fill: ' ',
        align: default_align,
        sign: '\0',
        no_neg_0: false,
        alternate: false,
        width: -1,
        grouping: Grouping::No,
        precision: -1,
        ty: default_type,
    };
    let (mut fill_given, mut align_given) = (false, false);
    if end - pos >= 2 && align_token(s[pos + 1]) {
        out.align = s[pos + 1];
        out.fill = s[pos];
        fill_given = true;
        align_given = true;
        pos += 2;
    } else if end - pos >= 1 && align_token(s[pos]) {
        out.align = s[pos];
        align_given = true;
        pos += 1;
    }
    if end - pos >= 1 && matches!(s[pos], ' ' | '+' | '-') {
        out.sign = s[pos];
        pos += 1;
    }
    if end - pos >= 1 && s[pos] == 'z' {
        out.no_neg_0 = true;
        pos += 1;
    }
    if end - pos >= 1 && s[pos] == '#' {
        out.alternate = true;
        pos += 1;
    }
    if !fill_given && end - pos >= 1 && s[pos] == '0' {
        out.fill = '0';
        if !align_given && default_align == '>' {
            out.align = '=';
        }
        pos += 1;
    }
    let (count, width) = get_integer(&s, &mut pos)?;
    out.width = if count == 0 { -1 } else { width };
    if end > pos && s[pos] == ',' {
        out.grouping = Grouping::Comma;
        pos += 1;
    }
    if end > pos && s[pos] == '_' {
        if out.grouping != Grouping::No {
            return value_error("Cannot specify both ',' and '_'.");
        }
        out.grouping = Grouping::Under;
        pos += 1;
    }
    if end > pos && s[pos] == ',' && out.grouping == Grouping::Under {
        return value_error("Cannot specify both ',' and '_'.");
    }
    if end > pos && s[pos] == '.' {
        pos += 1;
        let (count, precision) = get_integer(&s, &mut pos)?;
        if count == 0 {
            return value_error("Format specifier missing precision");
        }
        out.precision = precision;
    }
    if end - pos > 1 {
        return value_error(format!(
            "Invalid format specifier '{spec}' for object of type '{name}'"
        ));
    }
    if end - pos == 1 {
        out.ty = s[pos];
    }
    if out.grouping != Grouping::No {
        match out.ty {
            'd' | 'e' | 'f' | 'g' | 'E' | 'G' | '%' | 'F' | '\0' => {}
            'b' | 'o' | 'x' | 'X' if out.grouping == Grouping::Under => {
                out.grouping = Grouping::UnderFour;
            }
            ty => {
                let sep = if out.grouping == Grouping::Comma {
                    ','
                } else {
                    '_'
                };
                return value_error(format!("Cannot specify '{sep}' with '{ty}'."));
            }
        }
    }
    Ok(out)
}

fn unknown(ty: char, name: &str) -> FmtError {
    fail(
        Kind::Value,
        format!("Unknown format code '{ty}' for object of type '{name}'"),
    )
}

// -- numbers: widths, grouping and filling (calc_number_widths, fill_number) -------------------

/// The thousands separator and the sizes of its groups (LocaleInfo); 'n' in the C locale
/// groups nothing.
fn locale_of(grouping: Grouping, ty: char) -> (&'static str, Option<usize>) {
    if ty == 'n' {
        return ("", None);
    }
    match grouping {
        Grouping::No => ("", None),
        Grouping::Comma => (",", Some(3)),
        Grouping::Under => ("_", Some(3)),
        Grouping::UnderFour => ("_", Some(4)),
    }
}

/// _PyUnicode_InsertThousandsGrouping: `digits` grouped from the right (with zeros before
/// them up to `min_width`, the separators counted in it).
fn group(digits: &[char], min_width: i64, sep: &str, size: Option<usize>) -> Vec<char> {
    let sep_len = sep.chars().count() as i64;
    let mut min_width = min_width.max(0);
    let mut remaining = digits.len() as i64;
    let mut end = digits.len();
    let mut pieces: Vec<Vec<char>> = Vec::new(); // from the right
    let mut piece = |n_chars: i64, n_zeros: i64, with_sep: bool, end: &mut usize| {
        let mut p: Vec<char> = Vec::new();
        p.extend(std::iter::repeat_n('0', n_zeros.max(0) as usize));
        let from = *end - n_chars as usize;
        p.extend_from_slice(&digits[from..*end]);
        *end = from;
        if with_sep {
            p.extend(sep.chars());
        }
        pieces.push(p);
    };
    let mut use_sep = false;
    let mut broken = false;
    if let Some(size) = size {
        loop {
            let len = (size as i64).min(remaining.max(min_width).max(1));
            let n_zeros = (len - remaining).max(0);
            let n_chars = remaining.min(len).max(0);
            piece(n_chars, n_zeros, use_sep, &mut end);
            use_sep = true;
            remaining -= n_chars;
            min_width -= len;
            if remaining <= 0 && min_width <= 0 {
                broken = true;
                break;
            }
            min_width -= sep_len;
        }
    }
    if !broken {
        let len = remaining.max(min_width).max(1);
        let n_zeros = (len - remaining).max(0);
        let n_chars = remaining.min(len).max(0);
        piece(n_chars, n_zeros, use_sep, &mut end);
    }
    pieces.into_iter().rev().flatten().collect()
}

/// A number's text with its sign, prefix, padding and grouping: `number` is its digits, then
/// what is no digit (a decimal point: `has_decimal`; the fraction, an exponent, '%', "inf").
fn fill_number(
    spec: &Spec,
    sign_char: char,
    prefix: &str,
    digits: &[char],
    has_decimal: bool,
    remainder: &[char],
    toupper: bool,
) -> String {
    let (sep, size) = locale_of(spec.grouping, spec.ty);
    let (n_sign, sign) = match spec.sign {
        '+' => (1, if sign_char == '-' { '-' } else { '+' }),
        ' ' => (1, if sign_char == '-' { '-' } else { ' ' }),
        _ if sign_char == '-' => (1, '-'),
        _ => (0, '\0'),
    };
    let n_prefix = prefix.chars().count() as i64;
    let n_decimal = has_decimal as i64;
    let non_digit = n_sign + n_prefix + n_decimal + remainder.len() as i64;
    let min_width = if spec.fill == '0' && spec.align == '=' {
        spec.width - non_digit
    } else {
        0
    };
    let grouped: Vec<char> = if digits.is_empty() {
        Vec::new()
    } else {
        group(digits, min_width, sep, size)
    };
    let padding = spec.width - (non_digit + grouped.len() as i64);
    let (mut lpad, mut spad, mut rpad) = (0, 0, 0);
    if padding > 0 {
        match spec.align {
            '<' => rpad = padding,
            '^' => {
                lpad = padding / 2;
                rpad = padding - lpad;
            }
            '=' => spad = padding,
            _ => lpad = padding,
        }
    }
    let fill = |n: i64| std::iter::repeat_n(spec.fill, n.max(0) as usize);
    let mut out = String::new();
    out.extend(fill(lpad));
    if n_sign == 1 {
        out.push(sign);
    }
    if toupper {
        out.push_str(&prefix.to_ascii_uppercase());
    } else {
        out.push_str(prefix);
    }
    out.extend(fill(spad));
    if toupper {
        out.extend(grouped.iter().map(|c| c.to_ascii_uppercase()));
    } else {
        out.extend(grouped.iter());
    }
    if has_decimal {
        out.push('.');
    }
    out.extend(remainder.iter());
    out.extend(fill(rpad));
    out
}

// -- ints ---------------------------------------------------------------------------------------

/// format_long_internal
fn format_int(n: &BigInt, spec: &Spec) -> Result<String> {
    if spec.precision != -1 {
        return value_error("Precision not allowed in integer format specifier");
    }
    if spec.no_neg_0 {
        return value_error("Negative zero coercion (z) not allowed in integer format specifier");
    }
    if spec.ty == 'c' {
        if spec.sign != '\0' {
            return value_error("Sign not allowed with integer format specifier 'c'");
        }
        if spec.alternate {
            return value_error("Alternate form (#) not allowed with integer format specifier 'c'");
        }
        let x = n
            .to_i64()
            .ok_or_else(|| fail(Kind::Overflow, "Python int too large to convert to C long"))?;
        if !(0..=0x10ffff).contains(&x) {
            return Err(fail(Kind::Overflow, "%c arg not in range(0x110000)"));
        }
        // (a half of a surrogate pair, which Python makes, a Rust str cannot hold)
        let ch = char::from_u32(x as u32)
            .ok_or_else(|| fail(Kind::Value, "a surrogate: no character of a Rust str"))?;
        return Ok(fill_number(spec, '\0', "", &[], false, &[ch], false));
    }
    let (base, prefix) = match spec.ty {
        'b' => (2, "0b"),
        'o' => (8, "0o"),
        'x' | 'X' => (16, "0x"),
        _ => (10, ""),
    };
    let digits: Vec<char> = n.abs().to_str_radix(base).chars().collect();
    let sign_char = if n.sign() == Sign::Minus { '-' } else { '\0' };
    let prefix = if spec.alternate { prefix } else { "" };
    Ok(fill_number(
        spec,
        sign_char,
        prefix,
        &digits,
        false,
        &[],
        spec.ty == 'X',
    ))
}

// -- floats -------------------------------------------------------------------------------------

/// _Py_dg_dtoa of |x| (finite): its digits, none to spare ("0" for zero), and where the
/// decimal point is; mode 0 the shortest that reads back, 2 `n` significant digits, 3 `n`
/// digits after the point, each to the nearest.
fn dtoa(x: f64, mode: u8, n: usize) -> (Vec<char>, i64) {
    let x = x.abs();
    if x == 0.0 {
        return (vec!['0'], 1);
    }
    let from_exp = |text: String| -> (Vec<char>, i64) {
        let (mantissa, exp) = text.split_once('e').expect("an exponent");
        let mut digits: Vec<char> = mantissa.chars().filter(|&c| c != '.').collect();
        while digits.len() > 1 && digits.last() == Some(&'0') {
            digits.pop();
        }
        (digits, exp.parse::<i64>().expect("an exponent") + 1)
    };
    match mode {
        0 => from_exp(format!("{x:e}")),
        2 => from_exp(format!("{:.*e}", n.max(1) - 1, x)),
        _ => {
            let text = format!("{x:.n$}");
            let (int, frac) = text.split_once('.').unwrap_or((&text, ""));
            let mut digits: Vec<char> = int.chars().chain(frac.chars()).collect();
            let mut decpt = int.len() as i64;
            let lead = digits.iter().take_while(|&&c| c == '0').count();
            if lead == digits.len() {
                return (vec!['0'], 1);
            }
            digits.drain(..lead);
            decpt -= lead as i64;
            while digits.last() == Some(&'0') {
                digits.pop();
            }
            (digits, decpt)
        }
    }
}

/// PyOS_double_to_string (format_float_short): `code` 'r', 'e', 'f' or 'g' (lower case),
/// with the flags Python's format passes it.
fn double_to_string(
    x: f64,
    code: char,
    precision: i64,
    add_dot_0: bool,
    alt: bool,
    no_neg_0: bool,
    upper: bool,
) -> String {
    let mut sign = x.is_sign_negative();
    if x.is_nan() || x.is_infinite() {
        let word = if x.is_nan() {
            sign = false; // (the sign of a nan is not shown)
            "nan"
        } else {
            "inf"
        };
        let word = if upper {
            word.to_ascii_uppercase()
        } else {
            word.into()
        };
        return format!("{}{word}", if sign { "-" } else { "" });
    }
    let (mode, ndigits) = match code {
        'r' => (0, 0),
        'e' => (2, precision + 1),
        'f' => (3, precision),
        _ => (2, if precision == 0 { 1 } else { precision }),
    };
    let precision = if code == 'g' && precision == 0 {
        1
    } else {
        precision
    };
    let (digits, mut decpt) = dtoa(x, mode, ndigits.max(0) as usize);
    let digits_len = digits.len() as i64;
    if no_neg_0 && sign && (digits_len == 0 || (digits_len == 1 && digits[0] == '0')) {
        sign = false;
    }
    let mut vdigits_end = digits_len;
    let mut use_exp = false;
    match code {
        'e' => {
            use_exp = true;
            vdigits_end = precision + 1;
        }
        'f' => vdigits_end = decpt + precision,
        'g' => {
            let limit = if add_dot_0 { precision - 1 } else { precision };
            if decpt <= -4 || decpt > limit {
                use_exp = true;
            }
            if alt {
                vdigits_end = precision;
            }
        }
        _ => {
            if decpt <= -4 || decpt > 16 {
                use_exp = true;
            }
        }
    }
    let mut exp = 0;
    if use_exp {
        exp = decpt - 1;
        decpt = 1;
    }
    let vdigits_start = if decpt <= 0 { decpt - 1 } else { 0 };
    vdigits_end = if !use_exp && add_dot_0 {
        vdigits_end.max(decpt + 1)
    } else {
        vdigits_end.max(decpt)
    };
    let zeros = |n: i64| "0".repeat(n.max(0) as usize);
    let mut out = String::new();
    if sign {
        out.push('-');
    }
    if decpt <= 0 {
        out.push_str(&zeros(decpt - vdigits_start));
        out.push('.');
        out.push_str(&zeros(-decpt));
    } else {
        out.push_str(&zeros(-vdigits_start));
    }
    if 0 < decpt && decpt <= digits_len {
        out.extend(&digits[..decpt as usize]);
        out.push('.');
        out.extend(&digits[decpt as usize..]);
    } else {
        out.extend(&digits);
    }
    if digits_len < decpt {
        out.push_str(&zeros(decpt - digits_len));
        out.push('.');
        out.push_str(&zeros(vdigits_end - decpt));
    } else {
        out.push_str(&zeros(vdigits_end - digits_len));
    }
    if out.ends_with('.') && !alt {
        out.pop();
    }
    if use_exp {
        out.push(if upper { 'E' } else { 'e' });
        out.push(if exp < 0 { '-' } else { '+' });
        out.push_str(&format!("{:02}", exp.abs()));
    }
    out
}

/// format_float_internal
fn format_float(x: f64, spec: &Spec) -> Result<String> {
    let mut ty = spec.ty;
    let mut precision = spec.precision;
    let mut add_dot_0 = false;
    let mut default_precision = 6;
    if ty == '\0' {
        add_dot_0 = true;
        ty = 'r';
        default_precision = 0;
    }
    if ty == 'n' {
        ty = 'g';
    }
    let mut x = x;
    let mut pct = false;
    if ty == '%' {
        ty = 'f';
        x *= 100.0;
        pct = true;
    }
    if precision < 0 {
        precision = default_precision;
    } else if ty == 'r' {
        ty = 'g';
    }
    let upper = matches!(ty, 'E' | 'F' | 'G');
    let code = ty.to_ascii_lowercase();
    let mut text = double_to_string(
        x,
        code,
        precision,
        add_dot_0,
        spec.alternate,
        spec.no_neg_0,
        upper,
    );
    if pct {
        text.push('%');
    }
    let chars: Vec<char> = text.chars().collect();
    let (sign_char, body) = match chars.first() {
        Some('-') => ('-', &chars[1..]),
        _ => ('\0', &chars[..]),
    };
    let n_int = body.iter().take_while(|c| c.is_ascii_digit()).count();
    let has_decimal = body.get(n_int) == Some(&'.');
    let rest = &body[n_int + has_decimal as usize..];
    Ok(fill_number(
        spec,
        sign_char,
        "",
        &body[..n_int],
        has_decimal,
        rest,
        false,
    ))
}

// -- strs ---------------------------------------------------------------------------------------

/// format_string_internal
fn format_str(s: &str, spec: &Spec) -> Result<String> {
    if spec.sign != '\0' {
        return value_error(if spec.sign == ' ' {
            "Space not allowed in string format specifier"
        } else {
            "Sign not allowed in string format specifier"
        });
    }
    if spec.no_neg_0 {
        return value_error("Negative zero coercion (z) not allowed in string format specifier");
    }
    if spec.alternate {
        return value_error("Alternate form (#) not allowed in string format specifier");
    }
    if spec.align == '=' {
        return value_error("'=' alignment not allowed in string format specifier");
    }
    let chars: Vec<char> = s.chars().collect();
    let mut len = chars.len() as i64;
    if spec.precision >= 0 && len >= spec.precision {
        len = spec.precision;
    }
    let total = if spec.width >= 0 {
        len.max(spec.width)
    } else {
        len
    };
    let lpad = match spec.align {
        '>' => total - len,
        '^' => (total - len) / 2,
        _ => 0,
    };
    let rpad = total - len - lpad;
    let mut out: String = std::iter::repeat_n(spec.fill, lpad as usize).collect();
    out.extend(&chars[..len as usize]);
    out.extend(std::iter::repeat_n(spec.fill, rpad as usize));
    Ok(out)
}

// -- format() ---------------------------------------------------------------------------------

/// format(value, spec)
pub fn format(value: &Value, spec: &str) -> Result<String> {
    match value {
        Value::Str(s) => {
            if spec.is_empty() {
                return Ok(s.clone());
            }
            let parsed = parse_spec(spec, 's', '<', "str")?;
            match parsed.ty {
                's' => format_str(s, &parsed),
                ty => Err(unknown(ty, "str")),
            }
        }
        Value::Int(_) | Value::Bool(_) => {
            if spec.is_empty() {
                return Ok(str_of(value));
            }
            let name = type_name(value);
            let n = match value {
                Value::Int(n) => n.clone(),
                Value::Bool(b) => BigInt::from(*b as i64),
                _ => unreachable!(),
            };
            let parsed = parse_spec(spec, 'd', '>', name)?;
            match parsed.ty {
                'b' | 'c' | 'd' | 'o' | 'x' | 'X' | 'n' => format_int(&n, &parsed),
                'e' | 'E' | 'f' | 'F' | 'g' | 'G' | '%' => format_float(int_to_float(&n)?, &parsed),
                ty => Err(unknown(ty, name)),
            }
        }
        Value::Float(x) => {
            if spec.is_empty() {
                return Ok(float_repr(*x));
            }
            let parsed = parse_spec(spec, '\0', '>', "float")?;
            match parsed.ty {
                '\0' | 'e' | 'E' | 'f' | 'F' | 'g' | 'G' | 'n' | '%' => format_float(*x, &parsed),
                ty => Err(unknown(ty, "float")),
            }
        }
        other => {
            // object.__format__
            if spec.is_empty() {
                Ok(str_of(other))
            } else {
                Err(fail(
                    Kind::Type,
                    format!(
                        "unsupported format string passed to {}.__format__",
                        type_name(other)
                    ),
                ))
            }
        }
    }
}

// -- str.format: the fields of a format string ----------------------------------------------

/// A piece of a format string (_string.formatter_parser): its literal text, and the field
/// after it, if any: its name, its spec and its conversion.
pub struct Piece {
    pub literal: String,
    pub field: Option<(String, String, Option<char>)>,
}

/// _string.formatter_parser (MarkupIterator): the pieces of a format string one by one, as
/// Python reads them: a ValueError where it finds one, after the pieces before it (which
/// str.format has made by then: their errors come first).
pub struct Pieces {
    s: Vec<char>,
    pos: usize,
    failed: bool,
}

/// The pieces of `text` one by one.
pub fn pieces(text: &str) -> Pieces {
    Pieces {
        s: text.chars().collect(),
        pos: 0,
        failed: false,
    }
}

/// All the pieces of `text`, or its first ValueError.
pub fn parse(text: &str) -> Result<Vec<Piece>> {
    pieces(text).collect()
}

impl Iterator for Pieces {
    type Item = Result<Piece>;

    fn next(&mut self) -> Option<Result<Piece>> {
        if self.failed || self.pos >= self.s.len() {
            return None;
        }
        let piece = self.piece();
        self.failed = piece.is_err();
        Some(piece)
    }
}

impl Pieces {
    /// MarkupIterator_next: the literal text up to the next field, and that field
    /// (parse_field).
    fn piece(&mut self) -> Result<Piece> {
        let s = &self.s;
        let end = s.len();
        let mut pos = self.pos;
        let start = pos;
        let mut c = '\0';
        let mut markup = false;
        while pos < end {
            c = s[pos];
            pos += 1;
            if c == '{' || c == '}' {
                markup = true;
                break;
            }
        }
        let at_end = pos >= end;
        let mut len = pos - start;
        if c == '}' && markup && (at_end || s[pos] != c) {
            return value_error("Single '}' encountered in format string");
        }
        if at_end && c == '{' && markup {
            return value_error("Single '{' encountered in format string");
        }
        if !at_end && markup {
            if s[pos] == c {
                pos += 1; // an escaped { or }: literal text only
                markup = false;
            } else {
                len -= 1;
            }
        }
        let literal: String = s[start..start + len].iter().collect();
        if !markup {
            self.pos = pos;
            return Ok(Piece {
                literal,
                field: None,
            });
        }
        // parse_field
        let name_start = pos;
        let mut c = '\0';
        while pos < end {
            c = s[pos];
            pos += 1;
            match c {
                '{' => return value_error("unexpected '{' in field name"),
                '[' => {
                    while pos < end && s[pos] != ']' {
                        pos += 1;
                    }
                    continue;
                }
                '}' | ':' | '!' => break,
                _ => continue,
            }
        }
        if !matches!(c, '}' | ':' | '!') {
            return value_error("expected '}' before end of string");
        }
        let name: String = s[name_start..pos - 1].iter().collect();
        let mut conversion = None;
        let mut spec = String::new();
        if c == '!' || c == ':' {
            let mut done = false;
            if c == '!' {
                if pos >= end {
                    return value_error("end of string while looking for conversion specifier");
                }
                // (a NUL is no conversion: formatter_parser gives None for it)
                conversion = Some(s[pos]).filter(|&c| c != '\0');
                pos += 1;
                if pos < end {
                    let next = s[pos];
                    pos += 1;
                    if next == '}' {
                        done = true;
                    } else if next != ':' {
                        return value_error("expected ':' after conversion specifier");
                    }
                }
            }
            if !done {
                let spec_start = pos;
                let mut count = 1;
                let mut closed = false;
                while pos < end {
                    let c = s[pos];
                    pos += 1;
                    if c == '{' {
                        count += 1;
                    } else if c == '}' {
                        count -= 1;
                        if count == 0 {
                            spec = s[spec_start..pos - 1].iter().collect();
                            closed = true;
                            break;
                        }
                    }
                }
                if !closed {
                    return value_error("unmatched '{' in format spec");
                }
            }
        }
        self.pos = pos;
        Ok(Piece {
            literal,
            field: Some((name, spec, conversion)),
        })
    }
}

/// string.Formatter.vformat: `text` with its fields: `get_field` gives a field's value (by its
/// whole name, as get_field has it), `format_field` makes the text of a value in a spec.
pub fn vformat(
    text: &str,
    get_field: &dyn Fn(&str) -> Result<Value>,
    format_field: &dyn Fn(&Value, &str) -> Result<String>,
) -> Result<String> {
    let mut auto = Some(0usize);
    vformat_at(text, get_field, format_field, 2, &mut auto)
}

fn vformat_at(
    text: &str,
    get_field: &dyn Fn(&str) -> Result<Value>,
    format_field: &dyn Fn(&Value, &str) -> Result<String>,
    depth: i32,
    auto: &mut Option<usize>,
) -> Result<String> {
    if depth < 0 {
        return value_error("Max string recursion exceeded");
    }
    let mut out = String::new();
    for piece in pieces(text) {
        let piece = piece?;
        out.push_str(&piece.literal);
        let Some((name, spec, conversion)) = piece.field else {
            continue;
        };
        let name = if name.is_empty() {
            let Some(n) = *auto else {
                return value_error(
                    "cannot switch from manual field specification to automatic field numbering",
                );
            };
            *auto = Some(n + 1);
            n.to_string()
        } else {
            if name.chars().all(crate::isdigit) {
                if auto.is_some_and(|n| n > 0) {
                    return value_error(
                        "cannot switch from manual field specification to automatic field numbering",
                    );
                }
                *auto = None;
            }
            name
        };
        let value = get_field(&name)?;
        let value = match conversion {
            None => value,
            Some('s') => Value::Str(str_of(&value)),
            Some('r') => Value::Str(repr_of(&value)),
            Some('a') => Value::Str(ascii_of(&value)),
            Some(other) => return value_error(format!("Unknown conversion specifier {other}")),
        };
        let spec = vformat_at(&spec, get_field, format_field, depth - 1, auto)?;
        out.push_str(&format_field(&value, &spec)?);
    }
    Ok(out)
}
