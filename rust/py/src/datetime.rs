//! What Python's datetime knows of a day and a time without a zone (a naive datetime): its
//! day's number from 1 January of the year 1, its weekday, its day of the year and the ISO
//! week it is in.

/// A naive datetime, as Python's has it: the year 1 to 9999, a month, a day of it, the time to
/// the microsecond. Ordered as time goes.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord, Hash)]
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
            ..DateTime::default()
        }
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
}
