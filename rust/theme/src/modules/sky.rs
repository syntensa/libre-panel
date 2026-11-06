//! Modules of the sky: the sun's day, the moon, the earth.

use lp_i18n::t;
use lp_py::Value;

use super::{fmax, fmin, mix, round, round_to, Build, Fields, Source, CAP};
use crate::model::Result;

/// px: the sun's arc, at the least
const ARC_MIN: f64 = 16.0;
/// seconds for a turn of a module's globe, where it moves
const EARTH_SPIN_S: i64 = 180;

/// Puts `prefix` before the ids of the widgets from `start` on.
fn prefix_ids(b: &mut Build, start: usize, prefix: &str) {
    for widget in &mut b.out[start..] {
        let id = widget.get("id").map(Value::str).unwrap_or_default();
        widget.insert("id".into(), format!("{prefix}{id}").into());
    }
}

/// build_sun: sunrise and sunset under the day's arc; the moon's phase beside or below.
/// Without a place only the moon shows.
pub fn build_sun(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let a = b.aspect();
    let (ix, iy, iw, ih, pad) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.pad as f64,
    );
    b.needs = "sun.rise".into();
    let large = b.large();
    if large && a < 1.6 {
        let top = ih * 0.58;
        sun(b, ix, iy, iw, top, true)?;
        moon(b, ix, iy + top + pad, iw, ih - top - pad, true, false)?;
    } else if a >= 1.45 {
        let width = (iw - pad * 2.0) * 0.5;
        sun(b, ix, iy, width, ih, large)?;
        moon(
            b,
            ix + width + pad * 2.0,
            iy,
            iw - width - pad * 2.0,
            ih,
            large,
            false,
        )?;
    } else if a <= 0.72 && ih > iw * 1.5 {
        let top = ih * 0.55;
        sun(b, ix, iy, iw, top, false)?;
        moon(b, ix, iy + top + pad, iw, ih - top - pad, false, false)?;
    } else {
        sun(b, ix, iy, iw, ih, false)?;
    }
    let first = b.out.len();
    b.needs = "!sun.rise".into();
    moon(b, ix, iy, iw, ih, large || a >= 1.45, false)?;
    prefix_ids(b, first, "only-");
    b.needs = String::new();
    Ok(())
}

/// _sun: the day's arc, the sun or the moon on it, the times under its ends.
fn sun(b: &mut Build, x: f64, y: f64, w: f64, h: f64, details: bool) -> Result<()> {
    let color = b.tint("warn");
    let ts = b.ts as f64;
    let text_h = ts * if details { 4.6 } else { 2.4 };
    let mut d = fmax(0.0, fmin(w, (h - text_h) * 2.0));
    if d < ARC_MIN {
        // a box too low for the arc keeps the times
        d = 0.0;
    }
    let cx = x + w / 2.0;
    let base = y + d / 2.0; // the horizon: the arc's ends
    if d != 0.0 {
        let deep = b.deep(&color)?;
        let arc: Fields = vec![
            ("x", round(cx - d / 2.0).into()),
            ("y", round(y).into()),
            ("w", round(d).into()),
            ("h", round(d).into()),
            ("sensor", "sun.progress".into()),
            ("min", 0.into()),
            ("max", 1.into()),
            ("start_angle", 180.into()),
            ("end_angle", 360.into()),
            ("thickness", 3.max(round(d * 0.055)).into()),
            ("color", deep.into()),
            ("color2", color.as_str().into()),
            ("background", b.c("track").into()),
            ("cap", "round".into()),
            ("glow", round_to(0.45 * b.glow, 2).into()),
            ("glow_radius", 2.max(round(d * 0.04)).into()),
            ("smooth", true.into()),
        ];
        b.add("gauge", "arc", arc)?;
        let icon = d * 0.26;
        b.icon(
            "sun-icon",
            "sun",
            cx - icon / 2.0,
            base - icon - d * 0.04,
            icon,
            &color,
        )?;
        let last = b.out.len() - 1;
        b.out[last].insert("needs".into(), "sun.progress".into());
        b.icon(
            "night-icon",
            "moon",
            cx - icon / 2.0,
            base - icon - d * 0.04,
            icon,
            &b.c("text2"),
        )?;
        // night where the sun's times are known
        let last = b.out.len() - 1;
        b.out[last].insert("needs".into(), "sun.rise,!sun.progress".into());
    }
    let mut size = ts * 1.3;
    let cap = base + ts * 0.7;
    let mark = ts * 1.5;
    // the times under the arc's ends (not further out than a little)
    let mut left = fmax(x, cx - d / 2.0 - mark * 0.6);
    let mut right = fmin(x + w, cx + d / 2.0 + mark * 0.6);
    let needed = 2.0 * (mark * 1.2 + size * 2.7) + ts;
    if right - left < needed {
        // a small arc: the times use the whole width
        (left, right) = (x, x + w);
        size = fmin(size, fmax(7.0, (w - ts - 2.0 * mark * 1.2) / 5.4));
    }
    let half = (right - left) / 2.0;
    b.icon(
        "rise-icon",
        "sunrise",
        left,
        cap + CAP * size / 2.0 - mark / 2.0,
        mark,
        &b.c("text3"),
    )?;
    b.value(
        "rise",
        "sun.rise",
        "{value}",
        left + mark * 1.2,
        cap,
        size,
        None,
        "left",
        half - mark * 1.2,
        "display_font",
        true,
        Vec::new(),
    )?;
    b.icon(
        "set-icon",
        "sunset",
        right - mark,
        cap + CAP * size / 2.0 - mark / 2.0,
        mark,
        &b.c("text3"),
    )?;
    b.value(
        "set",
        "sun.set",
        "{value}",
        right - mark * 1.2,
        cap,
        size,
        None,
        "right",
        half - mark * 1.2,
        "display_font",
        true,
        Vec::new(),
    )?;
    if details {
        let line = cap + CAP * size + ts * 1.3;
        b.label(
            "daylight-label",
            &t("Daylight"),
            left,
            line,
            ts * 0.9,
            None,
            "left",
            half,
        )?;
        b.value(
            "daylight",
            "sun.daylight",
            "{value:duration}",
            right,
            line - ts * 0.05,
            ts * 1.05,
            Some(b.c("text2")),
            "right",
            half,
            "text_font",
            true,
            Vec::new(),
        )?;
    }
    Ok(())
}

