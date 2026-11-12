//! Python 3.12's json module (the release's; 3.13 words some refusals differently) as the app
//! reads and writes its files (theme.json above all): loads()
//! with what Python's reader allows (NaN, Infinity, ints of any size, a key given twice) and
//! refuses, in the words and at the place Python names (`Expecting ',' delimiter: line 3 column
//! 5 (char 41)`); dumps() as json.dumps writes, with its indent, separators, ensure_ascii and
//! sort_keys.
//!
//! What differs: a str in Rust cannot hold half of a surrogate pair ("\ud800" alone), which
//! Python's str can (and which the app cannot save again): a file Python reads with one still
//! in what it reads is refused here, once all of it has been read as Python reads it (so a file
//! Python refuses for something else is refused for that, in its words). And Python stops
//! at its C recursion limit (10000 calls on Linux and macOS, 3000 on Windows by CPython 3.12's
//! source, less those already made): a file nested deeper than 9997 levels (2997 on Windows) is
//! refused, as Python refuses it from the top of its stack (measured on Linux: 9997).

use std::fmt;

use num_bigint::BigInt;

use crate::float_repr;
use crate::value::{Dict, Value};

/// What went wrong, as Python would raise it.
#[derive(Clone, Debug, PartialEq)]
pub enum ErrorKind {
    /// json.JSONDecodeError: the message, and where (in characters)
    Decode {
        pos: usize,
        line: usize,
        column: usize,
    },
    /// ValueError (an int of more digits than Python reads, half a surrogate pair)
    Value,
    /// RecursionError
    Recursion,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Error {
    pub kind: ErrorKind,
    /// the message without the place (JSONDecodeError's msg)
    pub msg: String,
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.kind {
            ErrorKind::Decode { pos, line, column } => {
                write!(f, "{}: line {line} column {column} (char {pos})", self.msg)
            }
            _ => f.write_str(&self.msg),
        }
    }
}

/// The digits Python reads an int of at most (sys.get_int_max_str_digits()).
pub const INT_MAX_STR_DIGITS: usize = 4300;

/// How deep arrays and objects nest before Python's recursion limit (see above).
pub const MAX_DEPTH: usize = if cfg!(windows) { 2997 } else { 9997 };

fn decode_error(msg: &str, doc: &[char], pos: usize) -> Error {
    let line = doc[..pos].iter().filter(|&&c| c == '\n').count() + 1;
    let column = match doc[..pos].iter().rposition(|&c| c == '\n') {
        Some(newline) => pos - newline,
        None => pos + 1,
    };
    Error {
        kind: ErrorKind::Decode { pos, line, column },
        msg: msg.to_string(),
    }
}

fn white(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\r')
}

fn skip_white(s: &[char], mut idx: usize) -> usize {
    while idx < s.len() && white(s[idx]) {
        idx += 1;
    }
    idx
}

fn hex(c: char) -> Option<u32> {
    c.to_digit(16).filter(|_| c.is_ascii_hexdigit())
}

/// The four hex digits of a \u escape from `at`; Err: where Python says the escape is bad.
fn four(s: &[char], at: usize, bad: usize, doc: &[char]) -> Result<u32, Error> {
    let mut c = 0;
    for &digit in &s[at..at + 4] {
        match hex(digit) {
            Some(d) => c = (c << 4) | d,
            None => return Err(decode_error("Invalid \\uXXXX escape", doc, bad)),
        }
    }
    Ok(c)
}

/// Where the first half of a surrogate pair stands alone in a value as Python reads it (Python
/// keeps such a half in its str; here it is U+FFFD while the file is read, and the file is
/// refused at its end if the half is still in what was read: a dict's value given again for the
/// same key is gone, and the half in it with it).
type Half = Option<usize>;

fn first(a: Half, b: Half) -> Half {
    match (a, b) {
        (Some(x), Some(y)) => Some(x.min(y)),
        _ => a.or(b),
    }
}

