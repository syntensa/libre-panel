//! Dates on the panel (i18n.format_date): strftime with the days' and the months' names in the
//! panel's language, a day's number and a month's name joined as the language joins them
//! ("27. September", "27 de septiembre", "27 września", "9月27日"). Only the codes go through
//! strftime, one at a time (lp_py::strftime), never the names or the text round them.

use lp_py::datetime::DateTime;
use lp_py::strftime::{strftime, Refused};
use lp_py::Value;

use crate::{language, DAY_BEFORE_MONTH, MONTH_DAY};

/// Why a date is not written: the ValueError Python raises (a clock shows "--:--" for it),
/// another of its errors, or what Rust does not write yet.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DateError {
    ValueError(String),
    Failed(String),
    Unported(&'static str),
}

/// _NAMES: the codes whose names the language has, and the tables they are in.
fn table_of(token: &str) -> Option<&'static str> {
    match token {
        "%a" => Some("days_short"),
        "%A" => Some("days"),
        "%b" => Some("months_short"),
        "%B" => Some("months"),
        _ => None,
    }
}

/// _DAY_AND_MONTH (`%-?d\.? %[bB]|%[bB] %-?d`) at `at`: how many characters it takes.
fn day_and_month(chars: &[char], at: usize) -> Option<usize> {
    let is = |i: usize, c: char| chars.get(i) == Some(&c);
    let month = |i: usize| matches!(chars.get(i), Some('b' | 'B'));
    if !is(at, '%') {
        return None;
    }
    let mut i = at + 1 + usize::from(is(at + 1, '-'));
    if is(i, 'd') {
        i += 1;
        i += usize::from(is(i, '.'));
        if is(i, ' ') && is(i + 1, '%') && month(i + 2) {
            return Some(i + 3 - at);
        }
    }
    if month(at + 1) && is(at + 2, ' ') && is(at + 3, '%') {
        let i = at + 4 + usize::from(is(at + 4, '-'));
        if is(i, 'd') {
            return Some(i + 1 - at);
        }
    }
    None
}

/// _DATE_CODE (`%[-#_0^]?[A-Za-z%]`) at `at`: how many characters the code takes.
fn code_at(chars: &[char], at: usize) -> Option<usize> {
    if chars.get(at) != Some(&'%') {
        return None;
    }
    let code = |i: usize| {
        chars
            .get(i)
            .is_some_and(|c| c.is_ascii_alphabetic() || *c == '%')
    };
    let flag = chars.get(at + 1).is_some_and(|c| "-#_0^".contains(*c));
    if flag && code(at + 2) {
        return Some(3);
    }
    code(at + 1).then_some(2)
}

/// format_date: `at` in `fmt`, strftime's codes with the names of the panel's language.
pub fn format_date(at: &DateTime, fmt: &str) -> Result<String, DateError> {
    let language = language();
    let code = language.code();
    let names = language.catalog();
    let mut chars: Vec<char> = fmt.chars().collect();
    if let Some((_, pattern)) = MONTH_DAY.iter().find(|(c, _)| *c == code) {
        // (the month and the day as numbers: no name to look up)
        let both = pattern
            .replace("{month}", &at.month.to_string())
            .replace("{day}", &at.day.to_string())
            .replace('%', "%%");
        let mut joined = Vec::with_capacity(chars.len());
        let mut i = 0;
        while i < chars.len() {
            match day_and_month(&chars, i) {
                Some(n) => {
                    joined.extend(both.chars());
                    i += n;
                }
                None => {
                    joined.push(chars[i]);
                    i += 1;
                }
            }
        }
        chars = joined;
    }
    let mut text = String::new();
    let mut i = 0;
    while i < chars.len() {
        let Some(n) = code_at(&chars, i) else {
            text.push(chars[i]);
            i += 1;
            continue;
        };
        let token: String = chars[i..i + n].iter().collect();
        let end = i + n;
        let named = !names.is_empty();
        let month_after = chars.get(end) == Some(&' ')
            && chars.get(end + 1) == Some(&'%')
            && matches!(chars.get(end + 2), Some('b' | 'B'));
        if named && token == "%d" && month_after {
            let joiner = DAY_BEFORE_MONTH
                .iter()
                .find(|(c, _)| *c == code)
                .map_or("", |(_, j)| j);
            text.push_str(&format!("{:02}{joiner}", at.day));
        } else if let Some(table) = table_of(&token).filter(|_| named) {
            let mut table = table;
            if token == "%B" && names.contains_key("months_after_day") {
                let before: String = chars[..i].iter().collect();
                if before.trim_end_matches(lp_py::isspace).ends_with("%d") {
                    table = "months_after_day"; // "27 września"
                }
            }
            let index = if token == "%a" || token == "%A" {
                at.weekday()
            } else {
                at.month - 1
            };
            match names.get(table) {
                Some(Value::List(list)) => match list.get(index as usize) {
                    Some(Value::Str(name)) => text.push_str(name),
                    _ => return Err(DateError::Failed(format!("no name {index} in {table}"))),
                },
                Some(_) => return Err(DateError::Failed(format!("{table} is no list"))),
                None => return Err(DateError::Failed(format!("KeyError: '{table}'"))),
            }
        } else {
            match strftime(&token, at) {
                Ok(part) => text.push_str(&part),
                Err(Refused::ValueError(msg)) => return Err(DateError::ValueError(msg)),
                Err(Refused::Unported(what)) => return Err(DateError::Unported(what)),
            }
        }
        i = end;
    }
    Ok(text)
}
