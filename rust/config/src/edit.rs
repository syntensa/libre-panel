//! config.toml changed in place (config.py's edit_config): one setting set or taken out, an
//! array of tables replaced, the rest of the file, comments included, as it was. The new
//! file is compared with the old one: only what was asked for may differ, and it has to load
//! as a configuration. Else nothing is written and the error says to edit it by hand, so a
//! layout this cannot handle is refused, never mangled.

use std::path::Path;
use std::sync::Mutex;

use lp_py::toml::{Table, Toml};

use crate::{config_error, load_config, os_error, parse_config, Error, Result};

/// The commented config.toml that `config init` and the first change write, with
/// `@PROVIDERS@` where the system's sensor sources go.
const DEFAULT_CONFIG_TEMPLATE: &str = include_str!("default_config.toml");

/// Windows: a file another reader holds open cannot be replaced, a moment later it can (the
/// panel reads config.toml when it changed).
const REPLACE_TRIES: usize = 40;
const REPLACE_WAIT_S: f64 = 0.025;

/// Settings that are no more: what an old config.toml says of one is let be as it loads (no
/// error, nothing in the log, its value never read), and it goes from the file with the
/// file's next write (a setting the app or the tray changes, a theme chosen).
pub const GONE: [&str; 3] = [
    // 0.8 to 0.8.1rc1: light mode's switch, always on since
    "light_mode",
    // 0.7.4 to 0.8.1rc1: the relay of Claude Code's cloud sessions and its access token (only
    // the agents on this computer count since)
    "services.agents.cloud",
    "services.agents.cloud_token",
];

/// A change of config.toml: one setting (`"theme"`, `"device.brightness"`,
/// `"modes.game.theme"`, `"services.enabled"`) set to a boolean, a number, a string or a
/// list of them; a setting taken out, so that its default holds again (nothing happens where
/// it is not set); or an array of tables (`"services.autopilot.rules"`) replaced, each
/// written as a `[[name]]` block.
#[derive(Clone, Debug, PartialEq)]
pub enum Change {
    Value(String, Toml),
    Unset(String),
    Tables(String, Vec<Table>),
}

impl Change {
    fn name(&self) -> &str {
        match self {
            Change::Value(name, _) | Change::Unset(name) | Change::Tables(name, _) => name,
        }
    }
}

/// default_config_toml: the commented config.toml that `config init` and the first change
/// write.
pub fn default_config_toml(platform: &str) -> String {
    let providers = crate::default_providers(platform);
    let listed: Vec<String> = providers.iter().map(|name| toml_string(name)).collect();
    DEFAULT_CONFIG_TEMPLATE
        .replace("\r\n", "\n")
        .replace("@PROVIDERS@", &format!("[{}]", listed.join(", ")))
}

/// write_default_config: the default config.toml at `path`; FileExistsError where there is
/// one and it is not to be overwritten.
pub fn write_default_config(path: &Path, overwrite: bool, platform: &str) -> Result<()> {
    if path.exists() && !overwrite {
        return Err(Error::new("FileExistsError", path.display().to_string()));
    }
    if let Some(folder) = path.parent() {
        std::fs::create_dir_all(folder).map_err(|e| os_error(&e))?;
    }
    std::fs::write(path, as_written(&default_config_toml(platform))).map_err(|e| os_error(&e))
}

/// A text as Python's write_text writes it: on Windows each new line as `\r\n`.
fn as_written(text: &str) -> String {
    match cfg!(windows) {
        true => text.replace('\n', "\r\n"),
        false => text.to_string(),
    }
}

/// _toml_string: a JSON string is a valid TOML basic string (DEL is the one character it
/// leaves bare).
fn toml_string(text: &str) -> String {
    let how = lp_py::json::Dumps {
        ensure_ascii: false,
        ..lp_py::json::Dumps::default()
    };
    lp_py::json::dumps(&lp_py::Value::Str(text.to_string()), &how).replace('\x7f', "\\u007f")
}

fn bare(key: &str) -> bool {
    !key.is_empty() && key.bytes().all(name_byte)
}

