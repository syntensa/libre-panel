//! Time zones as Python meets them: a zone's file read as zoneinfo reads it (a TZif file, its
//! transitions and, past the last of them, its footer's POSIX rule), the computer's own zone as
//! the C library (glibc) finds it (TZ, else /etc/localtime; a POSIX TZ string itself where TZ is
//! one), and on them what datetime asks: a naive time's timestamp (CPython's local_to_seconds,
//! by the C library's localtime), the computer's offset at a moment, a naive time put into
//! another zone (astimezone: through the computer's zone, then zoneinfo's fromutc), the local
//! time of a timestamp (fromtimestamp, its fold where the clocks go back).
//!
//! Python reads a zone's file with zoneinfo's own reader, the C library the computer's zone with
//! its own, and each takes the file's footer by its own rules: Rust reads a file and a TZ string
//! as both do where both read it alike, and where the C library would read the computer's zone
//! otherwise than Rust knows (leap seconds, a TZ string of summer time without its rules, one
//! glibc reads by its leniencies), says it does not know it.
//!
//! On Windows Python takes the computer's zone from the system (the C runtime's localtime);
//! Rust does not know it there yet: [`local`] is None.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use crate::datetime::DateTime;

/// date(1970, 1, 1).toordinal(): the epoch's day.
const EPOCH_ORDINAL: i64 = 719_163;
/// CPython's max_fold_seconds: how far apart two moments of one local time may be.
const MAX_FOLD_SECONDS: i64 = 24 * 3600;
/// The naive seconds (from 1970) of datetime's first moment, 0001-01-01 00:00, and of the
/// moment after its last, 10000-01-01 00:00.
const FIRST_SECOND: i64 = (1 - EPOCH_ORDINAL) * 86400;
const END_SECOND: i64 = (3_652_060 - EPOCH_ORDINAL) * 86400;

/// A local time type: its offset from UTC (seconds east) and whether it is summer time.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Type {
    pub offset: i64,
    pub dst: bool,
}

/// The day of a rule's change in its year.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Day {
    /// Mm.w.d: the w-th day d (0 Sunday) of month m (w 5: the last)
    Month { m: i64, w: i64, d: i64 },
    /// Jn: the n-th day, 1 to 365, February 29 never counted
    Julian(i64),
    /// n: the n-th day, 0 to 365, February 29 counted
    Zero(i64),
}

/// A change of a POSIX rule: its day, and the local time of it (seconds, -167 to 167 hours).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Change {
    day: Day,
    time: i64,
}

/// A POSIX TZ rule (std offset dst [offset],start[/time],end[/time]): standard time, and
/// where there is summer time, its offset and when it starts and ends.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rule {
    std: Type,
    summer: Option<(Type, Change, Change)>,
}

/// A zone: its transitions (UTC seconds) and the type after each, its types, and its rule
/// past the last transition (where its file has one).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Zone {
    transitions: Vec<i64>,
    after: Vec<usize>,
    types: Vec<Type>,
    rule: Option<Rule>,
    /// whether the C library reads the zone as Rust does: a TZ string, or a file without leap
    /// seconds whose header, types and footer it takes as zoneinfo does
    glibc: bool,
}

// -- the days of a year ------------------------------------------------------------------------

/// glibc's __isleap (and Python's calendar.isleap): C's remainders, for any year.
fn is_leap(year: i64) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

/// The days from 1 January 1970 to 1 January of `year` (1 to 9999).
fn days_before_year(year: i64) -> i64 {
    DateTime::date(year, 1, 1).toordinal() - EPOCH_ORDINAL
}

/// The (proleptic Gregorian) year of the day `days` from 1970-01-01, for any day: the year
/// glibc's __offtime gives a moment.
fn year_of_day(days: i64) -> i64 {
    // (eras of 400 years from 0000-03-01)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    yoe + era * 400 + i64::from(mp >= 10)
}

/// The UTC year of the moment `ts` (seconds from 1970).
fn year_of(ts: i64) -> i64 {
    year_of_day(ts.div_euclid(86400))
}

