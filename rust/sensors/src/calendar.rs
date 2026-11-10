//! Calendar events from iCalendar (.ics): the secret address of a Google, Outlook, iCloud or
//! Nextcloud calendar, or a file (sensors/calendar.py).
//!
//! Readings for the next events, `calendar.<n>.*` (1 = the next or the one going on): `title`,
//! `when` ("Today 14:00", "Tomorrow", "Fri 16 Oct 09:30"), `day`, `time`, `location`, `start`
//! (Unix time), `minutes` (until it starts), `now` (1, only while it is going on);
//! `calendar.events` and `calendar.today` count them.
//!
//! Repeating events follow their rule (daily, weekly on given days, monthly on a day or "the
//! second Tuesday", yearly; with an end, a count, left-out days and moved single days). Times
//! keep their time zone.

use std::collections::{HashMap, HashSet};
use std::io::Read;
use std::sync::{Arc, Condvar, Mutex, OnceLock};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_py::datetime::{days_in_month, DateTime};
use lp_py::regex::Regex;
use lp_py::zones::{self, Zone};
use lp_py::Value;
use lp_render::Reading;
use num_bigint::BigInt;

use crate::lhm::Readings;

pub const MAX_EVENTS: usize = 20;
const MAX_BYTES: usize = 8 * 1024 * 1024;
/// occurrences looked at per repeating event
const MAX_STEPS: i128 = 5000;
const DAYS: [&str; 7] = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];
/// Outlook names its time zones the Windows way.
const WINDOWS_ZONES: [(&str, &str); 18] = [
    ("W. Europe Standard Time", "Europe/Berlin"),
    ("Central Europe Standard Time", "Europe/Budapest"),
    ("Romance Standard Time", "Europe/Paris"),
    ("Central European Standard Time", "Europe/Warsaw"),
    ("GMT Standard Time", "Europe/London"),
    ("E. Europe Standard Time", "Europe/Chisinau"),
    ("FLE Standard Time", "Europe/Kiev"),
    ("Russian Standard Time", "Europe/Moscow"),
    ("Eastern Standard Time", "America/New_York"),
    ("Central Standard Time", "America/Chicago"),
    ("Mountain Standard Time", "America/Denver"),
    ("Pacific Standard Time", "America/Los_Angeles"),
    ("Tokyo Standard Time", "Asia/Tokyo"),
    ("China Standard Time", "Asia/Shanghai"),
    ("India Standard Time", "Asia/Kolkata"),
    ("AUS Eastern Standard Time", "Australia/Sydney"),
    ("UTC", "UTC"),
    ("Coordinated Universal Time", "UTC"),
];

/// A time without a zone, as microseconds from 0001-01-01 00:00.
type Stamp = i128;
const SECOND: i128 = 1_000_000;
const DAY: i128 = 86_400 * SECOND;
/// 10000-01-01 00:00: the first moment that is none
const END: Stamp = 3_652_059 * DAY;
/// timedelta's most days
const MOST_DAYS: i128 = 999_999_999;

/// Why something of a calendar is not read.
#[derive(Clone, Debug, PartialEq)]
enum Bad {
    /// Python's ValueError: this one is left out
    Value,
    /// any other error of Python's: the calendar is not read
    Fail(String),
}

fn fail<T>(why: &str) -> Result<T, Bad> {
    Err(Bad::Fail(why.to_string()))
}

fn stamp_of(at: &DateTime) -> Stamp {
    i128::from(at.toordinal() - 1) * DAY + i128::from(at.time_of_day())
}

/// The time a stamp is (within datetime's years).
fn time_of(stamp: Stamp) -> DateTime {
    let day = DateTime::from_ordinal((stamp.div_euclid(DAY) + 1) as i64);
    let day = day.unwrap_or(DateTime::date(1, 1, 1));
    day.plus(stamp.rem_euclid(DAY) as i64).unwrap_or(day)
}

/// A stamp datetime can hold, else Python's OverflowError.
fn within(stamp: Stamp) -> Result<Stamp, Bad> {
    match (0..END).contains(&stamp) {
        true => Ok(stamp),
        false => fail("date value out of range"),
    }
}

/// timedelta(days=n) as microseconds, else Python's OverflowError.
fn days_span(n: i128) -> Result<i128, Bad> {
    match n.abs() <= MOST_DAYS {
        true => Ok(n * DAY),
        false => fail("days must have magnitude <= 999999999"),
    }
}