fn name_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-'
}

fn toml_key(key: &str) -> String {
    match bare(key) {
        true => key.to_string(),
        false => toml_string(key),
    }
}

/// _toml_literal: `value` as TOML: a boolean, number or string, a list of such values, or an
/// inline table (`{ music = true, apps = ["a.exe"] }`).
pub fn toml_literal(value: &Toml) -> Result<String> {
    Ok(match value {
        Toml::Bool(true) => "true".into(),
        Toml::Bool(false) => "false".into(),
        Toml::Int(number) => number.to_string(),
        Toml::Float(number) => lp_py::float_repr(*number),
        Toml::Str(text) => toml_string(text),
        Toml::Array(items) => {
            let items: Result<Vec<String>> = items.iter().map(toml_literal).collect();
            format!("[{}]", items?.join(", "))
        }
        Toml::Table(table) if table.is_empty() => "{}".into(),
        Toml::Table(table) => {
            let pairs: Result<Vec<String>> = (table.iter())
                .map(|(key, item)| Ok(format!("{} = {}", toml_key(key), toml_literal(item)?)))
                .collect();
            format!("{{ {} }}", pairs?.join(", "))
        }
        other => {
            let kind = other.type_name();
            return Err(Error::new(
                "TypeError",
                format!("cannot write {kind} to config.toml"),
            ));
        }
    })
}

// -- the file's text ------------------------------------------------------------------------------

fn blank(byte: u8) -> bool {
    byte == b' ' || byte == b'\t'
}

fn skip_blanks(text: &[u8], mut at: usize, end: usize) -> usize {
    while at < end && blank(text[at]) {
        at += 1;
    }
    at
}

/// The starts of the lines of `text` from `from` (a line's start) to before `end`.
fn line_starts(text: &str, from: usize, end: usize) -> impl Iterator<Item = usize> + '_ {
    let mut next = (from < end).then_some(from);
    std::iter::from_fn(move || {
        let at = next?;
        next = text[at..]
            .find('\n')
            .map(|found| at + found + 1)
            .filter(|start| *start < end);
        Some(at)
    })
}

fn line_end(text: &str, at: usize) -> usize {
    text[at..].find('\n').map_or(text.len(), |found| at + found)
}

/// _HEADER: the lines that open with a bracket, from `from` (a line's start) on.
fn headers(text: &str, from: usize) -> impl Iterator<Item = usize> + '_ {
    let bytes = text.as_bytes();
    line_starts(text, from, text.len()).filter(move |start| {
        let at = skip_blanks(bytes, *start, bytes.len());
        bytes.get(at) == Some(&b'[')
    })
}

/// A dotted name in a header, from `at`: its text without the spaces round its dots, and
/// where it ends.
fn dotted(bytes: &[u8], mut at: usize) -> Option<(String, usize)> {
    let mut name = String::new();
    loop {
        let begin = at;
        while at < bytes.len() && name_byte(bytes[at]) {
            at += 1;
        }
        if at == begin {
            return None;
        }
        name.push_str(std::str::from_utf8(&bytes[begin..at]).ok()?);
        let dot = skip_blanks(bytes, at, bytes.len());
        if bytes.get(dot) != Some(&b'.') {
            return Some((name, at));
        }
        name.push('.');
        at = skip_blanks(bytes, dot + 1, bytes.len());
    }
}

/// What follows a header's closing bracket on its line: blanks and a comment at most.
fn header_ends(bytes: &[u8], at: usize) -> Option<usize> {
    let at = skip_blanks(bytes, at, bytes.len());
    let end = (bytes[at..].iter().position(|b| *b == b'\n')).map_or(bytes.len(), |n| at + n);
    (at == end || bytes[at] == b'#').then_some(end)
}