/// The str that starts after the quote at `end - 1` (_json.c's scanstring_unicode, strict),
/// where it ends, and a half of a surrogate pair in it.
fn scanstring(s: &[char], end: usize) -> Result<(String, usize, Half), Error> {
    let len = s.len();
    let begin = end - 1;
    let mut out = String::new();
    let mut half = None;
    let mut end = end;
    loop {
        let mut next = end;
        let mut c = '\0';
        while next < len {
            c = s[next];
            if c == '"' || c == '\\' {
                break;
            }
            if (c as u32) <= 0x1f {
                return Err(decode_error("Invalid control character at", s, next));
            }
            next += 1;
        }
        if next >= len || (c != '"' && c != '\\') {
            return Err(decode_error("Unterminated string starting at", s, begin));
        }
        out.extend(&s[end..next]);
        next += 1;
        if c == '"' {
            return Ok((out, next, half));
        }
        if next == len {
            return Err(decode_error("Unterminated string starting at", s, begin));
        }
        let escaped = s[next];
        if escaped != 'u' {
            end = next + 1;
            out.push(match escaped {
                '"' => '"',
                '\\' => '\\',
                '/' => '/',
                'b' => '\u{8}',
                'f' => '\u{c}',
                'n' => '\n',
                'r' => '\r',
                't' => '\t',
                _ => return Err(decode_error("Invalid \\escape", s, end - 2)),
            });
            continue;
        }
        next += 1;
        end = next + 4;
        if end >= len {
            return Err(decode_error("Invalid \\uXXXX escape", s, next - 1));
        }
        let mut code = four(s, next, end - 5, s)?;
        next = end;
        // a surrogate pair: the second half must follow at once
        if (0xD800..0xDC00).contains(&code)
            && end + 6 < len
            && s[next] == '\\'
            && s[next + 1] == 'u'
        {
            let low = four(s, next + 2, end + 1, s)?;
            if (0xDC00..0xE000).contains(&low) {
                code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                end += 6;
            }
        }
        match char::from_u32(code) {
            Some(ch) => out.push(ch),
            None => {
                half = first(half, Some(end - 6));
                out.push('\u{fffd}');
            }
        }
    }
}

/// A number at `start` (_json.c's _match_number_unicode); None where there is none.
fn number(s: &[char], start: usize) -> Result<Option<(Value, usize)>, Error> {
    let digit = |i: usize| i < s.len() && s[i].is_ascii_digit();
    let end_idx = s.len() - 1;
    let mut idx = start;
    if s[idx] == '-' {
        idx += 1;
        if idx > end_idx {
            return Ok(None);
        }
    }
    if ('1'..='9').contains(&s[idx]) {
        idx += 1;
        while digit(idx) {
            idx += 1;
        }
    } else if s[idx] == '0' {
        idx += 1;
    } else {
        return Ok(None);
    }
    let mut float = false;
    if idx < end_idx && s[idx] == '.' && digit(idx + 1) {
        float = true;
        idx += 2;
        while digit(idx) {
            idx += 1;
        }
    }
    if idx < end_idx && (s[idx] == 'e' || s[idx] == 'E') {
        let e_start = idx;
        idx += 1;
        if idx < end_idx && (s[idx] == '-' || s[idx] == '+') {
            idx += 1;
        }
        while digit(idx) {
            idx += 1;
        }
        if s[idx - 1].is_ascii_digit() {
            float = true;
        } else {
            idx = e_start;
        }
    }
    let text: String = s[start..idx].iter().collect();
    if float {
        return Ok(Some((
            Value::Float(text.parse().expect("a float of JSON's")),
            idx,
        )));
    }
    let digits = text.trim_start_matches('-').len();
    if digits > INT_MAX_STR_DIGITS {
        return Err(Error {
            kind: ErrorKind::Value,
            msg: format!(
                "Exceeds the limit ({INT_MAX_STR_DIGITS} digits) for integer string conversion: \
                 value has {digits} digits; use sys.set_int_max_str_digits() to increase the limit"
            ),
        });
    }
    let n: BigInt = text.parse().expect("an int of JSON's");
    Ok(Some((Value::Int(n), idx)))
}

