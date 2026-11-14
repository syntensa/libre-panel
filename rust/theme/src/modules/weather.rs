//! Modules of the weather: now and the forecast, and the days ahead.

use lp_i18n::t;
use lp_py::Value;

use super::time::date_layout;
use super::{clamp, faded, floor_div, fmax, fmin, round, round_to, Build, Fields, Source, CAP};
use crate::model::Result;

/// (field, icon, format) of the details under the weather now
const DETAILS: [(&str, &str, &str); 3] = [
    ("apparent_temperature", "temperature", "{value:.0f}°"),
    ("humidity", "humidity", "{value:.0f}%"),
    ("wind_speed", "wind", "{value:.0f} km/h"),
];

/// Sets `needs` of the widgets from `start` on.
fn set_needs(b: &mut Build, start: usize, needs: &str) {
    for widget in &mut b.out[start..] {
        widget.insert("needs".into(), needs.into());
    }
}

/// Puts `first` before the `needs` the widgets from `start` on have.
fn needs_first(b: &mut Build, start: usize, first: &str) {
    for widget in &mut b.out[start..] {
        let own = widget.get("needs").map(Value::str).unwrap_or_default();
        let needs = if own.is_empty() {
            first.to_string()
        } else {
            format!("{first},{own}")
        };
        widget.insert("needs".into(), needs.into());
    }
}

/// _text_fields(size, cap_top, font, **kw): the font, size and y, then `kw`.
pub(super) fn text(b: &Build, size: f64, cap_top: f64, font: &str, kw: Fields) -> Fields {
    let mut fields = b.text_fields(size, cap_top, font);
    for (k, v) in kw {
        match fields.iter_mut().find(|(name, _)| *name == k) {
            Some(slot) => slot.1 = v,
            None => fields.push((k, v)),
        }
    }
    fields
}

/// build_weather: the weather of the configured place, with a forecast when there is room;
/// a calendar sheet while there is no weather.
pub fn build_weather(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let (ix, iy, iw, ih, pad, ts) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.pad as f64,
        b.ts as f64,
    );
    let start = b.out.len();
    if b.ms("fallback") == "none" {
        b.label(
            "setup",
            &t("No weather set up"),
            ix + iw / 2.0,
            iy + ih / 2.0 - CAP * ts / 2.0,
            ts,
            None,
            "center",
            iw,
        )?;
    } else {
        date_layout(b)?;
    }
    set_needs(b, start, "!weather.temperature");
    let start = b.out.len();
    let chosen = b.ms("forecast");
    let mode = match chosen.as_str() {
        "auto" | "both" => "days".to_string(),
        other => other.to_string(),
    };
    let a = b.aspect();
    let on = mode != "off";
    if on && a >= 3.0 {
        // long: now | forecast
        let width = fmin(iw * 0.4, ih * 2.0);
        weather_now(b, ix, iy, width, ih, false)?;
        forecast(
            b,
            ix + width + pad,
            iy,
            iw - width - pad,
            ih,
            &mode,
            false,
            "f",
        )?;
    } else if on && b.large() && a >= 1.6 {
        // big and wide: now with details | forecast
        let width = iw * 0.4;
        weather_now(b, ix, iy, width, ih, true)?;
        let (x, w) = (ix + width + pad, iw - width - pad);
        let both = chosen == "both" || (chosen == "auto" && ih >= ts * 14.0);
        if both {
            // days above, hours below
            let half = (ih - pad) / 2.0;
            forecast(b, x, iy, w, half, "days", false, "f")?;
            forecast(b, x, iy + half + pad, w, half, "hours", false, "h")?;
        } else {
            forecast(b, x, iy, w, ih, &mode, false, "f")?;
        }
    } else if on && a <= 0.72 && ih >= iw * 1.8 {
        // tall: the forecast as rows
        let both = chosen == "both" || (chosen == "auto" && ih >= ts * 44.0);
        let top = ih * if both { 0.24 } else { 0.42 };
        let large = b.large();
        weather_now(b, ix, iy, iw, top, large)?;
        let (y, h) = (iy + top + pad * 0.5, ih - top - pad * 0.5);
        if both {
            let half = (h - pad) / 2.0;
            forecast(b, ix, y, iw, half, "hours", true, "h")?;
            forecast(b, ix, y + half + pad, iw, half, "days", true, "f")?;
        } else {
            forecast(b, ix, y, iw, h, &mode, true, "f")?;
        }
    } else if on && b.large() {
        // big: now above the forecast
        let top = ih * 0.5;
        weather_now(b, ix, iy, iw, top, false)?;
        forecast(
            b,
            ix,
            iy + top + pad * 0.5,
            iw,
            ih - top - pad * 0.5,
            &mode,
            false,
            "f",
        )?;
    } else {
        let details = b.large() || a >= 2.3;
        weather_now(b, ix, iy, iw, ih, details)?;
    }
    needs_first(b, start, "weather.temperature");
    Ok(())
}