/// _ANY_HEADER at the line that starts at `start`: `[table]` or `[[array.of.tables]]`, as
/// (the opening brackets, the name, where the line ends).
fn any_header(text: &str, start: usize) -> Option<(&'static str, String, usize)> {
    let bytes = text.as_bytes();
    let mut at = skip_blanks(bytes, start, bytes.len());
    if bytes.get(at) != Some(&b'[') {
        return None;
    }
    at += 1;
    let brackets = match bytes.get(at) == Some(&b'[') {
        true => {
            at += 1;
            "[["
        }
        false => "[",
    };
    let (name, after) = dotted(bytes, skip_blanks(bytes, at, bytes.len()))?;
    let close = skip_blanks(bytes, after, bytes.len());
    if bytes.get(close) != Some(&b']') {
        return None;
    }
    let both = match bytes.get(close + 1) == Some(&b']') {
        true => header_ends(bytes, close + 2),
        false => None,
    };
    let end = both.or_else(|| header_ends(bytes, close + 1))?;
    Some((brackets, name, end))
}

/// _TABLE at the line that starts at `start`: `[table]` or `[table.sub]` (not `[[array]]`),
/// as (the name, where the line ends).
fn table_header(text: &str, start: usize) -> Option<(String, usize)> {
    let bytes = text.as_bytes();
    let at = skip_blanks(bytes, start, bytes.len());
    if bytes.get(at) != Some(&b'[') {
        return None;
    }
    let (name, after) = dotted(bytes, skip_blanks(bytes, at + 1, bytes.len()))?;
    let close = skip_blanks(bytes, after, bytes.len());
    if bytes.get(close) != Some(&b']') {
        return None;
    }
    Some((name, header_ends(bytes, close + 1)?))
}

/// _string_end: just past the string that opens at `i`: on one line, or over several when it
/// opens with three quotes.
fn string_end(bytes: &[u8], i: usize) -> usize {
    let quote = bytes[i];
    let three = |at: usize| bytes.len() >= at + 3 && bytes[at..at + 3].iter().all(|b| *b == quote);
    let step = |at: usize| match quote == b'"' && bytes[at] == b'\\' {
        true => 2,
        false => 1,
    };
    if three(i) {
        let mut j = i + 3;
        while j < bytes.len() && !three(j) {
            j += step(j);
        }
        return (j + 3).min(bytes.len());
    }
    let mut j = i + 1;
    while j < bytes.len() && bytes[j] != quote && bytes[j] != b'\n' {
        j += step(j);
    }
    match j < bytes.len() && bytes[j] == quote {
        true => j + 1,
        false => j.min(bytes.len()),
    }
}

/// _value_end: where the TOML value that begins at `i` ends: just past its last character,
/// over as many lines as an array or an inline table takes. What follows on the line (spaces
/// and a comment) is not part of it.
fn value_end(text: &str, i: usize) -> usize {
    let bytes = text.as_bytes();
    let mut i = skip_blanks(bytes, i, bytes.len());
    let mut depth = 0i64;
    while i < bytes.len() {
        match bytes[i] {
            b'"' | b'\'' => {
                i = string_end(bytes, i);
                if depth == 0 {
                    return i;
                }
                continue;
            }
            b'[' | b'{' => depth += 1,
            b']' | b'}' => {
                depth -= 1;
                if depth <= 0 {
                    return i + 1;
                }
            }
            // a comment inside an array runs to the end of its line
            b'#' if depth != 0 => match text[i..].find('\n') {
                Some(found) => {
                    i += found;
                    continue;
                }
                None => return bytes.len(),
            },
            b' ' | b'\t' | b'\n' | b'#' if depth == 0 => return i,
            _ => {}
        }
        i += 1;
    }
    i.min(bytes.len())
}

/// _table_body: where the keys of `table` are (None: the top level, before the first
/// header): the start of their first line and of the header that follows. None when there is
/// no such table.
fn table_body(text: &str, table: Option<&str>) -> Option<(usize, usize)> {
    let Some(table) = table else {
        return Some((0, headers(text, 0).next().unwrap_or(text.len())));
    };
    let header = line_starts(text, 0, text.len())
        .filter_map(|start| table_header(text, start))
        .find(|(name, _)| name == table)?;
    let start = match text[header.1..].find('\n') {
        Some(found) => header.1 + found + 1,
        None => text.len(),
    };
    Some((start, headers(text, start).next().unwrap_or(text.len())))
}

