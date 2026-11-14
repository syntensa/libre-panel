//! Modules of readings: rings, big numbers, histories, bars, the network.

use super::{clamp, detail_rows, floor_div, fmin, mix, sources, Build, Source, CAP, N};
use crate::model::Result;

/// _ring_inside: the value and its label inside a ring.
fn ring_inside(b: &mut Build, src: &Source, cx: f64, cy: f64, d: f64, color: &str) -> Result<()> {
    let (value_size, label_size) = (d * 0.25, super::fmax(7.0, d * 0.085));
    let gap = d * 0.05;
    let total = CAP * value_size + gap + CAP * label_size;
    let top = cy - total / 2.0;
    b.value(
        "value",
        src.load,
        src.fmt,
        cx,
        top,
        value_size,
        None,
        "center",
        d * 0.66,
        "display_font",
        true,
        Vec::new(),
    )?;
    let title = match b.ms("title") {
        t if !t.is_empty() => t,
        _ => src.label.clone(),
    };
    b.label(
        "label",
        &title,
        cx,
        top + CAP * value_size + gap,
        label_size,
        Some(color.to_string()),
        "center",
        d * 0.6,
    )
}

/// build_ring
pub fn build_ring(b: &mut Build, src: &Source) -> Result<()> {
    let color = b.tint(src.role);
    b.card(true)?;
    let a = b.aspect();
    let (ix, iy, iw, ih, pad) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.pad as f64,
    );
    if b.large() && 0.7 < a && a < 1.6 {
        // big square: ring and details, history below
        let top_h = fmin(ih * 0.58, iw * 0.5);
        ring_wide(b, src, &color, ix, iy, iw, top_h, false)?;
        let gy = iy + top_h + pad;
        b.graph(
            "history",
            src.load,
            ix,
            gy,
            iw,
            iy + ih - gy,
            &color,
            src.low,
            src.high,
            src.scale,
            true,
            N::I(0),
        )?;
    } else if a >= 1.45 {
        let below = b.large();
        ring_wide(b, src, &color, ix, iy, iw, ih, below)?;
    } else if a <= 0.72 {
        // tall: ring on top, details below
        let d = iw;
        b.ring("ring", src.load, ix, iy, d, &color, src.low, src.high)?;
        ring_inside(b, src, ix + d / 2.0, iy + d / 2.0, d, &color)?;
        let room = ih - d - pad;
        if room > b.ts as f64 * 3.0 {
            let graph_h = if room > d * 0.9 { room * 0.45 } else { 0.0 };
            detail_rows(b, src, ix, iy + d + pad, iw, room - graph_h, &color, false)?;
            if graph_h != 0.0 {
                b.graph(
                    "history",
                    src.load,
                    ix,
                    iy + ih - graph_h,
                    iw,
                    graph_h,
                    &color,
                    src.low,
                    src.high,
                    src.scale,
                    true,
                    N::I(0),
                )?;
            }
        }
    } else {
        // compact
        let d = fmin(iw, ih);
        let (x, y) = (ix + (iw - d) / 2.0, iy + (ih - d) / 2.0);
        b.ring("ring", src.load, x, y, d, &color, src.low, src.high)?;
        ring_inside(b, src, x + d / 2.0, y + d / 2.0, d, &color)?;
    }
    Ok(())
}

/// _ring_wide: the ring at the left, details and a history beside or under them.
#[allow(clippy::too_many_arguments)]
fn ring_wide(
    b: &mut Build,
    src: &Source,
    color: &str,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    graph_below: bool,
) -> Result<()> {
    let d = h;
    let pad = b.pad as f64;
    b.ring("ring", src.load, x, y, d, color, src.low, src.high)?;
    ring_inside(b, src, x + d / 2.0, y + d / 2.0, d, color)?;
    let rest = w - d - pad;
    if rest < b.ts as f64 * 5.0 {
        return Ok(());
    }
    let column = fmin(rest, d * 1.3);
    let graph_w = rest - column - pad;
    if graph_w > d * 0.8 {
        // a long box: the history on the right
        detail_rows(
            b,
            src,
            x + d + pad,
            y + h * 0.04,
            column,
            h * 0.92,
            color,
            true,
        )?;
        b.graph(
            "history",
            src.load,
            x + d + column + 2.0 * pad,
            y + h * 0.12,
            graph_w,
            h * 0.76,
            color,
            src.low,
            src.high,
            src.scale,
            true,
            N::I(0),
        )?;
    } else if graph_below {
        // a big box: the history under the details
        detail_rows(b, src, x + d + pad, y, rest, h * 0.6, color, true)?;
        b.graph(
            "history",
            src.load,
            x + d + pad,
            y + h * 0.66,
            rest,
            h * 0.34,
            color,
            src.low,
            src.high,
            src.scale,
            true,
            N::I(0),
        )?;
    } else {
        detail_rows(
            b,
            src,
            x + d + pad,
            y + h * 0.04,
            column,
            h * 0.92,
            color,
            true,
        )?;
    }
    Ok(())
}