/// date(year, month, day) as its first moment, else Python's ValueError (which nothing here
/// catches).
fn date(year: i128, month: i128, day: i128) -> Result<Stamp, Bad> {
    if !(1..=9999).contains(&year) || !(1..=12).contains(&month) {
        return fail("year or month out of range");
    }
    if day < 1 || day > i128::from(days_in_month(year as i64, month as u32)) {
        return fail("day is out of range for month");
    }
    let at = DateTime::date(year as i64, month as u32, day as u32);
    Ok(stamp_of(&at))
}

fn weekday(stamp: Stamp) -> i128 {
    i128::from(time_of(stamp).weekday())
}

/// A whole number of any size as one that counts here (its size capped far beyond any use).
fn capped(n: &BigInt) -> i128 {
    let most = 1i128 << 100;
    i128::try_from(n).unwrap_or(if n.sign() == num_bigint::Sign::Minus {
        -most
    } else {
        most
    })
}

/// One event, its times the computer's local time.
#[derive(Clone, Debug, PartialEq)]
pub struct Event {
    pub title: String,
    pub start: DateTime,
    pub end: DateTime,
    pub all_day: bool,
    pub location: String,
    pub calendar: String,
}

type Params = HashMap<String, String>;

/// A VEVENT's properties by their names, each with its parameters.
#[derive(Default)]
struct Raw {
    props: HashMap<String, Vec<(Params, String)>>,
}

impl Raw {
    fn get(&self, name: &str) -> Option<&(Params, String)> {
        self.props.get(name).and_then(|found| found.first())
    }

    fn value(&self, name: &str) -> &str {
        self.get(name).map_or("", |(_, value)| value.as_str())
    }
}

/// The lines of a text, those that go on over several put together.
fn unfold(text: &str) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    for line in text.split('\n') {
        match lines.last_mut() {
            Some(last) if line.starts_with(' ') || line.starts_with('\t') => {
                last.push_str(&line[1..]);
            }
            _ if !line.is_empty() => lines.push(line.to_string()),
            _ => {}
        }
    }
    lines
}

/// A line's name, its parameters and its value.
fn split(line: &str) -> (String, Params, String) {
    let mut quoted = false;
    let mut colon = None;
    for (at, ch) in line.char_indices() {
        if ch == '"' {
            quoted = !quoted;
        } else if ch == ':' && !quoted {
            colon = Some(at);
            break;
        }
    }
    let Some(colon) = colon else {
        return (lp_py::upper(line), Params::new(), String::new());
    };
    let (head, value) = (&line[..colon], &line[colon + 1..]);
    let mut parts = head.split(';');
    let name = parts.next().unwrap_or("");
    let mut found = Params::new();
    for param in parts {
        let (key, value) = param.split_once('=').unwrap_or((param, ""));
        found.insert(lp_py::upper(key), value.trim_matches('"').to_string());
    }
    (lp_py::upper(name), found, value.to_string())
}

/// A text's value with its escapes read (`\n`, `\,`, `\;`, `\\`).
fn text(value: &str) -> String {
    let mut out = String::new();
    let mut chars = value.chars().peekable();
    while let Some(ch) = chars.next() {
        match (ch, chars.peek()) {
            ('\\', Some('n' | 'N')) => {
                out.push('\n');
                chars.next();
            }
            ('\\', Some(&next @ ('\\' | ';' | ','))) => {
                out.push(next);
                chars.next();
            }
            _ => out.push(ch),
        }
    }
    out
}

/// The zone a TZID names, where there is one of its name.
fn zone_of(params: &Params) -> Option<Zone> {
    let name = params.get("TZID").map_or("", String::as_str);
    if name.is_empty() {
        return None;
    }
    let known = WINDOWS_ZONES.iter().find(|(windows, _)| *windows == name);
    zones::named(known.map_or(name, |(_, iana)| iana)).or_else(|| {
        // "/mozilla.org/20050126_1/Europe/Berlin": the last two parts name it
        let parts: Vec<&str> = name.trim_matches('/').split('/').collect();
        zones::named(&parts[parts.len().saturating_sub(2)..].join("/"))
    })
}

fn pattern(
    cell: &'static OnceLock<Option<Regex>>,
    text: &str,
    ignore_case: bool,
) -> Option<&'static Regex> {
    cell.get_or_init(|| Regex::new(text, ignore_case).ok())
        .as_ref()
}