/// The day of the month (1-based) of a Mm.w.d rule in `year` (zoneinfo's _CalendarOffset).
fn month_day(year: i64, m: i64, w: i64, d: i64) -> i64 {
    let first = DateTime::date(year, m as u32, 1);
    let first_wday = i64::from(first.tm_wday()); // (0 Sunday)
    let mut day = (d - first_wday).rem_euclid(7) + 1 + (w - 1) * 7;
    let days = i64::from(crate::datetime::days_in_month(year, m as u32));
    if day > days {
        day -= 7;
    }
    day
}

/// glibc's __mon_yday: the days of a year before each month, and its days (common, leap).
const MON_YDAY: [[i64; 13]; 2] = [
    [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365],
    [0, 31, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335, 366],
];

impl Change {
    /// zoneinfo's year_to_epoch: the local moment of the change in `year` (1 to 9999), seconds
    /// from 1970-01-01 local midnight (its days of a year as zoneinfo counts them: the n of a
    /// zero-based day one day before POSIX's).
    fn zoneinfo_local(&self, year: i64) -> i64 {
        let before = days_before_year(year) - 1;
        let days = match self.day {
            Day::Month { m, w, d } => {
                let first = DateTime::date(year, m as u32, 1).toordinal()
                    - DateTime::date(year, 1, 1).toordinal();
                before + first + month_day(year, m, w, d)
            }
            Day::Julian(n) => before + if n >= 59 && is_leap(year) { n + 1 } else { n },
            Day::Zero(n) => before + n,
        };
        days * 86400 + self.time
    }

    /// glibc's compute_change: the local moment of the change in `year` (any), as glibc counts
    /// it: from 1 January of the year after 1970, else from 1970's (for every earlier year),
    /// the month's first weekday by Zeller's congruence.
    fn glibc_local(&self, year: i64) -> i64 {
        let mut t = if year > 1970 {
            ((year - 1970) * 365 + ((year - 1) / 4 - 1970 / 4) - ((year - 1) / 100 - 1970 / 100)
                + ((year - 1) / 400 - 1970 / 400))
                * 86400
        } else {
            0
        };
        let leap = is_leap(year);
        match self.day {
            Day::Julian(d) => {
                t += (d - 1) * 86400;
                if d >= 60 && leap {
                    t += 86400;
                }
            }
            Day::Zero(d) => t += d * 86400,
            Day::Month { m, w, d } => {
                let myday = &MON_YDAY[usize::from(leap)];
                let m = m as usize;
                t += myday[m - 1] * 86400;
                let m1 = (m as i64 + 9) % 12 + 1;
                let yy0 = if m <= 2 { year - 1 } else { year };
                let (yy1, yy2) = (yy0 / 100, yy0 % 100);
                let mut dow = ((26 * m1 - 2) / 10 + 1 + yy2 + yy2 / 4 + yy1 / 4 - 2 * yy1) % 7;
                if dow < 0 {
                    dow += 7;
                }
                let mut day = d - dow;
                if day < 0 {
                    day += 7;
                }
                for _ in 1..w {
                    if day + 7 >= myday[m] - myday[m - 1] {
                        break;
                    }
                    day += 7;
                }
                t += day * 86400;
            }
        }
        t + self.time
    }
}

impl Rule {
    /// The type at the UTC moment `ts`, of the UTC year `year`: zoneinfo's
    /// find_tzrule_ttinfo_fromutc, or glibc's __tz_compute.
    fn at(&self, ts: i64, year: i64, glibc: bool) -> Type {
        let Some((dst, start, end)) = self.summer else {
            return self.std;
        };
        let local = |c: &Change| {
            if glibc {
                c.glibc_local(year)
            } else {
                c.zoneinfo_local(year)
            }
        };
        let (on, off) = (local(&start) - self.std.offset, local(&end) - dst.offset);
        let summer = if glibc {
            // (summer time ending in the next year where it starts later than it ends)
            if on > off {
                ts < off || ts >= on
            } else {
                on <= ts && ts < off
            }
        } else if on < off {
            on <= ts && ts < off
        } else {
            ts < off || ts >= on
        };
        if summer {
            dst
        } else {
            self.std
        }
    }
}

// -- reading ------------------------------------------------------------------------------------

fn be32(b: &[u8], at: usize) -> Option<i64> {
    Some(i64::from(i32::from_be_bytes(
        b.get(at..at + 4)?.try_into().ok()?,
    )))
}

fn be64(b: &[u8], at: usize) -> Option<i64> {
    Some(i64::from_be_bytes(b.get(at..at + 8)?.try_into().ok()?))
}

