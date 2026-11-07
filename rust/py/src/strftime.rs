//! What Python's datetime.strftime writes for one code of a format, as i18n.format_date passes
//! them one at a time (`%[-#_0^]?[A-Za-z%]`), for a naive datetime in the C locale. %f, %z and
//! %Z are Python's own; the rest is the C library's, and the two differ: glibc (Linux) takes
//! the flags `-` (no padding), `_` (spaces), `0` (zeros), `^` (capitals) and `#` (the other
//! case) and writes a code it does not know as it stands; the Universal CRT (Windows) takes
//! `#` alone, and for anything else Python raises ValueError("Invalid format string"), as it
//! does for %y and %#y before the year 1900 there, before the CRT is asked.
//!
//! Not written here yet: the years before 1000 (Python's own answer for them differs between
//! its releases: some pad the year to four digits on Linux), %s (the seconds since 1970 in the
//! computer's time zone) and, on Windows, %#z and %#Z (the computer's zone).

use crate::datetime::DateTime;

/// Why a code is not written: Python raises ValueError with this message, or Rust does not
/// write it yet.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Refused {
    ValueError(String),
    Unported(&'static str),
}

pub type Result<T> = std::result::Result<T, Refused>;

/// The C locale's names of the days (Sunday first) and the months.
pub const DAYS: [&str; 7] = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
];
pub const MONTHS: [&str; 12] = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
];

fn short(name: &str) -> &str {
    &name[..3]
}

/// datetime.strftime(token) for one code: `%`, maybe a flag, a letter or `%`.
pub fn strftime(token: &str, at: &DateTime) -> Result<String> {
    // (_datetime's wrap_strftime: these three of a naive datetime are Python's own)
    match token {
        "%f" => return Ok(format!("{:06}", at.microsecond)),
        "%z" | "%Z" => return Ok(String::new()),
        _ => {}
    }
    let chars: Vec<char> = token.chars().collect();
    match chars.as_slice() {
        ['%', code] => c_library(None, *code, token, at),
        ['%', flag, code] => c_library(Some(*flag), *code, token, at),
        _ => Ok(token.to_string()),
    }
}

/// Years before 1000 are not written here yet (see the module's note).
fn four_digits(year: i64) -> Result<String> {
    if year < 1000 {
        return Err(Refused::Unported("years before 1000 in a date"));
    }
    Ok(year.to_string())
}

fn hour12(at: &DateTime) -> u32 {
    match at.hour % 12 {
        0 => 12,
        h => h,
    }
}

fn ampm(at: &DateTime) -> &'static str {
    if at.hour < 12 {
        "AM"
    } else {
        "PM"
    }
}

/// %U: the week of the year, the first Sunday starting week 1.
fn week_from_sunday(at: &DateTime) -> u32 {
    (at.tm_yday() + 7 - at.tm_wday()) / 7
}

/// %W: the week of the year, the first Monday starting week 1.
fn week_from_monday(at: &DateTime) -> u32 {
    (at.tm_yday() + 7 - (at.tm_wday() + 6) % 7) / 7
}

/// `value` as `digits` digits at least, filled with `fill` before it (none: as it is).
fn padded(value: i64, digits: usize, fill: Option<char>) -> String {
    let text = value.to_string();
    match fill {
        Some(c) if text.len() < digits => {
            let mut out: String = std::iter::repeat_n(c, digits - text.len()).collect();
            out.push_str(&text);
            out
        }
        _ => text,
    }
}

