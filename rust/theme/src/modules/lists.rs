//! Modules of lists and numbers: the title, temperatures, cores, drives, processes, the
//! dashboard, the network's details, the ping, a very large number, power, the timer and the
//! UPS.

use lp_py::num;
use lp_py::Value;

use super::{
    clamp, default_items, floor_div, fmax, fmin, heat_rules, mix, pmax, pmin, round, round_to,
    Build, Fields, Source, CAP, N,
};
use crate::model::Result;

use lp_i18n::t;

/// `text or other`: the module's text, or `other` where it has none.
fn or(text: String, other: impl FnOnce() -> String) -> String {
    if text.is_empty() {
        other()
    } else {
        text
    }
}

/// b.out[-1]["needs"] = needs: the last widget shown only with that reading.
fn last_needs(b: &mut Build, needs: &str) {
    if let Some(widget) = b.out.last_mut() {
        widget.insert("needs".into(), needs.into());
    }
}

/// build_text: a title with the accent line under it.
pub fn build_text(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let text = or(b.ms("text"), || "Libre Panel".into());
    let (ix, iy, iw, ih) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    let size = fmin(
        ih * 0.5,
        iw / pmax(2, text.chars().count() as f64 * 0.55).f(),
    );
    let line = 2.max(round(size * 0.07));
    let block = CAP * size + size * 0.25 + line as f64;
    let top = iy + (ih - block) / 2.0;
    let mut fields: Fields = vec![
        ("x", round(b.ix).into()),
        ("text", text.into()),
        ("color", b.c("text").into()),
        ("max_width", round(b.iw).into()),
    ];
    fields.extend(b.text_fields(size, top, "display_font"));
    b.add("text", "text", fields)?;
    let rect: Fields = vec![
        ("x", round(ix + size * 0.04).into()),
        ("y", round(top + CAP * size + size * 0.25).into()),
        ("w", round(pmin(b.iw, size * 1.6)).into()),
        ("h", line.into()),
        ("color", b.tint("accent").into()),
        ("radius", line.div_euclid(2).into()),
        ("glow", round_to(0.7 * b.glow, 2).into()),
        ("glow_radius", 2.max(line * 3).into()),
    ];
    b.add("rect", "line", rect)?;
    Ok(())
}

