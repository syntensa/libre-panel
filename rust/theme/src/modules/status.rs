//! Modules of a state: the microphone, the battery and what the AI agents do.

use lp_py::{Dict, Value};

use super::{faded, fmax, fmin, heat_rules, pmax, round, round_to, Build, Fields, Source, CAP, N};
use crate::model::Result;

use lp_i18n::t;

/// b.out[-1][key] = value
fn set_last(b: &mut Build, key: &str, value: Value) {
    if let Some(widget) = b.out.last_mut() {
        widget.insert(key.into(), value);
    }
}

const MIC: &str = "audio.mic.muted";

/// build_mic: whether the microphone is muted: the crossed symbol and the word large in the
/// critical colour while it is, calm while it is on, and said where there is no microphone.
pub fn build_mic(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let (calm, alarm) = (b.c("text3"), b.tint("crit"));
    let wide = b.aspect() >= 1.5;
    let own = b.ms("title");
    let title = lp_py::upper(&if own.is_empty() { t("Microphone") } else { own });
    let (bx, by, bw, bh) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    let (ts, pad) = (b.ts as f64, b.pad as f64);
    let (d, ix, iy, tw, size, cap, label_y, align, x);
    if wide {
        d = fmin(bh * 0.72, bw * 0.34);
        (ix, iy) = (bx + (bh - d) / 2.0 * 0.4, by + (bh - d) / 2.0);
        let tx = ix + d + pad;
        tw = bx + bw - (ix + d + pad);
        size = fmin(bh * 0.34, tw / 4.2);
        cap = by + (bh - (CAP * size + ts * 1.9)) / 2.0 + ts * 1.9;
        (label_y, align, x) = (cap - ts * 1.9, "left", tx);
    } else {
        d = fmin(bw * 0.55, bh * 0.5);
        tw = bw;
        size = fmin(bh * 0.17, tw / 4.2);
        let block = d + ts * 1.2 + CAP * size + ts * 0.9 + CAP * ts; // centred in the box
        (ix, iy) = (bx + (bw - d) / 2.0, by + fmax(0.0, (bh - block) / 2.0));
        cap = iy + d + ts * 1.2;
        (label_y, align, x) = (cap + CAP * size + ts * 0.9, "center", bx + bw / 2.0);
    }
    let icons = [
        ("muted-icon", "mic_off", alarm.clone(), format!("{MIC}=1")),
        ("on-icon", "mic", calm.clone(), format!("{MIC}=0")),
        ("none-icon", "mic", faded(&calm, 0.5)?, format!("!{MIC}")),
    ];
    for (name, icon, color, need) in icons {
        b.icon(name, icon, ix, iy, d, &color)?;
        set_last(b, "needs", need.into());
        if name == "muted-icon" {
            set_last(b, "glow", round_to(0.6 * b.glow, 2).into());
            set_last(b, "glow_radius", 2.max(round(d / 10.0)).into());
        }
    }
    let texts = [
        ("muted", t("Muted"), alarm, format!("{MIC}=1")),
        ("on", t("On"), b.c("text"), format!("{MIC}=0")),
        ("none", t("No microphone"), calm.clone(), format!("!{MIC}")),
    ];
    for (name, text, color, need) in texts {
        let text_size = size * if name == "none" { 0.6 } else { 1.0 };
        let mut fields: Fields = vec![
            ("x", round(x).into()),
            ("text", text.into()),
            ("color", color.into()),
            ("align", align.into()),
            ("needs", need.into()),
            ("max_width", round(tw).into()),
            ("fit", "shrink".into()),
        ];
        let top = cap + (CAP * size - CAP * text_size) / 2.0;
        fields.extend(b.text_fields(text_size, top, "display_font"));
        b.add("text", name, fields)?;
    }
    b.label("title", &title, x, label_y, ts, Some(calm), align, tw)
}

