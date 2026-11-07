//! Counting down to a moment (render/countdown.py): "2026-12-24", "2026-12-24 18:00" or "12-24"
//! (every year), as the countdown widget shows it: the number, its unit, the time left in the
//! day, the whole in words, the days, the date it counts to (i18n.format_date).

use lp_i18n::DateError;
use lp_py::datetime::days_in_month;
use num_bigint::BigInt;

use crate::piece::{Failed, Result};
use crate::snapshot::Moment;

/// What a countdown says: its text, or what Rust does not write yet.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Said {
    Text(String),
    Unported(&'static str),
}

/// A target's year (None: every year), month, day, hour and minute (None: a date).
pub type Parts = (Option<i64>, u32, u32, Option<u32>, Option<u32>);

/// Where a countdown stands: the moment it counts to, the microseconds left (none once it is
/// there), whether it is there, whether its target has a time of day, and whether it is a
/// day (not every year's) that has gone by.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Countdown {
    pub target: Moment,
    pub left: i64,
    pub done: bool,
    pub timed: bool,
    pub over: bool,
}

/// What `\d` reads at `at` (Python's: any decimal digit), and its value.
fn digit(chars: &[char], at: usize) -> Option<u32> {
    chars.get(at).copied().and_then(lp_py::decimal)
}

/// `(\d{1,2})` and what follows it from `at`: the longest of one or two digits that `rest`
/// takes (a regular expression's greedy choice, then the shorter), with its value.
fn one_or_two<T>(chars: &[char], at: usize, rest: impl Fn(usize) -> Option<T>) -> Option<(u32, T)> {
    let first = digit(chars, at)?;
    if let Some(second) = digit(chars, at + 1) {
        if let Some(found) = rest(at + 2) {
            return Some((first * 10 + second, found));
        }
    }
    rest(at + 1).map(|found| (first, found))
}

/// `\s*$` from `at`: Python's white space to the end (a newline at the end among it).
fn spaces_to_end(chars: &[char], at: usize) -> bool {
    chars[at.min(chars.len())..]
        .iter()
        .all(|&c| lp_py::isspace(c))
}

/// `(?:[ T](\d{1,2}):(\d{2}))?\s*$` from `at`: the time of day, if there is one.
fn time_and_end(chars: &[char], at: usize) -> Option<Option<(u32, u32)>> {
    if matches!(chars.get(at), Some(' ' | 'T')) {
        let timed = one_or_two(chars, at + 1, |after| {
            if chars.get(after) != Some(&':') {
                return None;
            }
            let (tens, ones) = (digit(chars, after + 1)?, digit(chars, after + 2)?);
            spaces_to_end(chars, after + 3).then_some(tens * 10 + ones)
        });
        if let Some((hour, minute)) = timed {
            return Some(Some((hour, minute)));
        }
    }
    spaces_to_end(chars, at).then_some(None)
}

/// A target's month, day and time of day.
type Date = (u32, u32, Option<(u32, u32)>);

/// `(\d{1,2})-(\d{1,2})` and the rest from `at`: month, day and the time of day.
fn date_from(chars: &[char], at: usize) -> Option<Date> {
    let (month, (day, time)) = one_or_two(chars, at, |after| {
        if chars.get(after) != Some(&'-') {
            return None;
        }
        one_or_two(chars, after + 1, |end| time_and_end(chars, end))
    })?;
    Some((month, day, time))
}

/// parse_target: the parts of `^\s*(?:(\d{4})-)?(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?\s*$`
/// as Python's re matches it, if they make a day of a month (and a time of day); else None.
pub fn parse_target(text: &str) -> Option<Parts> {
    let chars: Vec<char> = text.chars().collect();
    let start = chars.iter().take_while(|&&c| lp_py::isspace(c)).count();
    let year: Option<u32> = (0..4)
        .map(|i| digit(&chars, start + i))
        .try_fold(0, |n, d| d.map(|d| n * 10 + d));
    let with_year = year
        .filter(|_| chars.get(start + 4) == Some(&'-'))
        .and_then(|year| date_from(&chars, start + 5).map(|date| (Some(year), date)));
    let (year, (month, day, time)) = match with_year {
        Some(found) => found,
        None => (None, date_from(&chars, start)?),
    };
    let (hour, minute) = match time {
        Some((hour, minute)) => (Some(hour), Some(minute)),
        None => (None, None),
    };
    if !((1..=12).contains(&month) && (1..=31).contains(&day)) || hour.is_some_and(|h| h > 23) {
        return None;
    }
    if minute.is_some_and(|m| m > 59) {
        return None;
    }
    Some((year.map(i64::from), month, day, hour, minute))
}

/// datetime(year, month, day, hour, minute), as Python checks it.
fn datetime(year: i64, month: u32, day: u32, hour: u32, minute: u32) -> Result<Moment> {
    if !(1..=9999).contains(&year) {
        return Err(Failed(format!("year {year} is out of range")));
    }
    if day < 1 || day > days_in_month(year, month) {
        return Err(Failed("day is out of range for month".into()));
    }
    Ok(Moment {
        year,
        month,
        day,
        hour,
        minute,
        second: 0,
        microsecond: 0,
    })
}

