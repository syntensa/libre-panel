//! ImageColor as Pillow 13.0 has it: a colour from its name or one of the forms Pillow reads
//! (#rgb, #rgba, #rrggbb, #rrggbbaa, rgb(), rgb() in percent, hsl(), hsv() and hsb(), rgba()),
//! and that colour for an image's mode (getcolor). Python's regular expressions are matched
//! as Python matches them: `$` before a last line end too, `\d` any decimal digit, `\s` Python's
//! white space, lower() and the division of ints Python's (lp-py). Python's ints have no end:
//! a number beyond 64 bits, read or worked out, is kept here at the largest 64-bit one (a
//! colour is only ever drawn with bands of 0 to 255; Pillow refuses such a number when it
//! draws).

use crate::{error, Result};

/// ImageColor's colour names (CSS's), lowercase.
const COLORMAP: &[(&str, &str)] = &[
    ("aliceblue", "#f0f8ff"),
    ("antiquewhite", "#faebd7"),
    ("aqua", "#00ffff"),
    ("aquamarine", "#7fffd4"),
    ("azure", "#f0ffff"),
    ("beige", "#f5f5dc"),
    ("bisque", "#ffe4c4"),
    ("black", "#000000"),
    ("blanchedalmond", "#ffebcd"),
    ("blue", "#0000ff"),
    ("blueviolet", "#8a2be2"),
    ("brown", "#a52a2a"),
    ("burlywood", "#deb887"),
    ("cadetblue", "#5f9ea0"),
    ("chartreuse", "#7fff00"),
    ("chocolate", "#d2691e"),
    ("coral", "#ff7f50"),
    ("cornflowerblue", "#6495ed"),
    ("cornsilk", "#fff8dc"),
    ("crimson", "#dc143c"),
    ("cyan", "#00ffff"),
    ("darkblue", "#00008b"),
    ("darkcyan", "#008b8b"),
    ("darkgoldenrod", "#b8860b"),
    ("darkgray", "#a9a9a9"),
    ("darkgrey", "#a9a9a9"),
    ("darkgreen", "#006400"),
    ("darkkhaki", "#bdb76b"),
    ("darkmagenta", "#8b008b"),
    ("darkolivegreen", "#556b2f"),
    ("darkorange", "#ff8c00"),
    ("darkorchid", "#9932cc"),
    ("darkred", "#8b0000"),
    ("darksalmon", "#e9967a"),
    ("darkseagreen", "#8fbc8f"),
    ("darkslateblue", "#483d8b"),
    ("darkslategray", "#2f4f4f"),
    ("darkslategrey", "#2f4f4f"),
    ("darkturquoise", "#00ced1"),
    ("darkviolet", "#9400d3"),
    ("deeppink", "#ff1493"),
    ("deepskyblue", "#00bfff"),
    ("dimgray", "#696969"),
    ("dimgrey", "#696969"),
    ("dodgerblue", "#1e90ff"),
    ("firebrick", "#b22222"),
    ("floralwhite", "#fffaf0"),
    ("forestgreen", "#228b22"),
    ("fuchsia", "#ff00ff"),
    ("gainsboro", "#dcdcdc"),
    ("ghostwhite", "#f8f8ff"),
    ("gold", "#ffd700"),
    ("goldenrod", "#daa520"),
    ("gray", "#808080"),
    ("grey", "#808080"),
    ("green", "#008000"),
    ("greenyellow", "#adff2f"),
    ("honeydew", "#f0fff0"),
    ("hotpink", "#ff69b4"),
    ("indianred", "#cd5c5c"),
    ("indigo", "#4b0082"),
    ("ivory", "#fffff0"),
    ("khaki", "#f0e68c"),
    ("lavender", "#e6e6fa"),
    ("lavenderblush", "#fff0f5"),
    ("lawngreen", "#7cfc00"),
    ("lemonchiffon", "#fffacd"),
    ("lightblue", "#add8e6"),
    ("lightcoral", "#f08080"),
    ("lightcyan", "#e0ffff"),
    ("lightgoldenrodyellow", "#fafad2"),
    ("lightgreen", "#90ee90"),
    ("lightgray", "#d3d3d3"),
    ("lightgrey", "#d3d3d3"),
    ("lightpink", "#ffb6c1"),
    ("lightsalmon", "#ffa07a"),
    ("lightseagreen", "#20b2aa"),
    ("lightskyblue", "#87cefa"),
    ("lightslategray", "#778899"),
    ("lightslategrey", "#778899"),
    ("lightsteelblue", "#b0c4de"),
    ("lightyellow", "#ffffe0"),
    ("lime", "#00ff00"),
    ("limegreen", "#32cd32"),
    ("linen", "#faf0e6"),
    ("magenta", "#ff00ff"),
    ("maroon", "#800000"),
    ("mediumaquamarine", "#66cdaa"),
    ("mediumblue", "#0000cd"),
    ("mediumorchid", "#ba55d3"),
    ("mediumpurple", "#9370db"),
    ("mediumseagreen", "#3cb371"),
    ("mediumslateblue", "#7b68ee"),
    ("mediumspringgreen", "#00fa9a"),
    ("mediumturquoise", "#48d1cc"),
    ("mediumvioletred", "#c71585"),
    ("midnightblue", "#191970"),
    ("mintcream", "#f5fffa"),
    ("mistyrose", "#ffe4e1"),
    ("moccasin", "#ffe4b5"),
    ("navajowhite", "#ffdead"),
    ("navy", "#000080"),
    ("oldlace", "#fdf5e6"),
    ("olive", "#808000"),
    ("olivedrab", "#6b8e23"),
    ("orange", "#ffa500"),
    ("orangered", "#ff4500"),
    ("orchid", "#da70d6"),
    ("palegoldenrod", "#eee8aa"),
    ("palegreen", "#98fb98"),
    ("paleturquoise", "#afeeee"),
    ("palevioletred", "#db7093"),
    ("papayawhip", "#ffefd5"),
    ("peachpuff", "#ffdab9"),
    ("peru", "#cd853f"),
    ("pink", "#ffc0cb"),
    ("plum", "#dda0dd"),
    ("powderblue", "#b0e0e6"),
    ("purple", "#800080"),
    ("rebeccapurple", "#663399"),
    ("red", "#ff0000"),
    ("rosybrown", "#bc8f8f"),
    ("royalblue", "#4169e1"),
    ("saddlebrown", "#8b4513"),
    ("salmon", "#fa8072"),
    ("sandybrown", "#f4a460"),
    ("seagreen", "#2e8b57"),
    ("seashell", "#fff5ee"),
    ("sienna", "#a0522d"),
    ("silver", "#c0c0c0"),
    ("skyblue", "#87ceeb"),
    ("slateblue", "#6a5acd"),
    ("slategray", "#708090"),
    ("slategrey", "#708090"),
    ("snow", "#fffafa"),
    ("springgreen", "#00ff7f"),
    ("steelblue", "#4682b4"),
    ("tan", "#d2b48c"),
    ("teal", "#008080"),
    ("thistle", "#d8bfd8"),
    ("tomato", "#ff6347"),
    ("turquoise", "#40e0d0"),
    ("violet", "#ee82ee"),
    ("wheat", "#f5deb3"),
    ("white", "#ffffff"),
    ("whitesmoke", "#f5f5f5"),
    ("yellow", "#ffff00"),
    ("yellowgreen", "#9acd32"),
];