/// build_battery: the battery's charge, whether it charges and the time left; its history
/// when big.
pub fn build_battery(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let start = b.out.len();
    let color = b.tint("net");
    let a = b.aspect();
    let (ix, iy, iw, ih) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    let (ts, pad) = (b.ts as f64, b.pad as f64);
    let below = (b.large() || a <= 0.72) && b.ih > b.ts * 14; // the history under the battery
    let beside = !below && a >= 3.4; // the history on the right
    let main_h = if below { N::F(ih * 0.55) } else { N::I(b.ih) };
    let main_w = if beside { N::F(iw * 0.45) } else { N::I(b.iw) };
    // (Python's division: a box of no height is its ZeroDivisionError)
    let ratio = lp_py::num::true_div(&main_w.value(), &main_h.value())?;
    let (main_h, main_w) = (main_h.f(), main_w.f());
    let numbers_beside = ratio.to_f64().is_some_and(|r| r >= 1.45) && !(below && a <= 0.72);
    if numbers_beside || beside {
        let bh = fmin(main_h * 0.5, main_w * 0.2);
        let bw = bh * 1.9;
        battery_shape(b, ix, iy + (main_h - bh) / 2.0, bw, bh, &color)?;
        let tx = ix + bw + pad * 1.6;
        let size = fmin(main_h * 0.36, (main_w - bw - pad * 1.6) * 0.3);
        let cap = iy + (main_h - (CAP * size + ts * 1.0 + CAP * ts * 1.1)) / 2.0;
        let width = ix + main_w - tx;
        b.value(
            "value",
            "battery.load",
            "{value:.0f}%",
            tx,
            cap,
            size,
            None,
            "left",
            width,
            "display_font",
            true,
            Vec::new(),
        )?;
        battery_state(b, tx, cap + CAP * size + ts * 1.0, ix + main_w - tx, "left")?;
    } else {
        // the battery above the numbers
        let bw = fmin(main_w * 0.62, main_h * 0.7);
        let bh = bw / 1.9;
        let size = fmin(main_h * 0.26, main_w * 0.3);
        let block = bh + ts * 0.9 + CAP * size + ts * 0.9 + CAP * ts * 1.1;
        let mut y = iy + (main_h - block) / 2.0;
        let cx = ix + main_w / 2.0;
        battery_shape(b, cx - bw / 2.0, y, bw, bh, &color)?;
        y += bh + ts * 0.9;
        b.value(
            "value",
            "battery.load",
            "{value:.0f}%",
            cx,
            y,
            size,
            None,
            "center",
            main_w,
            "display_font",
            true,
            Vec::new(),
        )?;
        battery_state(b, cx, y + CAP * size + ts * 0.9, main_w, "center")?;
    }
    if below {
        let gy = iy + main_h + pad;
        let gh = (b.iy + b.ih) as f64 - gy;
        b.graph(
            "history",
            "battery.load",
            ix,
            gy,
            iw,
            gh,
            &color,
            Some(0),
            Some(100),
            "linear",
            true,
            N::I(0),
        )?;
    } else if beside {
        let gx = ix + main_w + pad;
        let gw = (b.ix + b.iw) as f64 - gx;
        b.graph(
            "history",
            "battery.load",
            gx,
            iy + ih * 0.12,
            gw,
            ih * 0.76,
            &color,
            Some(0),
            Some(100),
            "linear",
            true,
            N::I(0),
        )?;
    }
    for widget in &mut b.out[start..] {
        widget.insert("needs".into(), "battery.load".into());
    }
    b.label(
        "none",
        &t("No battery"),
        ix + iw / 2.0,
        iy + ih / 2.0 - CAP * ts / 2.0,
        ts,
        None,
        "center",
        iw,
    )?;
    set_last(b, "needs", "!battery.load".into());
    Ok(())
}