/// _temp_fields: a temperature in the display font.
pub(super) fn temp_fields(
    b: &Build,
    x: f64,
    cap_top: f64,
    size: f64,
    width: f64,
    align: &str,
) -> Fields {
    let kw: Fields = vec![
        ("x", round(x).into()),
        ("color", b.c("text").into()),
        ("align", align.into()),
        ("max_width", round(width).into()),
        ("tabular", true.into()),
    ];
    text(b, size, cap_top, "display_font", kw)
}

/// _weather_desc: the sky in words.
pub(super) fn weather_desc(b: &mut Build, x: f64, top: f64, w: f64, size: f64) -> Result<()> {
    let kw: Fields = vec![
        ("x", round(x).into()),
        ("color", b.c("text2").into()),
        ("max_width", round(w).into()),
        ("fit", "ellipsis".into()),
        ("tabular", false.into()),
    ];
    let mut fields: Fields = vec![
        ("field", "description".into()),
        ("format", "{value}".into()),
        ("fallback", "".into()),
    ];
    fields.extend(text(b, size, top, "text_font", kw));
    b.add("weather", "desc", fields)?;
    Ok(())
}

fn temperature(b: &mut Build, fields: Fields) -> Result<()> {
    let mut all: Fields = vec![
        ("field", "temperature".into()),
        ("format", "{value:.0f}°".into()),
    ];
    all.extend(fields);
    b.add("weather", "temp", all)?;
    Ok(())
}

