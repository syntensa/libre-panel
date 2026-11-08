//! `lp-check`: runs the operations it reads, one JSON object a line on stdin, and writes each
//! one's result as a line on stdout, for the tests that compare Libre Panel's Rust crates with
//! the Python app (tests/test_rust_*.py, docs/RUST.md).
//!
//! An image goes as {"mode": "RGBA", "size": [w, h], "data": base64 of Pillow's tobytes()}.
//! A result is {"image": ...}, {"images": [...]}, {"box": [x0, y0, x1, y1] or null}, or
//! {"error": "..."} where the operation refuses what it was given.

use std::io::{self, BufRead, BufWriter, Write};

mod devices;

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use lp_pixels::{Canvas, Draw, Image, Mode};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

fn image_of(v: &Value) -> Result<Image, String> {
    let mode = v["mode"]
        .as_str()
        .and_then(Mode::from_name)
        .ok_or("an image without a mode")?;
    let size = v["size"].as_array().ok_or("an image without a size")?;
    let w = size[0].as_u64().ok_or("a width")? as usize;
    let h = size[1].as_u64().ok_or("a height")? as usize;
    let data = STANDARD
        .decode(v["data"].as_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    Image::from_bytes(mode, w, h, data).map_err(|e| e.to_string())
}

fn value_of(im: &Image) -> Value {
    json!({
        "mode": im.mode().name(),
        "size": [im.width(), im.height()],
        "data": STANDARD.encode(im.as_bytes()),
    })
}

fn ints(v: &Value) -> Result<Vec<i64>, String> {
    v.as_array()
        .ok_or("a list of numbers")?
        .iter()
        .map(|n| n.as_i64().ok_or_else(|| format!("{n} is no whole number")))
        .collect()
}

fn floats4(v: &Value) -> Result<Option<(f64, f64, f64, f64)>, String> {
    if v.is_null() {
        return Ok(None);
    }
    let a = v.as_array().ok_or("a box")?;
    let f = |i: usize| a[i].as_f64().ok_or("a number");
    Ok(Some((f(0)?, f(1)?, f(2)?, f(3)?)))
}

/// A mode-F image goes as {"mode": "F", "size": [w, h], "data": base64 of its float32s}.
fn float_of(v: &Value) -> Result<lp_pixels::FloatImage, String> {
    let size = v["size"].as_array().ok_or("an image without a size")?;
    let w = size[0].as_u64().ok_or("a width")? as usize;
    let h = size[1].as_u64().ok_or("a height")? as usize;
    let bytes = STANDARD
        .decode(v["data"].as_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    let data = bytes
        .chunks_exact(4)
        .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
        .collect();
    lp_pixels::FloatImage::from_vec(w, h, data).map_err(|e| e.to_string())
}

fn float_value(im: &lp_pixels::FloatImage) -> Value {
    let bytes: Vec<u8> = im.data.iter().flat_map(|f| f.to_le_bytes()).collect();
    json!({"mode": "F", "size": [im.width, im.height], "data": STANDARD.encode(bytes)})
}

fn points(v: &Value) -> Result<Vec<(f64, f64)>, String> {
    v.as_array()
        .ok_or("points")?
        .iter()
        .map(|p| Ok((p[0].as_f64().ok_or("x")?, p[1].as_f64().ok_or("y")?)))
        .collect()
}

fn mask_of(v: &Value) -> Result<Option<Image>, String> {
    if v.is_null() {
        Ok(None)
    } else {
        image_of(v).map(Some)
    }
}

/// One drawing command on a canvas: the core's (`draw`: kind "lines", "fill" true or false, an
/// "ink") or, `own`, ImageDraw's methods (`imagedraw`: kind "line", "fill" and "outline" inks
/// or null), as Pillow's keyword names have them; bitmaps by `bitmap` (bytes only).
fn drawn<C: Canvas>(
    d: &mut Draw<C>,
    c: &Value,
    own: bool,
    ink: impl Fn(&Value) -> Result<Option<C::Ink>, String>,
    bitmap: impl FnOnce(&mut Draw<C>, &Value) -> Result<(), String>,
) -> Result<(), String> {
    let width = c["width"].as_i64().unwrap_or(1) as i32;
    let kind = c["kind"].as_str().unwrap_or("");
    if kind == "bitmap" {
        return bitmap(d, c);
    }
    let bounds = || floats4(&c["xy"])?.ok_or_else(|| "a box".to_string());
    let (start, end) = (
        c["start"].as_f64().unwrap_or(0.0),
        c["end"].as_f64().unwrap_or(0.0),
    );
    if own {
        let (fill, outline) = (ink(&c["fill"])?, ink(&c["outline"])?);
        match kind {
            "point" => d.point(&points(&c["xy"])?, fill),
            "line" => {
                let curve = c["joint"].as_str() == Some("curve");
                d.line(&points(&c["xy"])?, fill, width, curve)
            }
            "rectangle" => d.rectangle(bounds()?, fill, outline, width),
            "rounded_rectangle" => {
                let mut corners = lp_pixels::ALL_CORNERS;
                if let Some(given) = c["corners"].as_array() {
                    for (corner, v) in corners.iter_mut().zip(given) {
                        *corner = v.as_bool().unwrap_or(true);
                    }
                }
                let radius = c["radius"].as_f64().unwrap_or(0.0);
                d.rounded_rectangle(bounds()?, radius, fill, outline, width, corners)
            }
            "ellipse" => d.ellipse(bounds()?, fill, outline, width),
            "circle" => {
                let at = points(&c["xy"])?;
                d.circle(
                    at[0],
                    c["radius"].as_f64().unwrap_or(0.0),
                    fill,
                    outline,
                    width,
                )
            }
            "arc" => d.arc(bounds()?, start, end, fill, width),
            "chord" => d.chord(bounds()?, start, end, fill, outline, width),
            "pieslice" => d.pieslice(bounds()?, start, end, fill, outline, width),
            "polygon" => d.polygon(&points(&c["xy"])?, fill, outline, width),
            other => return Err(format!("no ImageDraw.{other}")),
        }
    } else {
        let ink = ink(&c["ink"])?.ok_or("an ink")?;
        let filled = c["fill"].as_bool().unwrap_or(false);
        let (s, e) = (start as f32, end as f32);
        match kind {
            "points" => d.draw_points(&points(&c["xy"])?, ink),
            "lines" => d.draw_lines(&points(&c["xy"])?, ink, width),
            "polygon" => d.draw_polygon(&points(&c["xy"])?, ink, filled, width),
            "rectangle" => d.draw_rectangle(bounds()?, ink, filled, width),
            "ellipse" => d.draw_ellipse(bounds()?, ink, filled, width),
            "arc" => d.draw_arc(bounds()?, s, e, ink, width),
            "chord" => d.draw_chord(bounds()?, s, e, ink, filled, width),
            "pieslice" => d.draw_pieslice(bounds()?, s, e, ink, filled, width),
            other => return Err(format!("no drawing {other}")),
        }
    }
    .map_err(|err| err.to_string())
}

fn run(case: &Value) -> Result<Value, String> {
    let op = case["op"].as_str().ok_or("a case without an op")?;
    let e = |err: lp_pixels::Error| err.to_string();
    Ok(match op {
        "convert" => {
            let mode = case["mode"]
                .as_str()
                .and_then(Mode::from_name)
                .ok_or("a mode")?;
            json!({"image": value_of(&image_of(&case["image"])?.convert(mode).map_err(e)?)})
        }
        "alpha_composite" => {
            let out = lp_pixels::alpha_composite(&image_of(&case["a"])?, &image_of(&case["b"])?)
                .map_err(e)?;
            json!({"image": value_of(&out)})
        }
        "alpha_composite_at" => {
            let mut im = image_of(&case["image"])?;
            let (d, s) = (ints(&case["dest"])?, ints(&case["source"])?);
            im.alpha_composite_at(
                &image_of(&case["over"])?,
                (d[0], d[1]),
                (s[0], s[1], s[2], s[3]),
            )
            .map_err(e)?;
            json!({"image": value_of(&im)})
        }
        "crop" => {
            let b = ints(&case["box"])?;
            json!({"image": value_of(&image_of(&case["image"])?.crop(b[0], b[1], b[2], b[3]))})
        }
        "paste" => {
            let mut im = image_of(&case["image"])?;
            let at = ints(&case["at"])?;
            let mask = mask_of(&case["mask"])?;
            im.paste(&image_of(&case["paste"])?, at[0], at[1], mask.as_ref())
                .map_err(e)?;
            json!({"image": value_of(&im)})
        }
        "paste_colour" => {
            let mut im = image_of(&case["image"])?;
            let colour: Vec<u8> = ints(&case["colour"])?.iter().map(|&v| v as u8).collect();
            let b = ints(&case["box"])?;
            let mask = mask_of(&case["mask"])?;
            im.paste_colour(&colour, (b[0], b[1], b[2], b[3]), mask.as_ref())
                .map_err(e)?;
            json!({"image": value_of(&im)})
        }
        "point" => {
            let out = image_of(&case["image"])?
                .point(&ints(&case["lut"])?)
                .map_err(e)?;
            json!({"image": value_of(&out)})
        }
        "getchannel" => {
            let band = case["band"].as_u64().ok_or("a band")? as usize;
            json!({"image": value_of(&image_of(&case["image"])?.getchannel(band).map_err(e)?)})
        }
        "split" => {
            let parts: Vec<Value> = image_of(&case["image"])?
                .split()
                .iter()
                .map(value_of)
                .collect();
            json!({"images": parts})
        }
        "merge" => {
            let mode = case["mode"]
                .as_str()
                .and_then(Mode::from_name)
                .ok_or("a mode")?;
            let bands = case["bands"].as_array().ok_or("bands")?;
            let images: Vec<Image> = bands.iter().map(image_of).collect::<Result<_, _>>()?;
            let refs: Vec<&Image> = images.iter().collect();
            json!({"image": value_of(&lp_pixels::merge(mode, &refs).map_err(e)?)})
        }
        "putalpha" => {
            let mut im = image_of(&case["image"])?;
            if let Some(value) = case["value"].as_u64() {
                im.putalpha_value(value as u8).map_err(e)?;
            } else {
                im.putalpha(&image_of(&case["alpha"])?).map_err(e)?;
            }
            json!({"image": value_of(&im)})
        }
        "jpeg_save" | "dimmed" | "hongtai_frame" | "hongtai_picture" | "hongtai_jpeg"
        | "hongtai_answer" | "hongtai_session" => devices::run(op, case)?,
        "icon" => {
            // icons.py's draw_icon(name, size, color, stroke)
            let colour: Vec<i64> = ints(&case["color"])?;
            let ink =
                [0, 1, 2, 3].map(|i| colour.get(i).copied().unwrap_or(255).clamp(0, 255) as u8);
            let size = case["size"].as_i64().ok_or("a size")?;
            let stroke = case["stroke"].as_f64().ok_or("a stroke")?;
            let name = case["name"].as_str().unwrap_or("");
            match lp_render::icons::draw_icon(name, size, ink, stroke) {
                Ok(icon) => json!({"image": value_of(&icon)}),
                Err(e) => json!({"error": e.0}),
            }
        }
        "strftime" => {
            // datetime(...).strftime(token) for each case: Python's own codes and the C library's
            let answers: Vec<Value> = case["cases"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|c| {
                    let at = datetime_of(c["at"].as_array().map_or(&[], Vec::as_slice));
                    match lp_py::strftime::strftime(c["token"].as_str().unwrap_or(""), &at) {
                        Ok(text) => json!(text),
                        Err(lp_py::strftime::Refused::ValueError(msg)) => json!({"error": msg}),
                        Err(lp_py::strftime::Refused::Unported(what)) => json!({"unported": what}),
                    }
                })
                .collect();
            json!(answers)
        }
        "format_date" => {
            // i18n.format_date(moment, fmt) for each case, in its language
            let locale = std::path::PathBuf::from(case["locale"].as_str().unwrap_or(""));
            let mut answers = Vec::new();
            for c in case["cases"].as_array().into_iter().flatten() {
                let code = c["language"].as_str().unwrap_or("en");
                lp_i18n::set_language(lp_i18n::Language::load(&locale, code)?);
                let at = datetime_of(c["at"].as_array().map_or(&[], Vec::as_slice));
                answers.push(
                    match lp_i18n::format_date(&at, c["fmt"].as_str().unwrap_or("")) {
                        Ok(text) => json!(text),
                        Err(lp_i18n::DateError::ValueError(msg)) => json!({"error": msg}),
                        Err(lp_i18n::DateError::Failed(msg)) => json!({"failed": msg}),
                        Err(lp_i18n::DateError::Unported(what)) => json!({"unported": what}),
                    },
                );
            }
            lp_i18n::set_language(lp_i18n::Language::english());
            json!(answers)
        }
        "graph_range" => {
            // the range a graph is drawn in, in a still frame (_glided_bounds), of each case's
            // widget (min, max, range_step, as typed values) and numbers: its ends as typed
            // values, or {"failed": what Python raises}
            let answers: Vec<Value> = case["cases"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|c| {
                    let mut w = lp_py::Dict::new();
                    for (k, v) in c["widget"].as_object().into_iter().flatten() {
                        w.insert(k.clone(), typed(v));
                    }
                    let numbers: Vec<f64> = c["numbers"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .map(|v| match typed(v) {
                            lp_py::Value::Float(f) => f,
                            _ => f64::NAN,
                        })
                        .collect();
                    if numbers.is_empty() {
                        return json!({"error": "no numbers"});
                    }
                    // (each end typed as Python has it: an int or a float)
                    let typed_out = |v: &lp_py::Value| match v {
                        lp_py::Value::Int(n) => json!({"i": n.to_string()}),
                        lp_py::Value::Float(f) => json!({"f": format!("{:016x}", f.to_bits())}),
                        lp_py::Value::Bool(b) => json!({"b": b}),
                        _ => json!({"n": null}),
                    };
                    match lp_render::graph_range(&w, &numbers) {
                        Ok((lo, hi)) => json!([typed_out(&lo), typed_out(&hi)]),
                        Err(e) => json!({"failed": e}),
                    }
                })
                .collect();
            json!(answers)
        }
        "countdown_text" => {
            // countdown.py's countdown_text(text, part, now, done) for each case, in its language
            let locale = std::path::PathBuf::from(case["locale"].as_str().unwrap_or(""));
            let mut answers = Vec::new();
            for c in case["cases"].as_array().into_iter().flatten() {
                let code = c["language"].as_str().unwrap_or("en");
                lp_i18n::set_language(lp_i18n::Language::load(&locale, code)?);
                let now = datetime_of(c["now"].as_array().map_or(&[], Vec::as_slice));
                let s = |k: &str| c[k].as_str().unwrap_or("");
                let (text, part, done) = (s("text"), s("part"), s("done"));
                answers.push(
                    match lp_render::countdown::countdown_text(text, part, &now, done) {
                        Ok(lp_render::countdown::Said::Text(text)) => json!(text),
                        Ok(lp_render::countdown::Said::Unported(what)) => json!({"unported": what}),
                        Err(e) => json!({"error": e.0}),
                    },
                );
            }
            lp_i18n::set_language(lp_i18n::Language::english());
            json!(answers)
        }
        "icon_names" => {
            // weather_icon_name(code, night) and link_icon(kind, signal) for each case
            let names: Vec<Value> = case["weather"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|w| {
                    let night = w["night"].as_bool().unwrap_or(false);
                    json!(lp_render::icons::weather_icon_name(
                        &typed(&w["code"]),
                        night
                    ))
                })
                .collect();
            let links: Vec<Value> = case["link"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|l| {
                    json!(lp_render::icons::link_icon(
                        &typed(&l["kind"]),
                        &typed(&l["signal"])
                    ))
                })
                .collect();
            json!({"weather": names, "link": links})
        }
        "getbbox" => {
            let alpha_only = case["alpha_only"].as_bool().unwrap_or(true);
            let found = image_of(&case["image"])?.getbbox(alpha_only);
            json!({"box": found.map(|(a, b, c, d)| vec![a, b, c, d])})
        }
        "reduce" => {
            let f = ints(&case["factor"])?;
            let bounds = if case["box"].is_null() {
                None
            } else {
                let b = ints(&case["box"])?;
                Some((b[0] as usize, b[1] as usize, b[2] as usize, b[3] as usize))
            };
            let out = image_of(&case["image"])?
                .reduce(f[0] as usize, f[1] as usize, bounds)
                .map_err(e)?;
            json!({"image": value_of(&out)})
        }
        "dither_noise" | "to_u8" | "ramp_u8" | "blur_floats" => {
            // render/dither.py's: floats as little-endian float32s in base64
            let floats = |key: &str| -> Result<Vec<f32>, String> {
                let bytes = STANDARD
                    .decode(case[key].as_str().unwrap_or(""))
                    .map_err(|err| err.to_string())?;
                Ok(bytes
                    .chunks_exact(4)
                    .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
                    .collect())
            };
            let n = |key: &str| case[key].as_i64().unwrap_or(0);
            let flag = |key: &str| case[key].as_bool().unwrap_or(true);
            let encode = |v: &[f32]| -> String {
                STANDARD.encode(v.iter().flat_map(|f| f.to_le_bytes()).collect::<Vec<u8>>())
            };
            use lp_pixels::dither;
            match op {
                "dither_noise" => {
                    let out = dither::noise(n("w") as usize, n("h") as usize, n("x"), n("y"));
                    json!({ "floats": encode(&out) })
                }
                "to_u8" => {
                    let (w, h, c) = (n("w") as usize, n("h") as usize, n("c").max(1) as usize);
                    let out =
                        dither::to_u8(&floats("values")?, w, h, c, n("x"), n("y"), flag("dither"));
                    json!({ "bytes": STANDARD.encode(out) })
                }
                "ramp_u8" => {
                    let (n_, c) = (n("n") as usize, n("c").max(1) as usize);
                    let out = dither::ramp_u8(
                        &floats("line")?,
                        n_,
                        c,
                        n("length") as usize,
                        flag("across"),
                        flag("dither"),
                    );
                    json!({ "bytes": STANDARD.encode(out) })
                }
                _ => {
                    let radius = case["radius"].as_f64().unwrap_or(0.0);
                    let (w, h) = (n("w") as usize, n("h") as usize);
                    let out = dither::blur(&floats("values")?, h, w, radius);
                    json!({ "floats": encode(&out), "reach": dither::reach(radius) })
                }
            }
        }
        "quantize" => {
            // im.quantize(colors, method=MEDIANCUT).convert("RGB"), and its palette
            let im = image_of(&case["image"])?;
            let colors = case["colors"].as_u64().unwrap_or(256) as usize;
            let (indices, palette) = im.quantize(colors).map_err(e)?;
            let rgb: Vec<u8> = indices
                .iter()
                .flat_map(|&i| palette[usize::from(i)])
                .collect();
            let out = Image::from_bytes(Mode::RGB, im.width(), im.height(), rgb).map_err(e)?;
            json!({"image": value_of(&out), "palette": palette})
        }
        "getcolors" => {
            // im.getcolors(maxcolors): [count, pixel] each, in Pillow's order; null past it
            let im = image_of(&case["image"])?;
            let maxcolors = case["maxcolors"].as_u64().unwrap_or(256) as usize;
            match im.getcolors(maxcolors).map_err(e)? {
                None => json!({ "colors": null }),
                Some(list) => {
                    let list: Vec<Value> = list.iter().map(|(c, px)| json!([c, px])).collect();
                    json!({ "colors": list })
                }
            }
        }
        "resize" => {
            let size = ints(&case["size"])?;
            let filter = case["filter"]
                .as_i64()
                .and_then(lp_pixels::Filter::from_number)
                .ok_or("a filter")?;
            let bounds = floats4(&case["box"])?;
            let gap = case["reducing_gap"].as_f64();
            if case["image"]["mode"] == "F" {
                let src = float_of(&case["image"])?;
                let out = src
                    .resize(size[0] as usize, size[1] as usize, filter, bounds)
                    .map_err(e)?;
                json!({"floats": float_value(&out)})
            } else {
                let out = image_of(&case["image"])?
                    .resize(size[0] as usize, size[1] as usize, filter, bounds, gap)
                    .map_err(e)?;
                json!({"image": value_of(&out)})
            }
        }
        "gaussian_blur" | "box_blur" => {
            let r = case["radius"].as_array().ok_or("a radius")?;
            // (a number, or one JSON has none of as text: "nan")
            let radius = |v: &Value| {
                v.as_f64()
                    .or_else(|| v.as_str().and_then(|t| t.parse().ok()))
                    .ok_or("a radius")
            };
            let (x, y) = (radius(&r[0])? as f32, radius(&r[1])? as f32);
            let im = image_of(&case["image"])?;
            let out = if op == "gaussian_blur" {
                im.gaussian_blur(x, y)
            } else {
                im.box_blur(x, y, case["passes"].as_u64().unwrap_or(1) as u32)
            }
            .map_err(e)?;
            json!({"image": value_of(&out)})
        }
        "chop" => {
            use lp_pixels::chops;
            let (a, b) = (image_of(&case["a"])?, image_of(&case["b"])?);
            let scale = case["scale"].as_f64().unwrap_or(1.0) as f32;
            let offset = case["offset"].as_i64().unwrap_or(0) as i32;
            let out = match case["name"].as_str().unwrap_or("") {
                "multiply" => chops::multiply(&a, &b),
                "screen" => chops::screen(&a, &b),
                "add" => chops::add(&a, &b, scale, offset),
                "subtract" => chops::subtract(&a, &b, scale, offset),
                "lighter" => chops::lighter(&a, &b),
                "darker" => chops::darker(&a, &b),
                "difference" => chops::difference(&a, &b),
                "add_modulo" => chops::add_modulo(&a, &b),
                "subtract_modulo" => chops::subtract_modulo(&a, &b),
                other => return Err(format!("no chop {other}")),
            }
            .map_err(e)?;
            json!({"image": value_of(&out)})
        }
        "blend" => {
            let alpha = case["alpha"].as_f64().ok_or("an alpha")? as f32;
            let out =
                lp_pixels::chops::blend(&image_of(&case["a"])?, &image_of(&case["b"])?, alpha)
                    .map_err(e)?;
            json!({"image": value_of(&out)})
        }
        "composite" => {
            let (a, b, m) = (
                image_of(&case["a"])?,
                image_of(&case["b"])?,
                image_of(&case["mask"])?,
            );
            json!({"image": value_of(&lp_pixels::chops::composite(&a, &b, &m).map_err(e)?)})
        }
        "offset" => {
            let im = image_of(&case["image"])?;
            let (x, y) = (
                case["x"].as_i64().ok_or("x")?,
                case["y"].as_i64().ok_or("y")?,
            );
            json!({"image": value_of(&im.offset(x, y))})
        }
        "transpose" => {
            let method = case["method"]
                .as_i64()
                .and_then(lp_pixels::Transpose::from_number)
                .ok_or("a transpose")?;
            json!({"image": value_of(&image_of(&case["image"])?.transpose(method))})
        }
        "transform" | "rotate" => {
            let im = image_of(&case["image"])?;
            let filter = lp_pixels::Filter::from_number(case["resample"].as_i64().unwrap_or(0))
                .ok_or("a filter")?;
            let fill: Option<Vec<u8>> = if case["fillcolor"].is_null() {
                None
            } else {
                Some(ints(&case["fillcolor"])?.iter().map(|&v| v as u8).collect())
            };
            let pair = |v: &Value| -> Result<Option<(f64, f64)>, String> {
                if v.is_null() {
                    return Ok(None);
                }
                Ok(Some((v[0].as_f64().ok_or("x")?, v[1].as_f64().ok_or("y")?)))
            };
            let out = if op == "rotate" {
                im.rotate(
                    case["angle"].as_f64().ok_or("an angle")?,
                    filter,
                    case["expand"].as_bool().unwrap_or(false),
                    pair(&case["center"])?,
                    pair(&case["translate"])?,
                    fill.as_deref(),
                )
            } else {
                let size = ints(&case["size"])?;
                let data: Vec<f64> = case["data"]
                    .as_array()
                    .ok_or("data")?
                    .iter()
                    .map(|v| v.as_f64().unwrap_or(f64::NAN))
                    .collect();
                let take = |n: usize| -> Result<Vec<f64>, String> {
                    data.get(..n)
                        .map(<[f64]>::to_vec)
                        .ok_or("too little data".into())
                };
                let method = match case["method"].as_str().unwrap_or("") {
                    "affine" => lp_pixels::Transform::Affine(take(6)?.try_into().unwrap()),
                    "extent" => lp_pixels::Transform::Extent(take(4)?.try_into().unwrap()),
                    "perspective" => {
                        lp_pixels::Transform::Perspective(take(8)?.try_into().unwrap())
                    }
                    "quad" => lp_pixels::Transform::Quad(take(8)?.try_into().unwrap()),
                    other => return Err(format!("no transform {other}")),
                };
                im.transform(
                    (size[0] as usize, size[1] as usize),
                    method,
                    filter,
                    fill.as_deref(),
                )
            }
            .map_err(e)?;
            json!({"image": value_of(&out)})
        }
        "thumbnail" | "contain" | "cover" | "pad" | "fit" => {
            let im = image_of(&case["image"])?;
            let size = case["size"].as_array().ok_or("a size")?;
            let num = |v: &Value| v.as_f64().ok_or("a number");
            let method = lp_pixels::Filter::from_number(case["method"].as_i64().unwrap_or(3))
                .ok_or("a filter")?;
            let whole = (num(&size[0])? as usize, num(&size[1])? as usize);
            let centering = match case["centering"].as_array() {
                Some(c) => (num(&c[0])?, num(&c[1])?),
                None => (0.5, 0.5),
            };
            let out = match op {
                "thumbnail" => {
                    let gap = case["reducing_gap"].as_f64();
                    im.thumbnail((num(&size[0])?, num(&size[1])?), method, gap)
                }
                "contain" => im.contain(whole, method),
                "cover" => im.cover(whole, method),
                "pad" => {
                    let colour: Option<Vec<u8>> = if case["color"].is_null() {
                        None
                    } else {
                        Some(ints(&case["color"])?.iter().map(|&v| v as u8).collect())
                    };
                    im.pad(whole, method, colour.as_deref(), centering)
                }
                _ => im.fit(
                    whole,
                    method,
                    case["bleed"].as_f64().unwrap_or(0.0),
                    centering,
                ),
            }
            .map_err(e)?;
            json!({"image": value_of(&out)})
        }
        "exif_transpose" => {
            let im = image_of(&case["image"])?;
            let orientation = case["orientation"].as_u64().unwrap_or(1) as u16;
            json!({"image": value_of(&im.exif_transposed(orientation))})
        }
        "getextrema" => {
            let extrema = image_of(&case["image"])?.getextrema();
            json!({"extrema": extrema.map(|v| v.iter().map(|&(a, b)| vec![a, b]).collect::<Vec<_>>())})
        }
        "linear_gradient" => json!({"image": value_of(&Image::linear_gradient())}),
        "radial_gradient" => json!({"image": value_of(&Image::radial_gradient())}),
        "open" => {
            // a file's bytes as the app opens them: exif_transpose(Image.open(f)).convert(mode)
            let data = STANDARD
                .decode(case["data"].as_str().unwrap_or(""))
                .map_err(|err| err.to_string())?;
            let mode = case["mode"]
                .as_str()
                .and_then(Mode::from_name)
                .ok_or("a mode")?;
            let fail = |err: lp_decode::Error| err.to_string();
            // ("draft": {"mode", "size"}, im.draft(mode, size) before; "exif": false, no turn)
            let draft = match &case["draft"] {
                Value::Null => None,
                d => {
                    let kind = match d["mode"].as_str() {
                        Some("L") => Some(lp_decode::Kind::L),
                        Some("RGB") => Some(lp_decode::Kind::RGB),
                        _ => None,
                    };
                    let size = ints(&d["size"])?;
                    Some((kind, (size[0] as usize, size[1] as usize)))
                }
            };
            // ("thumbnail": {"size", "resample", "reducing_gap"}, im.thumbnail(...) first, as
            // media.py makes a cover small)
            let picture = match &case["thumbnail"] {
                Value::Null => lp_decode::decode_draft(&data, draft).map_err(fail)?,
                t => {
                    let size = t["size"].as_array().ok_or("a thumbnail size")?;
                    let side = |i: usize| size.get(i).and_then(Value::as_f64).ok_or("a size");
                    let filter =
                        lp_pixels::Filter::from_number(t["resample"].as_i64().unwrap_or(3))
                            .ok_or("a resampling filter")?;
                    let gap = match &t["reducing_gap"] {
                        Value::Null if t.get("reducing_gap").is_some() => None,
                        Value::Null => Some(2.0),
                        g => Some(g.as_f64().ok_or("a reducing_gap")?),
                    };
                    lp_decode::thumbnail(&data, (side(0)?, side(1)?), filter, gap).map_err(fail)?
                }
            };
            let out = if case["exif"].as_bool().unwrap_or(true) {
                lp_decode::opened(&picture, mode).map_err(fail)?
            } else {
                picture.convert(mode).map_err(fail)?
            };
            // ("digest": the picture's size and SHA-256 alone, for pictures too big to send)
            if case["digest"].as_bool().unwrap_or(false) {
                let sum = Sha256::digest(out.as_bytes());
                let hex: String = sum.iter().map(|b| format!("{b:02x}")).collect();
                json!({"size": [out.width(), out.height()], "sha256": hex})
            } else {
                json!({"image": value_of(&out)})
            }
        }
        "frames" => {
            // an animation's frames as the app reads them: each one's duration and RGBA picture
            let data = STANDARD
                .decode(case["data"].as_str().unwrap_or(""))
                .map_err(|err| err.to_string())?;
            let frames = lp_decode::frames(&data).map_err(|err| err.to_string())?;
            // ("digest": each picture's SHA-256 alone, for animations too big to send whole)
            let digest = case["digest"].as_bool().unwrap_or(false);
            let frames: Vec<Value> = frames
                .iter()
                .map(|f| {
                    let mut out = json!({"duration": f.duration});
                    if digest {
                        let sum = Sha256::digest(f.image.as_bytes());
                        let hex: String = sum.iter().map(|b| format!("{b:02x}")).collect();
                        out["size"] = json!([f.image.width(), f.image.height()]);
                        out["sha256"] = json!(hex);
                    } else {
                        out["image"] = value_of(&f.image);
                    }
                    out
                })
                .collect();
            json!({ "frames": frames })
        }
        "draw" | "imagedraw" => {
            let own = op == "imagedraw";
            let commands = case["commands"].as_array().ok_or("commands")?;
            if case["image"]["mode"].as_str() == Some("F") {
                let mut im = float_of(&case["image"])?;
                let ink = |v: &Value| Ok(v.as_f64().map(|f| f as f32));
                for c in commands {
                    drawn(&mut Draw::new(&mut im), c, own, ink, |_, _| {
                        Err("no bitmap on F".into())
                    })?;
                }
                json!({"image": float_value(&im)})
            } else {
                let mut im = image_of(&case["image"])?;
                let mode = im.mode();
                let ink = |v: &Value| -> Result<Option<[u8; 4]>, String> {
                    if v.is_null() {
                        return Ok(None);
                    }
                    let colour: Vec<u8> = ints(v)?.iter().map(|&b| b as u8).collect();
                    mode.ink(&colour).map(Some).map_err(|err| err.to_string())
                };
                for c in commands {
                    drawn(&mut Draw::new(&mut im), c, own, ink, |d, c| {
                        let at = points(&c["xy"])?;
                        let bitmap = image_of(&c["bitmap"])?;
                        let colour = if own { "fill" } else { "ink" };
                        let fill = ink(&c[colour])?;
                        if own {
                            d.bitmap(at[0].0, at[0].1, &bitmap, fill)
                        } else {
                            d.draw_bitmap(at[0].0, at[0].1, &bitmap, fill.ok_or("an ink")?)
                        }
                        .map_err(|err| err.to_string())
                    })?;
                }
                json!({"image": value_of(&im)})
            }
        }
        "font" => font_case(case),
        "facet_glyph" => {
            // a finish's letter (render/facets.py): its geometry, its terms as an image's four
            // bands, and the letter coloured; null for a blank
            let font = lp_text::fallback::FallbackFont::open(
                case["path"].as_str().unwrap_or_default(),
                case["size"].as_f64().unwrap_or(24.0),
                0,
                None,
            )
            .map_err(|e| e.to_string())?;
            let ch = case["ch"]
                .as_str()
                .and_then(|s| s.chars().next())
                .ok_or("ch")?;
            let fill = ints(&case["fill"])?;
            let env = ints(&case["environment"])?;
            let sky = [env[0], env[1], env[2]].map(|v| v as u8);
            let ground = [env[3], env[4], env[5]].map(|v| v as u8);
            let metal = case["style"].as_str() == Some("metal");
            match lp_render::facets::check_glyph(
                &font,
                ch,
                case["quarter"].as_i64().unwrap_or(0),
                metal,
                [fill[0], fill[1], fill[2], fill[3]],
                (sky, ground),
            )
            .map_err(|e| e.0)?
            {
                None => json!({"glyph": null}),
                Some((left, top, cover, terms, patch)) => json!({"glyph": {
                    "left": left, "top": top, "cover": value_of(&cover),
                    "terms": value_of(&terms), "patch": value_of(&patch)}}),
            }
        }
        "facet_bar" => {
            // the facet finish's raised bar (render/facets.py's bar_layer); null where a bevel
            // does not read
            let rgba = |v: &Value| -> Result<Option<[i64; 4]>, String> {
                if v.is_null() {
                    return Ok(None);
                }
                let c = ints(v)?;
                Ok(Some([c[0], c[1], c[2], c[3]]))
            };
            let paint = (
                rgba(&case["paint"])?.ok_or("paint")?,
                rgba(&case["paint2"])?,
            );
            match lp_render::facets::check_bar(
                case["w"].as_i64().ok_or("w")?,
                case["h"].as_i64().ok_or("h")?,
                case["frac"].as_f64().ok_or("frac")?,
                case["direction"].as_str().ok_or("direction")?,
                paint,
                rgba(&case["track"])?,
                case["segments"].as_i64().unwrap_or(0),
                case["gap"].as_f64().unwrap_or(0.0),
            )
            .map_err(|e| e.0)?
            {
                None => json!({"layer": null}),
                Some(layer) => json!({"layer": value_of(&layer)}),
            }
        }
        "fnmatch" => {
            // fnmatch.fnmatchcase of each [name, pattern], and fnmatch.filter of the names by
            // each pattern: true, false, or the error re raises
            let matched: Vec<Value> = case["cases"]
                .as_array()
                .ok_or("cases")?
                .iter()
                .map(|pair| {
                    let (name, pattern) = (
                        pair[0].as_str().unwrap_or(""),
                        pair[1].as_str().unwrap_or(""),
                    );
                    match lp_py::fnmatch::fnmatchcase(name, pattern) {
                        Ok(found) => json!(found),
                        Err(e) => json!({"error": e.0}),
                    }
                })
                .collect();
            let names: Vec<&str> = case["names"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .collect();
            let filtered: Vec<Value> = case["patterns"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|p| {
                    match lp_py::fnmatch::filter(names.iter().copied(), p.as_str().unwrap_or("")) {
                        Ok(found) => json!(found),
                        Err(e) => json!({"error": e.0}),
                    }
                })
                .collect();
            json!({"matched": matched, "filtered": filtered})
        }
        "np_exp" => {
            // numpy's float32 exp of each float32 (their bits)
            let bits: Vec<u64> = case["bits"]
                .as_array()
                .ok_or("bits")?
                .iter()
                .map(|b| b.as_u64().unwrap_or(0))
                .collect();
            let out: Vec<u32> = bits
                .iter()
                .map(|&b| lp_render::np::expf(f32::from_bits(b as u32)).to_bits())
                .collect();
            json!({"bits": out})
        }
        "symbol" => {
            // a weather symbol standing (render/symbols.py's compose of layers_for, no time):
            // its kind, night or day, size, colours (cloud, dark, sun, rain, snow), look and
            // whether it is lit
            let colours = case["colours"].as_array().ok_or("colours")?;
            let mut five = [[0i64; 4]; 5];
            for (i, c) in colours.iter().take(5).enumerate() {
                let c = ints(c)?;
                five[i] = [c[0], c[1], c[2], c[3]];
            }
            let image = lp_render::symbols::check_symbol(
                case["kind"].as_str().ok_or("kind")?,
                case["night"].as_bool().unwrap_or(false),
                case["k"].as_u64().ok_or("k")? as usize,
                five,
                case["pack"].as_str().unwrap_or("vivid"),
                case["lit"].as_bool().unwrap_or(false),
            )?;
            json!({"image": value_of(&image)})
        }
        "coverage" => {
            let path = case["path"].as_str().unwrap_or_default();
            let index = case["index"].as_i64().unwrap_or(0);
            match lp_text::fallback::coverage(std::path::Path::new(path), index) {
                None => json!({"coverage": null}),
                Some(c) => {
                    let codes = ints(&case["codes"])?;
                    let has: Vec<bool> =
                        codes.iter().map(|&code| c.contains(code as u32)).collect();
                    json!({"coverage": {"outlines": c.outlines, "len": c.len(), "has": has}})
                }
            }
        }
        "getrgb" | "getcolor" => {
            let answers: Vec<Value> = case["colors"]
                .as_array()
                .ok_or("colors")?
                .iter()
                .map(|c| {
                    let color = c.as_str().unwrap_or_default();
                    let found = if op == "getrgb" {
                        lp_pixels::colour::getrgb(color)
                    } else {
                        lp_pixels::colour::getcolor(color, case["mode"].as_str().unwrap_or("RGB"))
                    };
                    match found {
                        Ok(v) => json!(v),
                        Err(e) => json!({"error": e.0}),
                    }
                })
                .collect();
            json!({"answers": answers})
        }
        "py_num" => {
            // Python's arithmetic: each case `[op, a, b]` as JSON text (Python's json, NaN and
            // ints of any size), its result as JSON text or its error
            use lp_py::num;
            let how = lp_py::json::Dumps::default();
            let answers: Vec<Value> = case["cases"]
                .as_array()
                .ok_or("cases")?
                .iter()
                .map(|c| {
                    let Ok(lp_py::Value::List(parts)) =
                        lp_py::json::loads(c.as_str().unwrap_or(""))
                    else {
                        return json!({"error": "case", "kind": "case"});
                    };
                    let op = parts[0].as_str().unwrap_or("");
                    let (a, b) = (&parts[1], &parts[2]);
                    let bool_of = |r: num::Result<bool>| r.map(lp_py::Value::Bool);
                    let result = match op {
                        "+" => num::add(a, b),
                        "-" => num::sub(a, b),
                        "*" => num::mul(a, b),
                        "/" => num::true_div(a, b),
                        "//" => num::floor_div(a, b),
                        "%" => num::rem(a, b),
                        "<" => bool_of(num::lt(a, b)),
                        ">" => bool_of(num::gt(a, b)),
                        "max" => num::max(a, b),
                        "min" => num::min(a, b),
                        "round" => num::round(a),
                        "log" => num::to_float(a).and_then(num::log).map(lp_py::Value::Float),
                        _ => return json!({"error": op, "kind": "op"}),
                    };
                    match result {
                        Ok(v) => json!({"value": lp_py::json::dumps(&v, &how)}),
                        Err(e) => json!({"error": e.msg, "kind": e.kind.name()}),
                    }
                })
                .collect();
            // re.IGNORECASE's lowercase and cased of every code point, as a digest
            let mut table = String::new();
            for cp in 0..0x110000u32 {
                table.push_str(&format!(
                    "{cp:x} {:x} {}\n",
                    lp_py::re::lower(cp),
                    u8::from(lp_py::re::cased(cp))
                ));
            }
            let digest = format!("{:x}", sha2::Sha256::digest(table.as_bytes()));
            // East_Asian_Width W and F over every code point, as a digest
            // (a surrogate is no char here: never wide, as in Python)
            let wide: String = (0..=0x10FFFFu32)
                .map(|cp| match char::from_u32(cp) {
                    Some(c) if lp_py::east_asian_wide(c) => '1',
                    _ => '0',
                })
                .collect();
            let wide_digest = format!("{:x}", sha2::Sha256::digest(wide.as_bytes()));
            // sum() of floats given by their bits
            let sums: Vec<String> = case["sums"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|list| {
                            let items = list.as_array().into_iter().flatten().map(|b| {
                                f64::from_bits(
                                    u64::from_str_radix(b.as_str().unwrap_or("0"), 16).unwrap_or(0),
                                )
                            });
                            format!("{:016x}", lp_py::sum_floats(items).to_bits())
                        })
                        .collect()
                })
                .unwrap_or_default();
            let lines: Vec<Vec<&str>> = case["splitlines"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|s| lp_py::splitlines(s.as_str().unwrap_or("")))
                        .collect()
                })
                .unwrap_or_default();
            let zones: Vec<bool> = case["zones"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|k| lp_py::zoneinfo::exists(k.as_str().unwrap_or("")))
                        .collect()
                })
                .unwrap_or_default();
            json!({"answers": answers, "re_digest": digest, "wide_digest": wide_digest,
                "sums": sums, "splitlines": lines, "zones": zones})
        }
        "py" => {
            let texts = case["texts"].as_array().ok_or("texts")?;
            let reprs: Vec<String> = texts
                .iter()
                .map(|t| lp_py::repr(t.as_str().unwrap_or_default()))
                .collect();
            // over every code point: the digits' values, what is printable, what is space
            let mut table = String::new();
            for ch in (0..=0x10FFFFu32).filter_map(char::from_u32) {
                let (d, p, w) = (
                    lp_py::decimal(ch),
                    lp_py::isprintable(ch),
                    lp_py::isspace(ch),
                );
                if d.is_some() || !p || w {
                    table += &format!(
                        "{:04X} {} {} {}\n",
                        u32::from(ch),
                        d.map_or(-1, i64::from),
                        u8::from(p),
                        u8::from(w)
                    );
                }
            }
            let sum = Sha256::digest(table.as_bytes());
            let hex: String = sum.iter().map(|b| format!("{b:02x}")).collect();
            // over every code point: lower(), upper() and how the final sigma sees it
            let code = |text: String| -> String {
                let codes: Vec<String> = text
                    .chars()
                    .map(|c| format!("{:X}", u32::from(c)))
                    .collect();
                codes.join(".")
            };
            let mut cases = String::new();
            for ch in (0..=0x10FFFFu32).filter_map(char::from_u32) {
                let (low, up) = (lp_py::lower(&ch.to_string()), lp_py::upper(&ch.to_string()));
                let class = match (
                    lp_py::lower(&format!("A\u{3a3}{ch}")).chars().nth(1),
                    lp_py::lower(&format!("A\u{3a3}{ch}B")).chars().nth(1),
                ) {
                    (Some('\u{3c2}'), Some('\u{3c3}')) => "I",
                    (Some('\u{3c3}'), Some('\u{3c3}')) => "C",
                    _ => "N",
                };
                if low != ch.to_string() || up != ch.to_string() || class != "N" {
                    cases += &format!("{:04X} {} {} {class}\n", u32::from(ch), code(low), code(up));
                }
            }
            let case_sum = Sha256::digest(cases.as_bytes());
            let case_hex: String = case_sum.iter().map(|b| format!("{b:02x}")).collect();
            // over every code point: casefold() where it changes the character
            let mut folds = String::new();
            for ch in (0..=0x10FFFFu32).filter_map(char::from_u32) {
                let folded = lp_py::casefold(&ch.to_string());
                if folded != ch.to_string() {
                    folds += &format!("{:04X} {}\n", u32::from(ch), code(folded));
                }
            }
            let fold_sum = Sha256::digest(folds.as_bytes());
            let fold_hex: String = fold_sum.iter().map(|b| format!("{b:02x}")).collect();
            let text = |t: &Value| t.as_str().unwrap_or_default().to_string();
            let lowers: Vec<String> = texts.iter().map(|t| lp_py::lower(&text(t))).collect();
            let uppers: Vec<String> = texts.iter().map(|t| lp_py::upper(&text(t))).collect();
            let casefolds: Vec<String> = texts.iter().map(|t| lp_py::casefold(&text(t))).collect();
            let quotients: Vec<f64> = case["divisions"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|pair| {
                            let int =
                                |v: &Value| v.as_str().and_then(|s| s.parse().ok()).unwrap_or(1);
                            lp_py::true_div(int(&pair[0]), int(&pair[1]) as i64)
                        })
                        .collect()
                })
                .unwrap_or_default();
            let mut answer = json!({"reprs": reprs, "sha256": hex, "case_sha256": case_hex,
                "fold_sha256": fold_hex, "lowers": lowers, "uppers": uppers,
                "casefolds": casefolds, "quotients": quotients});
            if case["case_table"].as_bool() == Some(true) {
                answer["case_table"] = json!(cases);
            }
            answer
        }
        "json" => {
            // each text read as json.loads reads it, and written back as json.dumps writes
            let ways = [
                lp_py::json::Dumps::default(),
                lp_py::json::Dumps::file(2),
                lp_py::json::Dumps {
                    sort_keys: true,
                    ensure_ascii: false,
                    ..Default::default()
                },
                lp_py::json::Dumps {
                    separators: Some((",".into(), ":".into())),
                    ..Default::default()
                },
            ];
            let results: Vec<Value> = case["texts"]
                .as_array()
                .ok_or("texts")?
                .iter()
                .map(|t| match lp_py::json::loads(t.as_str().unwrap_or_default()) {
                    Ok(v) => json!({
                        "repr": v.repr(),
                        "dumps": ways.iter().map(|w| lp_py::json::dumps(&v, w)).collect::<Vec<_>>(),
                    }),
                    Err(e) => json!({"error": e.to_string(), "kind": match e.kind {
                        lp_py::json::ErrorKind::Decode { .. } => "decode",
                        lp_py::json::ErrorKind::Value => "value",
                        lp_py::json::ErrorKind::Recursion => "recursion",
                    }}),
                })
                .collect();
            // repr() of floats given by their bits
            let floats: Vec<String> = case["floats"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|b| {
                            let bits =
                                u64::from_str_radix(b.as_str().unwrap_or("0"), 16).unwrap_or(0);
                            lp_py::float_repr(f64::from_bits(bits))
                        })
                        .collect()
                })
                .unwrap_or_default();
            // format(x, ".{p}g") of floats given by their bits, each with its precision
            let general: Vec<String> = case["general"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|pair| {
                            let bits = u64::from_str_radix(pair[0].as_str().unwrap_or("0"), 16)
                                .unwrap_or(0);
                            lp_py::format_g(
                                f64::from_bits(bits),
                                pair[1].as_u64().unwrap_or(6) as usize,
                            )
                        })
                        .collect()
                })
                .unwrap_or_default();
            json!({"results": results, "floats": floats, "general": general,
                "max_depth": lp_py::json::MAX_DEPTH})
        }
        "theme" | "parse_theme" | "widget" => {
            // a theme read from its folder, or from its data, or one widget, as theme/model.py
            // reads them: what comes of it as JSON text (json.dumps), its warnings; or the error
            let env = lp_theme::Env {
                fonts_dir: case["fonts_dir"].as_str().unwrap_or("").into(),
            };
            let root = case["root"].as_str().map(std::path::PathBuf::from);
            let data = |key: &str| lp_py::json::loads(case[key].as_str().unwrap_or("null"));
            let how = lp_py::json::Dumps::default();
            let answer = match op {
                "theme" => lp_theme::load_theme(
                    std::path::Path::new(case["folder"].as_str().unwrap_or("")),
                    &env,
                )
                .map(|t| (lp_py::json::dumps(&t.to_dict(), &how), t.warnings)),
                "parse_theme" => match data("data") {
                    Ok(d) => lp_theme::parse_theme(&d, root.as_deref(), &env)
                        .map(|t| (lp_py::json::dumps(&t.to_dict(), &how), t.warnings)),
                    Err(e) => Err(lp_theme::Error {
                        kind: lp_theme::Kind::Value,
                        msg: e.to_string(),
                    }),
                },
                _ => match data("data") {
                    Ok(d) => {
                        let palette = match data("palette") {
                            Ok(lp_py::Value::Dict(p)) => Some(p),
                            _ => None,
                        };
                        let index = case["index"].as_u64().unwrap_or(0) as usize;
                        lp_theme::normalize_widget(&d, index, palette.as_ref(), root.as_deref())
                            .map(|(w, warnings)| {
                                (lp_py::json::dumps(&lp_py::Value::Dict(w), &how), warnings)
                            })
                    }
                    Err(e) => Err(lp_theme::Error {
                        kind: lp_theme::Kind::Value,
                        msg: e.to_string(),
                    }),
                },
            };
            match answer {
                Ok((dict, warnings)) => json!({"dict": dict, "warnings": warnings}),
                Err(e) => json!({"error": e.msg, "kind": e.kind.name()}),
            }
        }
        "math" => {
            // math.hypot of pairs of floats given by their bits
            let bits = |v: &Value| {
                f64::from_bits(u64::from_str_radix(v.as_str().unwrap_or("0"), 16).unwrap_or(0))
            };
            let hypot: Vec<String> = case["hypot"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|p| {
                            format!(
                                "{:016x}",
                                lp_py::math::hypot(bits(&p[0]), bits(&p[1])).to_bits()
                            )
                        })
                        .collect()
                })
                .unwrap_or_default();
            json!({"hypot": hypot})
        }
        "light_colours" => {
            // light_colour of colours, each with its role (a palette name, "" for ink, null)
            let made: Vec<String> = case["colours"]
                .as_array()
                .ok_or("colours")?
                .iter()
                .map(|c| {
                    let colour = c[0].as_str().unwrap_or_default();
                    let role = match c[1].as_str() {
                        None => lp_theme::light::Role::None,
                        Some("") => lp_theme::light::Role::Ink,
                        Some(name) => lp_theme::light::Role::Named(name),
                    };
                    lp_theme::light::light_colour(colour, role)
                })
                .collect();
            json!({"made": made})
        }
        "theme_versions" => {
            // what the versions, the places and the variants of themes come to
            use lp_theme::versions as v;
            let places = v::Places {
                user: case["user"].as_str().unwrap_or("").into(),
                plugins: Vec::new(),
                builtin: case["builtin"].as_str().unwrap_or("").into(),
            };
            let env = lp_theme::Env {
                fonts_dir: case["fonts_dir"].as_str().unwrap_or("").into(),
            };
            let names: Vec<String> = case["names"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .map(|n| n.as_str().unwrap_or("").to_string())
                        .collect()
                })
                .unwrap_or_default();
            let sizes: Vec<Option<(i64, i64)>> = case["sizes"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .map(|s| {
                            s.as_array()
                                .map(|p| (p[0].as_i64().unwrap_or(0), p[1].as_i64().unwrap_or(0)))
                        })
                        .collect()
                })
                .unwrap_or_default();
            // each panel's default theme (and none's), and whether it is a bar
            let defaults: Vec<Value> = std::iter::once(None)
                .chain(lp_devices::models::MODELS.iter().map(Some))
                .map(|m| {
                    json!([
                        m.map_or("", |m| m.id),
                        lp_theme::defaults::is_bar(m),
                        lp_theme::defaults::default_theme(m, &places)
                    ])
                })
                .collect();
            let answers: Vec<Value> = names.iter().map(|name| {
                let mut fors = Vec::new();
                for orientation in ["landscape", "portrait", ""] {
                    for size in &sizes {
                        fors.push(v::theme_for(name, orientation, *size, &places));
                    }
                }
                json!({
                    "stem": v::version_stem(name), "base": v::version_base(name), "root": v::root(name),
                    "companion": v::is_companion(name), "agents": v::is_agents_screen(name),
                    "pair": v::pair_version(name, &places), "unpaired": v::unpaired_version(name, &places),
                    "companion_for": v::companion_for(name, &places),
                    "find": match v::find_theme(name, &places) {
                        Ok(p) => json!({"path": p.to_string_lossy()}),
                        Err(e) => json!({"error": e.msg}),
                    },
                    "theme_for": fors,
                })
            }).collect();
            let listed: Vec<String> = v::list_themes(&places)
                .iter()
                .map(|d| {
                    lp_py::json::dumps(
                        &lp_py::Value::Dict(d.clone()),
                        &lp_py::json::Dumps::default(),
                    )
                })
                .collect();
            // the variants of each theme asked for: accent, card opacity, light colours, look
            let variants: Vec<Value> = case["variants"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|w| {
                            let load = |key: &str| {
                                lp_theme::load_theme(
                                    std::path::Path::new(w[key].as_str().unwrap_or("")),
                                    &env,
                                )
                            };
                            let made = (|| -> Result<String, lp_theme::Error> {
                                let mut theme = load("folder")?;
                                if let Some(colour) = w["accent"].as_str() {
                                    theme = v::with_accent(&theme, Some(colour));
                                }
                                if let Some(percent) = w["card_opacity"].as_i64() {
                                    theme = v::with_card_opacity(&theme, percent);
                                }
                                if w["look"].is_string() {
                                    let source = load("look")?;
                                    theme = v::with_look(&theme, Some(&source));
                                }
                                theme = v::with_light_colours(
                                    &theme,
                                    w["light"].as_bool().unwrap_or(false),
                                )?;
                                Ok(lp_py::json::dumps(
                                    &theme.to_dict(),
                                    &lp_py::json::Dumps::default(),
                                ))
                            })();
                            match made {
                                Ok(text) => json!({"dict": text}),
                                Err(e) => json!({"error": e.msg, "kind": e.kind.name()}),
                            }
                        })
                        .collect()
                })
                .unwrap_or_default();
            // themes saved into the user's folder, one after another
            let saved: Vec<Value> = case["saves"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|s| {
                            let data = lp_py::json::loads(s["data"].as_str().unwrap_or("null"));
                            let result = match data {
                                Ok(d) => v::save_theme(
                                    s["name"].as_str().unwrap_or(""),
                                    &d,
                                    s["source"].as_str(),
                                    &places,
                                    &env,
                                )
                                .map(|p| p.to_string_lossy().into_owned()),
                                Err(e) => Err(lp_theme::Error {
                                    kind: lp_theme::Kind::Value,
                                    msg: e.to_string(),
                                }),
                            };
                            match result {
                                Ok(path) => json!({"path": path}),
                                Err(e) => json!({"error": e.msg, "kind": e.kind.name()}),
                            }
                        })
                        .collect()
                })
                .unwrap_or_default();
            json!({"names": answers, "listed": listed, "variants": variants, "saved": saved,
                "defaults": defaults})
        }
        "theme_adapt" => {
            // grids, adapted themes and themes laid out, as theme/adapt.py makes them; set order
            // and int() of str
            let env = lp_theme::Env {
                fonts_dir: case["fonts_dir"].as_str().unwrap_or("").into(),
            };
            let builtin = std::path::PathBuf::from(case["builtin"].as_str().unwrap_or(""));
            let answer = |r: Result<String, lp_theme::Error>| match r {
                Ok(text) => json!({"dict": text}),
                Err(e) => json!({"error": e.msg, "kind": e.kind.name()}),
            };
            let how = lp_py::json::Dumps::default();
            // an int given as a number or, of any size, as its digits
            let big = |v: &Value| -> num_bigint::BigInt {
                match v {
                    Value::String(s) => s.parse().unwrap_or_default(),
                    other => other.as_i64().unwrap_or(0).into(),
                }
            };
            let fail = |e: lp_theme::Error| json!({"error": e.msg, "kind": e.kind.name()});
            // grids of what files hold as it is: each of its numbers as JSON text
            let raw_grids: Vec<Value> = case["raw_grids"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|g| {
                            let given = g["grid"]
                                .as_str()
                                .map(|t| lp_py::json::loads(t).expect("grid"));
                            match lp_theme::grid::make_cells(
                                &big(&g["width"]),
                                &big(&g["height"]),
                                given.as_ref(),
                                g["model"].as_str().unwrap_or("custom"),
                            ) {
                                Ok(c) => {
                                    json!([c.x, c.y, c.cell_w, c.cell_h, c.gap, c.columns, c.rows]
                                        .iter()
                                        .map(|v| lp_py::json::dumps(v, &how))
                                        .collect::<Vec<_>>())
                                }
                                Err(e) => fail(e.into()),
                            }
                        })
                        .collect()
                })
                .unwrap_or_default();
            // the versions beside a folder, in the folder's order, and the one nearest a size
            let versions: Vec<Value> = case["versions"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|v| {
                            let folder = std::path::Path::new(v["folder"].as_str().unwrap_or(""));
                            let size = (
                                v["size"][0].as_i64().unwrap_or(0),
                                v["size"][1].as_i64().unwrap_or(0),
                            );
                            let listed = match lp_theme::adapt::versions_of(folder) {
                                Ok(found) => json!(found
                                    .iter()
                                    .map(|(n, (w, h))| json!([n, w.to_string(), h.to_string()]))
                                    .collect::<Vec<_>>()),
                                Err(e) => fail(e),
                            };
                            let nearest = match lp_theme::adapt::nearest_version(folder, size) {
                                Ok(n) => json!({"name": n}),
                                Err(e) => fail(e),
                            };
                            json!({"listed": listed, "nearest": nearest})
                        })
                        .collect()
                })
                .unwrap_or_default();
            // how many characters of a name a stem takes up under re.IGNORECASE
            let ignorecase: Vec<Value> = case["ignorecase"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|p| {
                            json!(lp_py::re::prefix_ignoring_case(
                                p[0].as_str().unwrap_or(""),
                                p[1].as_str().unwrap_or("")
                            ))
                        })
                        .collect()
                })
                .unwrap_or_default();
            let grids: Vec<Value> = case["grids"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|g| {
                            let n = |k: &str| g[k].as_i64().unwrap_or(0);
                            let given = lp_theme::grid::Settings {
                                columns: n("columns"),
                                rows: n("rows"),
                                gap: n("gap"),
                                margin: n("margin"),
                            };
                            let grid = lp_theme::grid::make_grid(
                                n("width"),
                                n("height"),
                                given,
                                g["model"].as_str().unwrap_or("custom"),
                            );
                            json!([
                                grid.x,
                                grid.y,
                                format!("{:016x}", grid.cell_w.to_bits()),
                                format!("{:016x}", grid.cell_h.to_bits()),
                                grid.gap,
                                grid.columns,
                                grid.rows
                            ])
                        })
                        .collect()
                })
                .unwrap_or_default();
            let adapted: Vec<Value> = case["adapt"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|a| {
                            let data = lp_py::json::loads(a["data"].as_str().unwrap_or("null"));
                            answer(match data {
                                Ok(d) => lp_theme::adapt::adapt_theme(
                                    &d,
                                    a["model"].as_str().unwrap_or(""),
                                    a["orientation"].as_str().unwrap_or(""),
                                    a["mode"].as_str().unwrap_or("scale"),
                                    &big(&a["width"]),
                                    &big(&a["height"]),
                                    &env,
                                )
                                .map(|v| lp_py::json::dumps(&v, &how)),
                                Err(e) => Err(lp_theme::Error {
                                    kind: lp_theme::Kind::Value,
                                    msg: e.to_string(),
                                }),
                            })
                        })
                        .collect()
                })
                .unwrap_or_default();
            let laid: Vec<Value> = case["laid_out"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|l| {
                            let folder = std::path::Path::new(l["folder"].as_str().unwrap_or(""));
                            let size = l["size"]
                                .as_array()
                                .map(|p| (p[0].as_i64().unwrap_or(0), p[1].as_i64().unwrap_or(0)));
                            answer(
                                lp_theme::load_theme(folder, &env)
                                    .and_then(|t| {
                                        lp_theme::adapt::laid_out(
                                            &t,
                                            size,
                                            l["model"].as_str().unwrap_or("custom"),
                                            &builtin,
                                            &env,
                                        )
                                    })
                                    .map(|t| lp_py::json::dumps(&t.to_dict(), &how)),
                            )
                        })
                        .collect()
                })
                .unwrap_or_default();
            let orders: Vec<Value> = case["sets"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|set| {
                            let pairs: Vec<(u64, u64)> = set
                                .as_array()
                                .map(|ps| {
                                    ps.iter()
                                        .map(|p| {
                                            (p[0].as_u64().unwrap_or(0), p[1].as_u64().unwrap_or(0))
                                        })
                                        .collect()
                                })
                                .unwrap_or_default();
                            json!(lp_py::set_order(&pairs))
                        })
                        .collect()
                })
                .unwrap_or_default();
            let ints: Vec<Value> = case["ints"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|t| {
                            lp_py::int_of_str(t.as_str().unwrap_or(""))
                                .map_or(Value::Null, |n| json!(n.to_string()))
                        })
                        .collect()
                })
                .unwrap_or_default();
            json!({"grids": grids, "adapt": adapted, "laid_out": laid, "sets": orders, "ints": ints,
                "raw_grids": raw_grids, "versions": versions, "ignorecase": ignorecase})
        }
        "i18n" => {
            // each language's texts as t() finds them, its fields' labels, and the system's
            // language from an environment's variables (and Windows' own language)
            let locale = std::path::PathBuf::from(case["locale"].as_str().unwrap_or(""));
            let texts: Vec<&str> = case["texts"]
                .as_array()
                .map(|a| a.iter().filter_map(Value::as_str).collect())
                .unwrap_or_default();
            let mut languages = Vec::new();
            for code in case["languages"].as_array().into_iter().flatten() {
                let code = code.as_str().unwrap_or("");
                let language = lp_i18n::Language::load(&locale, code)?;
                languages.push(json!({
                    "t": texts.iter().map(|x| language.t(x)).collect::<Vec<_>>(),
                    "fields": texts.iter().map(|x| language.field_label(x)).collect::<Vec<_>>(),
                }));
            }
            let systems: Vec<String> = case["envs"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|e| {
                            let env = |k: &str| e[k].as_str().unwrap_or("").to_string();
                            let windows = e["windows"]
                                .as_u64()
                                .map(|id| lp_i18n::windows_name(id as u32));
                            lp_i18n::system_language(&env, windows)
                        })
                        .collect()
                })
                .unwrap_or_default();
            json!({"languages": languages, "systems": systems})
        }
        "theme_expand" => {
            // themes with their modules laid out (theme/modules.py's expand): each theme's data
            // as JSON text, read, in light colours where asked, expanded in its language; its
            // widgets as JSON text, the modules' boxes and the warnings, or the error
            let env = lp_theme::Env {
                fonts_dir: case["fonts_dir"].as_str().unwrap_or("").into(),
            };
            let locale = std::path::PathBuf::from(case["locale"].as_str().unwrap_or(""));
            let how = lp_py::json::Dumps::default();
            let answers: Vec<Value> = case["themes"]
                .as_array()
                .ok_or("themes")?
                .iter()
                .map(|c| {
                    let code = c["language"].as_str().unwrap_or("en");
                    match lp_i18n::Language::load(&locale, code) {
                        Ok(language) => lp_i18n::set_language(language),
                        Err(e) => return json!({"error": e, "kind": "language"}),
                    }
                    let made = lp_py::json::loads(c["data"].as_str().unwrap_or("null"))
                        .map_err(|e| lp_theme::Error { kind: lp_theme::Kind::Value, msg: e.to_string() })
                        .and_then(|data| lp_theme::parse_theme(&data, None, &env))
                        .and_then(|theme| {
                            lp_theme::versions::with_light_colours(&theme, c["light"].as_bool().unwrap_or(false))
                        })
                        .and_then(|theme| lp_theme::modules::expand(&theme, &env).map(|e| (theme, e)));
                    match made {
                        Ok((theme, e)) => {
                            let widgets = lp_py::Value::List(e.widgets.into_iter().map(lp_py::Value::Dict).collect());
                            let mut warnings = theme.warnings.clone();
                            warnings.extend(e.warnings);
                            json!({
                                "widgets": lp_py::json::dumps(&widgets, &how),
                                "boxes": e.boxes.iter().map(|(k, b)| json!([k, b])).collect::<Vec<_>>(),
                                "warnings": warnings,
                            })
                        }
                        Err(e) => json!({"error": e.msg, "kind": e.kind.name()}),
                    }
                })
                .collect();
            json!({"answers": answers})
        }
        "py_float" => {
            // float(text) of each text, as its bits (16 hex digits), or null for a ValueError
            let floats: Vec<Value> = case["texts"]
                .as_array()
                .map(|all| {
                    all.iter()
                        .map(|t| match lp_py::float_of_str(t.as_str().unwrap_or("")) {
                            Some(x) => json!(format!("{:016x}", x.to_bits())),
                            None => Value::Null,
                        })
                        .collect()
                })
                .unwrap_or_default();
            json!({"floats": floats})
        }
        "module_app" => {
            // what the editor asks of the modules: module_info and the templates of grids in
            // each language (JSON text); card lines, slots' cards, turns and their texts; and
            // per theme a module detached, follows_cards and with_cards
            use lp_theme::modules::{info, slots};
            let env = lp_theme::Env {
                fonts_dir: case["fonts_dir"].as_str().unwrap_or("").into(),
            };
            let locale = std::path::PathBuf::from(case["locale"].as_str().unwrap_or(""));
            let how = lp_py::json::Dumps::default();
            let text = |v: &lp_py::Value| lp_py::json::dumps(v, &how);
            let bits = |x: Option<f64>| match x {
                Some(x) => json!(format!("{:016x}", x.to_bits())),
                None => Value::Null,
            };
            let card = |c: slots::SlotCard| json!([c.name, c.rule, bits(c.seconds)]);
            let strings = |k: &str| -> Vec<String> {
                case[k]
                    .as_array()
                    .map(|a| {
                        a.iter()
                            .filter_map(|v| v.as_str().map(String::from))
                            .collect()
                    })
                    .unwrap_or_default()
            };
            let grids: Vec<(i64, i64)> = case["grids"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .map(|g| (g[0].as_i64().unwrap_or(1), g[1].as_i64().unwrap_or(1)))
                        .collect()
                })
                .unwrap_or_default();
            let mut languages = Vec::new();
            for code in strings("languages") {
                lp_i18n::set_language(lp_i18n::Language::load(&locale, &code)?);
                let templates: Vec<String> = grids
                    .iter()
                    .map(|&(c, r)| text(&info::templates(c, r)))
                    .collect();
                languages.push(json!({
                    "info": text(&lp_py::Value::Dict(info::module_info())),
                    "templates": templates,
                }));
            }
            lp_i18n::set_language(lp_i18n::Language::english());
            let lines: Vec<Value> = strings("lines")
                .iter()
                .map(|l| slots::card_line(l).map_or(Value::Null, card))
                .collect();
            let slot_cards: Vec<Value> = strings("modules")
                .iter()
                .map(|m| match lp_py::json::loads(m) {
                    Ok(lp_py::Value::Dict(d)) => {
                        Value::Array(slots::slot_cards(&d).into_iter().map(card).collect())
                    }
                    _ => Value::Null,
                })
                .collect();
            let times: Vec<Value> = strings("times")
                .iter()
                .map(|t| bits(slots::card_time(t)))
                .collect();
            let time_texts: Vec<String> = strings("seconds")
                .iter()
                .map(|h| slots::time_text(f64::from_bits(u64::from_str_radix(h, 16).unwrap_or(0))))
                .collect();
            let themes: Vec<Value> = case["themes"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|c| {
                    let code = c["language"].as_str().unwrap_or("en");
                    match lp_i18n::Language::load(&locale, code) {
                        Ok(language) => lp_i18n::set_language(language),
                        Err(e) => return json!({"error": e, "kind": "language"}),
                    }
                    let made = lp_py::json::loads(c["data"].as_str().unwrap_or("null"))
                        .map_err(|e| lp_theme::Error {
                            kind: lp_theme::Kind::Value,
                            msg: e.to_string(),
                        })
                        .and_then(|data| lp_theme::parse_theme(&data, None, &env))
                        .and_then(|theme| {
                            lp_theme::versions::with_light_colours(
                                &theme,
                                c["light"].as_bool().unwrap_or(false),
                            )
                        });
                    let theme = match made {
                        Ok(theme) => theme,
                        Err(e) => return json!({"error": e.msg, "kind": e.kind.name()}),
                    };
                    let cards: Vec<String> = c["cards"]
                        .as_array()
                        .map(|a| {
                            a.iter()
                                .filter_map(|v| v.as_str().map(String::from))
                                .collect()
                        })
                        .unwrap_or_default();
                    let order = c["order"].as_str().unwrap_or("first");
                    let transition = c["transition"].as_str().unwrap_or("light");
                    let with = slots::with_cards(&theme, &cards, order, transition);
                    let widgets = |t: &lp_theme::Theme| {
                        text(&lp_py::Value::List(
                            t.widgets.iter().cloned().map(lp_py::Value::Dict).collect(),
                        ))
                    };
                    let detached = match info::detach(&theme, &env, c["id"].as_str().unwrap_or(""))
                    {
                        Ok(parts) => json!(text(&lp_py::Value::List(
                            parts.into_iter().map(lp_py::Value::Dict).collect()
                        ))),
                        Err(e) => json!({"error": e.msg, "kind": e.kind.name()}),
                    };
                    json!({
                        "detach": detached,
                        "follows": slots::follows_cards(&theme),
                        "changed": with.is_some(),
                        "with_cards": widgets(with.as_ref().unwrap_or(&theme)),
                    })
                })
                .collect();
            lp_i18n::set_language(lp_i18n::Language::english());
            json!({
                "languages": languages,
                "lines": lines,
                "slot_cards": slot_cards,
                "times": times,
                "time_texts": time_texts,
                "themes": themes,
            })
        }
        "render_frame" => {
            // render/renderer.py's Renderer(theme, preview=True, dither=...).render(snapshot) of
            // each theme's folder: its frame's SHA-256 (and bytes where asked) and its boxes;
            // or what the Rust renderer does not draw yet, or why the theme was not read; in the
            // language asked (English by default)
            let env = lp_theme::Env {
                fonts_dir: case["fonts_dir"].as_str().unwrap_or("").into(),
            };
            let locale = std::path::PathBuf::from(case["locale"].as_str().unwrap_or(""));
            let code = case["language"].as_str().unwrap_or("en");
            lp_i18n::set_language(lp_i18n::Language::load(&locale, code)?);
            let frames: Vec<Value> = case["frames"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|c| {
                    let folder = std::path::PathBuf::from(c["folder"].as_str().unwrap_or(""));
                    let theme = match lp_theme::load_theme(&folder, &env) {
                        Ok(theme) => theme,
                        Err(e) => return json!({"error": e.msg}),
                    };
                    let options = lp_render::Options {
                        preview: true,
                        animate: false,
                        dither: c["dither"].as_bool().unwrap_or(false),
                    };
                    let mut renderer = match lp_render::Renderer::new(theme, &env, options) {
                        Ok(r) => r,
                        Err(e) => return json!({"error": e.0}),
                    };
                    match renderer.render(snapshot_of(&c["snapshot"])) {
                        Ok((frame, boxes)) => {
                            let sum = Sha256::digest(frame.as_bytes());
                            let hex: String = sum.iter().map(|b| format!("{b:02x}")).collect();
                            let boxes: serde_json::Map<String, Value> =
                                boxes.into_iter().map(|(k, b)| (k, json!(b))).collect();
                            let mut out = json!({"sha256": hex, "size": [frame.width(),
                                frame.height()], "boxes": boxes, "warnings": renderer.warnings()});
                            if c["bytes"].as_bool() == Some(true) {
                                out["frame"] = value_of(&frame);
                            }
                            out
                        }
                        Err(lp_render::Unported(what)) => json!({"unported": what}),
                    }
                })
                .collect();
            lp_i18n::set_language(lp_i18n::Language::english());
            json!({"frames": frames})
        }
        "render_ramps" => {
            // the float32s of gradients: the background's line (_gradient_ground) and a card's
            // ramp (_ramp_floats), each value's bits in hex
            use lp_render::shapes;
            let four = |v: &Value| -> [i64; 4] {
                std::array::from_fn(|i| v.get(i).and_then(Value::as_i64).unwrap_or(0))
            };
            let bits = |floats: Vec<f32>| -> Vec<String> {
                floats
                    .iter()
                    .map(|f| format!("{:08x}", f.to_bits()))
                    .collect()
            };
            let grounds: Vec<Vec<String>> = case["grounds"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|g| {
                    let (a, b) = (four(&g[0]).map(|v| v as f32), four(&g[1]).map(|v| v as f32));
                    bits(shapes::ground_line(
                        a,
                        b,
                        g[2].as_u64().unwrap_or(0) as usize,
                    ))
                })
                .collect();
            let ramps: Vec<Vec<String>> = case["ramps"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|r| {
                    let (steps, sub) = (r[2].as_u64().unwrap_or(0), r[3].as_u64().unwrap_or(1));
                    bits(shapes::ramp_floats(
                        four(&r[0]),
                        four(&r[1]),
                        steps as usize,
                        sub as usize,
                    ))
                })
                .collect();
            json!({"grounds": grounds, "ramps": ramps})
        }
        "render_needs" => {
            // need_holds of each condition over each snapshot (its readings typed, the keys of
            // its pictures, its time)
            use lp_render::snapshot as sn;
            let snapshots: Vec<sn::Snapshot> = case["snapshots"]
                .as_array()
                .into_iter()
                .flatten()
                .map(snapshot_of)
                .collect();
            let needs: Vec<&str> = case["needs"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|n| n.as_str().unwrap_or(""))
                .collect();
            let holds: Vec<Vec<bool>> = snapshots
                .iter()
                .map(|snap| needs.iter().map(|n| sn::need_holds(n, snap)).collect())
                .collect();
            json!({"holds": holds})
        }
        "py_vformat" => {
            // string.Formatter().format of each text, with a get_field that gives each field's
            // name as its value: the text, or the kind of Python's error
            let texts: Vec<Value> = case["texts"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|t| {
                    let get = |name: &str| Ok(lp_py::Value::Str(name.into()));
                    let field = |v: &lp_py::Value, spec: &str| lp_py::format::format(v, spec);
                    match lp_py::format::vformat(t.as_str().unwrap_or(""), &get, &field) {
                        Ok(text) => json!({"text": text}),
                        Err(e) => json!({"error": format!("{:?}", e.kind)}),
                    }
                })
                .collect();
            json!({"texts": texts})
        }
        "render_format" => {
            // render/formatting.py: safe_format of each case, auto_format, and the parts
            // (bytes, duration, clock, rate) of values given with their types
            use lp_render::formatting as f;
            fn kind(k: lp_py::format::Kind) -> &'static str {
                match k {
                    lp_py::format::Kind::Value => "ValueError",
                    lp_py::format::Kind::Type => "TypeError",
                    lp_py::format::Kind::Overflow => "OverflowError",
                }
            }
            let outcome = |r: Result<String, lp_py::format::FmtError>| match r {
                Ok(text) => json!({"text": text}),
                Err(e) => json!({"error": kind(e.kind)}),
            };
            let s = |v: &Value| v.as_str().unwrap_or("").to_string();
            let cases: Vec<Value> = case["cases"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|c| {
                    match f::safe_format(
                        &s(&c["fmt"]),
                        &typed(&c["value"]),
                        &s(&c["unit"]),
                        &s(&c["label"]),
                    ) {
                        Ok(text) => json!({"text": text}),
                        Err(f::FormatError::Format(_)) => json!({"error": "FormatError"}),
                        Err(f::FormatError::Uncaught(k, _)) => json!({"error": kind(k)}),
                    }
                })
                .collect();
            let auto: Vec<Value> = case["auto"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|c| outcome(f::auto_format(&typed(&c["value"]), &s(&c["unit"]))))
                .collect();
            let parts: Vec<Value> = case["parts"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|v| {
                    let v = typed(v);
                    json!({
                        "bytes": outcome(f::human_bytes(&v)),
                        "duration": outcome(f::human_duration(&v)),
                        "clock": outcome(f::clock_duration(&v)),
                        "rate": match f::rate_parts(&v) {
                            Ok((n, u)) => json!({"text": [n, u]}),
                            Err(e) => json!({"error": kind(e.kind)}),
                        },
                    })
                })
                .collect();
            json!({"cases": cases, "auto": auto, "parts": parts})
        }
        "theme_tables" => {
            // the tables, as JSON text, for the comparison with Python's
            use lp_py::{Dict, Value};
            use lp_theme::tables as t;
            let fields = |fs: &[lp_theme::specs::Field]| {
                Value::Dict(
                    fs.iter()
                        .map(|f| {
                            (
                                f.name.to_string(),
                                Value::List(vec![f.kind.into(), f.default.value()]),
                            )
                        })
                        .collect(),
                )
            };
            let strs = |xs: &[&str]| Value::List(xs.iter().map(|&x| x.into()).collect());
            let by_type = |table: &[(&str, &[lp_theme::specs::Field])]| {
                Value::Dict(
                    table
                        .iter()
                        .map(|(k, fs)| (k.to_string(), fields(fs)))
                        .collect(),
                )
            };
            let mut d = Dict::new();
            d.insert("COMMON".into(), fields(t::COMMON));
            d.insert("WIDGET_SPECS".into(), by_type(t::WIDGETS));
            d.insert("LOOK_SPECS".into(), by_type(t::LOOK_SPECS));
            d.insert("TOAST_STYLES".into(), by_type(t::TOAST_STYLES));
            d.insert(
                "PRIMARY_FIELDS".into(),
                Value::Dict(
                    t::PRIMARY_FIELDS
                        .iter()
                        .map(|(k, v)| (k.to_string(), strs(v)))
                        .collect(),
                ),
            );
            d.insert(
                "FINISH_TYPES".into(),
                Value::Dict(
                    t::FINISH_TYPES
                        .iter()
                        .map(|(k, v)| (k.to_string(), strs(v)))
                        .collect(),
                ),
            );
            d.insert(
                "LOOK_RANGES".into(),
                Value::List(
                    t::LOOK_RANGES
                        .iter()
                        .map(|(a, b, lo, hi)| {
                            Value::List(vec![(*a).into(), (*b).into(), (*lo).into(), (*hi).into()])
                        })
                        .collect(),
                ),
            );
            d.insert(
                "STYLE_DEFAULTS".into(),
                Value::Dict(lp_theme::model::style_defaults()),
            );
            d.insert(
                "TOAST_DEFAULTS".into(),
                Value::Dict(lp_theme::model::toast_defaults()),
            );
            d.insert(
                "GRID_DEFAULTS".into(),
                Value::Dict(
                    t::GRID_DEFAULTS
                        .iter()
                        .map(|(k, v)| (k.to_string(), (*v).into()))
                        .collect(),
                ),
            );
            for (name, xs) in [
                ("FINISHES", t::FINISHES),
                ("EFFECT_FIELDS", t::EFFECT_FIELDS),
                ("TOAST_ANCHORS", t::TOAST_ANCHORS),
                ("GONE_LOOK", t::GONE_LOOK),
                ("CARDS", t::CARDS),
                ("BACKDROPS", t::BACKDROPS),
                ("MODULE_KINDS", t::MODULE_KINDS),
                ("MODULE_SOURCES", t::MODULE_SOURCES),
                ("MODULE_FALLBACKS", t::MODULE_FALLBACKS),
                ("SYMBOL_PACKS", t::SYMBOL_PACKS),
                ("FAN_LOOKS", t::FAN_LOOKS),
                ("FAN_PAINTS", t::FAN_PAINTS),
                ("ICON_NAMES", t::ICON_NAMES),
                ("WEATHER_ICONS", t::WEATHER_ICONS),
                ("PART_TYPES", t::PART_TYPES),
            ] {
                d.insert(name.into(), strs(xs));
            }
            d.insert(
                "GONE_FIELDS".into(),
                Value::Dict(
                    t::GONE_FIELDS
                        .iter()
                        .map(|(k, v)| (k.to_string(), strs(v)))
                        .collect(),
                ),
            );
            let scalars = [
                ("THEME_FORMAT", Value::from(t::THEME_FORMAT)),
                ("THEME_FILENAME", t::THEME_FILENAME.into()),
                ("BLUR_MAX", t::BLUR_MAX.into()),
                ("MAX_SWITCHES", (t::MAX_SWITCHES as i64).into()),
                ("MAX_DETACHED", (t::MAX_DETACHED as i64).into()),
                ("CARD_OPACITY_MIN", t::CARD_OPACITY_MIN.into()),
                ("COVER_COLOR", t::COVER_COLOR.into()),
                ("AUTO", t::AUTO.into()),
                ("DEFAULT_THEME", t::DEFAULT_THEME.into()),
                ("BUILTIN_PREFIX", t::BUILTIN_PREFIX.into()),
                ("DEFAULT_FONT", t::DEFAULT_FONT.into()),
                ("GRID_CELLS", t::GRID_CELLS.into()),
            ];
            for (name, v) in scalars {
                d.insert(name.into(), v);
            }
            let models: Vec<Value> = lp_devices::models::MODELS
                .iter()
                .map(|m| m.to_dict())
                .collect();
            d.insert("MODELS".into(), Value::List(models));
            json!({"tables": lp_py::json::dumps(&Value::Dict(d), &lp_py::json::Dumps::default())})
        }
        "unicode" => {
            let sum = Sha256::digest(lp_text::fallback::unicode_table().as_bytes());
            let hex: String = sum.iter().map(|b| format!("{b:02x}")).collect();
            json!({"sha256": hex})
        }
        "discover" => {
            let language = case["language"].as_str().unwrap_or_default().to_string();
            let found = lp_text::fallback::discover(
                case["windows"].as_bool().unwrap_or(false),
                case["windir"].as_str(),
                Box::new(move || language.clone()),
            );
            let mut scripts: Vec<(&String, &Vec<lp_text::fallback::Entry>)> =
                found.scripts.iter().collect();
            scripts.sort();
            json!({"fonts": found.fonts, "scripts": scripts, "variant": found.variant()})
        }
        "fontconfig" => {
            let language = case["language"].as_str().unwrap_or_default();
            let answers: Vec<Value> = case["text"]
                .as_str()
                .unwrap_or_default()
                .chars()
                .map(|ch| {
                    match lp_text::fallback::fontconfig(
                        ch,
                        case["bold"].as_bool().unwrap_or(false),
                        language,
                    ) {
                        Ok(found) => json!(found),
                        Err(e) => json!({"error": e}),
                    }
                })
                .collect();
            json!({"answers": answers})
        }
        other => return Err(format!("no op {other}")),
    })
}