/// The sensor and format a reading module shows: its source's, or its own sensor's.
fn reading(b: &Build, src: &Source) -> (String, String) {
    if b.ms("source") == "sensor" {
        let sensor = match b.ms("sensor") {
            s if !s.is_empty() => s,
            _ => "cpu.load".into(),
        };
        let fmt = match b.ms("format") {
            f if !f.is_empty() => f,
            _ => "{value:.0f}{unit}".into(),
        };
        (sensor, fmt)
    } else {
        (src.load.to_string(), src.fmt.to_string())
    }
}

/// build_stat: a large number with its name; a bar and a history graph when there is room.
pub fn build_stat(b: &mut Build, src: &Source) -> Result<()> {
    let color = b.tint(src.role);
    b.card(true)?;
    let (sensor, fmt) = reading(b, src);
    let own = b.ms("source") == "sensor";
    let title = match b.ms("title") {
        t if !t.is_empty() => t,
        _ if !own => src.label.clone(),
        _ => sensor.clone(),
    };
    let a = b.aspect();
    let (ix, iy, iw, ih, pad, ts) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.pad as f64,
        b.ts as f64,
    );
    b.icon("icon", src.icon, ix, iy - ts * 0.1, ts * 1.5, &color)?;
    b.label(
        "title",
        &title,
        ix + ts * 2.1,
        iy + ts * 0.15,
        ts * 1.05,
        Some(color.clone()),
        "left",
        iw - ts * 2.1,
    )?;
    let head = ts * 1.9;
    let tall = a <= 0.72;
    let graph_room = b.large() || a >= 2.4 || tall;
    let stacked = b.large() || tall; // the history under the number
    let mut body_h = ih - head;
    if graph_room && stacked {
        body_h = ih * 0.5 - head;
    }
    let size = fmin(body_h * 0.62, iw * if a < 2.4 { 0.3 } else { 0.18 });
    let cap_top = iy + head + (body_h - CAP * size) * if graph_room { 0.35 } else { 0.45 };
    let width = if !(graph_room && !stacked) {
        iw
    } else {
        iw * 0.42
    };
    b.value(
        "value",
        &sensor,
        &fmt,
        ix,
        cap_top,
        size,
        None,
        "left",
        width,
        "display_font",
        true,
        Vec::new(),
    )?;
    let known = !own && src.high.is_some();
    if known && !(graph_room && stacked) && ih > ts * 6.0 {
        let bar_h = super::fmax(3.0, b.u as f64 * 0.035);
        b.bar(
            "bar",
            &sensor,
            ix,
            iy + ih - bar_h,
            width,
            bar_h,
            &color,
            src.low,
            src.high,
            src.scale,
        )?;
    }
    let src_known = ["cpu", "gpu", "mem", "disk"].contains(&b.ms("source").as_str());
    if stacked && b.large() && a >= 1.6 && src_known {
        // room beside the number: details
        detail_rows(b, src, ix + iw * 0.5, iy, iw * 0.5, ih * 0.5, &color, false)?;
    }
    if graph_room {
        let (low, high) = if known {
            (src.low, src.high)
        } else {
            (None, None)
        };
        if stacked {
            let gy = iy + ih * 0.5 + pad / 2.0;
            b.graph(
                "history",
                &sensor,
                ix,
                gy,
                iw,
                iy + ih - gy,
                &color,
                low,
                high,
                src.scale,
                true,
                N::I(0),
            )?;
        } else {
            let gx = ix + width + pad;
            b.graph(
                "history",
                &sensor,
                gx,
                iy + head,
                ix + iw - gx,
                ih - head,
                &color,
                low,
                high,
                src.scale,
                true,
                N::I(0),
            )?;
        }
    }
    Ok(())
}