/// _weather_now: symbol, temperature and sky in a box; feels-like, humidity and wind if
/// asked.
fn weather_now(b: &mut Build, x: f64, y: f64, w: f64, h: f64, details: bool) -> Result<()> {
    let icon_color = b.tint("text");
    let ts = b.ts as f64;
    let desc = ts * 1.25;
    let a = w / fmax(1.0, h);
    if a <= 0.72 {
        // tall: icon, temperature, sky, then the details as rows
        let size = fmin(w * 0.42, h * 0.18);
        b.icon("icon", "weather", x, y, size, &icon_color)?;
        let temp_top = y + size * 1.25;
        let fields = temp_fields(b, x, temp_top, size * 1.1, w, "left");
        temperature(b, fields)?;
        let desc_top = temp_top + CAP * size * 1.1 + ts * 1.1;
        weather_desc(b, x, desc_top, w, desc)?;
        let room = y + h - (desc_top + CAP * desc + ts * 1.4);
        let rows = if details || room > ts * 3.0 {
            3.min(floor_div(room, ts * 2.4) as i64)
        } else {
            0
        };
        for (i, (field, icon, fmt)) in DETAILS.iter().take(super::py_take(3, rows)).enumerate() {
            let row_y = y + h - (rows - i as i64) as f64 * ts * 2.4 + ts * 0.6;
            b.icon(
                &format!("{field}-icon"),
                icon,
                x,
                row_y - ts * 0.25,
                ts * 1.3,
                &b.c("text3"),
            )?;
            let kw: Fields = vec![
                ("x", round(x + ts * 1.8).into()),
                ("color", b.c("text2").into()),
                ("max_width", round(w - ts * 1.8).into()),
                ("tabular", true.into()),
            ];
            let mut fields: Fields = vec![("field", (*field).into()), ("format", (*fmt).into())];
            fields.extend(text(b, ts * 1.15, row_y, "text_font", kw));
            b.add("weather", field, fields)?;
        }
        return Ok(());
    }
    let details = details && h > ts * 7.0;
    let main_h = h * if details { 0.62 } else { 1.0 };
    let desc_top = if a >= 1.45 {
        let size = fmin(main_h * 0.62, w * 0.22);
        b.icon(
            "icon",
            "weather",
            x,
            y + (main_h * 0.7 - size) / 2.0,
            size,
            &icon_color,
        )?;
        let fields = temp_fields(
            b,
            x + size * 1.2,
            y + (main_h * 0.7 - CAP * size) / 2.0,
            size,
            w - size * 1.2,
            "left",
        );
        temperature(b, fields)?;
        y + main_h * 0.7 + ts * 0.2
    } else {
        let size = fmin(h * 0.34, w * 0.36);
        b.icon("icon", "weather", x, y, size, &icon_color)?;
        let fields = temp_fields(b, x + w, y + size * 0.12, size, w * 0.6, "right");
        temperature(b, fields)?;
        y + size + ts * 1.2
    };
    weather_desc(b, x, desc_top, w, desc)?;
    if details {
        let row_y = y + h - CAP * ts * 1.1;
        let rule: Fields = vec![
            ("x", round(x).into()),
            ("y", round(row_y - ts * 1.6).into()),
            ("w", round(w).into()),
            ("h", 1.into()),
            ("color", b.c("line").into()),
        ];
        b.add("rect", "rule", rule)?;
        let count = if w / 3.0 >= ts * 6.5 { 3 } else { 2 };
        let step = w / count as f64;
        for (i, (field, icon, fmt)) in DETAILS.iter().take(count).enumerate() {
            let col_x = x + i as f64 * step;
            b.icon(
                &format!("{field}-icon"),
                icon,
                col_x,
                row_y - ts * 0.25,
                ts * 1.3,
                &b.c("text3"),
            )?;
            let kw: Fields = vec![
                ("x", round(col_x + ts * 1.7).into()),
                ("color", b.c("text2").into()),
                ("max_width", round(step - ts * 2.0).into()),
                ("tabular", true.into()),
            ];
            let mut fields: Fields = vec![("field", (*field).into()), ("format", (*fmt).into())];
            fields.extend(text(b, ts * 1.1, row_y, "text_font", kw));
            b.add("weather", field, fields)?;
        }
    }
    Ok(())
}