/// datetime.strptime(text, "%Y%m%d") or, with `clock`, "%Y%m%dT%H%M%S".
fn strptime(text: &str, clock: bool) -> Result<Stamp, Bad> {
    static DATE: OnceLock<Option<Regex>> = OnceLock::new();
    static TIME: OnceLock<Option<Regex>> = OnceLock::new();
    const DAY_OF_YEAR: &str =
        r"(\d\d\d\d)(1[0-2]|0[1-9]|[1-9])(3[0-1]|[1-2]\d|0[1-9]|[1-9]| [1-9])";
    let found = match clock {
        false => pattern(&DATE, DAY_OF_YEAR, true),
        true => {
            let whole =
                format!("{DAY_OF_YEAR}T(2[0-3]|[0-1]\\d|\\d)([0-5]\\d|\\d)(6[0-1]|[0-5]\\d|\\d)");
            pattern(&TIME, &whole, true)
        }
    };
    let chars: Vec<char> = text.chars().collect();
    let found = found
        .and_then(|pattern| pattern.matches(&chars))
        .ok_or(Bad::Value)?;
    if found.end() != chars.len() {
        return Err(Bad::Value); // unconverted data remains
    }
    let number = |n: usize| -> Result<i128, Bad> {
        let digits = found.group(&chars, n).unwrap_or_default();
        lp_py::int_of_str(&digits)
            .map(|n| capped(&n))
            .ok_or(Bad::Value)
    };
    let (year, month, day) = (number(1)?, number(2)?, number(3)?);
    let day = date(year, month, day).map_err(|_| Bad::Value)?;
    if !clock {
        return Ok(day);
    }
    let (hour, minute, second) = (number(4)?, number(5)?, number(6)?);
    if second > 59 {
        return Err(Bad::Value);
    }
    Ok(day + (hour * 3600 + minute * 60 + second) * SECOND)
}

/// _moment: a start or end as local time, and whether it is a whole day.
fn moment(value: &str, params: &Params, local: &Zone) -> Result<(Stamp, bool), Bad> {
    let value = lp_py::strip(value);
    let chars: Vec<char> = value.chars().collect();
    let digits = chars.len() == 8 && chars.iter().all(|&ch| lp_py::decimal(ch).is_some());
    if params.get("VALUE").map(String::as_str) == Some("DATE") || digits {
        let day: String = chars.iter().take(8).collect();
        return Ok((strptime(&day, false)?, true));
    }
    let utc = value.ends_with('Z');
    let clock: String = value.trim_end_matches('Z').chars().take(15).collect();
    let mut at = strptime(&clock, true)?;
    let place = if utc {
        Some(Zone::utc())
    } else {
        zone_of(params)
    };
    if let Some(place) = place {
        let there = zones::to_local(local, &place, &time_of(at)).ok_or(Bad::Value)?;
        at = stamp_of(&there);
    }
    Ok((at, false))
}

/// _duration: a DURATION as microseconds (nothing where it is none).
fn duration(value: &str) -> Result<i128, Bad> {
    static SPAN: OnceLock<Option<Regex>> = OnceLock::new();
    let span = pattern(
        &SPAN,
        r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?",
        false,
    );
    let chars: Vec<char> = lp_py::strip(value).chars().collect();
    let Some(found) = span.and_then(|span| span.fullmatch(&chars)) else {
        return Ok(0);
    };
    let mut seconds = BigInt::from(0);
    for (group, each) in [(2, 604_800), (3, 86_400), (4, 3600), (5, 60), (6, 1)] {
        if let Some(digits) = found.group(&chars, group) {
            let count = lp_py::int_of_str(&digits).ok_or(Bad::Value)?;
            seconds += count * each;
        }
    }
    let total = capped(&seconds).saturating_mul(SECOND);
    let negative = found.group(&chars, 1).as_deref() == Some("-");
    // (timedelta holds 999999999 days and what is left of a day, or as many days back)
    let most = if negative {
        MOST_DAYS * DAY
    } else {
        (MOST_DAYS + 1) * DAY - 1
    };
    if total > most {
        return fail("days must have magnitude <= 999999999");
    }
    Ok(if negative { -total } else { total })
}

/// The events of a text as they are written, and the calendar's name.
fn raw_events(ics: &str) -> (Vec<Raw>, String) {
    let mut events = Vec::new();
    let mut name = String::new();
    let mut current: Option<Raw> = None;
    // VALARM and the like inside an event
    let mut depth = 0usize;
    for line in unfold(ics) {
        let (key, params, value) = split(&line);
        if key == "BEGIN" {
            if lp_py::upper(&value) == "VEVENT" && current.is_none() {
                current = Some(Raw::default());
            } else if current.is_some() {
                depth += 1;
            }
            continue;
        }
        if key == "END" {
            if current.is_some() && depth > 0 {
                depth -= 1;
            } else if current.is_some() && lp_py::upper(&value) == "VEVENT" {
                events.extend(current.take());
            }
            continue;
        }
        match &mut current {
            Some(raw) if depth == 0 => raw.props.entry(key).or_default().push((params, value)),
            _ if key == "X-WR-CALNAME" => name = text(&value),
            _ => {}
        }
    }
    (events, name)
}