/// A TZif header at `at`, as _TZifHeader.from_file reads it: its version byte (NUL, or a digit
/// int() reads) and its six counts (isut, isstd, leap, time, type, char).
fn header(data: &[u8], at: usize) -> Option<(u8, [usize; 6])> {
    if data.get(at..at + 4)? != b"TZif" {
        return None;
    }
    let version = *data.get(at + 4)?;
    if version != 0 && !version.is_ascii_digit() {
        return None;
    }
    let mut counts = [0usize; 6];
    for (i, c) in counts.iter_mut().enumerate() {
        *c = usize::try_from(be32(data, at + 20 + 4 * i)?).ok()?;
    }
    Some((version, counts))
}

/// load_data's get_abbr: whether the name of a type from its index (a signed byte, as
/// Python's find and slices take one) to the next NUL decodes as UTF-8 (Python raises where
/// it does not).
fn abbreviation_decodes(chars: &[u8], index: i8) -> bool {
    let len = chars.len() as i64;
    let at = |i: i64| if i < 0 { (len + i).max(0) } else { i.min(len) };
    let start = at(i64::from(index));
    let end = if i64::from(index) > len {
        -1
    } else {
        chars[start as usize..]
            .iter()
            .position(|&b| b == 0)
            .map_or(-1, |p| start + p as i64)
    };
    let end = at(end);
    end <= start || std::str::from_utf8(&chars[start as usize..end as usize]).is_ok()
}

/// From `least` to `most` digits at the start of `s`, as a number, and the rest.
fn digits(s: &str, least: usize, most: usize) -> Option<(i64, &str)> {
    let n = s.bytes().take(most).take_while(u8::is_ascii_digit).count();
    (n >= least).then(|| (s[..n].parse::<i64>().unwrap_or(0), &s[n..]))
}

/// A POSIX time, [+-]h[h][:mm[:ss]]: an offset (hours to 24, its minutes and seconds to 59) or
/// a rule's time (three digits of hours, to 167): its seconds, and the rest.
fn posix_time(s: &str, offset: bool) -> Option<(i64, &str)> {
    let (sign, rest) = match s.as_bytes().first()? {
        b'+' => (1, &s[1..]),
        b'-' => (-1, &s[1..]),
        _ => (1, s),
    };
    let (h, mut rest) = digits(rest, 1, if offset { 2 } else { 3 })?;
    if h > if offset { 24 } else { 167 } {
        return None;
    }
    let mut parts = [0i64; 2];
    for part in &mut parts {
        let Some(after) = rest.strip_prefix(':') else {
            break;
        };
        let (v, r) = digits(after, 2, 2)?;
        if offset && v > 59 {
            return None;
        }
        (*part, rest) = (v, r);
    }
    Some((sign * (h * 3600 + parts[0] * 60 + parts[1]), rest))
}

/// A zone's name in a POSIX TZ string: three letters or more, or <...> quoted.
fn posix_name(s: &str) -> Option<&str> {
    if let Some(rest) = s.strip_prefix('<') {
        let end = rest.find('>')?;
        let name = &rest[..end];
        let ok = name.len() >= 3
            && name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'+' || b == b'-');
        return ok.then(|| &rest[end + 1..]);
    }
    let n = s.bytes().take_while(u8::is_ascii_alphabetic).count();
    (n >= 3).then(|| &s[n..])
}

/// A rule's change: Mm.w.d, Jn or n, and its /time (2:00 where none is given).
fn posix_change(s: &str) -> Option<Change> {
    let (date, time) = match s.split_once('/') {
        Some((d, t)) => (d, Some(t)),
        None => (s, None),
    };
    let day = if let Some(rest) = date.strip_prefix('M') {
        let (m, rest) = digits(rest, 1, 2)?;
        let (w, rest) = digits(rest.strip_prefix('.')?, 1, 1)?;
        let (d, rest) = digits(rest.strip_prefix('.')?, 1, 1)?;
        if !rest.is_empty() || !(1..=12).contains(&m) || !(1..=5).contains(&w) || d > 6 {
            return None;
        }
        Day::Month { m, w, d }
    } else {
        let (julian, n) = match date.strip_prefix('J') {
            Some(n) => (true, n),
            None => (false, date),
        };
        let (n, rest) = digits(n, 1, 3)?;
        if !rest.is_empty() || n > 365 || (julian && n == 0) {
            return None;
        }
        if julian {
            Day::Julian(n)
        } else {
            Day::Zero(n)
        }
    };
    let time = match time {
        Some(t) => {
            let (v, rest) = posix_time(t, false)?;
            if !rest.is_empty() {
                return None;
            }
            v
        }
        None => 2 * 3600,
    };
    Some(Change { day, time })
}