/// Where a pattern ends: the end of the text, or a line end that is its last character.
fn ends(rest: &str) -> bool {
    rest.is_empty() || rest == "\n"
}

/// What a pattern of Python's has read so far: the rest of the text.
struct Scan<'a>(&'a str);

impl<'a> Scan<'a> {
    fn literal(&mut self, text: &str) -> Option<()> {
        self.0 = self.0.strip_prefix(text)?;
        Some(())
    }

    fn spaces(&mut self) {
        self.0 = self.0.trim_start_matches(lp_py::isspace);
    }

    /// \d+: the digits (Python's, of any script) as ASCII ones.
    fn digits(&mut self) -> Option<String> {
        let mut out = String::new();
        let mut rest = self.0;
        while let Some(ch) = rest.chars().next() {
            match lp_py::decimal(ch) {
                Some(d) => {
                    out.push(char::from_digit(d, 10).expect("a digit"));
                    rest = &rest[ch.len_utf8()..];
                }
                None => break,
            }
        }
        if out.is_empty() {
            return None;
        }
        self.0 = rest;
        Some(out)
    }

    /// \d+\.?\d*: a number as float() reads it.
    fn number(&mut self) -> Option<f64> {
        let mut text = self.digits()?;
        if let Some(rest) = self.0.strip_prefix('.') {
            self.0 = rest;
            text.push('.');
            let start = self.0;
            if let Some(more) = self.digits() {
                text.push_str(&more);
            } else {
                self.0 = start;
            }
        }
        text.parse().ok()
    }
}

fn int(digits: &str) -> i64 {
    digits
        .parse::<i128>()
        .map_or(i64::MAX, |n| n.clamp(0, i128::from(i64::MAX)) as i64)
}

/// rgb(\s*(\d+)%?\s*,\s*...\s*\)$ with `n` numbers, each followed by `suffix`.
fn call(color: &str, name: &str, n: usize, suffix: &str) -> Option<Vec<String>> {
    let mut s = Scan(color);
    s.literal(name)?;
    s.literal("(")?;
    let mut values = Vec::with_capacity(n);
    for i in 0..n {
        s.spaces();
        values.push(s.digits()?);
        s.literal(suffix)?;
        s.spaces();
        s.literal(if i + 1 < n { "," } else { ")" })?;
    }
    ends(s.0).then_some(values)
}