/// build_temps: every temperature as a bar, CPU and GPU first; the fans too where there is
/// room, in a big box each one turning with its speed (as Lumen's).
pub fn build_temps(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("accent");
    let title = or(b.ms("title"), || t("Temperatures"));
    let top = b.head("temperature", &title, &color, 0.0)?;
    let items = or(b.ms("items"), || default_items("temps"));
    let fans = or(b.ms("fan_items"), || default_items("fans"));
    let count = num::max(&Value::int(0), &b.m("fans"))?;
    let (ts, pad) = (b.ts as f64, b.pad as f64);
    let (x, y, w, h) = (
        b.ix as f64,
        b.iy as f64 + top,
        b.iw as f64,
        b.ih as f64 - top,
    );
    let temps = |b: &Build| -> Fields {
        vec![
            ("min", 20.into()),
            ("max", 100.into()),
            ("color_rules", heat_rules(b, 75, 88)),
            ("format", "{value:.0f}°C".into()),
            ("empty", t("No temperature readings").into()),
        ]
    };
    // (the fans' list as {**fan_list, "format": "{value:.0f}"}, after min and max)
    let fan_list = || -> Fields {
        vec![
            ("min", 0.into()),
            ("max", 3000.into()),
            ("format", "{value:.0f}".into()),
            ("empty", t("No fan readings").into()),
            ("uppercase", true.into()),
            ("max_items", count.clone()),
            ("hide_unused", true.into()),
        ]
    };
    let a = b.aspect();
    let rows_or_bars = if h > ts * 4.5 { "bars" } else { "rows" };
    if !count.truthy() {
        b.listing(
            "temps",
            &items,
            x,
            y,
            w,
            h,
            &color,
            rows_or_bars,
            0.0,
            temps(b),
        )?;
    } else if (a >= 3.0 && b.iw > b.ts * 40) || (a >= 1.8 && h > ts * 5.5) {
        // long or wide: the temperatures | the fans turning
        let width = (w - pad) * if a >= 3.0 { 0.6 } else { 0.5 };
        let style = if h > ts * 6.5 * if a >= 3.0 { 2.0 } else { 1.0 } {
            "bars"
        } else {
            "rows"
        };
        b.listing(
            "temps",
            &items,
            x,
            y,
            width,
            h,
            &color,
            style,
            0.0,
            temps(b),
        )?;
        let room = w - width - pad; // as many as are readable: three lines, one beside another
        let size = 7.max(round(clamp(fmin(room / 6.3, h / 7.5), 7, ts * 1.15)));
        let fields = fan_list();
        b.listing(
            "fans",
            &fans,
            x + width + pad,
            y,
            room,
            h,
            &color,
            "fans",
            size as f64,
            fields,
        )?;
    } else if (b.large() || a <= 0.72) && h > ts * 11.0 {
        // big: the fans turning below their numbers, as Lumen's where two fit side by side
        let size = 7.max(round(clamp(w / 12.5, 7, ts * 1.15)));
        let line = round(size as f64 * 1.9) + size;
        let beside = floor_div(w, round(size as f64 * 1.9) as f64 + size as f64 * 4.2);
        let side_by_side = 1.max(beside as i64);
        // -(-count // side_by_side): the lines that hold them
        let zero = Value::int(0);
        let wanted = num::sub(
            &zero,
            &num::floor_div(&num::sub(&zero, &count)?, &Value::int(side_by_side))?,
        )?;
        let fit = floor_div(h * 0.5 + size as f64 * 0.6, line as f64);
        let lines = num::max(
            &Value::int(1),
            &num::min(&Value::int(4), &num::min(&wanted, &Value::Float(fit))?)?,
        )?;
        let lines = lines.to_f64().unwrap_or(1.0) as i64;
        let fan_h = (lines * line) as f64 - size as f64 * 0.6 + 1.0;
        b.listing(
            "temps",
            &items,
            x,
            y,
            w,
            h - fan_h - pad * 1.5,
            &color,
            "bars",
            0.0,
            temps(b),
        )?;
        let fields = fan_list();
        b.listing(
            "fans",
            &fans,
            x,
            y + h - fan_h,
            w,
            fan_h,
            &color,
            "fans",
            size as f64,
            fields,
        )?;
    } else {
        b.listing(
            "temps",
            &items,
            x,
            y,
            w,
            h,
            &color,
            rows_or_bars,
            0.0,
            temps(b),
        )?;
    }
    Ok(())
}

