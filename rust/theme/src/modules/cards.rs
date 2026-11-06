//! Cards (theme/cards.py): the large modules after SPUR II's Studio screen: what plays, the
//! weather and the next hours, a download, rings with their values, the network, and a slot
//! that shows one card of several. A card is laid out in the numbers of the card it comes
//! from, scaled with the module's height; where the module is too small for that, the older
//! module of its kind takes over.

use lp_py::{num, Dict, Value};

use super::{
    build_module, clamp, floor_div, fmax, fmin, heat, heat_rules, mix, pmax, pmin, py_take, round,
    round_to, sources, Build, Fields, Source, CAP, DEFAULT_CARDS, N,
};
use crate::model::Result;

use lp_i18n::t;

/// a / b as Python divides floats: by zero its ZeroDivisionError.
fn fdiv(a: f64, b: f64) -> Result<f64> {
    let q = num::true_div(&Value::Float(a), &Value::Float(b))?;
    Ok(q.to_f64().unwrap_or(f64::NAN))
}

/// int(x) of a number Python holds (a float toward 0).
fn int_of(v: &Value) -> i64 {
    v.to_f64().unwrap_or(0.0) as i64
}

/// b.out[-1][key] = value
fn set_last(b: &mut Build, key: &str, value: Value) {
    if let Some(widget) = b.out.last_mut() {
        widget.insert(key.into(), value);
    }
}

/// _need: a condition added to a part, before the ones it has.
fn need(widget: &mut Dict, need: &str) {
    let own = widget.get("needs").map(Value::str).unwrap_or_default();
    let needs = if own.is_empty() {
        need.to_string()
    } else {
        format!("{need},{own}")
    };
    widget.insert("needs".into(), needs.into());
}

fn need_last(b: &mut Build, condition: &str) {
    if let Some(widget) = b.out.last_mut() {
        need(widget, condition);
    }
}

/// _Ref: a card in the numbers of the reference card; sizes and distances grow with the
/// module's height, or shrink with its width where the card would not fit.
struct Ref {
    g: f64,
    ok: bool,
    left: f64,
    top: f64,
    edge: f64,
}

impl Ref {
    fn new(b: &Build, ref_h: f64, least_w: f64, floor: f64, aspect: f64, height: i64) -> Ref {
        let h = height as f64;
        let g = fmin(h / ref_h, b.w as f64 / least_w);
        Ref {
            g,
            // too small, or too narrow for its height: the older module of its kind does better
            ok: g >= floor && b.w as f64 >= aspect * h,
            left: b.x as f64,
            // a card that is not as tall stands in the middle
            top: b.y as f64 + (h - ref_h * g) / 2.0,
            edge: (b.x + b.w) as f64,
        }
    }

    fn x(&self, v: f64) -> f64 {
        self.left + v * self.g
    }

    fn y(&self, v: f64) -> f64 {
        self.top + v * self.g
    }

    fn s(&self, v: f64) -> i64 {
        self.s_low(v, 7)
    }

    fn s_low(&self, v: f64, low: i64) -> i64 {
        low.max(round(v * self.g))
    }

    fn sf(&self, v: f64) -> f64 {
        self.s(v) as f64
    }

    fn right(&self, margin: f64) -> f64 {
        self.edge - margin * self.g
    }

    /// Where the capitals of a text of `size` start, for its baseline at `baseline`.
    fn cap(&self, baseline: f64, size: f64) -> f64 {
        self.y(baseline) - CAP * self.sf(size)
    }
}

/// _bar: a bar of one colour in a groove.
#[allow(clippy::too_many_arguments)]
fn bar(
    b: &mut Build,
    name: &str,
    sensor: &str,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    color: &str,
    high: i64,
    extra: Fields,
) -> Result<()> {
    let h = 2.max(round(h));
    let mut fields: Fields = vec![
        ("x", round(x).into()),
        ("y", round(y).into()),
        ("w", 4.max(round(w)).into()),
        ("h", h.into()),
        ("sensor", sensor.into()),
        ("min", 0.into()),
        ("max", high.into()),
        ("color", color.into()),
        ("color2", Value::None),
        ("background", b.c("track").into()),
        ("radius", h.div_euclid(2).into()),
        ("smooth", true.into()),
    ];
    fields.extend(extra);
    b.add("bar", name, fields)?;
    Ok(())
}

// -- the weather card -------------------------------------------------------------------------