fn text_error(e: lp_text::Error) -> Value {
    json!({"error": e.message, "kind": format!("{:?}", e.kind)})
}

fn opt_str(v: &Value) -> Option<&str> {
    v.as_str()
}

fn strings(v: &Value) -> Option<Vec<String>> {
    v.as_array().map(|a| {
        a.iter()
            .map(|s| s.as_str().unwrap_or_default().to_string())
            .collect()
    })
}

fn image_answer(im: &Image, digest: bool) -> Value {
    if digest {
        let sum = Sha256::digest(im.as_bytes());
        let hex: String = sum.iter().map(|b| format!("{b:02x}")).collect();
        json!({"mode": im.mode().name(), "size": [im.width(), im.height()], "sha256": hex})
    } else {
        value_of(im)
    }
}

/// One font and what is asked of it: {"font": {"path", "size", "index", "encoding",
/// "layout"}, "calls": [{"fn": "getlength" | "getbbox" | "getmask2" | "draw" | "textbbox" |
/// "textlength", ...}]}; each call's answer, or its error and the error's kind.
fn font_case(case: &Value) -> Value {
    let f = &case["font"];
    let layout = match f["layout"].as_str() {
        Some("basic") => Some(lp_text::Layout::Basic),
        Some("raqm") => Some(lp_text::Layout::Raqm),
        _ => None,
    };
    let opened = lp_text::Font::open(
        f["path"].as_str().unwrap_or_default(),
        f["size"].as_f64().unwrap_or(10.0),
        f["index"].as_i64().unwrap_or(0),
        f["encoding"].as_str().unwrap_or_default(),
        layout,
    );
    let font = match opened {
        Ok(font) => font,
        Err(e) => return text_error(e),
    };
    let digest = case["digest"].as_bool().unwrap_or(false);
    let calls = case["calls"].as_array().cloned().unwrap_or_default();
    let (family, style) = font.getname();
    let mut answer = json!({
        "layout": format!("{:?}", font.layout()),
        "name": [family, style],
        "metrics": font.getmetrics(),
        "height": font.height(),
        "ppem": font.ppem(),
        "glyphs": font.glyphs(),
    });
    let results: Vec<Value> = if case["fallbacks"].is_object() {
        let face = lp_text::fallback::FallbackFont::new(
            font,
            Some(std::sync::Arc::new(chain_of(&case["fallbacks"]))),
        );
        calls
            .iter()
            .map(|c| font_call(&face, c, digest).unwrap_or_else(text_error))
            .collect()
    } else {
        calls
            .iter()
            .map(|c| font_call(&font, c, digest).unwrap_or_else(text_error))
            .collect()
    };
    answer["results"] = json!(results);
    answer
}