/// `key`, blanks and `=` at `at`, before `end`: where they end.
fn key_at(bytes: &[u8], at: usize, end: usize, key: &str) -> Option<usize> {
    let after = at + key.len();
    if after > end || &bytes[at..after] != key.as_bytes() {
        return None;
    }
    let equals = skip_blanks(bytes, after, end);
    (equals < end && bytes[equals] == b'=').then_some(equals + 1)
}

/// _key_span: where `key = value` is in a table body: the start of its line, the end of its
/// indentation, and the end of the value (after as many lines as it takes).
fn key_span(text: &str, body: (usize, usize), key: &str) -> Option<(usize, usize, usize)> {
    let bytes = text.as_bytes();
    line_starts(text, body.0, body.1).find_map(|start| {
        let indent = skip_blanks(bytes, start, body.1);
        let after = key_at(bytes, indent, body.1, key)?;
        Some((start, indent, value_end(text, after)))
    })
}

/// _placeholder_end: where the line ends that holds `# key = ...` last in a table body (the
/// commented setting that the default config.toml shows), just past its line break; None if
/// none.
fn placeholder_end(text: &str, body: (usize, usize), key: &str) -> Option<usize> {
    let bytes = text.as_bytes();
    let ends = line_starts(text, body.0, body.1).filter_map(|start| {
        let hash = skip_blanks(bytes, start, body.1);
        if hash >= body.1 || bytes[hash] != b'#' {
            return None;
        }
        key_at(bytes, skip_blanks(bytes, hash + 1, body.1), body.1, key)?;
        let end = line_end(text, start).min(body.1);
        Some(end + usize::from(end < body.1 && bytes[end] == b'\n'))
    });
    ends.last()
}

/// The start of what follows the line at `at`: the whole text's end where it is the last.
fn next_line(text: &str, at: usize) -> usize {
    let at = at.min(text.len());
    text[at..]
        .find('\n')
        .map_or(text.len(), |found| at + found + 1)
}

fn with_key(text: &str, table: Option<&str>, key: &str, literal: &str) -> String {
    let line = format!("{key} = {literal}");
    let Some(body) = table_body(text, table) else {
        let table = table.unwrap_or("");
        return format!("{}\n\n[{table}]\n{line}\n", text.trim_end_matches('\n'));
    };
    let Some((_, indent_end, value_end)) = key_span(text, body, key) else {
        // a new setting goes under its commented line (the explanation it belongs to), else
        // first in its table
        if let Some(after) = placeholder_end(text, body, key) {
            let lead = match text[..after].ends_with('\n') {
                true => "",
                false => "\n",
            };
            return format!("{}{lead}{line}\n{}", &text[..after], &text[after..]);
        }
        return format!("{}{line}\n{}", &text[..body.0], &text[body.0..]);
    };
    // (what follows the value stays)
    format!("{}{line}{}", &text[..indent_end], &text[value_end..])
}

/// _comments_above: where the comment lines directly above the line at `pos` begin (`pos`
/// when there are none): they belong to the header on that line.
fn comments_above(text: &str, mut pos: usize) -> usize {
    while pos > 0 {
        let previous = text[..pos - 1].rfind('\n').map_or(0, |found| found + 1);
        if !text[previous..pos]
            .trim_start_matches(lp_py::isspace)
            .starts_with('#')
        {
            break;
        }
        pos = previous;
    }
    pos
}

/// _without_key: `text` without the setting `key` of `table` (None: the top level), over as
/// many lines as its value takes; the comments above it stay. Unchanged where it is not set.
fn without_key(text: &str, table: Option<&str>, key: &str) -> String {
    let found = table_body(text, table).and_then(|body| key_span(text, body, key));
    let Some((first, _, value_end)) = found else {
        return text.to_string();
    };
    format!("{}{}", &text[..first], &text[next_line(text, value_end)..])
}

