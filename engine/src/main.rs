//! Libre Panel's native engine (stage 1 of docs/ROADMAP.md, "Next: a native engine").
//!
//! `libre-panel-engine encode ...` takes the frames Libre Panel draws on its stdin (wire.rs: the
//! parts of each that may have changed), makes the panel's YUV anew where they did (yuv.rs, byte
//! for byte as devices/yuv.py), encodes it with libx264 told which macroblocks stayed the same,
//! and writes the H.264 (Annex B) to its stdout, a picture for each frame as it is made. A new
//! bit rate goes into the running stream: no new encoder, no keyframe.
//!
//! `libre-panel-engine panel ...` does the same with a TURZX panel of its own (panel.rs): it
//! sends Libre Panel's commands and its own pictures to the panel, each picture on its time.
//!
//! Either takes a frame also as the order of layers Libre Panel drew, each sent once: the
//! engine composes it then (scene.rs), where it changed.

mod globe;
mod link;
mod panel;
mod ring;
mod scene;
#[rustfmt::skip]
mod tables;
mod transport;
mod turzx;
mod video;
mod wire;
mod x264;
mod yuv;

use std::io::{self, BufReader, BufWriter, Write};
use std::process::ExitCode;
use std::time::Instant;

const USAGE: &str = "usage: libre-panel-engine encode --size WxH --turn 0|180|270 --fps N --crf N \
--kbit N --keyint N [--preset P] [--profile P] [--full] [--x264 name=value:...] [--dump-yuv PATH] [--dump-rgb PATH] \
[--stats]\n       libre-panel-engine panel --pid HEX [--ports N.N...] [--lead-ms N]\n       \
libre-panel-engine version";

pub(crate) struct Options {
    pub(crate) size: (usize, usize),
    pub(crate) turn: yuv::Turn,
    pub(crate) settings: x264::Settings,
    dump: Option<String>,
    /// Each frame's RGB as the YUV was made of it, whole (the tests': a frame of layers is
    /// composed as Libre Panel composes it).
    dump_rgb: Option<String>,
    stats: bool,
}

/// The options of `encode`, also those a panel's stream is opened with (panel.rs).
pub(crate) fn parse(args: &[String]) -> Result<Options, String> {
    let mut size = None;
    let mut turn = yuv::Turn::None;
    let (mut fps, mut crf, mut kbit, mut keyint) = (25u32, 20f32, 2000u32, 250u32);
    let (mut preset, mut profile) = ("superfast".to_string(), "high".to_string());
    let (mut full, mut extra, mut dump, mut stats) = (false, String::new(), None, false);
    let mut dump_rgb = None;
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
            "--dump-rgb" => {
                dump_rgb = Some(value(i)?);
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
        dump_rgb,
        stats,
    })
}