/// A chain of fonts to fall back to: {"fonts": [[regular, bold or null, index]], "scripts":
/// {"ja": [...]}, "language": "ja"} (no system query: what the tests set).
fn chain_of(v: &Value) -> lp_text::fallback::Fallbacks {
    let entries = |v: &Value| -> Vec<lp_text::fallback::Entry> {
        v.as_array()
            .map(|a| {
                a.iter()
                    .map(|e| {
                        (
                            e[0].as_str().unwrap_or_default().to_string(),
                            e[1].as_str().map(str::to_string),
                            e[2].as_i64().unwrap_or(0),
                        )
                    })
                    .collect()
            })
            .unwrap_or_default()
    };
    let scripts = v["scripts"]
        .as_object()
        .map(|o| o.iter().map(|(k, e)| (k.clone(), entries(e))).collect())
        .unwrap_or_default();
    let language = v["language"].as_str().map(str::to_string);
    let language: Option<Box<dyn Fn() -> String + Send + Sync>> =
        language.map(|l| Box::new(move || l.clone()) as Box<dyn Fn() -> String + Send + Sync>);
    lp_text::fallback::Fallbacks::new(entries(&v["fonts"]), None, language, scripts)
}

fn font_call<F: lp_text::text::TextFace>(
    font: &F,
    c: &Value,
    digest: bool,
) -> Result<Value, lp_text::Error> {
    use lp_text::text::{draw, Text};
    let bad = |m: &str| lp_text::Error {
        kind: lp_text::Kind::Value,
        message: m.to_string(),
    };
    let text = c["text"].as_str().unwrap_or_default();
    let mode = c["mode"].as_str().unwrap_or_default();
    let direction = opt_str(&c["direction"]);
    let features = strings(&c["features"]);
    let features = features.as_deref();
    let language = opt_str(&c["language"]);
    let anchor = opt_str(&c["anchor"]);
    let stroke = c["stroke_width"].as_f64().unwrap_or(0.0);
    let xy = |v: &Value| (v[0].as_f64().unwrap_or(0.0), v[1].as_f64().unwrap_or(0.0));
    Ok(match c["fn"].as_str().unwrap_or_default() {
        "getlength" => {
            json!({"length": font.getlength(text, mode, direction, features, language)?})
        }
        "getbbox" => {
            let b = font.getbbox(text, mode, direction, features, language, stroke, anchor)?;
            json!({"box": [b.0, b.1, b.2, b.3]})
        }
        "getmask2" => {
            let (im, offset) = font.getmask2(
                text,
                mode,
                direction,
                features,
                language,
                stroke,
                anchor,
                c["ink"].as_i64().unwrap_or(0),
                xy(&c["start"]),
                c["stroke_filled"].as_bool().unwrap_or(false),
            )?;
            json!({"image": image_answer(&im, digest), "offset": [offset.0, offset.1]})
        }
        "draw" | "textbbox" | "textlength" => {
            let mut image = match c["fn"].as_str() {
                Some("draw") => Some(image_of(&c["image"]).map_err(|e| bad(&e))?),
                _ => None,
            };
            let image_mode = match &image {
                Some(im) => im.mode().name().to_string(),
                None => c["image_mode"].as_str().unwrap_or("RGBA").to_string(),
            };
            let mut t = Text::new(text, font, &image_mode);
            t.spacing = c["spacing"].as_f64().unwrap_or(4.0);
            t.direction = direction;
            t.features = features;
            t.language = language;
            if c["embedded_color"].as_bool().unwrap_or(false) {
                t.embed_color()?;
            }
            t.stroke_width = stroke;
            let align = c["align"].as_str().unwrap_or("left");
            match (c["fn"].as_str(), image.as_mut()) {
                (Some("draw"), Some(im)) => {
                    let mode = im.mode();
                    let ink_of = |v: &Value| -> Result<Option<[u8; 4]>, lp_text::Error> {
                        if v.is_null() {
                            return Ok(None);
                        }
                        let colour: Vec<u8> = ints(v)
                            .map_err(|e| bad(&e))?
                            .iter()
                            .map(|&b| b as u8)
                            .collect();
                        mode.ink(&colour).map(Some).map_err(|e| bad(&e.0))
                    };
                    let ink = ink_of(&c["fill"])?.ok_or_else(|| bad("a fill"))?;
                    let stroke_ink = ink_of(&c["stroke_fill"])?;
                    draw(im, xy(&c["xy"]), &t, ink, stroke_ink, anchor, align)?;
                    json!({"image": image_answer(im, digest)})
                }
                (Some("textbbox"), _) => {
                    let b = t.get_bbox(xy(&c["xy"]), anchor, align)?;
                    json!({"box": [b.0, b.1, b.2, b.3]})
                }
                _ => json!({"length": t.get_length()?}),
            }
        }
        other => return Err(bad(&format!("no font call {other}"))),
    })
}