/// build_cores: the load of every core, columns side by side or tiles; the whole
/// processor's beside the title and its history when big, unless `total` is off.
pub fn build_cores(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("cpu");
    let total = b.m("total").truthy();
    let ts = b.ts as f64;
    let title = or(b.ms("title"), || t("CPU cores"));
    let top = b.head("cpu", &title, &color, ts * 3.6)?;
    if total {
        b.value(
            "value",
            "cpu.load",
            "{value:.0f}%",
            (b.ix + b.iw) as f64,
            b.iy as f64,
            ts * 1.6,
            None,
            "right",
            b.iw as f64 * 0.3,
            "display_font",
            true,
            Vec::new(),
        )?;
    }
    let (x, y, w, h) = (
        b.ix as f64,
        b.iy as f64 + top,
        b.iw as f64,
        b.ih as f64 - top,
    );
    // the physical cores where the processor says which threads are one, else the threads;
    // every thread where the module asks for them
    let shown: &[(&str, &str, &str)] = if b.m("threads").truthy() {
        &[("cores", "cpu.core.*.load", "")]
    } else {
        &[
            ("cores", "cpu.pcore.*.load", "cpu.pcore.1.load"),
            ("threads", "cpu.core.*.load", "!cpu.pcore.1.load"),
        ]
    };
    let big = b.large() && h > ts * 12.0;
    if total && big {
        let cores_h = h * 0.6;
        for &(name, items, needs) in shown {
            let kw: Fields = vec![("needs", needs.into())];
            b.listing(
                name,
                items,
                x,
                y,
                w,
                cores_h,
                &color,
                "columns",
                ts * 1.1,
                kw,
            )?;
        }
        let gy = y + cores_h + b.pad as f64;
        let gh = (b.iy + b.ih) as f64 - gy;
        b.graph(
            "history",
            "cpu.load",
            x,
            gy,
            w,
            gh,
            &color,
            Some(0),
            Some(100),
            "linear",
            true,
            N::I(0),
        )?;
    } else {
        // (a big one without the history: its columns the whole height)
        let (style, size) = if b.aspect() >= 1.45 || big {
            ("columns", 1.1)
        } else {
            ("cells", 1.4)
        };
        for &(name, items, needs) in shown {
            let kw: Fields = vec![("needs", needs.into())];
            b.listing(name, items, x, y, w, h, &color, style, ts * size, kw)?;
        }
    }
    Ok(())
}

/// build_drives: every drive, how full and how much is free; reading and writing when big.
pub fn build_drives(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("disk");
    let title = or(b.ms("title"), || t("Drives"));
    let top = b.head("disk", &title, &color, 0.0)?;
    let ts = b.ts as f64;
    let (x, y, w) = (b.ix as f64, b.iy as f64 + top, b.iw as f64);
    let mut h = b.ih as f64 - top;
    let rates = b.large() && h > ts * 12.0;
    if rates {
        h -= ts * 3.4;
    }
    let items = or(b.ms("items"), || default_items("drives"));
    let kw: Fields = vec![
        ("detail", "free".into()),
        ("detail_format", t("{value:.0f} GB free").into()),
        ("format", "{value:.0f}%".into()),
        ("color_rules", heat_rules(b, 85, 95)),
        ("uppercase", false.into()),
    ];
    b.listing("drives", &items, x, y, w, h, &color, "bars", 0.0, kw)?;
    if rates {
        let cap = (b.iy + b.ih) as f64 - CAP * ts * 1.3;
        let half = w / 2.0;
        let rates = [
            ("disk.read", "download", "read"),
            ("disk.write", "upload", "write"),
        ];
        for (i, (key, icon, name)) in rates.into_iter().enumerate() {
            let cx = x + i as f64 * half;
            let muted = b.c("text3");
            b.icon(
                &format!("{name}-icon"),
                icon,
                cx,
                cap + CAP * ts * 0.65 - ts * 0.7,
                ts * 1.4,
                &muted,
            )?;
            b.value(
                name,
                key,
                "{value:bytes}/s",
                cx + ts * 1.9,
                cap,
                ts * 1.3,
                None,
                "left",
                half - ts * 2.2,
                "display_font",
                true,
                vec![("hide_if_missing", true.into())],
            )?;
        }
    }
    Ok(())
}