fn encode(options: Options) -> Result<(), String> {
    let (width, height) = options.size;
    let mut frame = yuv::Yuv::new(width, height, options.turn, options.settings.full)?;
    let mut scene = scene::Scene::new(width, height, frame.block);
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
    let mut dump_rgb = match &options.dump_rgb {
        Some(path) => Some(BufWriter::new(
            std::fs::File::create(path).map_err(|e| e.to_string())?,
        )),
        None => None,
    };
    let (mut frames, mut made, mut spent_yuv, mut spent_x264) = (0u64, 0u64, 0f64, 0f64);
    let (mut spent_layers, mut spent_scenes) = (0f64, 0f64);
    loop {
        let record = wire::read(&mut input).map_err(|e| format!("the input: {e}"))?;
        match record {
            None => break,
            Some(wire::Record::Rate(kbit)) => encoder.rate(kbit)?,
            Some(wire::Record::Keyframe) => encoder.keyframe(),
            Some(
                wire::Record::Command(_)
                | wire::Record::Stream { .. }
                | wire::Record::End
                | wire::Record::Picture { .. },
            ) => return Err("a panel's record, but no panel here (see `panel`)".into()),
            Some(wire::Record::Ground { colour, picture }) => match colour {
                Some(rgb) => scene.ground_colour(rgb),
                None => scene.ground_picture(picture)?,
            },
            Some(wire::Record::Layer {
                id,
                x,
                y,
                w,
                h,
                how,
                pixels,
            }) => {
                let began = Instant::now();
                let how = scene::How::of(how)
                    .ok_or_else(|| format!("layer {id} goes on in no way known ({how})"))?;
                scene.layer(id, scene::Layer::new(x, y, w, h, how, pixels)?);
                spent_layers += began.elapsed().as_secs_f64();
            }
            Some(wire::Record::RingBase {
                id,
                side,
                geometry,
                groove,
                lit,
            }) => scene.ring_base(id, ring::Base::new(side, &geometry, groove, lit)?),
            Some(wire::Record::Source { id, w, h, pixels }) => scene.source(id, w, h, pixels)?,
            Some(wire::Record::Globe { id, tables }) => scene.globe(id, tables),
            Some(wire::Record::GlobeLayer {
                id,
                globe,
                map,
                air,
                step,
                x,
                y,
                mark,
            }) => {
                let began = Instant::now();
                scene.globe_layer(id, globe, (map, air, step), (x, y), mark)?;
                spent_layers += began.elapsed().as_secs_f64();
            }
            Some(wire::Record::View {
                id,
                x,
                y,
                w,
                h,
                parts,
            }) => {
                let began = Instant::now();
                scene.view(id, (x, y, w, h), &parts)?;
                spent_layers += began.elapsed().as_secs_f64();
            }
            Some(wire::Record::Ring {
                id,
                ring,
                x,
                y,
                frac,
            }) => {
                let began = Instant::now();
                scene.ring(id, ring, x, y, frac)?;
                spent_layers += began.elapsed().as_secs_f64();
            }
            Some(record @ (wire::Record::Frame { .. } | wire::Record::Scene { .. })) => {
                let began = Instant::now();
                let (number, boxes) = match record {
                    wire::Record::Frame { number, boxes, .. } => {
                        scene.frame_between(); // (the next frame of layers: composed whole)
                        (number, boxes)
                    }
                    wire::Record::Scene {
                        number,
                        half,
                        order,
                        ..
                    } => {
                        let composed = Instant::now();
                        let boxes = scene.frame(order, half)?;
                        spent_scenes += composed.elapsed().as_secs_f64();
                        (number, boxes)
                    }
                    _ => unreachable!("a frame"),
                };
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
                if let Some(out) = dump_rgb.as_mut() {
                    out.write_all(frame.rgb()).map_err(|e| e.to_string())?;
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
    for out in [dump, dump_rgb].into_iter().flatten() {
        let mut out = out;
        out.flush().map_err(|e| e.to_string())?;
    }
    if options.stats && frames > 0 {
        let per = |s: f64| s * 1000.0 / frames as f64;
        eprintln!(
            "{{\"frames\": {frames}, \"blocks_made\": {made}, \"yuv_ms\": {:.3}, \"x264_ms\": {:.3}, \
             \"layers_ms\": {:.3}, \"compose_ms\": {:.3}}}",
            per(spent_yuv),
            per(spent_x264),
            per(spent_layers),
            per(spent_scenes)
        );
    }
    Ok(())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("version") => {
            // (Libre Panel reads what it can there: "panel", a panel of its own (stage 1b);
            // "scenes", frames of layers it composes (stage 2, scene.rs); "rings", a lit gauge's
            // ring it makes for each value (ring.rs); "views", layers it cuts from sources it
            // keeps, a graph's window from its strip (scene.rs's view); "globes", a turning
            // globe's picture it makes for each step (globe.rs))
            println!(
                "libre-panel-engine {} (x264 build {}; panel, scenes, rings, views, globes)",
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
        Some("panel") => match panel::parse(&args[1..]).and_then(panel::run) {
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
