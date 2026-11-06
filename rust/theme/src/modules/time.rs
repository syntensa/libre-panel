//! Modules of time: the clock and the calendar sheet.

use lp_py::Value;

use super::{fmax, fmin, round, round_to, Build, Fields, Source, CAP};
use crate::model::Result;

fn glow(amount: f64, radius: i64) -> Fields {
    vec![
        ("glow", round_to(amount, 2).into()),
        ("glow_radius", radius.into()),
    ]
}

fn plain() -> Fields {
    vec![("tabular", false.into())]
}

/// build_clock: the time, the seconds when wide enough, and the date.
pub fn build_clock(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let a = b.aspect();
    let (ix, iy, iw, ih) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    if a <= 0.72 {
        // tall: hours over minutes
        let size = fmin(iw / 1.3, ih * 0.34);
        let date_size = fmax(8.0, size * 0.2);
        let gap = size * 0.22;
        let block = 2.0 * CAP * size + gap + date_size * 1.2 + CAP * date_size;
        let top = iy + (ih - block) / 2.0;
        let cx = ix + iw / 2.0;
        let radius = 2.max(round(size / 12.0));
        b.clock(
            "hours",
            "%H",
            cx,
            top,
            size,
            None,
            "center",
            0.0,
            "display_font",
            glow(0.3 * b.glow, radius),
        )?;
        b.clock(
            "minutes",
            "%M",
            cx,
            top + CAP * size + gap,
            size,
            Some(b.c("accent")),
            "center",
            0.0,
            "display_font",
            glow(0.6 * b.glow, radius),
        )?;
        b.clock(
            "date",
            "%a %d %b",
            cx,
            top + 2.0 * CAP * size + gap + date_size * 1.2,
            date_size,
            Some(b.c("text2")),
            "center",
            iw,
            "text_font",
            plain(),
        )?;
        return Ok(());
    }
    let seconds = a >= 2.2;
    let date_ratio = 0.2;
    let font = b.st("display_font");
    let clock_width = b.width_per_size(&font, "00:00")?;
    let extra = if seconds { 0.62 } else { 0.0 };
    let size = fmin(ih / (1.0 + date_ratio * 2.2), iw / (clock_width + extra));
    let date_size = fmax(8.0, size * date_ratio);
    let mut block = CAP * size + date_size * 1.1 + CAP * date_size;
    // the seconds run along a line under the date where the card has room (Almanac's)
    let line = ih - block > size * 0.9;
    let (line_h, line_gap) = (2.max(round(size * 0.012)), date_size * 1.6);
    if line {
        block += line_gap + line_h as f64;
    }
    let top = iy + (ih - block) / 2.0;
    let left = a >= 1.45;
    let x = if left { ix } else { ix + iw / 2.0 };
    let align = if left { "left" } else { "center" };
    b.clock(
        "time",
        "%H:%M",
        x,
        top,
        size,
        None,
        align,
        iw,
        "display_font",
        glow(0.3 * b.glow, 2.max(round(size / 12.0))),
    )?;
    if seconds {
        let sx = x + clock_width * size + size * 0.08;
        b.clock(
            "seconds",
            "%S",
            sx,
            top,
            size * 0.36,
            Some(b.c("accent")),
            "left",
            0.0,
            "display_font",
            glow(0.7 * b.glow, 2.max(round(size / 16.0))),
        )?;
    }
    let fmt = if iw > size * 4.2 {
        "%A · %d %B"
    } else {
        "%a %d %b"
    };
    let date_top = top + CAP * size + date_size * 1.1;
    b.clock(
        "date",
        fmt,
        x,
        date_top,
        date_size,
        Some(b.c("text2")),
        align,
        iw,
        "text_font",
        plain(),
    )?;
    if line {
        let line_w = clock_width * size + if seconds { size * 0.62 } else { 0.0 };
        let lx = if left { x } else { x - line_w / 2.0 };
        let ly = date_top + CAP * date_size + line_gap;
        let groove: Fields = vec![
            ("x", round(lx).into()),
            ("y", round(ly).into()),
            ("w", round(line_w).into()),
            ("h", line_h.into()),
            ("color", b.c("track").into()),
        ];
        b.add("rect", "seconds-groove", groove)?;
        let run: Fields = vec![
            ("x", round(lx).into()),
            ("y", round(ly).into()),
            ("w", round(line_w).into()),
            ("h", line_h.into()),
            ("sensor", "time.second".into()),
            ("min", 0.into()),
            ("max", 60.into()),
            ("color", b.c("accent").into()),
            ("background", Value::None),
            ("radius", 0.into()),
            ("smooth", false.into()),
            ("end_dot", round_to(line_h as f64 * 2.25, 1).into()),
        ];
        b.add("bar", "seconds-run", run)?;
    }
    Ok(())
}