/// glibc's strftime (time/strftime_l.c) in the C locale, for one code.
#[cfg(not(windows))]
fn c_library(flag: Option<char>, code: char, token: &str, at: &DateTime) -> Result<String> {
    let pad = flag.filter(|f| matches!(f, '-' | '_' | '0'));
    let mut upper = flag == Some('^');
    let change_case = flag == Some('#');
    let mut lower = false;
    // DO_NUMBER, DO_NUMBER_SPACEPAD: zeros (or spaces) to `digits`, unless the flag says so
    let number = |value: i64, digits: usize, spaces: bool| {
        let fill = match pad {
            Some('-') => None,
            Some('_') => Some(' '),
            Some('0') => Some('0'),
            _ => Some(if spaces { ' ' } else { '0' }),
        };
        padded(value, digits, fill)
    };
    let two = |value: u32| format!("{value:02}");
    let (wday, month) = (at.tm_wday() as usize, at.month as usize - 1);
    let (iso_year, iso_week, _) = at.isocalendar();
    let text = match code {
        '%' => "%".to_string(),
        'a' | 'A' | 'b' | 'h' | 'B' => {
            if change_case {
                upper = true;
            }
            match code {
                'a' => short(DAYS[wday]),
                'A' => DAYS[wday],
                'B' => MONTHS[month],
                _ => short(MONTHS[month]),
            }
            .to_string()
        }
        // (the subformats are written without the flags, then put in capitals for `^`)
        'c' => format!(
            "{} {} {:>2} {}:{}:{} {}",
            short(DAYS[wday]),
            short(MONTHS[month]),
            at.day,
            two(at.hour),
            two(at.minute),
            two(at.second),
            four_digits(at.year)?
        ),
        'D' | 'x' => format!(
            "{}/{}/{}",
            two(at.month),
            two(at.day),
            two((at.year % 100) as u32)
        ),
        'F' => format!(
            "{}-{}-{}",
            four_digits(at.year)?,
            two(at.month),
            two(at.day)
        ),
        'r' => format!(
            "{}:{}:{} {}",
            two(hour12(at)),
            two(at.minute),
            two(at.second),
            ampm(at)
        ),
        'R' => format!("{}:{}", two(at.hour), two(at.minute)),
        'T' | 'X' => format!("{}:{}:{}", two(at.hour), two(at.minute), two(at.second)),
        'C' => {
            four_digits(at.year)?;
            (at.year / 100).to_string()
        }
        'Y' => four_digits(at.year)?,
        'G' => four_digits(iso_year)?,
        'g' => number(iso_year.rem_euclid(100), 2, false),
        'y' => number(at.year.rem_euclid(100), 2, false),
        'd' => number(i64::from(at.day), 2, false),
        'e' => number(i64::from(at.day), 2, true),
        'H' => number(i64::from(at.hour), 2, false),
        'k' => number(i64::from(at.hour), 2, true),
        'I' => number(i64::from(hour12(at)), 2, false),
        'l' => number(i64::from(hour12(at)), 2, true),
        'j' => number(i64::from(at.tm_yday()) + 1, 3, false),
        'm' => number(i64::from(at.month), 2, false),
        'M' => number(i64::from(at.minute), 2, false),
        'S' => number(i64::from(at.second), 2, false),
        'u' => number(i64::from(at.weekday()) + 1, 1, false),
        'w' => number(i64::from(at.tm_wday()), 1, false),
        'U' => number(i64::from(week_from_sunday(at)), 2, false),
        'W' => number(i64::from(week_from_monday(at)), 2, false),
        'V' => number(i64::from(iso_week), 2, false),
        'n' => "\n".to_string(),
        't' => "\t".to_string(),
        'P' | 'p' => {
            if code == 'P' || change_case {
                lower = true;
            }
            ampm(at).to_string()
        }
        // (the time zone of a naive datetime: tm_isdst is -1, tm_zone none)
        'z' | 'Z' => String::new(),
        's' => {
            return Err(Refused::Unported(
                "%s (the seconds since 1970 in the computer's zone)",
            ))
        }
        // a code glibc does not know (E and O at the end among them): as it stands
        _ => token.to_string(),
    };
    Ok(if lower {
        text.to_ascii_lowercase()
    } else if upper {
        text.to_ascii_uppercase()
    } else {
        text
    })
}

