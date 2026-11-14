//! Python's `tomllib` (CPython 3.12): `loads`, statement by statement as `_parser.py` reads a
//! document, so that a config.toml is read here as the app reads it: the same values in the same
//! order, and the same refusal with the same words at the same place.
//!
//! What is Python's:
//! - the grammar as tomllib has it, its leniencies with it: a number, a date or a time is what its
//!   pattern takes from the front, and what is left is refused where the statement goes on
//!   (`01` is a 0 with a 1 left over: "Expected newline or end of document after a statement");
//! - whole numbers of any size (`int(text, 0)`), with Python's limit of 4300 digits for a decimal
//!   one, which is a ValueError there and not a TOMLDecodeError ([`ErrorKind::Value`]);
//! - floats as `float()` reads them: one too large is inf;
//! - places counted in code points: "(at line 3, column 7)", "(at end of document)";
//! - what may not be said twice: tables, arrays of tables, keys, and what an inline table or an
//!   array has closed;
//! - how deep a document may nest. tomllib is written in Python, so every level costs frames of
//!   Python's stack (two for an array, three for an inline table) and the 1000th is the last:
//!   [`ErrorKind::Recursion`]. How many levels that leaves depends on how deep the caller is
//!   already; [`loads`] takes it [`LOADS_FRAME`] deep, [`loads_at`] is told.
//!
//! What differs: nothing that a document shows. A time with an offset costs a frame more the first
//! time Python sees that offset (`cached_tz`); here it always does.

use std::collections::{HashMap, HashSet};
use std::fmt;

use indexmap::IndexMap;
use num_bigint::BigInt;

use crate::datetime::days_in_month;

/// A table: its keys in the order the document gives them.
pub type Table = IndexMap<String, Toml>;

/// `datetime.date`
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Date {
    pub year: u16,
    pub month: u8,
    pub day: u8,
}

/// `datetime.time`, without a zone
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Time {
    pub hour: u8,
    pub minute: u8,
    pub second: u8,
    pub microsecond: u32,
}

/// What tomllib gives for a value.
#[derive(Clone, Debug, PartialEq)]
pub enum Toml {
    Bool(bool),
    Int(BigInt),
    Float(f64),
    Str(String),
    Array(Vec<Toml>),
    Table(Table),
    Date(Date),
    Time(Time),
    /// `datetime.datetime`; the offset from UTC in minutes, None where the document names none
    DateTime(Date, Time, Option<i32>),
}

impl Toml {
    /// `type(value).__name__`
    pub fn type_name(&self) -> &'static str {
        match self {
            Toml::Bool(_) => "bool",
            Toml::Int(_) => "int",
            Toml::Float(_) => "float",
            Toml::Str(_) => "str",
            Toml::Array(_) => "list",
            Toml::Table(_) => "dict",
            Toml::Date(_) => "date",
            Toml::Time(_) => "time",
            Toml::DateTime(..) => "datetime",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ErrorKind {
    /// tomllib.TOMLDecodeError
    Decode,
    /// a ValueError that is not a TOMLDecodeError: a whole number of too many digits
    Value,
    /// RecursionError
    Recursion,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Error {
    pub kind: ErrorKind,
    /// `str(exc)`
    pub msg: String,
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.msg)
    }
}

impl std::error::Error for Error {}

type R<T> = Result<T, Error>;

/// sys.get_int_max_str_digits()
pub const INT_MAX_STR_DIGITS: usize = 4300;
/// sys.getrecursionlimit(): the deepest frame Python runs
pub const RECURSION_LIMIT: usize = 1000;
/// How deep [`loads`] takes itself to be called: as from the app, a command of it reading the
/// config (the script, main, the command, load_config, loads).
pub const LOADS_FRAME: usize = 5;

const FROZEN: u8 = 1;
const EXPLICIT_NEST: u8 = 2;

/// `tomllib.loads(text)`
pub fn loads(text: &str) -> R<Table> {
    loads_at(text, LOADS_FRAME)
}

/// `tomllib.loads(text)` where loads is the `frame`-th frame of Python's stack (1: the module).
pub fn loads_at(text: &str, frame: usize) -> R<Table> {
    let parser = Parser {
        src: text.replace("\r\n", "\n").chars().collect(),
    };
    parser.document(frame)
}

/// A frame of Python's stack at depth `d`: one beyond the limit is a RecursionError.
fn enter(d: usize) -> R<()> {
    if d > RECURSION_LIMIT {
        return Err(Error {
            kind: ErrorKind::Recursion,
            msg: "maximum recursion depth exceeded".to_string(),
        });
    }
    Ok(())
}

fn ws(ch: char) -> bool {
    ch == ' ' || ch == '\t'
}

fn ws_and_newline(ch: char) -> bool {
    ch == ' ' || ch == '\t' || ch == '\n'
}

fn bare_key(ch: char) -> bool {
    ch.is_ascii_alphanumeric() || ch == '-' || ch == '_'
}

/// ILLEGAL_BASIC_STR_CHARS: also a literal string's, and a comment's
fn illegal(ch: char) -> bool {
    (ch < ' ' && ch != '\t') || ch == '\x7f'
}

/// ILLEGAL_MULTILINE_BASIC_STR_CHARS: also a multi-line literal string's
fn illegal_multiline(ch: char) -> bool {
    illegal(ch) && ch != '\n'
}

/// A key as Python writes the tuple: `('a', 'b')`, `('a',)`.
fn key_repr(key: &[String]) -> String {
    match key {
        [] => "()".to_string(),
        [one] => format!("({},)", crate::repr(one)),
        _ => {
            let parts: Vec<String> = key.iter().map(|k| crate::repr(k)).collect();
            format!("({})", parts.join(", "))
        }
    }
}

#[derive(Default)]
struct FlagNode {
    flags: u8,
    recursive: u8,
    nested: HashMap<String, FlagNode>,
}

/// Flags: what the document has said of a key, so that it is not said twice.
#[derive(Default)]
struct Flags {
    root: HashMap<String, FlagNode>,
    pending: HashSet<(Vec<String>, u8)>,
}

impl Flags {
    fn add_pending(&mut self, key: Vec<String>, flag: u8) {
        self.pending.insert((key, flag));
    }