/// A value as the tests give it with its type: `{"f": bits in hex}`, `{"i": decimal}`,
/// `{"s": text}`, `{"b": bool}`, `{"l": [...]}`; anything else None.
fn typed(v: &Value) -> lp_py::Value {
    if let Some(h) = v["f"].as_str() {
        lp_py::Value::Float(f64::from_bits(u64::from_str_radix(h, 16).unwrap_or(0)))
    } else if let Some(i) = v["i"].as_str() {
        lp_py::Value::Int(i.parse().unwrap_or_default())
    } else if let Some(s) = v["s"].as_str() {
        lp_py::Value::Str(s.into())
    } else if let Some(b) = v["b"].as_bool() {
        lp_py::Value::Bool(b)
    } else if let Some(l) = v["l"].as_array() {
        lp_py::Value::List(l.iter().map(typed).collect())
    } else {
        lp_py::Value::None
    }
}

/// A snapshot as the tests give it: `readings` (key to a typed value), `images` (the keys
/// of the pictures: a blank one stands for each), `now` (year to microsecond), `history` (key
/// to its typed numbers).
fn snapshot_of(v: &Value) -> lp_render::snapshot::Snapshot {
    use lp_render::snapshot as sn;
    let mut snap = sn::Snapshot::default();
    if let Some(readings) = v["readings"].as_object() {
        for (key, value) in readings {
            // (a value, or {"v": value, "unit": .., "label": .., "origin": ..})
            let reading = match value.get("v") {
                Some(inner) => sn::Reading {
                    unit: value["unit"].as_str().unwrap_or("").to_string(),
                    label: value["label"].as_str().unwrap_or("").to_string(),
                    origin: value["origin"].as_str().unwrap_or("").to_string(),
                    ..sn::Reading::of(typed(inner))
                },
                None => sn::Reading::of(typed(value)),
            };
            snap.readings.insert(key.clone(), reading);
        }
    }
    // (the keys of the pictures, a blank one each; or each key with its picture)
    for key in v["images"].as_array().into_iter().flatten() {
        let blank = Image::blank(Mode::RGBA, 1, 1);
        snap.images.insert(key.as_str().unwrap_or("").into(), blank);
    }
    for (key, picture) in v["images"].as_object().into_iter().flatten() {
        if let Ok(im) = image_of(picture) {
            snap.images.insert(key.clone(), im);
        }
    }
    if let Some(now) = v["now"].as_array() {
        snap.now = datetime_of(now);
    }
    // (each sensor's history: its numbers, typed as the readings are)
    for (key, values) in v["history"].as_object().into_iter().flatten() {
        let numbers = values
            .as_array()
            .into_iter()
            .flatten()
            .map(|x| match typed(x) {
                lp_py::Value::Float(f) => f,
                lp_py::Value::Int(n) => lp_py::num::int_to_float(&n).unwrap_or(f64::NAN),
                _ => f64::NAN,
            });
        snap.history.insert(key.clone(), numbers.collect());
    }
    snap
}