/// The Universal CRT's strftime (time/wcsftime.cpp) in the C locale, for one code: `#` alone
/// among the flags (no padding, or the long date); E and O passed over.
#[cfg(windows)]
fn c_library(flag: Option<char>, code: char, _token: &str, at: &DateTime) -> Result<String> {
    // (timemodule.c's time_strftime1 looks at the format first: %y, and %#y, before 1900)
    if code == 'y' && matches!(flag, None | Some('#')) && at.year < 1900 {
        return Err(Refused::ValueError(
            "format %y requires year >= 1900 on Windows".into(),
        ));
    }
    let invalid = || Refused::ValueError("Invalid format string".into());
    let alternate = match flag {
        None => false,
        Some('#') => true,
        Some(_) => return Err(invalid()),
    };
    expand(code, alternate, at)?.ok_or_else(invalid)
}

/// expand_time: one code (None: one the CRT does not know).
#[cfg(windows)]
fn expand(code: char, alternate: bool, at: &DateTime) -> Result<Option<String>> {
    // store_number: zeros to `digits`, none with `#`
    let number = |value: i64, digits: usize| padded(value, digits, (!alternate).then_some('0'));
    let (wday, month) = (at.tm_wday() as usize, at.month as usize - 1);
    let (iso_year, iso_week, _) = at.isocalendar();
    let part = |code: char| -> Result<String> {
        Ok(expand(code, alternate, at)?.expect("a code the CRT knows"))
    };
    let text = match code {
        '%' => "%".to_string(),
        'a' => short(DAYS[wday]).to_string(),
        'A' => DAYS[wday].to_string(),
        'b' | 'h' => short(MONTHS[month]).to_string(),
        'B' => MONTHS[month].to_string(),
        // the C locale's: the C standard's form; with `#` the long date and the time
        'c' if !alternate => {
            four_digits(at.year)?;
            let e = padded(i64::from(at.day), 2, Some(' '));
            let t = format!("{:02}:{:02}:{:02}", at.hour, at.minute, at.second);
            format!(
                "{} {} {e} {t} {}",
                short(DAYS[wday]),
                short(MONTHS[month]),
                at.year
            )
        }
        'c' => format!("{} {}", long_date(at)?, time_of(at)),
        'x' if alternate => long_date(at)?,
        'x' => format!("{:02}/{:02}/{:02}", at.month, at.day, at.year % 100),
        'X' => time_of(at),
        'C' => {
            four_digits(at.year)?;
            number(at.year / 100, 2)
        }
        'Y' => {
            four_digits(at.year)?;
            number(at.year, 4)
        }
        'y' => number(at.year.rem_euclid(100), 2),
        'G' => four_digits(iso_year)?,
        'g' => padded(iso_year.rem_euclid(100), 2, Some('0')),
        'd' => number(i64::from(at.day), 2),
        'e' => padded(i64::from(at.day), 2, (!alternate).then_some(' ')),
        'H' => number(i64::from(at.hour), 2),
        'I' => number(i64::from(hour12(at)), 2),
        'j' => number(i64::from(at.tm_yday()) + 1, 3),
        'm' => number(i64::from(at.month), 2),
        'M' => number(i64::from(at.minute), 2),
        'S' => number(i64::from(at.second), 2),
        'U' => number(i64::from(week_from_sunday(at)), 2),
        'W' => number(i64::from(week_from_monday(at)), 2),
        'V' => number(i64::from(iso_week), 2),
        'u' => (at.weekday() + 1).to_string(),
        'w' => at.tm_wday().to_string(),
        'p' => ampm(at).to_string(),
        'n' => "\n".to_string(),
        't' => "\t".to_string(),
        'D' => format!("{}/{}/{}", part('m')?, part('d')?, part('y')?),
        'F' => format!("{}-{}-{}", part('Y')?, part('m')?, part('d')?),
        'R' => format!("{}:{}", part('H')?, part('M')?),
        'T' => format!("{}:{}:{}", part('H')?, part('M')?, part('S')?),
        'r' => format!("{}:{}:{} {}", part('I')?, part('M')?, part('S')?, ampm(at)),
        // (the computer's zone: tm_isdst is -1, which the CRT takes for summer time)
        'z' | 'Z' => return Err(Refused::Unported("%#z and %#Z (the computer's zone)")),
        _ => return Ok(None),
    };
    Ok(Some(text))
}