    fn finalize_pending(&mut self) {
        for (key, flag) in std::mem::take(&mut self.pending) {
            self.set(&key, flag, false);
        }
    }

    fn unset_all(&mut self, key: &[String]) {
        let Some((stem, parent)) = key.split_last() else {
            return;
        };
        let mut cont = &mut self.root;
        for k in parent {
            match cont.get_mut(k) {
                Some(node) => cont = &mut node.nested,
                None => return,
            }
        }
        cont.remove(stem);
    }

    fn set(&mut self, key: &[String], flag: u8, recursive: bool) {
        let Some((stem, parent)) = key.split_last() else {
            return;
        };
        let mut cont = &mut self.root;
        for k in parent {
            cont = &mut cont.entry(k.clone()).or_default().nested;
        }
        let node = cont.entry(stem.clone()).or_default();
        if recursive {
            node.recursive |= flag;
        } else {
            node.flags |= flag;
        }
    }

    fn is_(&self, key: &[String], flag: u8) -> bool {
        let Some((stem, parent)) = key.split_last() else {
            return false; // the document's root has no flags
        };
        let mut cont = &self.root;
        for k in parent {
            let Some(inner) = cont.get(k) else {
                return false;
            };
            if inner.recursive & flag != 0 {
                return true;
            }
            cont = &inner.nested;
        }
        match cont.get(stem) {
            Some(node) => (node.flags | node.recursive) & flag != 0,
            None => false,
        }
    }
}

/// NestedDict.get_or_create_nest: the table behind `key`, made where it is not there; Err: the
/// KeyError, there is something other than a table behind the key.
fn get_or_create_nest<'a>(
    root: &'a mut Table,
    key: &[String],
    access_lists: bool,
) -> Result<&'a mut Table, ()> {
    let mut cont = root;
    for k in key {
        let entry = cont
            .entry(k.clone())
            .or_insert_with(|| Toml::Table(Table::new()));
        cont = match entry {
            Toml::Table(table) => table,
            Toml::Array(list) if access_lists => match list.last_mut() {
                Some(Toml::Table(table)) => table,
                _ => return Err(()),
            },
            _ => return Err(()),
        };
    }
    Ok(cont)
}

/// NestedDict.append_nest_to_list
fn append_nest_to_list(root: &mut Table, key: &[String]) -> Result<(), ()> {
    let Some((last, parent)) = key.split_last() else {
        return Err(());
    };
    let cont = get_or_create_nest(root, parent, true)?;
    match cont.get_mut(last) {
        Some(Toml::Array(list)) => list.push(Toml::Table(Table::new())),
        Some(_) => return Err(()),
        None => {
            cont.insert(last.clone(), Toml::Array(vec![Toml::Table(Table::new())]));
        }
    }
    Ok(())
}

/// What RE_DATETIME takes.
struct DateTimeMatch {
    end: usize,
    year: u32,
    month: u32,
    day: u32,
    /// the time, and its offset in minutes with whether it is one cached_tz makes
    time: Option<(Time, Option<(i32, bool)>)>,
}

struct Parser {
    src: Vec<char>,
}

impl Parser {
    fn at(&self, pos: usize) -> Option<char> {
        self.src.get(pos).copied()
    }

    fn starts_with(&self, pos: usize, what: &str) -> bool {
        what.chars()
            .enumerate()
            .all(|(i, ch)| self.at(pos + i) == Some(ch))
    }

    fn text(&self, from: usize, to: usize) -> String {
        self.src[from..to].iter().collect()
    }