/// build_graph: a history graph under its name and current value.
pub fn build_graph(b: &mut Build, src: &Source) -> Result<()> {
    let color = b.tint(src.role);
    b.card(true)?;
    let (sensor, fmt) = reading(b, src);
    let known = b.ms("source") != "sensor";
    let title = match b.ms("title") {
        t if !t.is_empty() => t,
        _ if known => src.label.clone(),
        _ => sensor.clone(),
    };
    let (ix, iy, iw, ih, pad, ts) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.pad as f64,
        b.ts as f64,
    );
    let value_size = clamp(ih * 0.17, ts * 1.2, ts * 3.2).f();
    b.label(
        "title",
        &title,
        ix,
        iy + (CAP * value_size - CAP * ts) / 2.0,
        ts,
        Some(color.clone()),
        "left",
        iw * 0.5,
    )?;
    b.value(
        "value",
        &sensor,
        &fmt,
        ix + iw,
        iy,
        value_size,
        None,
        "right",
        iw * 0.55,
        "display_font",
        true,
        Vec::new(),
    )?;
    let gy = iy + CAP * value_size + pad * 0.8;
    let (low, high) = if known {
        (src.low, src.high)
    } else {
        (None, None)
    };
    b.graph(
        "history",
        &sensor,
        ix,
        gy,
        iw,
        iy + ih - gy,
        &color,
        low,
        high,
        src.scale,
        true,
        N::I(0),
    )
}

/// build_bars: rows with a bar each: CPU, RAM, disk and GPU, as many as fit.
pub fn build_bars(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let all = sources();
    let order: Vec<Source> = ["cpu", "mem", "disk", "gpu"]
        .iter()
        .map(|k| {
            all.iter()
                .find(|(n, _)| n == k)
                .expect("a source")
                .1
                .clone()
        })
        .collect();
    let wide = b.aspect() >= 2.2;
    let (ix, iy, iw, ih, pad, ts) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.pad as f64,
        b.ts as f64,
    );
    let row_min = ts * if wide { 2.2 } else { 3.3 };
    let count = clamp(floor_div(ih, row_min), 1, 4).f() as i64;
    let row = ih / count as f64;
    for (i, src) in order.iter().take(count as usize).enumerate() {
        let color = b.c(src.role);
        let top = iy + i as f64 * row;
        let needs = if src.role == "gpu" { src.load } else { "" };
        let size = fmin(row * if wide { 0.42 } else { 0.3 }, ts * 1.6);
        let bar_h = 3.max(super::round(fmin(row * 0.14, b.u as f64 * 0.03))) as f64;
        if wide {
            let mid = top + row / 2.0;
            let label_w = iw * 0.2;
            let value_w = iw * 0.2;
            b.label(
                &format!("r{i}-label"),
                &src.label,
                ix,
                mid - CAP * ts / 2.0,
                ts,
                Some(color.clone()),
                "left",
                label_w,
            )?;
            b.bar(
                &format!("r{i}-bar"),
                src.load,
                ix + label_w,
                mid - bar_h / 2.0,
                iw - label_w - value_w - pad,
                bar_h,
                &color,
                Some(0),
                Some(100),
                "linear",
            )?;
            b.value(
                &format!("r{i}-value"),
                src.load,
                src.fmt,
                ix + iw,
                mid - CAP * size / 2.0,
                size,
                None,
                "right",
                value_w,
                "display_font",
                true,
                Vec::new(),
            )?;
        } else {
            b.label(
                &format!("r{i}-label"),
                &src.label,
                ix,
                top + row * 0.12 + CAP * (size - ts) / 2.0,
                ts,
                Some(color.clone()),
                "left",
                iw * 0.5,
            )?;
            b.value(
                &format!("r{i}-value"),
                src.load,
                src.fmt,
                ix + iw,
                top + row * 0.12,
                size,
                None,
                "right",
                iw * 0.5,
                "display_font",
                true,
                Vec::new(),
            )?;
            b.bar(
                &format!("r{i}-bar"),
                src.load,
                ix,
                top + row * 0.12 + CAP * size + row * 0.16,
                iw,
                bar_h,
                &color,
                Some(0),
                Some(100),
                "linear",
            )?;
        }
        if !needs.is_empty() {
            let n = b.out.len();
            for w in &mut b.out[n.saturating_sub(3)..] {
                w.insert("needs".into(), needs.into());
            }
        }
    }
    Ok(())
}