/// A dotted name's parent (None: the top level) and its last part.
fn parted(name: &str) -> (Option<&str>, &str) {
    match name.rsplit_once('.') {
        Some((head, last)) => (Some(head).filter(|head| !head.is_empty()), last),
        None => (None, name),
    }
}

/// _without_tables: `text` without the `[[name]]` blocks (and the tables inside them), and
/// without a `name = [...]` in the parent table; and where the first block was (None when
/// there were none). A block takes the comments directly above its header along.
fn without_tables(text: &str, name: &str) -> (String, Option<usize>) {
    let found: Vec<usize> = headers(text, 0).collect();
    let mut starts: Vec<usize> = found.iter().map(|at| comments_above(text, *at)).collect();
    starts.push(text.len());
    let inside = format!("{name}.");
    let (mut kept, mut cut, mut at) = (String::new(), 0, None);
    for (n, header) in found.iter().enumerate() {
        let (begin, stop) = (starts[n], starts[n + 1]);
        let (brackets, named, _) = any_header(text, *header).unwrap_or(("", String::new(), 0));
        if (named == name && brackets == "[[") || named.starts_with(&inside) {
            kept.push_str(&text[cut.min(begin)..begin]);
            // (counted in characters, as Python's index is)
            at = at.or(Some(kept.chars().count()));
            cut = stop;
        }
    }
    kept.push_str(&text[cut..]);
    // (a list in the parent table goes too)
    let (head, last) = parted(name);
    let text = without_key(&kept, head, last);
    let at = at.map(|chars| (text.char_indices().nth(chars)).map_or(text.len(), |(at, _)| at));
    (text, at)
}

/// _with_tables: `text` with the array of tables `name` replaced by `tables`.
fn with_tables(text: &str, name: &str, tables: &[Table]) -> Result<String> {
    let (text, at) = without_tables(text, name);
    let mut blocks = Vec::new();
    for table in tables {
        let mut block = format!("[[{name}]]\n");
        for (key, value) in table {
            block.push_str(&format!("{} = {}\n", toml_key(key), toml_literal(value)?));
        }
        blocks.push(block);
    }
    let blocks = blocks.join("\n");
    if blocks.is_empty() {
        return Ok(text);
    }
    // new: after the parent table's keys, else at the end
    let at = at.unwrap_or_else(|| {
        let body = parted(name)
            .0
            .and_then(|head| table_body(&text, Some(head)));
        body.map_or(text.len(), |body| body.1)
    });
    let at = at.min(text.len());
    let (ahead, behind) = (&text[..at], &text[at..]);
    let gap = if lp_py::strip(ahead).is_empty() || ahead.ends_with("\n\n") {
        ""
    } else if ahead.ends_with('\n') {
        "\n"
    } else {
        "\n\n"
    };
    let tail = match lp_py::strip(behind).is_empty() {
        true => "",
        false => "\n",
    };
    Ok(format!("{ahead}{gap}{blocks}{tail}{behind}"))
}

// -- the document ---------------------------------------------------------------------------------

/// _pruned: `value` without the empty tables in it: a table that only the headers of the
/// array made is no difference.
fn pruned(table: &Table) -> Table {
    let kept = table.iter().map(|(key, item)| match item {
        Toml::Table(inner) => (key.clone(), Toml::Table(pruned(inner))),
        other => (key.clone(), other.clone()),
    });
    kept.filter(|(_, item)| !matches!(item, Toml::Table(inner) if inner.is_empty()))
        .collect()
}

/// A number as Python compares it with another kind of number.
fn number(value: &Toml) -> Option<f64> {
    match value {
        Toml::Bool(on) => Some(f64::from(u8::from(*on))),
        Toml::Int(number) => number.to_string().parse().ok(),
        Toml::Float(number) => Some(*number),
        _ => None,
    }
}

/// Python's `==` of two documents: tables key by key in any order, numbers by their value.
fn same(a: &Toml, b: &Toml) -> bool {
    match (a, b) {
        (Toml::Int(a), Toml::Int(b)) => a == b,
        (Toml::Table(a), Toml::Table(b)) => same_table(a, b),
        (Toml::Array(a), Toml::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| same(a, b))
        }
        (a, b) => match (number(a), number(b)) {
            (Some(a), Some(b)) => a == b,
            _ => a == b,
        },
    }
}