fn rule_of(value: &str) -> HashMap<String, String> {
    let mut rule = HashMap::new();
    for part in value.split(';') {
        let (key, value) = part.split_once('=').unwrap_or((part, ""));
        if !key.is_empty() {
            rule.insert(lp_py::upper(key), value.to_string());
        }
    }
    rule
}

/// The `nth` weekday of a month (from its end where `nth` is not positive), where the month
/// has one.
fn nth_weekday(year: i128, month: i128, day: i128, nth: i128) -> Result<Option<Stamp>, Bad> {
    let found = if nth > 0 {
        let first = date(year, month, 1)?;
        let ahead = (day - weekday(first)).rem_euclid(7) + 7 * (nth - 1);
        within(first + days_span(ahead)?)?
    } else {
        let following = date(year + i128::from(month == 12), month % 12 + 1, 1)?;
        let last = following - DAY;
        let back = (weekday(last) - day).rem_euclid(7) + 7 * (-nth - 1);
        within(last - days_span(back)?)?
    };
    Ok((i128::from(time_of(found).month) == month).then_some(found))
}

/// _first_step: the step to begin with: shortly before `since`, so a rule that has run for
/// years does not use up its steps before it reaches the window.
fn first_step(start: Stamp, freq: &str, interval: i128, since: Option<Stamp>) -> i128 {
    let Some(since) = since.filter(|&since| since > start) else {
        return 0;
    };
    let (from, to) = (time_of(start), time_of(since));
    let years = i128::from(to.year - from.year);
    let steps = match freq {
        "DAILY" => (since - start).div_euclid(DAY).div_euclid(interval),
        "WEEKLY" => (since - start).div_euclid(DAY).div_euclid(7 * interval),
        "MONTHLY" => {
            (years * 12 + i128::from(to.month) - i128::from(from.month)).div_euclid(interval)
        }
        "YEARLY" => years.div_euclid(interval),
        _ => return 0,
    };
    (steps - 1).max(0)
}

/// What a rule's starts are taken into.
struct Taken {
    start: Stamp,
    until: Option<Stamp>,
    count: Option<i128>,
    window: Stamp,
    found: Vec<Stamp>,
}

impl Taken {
    /// Adds one; false once the rule or the window ends.
    fn take(&mut self, at: Stamp) -> bool {
        if at < self.start {
            return true;
        }
        let over = self.until.is_some_and(|until| at > until);
        let counted = self
            .count
            .is_some_and(|count| self.found.len() as i128 >= count);
        if over || counted || at > self.window {
            return false;
        }
        self.found.push(at);
        true
    }
}