    /// suffixed_err, raised by the frame at depth `d`
    fn err(&self, d: usize, pos: usize, msg: impl AsRef<str>) -> Error {
        // suffixed_err, and coord_repr within it
        if let Err(too_deep) = enter(d + 2) {
            return too_deep;
        }
        let msg = msg.as_ref();
        let coord = if pos >= self.src.len() {
            "end of document".to_string()
        } else {
            let before = &self.src[..pos];
            let line = before.iter().filter(|&&c| c == '\n').count() + 1;
            let column = match before.iter().rposition(|&c| c == '\n') {
                Some(newline) => pos - newline,
                None => pos + 1,
            };
            format!("line {line}, column {column}")
        };
        Error {
            kind: ErrorKind::Decode,
            msg: format!("{msg} (at {coord})"),
        }
    }

    fn skip_chars(&self, mut pos: usize, chars: fn(char) -> bool, d: usize) -> R<usize> {
        enter(d)?;
        while self.at(pos).is_some_and(chars) {
            pos += 1;
        }
        Ok(pos)
    }

    fn skip_until(
        &self,
        pos: usize,
        expect: &str,
        error_on: fn(char) -> bool,
        error_on_eof: bool,
        d: usize,
    ) -> R<usize> {
        enter(d)?;
        let first = expect.chars().next();
        let found =
            (pos..self.src.len()).find(|&at| self.at(at) == first && self.starts_with(at, expect));
        let new_pos = match found {
            Some(at) => at,
            None => {
                let end = self.src.len();
                if error_on_eof {
                    return Err(self.err(d, end, format!("Expected {}", crate::repr(expect))));
                }
                end
            }
        };
        if let Some(offset) = self.src[pos..new_pos].iter().position(|&c| error_on(c)) {
            let at = pos + offset;
            let ch = self.src[at].to_string();
            return Err(self.err(
                d,
                at,
                format!("Found invalid character {}", crate::repr(&ch)),
            ));
        }
        Ok(new_pos)
    }

    fn skip_comment(&self, pos: usize, d: usize) -> R<usize> {
        enter(d)?;
        if self.at(pos) == Some('#') {
            return self.skip_until(pos + 1, "\n", illegal, false, d + 1);
        }
        Ok(pos)
    }

    fn skip_comments_and_array_ws(&self, mut pos: usize, d: usize) -> R<usize> {
        enter(d)?;
        loop {
            let before = pos;
            pos = self.skip_chars(pos, ws_and_newline, d + 1)?;
            pos = self.skip_comment(pos, d + 1)?;
            if pos == before {
                return Ok(pos);
            }
        }
    }

    /// loads, itself the frame at depth `d`
    fn document(&self, d: usize) -> R<Table> {
        enter(d)?;
        let mut data = Table::new();
        let mut flags = Flags::default();
        let mut header: Vec<String> = Vec::new();
        let mut pos = 0;
        loop {
            pos = self.skip_chars(pos, ws, d + 1)?;
            let Some(ch) = self.at(pos) else {
                break;
            };
            if ch == '\n' {
                pos += 1;
                continue;
            }
            if bare_key(ch) || ch == '"' || ch == '\'' {
                pos = self.key_value_rule(pos, &mut data, &mut flags, &header, d + 1)?;
                pos = self.skip_chars(pos, ws, d + 1)?;
            } else if ch == '[' {
                flags.finalize_pending();
                (pos, header) = if self.at(pos + 1) == Some('[') {
                    self.create_list_rule(pos, &mut data, &mut flags, d + 1)?
                } else {
                    self.create_dict_rule(pos, &mut data, &mut flags, d + 1)?
                };
                pos = self.skip_chars(pos, ws, d + 1)?;
            } else if ch != '#' {
                return Err(self.err(d, pos, "Invalid statement"));
            }
            pos = self.skip_comment(pos, d + 1)?;
            let Some(ch) = self.at(pos) else {
                break;
            };
            if ch != '\n' {
                return Err(self.err(
                    d,
                    pos,
                    "Expected newline or end of document after a statement",
                ));
            }
            pos += 1;
        }
        Ok(data)
    }

    fn create_dict_rule(
        &self,
        pos: usize,
        data: &mut Table,
        flags: &mut Flags,
        d: usize,
    ) -> R<(usize, Vec<String>)> {
        enter(d)?;
        let pos = self.skip_chars(pos + 1, ws, d + 1)?;
        let (pos, key) = self.parse_key(pos, d + 1)?;
        if flags.is_(&key, EXPLICIT_NEST) || flags.is_(&key, FROZEN) {
            return Err(self.err(d, pos, format!("Cannot declare {} twice", key_repr(&key))));
        }
        flags.set(&key, EXPLICIT_NEST, false);
        if get_or_create_nest(data, &key, true).is_err() {
            return Err(self.err(d, pos, "Cannot overwrite a value"));
        }
        if !self.starts_with(pos, "]") {
            return Err(self.err(d, pos, "Expected ']' at the end of a table declaration"));
        }
        Ok((pos + 1, key))
    }