/// The C locale's long date, "dddd, MMMM dd, yyyy".
#[cfg(windows)]
fn long_date(at: &DateTime) -> Result<String> {
    four_digits(at.year)?;
    let (wday, month) = (at.tm_wday() as usize, at.month as usize - 1);
    Ok(format!(
        "{}, {} {:02}, {:04}",
        DAYS[wday], MONTHS[month], at.day, at.year
    ))
}

/// The C locale's time, "HH:mm:ss".
#[cfg(windows)]
fn time_of(at: &DateTime) -> String {
    format!("{:02}:{:02}:{:02}", at.hour, at.minute, at.second)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(year: i64, month: u32, day: u32, hour: u32, minute: u32, second: u32) -> DateTime {
        DateTime {
            year,
            month,
            day,
            hour,
            minute,
            second,
            microsecond: 250,
        }
    }

    #[test]
    fn python_s_own_codes() {
        let t = at(2026, 11, 7, 18, 5, 9);
        assert_eq!(strftime("%f", &t), Ok("000250".into()));
        assert_eq!(strftime("%z", &t), Ok(String::new()));
        assert_eq!(strftime("%Z", &t), Ok(String::new()));
        assert_eq!(strftime("%%", &t), Ok("%".into()));
    }

    #[cfg(windows)]
    #[test]
    fn python_s_refusals_on_windows() {
        // (timemodule.c's time_strftime1: %y and %#y before 1900, before the CRT is asked)
        let old = at(1899, 12, 31, 23, 5, 9);
        let refused = |msg: &str| Err(Refused::ValueError(msg.into()));
        assert_eq!(
            strftime("%y", &old),
            refused("format %y requires year >= 1900 on Windows")
        );
        assert_eq!(
            strftime("%#y", &old),
            refused("format %y requires year >= 1900 on Windows")
        );
        assert_eq!(strftime("%-y", &old), refused("Invalid format string"));
        assert_eq!(strftime("%q", &old), refused("Invalid format string"));
    }

    #[cfg(not(windows))]
    #[test]
    fn glibc_s_codes_as_python_3_12_on_linux_writes_them() {
        // (datetime(2026, 11, 7, 18, 5, 9, 250).strftime(code) on glibc 2.39)
        let t = at(2026, 11, 7, 18, 5, 9);
        for (code, want) in [
            ("%c", "Sat Nov  7 18:05:09 2026"),
            ("%^c", "SAT NOV  7 18:05:09 2026"),
            ("%#a", "SAT"),
            ("%#p", "pm"),
            ("%^P", "pm"),
            ("%-d", "7"),
            ("%_m", "11"),
            ("%0e", "07"),
            ("%e", " 7"),
            ("%k", "18"),
            ("%l", " 6"),
            ("%j", "311"),
            ("%U", "44"),
            ("%W", "44"),
            ("%V", "45"),
            ("%G", "2026"),
            ("%-F", "2026-11-07"),
            ("%^f", "%^F"),
            ("%-f", "%-f"),
            ("%q", "%q"),
            ("%E", "%E"),
            ("%#Z", ""),
            ("%-z", ""),
        ] {
            assert_eq!(strftime(code, &t), Ok(want.to_string()), "{code}");
        }
        assert!(matches!(strftime("%s", &t), Err(Refused::Unported(_))));
        assert!(matches!(
            strftime("%Y", &at(999, 1, 1, 0, 0, 0)),
            Err(Refused::Unported(_))
        ));
    }
}
