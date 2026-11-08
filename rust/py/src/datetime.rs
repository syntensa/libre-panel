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