/// hsl(\s*(\d+\.?\d*)\s*,\s*(\d+\.?\d*)%\s*,\s*(\d+\.?\d*)%\s*\)$ and the like.
fn hue_call(color: &str, names: &[&str]) -> Option<[f64; 3]> {
    for name in names {
        let mut s = Scan(color);
        if s.literal(name).is_none() || s.literal("(").is_none() {
            continue;
        }
        let mut values = [0.0; 3];
        for (i, value) in values.iter_mut().enumerate() {
            s.spaces();
            *value = s.number()?;
            if i > 0 {
                s.literal("%")?;
            }
            s.spaces();
            s.literal(if i < 2 { "," } else { ")" })?;
        }
        return ends(s.0).then_some(values);
    }
    None
}

const ONE_THIRD: f64 = 1.0 / 3.0;
const ONE_SIXTH: f64 = 1.0 / 6.0;
const TWO_THIRD: f64 = 2.0 / 3.0;

/// Python's float `%` with a positive divisor: the result has the divisor's sign.
fn py_mod(x: f64, m: f64) -> f64 {
    let r = x % m;
    if r != 0.0 && (r < 0.0) != (m < 0.0) {
        r + m
    } else {
        r
    }
}

fn v(m1: f64, m2: f64, hue: f64) -> f64 {
    let hue = py_mod(hue, 1.0);
    if hue < ONE_SIXTH {
        return m1 + (m2 - m1) * hue * 6.0;
    }
    if hue < 0.5 {
        return m2;
    }
    if hue < TWO_THIRD {
        return m1 + (m2 - m1) * (TWO_THIRD - hue) * 6.0;
    }
    m1
}

/// colorsys.hls_to_rgb.
fn hls_to_rgb(h: f64, l: f64, s: f64) -> [f64; 3] {
    if s == 0.0 {
        return [l, l, l];
    }
    let m2 = if l <= 0.5 {
        l * (1.0 + s)
    } else {
        l + s - (l * s)
    };
    let m1 = 2.0 * l - m2;
    [
        v(m1, m2, h + ONE_THIRD),
        v(m1, m2, h),
        v(m1, m2, h - ONE_THIRD),
    ]
}

/// colorsys.hsv_to_rgb.
fn hsv_to_rgb(h: f64, s: f64, v: f64) -> [f64; 3] {
    if s == 0.0 {
        return [v, v, v];
    }
    let i = (h * 6.0).trunc();
    let f = (h * 6.0) - i;
    let p = v * (1.0 - s);
    let q = v * (1.0 - s * f);
    let t = v * (1.0 - s * (1.0 - f));
    match (i as i64).rem_euclid(6) {
        0 => [v, t, p],
        1 => [q, v, p],
        2 => [p, v, t],
        3 => [p, q, v],
        4 => [t, p, v],
        _ => [v, p, q],
    }
}

/// colorsys.rgb_to_hsv.
fn rgb_to_hsv(r: f64, g: f64, b: f64) -> [f64; 3] {
    let maxc = r.max(g).max(b);
    let minc = r.min(g).min(b);
    let rangec = maxc - minc;
    if minc == maxc {
        return [0.0, 0.0, maxc];
    }
    let s = rangec / maxc;
    let rc = (maxc - r) / rangec;
    let gc = (maxc - g) / rangec;
    let bc = (maxc - b) / rangec;
    let h = if r == maxc {
        bc - gc
    } else if g == maxc {
        2.0 + rc - bc
    } else {
        4.0 + gc - rc
    };
    [py_mod(h / 6.0, 1.0), s, maxc]
}

fn hex(c: u8) -> i64 {
    i64::from((c as char).to_digit(16).expect("a hex digit"))
}