/// _occurrences: the starts a rule gives, from `start` until the window's end (from shortly
/// before `since` on, unless the rule counts its occurrences).
fn occurrences(
    start: Stamp,
    rule: &HashMap<String, String>,
    window: Stamp,
    since: Option<Stamp>,
    local: &Zone,
) -> Result<Vec<Stamp>, Bad> {
    let get = |key: &str| rule.get(key).map_or("", String::as_str);
    let whole = |digits: &str| match lp_py::int_of_str(digits) {
        Some(n) => Ok(capped(&n)),
        None => fail("invalid literal for int()"),
    };
    let freq = get("FREQ");
    let interval = match rule.get("INTERVAL").map(String::as_str) {
        None | Some("") => 1,
        Some(digits) => whole(digits)?.max(1),
    };
    let digits = |text: &str| !text.is_empty() && text.chars().all(lp_py::isdigit);
    let count = match digits(get("COUNT")) {
        true => Some(whole(get("COUNT"))?),
        false => None,
    };
    let mut until = None;
    if !get("UNTIL").is_empty() {
        match moment(get("UNTIL"), &Params::new(), local) {
            Ok((at, _)) if get("UNTIL").chars().count() == 8 => {
                until = Some(within(at + DAY - SECOND)?); // the whole last day
            }
            Ok((at, _)) => until = Some(at),
            Err(Bad::Value) => {}
            Err(other) => return Err(other),
        }
    }
    let by_day: Vec<&str> = get("BYDAY")
        .split(',')
        .filter(|day| !day.is_empty())
        .collect();
    let mut by_month_day = Vec::new();
    for day in get("BYMONTHDAY").split(',') {
        if digits(day.trim_start_matches('-')) {
            by_month_day.push(whole(day)?);
        }
    }
    let clock = start.rem_euclid(DAY);
    let from = time_of(start);
    let mut taken = Taken {
        start,
        until,
        count,
        window,
        found: Vec::new(),
    };
    let first = match count {
        Some(_) => 0,
        None => first_step(start, freq, interval, since),
    };
    static SPEC: OnceLock<Option<Regex>> = OnceLock::new();
    let spec_pattern = pattern(&SPEC, r"([+-]?\d*)([A-Z]{2})", false);
    for step in first..first + MAX_STEPS {
        let steps = step.saturating_mul(interval);
        match freq {
            "DAILY" => {
                if !taken.take(within(start + days_span(steps)?)?) {
                    break;
                }
            }
            "WEEKLY" => {
                let monday = within(start - clock - i128::from(from.weekday()) * DAY)?;
                let week = within(monday + days_span(steps.saturating_mul(7))?)?;
                let last_two = |day: &str| -> String {
                    let chars: Vec<char> = day.chars().collect();
                    chars[chars.len().saturating_sub(2)..].iter().collect()
                };
                let mut days: Vec<i128> = by_day
                    .iter()
                    .filter_map(|day| DAYS.iter().position(|known| *known == last_two(day)))
                    .map(|day| day as i128)
                    .collect();
                days.sort_unstable();
                if days.is_empty() {
                    days.push(i128::from(from.weekday()));
                }
                let mut goes_on = true;
                for day in days {
                    if !taken.take(within(week + day * DAY)? + clock) {
                        goes_on = false;
                        break;
                    }
                }
                if !goes_on {
                    break;
                }
            }
            "MONTHLY" => {
                let month0 = i128::from(from.month) - 1 + steps;
                let year = i128::from(from.year) + month0.div_euclid(12);
                let month = month0.rem_euclid(12) + 1;
                let mut days: Vec<Stamp> = Vec::new();
                for spec in &by_day {
                    let chars: Vec<char> = spec.chars().collect();
                    let Some(found) = spec_pattern.and_then(|p| p.fullmatch(&chars)) else {
                        continue;
                    };
                    let name = found.group(&chars, 2).unwrap_or_default();
                    let Some(day) = DAYS.iter().position(|known| *known == name) else {
                        continue;
                    };
                    let nth = match found.group(&chars, 1).unwrap_or_default().as_str() {
                        "" => 1,
                        number => whole(number)?,
                    };
                    days.extend(nth_weekday(year, month, day as i128, nth)?);
                }
                let own = [i128::from(from.day)];
                let numbers: &[i128] = match (by_month_day.is_empty(), by_day.is_empty()) {
                    (false, _) => &by_month_day,
                    (true, false) => &[],
                    (true, true) => &own,
                };
                for &number in numbers {
                    let following = date(year + i128::from(month == 12), month % 12 + 1, 1)?;
                    let length = (following - date(year, month, 1)?) / DAY;
                    let day = if number > 0 {
                        number
                    } else {
                        length + number + 1
                    };
                    if (1..=length).contains(&day) {
                        days.push(date(year, month, day)?);
                    }
                }
                days.sort_unstable();
                let mut goes_on = true;
                for day in days {
                    if !taken.take(day + clock) {
                        goes_on = false;
                        break;
                    }
                }
                if !goes_on {
                    break;
                }
            }
            "YEARLY" => {
                let year = i128::from(from.year) + steps;
                // (29 February where the year has none: left out)
                let Ok(day) = date(year, i128::from(from.month), i128::from(from.day)) else {
                    continue;
                };
                if !taken.take(day + clock) {
                    break;
                }
            }
            _ => {
                taken.take(start);
                break;
            }
        }
    }
    Ok(taken.found)
}

/// An event before its times are turned into datetime's.
struct Found {
    title: String,
    start: Stamp,
    end: Stamp,
    all_day: bool,
    location: String,
    calendar: String,
}