/// build_processes: the programs that use the most processor time (or memory).
pub fn build_processes(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let memory = b.ms("sort") == "memory";
    let color = b.tint(if memory { "mem" } else { "cpu" });
    let ts = b.ts as f64;
    let title = or(b.ms("title"), || t("Processes"));
    let top = b.head("list", &title, &color, ts * 3.4)?;
    let muted = b.c("text3");
    b.label(
        "sort",
        if memory { "RAM" } else { "CPU" },
        (b.ix + b.iw) as f64,
        b.iy as f64 + ts * 0.15,
        ts,
        Some(muted),
        "right",
        0.0,
    )?;
    let (x, y, w, h) = (
        b.ix as f64,
        b.iy as f64 + top,
        b.iw as f64,
        b.ih as f64 - top,
    );
    let bars = (b.large() || b.aspect() <= 0.72) && h > ts * 10.0;
    // rows a little smaller than the bars, so four fit a short card; a narrow card keeps them
    // in one column, the few that matter large enough to read
    let kw: Fields = vec![
        (
            "columns",
            Value::int(if !bars && b.iw < b.ts * 30 { 1 } else { 0 }),
        ),
        ("format", "{value:.1f}%".into()),
        ("min", 0.into()),
        ("max", 0.into()),
        ("uppercase", false.into()),
        ("empty", t("Waiting for the first count …").into()),
    ];
    let items = if memory {
        "proc.mem.*.value"
    } else {
        "proc.cpu.*.value"
    };
    let (style, size) = if bars { ("bars", 1.3) } else { ("rows", 1.15) };
    b.listing("processes", items, x, y, w, h, &color, style, ts * size, kw)
}

/// build_values: readings of one's choice as tiles, a small dashboard.
pub fn build_values(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("accent");
    let ts = b.ts as f64;
    let (mut y, mut h) = (b.iy as f64, b.ih as f64);
    let title = b.ms("title");
    if !title.is_empty() {
        let top = b.head("grid", &title, &color, 0.0)?;
        (y, h) = (y + top, h - top);
    }
    let items = or(b.ms("items"), || default_items("values"));
    let tiles = if b.aspect() < 3.0 || b.ih > b.ts * 6 {
        "cells"
    } else {
        "rows"
    };
    let kw: Fields = vec![
        ("levels", false.into()),
        (
            "background",
            mix(&b.c("track"), &b.c("surface"), 0.35)?.into(),
        ),
        ("format", "auto".into()),
        ("empty", t("Add readings in the settings").into()),
    ];
    let (x, w) = (b.ix as f64, b.iw as f64);
    b.listing("values", &items, x, y, w, h, &color, tiles, ts * 2.4, kw)
}

/// build_netinfo: address, ping and today's traffic; the ping's history when big.
pub fn build_netinfo(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("net");
    let ts = b.ts as f64;
    let title = or(b.ms("title"), || t("Network"));
    let top = b.head("network", &title, &color, 0.0)?;
    // the names in full where the rows are wide; short where they would be cut
    let narrow = b.iw < b.ts * 30;
    let name = |short: &str, long: &str| if narrow { t(short) } else { t(long) };
    let names = [
        (
            "net.ip",
            if narrow {
                "IP".to_string()
            } else {
                t("IP address")
            },
        ),
        ("net.ping", t("Ping")),
        ("net.down", name("Down", "Download")),
        ("net.up", name("Up", "Upload")),
        ("net.today.down", name("Received", "Received today")),
        ("net.today.up", name("Sent", "Sent today")),
    ];
    let items = names
        .iter()
        .map(|(key, name)| format!("{key} = {name}"))
        .collect::<Vec<_>>()
        .join("\n");
    let (x, y, w, h) = (
        b.ix as f64,
        b.iy as f64 + top,
        b.iw as f64,
        b.ih as f64 - top,
    );
    let graph = (b.large() || b.aspect() <= 0.72) && h > ts * 14.0;
    let rows_h = if graph { h * 0.62 } else { h };
    let kw: Fields = vec![("format", "auto".into())];
    b.listing(
        "details",
        &items,
        x,
        y,
        w,
        rows_h,
        &color,
        "rows",
        ts * 1.25,
        kw,
    )?;
    if graph {
        let mut gy = y + rows_h + b.pad as f64;
        b.label(
            "ping-title",
            &t("Ping"),
            x,
            gy,
            ts * 0.9,
            None,
            "left",
            w * 0.5,
        )?;
        gy += ts * 1.6;
        let gh = (b.iy + b.ih) as f64 - gy;
        b.graph(
            "ping-history",
            "net.ping",
            x,
            gy,
            w,
            gh,
            &color,
            Some(0),
            None,
            "linear",
            true,
            N::I(0),
        )?;
        last_needs(b, "net.ping");
    }
    Ok(())
}

