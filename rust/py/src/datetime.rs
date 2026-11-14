//! What Python's datetime knows of a day and a time without a zone (a naive datetime): its
//! day's number from 1 January of the year 1, its weekday, its day of the year and the ISO
//! week it is in.

/// A naive datetime, as Python's has it: the year 1 to 9999, a month, a day of it, the time to
/// the microsecond. Ordered as time goes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct DateTime {
    pub year: i64,
    pub month: u32,
    pub day: u32,
    pub hour: u32,
    pub minute: u32,
    pub second: u32,
    pub microsecond: u32,
}

/// Whether `year` has a 29 February.
pub fn is_leap(year: i64) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

/// The days of `month` in `year`.
pub fn days_in_month(year: i64, month: u32) -> u32 {
    match month {
        2 if is_leap(year) => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// The days of the year before `month`.
fn days_before_month(year: i64, month: u32) -> i64 {
    (1..month).map(|m| i64::from(days_in_month(year, m))).sum()
}

/// The day of 1 January of `year`, by its number (date.toordinal).
fn first_of(year: i64) -> i64 {
    let y = year - 1;
    y * 365 + y.div_euclid(4) - y.div_euclid(100) + y.div_euclid(400) + 1
}

impl DateTime {
    /// datetime(year, month, day) at midnight.
    pub fn date(year: i64, month: u32, day: u32) -> DateTime {
        DateTime {
            year,
            month,
            day,
            hour: 0,
            minute: 0,
            second: 0,
            microsecond: 0,
        }
    }

    /// date.fromordinal: the day of number `n` (1 January of the year 1 the first), at
    /// midnight; None before the year 1 or past 9999.
    pub fn from_ordinal(n: i64) -> Option<DateTime> {
        if !(1..=3_652_059).contains(&n) {
            return None;
        }
        // (CPython's ord_to_ymd: the 400, 100 and 4 years and the single ones before the day)
        let n = n - 1;
        let (n400, n) = (n / 146_097, n % 146_097);
        let (n100, n) = (n / 36_524, n % 36_524);
        let (n4, n) = (n / 1_461, n % 1_461);
        let (n1, mut n) = (n / 365, n % 365);
        let year = n400 * 400 + 1 + n100 * 100 + n4 * 4 + n1;
        if n1 == 4 || n100 == 4 {
            // (the last day of a leap year that ends four years, or of 400 years)
            return Some(DateTime::date(year - 1, 12, 31));
        }
        let mut month = 1;
        while n >= i64::from(days_in_month(year, month)) {
            n -= i64::from(days_in_month(year, month));
            month += 1;
        }
        Some(DateTime::date(year, month, n as u32 + 1))
    }

    /// The day alone, at midnight (datetime.combine(d.date(), time())).
    pub fn midnight(&self) -> DateTime {
        DateTime::date(self.year, self.month, self.day)
    }

    /// date.toordinal: the day's number, 1 January of the year 1 the first.
    pub fn toordinal(&self) -> i64 {
        first_of(self.year) + days_before_month(self.year, self.month) + i64::from(self.day) - 1
    }

    /// date.weekday: Monday 0 to Sunday 6.
    pub fn weekday(&self) -> u32 {
        // (the day 1 was a Monday)
        (self.toordinal() + 6).rem_euclid(7) as u32
    }

    /// struct tm's tm_wday: Sunday 0 to Saturday 6.
    pub fn tm_wday(&self) -> u32 {
        (self.weekday() + 1) % 7
    }

    /// struct tm's tm_yday: 1 January 0.
    pub fn tm_yday(&self) -> u32 {
        (days_before_month(self.year, self.month) + i64::from(self.day) - 1) as u32
    }

    /// The microseconds since midnight.
    pub fn time_of_day(&self) -> i64 {
        ((i64::from(self.hour) * 60 + i64::from(self.minute)) * 60 + i64::from(self.second))
            * 1_000_000
            + i64::from(self.microsecond)
    }

    /// datetime - timedelta(seconds=1): None before the first second of the year 1 (Python's
    /// OverflowError, "date value out of range").
    pub fn second_before(&self) -> Option<DateTime> {
        if self.second > 0 {
            return Some(DateTime {
                second: self.second - 1,
                ..*self
            });
        }
        if self.minute > 0 {
            return Some(DateTime {
                minute: self.minute - 1,
                second: 59,
                ..*self
            });
        }
        if self.hour > 0 {
            return Some(DateTime {
                hour: self.hour - 1,
                minute: 59,
                second: 59,
                ..*self
            });
        }
        let day = DateTime::from_ordinal(self.toordinal() - 1)?;
        Some(DateTime {
            hour: 23,
            minute: 59,
            second: 59,
            microsecond: self.microsecond,
            ..day
        })
    }

    /// datetime - datetime, in microseconds.
    pub fn since(&self, earlier: &DateTime) -> i64 {
        (self.toordinal() - earlier.toordinal()) * 86_400_000_000 + self.time_of_day()
            - earlier.time_of_day()
    }

    /// date.isocalendar: the ISO year, the week in it (1 to 53) and the weekday (1 Monday to 7
    /// Sunday).
    pub fn isocalendar(&self) -> (i64, u32, u32) {
        let monday_of_week1 = |year: i64| {
            // the Monday of the week with 4 January in it
            let fourth = DateTime::date(year, 1, 4);
            fourth.toordinal() - i64::from(fourth.weekday())
        };
        let day = self.toordinal();
        let mut year = self.year;
        let mut start = monday_of_week1(year);
        if day < start {
            year -= 1;
            start = monday_of_week1(year);
        } else {
            let next = monday_of_week1(year + 1);
            if day >= next {
                year += 1;
                start = next;
            }
        }
        let week = (day - start) / 7 + 1;
        (year, week as u32, self.weekday() + 1)
    }

    /// datetime + timedelta(microseconds=micros): None before the year 1 or past 9999
    /// (Python's OverflowError).
    pub fn plus(&self, micros: i64) -> Option<DateTime> {
        let total = self.time_of_day().checked_add(micros)?;
        let day = DateTime::from_ordinal(
            self.toordinal()
                .checked_add(total.div_euclid(86_400_000_000))?,
        )?;
        let rest = total.rem_euclid(86_400_000_000);
        let seconds = rest / 1_000_000;
        Some(DateTime {
            hour: (seconds / 3600) as u32,
            minute: (seconds / 60 % 60) as u32,
            second: (seconds % 60) as u32,
            microsecond: (rest % 1_000_000) as u32,
            ..day
        })
    }
}

/// What datetime.fromisoformat makes of a text.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Iso {
    /// a time without a zone
    Naive(DateTime),
    /// a time with an offset from UTC: its local time, the offset's seconds and microseconds
    Aware(DateTime, i64, i64),
    /// ValueError
    Invalid,
}

/// `count` ASCII digits at `at` as a number, and the place after them.
fn iso_digits(text: &[u8], at: usize, count: usize) -> Option<(i64, usize)> {
    let digits = text.get(at..at + count)?;
    let mut n = 0i64;
    for digit in digits {
        if !digit.is_ascii_digit() {
            return None;
        }
        n = n * 10 + i64::from(digit - b'0');
    }
    Some((n, at + count))
}

/// _find_isoformat_datetime_separator: where the date of an ISO text ends.
fn iso_separator(text: &[u8]) -> Option<usize> {
    let len = text.len();
    if len == 7 {
        return Some(7);
    }
    let digit = |at: usize| text.get(at).is_some_and(u8::is_ascii_digit);
    if text[4] == b'-' {
        if text[5] != b'W' {
            return Some(10); // YYYY-MM-DD
        }
        if len < 8 {
            return None;
        }
        if len > 8 && text[8] == b'-' {
            // YYYY-Www-D (10) or YYYY-Www-HH (8)
            if len == 9 {
                return None;
            }
            return Some(if len > 10 && digit(10) { 8 } else { 10 });
        }
        return Some(8); // YYYY-Www
    }
    if text[4] == b'W' {
        // YYYYWww (7) or YYYYWwwd (8): the digits run on in pairs after the date
        let mut end = 7;
        while end < len && digit(end) {
            end += 1;
        }
        return Some(if end < 9 {
            end
        } else if end % 2 == 0 {
            7
        } else {
            8
        });
    }
    Some(8) // YYYYMMDD
}

/// parse_isoformat_date: the year, month and day of the first `len` bytes.
fn iso_date(text: &[u8], len: usize) -> Option<DateTime> {
    let (year, mut at) = iso_digits(text, 0, 4)?;
    let separated = text.get(at) == Some(&b'-');
    if separated {
        at += 1;
    }
    if text.get(at) == Some(&b'W') {
        // a date of the ISO calendar: the year, its week and the day of the week
        let (week, mut at) = iso_digits(text, at + 1, 2)?;
        let mut weekday = 1;
        if at < len {
            if separated {
                if text.get(at) != Some(&b'-') {
                    return None;
                }
                at += 1;
            }
            weekday = iso_digits(text, at, 1)?.0;
        }
        if year < 1 || !(1..=7).contains(&weekday) {
            return None;
        }
        let first = DateTime::date(year, 1, 1);
        let first_weekday = i64::from(first.weekday());
        if week <= 0 || week >= 53 {
            // (a year has 53 weeks where it starts on a Thursday, or a leap year on a Wednesday)
            let long = first_weekday == 3 || (first_weekday == 2 && is_leap(year));
            if !(week == 53 && long) {
                return None;
            }
        }
        let mut week1_monday = first.toordinal() - first_weekday;
        if first_weekday > 3 {
            week1_monday += 7;
        }
        return DateTime::from_ordinal(week1_monday + (week - 1) * 7 + weekday - 1);
    }
    let (month, mut at) = iso_digits(text, at, 2)?;
    if separated {
        if text.get(at) != Some(&b'-') {
            return None;
        }
        at += 1;
    }
    let (day, _) = iso_digits(text, at, 2)?;
    let valid = (1..=9999).contains(&year)
        && (1..=12).contains(&month)
        && day >= 1
        && day <= i64::from(days_in_month(year, month as u32));
    valid.then(|| DateTime::date(year, month as u32, day as u32))
}

/// parse_hh_mm_ss_ff: hours, minutes, seconds and microseconds of `text`, and whether
/// something that is none of them follows; None where it is no time.
fn iso_time(text: &[u8]) -> Option<([i64; 4], bool)> {
    let mut parts = [0i64; 4];
    let end = text.len();
    let mut at = 0;
    let mut separated = true;
    for (i, part) in parts.iter_mut().take(3).enumerate() {
        let (value, after) = iso_digits(text, at, 2)?;
        *part = value;
        // (the character after the two digits; past the end the text's own end)
        let next = text.get(after).copied().unwrap_or(0);
        at = after + 1;
        if i == 0 {
            separated = next == b':';
        }
        if at >= end {
            return Some((parts, next != 0));
        }
        if separated && next == b':' {
            continue;
        }
        if next == b'.' || next == b',' {
            break;
        }
        if separated {
            return None; // a malformed separator
        }
        at -= 1;
    }
    // the fraction: up to six digits count, more are cut off
    let count = (end - at).min(6);
    if count == 0 {
        return None;
    }
    let (mut micros, mut at) = iso_digits(text, at, count)?;
    for _ in count..6 {
        micros *= 10;
    }
    parts[3] = micros;
    while text.get(at).is_some_and(u8::is_ascii_digit) {
        at += 1;
    }
    Some((parts, at < end))
}

/// datetime.fromisoformat(text) as Python 3.12 reads one: a date (YYYY-MM-DD, YYYYMMDD or
/// of the ISO calendar, YYYY-Www-D), then after any one character a time (HH, HH:MM,
/// HH:MM:SS, with a fraction, or without the colons), then an offset from UTC (Z, +HH:MM).
pub fn fromisoformat(text: &str) -> Iso {
    let bytes = text.as_bytes();
    if bytes.len() < 7 {
        return Iso::Invalid;
    }
    let Some(separator) = iso_separator(bytes) else {
        return Iso::Invalid;
    };
    let Some(day) = iso_date(bytes, separator) else {
        return Iso::Invalid;
    };
    if bytes.len() <= separator {
        return Iso::Naive(day);
    }
    // (the separator is one character, of however many bytes)
    let Some(width) = text
        .get(separator..)
        .and_then(|rest| rest.chars().next())
        .map(char::len_utf8)
    else {
        return Iso::Invalid;
    };
    let time = &bytes[separator + width..];
    if time.is_empty() {
        return Iso::Invalid;
    }
    let zone = time
        .iter()
        .position(|byte| matches!(byte, b'Z' | b'+' | b'-'))
        .unwrap_or(time.len());
    let Some((parts, more)) = iso_time(&time[..zone]) else {
        return Iso::Invalid;
    };
    let at = DateTime {
        hour: parts[0] as u32,
        minute: parts[1] as u32,
        second: parts[2] as u32,
        microsecond: parts[3] as u32,
        ..day
    };
    let valid = |parts: &[i64; 4]| parts[0] < 24 && parts[1] < 60 && parts[2] < 60;
    if zone == time.len() {
        return match !more && valid(&parts) {
            true => Iso::Naive(at),
            false => Iso::Invalid,
        };
    }
    if !valid(&parts) {
        return Iso::Invalid;
    }
    if time[zone] == b'Z' {
        return match zone + 1 == time.len() {
            true => Iso::Aware(at, 0, 0),
            false => Iso::Invalid,
        };
    }
    let sign = if time[zone] == b'-' { -1 } else { 1 };
    match iso_time(&time[zone + 1..]) {
        Some((offset, false)) => {
            let seconds = offset[0] * 3600 + offset[1] * 60 + offset[2];
            // (an offset of a day and more is none Python's timezone takes)
            if seconds.abs() >= 86_400 {
                return Iso::Invalid;
            }
            Iso::Aware(at, sign * seconds, sign * offset[3])
        }
        _ => Iso::Invalid,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn days_and_weeks_as_python_counts_them() {
        // (datetime.date(...).toordinal(), weekday(), isocalendar() in Python 3.12)
        let cases = [
            ((1, 1, 1), 1, 0, (1, 1, 1)),
            ((2026, 11, 7), 739_927, 5, (2026, 45, 6)),
            ((2027, 1, 3), 739_984, 6, (2026, 53, 7)),
            ((2026, 1, 1), 739_617, 3, (2026, 1, 4)),
            ((2024, 12, 30), 739_250, 0, (2025, 1, 1)),
            ((9999, 12, 31), 3_652_059, 4, (9999, 52, 5)),
            ((2000, 2, 29), 730_179, 1, (2000, 9, 2)),
        ];
        for ((y, m, d), ordinal, weekday, iso) in cases {
            let at = DateTime::date(y, m, d);
            assert_eq!(at.toordinal(), ordinal, "{y}-{m}-{d}");
            assert_eq!(at.weekday(), weekday, "{y}-{m}-{d}");
            assert_eq!(at.isocalendar(), iso, "{y}-{m}-{d}");
        }
        assert_eq!(DateTime::date(2026, 12, 31).tm_yday(), 364);
        assert_eq!(DateTime::date(2028, 12, 31).tm_yday(), 365);
    }

    #[test]
    fn days_from_their_numbers_as_python_finds_them() {
        // (datetime.date.fromordinal(n) in Python 3.12)
        let cases = [
            (1, (1, 1, 1)),
            (2, (1, 1, 2)),
            (59, (1, 2, 28)),
            (60, (1, 3, 1)),
            (365, (1, 12, 31)),
            (366, (2, 1, 1)),
            (730_179, (2000, 2, 29)),
            (730_180, (2000, 3, 1)),
            (693_596, (1900, 1, 1)),
            (693_960, (1900, 12, 31)),
            (739_927, (2026, 11, 7)),
            (3_652_059, (9999, 12, 31)),
            (146_097, (400, 12, 31)),
            (146_096, (400, 12, 30)),
            (36_524, (100, 12, 31)),
            (36_525, (101, 1, 1)),
            (1_461, (4, 12, 31)),
            (1_460, (4, 12, 30)),
            (2_612_640, (7154, 3, 2)),
            (1_071_415, (2934, 6, 8)),
            (3_111_281, (8519, 5, 26)),
            (1_503_807, (4118, 4, 14)),
            (3_335_284, (9132, 9, 12)),
            (2_895_944, (7929, 10, 29)),
        ];
        for (n, (y, m, d)) in cases {
            assert_eq!(
                DateTime::from_ordinal(n),
                Some(DateTime::date(y, m, d)),
                "{n}"
            );
            assert_eq!(DateTime::date(y, m, d).toordinal(), n);
        }
        assert_eq!(DateTime::from_ordinal(0), None);
        // (a second before: within the minute, the hour, the day, the year, and none before 1)
        let at = |y, mo, d, h, mi, s| DateTime {
            hour: h,
            minute: mi,
            second: s,
            microsecond: 250,
            ..DateTime::date(y, mo, d)
        };
        assert_eq!(
            at(2024, 3, 1, 0, 0, 0).second_before(),
            Some(at(2024, 2, 29, 23, 59, 59))
        );
        assert_eq!(
            at(2025, 1, 1, 0, 0, 0).second_before(),
            Some(at(2024, 12, 31, 23, 59, 59))
        );
        assert_eq!(
            at(2025, 1, 1, 7, 0, 0).second_before(),
            Some(at(2025, 1, 1, 6, 59, 59))
        );
        assert_eq!(
            at(2025, 1, 1, 7, 3, 0).second_before(),
            Some(at(2025, 1, 1, 7, 2, 59))
        );
        assert_eq!(
            at(2025, 1, 1, 7, 3, 9).second_before(),
            Some(at(2025, 1, 1, 7, 3, 8))
        );
        assert_eq!(at(1, 1, 1, 0, 0, 0).second_before(), None);
        assert_eq!(DateTime::from_ordinal(3_652_060), None);
    }
}