/// build_forecast: the weather: a symbol that moves, the temperature with the day's range, the
/// sky, the details, the rain of the next two hours and the sun, and under them the next
/// hours; a calendar sheet where there is no weather; the weather module where it is small; a
/// tall one has the card at its top and the next days under it.
pub fn build_forecast(b: &mut Build, src: &Source) -> Result<()> {
    let mut r = Ref::new(b, 376.0, 590.0, 0.55, 1.35, b.h);
    let mut head = b.h;
    if !r.ok {
        head = round(b.w as f64 / 1.5);
        r = Ref::new(b, 376.0, 590.0, 0.55, 1.35, head);
        if r.g < 0.8 || ((b.h - head) as f64) < 0.6 * head as f64 {
            return super::weather::build_weather(b, src);
        }
    }
    b.card(true)?;
    let start = b.out.len();
    super::time::date_layout(b)?;
    for widget in &mut b.out[start..] {
        widget.insert("needs".into(), "!weather.temperature".into());
    }
    b.needs = "weather.temperature".into();
    let clouds = b.tint("text2");
    let (left, right) = (r.x(280.0), r.right(26.0));
    // the column of text: a wider card has more hours
    let width = pmin(right - left, r.s(400.0)).f();
    let symbol: Fields = vec![
        ("x", round(r.x(30.0)).into()),
        ("y", round(r.y(12.0)).into()),
        ("size", r.s(196.0).into()),
        ("sensor", "weather.code".into()),
        ("color", clouds.clone().into()),
        ("moving", b.m("moving")),
    ];
    b.add("symbol", "symbol", symbol)?;
    // no heading unless the theme gives it a title of its own
    let title = b.ms("title");
    let titled = !title.is_empty();
    if titled {
        let cap = r.cap(36.0, 21.0);
        b.label("kicker", &title, left, cap, r.sf(21.0), None, "left", width)?;
    }
    let (temp_base, temp_size) = if titled {
        (126.0, 104.0)
    } else {
        (108.0, 112.0)
    };
    let (high_base, low_base) = if titled { (84.0, 122.0) } else { (66.0, 104.0) };
    let (desc_base, detail_base) = if titled {
        (164.0, 194.0)
    } else {
        (146.0, 178.0)
    };

    // the temperature, and beside it today's range: as wide as two digits and the degree sign
    let size = r.sf(temp_size);
    let at = left - r.sf(4.0);
    let display_font = b.st("display_font");
    let digits = b.width_per_size(&display_font, "00°")? * size;
    let range_x = at + digits + r.sf(24.0);
    let mut temp: Fields = vec![
        ("field", "temperature".into()),
        ("format", "{value:.0f}°".into()),
    ];
    let cap = r.cap(temp_base, temp_size);
    temp.extend(super::weather::temp_fields(
        b,
        at,
        cap,
        size,
        range_x - r.sf(12.0) - at,
        "left",
    ));
    b.add("weather", "temp", temp)?;
    let arrow = r.sf(22.0);
    // beside the range, where the column has room for it, the sun's way today
    let tall = head < b.h;
    let values_x = range_x + arrow + r.sf(6.0);
    let range_end = values_x + b.width_per_size(&display_font, "-00°")? * r.sf(26.0);
    let corner = (range_end + r.sf(28.0), left + width);
    let corner_ok = !tall && corner.1 - corner.0 >= r.sf(110.0);
    for (name, key, baseline, role, icon) in [
        ("high", "weather.day.0.high", high_base, "text2", "up"),
        ("low", "weather.day.0.low", low_base, "text3", "down"),
    ] {
        let top = r.cap(baseline, 26.0);
        let tone = b.c(role);
        let y = top + CAP * r.sf(26.0) / 2.0 - arrow / 2.0;
        b.icon(&format!("{name}-icon"), icon, range_x, y, arrow, &tone)?;
        need_last(b, key);
        let reach = if corner_ok { range_end } else { left + width } - values_x;
        b.value(
            name,
            key,
            "{value:.0f}°",
            values_x,
            top,
            r.sf(26.0),
            Some(tone),
            "left",
            pmax(8, reach).f(),
            "display_font",
            true,
            vec![("hide_if_missing", true.into())],
        )?;
    }

    let cap = r.cap(desc_base, 30.0);
    super::weather::weather_desc(b, left, cap, width, r.sf(30.0))?;
    let small = r.sf(20.0);
    let details = [
        (
            "weather.apparent_temperature",
            t("Feels like {value:.0f}°"),
            0.0,
            148.0,
        ),
        (
            "weather.wind_speed",
            t("Wind {value:.0f} {unit}"),
            148.0,
            144.0,
        ),
        ("weather.humidity", "{value:.0f}%".to_string(), 292.0, 70.0),
    ];
    let squeeze = fmin(1.0, width / r.sf(362.0)); // a narrow column draws them closer
    let gap = r.sf(14.0); // ... but never into each other
    for (i, (key, fmt, at, room)) in details.iter().enumerate() {
        if i == 2 && squeeze < 0.9 {
            break; // (the last goes first)
        }
        let muted = b.c("text3");
        b.value(
            &format!("detail{i}"),
            key,
            fmt,
            left + at * r.g * squeeze,
            r.cap(detail_base, 20.0),
            small,
            Some(muted),
            "left",
            pmax(8, room * r.g * squeeze - gap).f(),
            "text_font",
            false,
            vec![("fallback", "".into()), ("hide_if_missing", true.into())],
        )?;
    }

    // the rain of the next two hours, and the sun's way today; in a tall card across it,
    // under the symbol
    let line = r.cap(226.0, 20.0);
    let (at, room) = if tall {
        (r.x(30.0), right - r.x(30.0))
    } else {
        (left, width)
    };
    let share = if tall {
        0.42
    } else if corner_ok {
        1.0
    } else {
        0.55
    };
    for (name, condition, role) in [
        ("rain", "weather.rain.minutes", "gpu"),
        ("dry", "!weather.rain.minutes", "text3"),
    ] {
        let tone = b.c(role);
        b.value(
            name,
            "weather.rain.text",
            "{value}",
            at,
            line,
            small,
            Some(tone),
            "left",
            room * share,
            "text_font",
            false,
            vec![
                ("fallback", "".into()),
                ("fit", "ellipsis".into()),
                ("needs", condition.into()),
            ],
        )?;
    }
    if tall {
        let top = if titled { 200.0 } else { 184.0 };
        sun_way(b, &r, at + room * 0.44, line, room * 0.56, small, top, true)?;
    } else if corner_ok {
        let top = if titled { 62.0 } else { 38.0 };
        sun_corner(b, &r, corner.0, corner.1, top, r.sf(18.0))?;
    } else {
        sun_way(
            b,
            &r,
            at + room * 0.58,
            line,
            room * 0.42,
            small,
            196.0,
            false,
        )?;
    }

    // the next hours across the card
    let drop = if head < b.h { r.s(18.0) } else { 0 };
    let lower = if head < b.h { r.s(4.0) } else { 0 };
    let given = b.m("step");
    let wanted = if given.truthy() { int_of(&given) } else { 1 };
    let step = 1.max(6.min(wanted));
    let strip = r.x(24.0);
    let count = clamp(
        floor_div(right - strip, r.sf(109.0)),
        1,
        24i64.div_euclid(step),
    )
    .to_int();
    let cell = (right - strip) / count as f64;
    let edge = r.s(64.0);
    let (text2, gpu) = (b.c("text2"), b.c("gpu"));
    for i in 0..count {
        let key = format!("weather.hour.{}", step * (i + 1));
        let cx = strip + cell * (i as f64 + 0.5);
        let first = b.out.len();
        b.value(
            &format!("h{i}-time"),
            &format!("{key}.time"),
            "{value}",
            cx,
            r.cap(252.0, 24.0) + drop as f64,
            r.sf(24.0),
            Some(text2.clone()),
            "center",
            cell * 0.95,
            "text_font",
            false,
            vec![("fallback", "".into())],
        )?;
        let symbol: Fields = vec![
            ("x", round(cx - edge as f64 / 2.0).into()),
            ("y", round(r.y(258.0) + drop as f64).into()),
            ("size", edge.into()),
            ("sensor", format!("{key}.code").into()),
            ("color", clouds.clone().into()),
            ("moving", b.m("moving")),
            ("offset", round_to(i as f64 * 1.7, 2).into()),
        ];
        b.add("symbol", &format!("h{i}-symbol"), symbol)?;
        b.value(
            &format!("h{i}-temp"),
            &format!("{key}.temperature"),
            "{value:.0f}°",
            cx,
            r.cap(352.0, 34.0) + drop as f64,
            r.sf(34.0),
            None,
            "center",
            cell * 0.92,
            "display_font",
            true,
            Vec::new(),
        )?;
        bar(
            b,
            &format!("h{i}-rain"),
            &format!("{key}.rain"),
            cx - cell * 0.36,
            r.y(358.0) + drop as f64 + lower as f64,
            cell * 0.72,
            r.s_low(9.0, 3) as f64,
            &gpu,
            100,
            Vec::new(),
        )?;
        let code = format!("{key}.code");
        for widget in &mut b.out[first..] {
            need(widget, &code); // a column only while its hour is known
        }
    }
    if head < b.h {
        // the next days under the card, each under an hour: as many, as wide
        let top = b.y + head + drop;
        let mut module = b.module.clone();
        module.insert("module".into(), "outlook".into());
        module.insert("card".into(), "off".into());
        let bx = [
            round(strip),
            top,
            round(right - strip),
            b.y + b.h - top - r.s(8.0),
        ];
        let mut days = Build::new(module, bx, b.colors, b.style, b.env);
        let inner = (strip, top as f64, right - strip, days.h as f64);
        for i in 0..count.min(7) {
            let first = days.out.len();
            let key = format!("weather.day.{i}");
            super::weather::outlook_column_in(&mut days, inner, i, &key, count, &clouds, &gpu)?;
            let code = format!("{key}.code");
            for widget in &mut days.out[first..] {
                need(widget, &code); // a day only while it is known
            }
        }
        let int = |w: &Dict, k: &str| w.get(k).map_or(0, int_of);
        let low = days
            .out
            .iter()
            .map(|w| {
                let tall = if w.contains_key("h") {
                    int(w, "h")
                } else {
                    int(w, "size")
                };
                int(w, "y") + tall
            })
            .fold(None, |best: Option<i64>, v| {
                Some(best.map_or(v, |m| m.max(v)))
            })
            .unwrap_or(0);
        let down = 0.max(round((top + days.h - low) as f64 / 2.0)); // the days in the middle
        for mut widget in days.out {
            let id = widget.get("id").map(Value::str).unwrap_or_default();
            widget.insert("id".into(), format!("days-{id}").into());
            let y = int(&widget, "y");
            widget.insert("y".into(), (y + down).into());
            need(&mut widget, "weather.temperature"); // (without weather: the calendar sheet)
            b.out.push(widget);
        }
    }
    b.needs = String::new();
    Ok(())
}