    fn create_list_rule(
        &self,
        pos: usize,
        data: &mut Table,
        flags: &mut Flags,
        d: usize,
    ) -> R<(usize, Vec<String>)> {
        enter(d)?;
        let pos = self.skip_chars(pos + 2, ws, d + 1)?;
        let (pos, key) = self.parse_key(pos, d + 1)?;
        if flags.is_(&key, FROZEN) {
            return Err(self.err(
                d,
                pos,
                format!("Cannot mutate immutable namespace {}", key_repr(&key)),
            ));
        }
        // free the namespace now that it holds a new table
        flags.unset_all(&key);
        flags.set(&key, EXPLICIT_NEST, false);
        if append_nest_to_list(data, &key).is_err() {
            return Err(self.err(d, pos, "Cannot overwrite a value"));
        }
        if !self.starts_with(pos, "]]") {
            return Err(self.err(d, pos, "Expected ']]' at the end of an array declaration"));
        }
        Ok((pos + 2, key))
    }

    fn key_value_rule(
        &self,
        pos: usize,
        data: &mut Table,
        flags: &mut Flags,
        header: &[String],
        d: usize,
    ) -> R<usize> {
        enter(d)?;
        let (pos, key, value) = self.parse_key_value_pair(pos, d + 1)?;
        let (stem, parent) = key.split_last().expect("a key has a part");
        let abs_parent: Vec<String> = header.iter().chain(parent).cloned().collect();
        for i in 1..key.len() {
            let cont_key: Vec<String> = header.iter().chain(&key[..i]).cloned().collect();
            // a table that the document has declared is not redefined by a dotted key
            if flags.is_(&cont_key, EXPLICIT_NEST) {
                return Err(self.err(
                    d,
                    pos,
                    format!("Cannot redefine namespace {}", key_repr(&cont_key)),
                ));
            }
            // tables that a dotted key makes may go on being made within this table's section
            flags.add_pending(cont_key, EXPLICIT_NEST);
        }
        if flags.is_(&abs_parent, FROZEN) {
            return Err(self.err(
                d,
                pos,
                format!(
                    "Cannot mutate immutable namespace {}",
                    key_repr(&abs_parent)
                ),
            ));
        }
        let Ok(nest) = get_or_create_nest(data, &abs_parent, true) else {
            return Err(self.err(d, pos, "Cannot overwrite a value"));
        };
        if nest.contains_key(stem) {
            return Err(self.err(d, pos, "Cannot overwrite a value"));
        }
        // an inline table or an array is closed: nothing is added to it afterwards
        if matches!(value, Toml::Table(_) | Toml::Array(_)) {
            let whole: Vec<String> = header.iter().chain(&key).cloned().collect();
            flags.set(&whole, FROZEN, true);
        }
        nest.insert(stem.clone(), value);
        Ok(pos)
    }

    fn parse_key_value_pair(&self, pos: usize, d: usize) -> R<(usize, Vec<String>, Toml)> {
        enter(d)?;
        let (pos, key) = self.parse_key(pos, d + 1)?;
        if self.at(pos) != Some('=') {
            return Err(self.err(d, pos, "Expected '=' after a key in a key/value pair"));
        }
        let pos = self.skip_chars(pos + 1, ws, d + 1)?;
        let (pos, value) = self.parse_value(pos, d + 1)?;
        Ok((pos, key, value))
    }

    fn parse_key(&self, pos: usize, d: usize) -> R<(usize, Vec<String>)> {
        enter(d)?;
        let (pos, part) = self.parse_key_part(pos, d + 1)?;
        let mut key = vec![part];
        let mut pos = self.skip_chars(pos, ws, d + 1)?;
        loop {
            if self.at(pos) != Some('.') {
                return Ok((pos, key));
            }
            pos = self.skip_chars(pos + 1, ws, d + 1)?;
            let (after, part) = self.parse_key_part(pos, d + 1)?;
            key.push(part);
            pos = self.skip_chars(after, ws, d + 1)?;
        }
    }

    fn parse_key_part(&self, pos: usize, d: usize) -> R<(usize, String)> {
        enter(d)?;
        match self.at(pos) {
            Some(ch) if bare_key(ch) => {
                let end = self.skip_chars(pos, bare_key, d + 1)?;
                Ok((end, self.text(pos, end)))
            }
            Some('\'') => self.parse_literal_str(pos, d + 1),
            Some('"') => self.parse_one_line_basic_str(pos, d + 1),
            _ => Err(self.err(d, pos, "Invalid initial character for a key part")),
        }
    }

    fn parse_one_line_basic_str(&self, pos: usize, d: usize) -> R<(usize, String)> {
        enter(d)?;
        self.parse_basic_str(pos + 1, false, d + 1)
    }