/// build_date: a calendar sheet: the day, the weekday, the month; the month's days when large.
pub fn build_date(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    date_layout(b)
}

/// _date_layout
pub fn date_layout(b: &mut Build) -> Result<()> {
    let a = b.aspect();
    let (ix, iy, iw, ih, pad) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.pad as f64,
    );
    if (b.large() && a >= 1.6) || a >= 3.0 {
        // the sheet beside the month
        let width = fmin(iw * 0.4, ih * 1.3);
        sheet(b, ix, iy, width, ih)?;
        month(b, ix + width + pad, iy, iw - width - pad, ih)
    } else if b.large() || (a <= 0.72 && ih >= iw * 1.6) {
        // the sheet above the month
        let top = ih * if b.large() { 0.32 } else { 0.4 };
        sheet(b, ix, iy, iw, top)?;
        month(b, ix, iy + top + pad * 0.5, iw, ih - top - pad * 0.5)
    } else {
        sheet(b, ix, iy, iw, ih)
    }
}

/// _month: the month's days.
pub fn month(b: &mut Build, x: f64, y: f64, w: f64, h: f64) -> Result<()> {
    let fields: Fields = vec![
        ("x", round(x).into()),
        ("y", round(y).into()),
        ("w", round(w).into()),
        ("h", round(h).into()),
        (
            "font",
            b.style.get("text_font").cloned().unwrap_or(Value::None),
        ),
        (
            "font_size",
            round(fmin(h / 7.0 * 0.6, w / 7.0 * 0.45)).into(),
        ),
        ("color", b.c("text2").into()),
        ("color2", b.tint("accent").into()),
        ("muted", b.c("text3").into()),
    ];
    b.add("calendar", "calendar", fields)?;
    Ok(())
}

/// _sheet: the day, the weekday and the month.
pub fn sheet(b: &mut Build, x: f64, y: f64, w: f64, h: f64) -> Result<()> {
    let accent = b.tint("accent");
    if w / fmax(1.0, h) >= 1.6 {
        // day on the left, names on the right
        let size = fmin(h * 0.78, w * 0.3);
        b.clock(
            "day",
            "%d",
            x,
            y + (h - CAP * size) / 2.0,
            size,
            Some(accent),
            "left",
            0.0,
            "display_font",
            glow(0.5 * b.glow, 2.max(round(size / 14.0))),
        )?;
        let nx = x + size * 1.3;
        let name = fmin(h * 0.2, (w - size * 1.3) * 0.16);
        b.clock(
            "weekday",
            "%A",
            nx,
            y + h / 2.0 - CAP * name - name * 0.25,
            name,
            None,
            "left",
            x + w - nx,
            "text_font",
            plain(),
        )?;
        b.clock(
            "month",
            "%B %Y",
            nx,
            y + h / 2.0 + name * 0.25,
            name * 0.8,
            Some(b.c("text3")),
            "left",
            x + w - nx,
            "text_font",
            plain(),
        )?;
        return Ok(());
    }
    let size = fmin(h * 0.46, w * 0.5);
    let name = fmax(8.0, fmin(size * 0.26, w * 0.14));
    let block = CAP * size + name * 0.9 + CAP * name + name * 0.6 + CAP * name * 0.8;
    let mut top = y + (h - block) / 2.0;
    b.clock(
        "day",
        "%d",
        x,
        top,
        size,
        Some(accent),
        "left",
        0.0,
        "display_font",
        glow(0.5 * b.glow, 2.max(round(size / 14.0))),
    )?;
    top += CAP * size + name * 0.9;
    b.clock(
        "weekday",
        "%A",
        x,
        top,
        name,
        None,
        "left",
        w,
        "text_font",
        plain(),
    )?;
    top += CAP * name + name * 0.6;
    b.clock(
        "month",
        "%B %Y",
        x,
        top,
        name * 0.8,
        Some(b.c("text3")),
        "left",
        w,
        "text_font",
        plain(),
    )
}