/// _sun_corner: the sun's way today from `x0` to `x1`, its arc from `top` (the reference's
/// px) and sunrise's and sunset's times under its ends.
fn sun_corner(b: &mut Build, r: &Ref, x0: f64, x1: f64, top: f64, small: f64) -> Result<()> {
    let w = x1 - x0;
    let (arc_y, arc_h) = (r.y(top), r.s(30.0));
    let warm = b.c("warn");
    let way: Fields = vec![
        ("x", round(x0).into()),
        ("y", round(arc_y).into()),
        ("w", round(w).into()),
        ("h", arc_h.into()),
        ("sensor", "sun.progress".into()),
        ("thickness", fmax(1.5, round_to(r.g * 2.6, 1)).into()),
        ("color", warm.clone().into()),
        ("color2", b.deep(&warm)?.into()),
        ("background", b.c("line").into()),
        ("night", b.c("text2").into()),
        ("needs", "sun.up".into()),
    ];
    b.add("sunpath", "sun-way", way)?;
    let line = arc_y + arc_h as f64 + r.sf(9.0); // (the capitals of the times)
    for (name, key, at, align) in [
        ("sun-rise", "sun.rise", x0, "left"),
        ("sun-set", "sun.set", x1, "right"),
    ] {
        let muted = b.c("text3");
        b.value(
            name,
            key,
            "{value}",
            at,
            line,
            small,
            Some(muted),
            align,
            w / 2.0,
            "text_font",
            true,
            vec![("fallback", "".into()), ("needs", "sun.rise".into())],
        )?;
    }
    Ok(())
}

/// _sun_way: the sun's way today across `room` px from `x`, the arc from sunrise to sunset
/// with the times at its ends (not `both`: only the time that comes next); where the arc
/// would be too short, a line of text says when the sun sets or rises.
#[allow(clippy::too_many_arguments)]
fn sun_way(
    b: &mut Build,
    r: &Ref,
    x: f64,
    line: f64,
    room: f64,
    small: f64,
    top: f64,
    both: bool,
) -> Result<()> {
    let text_font = b.st("text_font");
    let label = b.width_per_size(&text_font, "00:00")? * small;
    let gap = r.sf(10.0);
    let horizon = line + CAP * small / 2.0; // the middle of the times' capitals
    let times = if both { 2.0 } else { 1.0 };
    let arc_w = pmin(room - times * (label + gap), r.s(260.0)).f();
    let muted = b.c("text3");
    if arc_w < r.sf(56.0) {
        // too short to read: the time in words
        for (name, key, text, up) in [
            ("sun-set", "sun.set", t("Sun until {value}"), 1),
            ("sun-rise", "sun.next_rise", t("Sun from {value}"), 0),
        ] {
            b.value(
                name,
                key,
                &text,
                x + room,
                line,
                small,
                Some(muted.clone()),
                "right",
                room * 0.84,
                "text_font",
                false,
                vec![
                    ("fallback", "".into()),
                    ("fit", "ellipsis".into()),
                    ("needs", format!("sun.up={up}").into()),
                ],
            )?;
        }
        return Ok(());
    }
    let end = x + room; // (the times and the arc keep to the right)
    let arc_y = r.y(top);
    let warm = b.c("warn");
    let deep = b.deep(&warm)?;
    let (track, night) = (b.c("line"), b.c("text2"));
    let way = |b: &mut Build, name: &str, arc_x: f64, needs: &str| -> Result<()> {
        let fields: Fields = vec![
            ("x", round(arc_x).into()),
            ("y", round(arc_y).into()),
            ("w", round(arc_w).into()),
            ("h", 8.max(round(horizon - arc_y + 2.0)).into()),
            ("sensor", "sun.progress".into()),
            ("thickness", fmax(1.5, round_to(r.g * 2.6, 1)).into()),
            ("color", warm.clone().into()),
            ("color2", deep.clone().into()),
            ("background", track.clone().into()),
            ("night", night.clone().into()),
            ("needs", needs.into()),
        ];
        b.add("sunpath", name, fields)?;
        Ok(())
    };
    let time = |b: &mut Build, name: &str, key: &str, at: f64, align: &str, needs: &str| {
        b.value(
            name,
            key,
            "{value}",
            at,
            line,
            small,
            Some(muted.clone()),
            align,
            label + gap,
            "text_font",
            true,
            vec![("fallback", "".into()), ("needs", needs.into())],
        )
    };
    if both {
        let arc_x = end - label - gap - arc_w;
        way(b, "sun-way", arc_x, "sun.up")?;
        time(b, "sun-rise", "sun.rise", arc_x - gap, "right", "sun.rise")?;
        return time(b, "sun-set", "sun.set", end - label, "left", "sun.rise");
    }
    // by day the arc, then sunset's time; by night sunrise's time, then the arc
    way(b, "sun-way", end - label - gap - arc_w, "sun.up=1")?;
    time(
        b,
        "sun-set",
        "sun.set",
        end - label,
        "left",
        "sun.rise,sun.up=1",
    )?;
    way(b, "sun-way-night", end - arc_w, "sun.up=0")?;
    let at = end - arc_w - gap;
    time(
        b,
        "sun-rise",
        "sun.next_rise",
        at,
        "right",
        "sun.rise,sun.up=0",
    )
}