fn events_of(
    ics: &str,
    now: Stamp,
    days: i64,
    calendar: &str,
    local: &Zone,
) -> Result<Vec<Found>, Bad> {
    let (raws, name) = raw_events(ics);
    let window_start = within(now - DAY)?;
    let window_end = within(now + i128::from(days) * DAY)?;
    let mut moved: HashSet<(String, Stamp)> = HashSet::new();
    let mut plain: Vec<&Raw> = Vec::new();
    for raw in &raws {
        if let Some((params, value)) = raw.get("RECURRENCE-ID") {
            match moment(value, params, local) {
                Ok((at, _)) => moved.insert((raw.value("UID").to_string(), at)),
                Err(Bad::Value) => continue,
                Err(other) => return Err(other),
            };
        }
        plain.push(raw);
    }
    let mut found = Vec::new();
    for raw in plain {
        let Some((start_params, start_value)) = raw.get("DTSTART") else {
            continue;
        };
        if lp_py::upper(raw.value("STATUS")) == "CANCELLED" {
            continue;
        }
        let times = || -> Result<(Stamp, Stamp, bool), Bad> {
            let (start, all_day) = moment(start_value, start_params, local)?;
            let end = if let Some((params, value)) = raw.get("DTEND") {
                moment(value, params, local)?.0
            } else if raw.get("DURATION").is_some() {
                within(start + duration(raw.value("DURATION"))?)?
            } else {
                within(start + if all_day { DAY } else { 0 })?
            };
            Ok((start, end, all_day))
        };
        let (start, end, all_day) = match times() {
            Ok(times) => times,
            Err(Bad::Value) => continue,
            Err(other) => return Err(other),
        };
        let length = (end - start).max(0);
        let mut title = text(raw.value("SUMMARY"));
        if title.is_empty() {
            title = lp_i18n::t("(no title)");
        }
        let location = text(raw.value("LOCATION"));
        let uid = raw.value("UID").to_string();
        let mut starts = vec![start];
        if let (Some((_, rule)), None) = (raw.get("RRULE"), raw.get("RECURRENCE-ID")) {
            let since = within(window_start - length)?;
            starts = occurrences(start, &rule_of(rule), window_end, Some(since), local)?;
            let mut left_out = HashSet::new();
            for (params, value) in raw.props.get("EXDATE").into_iter().flatten() {
                for part in value.split(',') {
                    match moment(part, params, local) {
                        Ok((at, _)) => left_out.insert(at),
                        Err(Bad::Value) => continue,
                        Err(other) => return Err(other),
                    };
                }
            }
            starts.retain(|at| !left_out.contains(at) && !moved.contains(&(uid.clone(), *at)));
        }
        for begin in starts {
            let finish = within(begin + length)?;
            if finish >= window_start && begin < window_end {
                found.push(Found {
                    title: title.clone(),
                    start: begin,
                    end: finish,
                    all_day,
                    location: location.clone(),
                    calendar: if calendar.is_empty() {
                        name.clone()
                    } else {
                        calendar.to_string()
                    },
                });
            }
        }
    }
    found.sort_by(|a, b| (a.start, !a.all_day, &a.title).cmp(&(b.start, !b.all_day, &b.title)));
    Ok(found)
}

/// events_from_ics: the events of an .ics text that end after `now - 1 day` and start before
/// `now + days`, repeating ones unfolded; their times in the computer's zone `local`. An
/// error where the calendar cannot be read (Python raises).
pub fn events_from_ics(
    ics: &str,
    now: &DateTime,
    days: i64,
    calendar: &str,
    local: &Zone,
) -> Result<Vec<Event>, String> {
    let found = events_of(ics, stamp_of(now), days, calendar, local).map_err(|bad| match bad {
        Bad::Value => "a value that is none".to_string(),
        Bad::Fail(why) => why,
    })?;
    Ok(found
        .into_iter()
        .map(|event| Event {
            title: event.title,
            start: time_of(event.start),
            end: time_of(event.end),
            all_day: event.all_day,
            location: event.location,
            calendar: event.calendar,
        })
        .collect())
}

/// _when: the day ("Today", "Tomorrow", "Fri 16 Oct") and the time ("14:00", "All day").
fn when(event: &Event, now: &DateTime) -> (String, String) {
    let (start, end, at) = (stamp_of(&event.start), stamp_of(&event.end), stamp_of(now));
    let today = now.toordinal();
    let mut day = event.start.toordinal();
    if event.all_day && start <= at && at < end {
        day = today;
    }
    let named = |fmt: &str| {
        let midnight = DateTime::from_ordinal(day).unwrap_or(event.start.midnight());
        lp_i18n::format_date(&midnight, fmt).unwrap_or_default()
    };
    let name = if day == today {
        lp_i18n::t("Today")
    } else if day == today + 1 {
        lp_i18n::t("Tomorrow")
    } else if day < today + 7 {
        named("%A")
    } else {
        named("%a %d %b")
    };
    let clock = match event.all_day {
        true => lp_i18n::t("All day"),
        false => format!("{:02}:{:02}", event.start.hour, event.start.minute),
    };
    (name, clock)
}