    fn parse_array(&self, pos: usize, d: usize) -> R<(usize, Toml)> {
        enter(d)?;
        let mut array = Vec::new();
        let mut pos = self.skip_comments_and_array_ws(pos + 1, d + 1)?;
        if self.at(pos) == Some(']') {
            return Ok((pos + 1, Toml::Array(array)));
        }
        loop {
            let (after, value) = self.parse_value(pos, d + 1)?;
            array.push(value);
            pos = self.skip_comments_and_array_ws(after, d + 1)?;
            match self.at(pos) {
                Some(']') => return Ok((pos + 1, Toml::Array(array))),
                Some(',') => {}
                _ => return Err(self.err(d, pos, "Unclosed array")),
            }
            pos = self.skip_comments_and_array_ws(pos + 1, d + 1)?;
            if self.at(pos) == Some(']') {
                return Ok((pos + 1, Toml::Array(array)));
            }
        }
    }

    fn parse_inline_table(&self, pos: usize, d: usize) -> R<(usize, Toml)> {
        enter(d)?;
        let mut table = Table::new();
        let mut flags = Flags::default();
        // (NestedDict() and Flags() are made a frame deeper too, as deep as this skip_chars)
        let mut pos = self.skip_chars(pos + 1, ws, d + 1)?;
        if self.at(pos) == Some('}') {
            return Ok((pos + 1, Toml::Table(table)));
        }
        loop {
            let (after, key, value) = self.parse_key_value_pair(pos, d + 1)?;
            pos = after;
            let (stem, parent) = key.split_last().expect("a key has a part");
            if flags.is_(&key, FROZEN) {
                return Err(self.err(
                    d,
                    pos,
                    format!("Cannot mutate immutable namespace {}", key_repr(&key)),
                ));
            }
            let Ok(nest) = get_or_create_nest(&mut table, parent, false) else {
                return Err(self.err(d, pos, "Cannot overwrite a value"));
            };
            if nest.contains_key(stem) {
                return Err(self.err(
                    d,
                    pos,
                    format!("Duplicate inline table key {}", crate::repr(stem)),
                ));
            }
            let closed = matches!(value, Toml::Table(_) | Toml::Array(_));
            nest.insert(stem.clone(), value);
            pos = self.skip_chars(pos, ws, d + 1)?;
            match self.at(pos) {
                Some('}') => return Ok((pos + 1, Toml::Table(table))),
                Some(',') => {}
                _ => return Err(self.err(d, pos, "Unclosed inline table")),
            }
            if closed {
                flags.set(&key, FROZEN, true);
            }
            pos = self.skip_chars(pos + 1, ws, d + 1)?;
        }
    }

    /// parse_basic_str_escape, at the backslash: where the string goes on, and what the escape
    /// stands for (nothing: a line ending trimmed)
    fn parse_basic_str_escape(
        &self,
        pos: usize,
        multiline: bool,
        d: usize,
    ) -> R<(usize, Option<char>)> {
        enter(d)?;
        let id = self.at(pos + 1);
        let mut pos = pos + 2;
        if multiline && matches!(id, Some(' ' | '\t' | '\n')) {
            // a backslash at the end of a line: the line ending and the white space after it go
            if id != Some('\n') {
                pos = self.skip_chars(pos, ws, d + 1)?;
                let Some(ch) = self.at(pos) else {
                    return Ok((pos, None));
                };
                if ch != '\n' {
                    return Err(self.err(d, pos, "Unescaped '\\' in a string"));
                }
                pos += 1;
            }
            pos = self.skip_chars(pos, ws_and_newline, d + 1)?;
            return Ok((pos, None));
        }
        let ch = match id {
            Some('u') => return self.parse_hex_char(pos, 4, d + 1),
            Some('U') => return self.parse_hex_char(pos, 8, d + 1),
            Some('b') => '\u{8}',
            Some('t') => '\t',
            Some('n') => '\n',
            Some('f') => '\u{c}',
            Some('r') => '\r',
            Some('"') => '"',
            Some('\\') => '\\',
            _ => return Err(self.err(d, pos, "Unescaped '\\' in a string")),
        };
        Ok((pos, Some(ch)))
    }

    fn parse_basic_str_escape_multiline(&self, pos: usize, d: usize) -> R<(usize, Option<char>)> {
        enter(d)?;
        self.parse_basic_str_escape(pos, true, d + 1)
    }

    fn parse_hex_char(&self, pos: usize, hex_len: usize, d: usize) -> R<(usize, Option<char>)> {
        enter(d)?;
        let end = (pos + hex_len).min(self.src.len());
        let hex = self.src.get(pos..end).unwrap_or_default();
        if hex.len() != hex_len || !hex.iter().all(char::is_ascii_hexdigit) {
            return Err(self.err(d, pos, "Invalid hex value"));
        }
        let pos = pos + hex_len;
        let code = hex
            .iter()
            .fold(0u32, |n, c| n * 16 + c.to_digit(16).unwrap_or(0));
        enter(d + 1)?; // is_unicode_scalar_value
        match char::from_u32(code) {
            Some(ch) => Ok((pos, Some(ch))),
            None => Err(self.err(d, pos, "Escaped character is not a Unicode scalar value")),
        }
    }