/// _face: a clock face.
fn face(b: &mut Build, name: &str, x: f64, y: f64, d: f64, zone: &str) -> Result<()> {
    let color2 = if !b.m("color").truthy() {
        b.c("accent")
    } else {
        b.tint("accent")
    };
    let dial = if b.card_on {
        super::mix(&b.c("surface"), &b.c("track"), 0.7)?
    } else {
        b.c("surface")
    };
    let fields: Fields = vec![
        ("x", round(x).into()),
        ("y", round(y).into()),
        ("w", round(d).into()),
        ("h", round(d).into()),
        ("timezone", zone.into()),
        ("color", b.c("text").into()),
        ("color2", color2.into()),
        ("face", dial.into()),
        ("marks", b.c("text3").into()),
        ("seconds", (d >= 90.0).into()),
        ("hand", "glide".into()),
        ("glow", round_to(0.15 * b.glow, 2).into()),
        ("glow_radius", 2.max(round(d * 0.03)).into()),
    ];
    b.add("analog", name, fields)?;
    Ok(())
}

fn zoned(zone: &str) -> Fields {
    vec![("timezone", zone.into())]
}

fn plain_zoned(zone: &str) -> Fields {
    vec![("tabular", false.into()), ("timezone", zone.into())]
}

/// build_analog: a clock face; the time and the date beside or under it when there is room.
pub fn build_analog(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let zone = b.ms("timezone");
    let a = b.aspect();
    let (ix, iy, iw, ih, pad, ts) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.pad as f64,
        b.ts as f64,
    );
    let title = b.ms("title");
    if a >= 1.6 {
        // face | time and date
        let d = ih;
        face(b, "face", ix, iy, d, &zone)?;
        let x = ix + d + pad * 1.6;
        let w = ix + iw - x;
        let size = fmin(ih * 0.3, w / 3.0);
        let date_size = fmax(8.0, size * 0.32);
        let block = CAP * size + date_size * 1.2 + CAP * date_size;
        let top = iy + (ih - block) / 2.0;
        b.clock(
            "time",
            "%H:%M",
            x,
            top,
            size,
            None,
            "left",
            w,
            "display_font",
            zoned(&zone),
        )?;
        let fmt = if w > size * 4.2 {
            "%A · %d %B"
        } else {
            "%a %d %b"
        };
        b.clock(
            "date",
            fmt,
            x,
            top + CAP * size + date_size * 1.2,
            date_size,
            Some(b.c("text2")),
            "left",
            w,
            "text_font",
            plain_zoned(&zone),
        )?;
        if !title.is_empty() {
            b.label("title", &title, x, top - ts * 1.6, ts, None, "left", w)?;
        }
        return Ok(());
    }
    let tall = a <= 0.72 && ih > iw * 1.3;
    let room = if tall || !title.is_empty() {
        ts * 2.4
    } else {
        0.0
    };
    let d = fmin(iw, ih - room);
    let (x, y) = (ix + (iw - d) / 2.0, iy + (ih - d - room) / 2.0);
    face(b, "face", x, y, d, &zone)?;
    if tall && title.is_empty() {
        b.clock(
            "date",
            "%a %d %b",
            ix + iw / 2.0,
            y + d + ts * 1.0,
            ts * 1.2,
            Some(b.c("text2")),
            "center",
            iw,
            "text_font",
            plain_zoned(&zone),
        )?;
    } else if !title.is_empty() {
        b.label(
            "title",
            &title,
            ix + iw / 2.0,
            y + d + ts * 1.0,
            ts,
            None,
            "center",
            iw,
        )?;
    }
    Ok(())
}

/// _cities: the places of a world clock (`items`: "zone = name" a line), those whose zone is
/// known.
fn cities(b: &Build) -> Vec<(String, String)> {
    let items = match b.ms("items") {
        s if !s.is_empty() => s,
        _ => super::default_items("world"),
    };
    let mut found = Vec::new();
    for line in lp_py::splitlines(&items) {
        let (zone, name) = line.split_once('=').unwrap_or((line, ""));
        let zone = lp_py::strip(zone);
        if zone.is_empty() || zone.starts_with('#') || !lp_py::zoneinfo::exists(zone) {
            continue;
        }
        let name = match lp_py::strip(name) {
            "" => zone.rsplit('/').next().unwrap_or(zone).replace('_', " "),
            given => given.to_string(),
        };
        found.push((zone.to_string(), name));
    }
    found
}