/// A row of _pairs: (sensor, format, name).
type Pair = (String, &'static str, String);

/// _pairs: readings as rows of a name and a value, as many as fit.
fn pairs(b: &mut Build, pairs: &[Pair], x: f64, y: f64, w: f64, h: f64) -> Result<()> {
    let ts = b.ts as f64;
    let count = (pairs.len() as i64).min(floor_div(h, ts * 1.9) as i64);
    if count < 1 || w < ts * 5.0 {
        return Ok(());
    }
    let row = fmin(h / count as f64, ts * 3.0);
    let size = fmin(fmin(row * 0.55, ts * 1.5), w * 0.11);
    let top = y + (h - row * count as f64) / 2.0;
    for (i, (sensor, fmt, name)) in pairs.iter().take(count as usize).enumerate() {
        let base = top + i as f64 * row + (row - CAP * size) / 2.0;
        b.label(
            &format!("r{i}-label"),
            name,
            x,
            base + CAP * (size - ts) / 2.0,
            ts,
            None,
            "left",
            w * 0.45,
        )?;
        b.value(
            &format!("r{i}"),
            sensor,
            fmt,
            x + w,
            base,
            size,
            None,
            "right",
            w * 0.53,
            "display_font",
            true,
            vec![("fallback", "--".into()), ("fit", "ellipsis".into())],
        )?;
    }
    Ok(())
}

/// The big number of _number_rows_history.
#[allow(clippy::too_many_arguments)]
fn number(b: &mut Build, sensor: &str, fmt: &str, x: f64, y: f64, w: f64, h: f64) -> Result<()> {
    let size = fmin(h * 0.62, w * 0.3);
    b.value(
        "value",
        sensor,
        fmt,
        x,
        y + (h - CAP * size) / 2.0,
        size,
        None,
        "left",
        w,
        "display_font",
        true,
        vec![("fallback", "--".into())],
    )
}

/// The number's history of _number_rows_history, shown only with its reading.
#[allow(clippy::too_many_arguments)]
fn history(b: &mut Build, sensor: &str, color: &str, x: f64, y: f64, w: f64, h: f64) -> Result<()> {
    b.graph(
        "history",
        sensor,
        x,
        y,
        w,
        h,
        color,
        Some(0),
        None,
        "linear",
        true,
        N::I(0),
    )?;
    last_needs(b, sensor);
    Ok(())
}

/// _number_rows_history: a big number under the head; beside or under it the `rows` where
/// there is room, and the number's history (ping, power).
#[allow(clippy::too_many_arguments)]
fn number_rows_history(
    b: &mut Build,
    sensor: &str,
    fmt: &str,
    rows: &[Pair],
    color: &str,
    top: f64,
    with_history: bool,
) -> Result<()> {
    let (ts, pad) = (b.ts as f64, b.pad as f64);
    let (x, y, w, h) = (
        b.ix as f64,
        b.iy as f64 + top,
        b.iw as f64,
        b.ih as f64 - top,
    );
    let a = b.aspect();
    let (tall, wide) = (a <= 0.72, a >= 2.0);
    let stacked = (b.large() && !wide) || tall;
    let rows_h = (rows.len() as i64 * b.ts) as f64 * 2.2;
    if stacked {
        let number_h = fmin(h * 0.36, b.iw as f64 * 0.4);
        number(b, sensor, fmt, x, y, w, number_h)?;
        let rest_y = y + number_h + pad * 0.5;
        let rest = (b.iy + b.ih) as f64 - rest_y;
        let rows_in = fmin(rows_h, if with_history { rest * 0.5 } else { rest });
        pairs(b, rows, x, rest_y, w, rows_in)?;
        if with_history && rest - rows_in > ts * 3.0 {
            let gy = rest_y + rows_in + pad * 0.5;
            history(b, sensor, color, x, gy, w, rest - rows_in - pad * 0.5)?;
        }
    } else if wide {
        let big = b.large() || a >= 3.2;
        let number_w = w * if big && !rows.is_empty() && with_history {
            0.3
        } else {
            0.42
        };
        number(b, sensor, fmt, x, y, number_w, h)?;
        let rest_x = x + number_w + pad;
        let rest_w = (b.ix + b.iw) as f64 - rest_x;
        if !rows.is_empty() && with_history && big {
            let rows_w = rest_w * 0.55;
            pairs(b, rows, rest_x, y, rows_w, h)?;
            let gx = rest_x + rows_w + pad * 1.5;
            history(b, sensor, color, gx, y, rest_w - rows_w - pad * 1.5, h)?;
        } else if !rows.is_empty() {
            pairs(b, rows, rest_x + pad, y, rest_w - pad, h)?;
        } else if with_history {
            history(b, sensor, color, rest_x, y, rest_w, h)?;
        }
    } else {
        number(b, sensor, fmt, x, y, w, h)?;
    }
    Ok(())
}

/// ping_key: the reading of the ping a module shows: its `target` is which of the hosts of
/// `[sensors.psutil] ping` (1, the first, to 4).
pub fn ping_key(target: &str) -> String {
    let n = lp_py::strip(target);
    if ["2", "3", "4"].contains(&n) {
        format!("net.ping.{n}")
    } else {
        "net.ping".into()
    }
}

/// build_ping: how long the internet (or a host of `[sensors.psutil] ping`) takes to
/// answer, with its history and the share of tries without an answer.
pub fn build_ping(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("net");
    let key = ping_key(&b.ms("target"));
    let title = or(b.ms("title"), || t("Ping"));
    let top = b.head("signal", &title, &color, 0.0)?;
    let rows: [Pair; 2] = [
        (format!("{key}.lost"), "{value:.0f}%", t("Lost")),
        (format!("{key}.host"), "{value}", t("Host")),
    ];
    number_rows_history(b, &key, "{value:.0f} ms", &rows, &color, top, true)
}

/// build_huge: one reading as large as the card allows, its name small above it, in the
/// warning and the critical colour from 80 and 90 °C (a share: 85 and 95 %).
pub fn build_huge(b: &mut Build, src: &Source) -> Result<()> {
    b.card(true)?;
    let (mut sensor, mut fmt) = (src.load.to_string(), src.fmt.to_string());
    let mut title = or(b.ms("title"), || src.label.clone());
    if b.ms("source") == "sensor" {
        sensor = or(b.ms("sensor"), || "cpu.temp".into());
        fmt = or(b.ms("format"), || "{value:.0f}°".into());
        title = or(b.ms("title"), || sensor.clone());
    }
    let (ix, iy, iw, ih) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    let label = fmax(7.0, fmin(ih * 0.13, b.ts as f64 * 1.8));
    let muted = b.c("text2");
    b.label(
        "title",
        &title,
        ix + iw / 2.0,
        iy,
        label,
        Some(muted),
        "center",
        iw,
    )?;
    let top = iy + label * (CAP + 0.7);
    let h = (b.iy + b.ih) as f64 - top;
    let size = fmin(h * 0.86 / CAP, iw / 2.3); // four characters: "100%", "72°C"
    let mut rules = Value::List(Vec::new());
    for (mark, warn, crit) in [("°", 80, 90), ("%", 85, 95)] {
        if fmt.contains(mark) {
            rules = heat_rules(b, warn, crit);
        }
    }
    b.value(
        "value",
        &sensor,
        &fmt,
        ix + iw / 2.0,
        top + (h - CAP * size) / 2.0,
        size,
        None,
        "center",
        iw,
        "display_font",
        true,
        vec![("fallback", "--".into()), ("color_rules", rules)],
    )
}

/// build_power: the watts the PC draws now, and the energy of today and of the month with
/// what it costs (the `power` service).
pub fn build_power(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("gpu");
    let title = or(b.ms("title"), || t("Power use"));
    let top = b.head("power", &title, &color, 0.0)?;
    let rows: [Pair; 4] = [
        ("power.today".into(), "{value:.2f} kWh", t("Today")),
        (
            "power.today.cost".into(),
            "{value:.2f} {unit}",
            t("Cost today"),
        ),
        ("power.month".into(), "{value:.1f} kWh", t("This month")),
        (
            "power.month.cost".into(),
            "{value:.2f} {unit}",
            t("Cost this month"),
        ),
    ];
    let a = b.aspect();
    let with_history = a >= 3.2 || (b.large() && a >= 2.0);
    number_rows_history(
        b,
        "power.watts",
        "{value:.0f} W",
        &rows,
        &color,
        top,
        with_history,
    )
}

/// build_timer: the time left of the timer (the `timer` service) in a ring that fills as it
/// runs, and whether it is focus or break.
pub fn build_timer(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("accent");
    let title = or(b.ms("title"), || t("Timer"));
    let top = b.head("hourglass", &title, &color, 0.0)?;
    let (ts, pad) = (b.ts as f64, b.pad as f64);
    let (x, y, w, h) = (
        b.ix as f64,
        b.iy as f64 + top,
        b.iw as f64,
        b.ih as f64 - top,
    );
    let wide = w / pmax(1, h).f() >= 1.8;
    let d = pmax(8, fmin(h, w * if wide { 0.5 } else { 1.0 })).f();
    let rx = if wide { x } else { x + (w - d) / 2.0 };
    let ry = y + (h - d) / 2.0;
    b.ring("ring", "timer.share", rx, ry, d, &color, Some(0), Some(1))?;
    let (size, small) = (d * 0.22, pmax(7, d * 0.08).f());
    let gap = d * 0.05;
    let cap = ry + (d - (CAP * size + gap + CAP * small)) / 2.0;
    b.value(
        "left",
        "timer.text",
        "{value}",
        rx + d / 2.0,
        cap,
        size,
        None,
        "center",
        d * 0.72,
        "display_font",
        true,
        vec![("fallback", "--:--".into())],
    )?;
    if d > ts * 5.0 {
        b.value(
            "phase",
            "timer.phase",
            "{value}",
            rx + d / 2.0,
            cap + CAP * size + gap,
            small,
            Some(color.clone()),
            "center",
            d * 0.6,
            "text_font",
            false,
            vec![("fallback", "".into()), ("hide_if_missing", true.into())],
        )?;
    }
    if wide && w - d - pad * 1.5 > ts * 5.0 {
        let tx = rx + d + pad * 1.5;
        let rows: [Pair; 1] = [("timer.rounds".into(), "{value:.0f}", t("Rounds"))];
        pairs(b, &rows, tx, y, (b.ix + b.iw) as f64 - tx, h)?;
    }
    Ok(())
}

/// build_ups: the UPS (the `nut` source), its battery, how long it lasts, its load and power,
/// and whether the PC runs on it.
pub fn build_ups(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let color = b.tint("net");
    let title = or(b.ms("title"), || t("UPS"));
    let top = b.head("battery", &title, &color, 0.0)?;
    let rows: [Pair; 4] = [
        ("ups.runtime".into(), "{value:.0f} min", t("Runtime")),
        ("ups.load".into(), "{value:.0f} %", t("Load")),
        ("ups.power".into(), "{value:.0f} W", t("Power")),
        ("ups.status".into(), "{value}", t("State")),
    ];
    let a = b.aspect();
    let with_history = a >= 3.2 || (b.large() && a >= 2.0);
    number_rows_history(
        b,
        "ups.charge",
        "{value:.0f} %",
        &rows,
        &color,
        top,
        with_history,
    )
}