/// calendar_readings: the events to come (or going on) at `now`, as readings.
pub fn calendar_readings(events: &[Event], now: &DateTime, local: &Zone) -> Readings {
    let at = stamp_of(now);
    let upcoming: Vec<&Event> = events
        .iter()
        .filter(|event| {
            let (start, end) = (stamp_of(&event.start), stamp_of(&event.end));
            end > at || (end == start && start >= at)
        })
        .collect();
    let mut out = Readings::new();
    let mut put = |key: String, value: Value, unit: &str| {
        let reading = Reading {
            unit: unit.into(),
            label: key.rsplit('.').next().unwrap_or("").to_string(),
            ..Reading::of(value)
        };
        out.insert(key, reading);
    };
    let today = upcoming
        .iter()
        .filter(|event| event.start.toordinal() <= now.toordinal())
        .count();
    put(
        "calendar.events".into(),
        Value::Float(upcoming.len() as f64),
        "",
    );
    put("calendar.today".into(), Value::Float(today as f64), "");
    for (n, event) in upcoming.iter().take(MAX_EVENTS).enumerate() {
        let n = n + 1;
        let (day, clock) = when(event, now);
        let (start, end) = (stamp_of(&event.start), stamp_of(&event.end));
        let both = match event.all_day {
            true => format!("{day} · {clock}"),
            false => format!("{day} {clock}"),
        };
        put(
            format!("calendar.{n}.title"),
            Value::Str(event.title.clone()),
            "",
        );
        put(format!("calendar.{n}.day"), Value::Str(day), "");
        put(format!("calendar.{n}.time"), Value::Str(clock), "");
        put(format!("calendar.{n}.when"), Value::Str(both), "");
        if !event.location.is_empty() {
            put(
                format!("calendar.{n}.location"),
                Value::Str(event.location.clone()),
                "",
            );
        }
        if !event.calendar.is_empty() {
            put(
                format!("calendar.{n}.calendar"),
                Value::Str(event.calendar.clone()),
                "",
            );
        }
        if let Some(seconds) = zones::timestamp(local, &event.start, false) {
            put(format!("calendar.{n}.start"), Value::Float(seconds), "s");
        }
        let minutes = (start - at) as f64 / 1e6 / 60.0;
        put(
            format!("calendar.{n}.minutes"),
            Value::Float(minutes),
            "min",
        );
        if start <= at && at < end {
            put(format!("calendar.{n}.now"), Value::int(1), ""); // only while it is on
        }
    }
    out
}

/// Path.expanduser: `~` at the start as the user's folder.
fn expanduser(path: &str) -> Option<std::path::PathBuf> {
    let Some(rest) = path.strip_prefix('~') else {
        return Some(path.into());
    };
    if !(rest.is_empty() || rest.starts_with('/') || rest.starts_with('\\')) {
        return None;
    }
    let home = match cfg!(windows) {
        true => std::env::var_os("USERPROFILE"),
        false => std::env::var_os("HOME"),
    }?;
    Some(std::path::PathBuf::from(home).join(rest.trim_start_matches(['/', '\\'])))
}

/// _load: a calendar's text; why there is none, without its address (it is a secret).
fn load(source: &str, agent: &str) -> Result<String, String> {
    let mut source = lp_py::strip(source).to_string();
    if let Some(rest) = source.strip_prefix("webcal://") {
        source = format!("https://{rest}");
    }
    let data = if source.starts_with("http://") || source.starts_with("https://") {
        lp_net::get(&source, &[("User-Agent", agent)], 15.0, MAX_BYTES + 1).map_err(
            |why| match why.status {
                Some(status) => format!("the server's answer {status}"),
                None => "no answer".to_string(),
            },
        )?
    } else {
        let path = expanduser(&source).ok_or("no folder of that user")?;
        let mut data = Vec::new();
        std::fs::File::open(path)
            .and_then(|file| file.take(MAX_BYTES as u64 + 1).read_to_end(&mut data))
            .map_err(|why| format!("{:?}", why.kind()))?;
        data
    };
    if data.len() > MAX_BYTES {
        return Err("calendar larger than 8 MB".into());
    }
    Ok(String::from_utf8_lossy(&data).into_owned())
}

#[derive(Default)]
struct State {
    stop: bool,
    rounds: u64,
    /// each source's text, in the order they first came
    texts: IndexMap<String, String>,
    /// the texts changed since the events were made of them
    changed: bool,
    notes: Vec<String>,
}

struct Shared {
    state: Mutex<State>,
    changed: Condvar,
}

