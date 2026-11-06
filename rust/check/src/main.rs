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
use lp_pixels::{Image, Mode};
use serde_json::{json, Value};

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
            let (x, y) = (
                r[0].as_f64().ok_or("x")? as f32,
                r[1].as_f64().ok_or("y")? as f32,
            );
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
        "draw" => {
            let mut im = image_of(&case["image"])?;
            for c in case["commands"].as_array().ok_or("commands")? {
                let ink: Vec<u8> = ints(&c["ink"])?.iter().map(|&v| v as u8).collect();
                let width = c["width"].as_i64().unwrap_or(1) as i32;
                let fill = c["fill"].as_bool().unwrap_or(false);
                match c["kind"].as_str().unwrap_or("") {
                    "points" => im.draw_points(&points(&c["xy"])?, &ink),
                    "lines" => im.draw_lines(&points(&c["xy"])?, &ink, width),
                    "polygon" => im.draw_polygon(&points(&c["xy"])?, &ink, fill, width),
                    "rectangle" => {
                        let b = floats4(&c["xy"])?.ok_or("a box")?;
                        im.draw_rectangle(b, &ink, fill, width)
                    }
                    "ellipse" => {
                        let b = floats4(&c["xy"])?.ok_or("a box")?;
                        im.draw_ellipse(b, &ink, fill, width)
                    }
                    "arc" | "chord" | "pieslice" => {
                        let b = floats4(&c["xy"])?.ok_or("a box")?;
                        let start = c["start"].as_f64().ok_or("a start")? as f32;
                        let end = c["end"].as_f64().ok_or("an end")? as f32;
                        match c["kind"].as_str() {
                            Some("arc") => im.draw_arc(b, start, end, &ink, width),
                            Some("chord") => im.draw_chord(b, start, end, &ink, fill, width),
                            _ => im.draw_pieslice(b, start, end, &ink, fill, width),
                        }
                    }
                    "bitmap" => {
                        let at = points(&c["xy"])?;
                        im.draw_bitmap(at[0].0, at[0].1, &image_of(&c["bitmap"])?, &ink)
                    }
                    other => return Err(format!("no drawing {other}")),
                }
                .map_err(e)?;
            }
            json!({"image": value_of(&im)})
        }
        other => return Err(format!("no op {other}")),
    })
}

fn main() {
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
