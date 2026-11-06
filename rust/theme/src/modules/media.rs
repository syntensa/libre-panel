//! Modules of pictures, music, the volume, the calendar and games.

use lp_py::num;
use lp_py::Value;

use super::{
    clamp, faded, floor_div, fmax, fmin, mix, pmax, pmin, round, round_to, times_div, Build,
    Fields, Source, CAP, N,
};
use crate::model::Result;

use lp_i18n::t;

/// b.out[-1][key] = value
fn set_last(b: &mut Build, key: &str, value: Value) {
    if let Some(widget) = b.out.last_mut() {
        widget.insert(key.into(), value);
    }
}

/// round(style["radius"] * u / 200): a corner's radius for something `u` large.
fn corner(b: &Build, u: N) -> i64 {
    let radius = b.style.get("radius").map_or(N::I(0), N::of);
    round(times_div(radius, u, 200.0))
}

/// build_image: a picture, or several in turn (`items`: one per line, "photos/*.jpg" a
/// folder).
pub fn build_image(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let items = b.ms("items");
    let pictures: Vec<&str> = lp_py::splitlines(&items)
        .into_iter()
        .map(lp_py::strip)
        .filter(|line| !line.is_empty())
        .collect();
    let radius = if b.st("card") != "none" {
        corner(b, b.frame_u())
    } else {
        0
    };
    let (x, y, w, h) = (b.x, b.y, b.w, b.h);
    let ts = b.ts as f64;
    if pictures.is_empty() {
        let see = b
            .style
            .get("card_opacity")
            .and_then(Value::to_f64)
            .unwrap_or(1.0);
        let frame: Fields = vec![
            ("x", x.into()),
            ("y", y.into()),
            ("w", w.into()),
            ("h", h.into()),
            ("color", faded(&b.c("surface"), see)?.into()),
            ("outline", b.c("line").into()),
            ("radius", radius.into()),
        ];
        b.add("rect", "frame", frame)?;
        let (xf, yf, wf, hf) = (x as f64, y as f64, w as f64, h as f64);
        let icon = w.min(h) as f64 * 0.28;
        let muted = b.c("text3");
        b.icon(
            "icon",
            "image",
            xf + (wf - icon) / 2.0,
            yf + hf / 2.0 - icon * 0.75,
            icon,
            &muted,
        )?;
        return b.label(
            "hint",
            &t("Add pictures in the settings"),
            xf + wf / 2.0,
            yf + hf / 2.0 + icon * 0.45,
            ts * 0.9,
            None,
            "center",
            wf * 0.9,
        );
    }
    let picture: Fields = vec![
        ("x", x.into()),
        ("y", y.into()),
        ("w", w.into()),
        ("h", h.into()),
        ("src", "".into()),
        ("slides", pictures.join("\n").into()),
        ("seconds", num::max(&Value::int(1), &b.m("seconds"))?),
        ("speed", b.m("speed")),
        ("fit", "cover".into()),
        ("radius", radius.into()),
    ];
    b.add("image", "picture", picture)?;
    let title = b.ms("title");
    if !title.is_empty() {
        let shade = round(h as f64 * 0.4);
        let fields: Fields = vec![
            ("x", x.into()),
            ("y", (y + h - shade).into()),
            ("w", w.into()),
            ("h", shade.into()),
            ("color", "#00000000".into()),
            ("color2", faded(&b.c("bg2"), 0.8)?.into()),
            ("radius", radius.into()),
        ];
        b.add("rect", "shade", fields)?;
        let text = b.c("text");
        let cap = (y + h - b.pad * 2) as f64 - CAP * ts;
        let width = (w - b.pad * 4) as f64;
        b.label(
            "title",
            &title,
            (x + b.pad * 2) as f64,
            cap,
            ts * 1.05,
            Some(text),
            "left",
            width,
        )?;
    }
    Ok(())
}