/// _moment: the day at the time, or the 28th where the month has no such day (29 February in
/// a common year).
fn moment(
    year: i64,
    month: u32,
    day: u32,
    hour: Option<u32>,
    minute: Option<u32>,
) -> Result<Moment> {
    let (h, m) = (hour.unwrap_or(0), minute.unwrap_or(0));
    datetime(year, month, day, h, m).or_else(|_| datetime(year, month, day.min(28), h, m))
}

/// countdown: where `text` stands from `now`; None if it is no date.
pub fn countdown(text: &str, now: &Moment) -> Result<Option<Countdown>> {
    let Some((year, month, day, hour, minute)) = parse_target(text) else {
        return Ok(None);
    };
    let timed = hour.is_some();
    let given = year.filter(|&y| y != 0); // (`year or now.year`: the year 0 is this one)
    let mut target = moment(given.unwrap_or(now.year), month, day, hour, minute)?;
    if year.is_none() && target.midnight() < now.midnight() {
        target = moment(now.year + 1, month, day, hour, minute)?; // every year: the next one
    }
    let stand = |target: Moment, left: i64, done: bool, over: bool| Countdown {
        target,
        left,
        done,
        timed,
        over,
    };
    if target.midnight() == now.midnight() && (!timed || *now >= target) {
        return Ok(Some(stand(target, 0, true, false)));
    }
    if target < *now {
        if year.is_some() {
            return Ok(Some(stand(target, 0, true, true)));
        }
        target = moment(now.year + 1, month, day, hour, minute)?;
    }
    Ok(Some(stand(target, target.since(now), false, false)))
}

/// int(timedelta.total_seconds()): the microseconds divided as Python divides two ints (to
/// the nearest float), then cut to a whole number.
fn whole_seconds(us: i64) -> i64 {
    let seconds = lp_py::num::int_true_div(&BigInt::from(us), &BigInt::from(1_000_000))
        .expect("a million is no zero");
    seconds.trunc() as i64
}

/// _clock: the hours, minutes and seconds of `seconds`, two digits each.
fn clock(seconds: i64) -> String {
    let (hours, rest) = (seconds / 3600, seconds % 3600);
    format!("{hours:02}:{:02}:{:02}", rest / 60, rest % 60)
}

/// countdown_text: one part of a countdown: the number, its unit, the time left in the day,
/// the whole in words, the days, or the date it counts to.
pub fn countdown_text(text: &str, part: &str, now: &Moment, done: &str) -> Result<Said> {
    let t = lp_i18n::t;
    let Some(state) = countdown(text, now)? else {
        return Ok(Said::Text("--".into()));
    };
    if part == "date" {
        let fmt = if state.timed {
            "%A, %d %B %Y %H:%M"
        } else {
            "%A, %d %B %Y"
        };
        return match lp_i18n::format_date(&state.target, fmt) {
            Ok(date) => Ok(Said::Text(date)),
            Err(DateError::ValueError(msg) | DateError::Failed(msg)) => Err(Failed(msg)),
            Err(DateError::Unported(what)) => Ok(Said::Unported(what)),
        };
    }
    let or_done = |otherwise: String| {
        if done.is_empty() {
            otherwise
        } else {
            done.to_string()
        }
    };
    if state.over {
        // (nothing left to count)
        return Ok(Said::Text(match part {
            "unit" => t("days"),
            "clock" => String::new(),
            "text" => or_done("0".into()),
            _ => "0".into(),
        }));
    }
    if state.done {
        if part == "unit" || part == "clock" {
            return Ok(Said::Text(String::new()));
        }
        return Ok(Said::Text(or_done(if state.timed {
            t("Now")
        } else {
            t("Today")
        })));
    }
    let seconds = whole_seconds(state.left);
    let (mut days, rest) = (seconds / 86400, seconds % 86400);
    let (hours, minutes) = (rest / 3600, rest % 3600 / 60);
    if !state.timed {
        // a date: the days still to sleep, today not counted
        days = state.target.toordinal() - now.toordinal();
    }
    let unit = |days: i64| if days == 1 { t("day") } else { t("days") };
    Ok(Said::Text(match part {
        "days" => days.to_string(),
        "clock" => clock(rest),
        "unit" if days != 0 => unit(days),
        "unit" if hours != 0 => t("hours"),
        "unit" => t("minutes"),
        "number" if days != 0 => days.to_string(),
        "number" if hours != 0 => format!("{hours}:{minutes:02}"),
        "number" => format!("{minutes}:{:02}", rest % 60),
        _ if days != 0 => {
            let hours = if state.timed && days < 3 {
                format!(" {hours} h")
            } else {
                String::new()
            };
            format!("{days} {}{hours}", unit(days))
        }
        _ if hours != 0 => format!("{hours} h {minutes} min"),
        _ => format!("{minutes} min {} s", rest % 60),
    }))
}