fn poll(shared: &Shared, sources: &[String], agent: &str, every: Duration) {
    let mut failed: HashSet<usize> = HashSet::new();
    loop {
        for (n, source) in sources.iter().enumerate() {
            let loaded = load(source, agent);
            let Ok(mut state) = shared.state.lock() else {
                return;
            };
            match loaded {
                Ok(ics) => {
                    state.texts.insert(source.clone(), ics);
                    state.changed = true;
                    failed.remove(&n);
                }
                // (a calendar may be away for a while)
                Err(why) => {
                    if failed.insert(n) {
                        let note = format!("calendar {} not loaded: {why}", n + 1);
                        state.notes.push(note);
                    }
                }
            }
        }
        let Ok(mut state) = shared.state.lock() else {
            return;
        };
        state.rounds += 1;
        shared.changed.notify_all();
        let until = Instant::now() + every;
        while !state.stop {
            let left = until.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            state = match shared.changed.wait_timeout(state, left) {
                Ok((state, _)) => state,
                Err(_) => return,
            };
        }
        if state.stop {
            return;
        }
    }
}

/// `calendar.*` from iCalendar addresses and files (CalendarProvider).
pub struct CalendarSource {
    shared: Arc<Shared>,
    days: i64,
    events: Vec<Event>,
    /// how many texts the events are made of, and on which day
    built_for: Option<(usize, i64)>,
    thread: Option<JoinHandle<()>>,
}

impl CalendarSource {
    /// The calendars at `sources`, `days` ahead (1 to 366), loaded every `update_minutes`
    /// (one at least) by a program that names itself `agent`.
    pub fn new(
        sources: Vec<String>,
        days: i64,
        update_minutes: f64,
        agent: &str,
    ) -> CalendarSource {
        let mut state = State::default();
        if sources.is_empty() {
            let note = "calendar: no sources in [sensors.calendar]";
            state.notes.push(note.into());
        }
        let shared = Arc::new(Shared {
            state: Mutex::new(state),
            changed: Condvar::new(),
        });
        let every = Duration::from_secs_f64((update_minutes * 60.0).max(60.0));
        let (theirs, agent) = (Arc::clone(&shared), agent.to_string());
        let thread = std::thread::Builder::new()
            .name("sensors-calendar".into())
            .spawn(move || poll(&theirs, &sources, &agent, every))
            .ok();
        CalendarSource {
            shared,
            days: days.clamp(1, 366),
            events: Vec::new(),
            built_for: None,
            thread,
        }
    }

    pub fn read(&mut self) -> Readings {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0.0, |since| since.as_secs_f64());
        let Some(local) = zones::local() else {
            return Readings::new();
        };
        let Some((now, _)) = zones::fromtimestamp(local, now) else {
            return Readings::new();
        };
        if let Ok(mut state) = self.shared.state.lock() {
            let stamp = (state.texts.len(), now.toordinal());
            let changed = std::mem::take(&mut state.changed);
            if changed || self.built_for != Some(stamp) || now.minute % 10 == 0 {
                let mut events = Vec::new();
                let mut notes = Vec::new();
                for ics in state.texts.values() {
                    match events_from_ics(ics, &now, self.days, "", local) {
                        Ok(found) => events.extend(found),
                        // (one odd calendar must not stop the others)
                        Err(why) if changed => {
                            notes.push(format!("calendar could not be read: {why}"));
                        }
                        Err(_) => {}
                    }
                }
                state.notes.extend(notes);
                events.sort_by(|a, b| {
                    (stamp_of(&a.start), !a.all_day, &a.title).cmp(&(
                        stamp_of(&b.start),
                        !b.all_day,
                        &b.title,
                    ))
                });
                self.events = events;
                self.built_for = Some(stamp);
            }
        }
        calendar_readings(&self.events, &now, local)
    }

    /// What it said since it was last asked.
    pub fn notes(&self) -> Vec<String> {
        self.shared
            .state
            .lock()
            .map(|mut state| std::mem::take(&mut state.notes))
            .unwrap_or_default()
    }

    /// Waits until every calendar was asked for once, `seconds` at most.
    pub fn wait_ready(&self, seconds: f64) {
        let until = Instant::now() + Duration::from_secs_f64(seconds.max(0.0));
        let Ok(mut state) = self.shared.state.lock() else {
            return;
        };
        while state.rounds == 0 {
            let left = until.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return;
            }
            state = match self.shared.changed.wait_timeout(state, left) {
                Ok((state, _)) => state,
                Err(_) => return,
            };
        }
    }

    pub fn close(&mut self) {
        if let Ok(mut state) = self.shared.state.lock() {
            state.stop = true;
        }
        self.shared.changed.notify_all();
        drop(self.thread.take());
    }
}

impl Drop for CalendarSource {
    fn drop(&mut self) {
        self.close();
    }
}