/// A dict being read: its items so far, the key whose value comes next, and the halves of
/// surrogate pairs in its keys (which stay, as Python keeps every key) and in each key's value
/// (which goes with the value when the key is given again).
struct OpenDict {
    dict: Dict,
    name: String,
    in_keys: Half,
    in_values: std::collections::HashMap<String, usize>,
}

/// What is being read: a list's items so far (and a half in them), or a dict.
enum Open {
    List(Vec<Value>, Half),
    Dict(Box<OpenDict>),
}

fn too_deep(what: &str) -> Error {
    Error {
        kind: ErrorKind::Recursion,
        msg: format!(
            "maximum recursion depth exceeded while decoding a JSON {what} from a unicode string"
        ),
    }
}

fn starts(s: &[char], idx: usize, word: &str) -> bool {
    let n = word.chars().count();
    idx + n <= s.len() && s[idx..idx + n].iter().copied().eq(word.chars())
}

/// The key of a dict's next item at `idx`, where its value starts, and a half in the key.
fn key(s: &[char], idx: usize) -> Result<(String, usize, Half), Error> {
    if idx >= s.len() || s[idx] != '"' {
        return Err(decode_error(
            "Expecting property name enclosed in double quotes",
            s,
            idx,
        ));
    }
    let (key, next, half) = scanstring(s, idx + 1)?;
    let idx = skip_white(s, next);
    if idx >= s.len() || s[idx] != ':' {
        return Err(decode_error("Expecting ':' delimiter", s, idx));
    }
    Ok((key, skip_white(s, idx + 1), half))
}

/// One value from `idx` (scan_once), the lists and dicts in it read without recursing; where
/// it ends, and a half of a surrogate pair still in it.
fn scan(s: &[char], mut idx: usize) -> Result<(Value, usize, Half), Error> {
    let mut open: Vec<Open> = Vec::new();
    loop {
        // a value at idx
        if idx >= s.len() {
            return Err(decode_error("Expecting value", s, idx));
        }
        let mut half = None;
        let mut value = match s[idx] {
            '"' => {
                let (text, next, in_text) = scanstring(s, idx + 1)?;
                idx = next;
                half = in_text;
                Value::Str(text)
            }
            '{' => {
                if open.len() >= MAX_DEPTH {
                    return Err(too_deep("object"));
                }
                let after = skip_white(s, idx + 1);
                if after < s.len() && s[after] == '}' {
                    idx = after + 1;
                    Value::Dict(Dict::default())
                } else {
                    let (name, next, in_key) = key(s, after)?;
                    open.push(Open::Dict(Box::new(OpenDict {
                        dict: Dict::default(),
                        name,
                        in_keys: in_key,
                        in_values: Default::default(),
                    })));
                    idx = next;
                    continue;
                }
            }
            '[' => {
                if open.len() >= MAX_DEPTH {
                    return Err(too_deep("array"));
                }
                let after = skip_white(s, idx + 1);
                if after < s.len() && s[after] == ']' {
                    idx = after + 1;
                    Value::List(Vec::new())
                } else {
                    open.push(Open::List(Vec::new(), None));
                    idx = after;
                    continue;
                }
            }
            'n' if starts(s, idx, "null") => {
                idx += 4;
                Value::None
            }
            't' if starts(s, idx, "true") => {
                idx += 4;
                Value::Bool(true)
            }
            'f' if starts(s, idx, "false") => {
                idx += 5;
                Value::Bool(false)
            }
            'N' if starts(s, idx, "NaN") => {
                idx += 3;
                Value::Float(f64::NAN)
            }
            'I' if starts(s, idx, "Infinity") => {
                idx += 8;
                Value::Float(f64::INFINITY)
            }
            '-' if starts(s, idx, "-Infinity") => {
                idx += 9;
                Value::Float(f64::NEG_INFINITY)
            }
            _ => match number(s, idx)? {
                Some((number, next)) => {
                    idx = next;
                    number
                }
                None => return Err(decode_error("Expecting value", s, idx)),
            },
        };
        // the value into what is open, closing what it ends
        loop {
            match open.last_mut() {
                None => return Ok((value, idx, half)),
                Some(Open::List(items, in_items)) => {
                    items.push(value);
                    *in_items = first(*in_items, half);
                    idx = skip_white(s, idx);
                    if idx < s.len() && s[idx] == ']' {
                        idx += 1;
                        let Some(Open::List(items, in_items)) = open.pop() else {
                            unreachable!()
                        };
                        value = Value::List(items);
                        half = in_items;
                        continue;
                    }
                    if idx >= s.len() || s[idx] != ',' {
                        return Err(decode_error("Expecting ',' delimiter", s, idx));
                    }
                    idx = skip_white(s, idx + 1);
                    break;
                }
                Some(Open::Dict(d)) => {
                    let name = std::mem::take(&mut d.name);
                    match half {
                        Some(at) => d.in_values.insert(name.clone(), at),
                        None => d.in_values.remove(&name),
                    };
                    d.dict.insert(name, value);
                    idx = skip_white(s, idx);
                    if idx < s.len() && s[idx] == '}' {
                        idx += 1;
                        let Some(Open::Dict(d)) = open.pop() else {
                            unreachable!()
                        };
                        half = first(d.in_keys, d.in_values.values().min().copied());
                        value = Value::Dict(d.dict);
                        continue;
                    }
                    if idx >= s.len() || s[idx] != ',' {
                        return Err(decode_error("Expecting ',' delimiter", s, idx));
                    }
                    let (next_name, next, in_key) = key(s, skip_white(s, idx + 1))?;
                    d.name = next_name;
                    d.in_keys = first(d.in_keys, in_key);
                    idx = next;
                    break;
                }
            }
        }
    }
}