    fn parse_literal_str(&self, pos: usize, d: usize) -> R<(usize, String)> {
        enter(d)?;
        let start = pos + 1;
        let end = self.skip_until(start, "'", illegal, true, d + 1)?;
        Ok((end + 1, self.text(start, end)))
    }

    fn parse_multiline_str(&self, pos: usize, literal: bool, d: usize) -> R<(usize, String)> {
        enter(d)?;
        let mut pos = pos + 3;
        if self.at(pos) == Some('\n') {
            pos += 1;
        }
        let delim;
        let mut result;
        if literal {
            delim = '\'';
            let end = self.skip_until(pos, "'''", illegal_multiline, true, d + 1)?;
            result = self.text(pos, end);
            pos = end + 3;
        } else {
            delim = '"';
            (pos, result) = self.parse_basic_str(pos, true, d + 1)?;
        }
        // one or two quotation marks more belong to the string
        if self.at(pos) != Some(delim) {
            return Ok((pos, result));
        }
        pos += 1;
        result.push(delim);
        if self.at(pos) != Some(delim) {
            return Ok((pos, result));
        }
        result.push(delim);
        Ok((pos + 1, result))
    }

    fn parse_basic_str(&self, mut pos: usize, multiline: bool, d: usize) -> R<(usize, String)> {
        enter(d)?;
        let error_on: fn(char) -> bool = if multiline {
            illegal_multiline
        } else {
            illegal
        };
        let mut result = String::new();
        let mut start = pos;
        loop {
            let Some(ch) = self.at(pos) else {
                return Err(self.err(d, pos, "Unterminated string"));
            };
            if ch == '"' {
                if !multiline {
                    result.extend(&self.src[start..pos]);
                    return Ok((pos + 1, result));
                }
                if self.starts_with(pos, "\"\"\"") {
                    result.extend(&self.src[start..pos]);
                    return Ok((pos + 3, result));
                }
                pos += 1;
                continue;
            }
            if ch == '\\' {
                result.extend(&self.src[start..pos]);
                let escape;
                (pos, escape) = if multiline {
                    self.parse_basic_str_escape_multiline(pos, d + 1)?
                } else {
                    self.parse_basic_str_escape(pos, false, d + 1)?
                };
                result.extend(escape);
                start = pos;
                continue;
            }
            if error_on(ch) {
                return Err(self.err(
                    d,
                    pos,
                    format!("Illegal character {}", crate::repr(&ch.to_string())),
                ));
            }
            pos += 1;
        }
    }

    fn parse_value(&self, pos: usize, d: usize) -> R<(usize, Toml)> {
        enter(d)?;
        let text = |(end, s): (usize, String)| (end, Toml::Str(s));
        match self.at(pos) {
            Some('"') => {
                return if self.starts_with(pos, "\"\"\"") {
                    self.parse_multiline_str(pos, false, d + 1).map(text)
                } else {
                    self.parse_one_line_basic_str(pos, d + 1).map(text)
                };
            }
            Some('\'') => {
                return if self.starts_with(pos, "'''") {
                    self.parse_multiline_str(pos, true, d + 1).map(text)
                } else {
                    self.parse_literal_str(pos, d + 1).map(text)
                };
            }
            Some('t') if self.starts_with(pos, "true") => return Ok((pos + 4, Toml::Bool(true))),
            Some('f') if self.starts_with(pos, "false") => {
                return Ok((pos + 5, Toml::Bool(false)));
            }
            Some('[') => return self.parse_array(pos, d + 1),
            Some('{') => return self.parse_inline_table(pos, d + 1),
            _ => {}
        }

        // dates, times and numbers. (match_to_datetime, match_to_localtime and match_to_number
        // are a frame deeper, at d + 1. That needs no check of its own: before every value
        // comes its key or an array's white space, and reading those went as deep already.
        // Only a zone of its own, made by cached_tz inside match_to_datetime, goes deeper)
        if let Some(m) = self.match_datetime(pos) {
            if matches!(m.time, Some((_, Some((_, true))))) {
                enter(d + 2)?; // cached_tz
            }
            if m.year == 0 || m.day > days_in_month(i64::from(m.year), m.month) {
                return Err(self.err(d, pos, "Invalid date or datetime"));
            }
            let date = Date {
                year: m.year as u16,
                month: m.month as u8,
                day: m.day as u8,
            };
            let value = match m.time {
                None => Toml::Date(date),
                Some((time, offset)) => Toml::DateTime(date, time, offset.map(|(min, _)| min)),
            };
            return Ok((m.end, value));
        }
        if let Some((end, time)) = self.match_time(pos) {
            return Ok((end, Toml::Time(time)));
        }

        // whole numbers and floats: a float that is all digits and underscores looks like a
        // whole number, so the one pattern takes both
        if let Some((end, float)) = self.match_number(pos) {
            let text = self.text(pos, end);
            if float {
                return match crate::float_of_str(&text) {
                    Some(value) => Ok((end, Toml::Float(value))),
                    None => Err(self.err(d, pos, "Invalid value")),
                };
            }
            return Ok((end, Toml::Int(int_of_literal(&text)?)));
        }

        // special floats
        let three = self.text(pos.min(self.src.len()), (pos + 3).min(self.src.len()));
        match three.as_str() {
            "inf" => return Ok((pos + 3, Toml::Float(f64::INFINITY))),
            "nan" => return Ok((pos + 3, Toml::Float(f64::NAN))),
            _ => {}
        }
        let four = self.text(pos.min(self.src.len()), (pos + 4).min(self.src.len()));
        match four.as_str() {
            "+inf" => return Ok((pos + 4, Toml::Float(f64::INFINITY))),
            "-inf" => return Ok((pos + 4, Toml::Float(f64::NEG_INFINITY))),
            "+nan" => return Ok((pos + 4, Toml::Float(f64::NAN))),
            "-nan" => return Ok((pos + 4, Toml::Float(-f64::NAN))),
            _ => {}
        }

        Err(self.err(d, pos, "Invalid value"))
    }