/// -(-n // d)
fn ceil_div(n: i64, d: i64) -> i64 {
    -(-n).div_euclid(d)
}

/// build_world: the time in other places: rows of cities, clock faces when big.
pub fn build_world(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let (ix, iy, iw, ih, pad, ts) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.pad as f64,
        b.ts as f64,
    );
    let cities = cities(b);
    if cities.is_empty() {
        b.label(
            "none",
            &lp_i18n::t("Add places in the settings"),
            ix + iw / 2.0,
            iy + ih / 2.0 - CAP * ts / 2.0,
            ts,
            None,
            "center",
            iw,
        )?;
        return Ok(());
    }
    let mut top = 0.0;
    let title = b.ms("title");
    if !title.is_empty() {
        let accent = b.tint("accent");
        top = b.head("globe", &title, &accent, 0.0)?;
    }
    let (x, y, w, h) = (ix, iy + top, iw, ih - top);
    let n = cities.len() as i64;
    if b.large() && h > ts * 9.0 {
        // faces in a grid
        let mut best = (0.0, 1);
        for columns in 1..=n {
            let lines = ceil_div(n, columns);
            let cell_w = (w - ((columns - 1) * b.pad) as f64) / columns as f64;
            let cell_h = (h - ((lines - 1) * b.pad) as f64) / lines as f64;
            let size = fmin(cell_w, cell_h - ts * 3.2);
            if size > best.0 {
                best = (size, columns);
            }
        }
        let (mut d, columns) = best;
        d *= 0.92;
        let lines = ceil_div(n, columns);
        let cell_w = (w - ((columns - 1) * b.pad) as f64) / columns as f64;
        let cell_h = (h - ((lines - 1) * b.pad) as f64) / lines as f64;
        for (i, (zone, name)) in cities.iter().enumerate() {
            let (row, col) = (
                (i as i64).div_euclid(columns),
                (i as i64).rem_euclid(columns),
            );
            let cx = x + col as f64 * (cell_w + pad) + cell_w / 2.0;
            let cy = y + row as f64 * (cell_h + pad) + (cell_h - d - ts * 3.2) / 2.0;
            face(b, &format!("c{i}-face"), cx - d / 2.0, cy, d, zone)?;
            b.label(
                &format!("c{i}-name"),
                name,
                cx,
                cy + d + ts * 0.8,
                ts * 0.95,
                None,
                "center",
                cell_w,
            )?;
            b.clock(
                &format!("c{i}-time"),
                "%a %H:%M",
                cx,
                cy + d + ts * 2.2,
                ts,
                Some(b.c("text3")),
                "center",
                cell_w,
                "text_font",
                zoned(zone),
            )?;
        }
        return Ok(());
    }
    let mut best = (1e9, 1);
    for columns in 1..=n {
        // cells close to 3.5:1, the shape of a row
        let lines = ceil_div(n, columns);
        let (cell_w, cell_h) = (w / columns as f64, h / lines as f64);
        if cell_h < ts * 2.2 && columns < n {
            continue;
        }
        let score = (fmax(1e-3, cell_w / fmax(1.0, cell_h)) / 3.5).ln().abs();
        if score < best.0 {
            best = (score, columns);
        }
    }
    let columns = best.1;
    let lines = ceil_div(n, columns);
    let gap = pad * 1.5;
    let cell_w = (w - (columns - 1) as f64 * gap) / columns as f64;
    let cell_h = h / lines as f64;
    let stacked = cell_h >= ts * 4.6 && cell_w < ts * 16.0;
    for (i, (zone, name)) in cities.iter().enumerate() {
        let (line, col) = (
            (i as i64).div_euclid(columns),
            (i as i64).rem_euclid(columns),
        );
        let cx = x + col as f64 * (cell_w + gap);
        let cy = y + line as f64 * cell_h;
        if stacked {
            // name, time, weekday, centred
            let size = fmin(cell_h * 0.32, cell_w * 0.3);
            let block = CAP * ts + ts * 0.9 + CAP * size + ts * 0.8 + CAP * ts * 0.9;
            let mut top = cy + (cell_h - block) / 2.0;
            let mid = cx + cell_w / 2.0;
            b.label(
                &format!("c{i}-name"),
                name,
                mid,
                top,
                ts,
                Some(b.c("text2")),
                "center",
                cell_w,
            )?;
            top += CAP * ts + ts * 0.9;
            b.clock(
                &format!("c{i}-time"),
                "%H:%M",
                mid,
                top,
                size,
                None,
                "center",
                cell_w,
                "display_font",
                zoned(zone),
            )?;
            b.clock(
                &format!("c{i}-day"),
                "%a",
                mid,
                top + CAP * size + ts * 0.8,
                ts * 0.9,
                Some(b.c("text3")),
                "center",
                cell_w,
                "text_font",
                plain_zoned(zone),
            )?;
            continue;
        }
        let mid = cy + cell_h / 2.0;
        let size = fmin(fmin(cell_h * 0.5, ts * 2.2), cell_w * 0.16);
        let day = cell_h >= ts * 3.4;
        let below = if day { ts * 0.8 + CAP * ts * 0.9 } else { 0.0 };
        let name_top = mid - (CAP * ts + below) / 2.0;
        b.label(
            &format!("c{i}-name"),
            name,
            cx,
            name_top,
            ts,
            Some(b.c("text2")),
            "left",
            cell_w * 0.55,
        )?;
        if day {
            b.clock(
                &format!("c{i}-day"),
                "%a",
                cx,
                name_top + CAP * ts + ts * 0.8,
                ts * 0.9,
                Some(b.c("text3")),
                "left",
                cell_w * 0.5,
                "text_font",
                plain_zoned(zone),
            )?;
        }
        b.clock(
            &format!("c{i}-time"),
            "%H:%M",
            cx + cell_w,
            mid - CAP * size / 2.0,
            size,
            None,
            "right",
            cell_w * 0.45,
            "display_font",
            zoned(zone),
        )?;
    }
    Ok(())
}