/// json.loads(text).
pub fn loads(text: &str) -> Result<Value, Error> {
    let s: Vec<char> = text.chars().collect();
    if s.first() == Some(&'\u{feff}') {
        return Err(decode_error(
            "Unexpected UTF-8 BOM (decode using utf-8-sig)",
            &s,
            0,
        ));
    }
    let (value, end, half) = scan(&s, skip_white(&s, 0))?;
    let end = skip_white(&s, end);
    if end != s.len() {
        return Err(decode_error("Extra data", &s, end));
    }
    if let Some(at) = half {
        return Err(Error {
            kind: ErrorKind::Value,
            msg: format!(
                "half of a surrogate pair at char {at}: Python reads it, Rust's str cannot hold it"
            ),
        });
    }
    Ok(value)
}

/// How json.dumps writes: its keyword arguments.
#[derive(Clone, Debug)]
pub struct Dumps {
    /// indent: None writes on one line; Some("  ") a line per item, so much further in a level
    pub indent: Option<String>,
    pub ensure_ascii: bool,
    pub sort_keys: bool,
    /// separators: (item, key); None: (", ", ": "), or (",", ": ") with an indent
    pub separators: Option<(String, String)>,
}

impl Default for Dumps {
    fn default() -> Dumps {
        Dumps {
            indent: None,
            ensure_ascii: true,
            sort_keys: false,
            separators: None,
        }
    }
}

impl Dumps {
    /// json.dumps(value, indent=n, ensure_ascii=False), as the app saves its files
    pub fn file(indent: usize) -> Dumps {
        Dumps {
            indent: Some(" ".repeat(indent)),
            ensure_ascii: false,
            ..Dumps::default()
        }
    }
}

fn write_str(out: &mut String, text: &str, ascii: bool) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c if ascii && !(' '..='~').contains(&c) => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

fn write_float(out: &mut String, f: f64) {
    if f.is_nan() {
        out.push_str("NaN");
    } else if f.is_infinite() {
        out.push_str(if f > 0.0 { "Infinity" } else { "-Infinity" });
    } else {
        out.push_str(&float_repr(f));
    }
}