    fn digit(&self, pos: usize) -> Option<u32> {
        self.at(pos).and_then(|c| c.to_digit(10))
    }

    /// two digits, as a number
    fn two(&self, pos: usize) -> Option<u32> {
        Some(self.digit(pos)? * 10 + self.digit(pos + 1)?)
    }

    /// _TIME_RE_STR at `pos`: `12:34:56`, `12:34:56.789`; of a fraction's digits six count
    fn match_time(&self, pos: usize) -> Option<(usize, Time)> {
        let hour = self.two(pos).filter(|&h| h <= 23)?;
        let minute = self.two(pos + 3).filter(|&m| m <= 59)?;
        let second = self.two(pos + 6).filter(|&s| s <= 59)?;
        if self.at(pos + 2) != Some(':') || self.at(pos + 5) != Some(':') {
            return None;
        }
        let mut end = pos + 8;
        let mut microsecond = 0;
        if self.at(end) == Some('.') && self.digit(end + 1).is_some() {
            end += 1;
            let mut taken = 0;
            while let Some(digit) = self.digit(end) {
                if taken < 6 {
                    microsecond = microsecond * 10 + digit;
                    taken += 1;
                }
                end += 1;
            }
            microsecond *= 10u32.pow(6 - taken);
        }
        let time = Time {
            hour: hour as u8,
            minute: minute as u8,
            second: second as u8,
            microsecond,
        };
        Some((end, time))
    }

    /// RE_DATETIME at `pos`: a date, and where a time follows it the time, and where an offset
    /// follows that the offset
    fn match_datetime(&self, pos: usize) -> Option<DateTimeMatch> {
        let year = self.two(pos)? * 100 + self.two(pos + 2)?;
        let month = self.two(pos + 5).filter(|m| (1..=12).contains(m))?;
        let day = self.two(pos + 8).filter(|d| (1..=31).contains(d))?;
        if self.at(pos + 4) != Some('-') || self.at(pos + 7) != Some('-') {
            return None;
        }
        let mut found = DateTimeMatch {
            end: pos + 10,
            year,
            month,
            day,
            time: None,
        };
        if !matches!(self.at(pos + 10), Some('T' | 't' | ' ')) {
            return Some(found);
        }
        let Some((end, time)) = self.match_time(pos + 11) else {
            return Some(found);
        };
        found.end = end;
        found.time = Some((time, None));
        match self.at(end) {
            Some('Z' | 'z') => {
                found.end = end + 1;
                found.time = Some((time, Some((0, false))));
            }
            Some(sign @ ('+' | '-')) => {
                let hours = self.two(end + 1).filter(|&h| h <= 23);
                let minutes = self.two(end + 4).filter(|&m| m <= 59);
                if let (Some(hours), Some(':'), Some(minutes)) = (hours, self.at(end + 3), minutes)
                {
                    let offset = (hours * 60 + minutes) as i32;
                    let offset = if sign == '-' { -offset } else { offset };
                    found.end = end + 6;
                    found.time = Some((time, Some((offset, true))));
                }
            }
            _ => {}
        }
        Some(found)
    }

    /// `first(_?more)*` from `pos`, where `first` is known to be there: its end
    fn digits(&self, mut pos: usize, ok: fn(char) -> bool) -> usize {
        pos += 1;
        loop {
            if self.at(pos).is_some_and(ok) {
                pos += 1;
            } else if self.at(pos) == Some('_') && self.at(pos + 1).is_some_and(ok) {
                pos += 2;
            } else {
                return pos;
            }
        }
    }