/// _forecast: days (today first) or hours (every `step` hours) as columns, or as rows.
#[allow(clippy::too_many_arguments)]
fn forecast(
    b: &mut Build,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    mode: &str,
    rows: bool,
    prefix: &str,
) -> Result<()> {
    let ts = b.ts as f64;
    let given = b.m("step");
    let step = 1.max(6.min(if given.truthy() {
        super::N::of(&given).to_int()
    } else {
        3
    }));
    let limit = if mode == "days" {
        7
    } else {
        24i64.div_euclid(step)
    };
    let count = if rows {
        clamp(floor_div(h, ts * 2.6), 1, limit).f() as i64
    } else {
        clamp(floor_div(w, fmax(ts * 5.0, h * 0.7)), 1, limit).f() as i64
    };
    let slots: Vec<i64> = if mode == "days" {
        (0..count).collect()
    } else {
        (0..count).map(|i| step * (i + 1)).collect()
    };
    let base = if mode == "days" {
        "weather.day"
    } else {
        "weather.hour"
    };
    let label = if mode == "days" { "name" } else { "time" };
    for (i, n) in slots.into_iter().enumerate() {
        let key = format!("{base}.{n}");
        let tag = format!("{prefix}{i}");
        let first = b.out.len();
        if rows {
            let row = h / count as f64;
            let mid = y + i as f64 * row + row / 2.0;
            let size = fmin(row * 0.42, ts * 1.4);
            b.value(
                &format!("{tag}-label"),
                &format!("{key}.{label}"),
                "{value}",
                x,
                mid - CAP * ts / 2.0,
                ts,
                Some(b.c("text3")),
                "left",
                w * 0.34,
                "text_font",
                false,
                vec![("fallback", "".into())],
            )?;
            let icon = fmin(row * 0.7, ts * 2.2);
            b.icon(
                &format!("{tag}-icon"),
                "weather",
                x + w * 0.36,
                mid - icon / 2.0,
                icon,
                &b.tint("text"),
            )?;
            let last = b.out.len() - 1;
            b.out[last].insert("sensor".into(), format!("{key}.code").into());
            let first_temp = if mode == "days" {
                "high"
            } else {
                "temperature"
            };
            b.value(
                &format!("{tag}-temp"),
                &format!("{key}.{first_temp}"),
                "{value:.0f}°",
                x + w,
                mid - CAP * size / 2.0,
                size,
                None,
                "right",
                w * 0.3,
                "display_font",
                true,
                Vec::new(),
            )?;
        } else {
            let col = w / count as f64;
            let cx = x + col * i as f64 + col / 2.0;
            let label_size = fmin(ts, col * 0.2);
            b.value(
                &format!("{tag}-label"),
                &format!("{key}.{label}"),
                "{value}",
                cx,
                y + h * 0.04,
                label_size,
                Some(b.c("text3")),
                "center",
                col * 0.92,
                "text_font",
                false,
                vec![("fallback", "".into())],
            )?;
            let icon = fmin(col * 0.52, h * 0.3);
            let icon_y = y + h * 0.04 + CAP * label_size + h * 0.08;
            b.icon(
                &format!("{tag}-icon"),
                "weather",
                cx - icon / 2.0,
                icon_y,
                icon,
                &b.tint("text"),
            )?;
            let last = b.out.len() - 1;
            b.out[last].insert("sensor".into(), format!("{key}.code").into());
            let size = fmin(col * 0.26, h * 0.16);
            let temp_top = icon_y + icon + h * 0.07;
            if mode == "days" {
                b.value(
                    &format!("{tag}-high"),
                    &format!("{key}.high"),
                    "{value:.0f}°",
                    cx - col * 0.04,
                    temp_top,
                    size,
                    None,
                    "right",
                    col * 0.46,
                    "display_font",
                    true,
                    Vec::new(),
                )?;
                b.value(
                    &format!("{tag}-low"),
                    &format!("{key}.low"),
                    "{value:.0f}°",
                    cx + col * 0.04,
                    temp_top,
                    size,
                    Some(b.c("text3")),
                    "left",
                    col * 0.46,
                    "display_font",
                    true,
                    Vec::new(),
                )?;
            } else {
                b.value(
                    &format!("{tag}-temp"),
                    &format!("{key}.temperature"),
                    "{value:.0f}°",
                    cx,
                    temp_top,
                    size,
                    None,
                    "center",
                    col * 0.9,
                    "display_font",
                    true,
                    Vec::new(),
                )?;
            }
            let rain_top = temp_top + CAP * size + h * 0.08;
            if rain_top + CAP * label_size <= y + h {
                // room for the chance of rain
                b.value(
                    &format!("{tag}-rain"),
                    &format!("{key}.rain"),
                    "{value:.0f}%",
                    cx,
                    rain_top,
                    label_size,
                    Some(b.tint("net")),
                    "center",
                    col * 0.9,
                    "text_font",
                    true,
                    vec![("hide_if_missing", true.into())],
                )?;
            }
        }
        set_needs(b, first, &format!("{key}.code"));
    }
    Ok(())
}