/// A POSIX TZ string (std offset [dst [offset] ,start[/time],end[/time]]) as both zoneinfo and
/// glibc read it alike: None for any other (one naming summer time without its rules: glibc
/// would take them from elsewhere).
fn posix_rule(s: &str) -> Option<Rule> {
    let rest = posix_name(s)?;
    // (POSIX's offsets are west of UTC: "CET-1" one hour east)
    let (std, rest) = posix_time(rest, true)?;
    let std = Type {
        offset: -std,
        dst: false,
    };
    if rest.is_empty() {
        return Some(Rule { std, summer: None });
    }
    let after = posix_name(rest)?;
    let (dst, after) = if after.starts_with(',') {
        (std.offset + 3600, after)
    } else {
        let (v, r) = posix_time(after, true)?;
        (-v, r)
    };
    let (start, end) = after.strip_prefix(',')?.split_once(',')?;
    Some(Rule {
        std,
        summer: Some((
            Type {
                offset: dst,
                dst: true,
            },
            posix_change(start)?,
            posix_change(end)?,
        )),
    })
}

impl Zone {
    /// A TZif file as zoneinfo's load_data reads it (a version but 1: the 64-bit data after the
    /// first; from version 2 the footer's TZ string), and whether glibc reads it alike.
    pub fn from_tzif(data: &[u8]) -> Option<Zone> {
        let (first, mut counts) = header(data, 0)?;
        // (glibc: no more stamps of standard or UT time than types, in each header)
        let fits = |c: &[usize; 6]| c[0] <= c[4] && c[1] <= c[4];
        let mut glibc = fits(&counts) && (first == 0 || first >= b'2');
        let (mut at, mut size, mut version) = (44, 4, first);
        // (_TZifHeader: NUL is version 1, as b"1" is; int(b"0") is 0, read as 2 is)
        if first != 0 && first != b'1' {
            let [isut, isstd, leap, time, typ, chars] = counts;
            at += time * 5 + typ * 6 + chars + leap * 8 + isstd + isut;
            (version, counts) = header(data, at)?;
            glibc &= fits(&counts) && version >= b'2';
            at += 44;
            size = 8;
        }
        let [isut, isstd, leap, time, typ, chars] = counts;
        let mut transitions = Vec::with_capacity(time);
        for i in 0..time {
            transitions.push(if size == 8 {
                be64(data, at + 8 * i)?
            } else {
                be32(data, at + 4 * i)?
            });
        }
        at += time * size;
        let after: Vec<usize> = data
            .get(at..at + time)?
            .iter()
            .map(|&i| usize::from(i))
            .collect();
        at += time;
        let names = data.get(at + typ * 6..at + typ * 6 + chars)?;
        let mut types = Vec::with_capacity(typ);
        for i in 0..typ {
            let p = at + 6 * i;
            let (dst, index) = (*data.get(p + 4)?, *data.get(p + 5)?);
            if !abbreviation_decodes(names, index as i8) {
                return None;
            }
            // (glibc: a summer flag of 0 or 1, a name's index within the names)
            glibc &= dst <= 1 && usize::from(index) <= chars;
            types.push(Type {
                offset: be32(data, p)?,
                dst: dst != 0,
            });
        }
        at += typ * 6 + chars;
        if after.iter().any(|&i| i >= types.len()) {
            return None;
        }
        // (glibc corrects its times by the leap seconds; zoneinfo does not)
        glibc &= leap == 0 && !types.is_empty();
        let mut rule = None;
        if version >= b'2' {
            // (load_data: the byte before the footer (a newline), then up to the next newline;
            // glibc's footer is the rest of the file, all but its last byte)
            at += isut + isstd + leap * 12;
            let rest = data.get(at + 1..)?;
            let end = rest.iter().position(|&b| b == b'\n')?;
            glibc &= data[at] == b'\n' && at + 1 + end + 1 == data.len();
            let footer = &rest[..end];
            if !footer.is_empty() {
                rule = Some(posix_rule(std::str::from_utf8(footer).ok()?)?);
            }
        }
        if types.is_empty() && rule.is_none() {
            return None;
        }
        Some(Zone {
            transitions,
            after,
            types,
            rule,
            glibc,
        })
    }