/// build_music: what is playing: the cover, title, artist and how far it is.
pub fn build_music(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("accent");
    let a = b.aspect();
    let (ix, iy, iw, ih) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    let (ts, pad) = (b.ts as f64, b.pad as f64);
    b.needs = "media.title".into();
    if a >= 1.45 {
        // cover | words
        let d = N::I(b.ih);
        cover(b, ix, iy, d)?;
        let df = d.f();
        song(
            b,
            ix + df + pad * 1.4,
            iy,
            iw - df - pad * 1.4,
            ih,
            &color,
            true,
        )?;
    } else if (a <= 0.72 || b.large()) && b.ih as f64 > ts * 12.0 {
        // cover over words
        let d = pmin(b.iw, ih * 0.55);
        let df = d.f();
        cover(b, ix + (iw - df) / 2.0, iy, d)?;
        song(b, ix, iy + df + pad, iw, ih - df - pad, &color, true)?;
    } else {
        b.icon("icon", "music", ix, iy - ts * 0.1, ts * 1.5, &color)?;
        song(b, ix, iy + ts * 2.2, iw, ih - ts * 2.2, &color, false)?;
    }
    b.needs = "!media.title".into();
    let icon = b.iw.min(b.ih) as f64 * 0.3;
    let muted = b.c("text3");
    b.icon(
        "idle-icon",
        "music",
        ix + (iw - icon) / 2.0,
        iy + ih / 2.0 - icon * 0.8,
        icon,
        &muted,
    )?;
    b.label(
        "idle",
        &t("Nothing playing"),
        ix + iw / 2.0,
        iy + ih / 2.0 + icon * 0.45,
        ts,
        None,
        "center",
        iw,
    )?;
    b.needs = String::new();
    Ok(())
}

/// _cover: the cover's frame, a note while there is no cover, and the cover.
fn cover(b: &mut Build, x: f64, y: f64, d: N) -> Result<()> {
    let radius = corner(b, d);
    let df = d.f();
    let frame: Fields = vec![
        ("x", round(x).into()),
        ("y", round(y).into()),
        ("w", round(d).into()),
        ("h", round(d).into()),
        ("color", mix(&b.c("track"), &b.c("surface"), 0.3)?.into()),
        ("radius", radius.into()),
    ];
    b.add("rect", "cover-frame", frame)?;
    let icon = df * 0.36;
    let muted = b.c("text3");
    b.icon(
        "cover-icon",
        "music",
        x + (df - icon) / 2.0,
        y + (df - icon) / 2.0,
        icon,
        &muted,
    )?;
    let image: Fields = vec![
        ("x", round(x).into()),
        ("y", round(y).into()),
        ("w", round(d).into()),
        ("h", round(d).into()),
        ("src", "@media.cover".into()),
        ("fit", "cover".into()),
        ("radius", radius.into()),
    ];
    b.add("image", "cover", image)?;
    Ok(())
}

/// end_time: what the time at the right of a track's progress shows: its length, or (`time`
/// "remaining") what is left of it with a minus in front.
pub fn end_time(b: &Build) -> (&'static str, &'static str) {
    if b.ms("time") == "remaining" {
        ("media.remaining", "-{value:clock}")
    } else {
        ("media.duration", "{value:clock}")
    }
}