fn same_table(a: &Table, b: &Table) -> bool {
    a.len() == b.len() && (a.iter()).all(|(key, item)| b.get(key).is_some_and(|o| same(item, o)))
}

/// _table_of: the table at `parents` in `document`, made where it is not there yet.
fn table_of<'a>(document: &'a mut Table, parents: &[&str], name: &str) -> Result<&'a mut Table> {
    let mut node = document;
    for part in parents {
        let entry = (node.entry((*part).to_string())).or_insert_with(|| Toml::Table(Table::new()));
        let Toml::Table(inner) = entry else {
            return Err(config_error(format!("{name}: {part} is not a table")));
        };
        node = inner;
    }
    Ok(node)
}

/// _holds: whether `document` has the setting `name` (dotted, as edit_config takes it).
fn holds(document: &Table, name: &str) -> bool {
    let mut node = document;
    let mut parts = name.split('.').peekable();
    while let Some(part) = parts.next() {
        match node.get(part) {
            None => return false,
            Some(_) if parts.peek().is_none() => return true,
            Some(Toml::Table(inner)) => node = inner,
            Some(_) => return false,
        }
    }
    false
}

fn loads(text: &str, said: impl Fn(&str) -> String) -> Result<Table> {
    lp_py::toml::loads(text).map_err(|e| match e.kind {
        lp_py::toml::ErrorKind::Decode => config_error(said(&e.msg)),
        lp_py::toml::ErrorKind::Value => Error::new("ValueError", e.msg),
        lp_py::toml::ErrorKind::Recursion => Error::new("RecursionError", e.msg),
    })
}

/// _edited's text: `original` (which reads as `before`) with `changes`; an error where the
/// result is not what was asked for, or no configuration.
pub fn edited_text(
    path: &Path,
    original: &str,
    before: &Table,
    changes: &[Change],
    platform: &str,
) -> Result<String> {
    let names: Vec<&str> = changes.iter().map(Change::name).collect();
    let names = names.join(", ");
    let (mut text, mut expected) = (original.to_string(), before.clone());
    // (the tables after the values: their blocks go behind the table's own keys)
    let values = changes.iter().filter(|c| matches!(c, Change::Value(..)));
    let others = changes.iter().filter(|c| !matches!(c, Change::Value(..)));
    for change in values.chain(others) {
        let mut parents: Vec<&str> = change.name().split('.').collect();
        let key = parents.pop().unwrap_or("");
        let node = table_of(&mut expected, &parents, change.name())?;
        let table = parents.join(".");
        let table = Some(table.as_str()).filter(|table| !table.is_empty());
        match change {
            Change::Tables(name, tables) => {
                text = with_tables(&text, name, tables)?;
                match tables.is_empty() {
                    // (no key at all)
                    true => drop(node.shift_remove(key)),
                    false => {
                        let tables = tables.iter().cloned().map(Toml::Table).collect();
                        node.insert(key.to_string(), Toml::Array(tables));
                    }
                }
            }
            Change::Value(_, value) => {
                text = with_key(&text, table, key, &toml_literal(value)?);
                node.insert(key.to_string(), value.clone());
            }
            Change::Unset(_) => {
                text = without_key(&text, table, key);
                node.shift_remove(key);
            }
        }
    }
    let shown = path.display();
    let data = loads(&text, |why| {
        format!("{shown}: could not set {names} ({why}); please edit it by hand")
    })?;
    if !same_table(&pruned(&data), &pruned(&expected)) {
        return Err(config_error(format!(
            "{shown}: could not set {names}; please edit it by hand"
        )));
    }
    parse_config(&data, Some(path), platform)?;
    Ok(text)
}

/// _write: config.toml's text written beside it and put in its place.
pub fn write_text(path: &Path, text: &str) -> Result<()> {
    write(path, text)
}