    /// A zone of a POSIX TZ string alone (TZ="CET-1CEST,M3.5.0,M10.5.0/3").
    pub fn from_posix(spec: &str) -> Option<Zone> {
        Some(Zone {
            transitions: Vec::new(),
            after: Vec::new(),
            types: Vec::new(),
            rule: Some(posix_rule(spec)?),
            glibc: true,
        })
    }

    /// UTC: no offset, ever.
    pub fn utc() -> Zone {
        Zone::from_posix("UTC0").expect("UTC0")
    }

    /// The first type of standard time (else the first): what both take before the first
    /// transition (glibc's, zoneinfo's ttinfo_before).
    fn first_standard(&self) -> Option<Type> {
        self.types
            .iter()
            .find(|t| !t.dst)
            .or(self.types.first())
            .copied()
    }

    /// zoneinfo's fromutc: the offset of the zone at the UTC moment `ts` (seconds), the UTC
    /// year `year` its rule's.
    pub fn fromutc(&self, ts: i64, year: i64) -> i64 {
        let n = self.transitions.len();
        if n >= 1 && ts < self.transitions[0] {
            return self.first_standard().map_or(0, |t| t.offset);
        }
        if n == 0 || ts > self.transitions[n - 1] {
            if let Some(rule) = self.rule {
                return rule.at(ts, year, false).offset;
            }
        }
        if n == 0 {
            // (a file of types and no transitions nor rule: zoneinfo's last type)
            return self.types.last().map_or(0, |t| t.offset);
        }
        let idx = self.transitions.partition_point(|&t| t <= ts);
        self.types[self.after[idx - 1]].offset
    }

    /// glibc's localtime: the offset of the zone at the UTC moment `ts` (seconds).
    pub fn localtime(&self, ts: i64) -> i64 {
        let n = self.transitions.len();
        if self.types.is_empty() {
            // (a TZ string alone)
            return self.rule.map_or(0, |r| r.at(ts, year_of(ts), true).offset);
        }
        if n == 0 || ts < self.transitions[0] {
            return self.first_standard().map_or(0, |t| t.offset);
        }
        if ts >= self.transitions[n - 1] {
            if let Some(rule) = self.rule {
                return rule.at(ts, year_of(ts), true).offset;
            }
            return self.types[self.after[n - 1]].offset;
        }
        let idx = self.transitions.partition_point(|&t| t <= ts);
        self.types[self.after[idx - 1]].offset
    }
}

// -- the computer's zone ------------------------------------------------------------------------

/// Where glibc looks for a zone's file of a relative name: TZDIR, else /usr/share/zoneinfo.
fn tzdir() -> PathBuf {
    std::env::var_os("TZDIR")
        .filter(|v| !v.is_empty())
        .map_or_else(|| PathBuf::from("/usr/share/zoneinfo"), PathBuf::from)
}

/// A zone's file for the computer's zone: None where there is no TZif file to read (glibc
/// goes on to read the name as a TZ string), Some(None) where there is one that glibc reads
/// otherwise than Rust knows.
fn local_file(path: &Path) -> Option<Option<Zone>> {
    let data = std::fs::read(path).ok()?;
    if !data.starts_with(b"TZif") {
        return None;
    }
    Some(Zone::from_tzif(&data).filter(|zone| zone.glibc))
}