// -- the playback card ------------------------------------------------------------------------

/// build_playback: what plays: the cover, the state, the title and artist, the app with its
/// icon, the progress with the time played and the length; nothing playing, a note and a word.
pub fn build_playback(b: &mut Build, src: &Source) -> Result<()> {
    let r = Ref::new(b, 376.0, 560.0, 0.55, 1.35, b.h);
    if !r.ok {
        return super::media::build_music(b, src);
    }
    b.card(true)?;
    // the colour the cover carries, as the Studio's
    let color = match b.m("color") {
        v if v.truthy() => v.str(),
        _ => crate::tables::COVER_COLOR.to_string(),
    };
    let (left, right) = (r.x(280.0), r.right(26.0));
    let width = right - left;
    b.needs = "media.title".into();
    super::media::cover(b, r.x(24.0), r.y(24.0), N::I(r.s(228.0)))?;

    // the state, as a sign and a word
    let label = r.sf(21.0);
    let top = r.cap(54.0, 21.0);
    let sign = r.sf(22.0);
    for (name, state, icon, text) in [
        ("playing", 1, "play", t("Now playing")),
        ("paused", 0, "pause", t("Paused")),
    ] {
        let y = top + CAP * label / 2.0 - sign / 2.0;
        b.icon(&format!("{name}-sign"), icon, left, y, sign, &color)?;
        need_last(b, &format!("media.playing={state}"));
        let x = left + r.sf(30.0);
        b.label(name, &text, x, top, label, None, "left", width - r.sf(30.0))?;
        need_last(b, &format!("media.playing={state}"));
    }

    // the title, the artist and the app
    let quiet = || -> Fields {
        vec![
            ("fallback", "".into()),
            ("fit", "ellipsis".into()),
            ("hide_if_missing", true.into()),
        ]
    };
    b.value(
        "title",
        "media.title",
        "{value}",
        left,
        r.cap(146.0, 44.0),
        r.sf(44.0),
        None,
        "left",
        width,
        "display_font",
        false,
        quiet(),
    )?;
    b.value(
        "artist",
        "media.artist",
        "{value}",
        left,
        r.cap(192.0, 30.0),
        r.sf(30.0),
        Some(b.c("text2")),
        "left",
        width,
        "text_font",
        false,
        quiet(),
    )?;
    let icon = r.s(32.0);
    let app: Fields = vec![
        ("x", round(left).into()),
        ("y", round(r.y(216.0)).into()),
        ("w", icon.into()),
        ("h", icon.into()),
        ("src", "@media.app_icon".into()),
        ("fit", "contain".into()),
    ];
    b.add("image", "app-icon", app)?;
    for (name, shift, condition) in [
        ("app", 42.0, "@media.app_icon"),
        ("app-bare", 0.0, "!@media.app_icon"),
    ] {
        let mut kw = quiet();
        kw.push(("needs", condition.into()));
        b.value(
            name,
            "media.source",
            "{value}",
            left + r.sf(shift),
            r.cap(240.0, 22.0),
            r.sf(22.0),
            Some(b.c("text3")),
            "left",
            width - r.sf(shift),
            "text_font",
            false,
            kw,
        )?;
    }

    // the progress, with the time played and the length
    let strip = r.x(24.0);
    let thin = r.s_low(8.0, 3) as f64;
    let progress: Fields = vec![("hide_if_missing", true.into())];
    let y = r.y(304.0);
    bar(
        b,
        "progress",
        "media.progress",
        strip,
        y,
        right - strip,
        thin,
        &color,
        1,
        progress,
    )?;
    let time = r.sf(22.0);
    let muted = b.c("text3");
    b.value(
        "position",
        "media.position",
        "{value:clock}",
        strip,
        r.cap(340.0, 22.0),
        time,
        Some(muted.clone()),
        "left",
        (right - strip) / 2.0,
        "text_font",
        true,
        vec![("hide_if_missing", true.into())],
    )?;
    let (sensor, fmt) = super::media::end_time(b); // the length, or what is left of it
    b.value(
        "length",
        sensor,
        fmt,
        right,
        r.cap(340.0, 22.0),
        time,
        Some(muted.clone()),
        "right",
        (right - strip) / 2.0,
        "text_font",
        true,
        vec![("hide_if_missing", true.into())],
    )?;

    b.needs = "!media.title".into();
    let side = r.sf(228.0);
    let note = side * 0.3;
    let (x, y) = (r.x(24.0), r.y(24.0));
    b.icon(
        "idle-icon",
        "music",
        x + (side - note) / 2.0,
        y + side / 2.0 - note * 0.8,
        note,
        &muted,
    )?;
    b.label(
        "idle",
        &t("Nothing playing"),
        x + side / 2.0,
        y + side / 2.0 + note * 0.45,
        r.sf(21.0),
        None,
        "center",
        side,
    )?;
    b.needs = String::new();
    Ok(())
}

// -- the ring instrument ---------------------------------------------------------------------

/// _unit_of: a value format split into the number's and the unit's after it; a format that
/// makes its own unit (bytes) stays whole.
fn unit_of(fmt: &str) -> (String, String) {
    match fmt.rfind('}') {
        Some(at) if !fmt.contains("bytes") => (
            fmt[..=at].to_string(),
            lp_py::strip(&fmt[at + 1..]).to_string(),
        ),
        _ => (fmt.to_string(), String::new()),
    }
}

/// _fan_name: a fan's name from its sensor: `gpu.fan` is "gpu", `fan.board.case_1` "case 1".
fn fan_name(sensor: &str) -> String {
    let parts: Vec<&str> = sensor.split('.').collect();
    let last = parts[parts.len() - 1];
    let name = if last == "fan" && parts.len() > 1 {
        parts[parts.len() - 2]
    } else {
        last
    };
    name.replace('_', " ")
}

/// _fan_title: the fan's name over its speed: the module's `title`, else the name the fan
/// goes by, in capitals.
#[allow(clippy::too_many_arguments)]
fn fan_title(
    b: &mut Build,
    title: &str,
    sensor: &str,
    x: f64,
    top: f64,
    size: f64,
    color: &str,
    width: f64,
    align: &str,
) -> Result<()> {
    if !title.is_empty() {
        return b.label(
            "title",
            title,
            x,
            top,
            size,
            Some(color.into()),
            align,
            width,
        );
    }
    b.value(
        "title",
        sensor,
        "{label:upper}",
        x,
        top,
        size,
        Some(color.into()),
        align,
        width,
        "text_font",
        false,
        vec![
            ("letter_spacing", 1.max(round(size * 0.14)).into()),
            ("fit", "shrink".into()),
            ("fallback", lp_py::upper(&fan_name(sensor)).into()),
        ],
    )
}