/// _song: title, artist (album) and the progress, filling the height.
#[allow(clippy::too_many_arguments)]
fn song(b: &mut Build, x: f64, y: f64, w: f64, h: f64, color: &str, mut state: bool) -> Result<()> {
    let ts = b.ts as f64;
    let title = fmin(fmin(ts * 1.9, h * 0.2), w * 0.12);
    let small = ts * 1.05;
    let mut lines = vec![
        ("title", "media.title", title, "text", "display_font"),
        ("artist", "media.artist", small * 1.15, "text2", "text_font"),
    ];
    if h > ts * 11.0 {
        lines.push(("album", "media.album", small, "text3", "text_font"));
    }
    let bar_block = if h > ts * 6.0 { ts * 3.2 } else { 0.0 };
    let needed = |lines: &[(&str, &str, f64, &str, &str)], state: bool| -> f64 {
        let words = lp_py::sum_floats(lines.iter().map(|l| CAP * l.2 + ts * 0.9));
        words + bar_block + if state { CAP * ts + ts * 1.0 } else { 0.0 }
    };
    if needed(&lines, state) > h && lines.len() > 2 {
        lines.pop(); // the album goes first
    }
    if needed(&lines, state) > h {
        state = false;
    }
    let block = needed(&lines, state);
    let mut top = y + fmax(0.0, (h - block) / 2.0);
    if state {
        b.value(
            "state",
            "media.state",
            "{value}",
            x,
            top,
            ts,
            Some(color.to_string()),
            "left",
            w,
            "text_font",
            false,
            vec![("fallback", "".into()), ("hide_if_missing", true.into())],
        )?;
        top += CAP * ts + ts * 1.0;
    }
    for (name, key, size, role, font) in lines {
        b.value(
            name,
            key,
            "{value}",
            x,
            top,
            size,
            Some(b.c(role)),
            "left",
            w,
            font,
            false,
            vec![
                ("fallback", "".into()),
                ("fit", "ellipsis".into()),
                ("hide_if_missing", true.into()),
            ],
        )?;
        top += CAP * size + ts * 0.9;
    }
    if bar_block != 0.0 {
        top += ts * 0.4;
        let bar_h = pmax(3, ts * 0.32).f();
        b.bar(
            "progress",
            "media.progress",
            x,
            top,
            w,
            bar_h,
            color,
            Some(0),
            Some(1),
            "linear",
        )?;
        set_last(b, "hide_if_missing", true.into());
        let times = top + ts * 0.9;
        let muted = b.c("text3");
        b.value(
            "position",
            "media.position",
            "{value:clock}",
            x,
            times,
            ts * 0.95,
            Some(muted.clone()),
            "left",
            w / 2.0,
            "display_font",
            true,
            vec![("hide_if_missing", true.into())],
        )?;
        let (sensor, fmt) = end_time(b);
        b.value(
            "length",
            sensor,
            fmt,
            x + w,
            times,
            ts * 0.95,
            Some(muted),
            "right",
            w / 2.0,
            "display_font",
            true,
            vec![("hide_if_missing", true.into())],
        )?;
    }
    Ok(())
}

/// build_volume: the volume: a speaker, the level in percent, and levels that rise as on a
/// mixer; muted, a crossed speaker and the word. Upright in a tall module.
pub fn build_volume(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("accent");
    let (ix, iy, iw, ih) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    let ts = b.ts as f64;
    let (speaker, crit) = (b.c("text2"), b.c("crit"));
    if b.aspect() <= 0.72 {
        // tall
        let size = fmin(iw * 0.34, ih * 0.12);
        let icon = size * 0.9;
        let cx = ix + iw / 2.0;
        b.icon("icon", "volume", cx - icon / 2.0, iy, icon, &speaker)?;
        set_last(b, "needs", "audio.muted!=1".into());
        b.icon("muted-icon", "mute", cx - icon / 2.0, iy, icon, &crit)?;
        set_last(b, "needs", "audio.muted=1".into());
        let mut top = iy + icon + ts * 0.9;
        b.value(
            "value",
            "audio.volume",
            "{value:.0f}%",
            cx,
            top,
            size,
            None,
            "center",
            iw,
            "display_font",
            true,
            vec![("needs", "audio.muted!=1".into())],
        )?;
        b.label(
            "muted",
            &t("Muted"),
            cx,
            top + CAP * (size - ts) / 2.0,
            ts,
            Some(crit),
            "center",
            iw,
        )?;
        set_last(b, "needs", "audio.muted=1".into());
        top += CAP * size + ts * 1.2;
        let lh = (b.iy + b.ih) as f64 - top;
        return levels(b, ix + iw * 0.2, top, iw * 0.6, lh, &color, true);
    }
    let size = fmin(ih * 0.26, iw * 0.09);
    let label = pmax(8, fmin(size * 0.42, ts * 1.4)).f();
    let icon = size * 0.86;
    let cap = iy;
    let icon_y = cap + CAP * size / 2.0 - icon / 2.0;
    b.icon("icon", "volume", ix, icon_y, icon, &speaker)?;
    set_last(b, "needs", "audio.muted!=1".into());
    b.icon("muted-icon", "mute", ix, icon_y, icon, &crit)?;
    set_last(b, "needs", "audio.muted=1".into());
    let own = b.ms("title");
    let title = if own.is_empty() { t("Volume") } else { own };
    let unit = label * 1.15;
    let (text_font, display_font) = (b.st("text_font"), b.st("display_font"));
    let unit_w = b.width_per_size(&text_font, "%")? * unit;
    let room = iw - icon * 1.4 - b.width_per_size(&display_font, "100")? * size - unit_w;
    b.label(
        "title",
        &title,
        ix + icon * 1.4,
        cap + CAP * (size - label) / 2.0,
        label,
        None,
        "left",
        pmax(8, room - ts).f(),
    )?;
    let right = (b.ix + b.iw) as f64;
    b.value(
        "value",
        "audio.volume",
        "{value:.0f}",
        right - unit_w - size * 0.08,
        cap,
        size,
        None,
        "right",
        0.0,
        "display_font",
        true,
        vec![("needs", "audio.muted!=1".into())],
    )?;
    let mut fields: Fields = vec![
        ("x", round(right).into()),
        ("text", "%".into()),
        ("color", b.c("text3").into()),
        ("align", "right".into()),
        ("tabular", false.into()),
        ("needs", "audio.muted!=1".into()),
    ];
    fields.extend(b.text_fields(unit, cap + CAP * (size - unit), "text_font"));
    b.add("text", "unit", fields)?;
    b.label(
        "muted",
        &t("Muted"),
        right,
        cap + CAP * (size - label) / 2.0,
        label,
        Some(crit),
        "right",
        pmax(8, room).f(),
    )?;
    set_last(b, "needs", "audio.muted=1".into());
    let top = cap + CAP * size + fmax(ts * 0.9, ih * 0.12);
    let lh = (b.iy + b.ih) as f64 - top;
    levels(b, ix, top, iw, lh, &color, false)
}