/// The day a countdown counts to: no year for every year, no hour for the day's start.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Target {
    pub year: Option<i64>,
    pub month: i64,
    pub day: i64,
    pub hour: Option<i64>,
    pub minute: Option<i64>,
}

/// render/countdown.py's parse_target: "2026-12-24", "2026-12-24 18:00" or "12-24" (every
/// year), as its regular expression reads it; (year, month, day, hour, minute), None for what
/// is no such day.
pub fn parse_target(text: &str) -> Option<Target> {
    let chars: Vec<char> = text.chars().collect();
    let digit = |i: usize| chars.get(i).and_then(|&c| lp_py::decimal(c));
    let number = |from: usize, to: usize| -> i64 {
        (from..to).fold(0, |n, i| n * 10 + digit(i).expect("a digit") as i64)
    };
    // ^\s*
    let start = chars.iter().take_while(|&&c| lp_py::isspace(c)).count();
    // \s*$ from `at`
    let ends = |at: usize| chars[at..].iter().all(|&c| lp_py::isspace(c));
    // (\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?\s*$ from `at`, the longest digits first
    let rest = |at: usize| -> Option<(i64, i64, Option<i64>, Option<i64>)> {
        for m in [2, 1] {
            if (0..m).any(|k| digit(at + k).is_none()) || chars.get(at + m) != Some(&'-') {
                continue;
            }
            let day_at = at + m + 1;
            for d in [2, 1] {
                if (0..d).any(|k| digit(day_at + k).is_none()) {
                    continue;
                }
                let after = day_at + d;
                // the time, where it follows
                if matches!(chars.get(after), Some(' ') | Some('T')) {
                    for hl in [2, 1] {
                        let h_at = after + 1;
                        if (0..hl).any(|k| digit(h_at + k).is_none())
                            || chars.get(h_at + hl) != Some(&':')
                        {
                            continue;
                        }
                        let mi = h_at + hl + 1;
                        if digit(mi).is_some() && digit(mi + 1).is_some() && ends(mi + 2) {
                            return Some((
                                number(at, at + m),
                                number(day_at, after),
                                Some(number(h_at, h_at + hl)),
                                Some(number(mi, mi + 2)),
                            ));
                        }
                    }
                }
                if ends(after) {
                    return Some((number(at, at + m), number(day_at, after), None, None));
                }
            }
        }
        None
    };
    let with_year =
        (0..4).all(|k| digit(start + k).is_some()) && chars.get(start + 4) == Some(&'-');
    let found = with_year
        .then(|| rest(start + 5).map(|r| (Some(number(start, start + 4)), r)))
        .flatten()
        .or_else(|| rest(start).map(|r| (None, r)));
    let (year, (month, day, hour, minute)) = found?;
    if !((1..=12).contains(&month) && (1..=31).contains(&day)) || hour.is_some_and(|h| h > 23) {
        return None;
    }
    if minute.is_some_and(|m| m > 59) {
        return None;
    }
    Some(Target {
        year,
        month,
        day,
        hour,
        minute,
    })
}