/// _build_fan: a fan with its speed: the rotor in its ring and, beside it or under it, the
/// name and the speed in RPM.
fn build_fan(b: &mut Build, src: &Source) -> Result<()> {
    let sensor = match b.ms("sensor") {
        s if !s.is_empty() => s,
        _ => src.load.to_string(),
    };
    let color = match b.m("color") {
        v if v.truthy() => v.str(),
        _ => b.c(if sensor.starts_with("gpu") {
            "gpu"
        } else {
            "cpu"
        }),
    };
    b.card(true)?;
    let title = b.ms("title");
    let (text_font, display_font) = (b.st("text_font"), b.st("display_font"));
    let (ix, iy, iw, ih) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    let wide = b.aspect() >= 1.5;
    let mut value = fmin(ih * if wide { 0.36 } else { 0.2 }, iw * 0.2);
    let digits = b.width_per_size(&display_font, "0000")?;
    let mut unit_size = pmax(7, value * 0.5).f();
    let mut unit_w = b.width_per_size(&text_font, "RPM")? * unit_size;
    let mut name = value * 0.55;
    let mut gap = value * 0.16;
    let (d, fan_xy, number_at, cap);
    if wide {
        d = fmin(ih * 0.84, iw * 0.4);
        let x0 = ix + b.pad as f64 * 0.5;
        let left = x0 + d + 0.16 * d;
        let room = ix + iw - left;
        if digits * value + gap + unit_w > room {
            // smaller until the number and its unit fit
            let k = fdiv(room, digits * value + gap + unit_w)?;
            (value, unit_size, name, gap) = (value * k, unit_size * k, name * k, gap * k);
        }
        // (Python measures the unit again here and reads it no more: the same, kept)
        let block = CAP * name + gap * 1.6 + CAP * value;
        let top = iy + (ih - block) / 2.0;
        fan_xy = (x0, iy + (ih - d) / 2.0);
        let size = pmax(7, name).f();
        fan_title(b, &title, &sensor, left, top, size, &color, room, "left")?;
        (number_at, cap) = (left + digits * value, top + CAP * name + gap * 1.6);
    } else {
        d = fmin(iw * 0.74, ih * 0.52);
        if digits * value + gap + unit_w > iw * 0.92 {
            let k = fdiv(iw * 0.92, digits * value + gap + unit_w)?;
            (value, unit_size, name, gap) = (value * k, unit_size * k, name * k, gap * k);
            unit_w = b.width_per_size(&text_font, "RPM")? * unit_size;
        }
        let block = d + 0.14 * d + CAP * name + gap * 1.6 + CAP * value;
        let top = iy + (ih - block) / 2.0;
        let cx = ix + iw / 2.0;
        fan_xy = (cx - d / 2.0, top);
        let title_top = top + d + 0.14 * d;
        let size = pmax(7, name).f();
        fan_title(
            b, &title, &sensor, cx, title_top, size, &color, iw, "center",
        )?;
        cap = title_top + CAP * name + gap * 1.6;
        let pair = digits * value + gap + unit_w;
        number_at = cx - pair / 2.0 + digits * value;
    }
    let fan: Fields = vec![
        ("x", round(fan_xy.0).into()),
        ("y", round(fan_xy.1).into()),
        ("w", round(d).into()),
        ("h", round(d).into()),
        ("sensor", sensor.clone().into()),
        ("color", color.into()),
        ("max_rpm", 3000.into()),
        ("frame", "ring".into()),
    ];
    b.add("fan", "fan", fan)?;
    b.value(
        "value",
        &sensor,
        "{value:.0f}",
        number_at,
        cap,
        pmax(8, value).f(),
        None,
        "right",
        digits * value * 1.15,
        "display_font",
        true,
        Vec::new(),
    )?;
    let mut fields: Fields = vec![("text", "RPM".into()), ("x", round(number_at + gap).into())];
    let kw: Fields = vec![("color", b.c("text3").into()), ("tabular", false.into())];
    let top = cap + CAP * value - CAP * unit_size;
    fields.extend(super::weather::text(b, unit_size, top, "text_font", kw));
    b.add("text", "unit", fields)?;
    Ok(())
}

/// A reading in a row: (sensor, format, name).
type Row = (String, String, String);

/// _rows: the readings under a ring, as the Studio screen shows them; a source that is not
/// here shows the rows its module has.
fn rows_of(role: &str) -> Option<Vec<Row>> {
    let row = |s: &str, f: &str, n: String| (s.to_string(), f.to_string(), n);
    let heat = "{value:.0f}°C";
    Some(match role {
        "cpu" => vec![
            row("cpu.temp", heat, t("Temp")),
            row("cpu.power", "{value:.0f} W", t("Watt")),
            row("cpu.freq", "{value:.0f} MHz", t("Freq")),
        ],
        "gpu" => vec![
            row("gpu.temp", heat, t("Temp")),
            row("gpu.power", "{value:.0f} W", t("Watt")),
            row("gpu.mem.used", "{value:gb} GB", t("VRAM")),
        ],
        "mem" => vec![
            row("mem.free", "{value:.1f} GB", t("Free")),
            row("mem.temp", heat, t("Temp")),
            row("mem.speed", "{value:.0f} MT/s", t("Freq")),
        ],
        _ => return None,
    })
}

/// _heat_of: the colours a temperature row turns to (a memory module runs cooler).
fn heat_of(b: &Build, sensor: &str) -> Value {
    if sensor == "mem.temp" {
        heat_rules(b, 58, 66)
    } else if sensor.ends_with(".temp") {
        heat(b)
    } else {
        Value::List(Vec::new())
    }
}

/// _sample: the widest a reading may be, as text to measure.
fn sample(number: &str, unit: &str) -> &'static str {
    if number.contains(".1f") {
        "00.0"
    } else if ["MHz", "RPM", "MT/s"].contains(&unit) {
        "0000"
    } else {
        "000"
    }
}

/// max(...) of widths, Python's first maximal; `default` where there is none.
fn widest(widths: impl IntoIterator<Item = f64>) -> f64 {
    widths
        .into_iter()
        .fold(None, |best: Option<f64>, v| {
            Some(match best {
                Some(m) if v.partial_cmp(&m) != Some(std::cmp::Ordering::Greater) => m,
                _ => v,
            })
        })
        .unwrap_or(0.0)
}

