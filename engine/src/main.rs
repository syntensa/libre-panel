//! Libre Panel's native engine (stage 1 of docs/ROADMAP.md, "Next: a native engine").
//!
//! `libre-panel-engine encode ...` takes the frames Libre Panel draws on its stdin (wire.rs: the
//! parts of each that may have changed), makes the panel's YUV anew where they did (yuv.rs, byte
//! for byte as devices/yuv.py), encodes it with libx264 told which macroblocks stayed the same,
//! and writes the H.264 (Annex B) to its stdout, a picture for each frame as it is made. A new
//! bit rate goes into the running stream: no new encoder, no keyframe.

#[rustfmt::skip]
mod tables;
mod wire;
mod x264;
mod yuv;

use std::io::{self, BufReader, BufWriter, Write};
use std::process::ExitCode;
use std::time::Instant;

const USAGE: &str = "usage: libre-panel-engine encode --size WxH --turn 0|180|270 --fps N --crf N \
--kbit N --keyint N [--preset P] [--profile P] [--full] [--x264 name=value:...] [--dump-yuv PATH] \
[--stats]\n       libre-panel-engine version";

struct Options {
    size: (usize, usize),
    turn: yuv::Turn,
    settings: x264::Settings,
    dump: Option<String>,
    stats: bool,
}

fn parse(args: &[String]) -> Result<Options, String> {
    let mut size = None;
    let mut turn = yuv::Turn::None;
    let (mut fps, mut crf, mut kbit, mut keyint) = (25u32, 20f32, 2000u32, 250u32);
    let (mut preset, mut profile) = ("superfast".to_string(), "high".to_string());
    let (mut full, mut extra, mut dump, mut stats) = (false, String::new(), None, false);
    let mut i = 0;
    let value = |i: usize| {
        args.get(i + 1)
            .cloned()
            .ok_or_else(|| format!("{} wants a value", args[i]))
    };
    while i < args.len() {
        match args[i].as_str() {
            "--size" => {
                let text = value(i)?;
                let (w, h) = text.split_once('x').ok_or("--size is WxH")?;
                size = Some((
                    w.parse().map_err(|_| "--size")?,
                    h.parse().map_err(|_| "--size")?,
                ));
                i += 1;
            }
            "--turn" => {
                turn = yuv::Turn::of(value(i)?.parse().map_err(|_| "--turn")?)
                    .ok_or("--turn is 0, 180 or 270")?;
                i += 1;
            }
            "--fps" => {
                fps = value(i)?.parse().map_err(|_| "--fps")?;
                i += 1;
            }
            "--crf" => {
                crf = value(i)?.parse().map_err(|_| "--crf")?;
                i += 1;
            }
            "--kbit" => {
                kbit = value(i)?.parse().map_err(|_| "--kbit")?;
                i += 1;
            }
            "--keyint" => {
                keyint = value(i)?.parse().map_err(|_| "--keyint")?;
                i += 1;
            }
            "--preset" => {
                preset = value(i)?;
                i += 1;
            }
            "--profile" => {
                profile = value(i)?;
                i += 1;
            }
            "--x264" => {
                extra = value(i)?;
                i += 1;
            }
            "--dump-yuv" => {
                dump = Some(value(i)?);
                i += 1;
            }
            "--full" => full = true,
            "--stats" => stats = true,
            other => return Err(format!("unknown option {other}")),
        }
        i += 1;
    }
    let size = size.ok_or("--size is wanted")?;
    let turned = if turn == yuv::Turn::Quarter {
        (size.1, size.0)
    } else {
        size
    };
    let settings = x264::Settings {
        width: turned.0,
        height: turned.1,
        fps,
        crf,
        kbit,
        keyint,
        preset,
        profile,
        full,
        extra,
    };
    Ok(Options {
        size,
        turn,
        settings,
        dump,
        stats,
    })
}

fn encode(options: Options) -> Result<(), String> {
    let (width, height) = options.size;
    let mut frame = yuv::Yuv::new(width, height, options.turn, options.settings.full)?;
    let mut encoder = x264::Encoder::open(&options.settings)?;
    let (mb_cols, mb_rows) = frame.macroblocks();
    let mut mb = vec![0u8; mb_cols * mb_rows];
    let mut input = BufReader::with_capacity(1 << 20, io::stdin().lock());
    let mut output = BufWriter::with_capacity(1 << 20, io::stdout().lock());
    let mut dump = match &options.dump {
        Some(path) => Some(BufWriter::new(
            std::fs::File::create(path).map_err(|e| e.to_string())?,
        )),
        None => None,
    };
    let (mut frames, mut made, mut spent_yuv, mut spent_x264) = (0u64, 0u64, 0f64, 0f64);
    loop {
        let record = wire::read(&mut input).map_err(|e| format!("the input: {e}"))?;
        match record {
            None => break,
            Some(wire::Record::Rate(kbit)) => encoder.rate(kbit)?,
            Some(wire::Record::Keyframe) => encoder.keyframe(),
            Some(wire::Record::Frame { number, boxes }) => {
                let began = Instant::now();
                for (x0, y0, x1, y1, pixels) in &boxes {
                    frame.put(*x0, *y0, *x1, *y1, pixels)?;
                }
                made += frame.finish(&mut mb) as u64;
                let converted = Instant::now();
                if let Some(out) = dump.as_mut() {
                    for plane in [&frame.y, &frame.u, &frame.v] {
                        out.write_all(plane).map_err(|e| e.to_string())?;
                    }
                }
                let stride = frame.turned.0;
                let (picture, _key) = encoder.encode(
                    &mut frame.y,
                    &mut frame.u,
                    &mut frame.v,
                    stride,
                    &mut mb,
                    number as i64,
                )?;
                if !picture.is_empty() {
                    output
                        .write_all(picture)
                        .map_err(|e| format!("the output: {e}"))?;
                    output.flush().map_err(|e| format!("the output: {e}"))?;
                }
                spent_yuv += (converted - began).as_secs_f64();
                spent_x264 += converted.elapsed().as_secs_f64();
                frames += 1;
            }
        }
    }
    if let Some(mut out) = dump {
        out.flush().map_err(|e| e.to_string())?;
    }
    if options.stats && frames > 0 {
        eprintln!(
            "{{\"frames\": {frames}, \"blocks_made\": {made}, \"yuv_ms\": {:.3}, \"x264_ms\": {:.3}}}",
            spent_yuv * 1000.0 / frames as f64,
            spent_x264 * 1000.0 / frames as f64
        );
    }
    Ok(())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("version") => {
            println!(
                "libre-panel-engine {} (x264 build {})",
                env!("CARGO_PKG_VERSION"),
                x264::build()
            );
            ExitCode::SUCCESS
        }
        Some("encode") => match parse(&args[1..]).and_then(encode) {
            Ok(()) => ExitCode::SUCCESS,
            Err(e) => {
                eprintln!("libre-panel-engine: {e}");
                ExitCode::FAILURE
            }
        },
        _ => {
            eprintln!("{USAGE}");
            ExitCode::from(2)
        }
    }
}