/// build_outlook: the next days, today first, at most a week; a hint while there is no
/// weather.
pub fn build_outlook(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let (ix, iy, iw, ih, ts) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.ts as f64,
    );
    let icon = fmin(iw, ih) * 0.24;
    b.needs = "!weather.day.0.code".into();
    b.icon(
        "idle-icon",
        "partly",
        ix + (iw - icon) / 2.0,
        iy + ih / 2.0 - icon * 0.9,
        icon,
        &b.c("text3"),
    )?;
    let hint = t("The days ahead show here once the weather is on: Settings, Weather and place");
    // where it is set on a line of its own
    let lines: Vec<String> = match hint.split_once(": ") {
        Some((first, where_)) => vec![format!("{first}:"), where_.to_string()],
        None => vec![hint.clone()],
    };
    for (n, line) in lines.iter().enumerate() {
        let mut fields: Fields = vec![
            ("x", round(ix + iw / 2.0).into()),
            ("text", line.as_str().into()),
            ("color", b.c("text3").into()),
            ("align", "center".into()),
            ("max_width", round(iw * 0.92).into()),
            ("fit", "shrink".into()),
            ("tabular", false.into()),
        ];
        fields.extend(b.text_fields(
            ts,
            iy + ih / 2.0 + icon * 0.35 + n as f64 * ts * 1.6,
            "text_font",
        ));
        b.add("text", &format!("idle-{n}"), fields)?;
    }
    b.needs = String::new();
    let clouds = match b.module.get("color") {
        Some(v) if v.truthy() => v.str(),
        _ => b.c("text2"),
    };
    let rain = b.c("gpu");
    let a = b.aspect();
    let rows = a <= 0.72 || (b.large() && ih >= ts * 16.0 && a < 2.2);
    let count = if rows {
        clamp(floor_div(ih, ts * 2.4), 1, 7).f() as i64
    } else {
        clamp(floor_div(iw, fmax(ts * 4.6, ih * 0.36)), 1, 7).f() as i64
    };
    for i in 0..count {
        let key = format!("weather.day.{i}");
        let first = b.out.len();
        if rows {
            outlook_row(b, i, &key, count, &clouds, &rain)?;
        } else {
            outlook_column(b, i, &key, count, &clouds, &rain)?;
        }
        // a day only while it is known
        needs_first(b, first, &format!("{key}.code"));
    }
    Ok(())
}

fn symbol(b: &mut Build, i: i64, key: &str, x: f64, y: f64, edge: f64, clouds: &str) -> Result<()> {
    let fields: Fields = vec![
        ("x", round(x).into()),
        ("y", round(y).into()),
        ("size", 8.max(round(edge)).into()),
        ("sensor", format!("{key}.code").into()),
        ("color", clouds.into()),
        ("moving", b.m("moving")),
        ("offset", round_to(i as f64 * 1.7, 2).into()),
    ];
    b.add("symbol", &format!("d{i}-symbol"), fields)?;
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn rain_bar(
    b: &mut Build,
    i: i64,
    key: &str,
    x: f64,
    y: f64,
    w: f64,
    thick: i64,
    rain: &str,
) -> Result<()> {
    let fields: Fields = vec![
        ("x", round(x).into()),
        ("y", round(y).into()),
        ("w", 4.max(round(w)).into()),
        ("h", thick.into()),
        ("sensor", format!("{key}.rain").into()),
        ("min", 0.into()),
        ("max", 100.into()),
        ("color", rain.into()),
        ("color2", Value::None),
        ("background", b.c("track").into()),
        ("radius", thick.div_euclid(2).into()),
        ("hide_if_missing", true.into()),
    ];
    b.add("bar", &format!("d{i}-rain-bar"), fields)?;
    Ok(())
}

/// _outlook_row: a day as a row: its name, its symbol, the chance of rain, the low and the
/// high.
fn outlook_row(
    b: &mut Build,
    i: i64,
    key: &str,
    count: i64,
    clouds: &str,
    rain: &str,
) -> Result<()> {
    let (ix, iy, iw, ih, ts) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.ts as f64,
    );
    let row = ih / count as f64;
    let mid = iy + row * (i as f64 + 0.5);
    let size = fmin(fmin(row * 0.42, ts * 1.5), iw * 0.08);
    let name = fmin(ts * 1.15, row * 0.4);
    if i != 0 {
        let rule: Fields = vec![
            ("x", round(ix).into()),
            ("y", round(iy + row * i as f64).into()),
            ("w", round(iw).into()),
            ("h", 1.into()),
            ("color", faded(&b.c("line"), 0.7)?.into()),
        ];
        b.add("rect", &format!("d{i}-rule"), rule)?;
    }
    b.value(
        &format!("d{i}-name"),
        &format!("{key}.name"),
        "{value}",
        ix,
        mid - CAP * name / 2.0,
        name,
        Some(b.c("text2")),
        "left",
        iw * 0.26,
        "text_font",
        false,
        vec![("fallback", "".into())],
    )?;
    let edge = fmin(row * 1.15, iw * 0.16);
    symbol(
        b,
        i,
        key,
        ix + iw * 0.3 - edge / 2.0,
        mid - edge / 2.0,
        edge,
        clouds,
    )?;
    let small = fmin(ts, row * 0.36);
    b.value(
        &format!("d{i}-rain"),
        &format!("{key}.rain"),
        "{value:.0f}%",
        ix + iw * 0.42,
        mid - CAP * small / 2.0,
        small,
        Some(rain.to_string()),
        "left",
        iw * 0.1,
        "text_font",
        true,
        vec![("hide_if_missing", true.into())],
    )?;
    let thick = 2.max(round(ts * 0.3));
    rain_bar(
        b,
        i,
        key,
        ix + iw * 0.53,
        mid - thick as f64 / 2.0,
        iw * 0.14,
        thick,
        rain,
    )?;
    b.value(
        &format!("d{i}-low"),
        &format!("{key}.low"),
        "{value:.0f}°",
        ix + iw * 0.8,
        mid - CAP * size / 2.0,
        size * 0.9,
        Some(b.c("text3")),
        "right",
        iw * 0.18,
        "display_font",
        true,
        Vec::new(),
    )?;
    b.value(
        &format!("d{i}-high"),
        &format!("{key}.high"),
        "{value:.0f}°",
        ix + iw,
        mid - CAP * size / 2.0,
        size,
        None,
        "right",
        iw * 0.18,
        "display_font",
        true,
        Vec::new(),
    )
}