/// _battery_shape: the battery's outline, its nub and the charge inside.
fn battery_shape(b: &mut Build, x: f64, y: f64, w: f64, h: f64, color: &str) -> Result<()> {
    let line = 2.max(round(h * 0.07));
    let nub = 3.max(round(w * 0.06));
    let body = w - nub as f64 - line as f64;
    let outline: Fields = vec![
        ("x", round(x).into()),
        ("y", round(y).into()),
        ("w", round(body).into()),
        ("h", round(h).into()),
        ("color", Value::None),
        ("outline", b.c("text2").into()),
        ("outline_width", line.into()),
        ("radius", round(h * 0.18).into()),
    ];
    b.add("rect", "battery", outline)?;
    let tip: Fields = vec![
        ("x", round(x + body + line as f64 * 0.6).into()),
        ("y", round(y + h * 0.32).into()),
        ("w", nub.into()),
        ("h", round(h * 0.36).into()),
        ("color", b.c("text2").into()),
        ("radius", 1.max(nub.div_euclid(2)).into()),
    ];
    b.add("rect", "battery-nub", tip)?;
    let inset = line as f64 * 2.2;
    let rule = |above: i64, color: String| {
        let mut d = Dict::default();
        d.insert("above".into(), above.into());
        d.insert("color".into(), color.into());
        Value::Dict(d)
    };
    let rules = Value::List(vec![rule(20, b.c("warn")), rule(40, color.to_string())]);
    let charge: Fields = vec![
        ("x", round(x + inset).into()),
        ("y", round(y + inset).into()),
        ("w", round(body - 2.0 * inset).into()),
        ("h", round(h - 2.0 * inset).into()),
        ("sensor", "battery.load".into()),
        ("min", 0.into()),
        ("max", 100.into()),
        ("color", b.c("crit").into()),
        ("color2", Value::None),
        ("background", Value::None),
        ("radius", round(h * 0.08).into()),
        ("color_rules", rules),
        ("glow", round_to(0.3 * b.glow, 2).into()),
        ("glow_radius", 2.max(round(h * 0.1)).into()),
    ];
    b.add("bar", "charge", charge)?;
    Ok(())
}

/// _battery_state: whether it charges, and the time left.
fn battery_state(b: &mut Build, x: f64, cap: f64, width: f64, align: &str) -> Result<()> {
    let ts = b.ts as f64;
    b.value(
        "state",
        "battery.state",
        "{value}",
        x,
        cap,
        ts * 1.1,
        Some(b.c("text2")),
        align,
        width,
        "text_font",
        false,
        vec![("fallback", "".into()), ("hide_if_missing", true.into())],
    )?;
    b.value(
        "left",
        "battery.left",
        "{value:duration}",
        x,
        cap + ts * 2.0,
        ts * 1.1,
        Some(b.c("text3")),
        align,
        width,
        "text_font",
        true,
        vec![("hide_if_missing", true.into())],
    )
}

/// The sessions the agents service lists one by one (agents.LISTED).
const AI_LISTED: i64 = 3;
const AI_NAMES: [(&str, &str); 2] = [("claude", "Claude Code"), ("codex", "Codex")];