/// _levels: the volume as levels: segments rising from a fifth to all of the bar's thickness
/// (upright: plain segments up a column).
fn levels(b: &mut Build, x: f64, y: f64, w: f64, h: f64, color: &str, upright: bool) -> Result<()> {
    if w < 8.0 || h < 4.0 {
        return Ok(());
    }
    let (length, across) = if upright { (h, w) } else { (w, h) };
    let per = fmax(5.0, if upright { across * 0.16 } else { across * 0.3 });
    let count = clamp(length / per, 8, 40).to_int();
    let gap = 1.max(round(length / count as f64 * 0.3));
    let fields: Fields = vec![
        ("x", round(x).into()),
        ("y", round(y).into()),
        ("w", 4.max(round(w)).into()),
        ("h", 4.max(round(h)).into()),
        ("sensor", "audio.volume".into()),
        ("min", 0.into()),
        ("max", 100.into()),
        ("color", b.deep(color)?.into()),
        ("color2", color.into()),
        ("background", b.c("track").into()),
        (
            "radius",
            1.max(round(fmin(length / count as f64, across) * 0.12))
                .into(),
        ),
        ("direction", if upright { "up" } else { "right" }.into()),
        ("segments", count.into()),
        ("segment_gap", gap.into()),
        (
            "rising",
            if upright {
                Value::int(0)
            } else {
                Value::Float(0.2)
            },
        ),
        ("glow", round_to(0.5 * b.glow, 2).into()),
        ("glow_radius", 2.max(round(across * 0.12)).into()),
        ("finish", "flat".into()),
        ("needs", "audio.volume".into()),
    ];
    b.add("bar", "levels", fields)?;
    Ok(())
}