/// _outlook_column: a day as a column: its name, its symbol, the high, the low, the chance
/// of rain.
fn outlook_column(
    b: &mut Build,
    i: i64,
    key: &str,
    count: i64,
    clouds: &str,
    rain: &str,
) -> Result<()> {
    let inner = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    outlook_column_in(b, inner, i, key, count, clouds, rain)
}

/// _outlook_column in the box `inner` (x, y, w, h): the weather card lays its days out in
/// a box of its own, of fractions of a pixel.
#[allow(clippy::too_many_arguments)]
pub(super) fn outlook_column_in(
    b: &mut Build,
    inner: (f64, f64, f64, f64),
    i: i64,
    key: &str,
    count: i64,
    clouds: &str,
    rain: &str,
) -> Result<()> {
    let (ix, iy, iw, ih) = inner;
    let ts = b.ts as f64;
    let col = iw / count as f64;
    let cx = ix + col * (i as f64 + 0.5);
    let name = fmin(ts * 1.1, col * 0.2);
    b.value(
        &format!("d{i}-name"),
        &format!("{key}.name"),
        "{value}",
        cx,
        iy,
        name,
        Some(b.c("text2")),
        "center",
        col * 0.92,
        "text_font",
        false,
        vec![("fallback", "".into())],
    )?;
    let edge = fmin(col * 0.7, ih * 0.34);
    let mut top = iy + CAP * name + ih * 0.06;
    symbol(b, i, key, cx - edge / 2.0, top, edge, clouds)?;
    let size = fmin(col * 0.26, ih * 0.13);
    top += edge + ih * 0.05;
    b.value(
        &format!("d{i}-high"),
        &format!("{key}.high"),
        "{value:.0f}°",
        cx,
        top,
        size,
        None,
        "center",
        col * 0.9,
        "display_font",
        true,
        Vec::new(),
    )?;
    top += CAP * size + ih * 0.05;
    let low = size * 0.72;
    b.value(
        &format!("d{i}-low"),
        &format!("{key}.low"),
        "{value:.0f}°",
        cx,
        top,
        low,
        Some(b.c("text3")),
        "center",
        col * 0.9,
        "display_font",
        true,
        Vec::new(),
    )?;
    top += CAP * low + ih * 0.07;
    let small = fmin(ts * 0.95, col * 0.17);
    if top + CAP * small + ih * 0.05 > iy + ih {
        // no room for the chance of rain
        return Ok(());
    }
    b.value(
        &format!("d{i}-rain"),
        &format!("{key}.rain"),
        "{value:.0f}%",
        cx,
        top,
        small,
        Some(rain.to_string()),
        "center",
        col * 0.9,
        "text_font",
        true,
        vec![("hide_if_missing", true.into())],
    )?;
    let thick = 2.max(round(ts * 0.3));
    let track = top + CAP * small + ih * 0.04;
    if track + thick as f64 <= iy + ih {
        rain_bar(b, i, key, cx - col * 0.3, track, col * 0.6, thick, rain)?;
    }
    Ok(())
}