/// build_countdown: the days (or hours) left until a day you choose; every year with "12-24".
pub fn build_countdown(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let given = b.ms("target");
    let target = if given.is_empty() {
        "01-01".to_string()
    } else {
        given.clone()
    };
    let timed = parse_target(&target).is_some_and(|p| p.hour.is_some());
    let color = b.tint("accent");
    let title = match b.ms("title") {
        t if !t.is_empty() => t,
        _ if given.is_empty() => lp_i18n::t("New Year"),
        _ => lp_i18n::t("Countdown"),
    };
    let top = b.head("hourglass", &title, &color, 0.0)?;
    let (ix, iy, iw, ih, ts) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.ts as f64,
    );
    let (x, y, w, h) = (ix, iy + top, iw, ih - top);
    let wide = w / fmax(1.0, h) >= 2.2;
    let number_w = w * if wide { 0.45 } else { 1.0 };
    let size = fmin(h * if !b.large() { 0.5 } else { 0.42 }, number_w * 0.42);
    let unit = ts * 1.1;
    let block = CAP * size + ts * 0.9 + CAP * unit;
    let cap = if wide || !b.large() {
        y + (h - block) / 2.0
    } else {
        y + h * 0.08
    };
    let part = |b: &Build, part: &str, size: f64, cap: f64, font: &str, kw: Fields| -> Fields {
        let mut fields: Fields = vec![("part", part.into()), ("target", target.as_str().into())];
        let mut text = b.text_fields(size, cap, font);
        text.extend(kw);
        fields.extend(text);
        fields
    };
    let kw: Fields = vec![
        ("x", round(x).into()),
        ("color", b.c("text").into()),
        ("max_width", round(number_w).into()),
        ("tabular", true.into()),
        ("glow", round_to(0.3 * b.glow, 2).into()),
        ("glow_radius", 2.max(round(size / 14.0)).into()),
    ];
    let number = part(b, "number", size, cap, "display_font", kw);
    b.add("countdown", "number", number)?;
    let kw: Fields = vec![
        ("x", round(x).into()),
        ("color", color.as_str().into()),
        ("max_width", round(number_w).into()),
        ("tabular", false.into()),
    ];
    let unit_fields = part(
        b,
        "unit",
        unit,
        cap + CAP * size + ts * 0.9,
        "text_font",
        kw,
    );
    b.add("countdown", "unit", unit_fields)?;
    let (mut rest_x, mut rest_y, mut rest_w) = (x, cap + block + ts * 1.6, w);
    if wide {
        (rest_x, rest_w) = (x + w * 0.5, w * 0.5);
        rest_y = y + (h - (CAP * ts * 1.3 + if timed { ts * 1.8 } else { 0.0 })) / 2.0;
    }
    let mut lines: Vec<(&str, f64, String, &str)> = Vec::new();
    if timed {
        lines.push(("clock", ts * 1.3, b.c("text2"), "display_font"));
    }
    if wide || b.large() {
        lines.push(("date", ts, b.c("text3"), "text_font"));
    }
    for (name, line_size, line_color, font) in lines {
        if rest_y + CAP * line_size > iy + ih {
            break;
        }
        let kw: Fields = vec![
            ("x", round(rest_x).into()),
            ("color", line_color.into()),
            ("max_width", round(rest_w).into()),
            ("tabular", (name == "clock").into()),
            ("fit", "ellipsis".into()),
        ];
        let fields = part(b, name, line_size, rest_y, font, kw);
        b.add("countdown", name, fields)?;
        rest_y += CAP * line_size + ts * 1.0;
    }
    Ok(())
}
