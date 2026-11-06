//! `lp-check`: runs the operations it reads, one JSON object a line on stdin, and writes each
//! one's result as a line on stdout, for the tests that compare Libre Panel's Rust crates with
//! the Python app (tests/test_rust_*.py, docs/RUST.md).
//!
//! An image goes as {"mode": "RGBA", "size": [w, h], "data": base64 of Pillow's tobytes()}.
//! A result is {"image": ...}, {"images": [...]}, {"box": [x0, y0, x1, y1] or null}, or
//! {"error": "..."} where the operation refuses what it was given.

use std::io::{self, BufRead, BufWriter, Write};

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
            let text = |t: &Value| t.as_str().unwrap_or_default().to_string();
            let lowers: Vec<String> = texts.iter().map(|t| lp_py::lower(&text(t))).collect();
            let uppers: Vec<String> = texts.iter().map(|t| lp_py::upper(&text(t))).collect();
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
                "lowers": lowers, "uppers": uppers, "quotients": quotients});
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

fn main() {
    // (as Python does when it starts: HarfBuzz's language of a text without one)
    lp_text::set_locale_from_environment();
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