/// The computer's zone as glibc's tzset finds it for a TZ of `tz` (None: TZ unset): the empty
/// TZ UTC; a file of its name (a leading colon dropped; a relative name in TZDIR), else the TZ
/// string it is; unset, /etc/localtime; UTC where none of these is read. None where glibc would
/// take more than Rust knows (a file of leap seconds, a TZ string naming summer time without its
/// rules, or one glibc reads by its leniencies).
pub fn local_of(tz: Option<&str>) -> Option<Zone> {
    let Some(tz) = tz else {
        return local_file(Path::new("/etc/localtime")).unwrap_or_else(|| Some(Zone::utc()));
    };
    let tz = if tz.is_empty() { "Universal" } else { tz };
    let tz = tz.strip_prefix(':').unwrap_or(tz);
    if tz.is_empty() {
        return Some(Zone::utc());
    }
    let path = if tz.starts_with('/') {
        PathBuf::from(tz)
    } else {
        tzdir().join(tz)
    };
    if let Some(zone) = local_file(&path) {
        return zone;
    }
    if let Some(zone) = Zone::from_posix(tz) {
        return Some(zone);
    }
    // (__tzset_parse_tz: unnamed UTC unless the string begins with a name and an offset;
    // where parse_offset's sscanf may read one (after a sign, white space and a sign of its
    // own), glibc reads on by rules Rust does not take)
    let Some(rest) = posix_name(tz) else {
        return Some(Zone::utc());
    };
    let may = match rest.as_bytes().first() {
        Some(b'0'..=b'9') => true,
        Some(b'+' | b'-') => {
            let r = rest[1..].trim_start_matches([' ', '\t', '\n', '\x0b', '\x0c', '\r']);
            let r = r.strip_prefix(['+', '-']).unwrap_or(r);
            r.starts_with(|c: char| c.is_ascii_digit())
        }
        _ => false,
    };
    (!may).then(Zone::utc)
}

/// A zone of a key as zoneinfo finds its file (the search path, else the tzdata package) and
/// reads it; None where there is none, or Python could not read it (timezones.zone's None).
/// Each key's file is read once.
pub fn named(key: &str) -> Option<Zone> {
    static READ: OnceLock<Mutex<HashMap<String, Option<Zone>>>> = OnceLock::new();
    let read = READ.get_or_init(Mutex::default);
    if let Some(zone) = read.lock().ok()?.get(key) {
        return zone.clone();
    }
    let zone = (|| {
        if key.is_empty() || !crate::zoneinfo::valid_key(key) {
            return None;
        }
        let path =
            crate::zoneinfo::find_tzfile(key).or_else(|| crate::zoneinfo::tzdata_file(key))?;
        Zone::from_tzif(&std::fs::read(path).ok()?)
    })();
    read.lock().ok()?.insert(key.to_string(), zone.clone());
    zone
}

/// The computer's zone, as the C library finds it (once): None on Windows, or where Rust does
/// not know it.
pub fn local() -> Option<&'static Zone> {
    static ZONE: OnceLock<Option<Zone>> = OnceLock::new();
    ZONE.get_or_init(|| {
        if cfg!(windows) {
            return None;
        }
        let tz = std::env::var("TZ").ok();
        local_of(tz.as_deref())
    })
    .as_ref()
}

// -- what datetime asks -------------------------------------------------------------------------

/// The seconds of a naive time from 1970-01-01 00:00, as if it were UTC.
fn naive_seconds(dt: &DateTime) -> i64 {
    (dt.toordinal() - EPOCH_ORDINAL) * 86400
        + i64::from(dt.hour) * 3600
        + i64::from(dt.minute) * 60
        + i64::from(dt.second)
}

/// The naive time of the seconds `t` from 1970 (and a microsecond); None outside datetime's
/// years.
fn naive_of(t: i64, microsecond: u32) -> Option<DateTime> {
    let day = DateTime::from_ordinal(t.div_euclid(86400) + EPOCH_ORDINAL)?;
    let secs = t.rem_euclid(86400);
    Some(DateTime {
        hour: (secs / 3600) as u32,
        minute: (secs / 60 % 60) as u32,
        second: (secs % 60) as u32,
        microsecond,
        ..day
    })
}

/// Whether the naive seconds `t` fall in datetime's years (1 to 9999).
fn in_years(t: i64) -> bool {
    (FIRST_SECOND..END_SECOND).contains(&t)
}

/// CPython's local(u): the local time of the UTC moment `u` as naive seconds; None where its
/// year is not datetime's (Python raises).
fn local_seconds(zone: &Zone, u: i64) -> Option<i64> {
    Some(u.checked_add(zone.localtime(u))?).filter(|&t| in_years(t))
}