/// A naive datetime from [year, month, day, hour, minute, second, microsecond].
fn datetime_of(at: &[Value]) -> lp_py::datetime::DateTime {
    let n = |i: usize| at.get(i).and_then(Value::as_i64).unwrap_or(0);
    lp_py::datetime::DateTime {
        year: n(0),
        month: n(1) as u32,
        day: n(2) as u32,
        hour: n(3) as u32,
        minute: n(4) as u32,
        second: n(5) as u32,
        microsecond: n(6) as u32,
    }
}

fn main() {
    // (as Python does when it starts: HarfBuzz's language of a text without one)
    lp_text::set_locale_from_environment();
    // (the fallback fonts of the East Asian languages go by the app's language, as Python's)
    lp_text::fallback::set_language(Box::new(|| lp_i18n::language().code().to_string()));
    let stdin = io::stdin();
    let mut out = BufWriter::new(io::stdout().lock());
    for line in stdin.lock().lines() {
        let line = line.expect("a line on stdin");
        if line.trim().is_empty() {
            continue;
        }
        let answer = match serde_json::from_str::<Value>(&line) {
            Ok(case) => run(&case).unwrap_or_else(|err| json!({"error": err})),
            Err(err) => json!({"error": format!("not JSON: {err}")}),
        };
        serde_json::to_writer(&mut out, &answer).expect("stdout");
        out.write_all(b"\n").expect("stdout");
    }
    out.flush().expect("stdout");
}