/// build_instrument: a ring with its value inside, the number with a small unit, the name
/// under it and the readings of the part in rows below (beside it where wide); `source`
/// `fan`, a fan instead.
pub fn build_instrument(b: &mut Build, src: &Source) -> Result<()> {
    if b.ms("source") == "fan" {
        return build_fan(b, src);
    }
    let color = b.tint(src.role);
    b.card(true)?;
    let rows: Vec<Row> = rows_of(src.role).unwrap_or_else(|| {
        src.details
            .iter()
            .map(|(s, f, n)| (s.to_string(), f.to_string(), n.clone()))
            .collect()
    });
    let (ix, iy, iw, ih) = (b.ix as f64, b.iy as f64, b.iw as f64, b.ih as f64);
    let ts = b.ts as f64;
    let wide = b.aspect() >= 1.3;
    let mut history = 0.0; // the height of the course of the load, below the rows
    let n_rows = rows.len() as i64;
    let mut count;
    let mut d;
    if wide {
        d = fmin(ih * 0.92, iw * 0.46);
        let fit = num::floor_div(&Value::Float(ih - d * 0.2), &Value::Float(d * 0.268))?;
        count = n_rows.min(int_of(&fit));
    } else {
        d = iw / 1.12;
        count = n_rows.min(3);
        let need_of = |count: i64| {
            1.38 + if count != 0 {
                0.287 + 0.268 * (count - 1) as f64
            } else {
                0.0
            }
        };
        let mut need = need_of(count);
        // as many rows as fit with a ring at least half as large
        while !(count == 0 || ih / need >= 0.5 * d) {
            count -= 1;
            need = need_of(count);
        }
        d = fmin(d, ih / need);
        // the course of the load along the bottom, where the card is tall enough for it
        if b.m("history").truthy() {
            let spare = ih - need * 0.62 * iw / 1.12;
            if spare >= 4.5 * ts {
                history = fmin(spare, 0.4 * ih);
                d = fmin(d, (ih - history - b.pad as f64) / need);
            }
        }
    }
    let d = 24.max(round(d)) as f64;
    let u = d / 164.0;
    let pad = 0.05 * d;
    let extra = if count != 0 {
        0.287 + 0.268 * (count - 1) as f64
    } else {
        0.0
    };
    let (cx, top) = if wide {
        (ix + pad + d / 2.0, iy + (ih - d) / 2.0)
    } else {
        let total = d * (1.33 + extra) + pad;
        (ix + iw / 2.0, iy + (ih - history - total) / 2.0 + pad)
    };
    let ring: Fields = vec![
        ("x", round(cx - d / 2.0).into()),
        ("y", round(top).into()),
        ("w", (d as i64).into()),
        ("h", (d as i64).into()),
        ("sensor", src.load.into()),
        ("min", src.low.unwrap_or(0).into()),
        ("max", src.high.unwrap_or(100).into()),
        ("start_angle", (-90).into()),
        ("end_angle", 270.into()),
        ("thickness", 3.max(round(10.0 * u)).into()),
        ("color", color.clone().into()),
        ("color2", Value::None),
        ("background", b.c("track").into()),
        ("cap", "flat".into()),
        ("smooth", true.into()),
    ];
    b.add("gauge", "ring", ring)?;

    // the number in the middle with its unit: centred as a pair, for two digits
    let (size, small) = (8.max(round(50.0 * u)) as f64, 7.max(round(22.0 * u)) as f64);
    let (text_font, display_font) = (b.st("text_font"), b.st("display_font"));
    let digits = b.width_per_size(&display_font, "00")? * size;
    let (number_fmt, unit) = unit_of(src.fmt);
    let sign = b.width_per_size(&text_font, if unit.is_empty() { "%" } else { &unit })? * small;
    let gap = 4.0 * u;
    let pair = digits + gap + sign;
    let baseline = top + d / 2.0 + 0.11 * d;
    b.value(
        "value",
        src.load,
        &number_fmt,
        cx + pair / 2.0 - sign - gap,
        baseline - CAP * size,
        size,
        None,
        "right",
        digits * 1.7,
        "display_font",
        true,
        Vec::new(),
    )?;
    if !unit.is_empty() {
        let mut fields: Fields = vec![
            ("text", unit.clone().into()),
            ("x", round(cx + pair / 2.0 - sign).into()),
        ];
        let kw: Fields = vec![("color", b.c("text3").into()), ("tabular", false.into())];
        let cap = baseline - CAP * small;
        fields.extend(super::weather::text(b, small, cap, "text_font", kw));
        b.add("text", "unit", fields)?;
    }

    // its name, and the readings
    let title = match b.ms("title") {
        t if !t.is_empty() => t,
        _ => src.label.clone(),
    };
    let label = 7.max(round(20.0 * u)) as f64;
    let value = 8.max(round(30.0 * u)) as f64;
    let note = 7.max(round(17.0 * u)) as f64;
    let shown = &rows[..py_take(rows.len(), count)];
    let heading = 8.max(round(26.0 * u)) as f64;
    let (x0, row_top, room);
    if wide {
        x0 = ix + pad * 2.0 + d + 0.12 * d;
        let block = d * 0.16 * CAP + d * 0.14 + (0.max(count - 1) as f64 * d) * 0.268 + CAP * value;
        let first = iy + (ih - block) / 2.0;
        let width = (b.ix + b.iw) as f64 - x0;
        b.label(
            "title",
            &title,
            x0,
            first,
            heading,
            Some(color.clone()),
            "left",
            width,
        )?;
        row_top = first + d * 0.16 * CAP + d * 0.14;
        room = (b.ix + b.iw) as f64 - x0 - pad;
    } else {
        x0 = 0.0;
        let base = top + d + 0.28 * d;
        let cap = base - CAP * heading;
        b.label(
            "title",
            &title,
            cx,
            cap,
            heading,
            Some(color.clone()),
            "center",
            iw,
        )?;
        row_top = base + 0.287 * d - CAP * value;
        room = fmin(iw - pad, 1.1 * d);
    }
    // a reading in a row: its name, its number (right-aligned in a column of the widest)
    // and its unit; the row as wide as the widest of each, smaller where that is not room
    let names: Vec<String> = shown.iter().map(|(_, _, n)| lp_py::upper(n)).collect();
    let parts: Vec<(String, String)> = shown.iter().map(|(_, f, _)| unit_of(f)).collect();
    let gap = 8.0 * u;
    let measure = |b: &mut Build, k: f64| -> Result<(f64, f64, f64)> {
        let spacing = 1.max(round(label * k * 0.14));
        let mut names_w = Vec::new();
        for n in &names {
            names_w.push(
                b.width_per_size(&text_font, n)? * label * k
                    + (spacing * n.chars().count() as i64) as f64,
            );
        }
        let mut number_w = Vec::new();
        for (n, s) in &parts {
            number_w.push(b.width_per_size(&display_font, sample(n, s))? * value * k);
        }
        let mut unit_w = Vec::new();
        for (_, s) in &parts {
            unit_w.push(b.width_per_size(&text_font, s)? * note * k);
        }
        Ok((widest(names_w), widest(number_w), widest(unit_w)))
    };
    let total_of = |(names_w, number_w, unit_w): (f64, f64, f64), k: f64| {
        names_w
            + gap * k
            + number_w
            + if unit_w != 0.0 {
                5.0 * u * k + unit_w
            } else {
                0.0
            }
    };
    let mut k = 1.0;
    for _ in 0..4 {
        // smaller until the row fits (at least a little over half)
        let widths = measure(b, k)?;
        let total = total_of(widths, k);
        if total <= room || k <= 0.6 {
            break;
        }
        k = fmax(0.6, fdiv(k * room, total)?);
    }
    let (label_k, value_k, note_k) = (label * k, value * k, note * k);
    let widths = measure(b, k)?;
    let (names_w, number_w, unit_w) = widths;
    let total = total_of(widths, k);
    let left = if wide { x0 } else { cx - total / 2.0 };
    let edge = left + names_w + gap * k + number_w;
    let kept = src.role == "mem";
    for (i, (sensor, _fmt, name)) in shown.iter().enumerate() {
        let (number, suffix) = &parts[i];
        let cap = row_top + i as f64 * 0.268 * d;
        let base = cap + CAP * value;
        let first = b.out.len();
        b.label(
            &format!("r{i}-label"),
            name,
            left,
            base - CAP * label_k,
            label_k,
            None,
            "left",
            names_w + 2.0 * u,
        )?;
        let rules = heat_of(b, sensor);
        b.value(
            &format!("r{i}"),
            sensor,
            number,
            edge,
            base - CAP * value_k,
            value_k,
            None,
            "right",
            number_w * 1.15,
            "display_font",
            true,
            vec![
                ("hide_if_missing", (!kept).into()),
                ("fallback", "--".into()),
                ("color_rules", rules),
            ],
        )?;
        if !suffix.is_empty() {
            let mut fields: Fields = vec![
                ("text", suffix.clone().into()),
                ("x", round(edge + 5.0 * u * k).into()),
            ];
            let kw: Fields = vec![
                ("color", b.c("text3").into()),
                ("max_width", round(unit_w * 1.2).into()),
                ("tabular", false.into()),
            ];
            let cap = base - CAP * note_k;
            fields.extend(super::weather::text(b, note_k, cap, "text_font", kw));
            b.add("text", &format!("r{i}-unit"), fields)?;
        }
        let (own, label_id) = (format!("r{i}"), format!("r{i}-label"));
        for widget in &mut b.out[first..] {
            let id = widget.get("id").map(Value::str).unwrap_or_default();
            if id != own && !(kept && id == label_id) {
                need(widget, sensor); // (no unit where there is no number)
            }
        }
    }
    if history != 0.0 {
        // from one edge of the card to the other, inside its line
        let frame = b.frame_u();
        let edge = if b.card_on {
            1.max(round(frame.f() / 160.0))
        } else {
            0
        };
        let radius = if b.card_on {
            let r = b.style.get("radius").map_or(N::I(0), N::of);
            round(super::times_div(r, frame, 200.0))
        } else {
            0
        };
        let gy = (b.iy + b.ih) as f64 - history;
        let gh = (b.y + b.h - edge) as f64 - radius as f64 / 2.0 - gy;
        let (low, high) = (src.low.unwrap_or(0), src.high.unwrap_or(100));
        b.graph(
            "history",
            src.load,
            (b.x + edge) as f64,
            gy,
            (b.w - 2 * edge) as f64,
            gh,
            &color,
            Some(low),
            Some(high),
            "linear",
            true,
            N::I(0),
        )?;
        set_last(b, "clip", true.into());
    }
    Ok(())
}