fn write_value(out: &mut String, value: &Value, how: &Dumps, level: usize) {
    let (item_sep, key_sep) = match &how.separators {
        Some((item, key)) => (item.as_str(), key.as_str()),
        None if how.indent.is_some() => (",", ": "),
        None => (", ", ": "),
    };
    // the line break and indent before an item of the next level (none on one line)
    let newline = |out: &mut String, level: usize| {
        if let Some(indent) = &how.indent {
            out.push('\n');
            for _ in 0..level {
                out.push_str(indent);
            }
        }
    };
    match value {
        Value::None => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Int(n) => out.push_str(&n.to_string()),
        Value::Float(f) => write_float(out, *f),
        Value::Str(s) => write_str(out, s, how.ensure_ascii),
        Value::List(items) if items.is_empty() => out.push_str("[]"),
        Value::Dict(dict) if dict.is_empty() => out.push_str("{}"),
        Value::List(items) => {
            out.push('[');
            newline(out, level + 1);
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(item_sep);
                    newline(out, level + 1);
                }
                write_value(out, item, how, level + 1);
            }
            newline(out, level);
            out.push(']');
        }
        Value::Dict(dict) => {
            out.push('{');
            newline(out, level + 1);
            let mut items: Vec<(&String, &Value)> = dict.iter().collect();
            if how.sort_keys {
                items.sort_by(|a, b| a.0.cmp(b.0));
            }
            for (i, (key, item)) in items.into_iter().enumerate() {
                if i > 0 {
                    out.push_str(item_sep);
                    newline(out, level + 1);
                }
                write_str(out, key, how.ensure_ascii);
                out.push_str(key_sep);
                write_value(out, item, how, level + 1);
            }
            newline(out, level);
            out.push('}');
        }
    }
}

/// json.dumps(value, ...).
pub fn dumps(value: &Value, how: &Dumps) -> String {
    let mut out = String::new();
    write_value(&mut out, value, how, 0);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_s_reading_and_its_refusals() {
        let v = loads(r#"{"a": 1, "a": 2.5, "b": [NaN, -Infinity, -0, 1e400, "😀"]}"#).unwrap();
        assert_eq!(v.repr(), "{'a': 2.5, 'b': [nan, -inf, 0, inf, '😀']}");
        let err = |text: &str| loads(text).unwrap_err().to_string();
        assert_eq!(
            err("[1 2]"),
            "Expecting ',' delimiter: line 1 column 4 (char 3)"
        );
        assert_eq!(
            err("{\"a\":1}\n\n x"),
            "Extra data: line 3 column 2 (char 10)"
        );
        assert_eq!(
            err("\"\\ud83d\\uzz\""),
            "Invalid \\uXXXX escape: line 1 column 9 (char 8)"
        );
        assert_eq!(
            err("[1e+]"),
            "Expecting ',' delimiter: line 1 column 3 (char 2)"
        );
        assert_eq!(
            loads(&"[".repeat(MAX_DEPTH + 1)).unwrap_err().kind,
            ErrorKind::Recursion
        );
    }

    #[test]
    fn python_s_writing() {
        let v =
            loads(r#"{"b": [1, 2.0, {"c": null}], "a": "é\u0001\"", "e": [], "f": {}}"#).unwrap();
        assert_eq!(
            dumps(&v, &Dumps::file(2)),
            "{\n  \"b\": [\n    1,\n    2.0,\n    {\n      \"c\": null\n    }\n  ],\n  \"a\": \
             \"é\\u0001\\\"\",\n  \"e\": [],\n  \"f\": {}\n}"
        );
        let sorted = Dumps {
            sort_keys: true,
            ..Dumps::default()
        };
        assert_eq!(
            dumps(&v, &sorted),
            "{\"a\": \"\\u00e9\\u0001\\\"\", \"b\": [1, 2.0, {\"c\": null}], \"e\": [], \"f\": {}}"
        );
    }
}