/// build_moon: the moon as it is lit tonight, large: its phase, how much of it is lit, and
/// the days until the next full moon and new moon.
pub fn build_moon(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let (ix, iy, iw, ih) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    moon(b, ix, iy, iw, ih, true, true)
}

/// (name, sensor, format, scale of the type, role of its colour)
type Line = (&'static str, &'static str, String, f64, &'static str);

/// Lines of texts one under the other, from `top`.
#[allow(clippy::too_many_arguments)]
fn lines_of(
    b: &mut Build,
    lines: &[Line],
    tx: f64,
    mut top: f64,
    align: &str,
    width: f64,
) -> Result<()> {
    let ts = b.ts as f64;
    for (name, key, fmt, scale, role) in lines {
        let size = ts * scale;
        let kw: Fields = vec![
            ("fallback", "".into()),
            ("fit", "ellipsis".into()),
            ("hide_if_missing", true.into()),
        ];
        b.value(
            name,
            key,
            fmt,
            tx,
            top,
            size,
            Some(b.c(role)),
            align,
            width,
            "text_font",
            false,
            kw,
        )?;
        top += CAP * size + ts * 0.9;
    }
    Ok(())
}

/// _moon: the moon's disc and its lines beside or under it.
#[allow(clippy::too_many_arguments)]
fn moon(b: &mut Build, x: f64, y: f64, w: f64, h: f64, details: bool, more: bool) -> Result<()> {
    let (ts, pad) = (b.ts as f64, b.pad as f64);
    let beside = w / fmax(1.0, h) >= 1.2 && w >= ts * 14.0;
    let mut lines: Vec<Line> = vec![
        ("name", "moon.name", "{value}".into(), 1.25, "text"),
        (
            "lit",
            "moon.illumination",
            t("{value:.0f}% lit"),
            1.0,
            "text2",
        ),
    ];
    if details {
        lines.push((
            "full",
            "moon.full_in",
            t("Full moon in {value:.0f} days"),
            0.95,
            "text3",
        ));
    }
    if more {
        lines.push((
            "new",
            "moon.new_in",
            t("New moon in {value:.0f} days"),
            0.95,
            "text3",
        ));
    }
    let block = lp_py::sum_floats(lines.iter().map(|l| CAP * ts * l.3))
        + ts * 0.9 * (lines.len() as f64 - 1.0);
    let (disc, dx, dy, tx, align, width, top);
    if beside {
        disc = fmin(h * 0.82, w * 0.38);
        (dx, dy) = (x, y + (h - disc) / 2.0);
        (tx, align, width) = (x + disc + pad * 1.2, "left", w - disc - pad * 1.2);
        top = y + (h - block) / 2.0;
    } else {
        disc = fmin(fmin(w * 0.5, h * 0.55), h - block - ts * 1.2);
        (dx, dy) = (
            x + (w - disc) / 2.0,
            y + (h - disc - block - ts * 1.2) / 2.0,
        );
        (tx, align, width) = (x + w / 2.0, "center", w);
        top = dy + disc + ts * 1.2;
    }
    if disc < 8.0 {
        return Ok(());
    }
    let fields: Fields = vec![
        ("x", round(dx).into()),
        ("y", round(dy).into()),
        ("size", round(disc).into()),
        ("sensor", "moon.phase".into()),
        ("color", b.c("text").into()),
        ("color2", mix(&b.c("track"), &b.c("text3"), 0.22)?.into()),
        ("glow", round_to(0.25 * b.glow, 2).into()),
        ("glow_radius", 2.max(round(disc * 0.08)).into()),
    ];
    b.add("moon", "moon", fields)?;
    lines_of(b, &lines, tx, top, align, width)
}

/// build_earth: the earth as it is now, lit by the sun, your place marked; whether it is day
/// or night there, the sunrise, the sunset and the day's length beside or under it. Without
/// a place the globe stands alone.
pub fn build_earth(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let (ix, iy, iw, ih) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    b.needs = "sun.up".into(); // (the sun's readings: there is a place)
    earth(b, ix, iy, iw, ih, true)?;
    let lone = b.out.len();
    b.needs = "!sun.up".into();
    earth(b, ix, iy, iw, ih, false)?;
    prefix_ids(b, lone, "only-");
    b.needs = String::new();
    Ok(())
}

/// _earth
fn earth(b: &mut Build, x: f64, y: f64, w: f64, h: f64, details: bool) -> Result<()> {
    let (ts, pad) = (b.ts as f64, b.pad as f64);
    let lines: Vec<Line> = if details {
        vec![
            ("rise", "sun.rise", t("Sunrise {value}"), 1.0, "text2"),
            ("set", "sun.set", t("Sunset {value}"), 1.0, "text2"),
            (
                "daylight",
                "sun.daylight",
                t("Day length {value:duration}"),
                0.95,
                "text3",
            ),
        ]
    } else {
        Vec::new()
    };
    let head = if details { ts * 1.6 } else { 0.0 };
    let block = head + lp_py::sum_floats(lines.iter().map(|l| CAP * ts * l.3 + ts * 0.9));
    let beside = details && w / fmax(1.0, h) >= 1.2 && w >= ts * 14.0;
    let (size, gx, gy);
    let mut place = (0.0, "left", 0.0, 0.0); // (tx, align, width, top)
    if !details {
        size = fmin(w, h);
        (gx, gy) = (x + (w - size) / 2.0, y + (h - size) / 2.0);
    } else if beside {
        // the globe as large as the lines beside it leave room for
        size = fmin(fmin(h, w - ts * 12.0), w * 0.7);
        (gx, gy) = (x, y + (h - size) / 2.0);
        place = (
            x + size + pad * 0.6,
            "left",
            w - size - pad * 0.6,
            y + (h - block) / 2.0,
        );
    } else {
        size = fmin(w, h - block - ts);
        (gx, gy) = (x + (w - size) / 2.0, y + (h - size - block - ts) / 2.0);
        place = (x + w / 2.0, "center", w, gy + size + ts);
    }
    if size < 16.0 {
        return Ok(());
    }
    let spin = if b.m("moving").truthy() {
        EARTH_SPIN_S
    } else {
        0
    };
    let fields: Fields = vec![
        ("x", round(gx).into()),
        ("y", round(gy).into()),
        ("size", round(size).into()),
        ("spin", spin.into()),
        ("color2", b.tint("accent").into()),
    ];
    b.add("earth", "earth", fields)?;
    if !details {
        return Ok(());
    }
    let (tx, align, width, mut top) = place;
    let big = ts * 1.6;
    for (name, word, need) in [
        ("day", t("Daytime"), "sun.up=1"),
        ("night", t("Night-time"), "sun.up=0"),
    ] {
        let mut fields: Fields = vec![
            ("x", round(tx).into()),
            ("text", word.into()),
            ("color", b.c("text").into()),
            ("align", align.into()),
            ("max_width", round(width).into()),
            ("tabular", false.into()),
            ("needs", need.into()),
        ];
        fields.extend(b.text_fields(big, top, "display_font"));
        b.add("text", name, fields)?;
    }
    top += CAP * big + ts * 1.0;
    lines_of(b, &lines, tx, top, align, width)
}