/// CPython's local_to_seconds (seconds from 1970): the UTC moment of a naive local time in
/// `zone`; where the clocks go back the earlier of the two (fold 0) or the later (fold 1),
/// past the gap where they go on the later (fold 0) or the earlier. None where Python raises.
pub fn local_to_seconds(zone: &Zone, dt: &DateTime, fold: bool) -> Option<i64> {
    let local = |u: i64| local_seconds(zone, u);
    let t = naive_seconds(dt);
    let a = local(t)? - t;
    let u1 = t - a;
    let t1 = local(u1)?;
    let b = if t1 == t {
        // (a solution: is there another the fold asks for?)
        let u2 = if fold {
            u1 + MAX_FOLD_SECONDS
        } else {
            u1 - MAX_FOLD_SECONDS
        };
        let c = local(u2)? - u2;
        if a == c {
            return Some(u1);
        }
        c
    } else {
        t1 - u1
    };
    let u2 = t - b;
    let t2 = local(u2)?;
    if t2 == t {
        return Some(u2);
    }
    if t1 == t {
        return Some(u1);
    }
    // (in the gap: neither is a solution)
    Some(if fold { u1.min(u2) } else { u1.max(u2) })
}

/// datetime.timestamp() of a naive local time in `zone` (of its fold).
pub fn timestamp(zone: &Zone, dt: &DateTime, fold: bool) -> Option<f64> {
    Some(local_to_seconds(zone, dt, fold)? as f64 + f64::from(dt.microsecond) / 1e6)
}

/// local_timezone_from_local: the computer's offset at a naive local time, as a timezone takes
/// it (strictly within a day; Python raises for any other): at the moment of its fold, both
/// folds' moments asked; where they part, the other's where it is the later for fold 1 or the
/// earlier for fold 0 (in the gap where the clocks go on, the offset of before it for fold 0).
fn own_offset(local: &Zone, dt: &DateTime, fold: bool) -> Option<i64> {
    let mut seconds = local_to_seconds(local, dt, fold)?;
    let other = local_to_seconds(local, dt, !fold)?;
    if other != seconds && (other > seconds) == fold {
        seconds = other;
    }
    let offset = local.localtime(seconds);
    (offset.abs() < 86400).then_some(offset)
}

/// dt.astimezone().utcoffset() of a naive local time (seconds): the time less the computer's
/// offset at it is UTC, and the offset there is the result's.
pub fn utcoffset(local: &Zone, dt: &DateTime, fold: bool) -> Option<i64> {
    let utc = naive_seconds(dt) - own_offset(local, dt, fold)?;
    let offset = local.localtime(utc);
    (in_years(utc) && offset.abs() < 86400 && in_years(utc + offset)).then_some(offset)
}

/// A naive local time (of the computer's `local` zone) as it is in `zone`, naive:
/// dt.astimezone(zone).replace(tzinfo=None): the time less its offset (at the moment
/// local_to_seconds finds; in the gap where the clocks go on, not that moment itself), then
/// zoneinfo's fromutc. None where Python raises.
pub fn astimezone(local: &Zone, zone: &Zone, dt: &DateTime, fold: bool) -> Option<DateTime> {
    let utc = naive_seconds(dt) - own_offset(local, dt, fold)?;
    if !in_years(utc) {
        return None;
    }
    let there = utc.checked_add(zone.fromutc(utc, year_of(utc)))?;
    if !in_years(there) {
        return None;
    }
    naive_of(there, dt.microsecond)
}

/// _PyTime_ObjectToTimeval of a float, rounded half to even: its whole seconds and
/// microseconds (0 to 999999). None where Python raises (not a number, out of time_t).
pub fn timeval(t: f64) -> Option<(i64, u32)> {
    if !t.is_finite() {
        return None;
    }
    let (mut whole, mut us) = (t.trunc(), ((t - t.trunc()) * 1e6).round_ties_even());
    if us >= 1e6 {
        us -= 1e6;
        whole += 1.0;
    } else if us < 0.0 {
        us += 1e6;
        whole -= 1.0;
    }
    (-9.2e18..9.2e18)
        .contains(&whole)
        .then_some((whole as i64, us as u32))
}

/// datetime.fromtimestamp(t): the computer's local time at the timestamp `t`, and its fold
/// (1 for the later of two moments of one local time). None where Python raises.
pub fn fromtimestamp(local: &Zone, t: f64) -> Option<(DateTime, bool)> {
    let (secs, us) = timeval(t)?;
    local_moment(local, secs, us)
}