/// build_ai: what Claude Code and Codex do (the agents service): their sign, moving while
/// they work or wait, the state in words, the tool at work, what the session is called, and
/// how much of the plan's limits is used; both side by side, one over the other, or the one
/// the module's `agent` names; of both, while one is not running, the other alone.
pub fn build_ai(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let which = match b.ms("agent") {
        w if !w.is_empty() => w,
        _ => "both".to_string(),
    };
    let (ix, iy, iw, ih) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    let pad = b.pad as f64;
    if which != "both" {
        return ai_agent(b, &which, ix, iy, iw, ih, true, "");
    }
    let boxes = if b.aspect() >= 1.6 {
        // side by side
        let w = (iw - pad) / 2.0;
        [(ix, iy, w, ih), (ix + w + pad, iy, w, ih)]
    } else {
        // one over the other
        let h = (ih - pad) / 2.0;
        [(ix, iy, iw, h), (ix, iy + h + pad, iw, h)]
    };
    let own = b.needs.clone();
    let (claude, codex) = ("ai.claude.state=off", "ai.codex.state=off");
    // both while both run (or nothing is known yet) and while neither does; while one is
    // off, the one that runs alone
    let parts = [
        ("", format!("!{claude},!{codex}"), None),
        ("none-", format!("{claude},{codex}"), None),
        ("only-", format!("!{claude},{codex}"), Some("claude")),
        ("only-", format!("{claude},!{codex}"), Some("codex")),
    ];
    for (tag, needs, alone) in parts {
        b.needs = if own.is_empty() {
            needs
        } else {
            format!("{needs},{own}")
        };
        match alone {
            None => {
                for ((name, _), (x, y, w, h)) in AI_NAMES.iter().zip(boxes) {
                    ai_agent(b, name, x, y, w, h, false, tag)?;
                }
            }
            Some(name) => ai_agent(b, name, ix, iy, iw, ih, true, tag)?,
        }
    }
    b.needs = own;
    Ok(())
}