    /// RE_NUMBER at `pos`: where the number ends, and whether it is a float
    fn match_number(&self, pos: usize) -> Option<(usize, bool)> {
        let decimal: fn(char) -> bool = |c| c.is_ascii_digit();
        if self.at(pos) == Some('0') {
            let ok: Option<fn(char) -> bool> = match self.at(pos + 1) {
                Some('x') => Some(|c| c.is_ascii_hexdigit()),
                Some('b') => Some(|c| matches!(c, '0' | '1')),
                Some('o') => Some(|c| matches!(c, '0'..='7')),
                _ => None,
            };
            if let Some(ok) = ok {
                if self.at(pos + 2).is_some_and(ok) {
                    return Some((self.digits(pos + 2, ok), false));
                }
            }
        }
        let mut end = pos;
        if matches!(self.at(end), Some('+' | '-')) {
            end += 1;
        }
        end = match self.at(end) {
            Some('0') => end + 1,
            Some('1'..='9') => self.digits(end, decimal),
            _ => return None,
        };
        let whole = end;
        if self.at(end) == Some('.') && self.at(end + 1).is_some_and(decimal) {
            end = self.digits(end + 1, decimal);
        }
        if matches!(self.at(end), Some('e' | 'E')) {
            let mut exponent = end + 1;
            if matches!(self.at(exponent), Some('+' | '-')) {
                exponent += 1;
            }
            if self.at(exponent).is_some_and(decimal) {
                end = self.digits(exponent, decimal);
            }
        }
        Some((end, end != whole))
    }
}

/// `int(text, 0)` of what RE_NUMBER takes for a whole number.
fn int_of_literal(text: &str) -> R<BigInt> {
    let digits: String = text.chars().filter(|&c| c != '_').collect();
    let radix = match digits.get(..2) {
        Some("0x") => 16,
        Some("0o") => 8,
        Some("0b") => 2,
        _ => 10,
    };
    let parsed = if radix == 10 {
        let (negative, digits) = match digits.strip_prefix('-') {
            Some(rest) => (true, rest),
            None => (false, digits.strip_prefix('+').unwrap_or(&digits)),
        };
        if digits.len() > INT_MAX_STR_DIGITS {
            return Err(Error {
                kind: ErrorKind::Value,
                msg: format!(
                    "Exceeds the limit ({INT_MAX_STR_DIGITS} digits) for integer string \
                     conversion: value has {} digits; use sys.set_int_max_str_digits() to \
                     increase the limit",
                    digits.len()
                ),
            });
        }
        BigInt::parse_bytes(digits.as_bytes(), 10).map(|n| if negative { -n } else { n })
    } else {
        BigInt::parse_bytes(&digits.as_bytes()[2..], radix)
    };
    parsed.ok_or_else(|| Error {
        kind: ErrorKind::Value,
        msg: format!(
            "invalid literal for int() with base 0: {}",
            crate::repr(text)
        ),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn deepest(make: impl Fn(usize) -> String, frame: usize) -> usize {
        (1..600)
            .take_while(|&n| loads_at(&make(n), frame).is_ok())
            .last()
            .unwrap_or(0)
    }

    #[test]
    fn nests_as_deep_as_python_was_measured_to() {
        // CPython 3.12.3, loads called from a function that the module calls: its third frame
        let nested = |open: &'static str, inner: &'static str, close: &'static str| {
            move |n: usize| format!("a={}{inner}{}", open.repeat(n), close.repeat(n))
        };
        assert_eq!(deepest(nested("[", "", "]"), 3), 496);
        assert_eq!(deepest(nested("[", "1", "]"), 3), 496);
        assert_eq!(deepest(nested("[", "\"\\u0041\"", "]"), 3), 494);
        assert_eq!(deepest(nested("{a=", "1", "}"), 3), 330);
        assert_eq!(deepest(nested("[{a=", "1", "}]"), 3), 198);
        assert_eq!(deepest(nested("[", "", "]"), 4), 496);
        let empty = |n: usize| format!("a={}{{}}{}", "{a=".repeat(n - 1), "}".repeat(n - 1));
        assert_eq!(deepest(empty, 3), 331);
        let too_deep = loads_at(&format!("a={}", "[".repeat(3000)), 3).unwrap_err();
        assert_eq!(too_deep.kind, ErrorKind::Recursion);
    }

    #[test]
    fn reads_a_document() {
        let doc =
            "title = \"x\"\n[a.b]\nn = 0x1F\nwhen = 1979-05-27T07:32:00-08:00\n[[c]]\nf = 1e3\n";
        let table = loads(doc).unwrap();
        assert_eq!(table["title"], Toml::Str("x".into()));
        let Toml::Table(a) = &table["a"] else {
            panic!("a table")
        };
        let Toml::Table(b) = &a["b"] else {
            panic!("a table")
        };
        assert_eq!(b["n"], Toml::Int(31.into()));
        let date = Date {
            year: 1979,
            month: 5,
            day: 27,
        };
        let time = Time {
            hour: 7,
            minute: 32,
            second: 0,
            microsecond: 0,
        };
        assert_eq!(b["when"], Toml::DateTime(date, time, Some(-480)));
        assert_eq!(
            loads("a = 1\na = 2").unwrap_err().msg,
            "Cannot overwrite a value (at end of document)"
        );
        assert_eq!(
            loads("[a]\n[a]").unwrap_err().msg,
            "Cannot declare ('a',) twice (at line 2, column 3)"
        );
    }
}