/// build_agenda: the next events of one's calendars (`[sensors.calendar]`).
pub fn build_agenda(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("accent");
    let own = b.ms("title");
    let title = if own.is_empty() { t("Agenda") } else { own };
    let top = b.head("calendar", &title, &color, 0.0)?;
    let ts = b.ts as f64;
    let (x, y, w, h) = (
        b.ix as f64,
        b.iy as f64 + top,
        b.iw as f64,
        b.ih as f64 - top,
    );
    let stacked = b.iw < b.ts * 24; // the time above the title, else beside it
    let columns: i64 = if !stacked && b.iw > b.ts * 50 { 2 } else { 1 };
    let gap = b.pad * 2;
    let column_w = (b.iw - (columns - 1) * gap) as f64 / columns as f64;
    let row = ts * if stacked { 3.4 } else { 2.3 };
    let per_column = clamp(floor_div(h, row), 1, 12).to_int();
    let when_w = if stacked {
        0.0
    } else {
        pmin(column_w * 0.36, b.ts * 11).f()
    };
    let (line_color, text3, text) = (b.c("line"), b.c("text3"), b.c("text"));
    for i in 0..per_column * columns {
        let key = format!("calendar.{}", i + 1);
        let (col, line) = (i.div_euclid(per_column), i.rem_euclid(per_column));
        let (cx, cy) = (
            x + col as f64 * (column_w + gap as f64),
            y + line as f64 * row,
        );
        let first = b.out.len();
        let mark = |color: &str| -> Fields {
            vec![
                ("x", round(cx).into()),
                ("color", color.into()),
                ("y", round(cy + ts * 0.15).into()),
                ("w", 2.max(round(ts * 0.2)).into()),
                ("h", round(row - ts * 0.9).into()),
                ("radius", 1.max(round(ts * 0.1)).into()),
            ]
        };
        b.add("rect", &format!("e{i}-mark"), mark(&line_color))?;
        b.add("rect", &format!("e{i}-now"), mark(&color))?;
        set_last(b, "needs", format!("{key}.now").into()); // the one going on is marked
        let inset = ts * 0.8;
        let kw = || -> Fields { vec![("fallback", "".into()), ("fit", "ellipsis".into())] };
        if stacked {
            b.value(
                &format!("e{i}-when"),
                &format!("{key}.when"),
                "{value}",
                cx + inset,
                cy + ts * 0.2,
                ts * 0.95,
                Some(text3.clone()),
                "left",
                column_w - inset,
                "text_font",
                false,
                kw(),
            )?;
            b.value(
                &format!("e{i}-title"),
                &format!("{key}.title"),
                "{value}",
                cx + inset,
                cy + ts * 1.55,
                ts * 1.25,
                Some(text.clone()),
                "left",
                column_w - inset,
                "text_font",
                false,
                kw(),
            )?;
        } else {
            let mid = cy + row / 2.0 - ts * 0.45;
            b.value(
                &format!("e{i}-when"),
                &format!("{key}.when"),
                "{value}",
                cx + inset,
                mid - CAP * ts * 0.5 / 2.0,
                ts * 0.95,
                Some(text3.clone()),
                "left",
                when_w - inset,
                "text_font",
                false,
                kw(),
            )?;
            b.value(
                &format!("e{i}-title"),
                &format!("{key}.title"),
                "{value}",
                cx + when_w,
                mid - CAP * ts * 0.1,
                ts * 1.2,
                Some(text.clone()),
                "left",
                column_w - when_w,
                "text_font",
                false,
                kw(),
            )?;
        }
        for widget in &mut b.out[first..] {
            let own = widget.get("needs").map(Value::str).unwrap_or_default();
            let needs = if own.is_empty() {
                format!("{key}.title")
            } else {
                format!("{key}.title,{own}")
            };
            widget.insert("needs".into(), needs.into());
        }
    }
    let mid_y = y + h / 2.0 - CAP * ts / 2.0;
    let none = t("No upcoming events");
    b.label("none", &none, x + w / 2.0, mid_y, ts, None, "center", w)?;
    set_last(b, "needs", "calendar.events,!calendar.1.title".into());
    let setup = t("No calendar set up");
    b.label("setup", &setup, x + w / 2.0, mid_y, ts, None, "center", w)?;
    set_last(b, "needs", "!calendar.events".into());
    Ok(())
}

/// _played: how long the game has been played, its name before it, on the line of the clock
/// and as small, ending at `right`, where the line has room for it; how much of the line it
/// takes (0: none: then it is one of the readings beside the number).
fn played(b: &mut Build, right: f64) -> Result<f64> {
    let said = t("Played");
    let ts = b.ts as f64;
    let label = ts * 0.8;
    let spacing = 1.max(round(label * 0.14));
    let font = b.st("text_font");
    let label_w = b.width_per_size(&font, &lp_py::upper(&said))? * label
        + (spacing * said.chars().count() as i64) as f64;
    let slot = b.width_per_size(&font, "0:00:00")? * ts * 1.05; // (a long round's)
    let taken = label_w + ts * 0.45 + slot + ts * 1.2;
    let clock_w = b.width_per_size(&font, "00:00")? * ts * 1.05;
    if b.iw as f64 - ts * 3.1 - clock_w - taken < ts * 8.0 {
        return Ok(0.0); // (the game's name, or logo, keeps room)
    }
    let kept = std::mem::replace(&mut b.needs, "game.time".into());
    let x = right - slot;
    let iy = b.iy as f64;
    b.label(
        "played-label",
        &said,
        x - ts * 0.45,
        iy + ts * 0.15 + CAP * ts * 0.25,
        label,
        None,
        "right",
        label_w + ts,
    )?;
    b.value(
        "played",
        "game.time",
        "{value:clock}",
        x,
        iy + ts * 0.15,
        ts * 1.05,
        Some(b.c("text2")),
        "left",
        slot * 1.1,
        "text_font",
        true,
        vec![("hide_if_missing", true.into()), ("smooth", false.into())],
    )?;
    b.needs = kept;
    Ok(taken)
}