fn write(path: &Path, text: &str) -> Result<()> {
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(".tmp");
    let tmp = path.with_file_name(name);
    std::fs::write(&tmp, as_written(text)).map_err(|e| os_error(&e))?;
    for attempt in 0..REPLACE_TRIES {
        match std::fs::rename(&tmp, path) {
            Ok(()) => return Ok(()),
            // (Windows: "access denied" while it is open for reading)
            Err(why)
                if why.kind() == std::io::ErrorKind::PermissionDenied
                    && attempt + 1 < REPLACE_TRIES =>
            {
                std::thread::sleep(std::time::Duration::from_secs_f64(REPLACE_WAIT_S));
            }
            Err(why) => return Err(os_error(&why)),
        }
    }
    Ok(())
}

/// The file's text as open() reads it.
fn read(path: &Path) -> Result<String> {
    let bytes = std::fs::read(path).map_err(|e| os_error(&e))?;
    lp_py::codecs::read_text(&bytes).map_err(|e| {
        config_error(format!(
            "{}: not UTF-8 text (byte {}); save it as UTF-8",
            path.display(),
            e.start
        ))
    })
}

/// One write at a time in this program (the tray and each request of the app write from
/// threads of their own): else both would start from the same file, and the last to finish
/// would undo the other.
static EDITING: Mutex<()> = Mutex::new(());

/// edit_config: config.toml changed in one write (see the module). The write is atomic, and
/// it waits for one under way in this program.
pub fn edit_config(changes: &[Change], path: &Path, platform: &str) -> Result<()> {
    let _one = EDITING.lock().unwrap_or_else(|held| held.into_inner());
    if !path.exists() {
        write_default_config(path, false, platform)?;
    }
    let original = read(path)?;
    let before = loads(&original, |why| format!("{}: {why}", path.display()))?;
    let gone = GONE.iter().filter(|name| holds(&before, name));
    let gone: Vec<Change> = gone
        .map(|name| Change::Unset((*name).to_string()))
        .collect();
    if !gone.is_empty() {
        // the settings that are no more go with this write, where the file's layout lets
        // them: else it is as asked
        let all: Vec<Change> = changes.iter().cloned().chain(gone).collect();
        match edited_text(path, &original, &before, &all, platform) {
            Ok(text) => return write(path, &text),
            Err(why) if why.class == "ConfigError" => {}
            Err(why) => return Err(why),
        }
    }
    write(
        path,
        &edited_text(path, &original, &before, changes, platform)?,
    )
}

/// set_config_value: one setting changed in config.toml.
pub fn set_config_value(name: &str, value: Toml, path: &Path, platform: &str) -> Result<()> {
    edit_config(&[Change::Value(name.to_string(), value)], path, platform)
}

/// set_config_tables: an array of tables replaced in config.toml.
pub fn set_config_tables(
    name: &str,
    tables: Vec<Table>,
    path: &Path,
    platform: &str,
) -> Result<()> {
    edit_config(&[Change::Tables(name.to_string(), tables)], path, platform)
}

/// set_active_theme: config.toml pointed at another theme, the rest of the file (and
/// comments) kept. One theme chosen stops the screens in turn (`[sequence]`); their steps
/// are kept.
pub fn set_active_theme(name: &str, path: &Path, platform: &str) -> Result<()> {
    let mut changes = vec![Change::Value("theme".into(), Toml::Str(name.to_string()))];
    match load_config(path, platform) {
        Ok(config) => {
            if config.sequence.on && !config.sequence.steps.is_empty() {
                changes.push(Change::Value("sequence.on".into(), Toml::Bool(false)));
            }
            // a theme chosen: the scene is no longer on
            if !config.scene.is_empty() {
                changes.push(Change::Unset("scene".into()));
            }
        }
        // (the write below says what is wrong with the file)
        Err(why) if why.class == "ConfigError" => {}
        Err(why) => return Err(why),
    }
    edit_config(&changes, path, platform)
}

pub fn set_brightness(percent: i64, path: &Path, platform: &str) -> Result<()> {
    set_config_value(
        "device.brightness",
        Toml::Int(percent.into()),
        path,
        platform,
    )
}
