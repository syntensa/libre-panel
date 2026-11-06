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