/// build_game: frames per second of the game being played (`[sensors.presentmon]`).
pub fn build_game(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("gpu");
    let a = b.aspect();
    let (ix, iy, iw, ih) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    let (ts, pad) = (b.ts as f64, b.pad as f64);
    let text_font = b.st("text_font");
    // the time, small and dim in the corner, where the card has room for it
    let mut clock_w = b.width_per_size(&text_font, "00:00")? * ts * 1.05;
    let mut played_w = 0.0; // (how much of the line the time played takes)
    if b.iw >= b.ts * 14 {
        let muted = b.c("text3");
        b.clock(
            "clock",
            "%H:%M",
            ix + iw,
            iy + ts * 0.15,
            ts * 1.05,
            Some(muted),
            "right",
            clock_w,
            "text_font",
            Vec::new(),
        )?;
        played_w = played(b, ix + iw - clock_w - ts * 1.2)?;
        clock_w += played_w;
    } else {
        clock_w = -ts; // the name has the whole line
    }
    let title = b.ms("title");
    let title_w = iw - ts * 2.1 - clock_w - ts;
    let top = ts * 2.4;
    // the game's logo in place of its name where the line has room for one
    let logo = title.is_empty() && title_w >= ts * 8.0;
    b.needs = if logo {
        "game.fps,!@game.logo"
    } else {
        "game.fps"
    }
    .into();
    b.icon("icon", "gamepad", ix, iy - ts * 0.1, ts * 1.5, &color)?;
    if !title.is_empty() {
        b.label(
            "title",
            &title,
            ix + ts * 2.1,
            iy + ts * 0.15,
            ts * 1.05,
            Some(color.clone()),
            "left",
            title_w,
        )?;
    } else {
        b.value(
            "title",
            "game.app",
            "{value}",
            ix + ts * 2.1,
            iy + ts * 0.15,
            ts * 1.05,
            Some(color.clone()),
            "left",
            title_w,
            "text_font",
            false,
            vec![("fallback", "".into()), ("fit", "ellipsis".into())],
        )?;
    }
    b.needs = "game.fps".into();
    let (x, y, w, h) = (ix, iy + top, iw, ih - top);
    let graph = b.large() || a >= 3.0 || (a <= 0.72 && h > ts * 12.0);
    let stacked = b.large() || a <= 0.72; // the history under the number
    let number_h = if graph && stacked { h * 0.55 } else { h };
    let number_w = if !(graph && !stacked) { w } else { w * 0.45 };
    let size = fmin(number_h * 0.62, number_w * 0.34);
    let lift = if number_h > ts * 6.0 { ts * 0.6 } else { 0.0 };
    let cap = y + (number_h - CAP * size) / 2.0 - lift;
    b.value(
        "fps",
        "game.fps",
        "{value:.0f}",
        x,
        cap,
        size,
        None,
        "left",
        number_w * 0.62,
        "display_font",
        true,
        vec![
            ("glow", round_to(0.3 * b.glow, 2).into()),
            ("glow_radius", 2.max(round(size / 14.0)).into()),
        ],
    )?;
    if logo {
        // on the name's line, as large as the room above the number lets it be, and then no
        // wider than the number
        b.needs = "game.fps,@game.logo".into();
        let highest = fmax(iy - ts * 0.6, b.y as f64 + ts * 0.8); // (clear of the card's edge)
        let logo_h = fmax(
            ts * 2.2,
            fmin(fmin(ts * 4.5, ih * 0.16), cap - ts - highest),
        );
        let logo_y = fmax(highest, iy + ts * 0.6 - logo_h / 2.0);
        let mut logo_w = ts * 2.1 + title_w;
        if logo_h > ts * 2.4 {
            logo_w = fmin(logo_w, number_w * 0.62);
        }
        let image: Fields = vec![
            ("x", round(b.ix).into()),
            ("y", round(logo_y).into()),
            ("src", "@game.logo".into()),
            ("w", round(logo_w).into()),
            ("h", round(logo_h).into()),
            ("fit", "contain".into()),
            ("align", "left".into()),
        ];
        b.add("image", "logo", image)?;
        b.needs = "game.fps".into();
    }
    let unit_x = x + number_w * 0.64;
    b.label(
        "unit",
        "FPS",
        unit_x,
        cap + CAP * size - CAP * ts,
        ts,
        Some(color.clone()),
        "left",
        number_w * 0.36,
    )?;
    if number_h > ts * 6.5 {
        // what holds the frame rate down, and the longest frame since the last reading
        let mut rows = vec![
            ("low", "game.low", "{value:.0f}", t("1% low")),
            (
                "frametime",
                "game.frametime",
                "{value:.1f} ms",
                t("Frame time"),
            ),
            ("limiter", "game.limiter", "", t("Limited by")),
            (
                "peak",
                "game.frametime_peak",
                "{value:.0f} ms",
                t("Longest frame"),
            ),
        ];
        if played_w == 0.0 {
            // (not on the clock's line: the last of the readings, where there is room)
            rows.push(("played", "game.time", "{value:clock}", t("Played")));
        }
        let room_w = number_w * 0.36;
        // the widest name sets one size for all, lettered with 14 % of their size between
        let mut per_size = f64::NEG_INFINITY;
        for (_, _, _, text) in &rows {
            let v = b.width_per_size(&text_font, &lp_py::upper(text))?
                + 0.14 * (text.chars().count() as i64 - 1) as f64;
            if per_size == f64::NEG_INFINITY || v > per_size {
                per_size = v;
            }
        }
        let tries: &[i64] = if room_w >= ts * 12.0 { &[2, 1] } else { &[1] };
        let (mut cols, mut lines, mut col_w) = (1, 1, 0.0);
        let (mut step, mut value_size, mut width, mut label_size) = (0.0, 0.0, 0.0, 0.0);
        for &c in tries {
            cols = c;
            lines = (rows.len() as f64 / cols as f64).ceil() as i64;
            col_w = room_w / cols as f64;
            step = fmax(ts * 2.4, fmin(number_h * 0.9 / lines as f64, col_w * 0.6));
            value_size = fmax(ts * 1.05, step * 0.4);
            width = col_w - ts; // a gutter between the columns, and before the edge
            label_size = fmin(
                fmax(ts * 0.85, fmin(step * 0.17, ts * 1.2)),
                width / per_size,
            );
            if label_size >= ts * 0.6 {
                break;
            }
        }
        let compact = step <= ts * 2.4; // (a small card: the rows from the number's top)
        let block = step * (lines - 1) as f64 + label_size * 1.3 + CAP * value_size;
        let first = if compact {
            cap
        } else {
            y + (number_h - block) / 2.0
        };
        let text2 = b.c("text2");
        for (i, (name, key, fmt, text)) in rows.iter().enumerate() {
            let i = i as i64;
            let line = first + i.div_euclid(cols) as f64 * step;
            let at = unit_x + i.rem_euclid(cols) as f64 * col_w;
            let below = line + label_size * 1.3; // the value's cap line, under its name
            let end = if compact {
                line + CAP * ts * 1.2
            } else {
                below + CAP * value_size
            };
            if end > y + number_h {
                break;
            }
            let label_name = format!("{name}-label");
            if *name != "limiter" {
                b.needs = if *name == "played" {
                    "game.fps,game.time"
                } else {
                    "game.fps"
                }
                .into();
                b.label(&label_name, text, at, line, label_size, None, "left", width)?;
                let mut kw: Fields = vec![("hide_if_missing", true.into())];
                if *name == "played" {
                    kw.push(("smooth", false.into()));
                }
                b.value(
                    name,
                    key,
                    fmt,
                    at,
                    below,
                    value_size,
                    Some(text2.clone()),
                    "left",
                    width,
                    "display_font",
                    true,
                    kw,
                )?;
                b.needs = "game.fps".into();
                continue;
            }
            b.needs = "game.fps,game.limiter".into();
            b.label(&label_name, text, at, line, label_size, None, "left", width)?;
            for (what, said) in [
                ("gpu", "GPU".to_string()),
                ("cpu", "CPU".to_string()),
                ("cap", t("Frame cap")),
            ] {
                b.needs = format!("game.fps,game.limiter={what}");
                let mut fields: Fields = vec![
                    ("x", round(at).into()),
                    ("text", said.into()),
                    ("color", text2.clone().into()),
                    ("max_width", round(width).into()),
                    ("tabular", false.into()),
                ];
                fields.extend(b.text_fields(value_size, below, "display_font"));
                b.add("text", &format!("limiter-{what}"), fields)?;
            }
            b.needs = "game.fps".into();
        }
        // the rows say what it is
        b.out
            .retain(|widget| widget.get("id").map(Value::str).as_deref() != Some("unit"));
        b.label(
            "unit",
            "FPS",
            x,
            cap + CAP * size + ts * 0.8,
            ts,
            Some(color.clone()),
            "left",
            number_w * 0.6,
        )?;
    }
    if graph {
        let (gx, gy, gw, gh, clip);
        if stacked {
            // under the numbers from one edge of the card to the other, inside its line
            let frame = b.frame_u();
            let edge = if b.card_on {
                1.max(round(frame.f() / 160.0))
            } else {
                0
            };
            let radius = if b.card_on { corner(b, frame) } else { 0 };
            (gx, gy, gw) = (
                (b.x + edge) as f64,
                y + number_h + pad,
                (b.w - 2 * edge) as f64,
            );
            // (its fill fades out above the corners)
            gh = (b.y + b.h - edge) as f64 - radius as f64 / 2.0 - gy;
            clip = true;
        } else {
            gx = x + number_w + pad;
            (gy, gw, gh) = (y, x + w - gx, h);
            clip = false;
        }
        // from 0 to the module's top: a dip shows as deep as it was; with a top of 0 its own,
        // in steps of 30 fps
        let given = b.m("top");
        let top = num::gt(&given, &Value::int(0))?.then_some(given);
        let unit = N::I(b.u.min(20 * b.ts));
        b.graph(
            "history",
            "game.fps",
            gx,
            gy,
            gw,
            gh,
            &color,
            Some(0),
            None,
            "linear",
            true,
            unit,
        )?;
        let step = if top.is_some() { 0 } else { 30 };
        if let Some(top) = top {
            set_last(b, "max", top);
        }
        // every frame's rate as measured
        set_last(b, "clip", clip.into());
        set_last(b, "trace", "game.fps_trace".into());
        set_last(b, "range_step", step.into());
    }
    let icon = b.iw.min(b.ih) as f64 * 0.3;
    let muted = b.c("text3");
    // a game found, its frame rate not yet there: its logo where it has one
    for (name, needs) in [
        ("idle-icon", "!game.fps,!game.name"),
        ("idle-icon-game", "!game.fps,game.name,!@game.logo"),
    ] {
        b.needs = needs.into();
        b.icon(
            name,
            "gamepad",
            ix + (iw - icon) / 2.0,
            iy + ih / 2.0 - icon * 0.8,
            icon,
            &muted,
        )?;
    }
    b.needs = "!game.fps,game.name,@game.logo".into();
    let image: Fields = vec![
        ("x", round(b.ix).into()),
        ("y", round(iy + ih / 2.0 - icon * 0.8).into()),
        ("w", round(b.iw).into()),
        ("h", round(icon).into()),
        ("src", "@game.logo".into()),
        ("fit", "contain".into()),
    ];
    b.add("image", "idle-logo", image)?;
    let line = iy + ih / 2.0 + icon * 0.45;
    b.needs = "!game.fps,!game.name".into();
    let idle = t("No game running");
    b.label("idle", &idle, ix + iw / 2.0, line, ts, None, "center", iw)?;
    // game mode found a game whose frame rate has not come (yet): its name
    b.needs = "!game.fps,game.name".into();
    b.value(
        "idle-game",
        "game.name",
        "{value}",
        ix + iw / 2.0,
        line,
        ts * 1.05,
        Some(b.c("text2")),
        "center",
        iw,
        "text_font",
        false,
        vec![("fallback", "".into()), ("fit", "ellipsis".into())],
    )?;
    let wait = t("No frame rate yet");
    b.label(
        "idle-wait",
        &wait,
        ix + iw / 2.0,
        line + ts * 1.9,
        ts * 0.85,
        None,
        "center",
        iw,
    )?;
    b.needs = String::new();
    Ok(())
}