// -- the download and the network -------------------------------------------------------------

/// build_download: a download as the inset of SPUR II's Studio screen (render.download).
pub fn build_download(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(true)?;
    let fields: Fields = vec![
        ("x", round(b.ix).into()),
        ("y", round(b.iy).into()),
        ("w", round(b.iw).into()),
        ("h", round(b.ih).into()),
        (
            "font",
            b.style.get("display_font").cloned().unwrap_or(Value::None),
        ),
        (
            "label_font",
            b.style.get("text_font").cloned().unwrap_or(Value::None),
        ),
        ("color", b.c("text").into()),
        ("muted", b.c("text3").into()),
        ("color2", b.tint("net").into()),
        ("done", b.c("accent").into()),
        ("background", b.c("track").into()),
        ("empty", t("No download running").into()),
        ("glow", round_to(0.3 * b.glow, 2).into()),
    ];
    b.add("downloads", "download", fields)?;
    Ok(())
}

/// build_net: the network as the Studio's NET section has it: how the PC is connected, the
/// upload and the download with their speeds and their histories; the network module where
/// it is small.
pub fn build_net(b: &mut Build, src: &Source) -> Result<()> {
    let r = Ref::new(b, 130.0, 540.0, 0.45, 1.7, b.h);
    if !r.ok {
        return super::readings::build_network(b, src);
    }
    b.card(true)?;
    let color = b.tint("net");
    let up_tone = mix(&color, &b.c("text"), 0.62)?; // the upload: the same green, paler
    let (left, right) = (r.x(20.0), r.right(18.0));
    link(b, &r, left, r.x(250.0))?;
    let (chip, arrow) = (r.s(32.0), r.sf(20.0));
    let value_right = r.x(186.0);
    for (i, (key, name, icon, tone)) in [
        ("net.up", "up", "up", up_tone.clone()),
        ("net.down", "down", "down", color.clone()),
    ]
    .into_iter()
    .enumerate()
    {
        let baseline = [80.0, 118.0][i];
        let first = b.out.len();
        let mid = r.cap(baseline, 34.0) + CAP * r.sf(34.0) / 2.0;
        // its arrow on a disc of its tone, faint
        let disc: Fields = vec![
            ("x", round(left).into()),
            ("y", round(mid - chip as f64 / 2.0).into()),
            ("w", chip.into()),
            ("h", chip.into()),
            ("radius", chip.div_euclid(2).into()),
            ("color", mix(&tone, &b.c("surface"), 0.84)?.into()),
        ];
        b.add("rect", &format!("{name}-chip"), disc)?;
        need_last(b, key);
        let x = left + (chip as f64 - arrow) / 2.0;
        b.icon(
            &format!("{name}-icon"),
            icon,
            x,
            mid - arrow / 2.0,
            arrow,
            &tone,
        )?;
        need_last(b, key);
        b.value(
            &format!("{name}-value"),
            key,
            "{value:rate}",
            value_right,
            r.cap(baseline, 34.0),
            r.sf(34.0),
            None,
            "right",
            value_right - left - chip as f64 - r.sf(8.0),
            "display_font",
            true,
            Vec::new(),
        )?;
        b.value(
            &format!("{name}-unit"),
            key,
            "{value:rate_unit}",
            value_right + r.sf(7.0),
            r.cap(baseline, 18.0),
            r.sf(18.0),
            Some(b.c("text3")),
            "left",
            r.sf(70.0),
            "text_font",
            false,
            vec![("fallback", "".into())],
        )?;
        for widget in &mut b.out[first..] {
            widget.insert("hide_if_missing".into(), true.into());
        }
    }
    let gx = r.x(268.0);
    let gw = right - gx;
    if gw >= r.sf(40.0) {
        let (top, bottom) = (r.y(16.0), r.y(120.0));
        let mid = (top + bottom) / 2.0;
        let half = (bottom - top) / 2.0;
        let line = fmax(1.0, round_to(1.6 * r.g, 1));
        for (name, key, down, tone) in [
            ("up-history", "net.up", false, up_tone.clone()),
            ("down-history", "net.down", true, color.clone()),
        ] {
            let fields: Fields = vec![
                ("x", round(gx).into()),
                ("y", round(if down { mid } else { mid - half }).into()),
                ("w", round(gw).into()),
                ("h", round(half).into()),
                ("sensor", key.into()),
                ("min", 0.into()),
                ("max", Value::None),
                ("scale", "sqrt".into()),
                ("history", 30.max(300.min(round(gw).div_euclid(2))).into()),
                ("color", tone.into()),
                ("fill", true.into()),
                ("fill_fade", true.into()),
                ("fill_alpha", 0.28.into()),
                ("smooth", true.into()),
                ("line_width", line.into()),
                ("direction", if down { "down" } else { "up" }.into()),
                ("inset", false.into()),
                ("end_dot", round_to(line * 1.7, 1).into()),
                ("glow", round_to(0.3 * b.glow, 2).into()),
                ("glow_radius", 2.max(round(r.g * 5.0)).into()),
            ];
            b.add("graph", name, fields)?;
        }
        let axis: Fields = vec![
            ("x", round(gx).into()),
            ("y", round(mid).into()),
            ("w", round(gw).into()),
            ("h", 1.max(round(r.g)).into()),
            ("color", mix(&b.c("line"), &b.c("surface"), 0.7)?.into()),
        ];
        b.add("rect", "axis", axis)?;
    }
    Ok(())
}