/// ImageColor.getrgb: the colour's (r, g, b) or (r, g, b, a).
pub fn getrgb(color: &str) -> Result<Vec<i64>> {
    if color.chars().count() > 100 {
        return error("color specifier is too long");
    }
    let color = lp_py::lower(color);
    if let Some(&(_, hex_value)) = COLORMAP.iter().find(|(name, _)| *name == color) {
        return getrgb(hex_value);
    }
    let b = color.as_bytes();
    let hexes = |n: usize| {
        b.first() == Some(&b'#')
            && b.len() > n
            && b[1..=n]
                .iter()
                .all(|c| matches!(c, b'a'..=b'f' | b'0'..=b'9'))
            && ends(&color[n + 1..])
    };
    if hexes(3) {
        return Ok(b[1..4].iter().map(|&c| hex(c) * 17).collect());
    }
    if hexes(4) {
        return Ok(b[1..5].iter().map(|&c| hex(c) * 17).collect());
    }
    if hexes(6) || hexes(8) {
        let n = if hexes(6) { 6 } else { 8 };
        return Ok(b[1..=n]
            .chunks(2)
            .map(|p| hex(p[0]) * 16 + hex(p[1]))
            .collect());
    }
    if let Some(v) = call(&color, "rgb", 3, "") {
        return Ok(v.iter().map(|d| int(d)).collect());
    }
    if let Some(v) = call(&color, "rgb", 3, "%") {
        return Ok(v
            .iter()
            .map(|d| ((i128::from(int(d)) * 255) as f64 / 100.0 + 0.5) as i64)
            .collect());
    }
    if let Some([h, s, l]) = hue_call(&color, &["hsl"]) {
        let rgb = hls_to_rgb(h / 360.0, l / 100.0, s / 100.0);
        return Ok(rgb.iter().map(|c| (c * 255.0 + 0.5) as i64).collect());
    }
    if let Some([h, s, v]) = hue_call(&color, &["hsb", "hsv"]) {
        let rgb = hsv_to_rgb(h / 360.0, s / 100.0, v / 100.0);
        return Ok(rgb.iter().map(|c| (c * 255.0 + 0.5) as i64).collect());
    }
    if let Some(v) = call(&color, "rgba", 4, "") {
        return Ok(v.iter().map(|d| int(d)).collect());
    }
    error(format!("unknown color specifier: {}", lp_py::repr(&color)))
}

/// The base mode Pillow converts a mode's colours through (ImageMode's basemode).
fn basemode(mode: &str) -> Option<&'static str> {
    Some(match mode {
        "1" | "L" | "LA" | "La" | "I" | "F" | "I;16" | "I;16L" | "I;16B" | "I;16N" => "L",
        "P" => "P",
        "PA" | "RGB" | "RGBX" | "RGBA" | "RGBa" | "CMYK" | "YCbCr" | "LAB" | "HSV" => "RGB",
        _ => return None,
    })
}

/// ImageColor.getcolor: the colour as an image of `mode` has it (one value, or a tuple).
pub fn getcolor(color: &str, mode: &str) -> Result<Vec<i64>> {
    let mut rgb = getrgb(color)?;
    let mut alpha = 255;
    if rgb.len() == 4 {
        alpha = rgb[3];
        rgb.truncate(3);
    }
    if mode == "HSV" {
        let [r, g, b] = [rgb[0], rgb[1], rgb[2]].map(|c| lp_py::true_div(i128::from(c), 255));
        let hsv = rgb_to_hsv(r, g, b);
        return Ok(hsv.iter().map(|c| (c * 255.0) as i64).collect());
    }
    let Some(base) = basemode(mode) else {
        return error(mode.to_string());
    };
    let wide = |v: i128| v.clamp(i128::from(i64::MIN), i128::from(i64::MAX)) as i64;
    let mut value = if base == "L" {
        let [r, g, b] = [rgb[0], rgb[1], rgb[2]].map(i128::from);
        vec![wide((r * 19595 + g * 38470 + b * 7471 + 0x8000) >> 16)]
    } else {
        rgb
    };
    if mode.ends_with('a') {
        for band in &mut value {
            *band =
                lp_py::round(lp_py::true_div(i128::from(*band) * i128::from(alpha), 255)) as i64;
        }
    }
    if mode.ends_with('A') || mode.ends_with('a') {
        value.push(alpha);
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn colours_are_read_as_pillow_reads_them() {
        assert_eq!(getrgb("#ABC").unwrap(), vec![170, 187, 204]);
        assert_eq!(getrgb("#abc\n").unwrap(), vec![170, 187, 204]);
        assert_eq!(getrgb("#11223344").unwrap(), vec![17, 34, 51, 68]);
        assert_eq!(getrgb("White").unwrap(), vec![255, 255, 255]);
        assert_eq!(getrgb("rgb( 1 , 2 ,3)").unwrap(), vec![1, 2, 3]);
        assert_eq!(getrgb("rgb(\u{663},\u{661},2)").unwrap(), vec![3, 1, 2]);
        assert_eq!(getrgb("rgb(50%,0%,100%)").unwrap(), vec![128, 0, 255]);
        assert_eq!(getrgb("hsl(120, 100%, 25%)").unwrap(), vec![0, 128, 0]);
        assert_eq!(getrgb("hsv(0,100%,100%)").unwrap(), vec![255, 0, 0]);
        assert!(
            getrgb("#abcde").is_err() && getrgb("rgb(1,2)").is_err() && getrgb("nope").is_err()
        );
        assert_eq!(getcolor("#ff0000", "L").unwrap(), vec![76]);
        assert_eq!(getcolor("#ff000080", "RGBa").unwrap(), vec![128, 0, 0, 128]);
    }
}