/// datetime.now()'s offset from UTC, astimezone().utcoffset(), at this moment (seconds); None
/// where Python raises.
pub fn now_offset(local: &Zone) -> Option<i64> {
    let since = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?;
    let secs = i64::try_from(since.as_secs()).ok()?;
    // (datetime_best_possible: the clock's time, its microseconds floored)
    let (now, fold) = local_moment(local, secs, since.subsec_micros())?;
    utcoffset(local, &now, fold)
}

/// datetime_from_timet_and_us: the local time of whole seconds and microseconds, and its fold.
fn local_moment(local: &Zone, secs: i64, us: u32) -> Option<(DateTime, bool)> {
    let own = local_seconds(local, secs)?;
    // (datetime_from_timet_and_us: a moment a day before, and the change between)
    let probe = local_seconds(local, secs - MAX_FOLD_SECONDS)?;
    let transition = own - probe - MAX_FOLD_SECONDS;
    let fold = transition < 0 && local_seconds(local, secs + transition)? == own;
    Some((naive_of(own, us)?, fold))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn posix_rules_as_their_strings_say() {
        let cet = Zone::from_posix("CET-1CEST,M3.5.0,M10.5.0/3").unwrap();
        // (2026: summer time from 29 March 1:00 UTC to 25 October 1:00 UTC)
        let march = naive_seconds(&DateTime::date(2026, 3, 29)) + 3600;
        assert_eq!(cet.localtime(march - 1), 3600);
        assert_eq!(cet.localtime(march), 7200);
        let october = naive_seconds(&DateTime::date(2026, 10, 25)) + 3600;
        assert_eq!(cet.localtime(october - 1), 7200);
        assert_eq!(cet.localtime(october), 3600);
        assert_eq!(cet.fromutc(october, 2026), 3600);
        assert!(Zone::from_posix("EST5EDT").is_none());
        assert_eq!(
            Zone::from_posix("<+0330>-3:30").unwrap().localtime(0),
            12_600
        );
        assert!(Zone::from_posix("X1").is_none());
        assert!(Zone::from_posix("CET-1:75").is_none());
        assert!(Zone::from_posix("XXX3YYY,J+5,J300").is_none());
    }

    #[test]
    fn naive_times_where_the_clocks_change() {
        let cet = Zone::from_posix("CET-1CEST,M3.5.0,M10.5.0/3").unwrap();
        let at = |h, m| DateTime {
            hour: h,
            minute: m,
            ..DateTime::date(2026, 10, 25)
        };
        // (2:30 twice: fold 0 the first, in summer time, fold 1 the second)
        assert_eq!(
            local_to_seconds(&cet, &at(2, 30), false),
            Some(naive_seconds(&at(0, 30)))
        );
        assert_eq!(
            local_to_seconds(&cet, &at(2, 30), true),
            Some(naive_seconds(&at(1, 30)))
        );
        let gap = DateTime {
            hour: 2,
            minute: 30,
            ..DateTime::date(2026, 3, 29)
        };
        // (2:30 never: fold 0 the later, 3:30 summer time; astimezone() takes it less the
        // offset of before the gap, 1:30 UTC, in summer time)
        assert_eq!(
            local_to_seconds(&cet, &gap, false),
            Some(naive_seconds(&DateTime { hour: 1, ..gap }))
        );
        assert_eq!(utcoffset(&cet, &gap, false), Some(7200));
        // (the second 2:30 of October, from its timestamp)
        let (back, fold) = fromtimestamp(&cet, naive_seconds(&at(1, 30)) as f64).unwrap();
        assert_eq!((back, fold), (at(2, 30), true));
    }

    #[test]
    fn years_of_any_day() {
        for days in [
            -800_000, -719_528, -1, 0, 59, 365, 10_957, 2_932_896, 2_932_897,
        ] {
            let expected = DateTime::from_ordinal(days + EPOCH_ORDINAL).map(|d| d.year);
            if let Some(year) = expected {
                assert_eq!(year_of_day(days), year, "{days}");
            }
        }
        assert_eq!(year_of_day(2_932_897), 10_000);
        assert_eq!(year_of_day(-719_162), 1);
        assert_eq!(year_of_day(-719_163), 0);
    }
}