/// build_network: download and upload; a graph of the download when there is room.
pub fn build_network(b: &mut Build, _src: &Source) -> Result<()> {
    let color = b.tint("net");
    let up_color = mix(&color, &b.c("text"), 0.45)?;
    b.card(true)?;
    let a = b.aspect();
    let (ix, iy, iw, ih, pad) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.pad as f64,
    );
    let stacked = b.large() || a <= 0.72; // the graph under the numbers
    let graph = a >= 1.45 || stacked;
    let column = if !graph || stacked {
        iw
    } else {
        fmin(iw * 0.45, ih * 1.6)
    };
    let lines_h = if !(graph && stacked) { ih } else { ih * 0.45 };
    let size = fmin(lines_h * 0.26, column * 0.16);
    let icon = size * 0.95;
    let rate = "{value:bytes}/s";
    for (i, (key, name, tone)) in [
        ("net.down", "download", &color),
        ("net.up", "upload", &up_color),
    ]
    .into_iter()
    .enumerate()
    {
        let cap_top = iy + lines_h * (0.18 + 0.5 * i as f64);
        b.icon(
            &format!("{name}-icon"),
            name,
            ix,
            cap_top + CAP * size / 2.0 - icon / 2.0,
            icon,
            tone,
        )?;
        b.value(
            name,
            key,
            rate,
            ix + icon * 1.35,
            cap_top,
            size,
            None,
            "left",
            column - icon * 1.35,
            "display_font",
            true,
            Vec::new(),
        )?;
    }
    if !graph {
        return Ok(());
    }
    let (gx, gy, gw, gh) = if stacked {
        (ix, iy + lines_h + pad, iw, ih - lines_h - pad)
    } else {
        (ix + column + pad, iy, iw - column - pad, ih)
    };
    b.graph(
        "down-history",
        "net.down",
        gx,
        gy,
        gw,
        gh,
        &color,
        Some(0),
        None,
        "sqrt",
        true,
        N::I(0),
    )?;
    b.graph(
        "up-history",
        "net.up",
        gx,
        gy,
        gw,
        gh,
        &up_color,
        Some(0),
        None,
        "sqrt",
        false,
        N::I(0),
    )
}

/// build_system: the names of the processor and graphics card, and the uptime; with room for
/// a fourth row the memory's name too.
pub fn build_system(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let mut rows: Vec<(&str, &str, String, &str)> = vec![
        ("cpu.name", "{value}", "CPU".into(), "cpu"),
        ("gpu.name", "{value}", "GPU".into(), "gpu"),
        (
            "sys.uptime",
            "{value:duration}",
            lp_i18n::t("Uptime"),
            "accent",
        ),
    ];
    let (ix, iy, iw, ih, ts) = (
        b.ix as f64,
        b.iy as f64,
        b.iw as f64,
        b.ih as f64,
        b.ts as f64,
    );
    let row_min = ts * 3.4;
    let count = clamp(floor_div(ih, row_min), 1, 4).f() as i64;
    if count == 4 {
        // the names together, then the uptime
        rows.insert(2, ("mem.name", "{value}", lp_i18n::t("RAM"), "mem"));
    }
    let row = ih / count as f64;
    for (i, (sensor, fmt, text, role)) in rows.iter().take(count as usize).enumerate() {
        let top = iy + i as f64 * row + (row - (CAP * ts + ts * 0.7 + CAP * ts * 1.3)) / 2.0;
        b.label(
            &format!("r{i}-label"),
            text,
            ix,
            top,
            ts,
            Some(b.c(role)),
            "left",
            iw,
        )?;
        let kw = vec![
            ("hide_if_missing", true.into()),
            ("fallback", "".into()),
            ("fit", "ellipsis".into()),
        ];
        b.value(
            &format!("r{i}"),
            sensor,
            fmt,
            ix,
            top + CAP * ts + ts * 0.7,
            ts * 1.3,
            Some(b.c("text")),
            "left",
            iw,
            "text_font",
            false,
            kw,
        )?;
    }
    Ok(())
}