/// _link: how the PC is connected, at the top left from `x` (to `right`): its symbol and by
/// cable or Wi-Fi and how fast.
fn link(b: &mut Build, r: &Ref, x: f64, right: f64) -> Result<()> {
    let size = r.sf(19.0);
    let icon = r.sf(24.0);
    let mid = r.y(30.0) - CAP * r.sf(26.0) / 2.0;
    let muted = b.c("text3");
    b.icon(
        "link",
        "link",
        x - r.sf(1.0),
        mid - icon / 2.0,
        icon,
        &muted,
    )?;
    set_last(b, "needs", "net.link.kind".into());
    let text_x = x + icon + r.sf(8.0);
    b.value(
        "link-text",
        "net.link.text",
        "{value}",
        text_x,
        mid - CAP * size / 2.0,
        size,
        Some(muted),
        "left",
        right - text_x,
        "text_font",
        false,
        vec![("hide_if_missing", true.into()), ("fallback", "".into())],
    )
}

// -- a slot: one card of several ---------------------------------------------------------------

/// Whether `name` is a card a slot can hold (CARD_NAMES): every module kind but the slots,
/// and "net".
fn is_card(name: &str) -> bool {
    name == "net" || (name != "cards" && name != "netcard" && super::kind_of(name).is_some())
}

/// card_line: the card a line of a slot's `items` names (`name`, then `for <time>`, then
/// `if <conditions>`; a `#` begins a comment), with its rule; None for a line that names none.
pub fn card_line(line: &str) -> Option<(String, String)> {
    let line = lp_py::strip(line.split('#').next().unwrap_or(""));
    let (head, rule) = line.split_once(" if ").unwrap_or((line, ""));
    let head = lp_py::strip(head);
    let name = lp_py::strip(head.split_once(" for ").map_or(head, |(n, _)| n));
    is_card(name).then(|| (name.to_string(), lp_py::strip(rule).to_string()))
}

/// slot_items: the cards a slot module holds, each with its rule, in the order they are
/// asked; `netcard` holds a download card and the network.
pub fn slot_items(module: &Dict) -> Vec<(String, String)> {
    let get = |k: &str| module.get(k).map(Value::str).unwrap_or_default();
    if get("module") == "netcard" {
        return vec![
            ("download".into(), String::new()),
            ("net".into(), String::new()),
        ];
    }
    let items = match module.get("items") {
        Some(Value::Str(s)) if !s.is_empty() => s.clone(),
        _ => DEFAULT_CARDS.to_string(),
    };
    let found: Vec<(String, String)> = lp_py::splitlines(&items)
        .into_iter()
        .filter_map(card_line)
        .collect();
    if found.is_empty() {
        DEFAULT_CARDS
            .lines()
            .map(|name| (name.to_string(), String::new()))
            .collect()
    } else {
        found
    }
}

/// _child: the parts of the card `name` in `bx` (a module without a card of its own).
fn child(b: &Build, name: &str, bx: [i64; 4]) -> Result<Vec<Dict>> {
    let mut module = b.module.clone();
    module.insert("module".into(), name.into());
    module.insert("card".into(), "off".into());
    for key in ["title", "items", "text"] {
        module.insert(key.into(), "".into());
    }
    if name != "net" {
        return build_module(&module, bx, b.colors, b.style, b.env);
    }
    let mut inner = Build::new(module, bx, b.colors, b.style, b.env);
    let net = sources()
        .into_iter()
        .find(|(k, _)| *k == "net")
        .map(|(_, s)| s)
        .expect("the network's source");
    build_net(&mut inner, &net)?;
    Ok(inner.out)
}

/// build_cards: a slot: the module's `items` are the cards it holds, one a line; the first
/// that has something to show is on show (render.slot). Every card is built for the whole
/// slot.
pub fn build_cards(b: &mut Build, _src: &Source) -> Result<()> {
    b.card(false)?;
    let inset = 1.max(round(b.w.min(b.h) as f64 / 140.0)) + 1;
    let bx = [b.x + inset, b.y + inset, b.w - 2 * inset, b.h - 2 * inset];
    for (i, (name, _rule)) in slot_items(&b.module).into_iter().enumerate() {
        for mut widget in child(b, &name, bx)? {
            let id = widget.get("id").map(Value::str).unwrap_or_default();
            widget.insert("id".into(), format!("c{i}-{id}").into());
            widget.insert("_card".into(), Value::int(i as i64));
            b.out.push(widget);
        }
    }
    Ok(())
}

/// build_netcard: a download while one runs, else the network.
pub fn build_netcard(b: &mut Build, src: &Source) -> Result<()> {
    build_cards(b, src)
}