/// _ai_agent: one agent's part: its name, sign and state, and below the state what the
/// session is called and the rows of the plan's limits that fit; alone also how full its
/// context is, and its other sessions where there is room.
#[allow(clippy::too_many_arguments)]
fn ai_agent(
    b: &mut Build,
    name: &str,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    alone: bool,
    tag: &str,
) -> Result<()> {
    let (key, part) = (format!("ai.{name}"), format!("{tag}{name}"));
    let (color, wait, calm) = (b.tint("accent"), b.c("warn"), b.c("text3"));
    let (ts, pad) = (b.ts as f64, b.pad as f64);
    let title = fmax(7.0, fmin(ts * 1.05, h * 0.11));
    let shown = AI_NAMES
        .iter()
        .find(|(k, _)| *k == name)
        .map_or(name, |(_, n)| n);
    b.label(
        &format!("{part}-name"),
        shown,
        x,
        y,
        title,
        Some(calm.clone()),
        "left",
        w,
    )?;
    if name == "claude" && w >= title * 18.0 {
        // what its sessions cost today, by its status line
        b.value(
            &format!("{part}-cost"),
            &format!("{key}.cost_today"),
            &(t("Today") + " ${value:.2f}"),
            x + w,
            y,
            title,
            Some(calm.clone()),
            "right",
            w * 0.4,
            "text_font",
            false,
            vec![("fallback", "".into()), ("hide_if_missing", true.into())],
        )?;
    }
    let top = y + title * 1.9;
    let room = fmax(8.0, y + h - top);
    let wide = w / room >= 1.5;
    let (d, sign_x, mut sign_y, tx, tw, align, text_top, text_room);
    if wide {
        d = fmin(room * 0.9, w * 0.32);
        (sign_x, sign_y) = (x, top + (room - d) / 2.0);
        (tx, tw, align) = (x + d + pad, fmax(8.0, x + w - (x + d + pad)), "left");
        (text_top, text_room) = (top, room);
    } else {
        d = fmin(room * 0.32, w * 0.5);
        (sign_x, sign_y) = (x + (w - d) / 2.0, top);
        (tx, tw, align) = (x, w, "center");
        text_top = top + d + pad * 0.6;
        text_room = fmax(8.0, y + h - (top + d + pad * 0.6));
    }
    let ax = if align == "left" { tx } else { tx + tw / 2.0 };
    let words = [
        ("working", t("Working"), b.c("text")),
        ("waiting", t("Waiting for you"), wait.clone()),
        ("idle", t("Idle"), b.c("text2")),
        ("off", t("Not running"), calm.clone()),
    ];
    let small = fmax(7.0, fmin(ts * 1.0, text_room * 0.12));
    let size = fmax(
        8.0,
        fmin(text_room * if wide { 0.26 } else { 0.2 }, tw / 6.5),
    );
    let mut meters = vec![("five_hour", t("5 hours")), ("week", t("7 days"))];
    if alone && name == "claude" {
        meters.push(("context", t("Context"))); // (its status line tells it)
    }
    // what the session is called has a line of its own: alone always; beside the other only
    // where every meter fits too
    let fit = |above: f64| -> i64 {
        super::floor_div(
            text_room - size * CAP - small * 2.0 - above,
            small * 2.4 + 6.0,
        ) as i64
    };
    let count = meters.len() as i64;
    let mut task_h = small * 1.8;
    let task = alone || fit(task_h) >= count;
    if !task {
        task_h = 0.0;
    }
    let mut rows = 0.max(count.min(fit(task_h)));
    if rows == 1 && !alone {
        rows = 0; // (side by side: both rows or none)
    }
    let mut block = CAP * size
        + small * 2.0
        + task_h
        + rows as f64 * (small * 1.5 + pmax(4, small * 0.5).f() + small * 0.6);
    // alone, where there is room under the meters: its other sessions, a line each
    let step = small * 1.9;
    let mut others = 0;
    if alone && rows == count {
        let fits = super::floor_div(text_room - block - small, step) as i64;
        others = 0.max((AI_LISTED - 1).min(fits));
    }
    block += others as f64 * step + if others != 0 { small } else { 0.0 };
    let free = fmax(0.0, text_room - block);
    let cap = if wide {
        text_top + free / 2.0
    } else {
        // the sign and the words together, a little above the middle
        sign_y += free * 0.4;
        text_top + free * 0.4
    };
    let sign: Fields = vec![
        ("x", round(sign_x).into()),
        ("y", round(sign_y).into()),
        ("size", 8.max(round(d)).into()),
        ("sensor", format!("{key}.state").into()),
        ("color", color.clone().into()),
        ("color2", wait.clone().into()),
        ("muted", calm.clone().into()),
    ];
    b.add("agent", &format!("{part}-sign"), sign)?;
    for (state, word, tone) in &words {
        let mut fields: Fields = vec![
            ("x", round(ax).into()),
            ("text", word.clone().into()),
            ("color", tone.clone().into()),
            ("align", align.into()),
            ("needs", format!("{key}.state={state}").into()),
            ("max_width", round(tw).into()),
            ("fit", "shrink".into()),
        ];
        fields.extend(b.text_fields(size, cap, "display_font"));
        b.add("text", &format!("{part}-{state}"), fields)?;
    }
    let line = cap + CAP * size + small * 1.0;
    // since when it is so, at the right of the line, where the line is wide enough for both
    let since = align == "left" && tw >= small * 14.0;
    let words_w = if since { tw * 0.62 } else { tw };
    b.value(
        &format!("{part}-tool"),
        &format!("{key}.tool"),
        "{value}",
        ax,
        line,
        small,
        Some(calm.clone()),
        align,
        words_w,
        "text_font",
        false,
        vec![
            ("needs", format!("{key}.state=working").into()),
            ("fallback", "".into()),
            ("hide_if_missing", true.into()),
        ],
    )?;
    b.value(
        &format!("{part}-model"),
        &format!("{key}.model"),
        "{value}",
        ax,
        line,
        small,
        Some(calm.clone()),
        align,
        words_w,
        "text_font",
        false,
        vec![
            (
                "needs",
                format!("{key}.state!=working,{key}.state!=off").into(),
            ),
            ("fallback", "".into()),
            ("hide_if_missing", true.into()),
        ],
    )?;
    if since {
        b.value(
            &format!("{part}-since"),
            &format!("{key}.since"),
            "{value:duration}",
            tx + tw,
            line,
            small,
            Some(calm.clone()),
            "right",
            tw * 0.34,
            "text_font",
            false,
            vec![
                ("needs", format!("{key}.state!=off").into()),
                ("fallback", "".into()),
                ("hide_if_missing", true.into()),
            ],
        )?;
    }
    let mut row = line + small * 2.0;
    if task {
        b.value(
            &format!("{part}-task"),
            &format!("{key}.task"),
            "{value}",
            ax,
            row,
            small,
            Some(b.c("text2")),
            align,
            tw,
            "text_font",
            false,
            vec![
                ("fit", "ellipsis".into()),
                ("fallback", "".into()),
                ("hide_if_missing", true.into()),
            ],
        )?;
        row += task_h;
    }
    let bar_h = 4.max(round(small * 0.5));
    // when a window begins anew, where it fits between the window's name and its share
    let reset = tw >= small * 15.0;
    for (window, label) in meters.iter().take(rows as usize) {
        b.label(
            &format!("{part}-{window}-label"),
            label,
            tx,
            row,
            small * 0.9,
            None,
            "left",
            tw * 0.5,
        )?;
        if *window != "context" && reset {
            b.value(
                &format!("{part}-{window}-reset"),
                &format!("{key}.{window}_reset"),
                "{value:duration}",
                tx + tw - small * 4.2,
                row,
                small * 0.9,
                Some(calm.clone()),
                "right",
                tw * 0.3,
                "text_font",
                false,
                vec![("fallback", "".into()), ("hide_if_missing", true.into())],
            )?;
        }
        b.value(
            &format!("{part}-{window}"),
            &format!("{key}.{window}"),
            "{value:.0f} %",
            tx + tw,
            row,
            small * 0.9,
            None,
            "right",
            tw * 0.25,
            "display_font",
            true,
            vec![("fallback", "--".into())],
        )?;
        b.bar(
            &format!("{part}-{window}-bar"),
            &format!("{key}.{window}"),
            tx,
            row + small * 1.3,
            tw,
            bar_h as f64,
            &color,
            Some(0),
            Some(100),
            "linear",
        )?;
        let rules = heat_rules(b, 80, 95);
        set_last(b, "color_rules", rules);
        row += small * 1.5 + bar_h as f64 + small * 0.6;
    }
    row += if others != 0 { small } else { 0.0 };
    for n in 2..2 + others {
        // its sign, what it is called, its state and since when
        let (one, dot) = (format!("{key}.s{n}"), small * 1.2);
        let sign: Fields = vec![
            ("x", round(tx).into()),
            ("y", round(row - small * 0.1).into()),
            ("size", 8.max(round(dot)).into()),
            ("sensor", format!("{one}.state").into()),
            ("color", color.clone().into()),
            ("color2", wait.clone().into()),
            ("muted", calm.clone().into()),
            ("needs", format!("{one}.state").into()),
        ];
        b.add("agent", &format!("{part}-s{n}-sign"), sign)?;
        b.value(
            &format!("{part}-s{n}-task"),
            &format!("{one}.task"),
            "{value}",
            tx + dot + small * 0.6,
            row,
            small,
            Some(b.c("text2")),
            "left",
            tw * 0.55 - dot,
            "text_font",
            false,
            vec![
                ("fit", "ellipsis".into()),
                ("fallback", "".into()),
                ("hide_if_missing", true.into()),
            ],
        )?;
        for (state, word, tone) in words.iter().take(3) {
            let mut fields: Fields = vec![
                ("x", round(tx + tw * 0.6).into()),
                ("text", word.clone().into()),
                ("color", tone.clone().into()),
                ("max_width", round(tw * 0.26).into()),
                ("fit", "shrink".into()),
                ("needs", format!("{one}.state={state}").into()),
            ];
            fields.extend(b.text_fields(small * 0.9, row, "text_font"));
            b.add("text", &format!("{part}-s{n}-{state}"), fields)?;
        }
        b.value(
            &format!("{part}-s{n}-since"),
            &format!("{one}.since"),
            "{value:duration}",
            tx + tw,
            row,
            small * 0.9,
            Some(calm.clone()),
            "right",
            tw * 0.13,
            "text_font",
            false,
            vec![("fallback", "".into()), ("hide_if_missing", true.into())],
        )?;
        row += step;
    }
    Ok(())
}
