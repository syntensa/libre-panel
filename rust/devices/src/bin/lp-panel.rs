//! lp-panel: a panel tried on its own, for its owner (docs/RUST.md, R6): the serial ports the
//! system has and what is on them; and a Hongtai screen or the MAJESTY 9.16" (issue #40) tried
//! with Libre Panel's driver in Rust: opened, a card to tell its way round by, its brightness
//! stepped, pictures as fast as it takes them, let go. Each step says what it did, and how long
//! it took.

use std::process::ExitCode;
use std::thread::sleep;
use std::time::{Duration, Instant};

use lp_devices::hongtai::{self, HongtaiDisplay, Kind, Timing};
use lp_devices::serial::{self, SerialPort};
use lp_pixels::{Image, Mode};

const USAGE: &str = "usage: lp-panel list
       lp-panel test [--port PORT] [--kind majesty|hongtai] [--seconds N]";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("list") => {
            list();
            ExitCode::SUCCESS
        }
        Some("test") => match test(&args[1..]) {
            Ok(()) => ExitCode::SUCCESS,
            Err(e) => {
                eprintln!("lp-panel: {e}");
                ExitCode::FAILURE
            }
        },
        _ => {
            eprintln!("{USAGE}");
            ExitCode::from(2)
        }
    }
}

fn kind_name(kind: Kind) -> &'static str {
    match kind {
        Kind::Majesty => "MAJESTY 9.16\" AIO monitor",
        Kind::Family => "Hongtai screen",
    }
}

/// The ports, their USB ids and what Libre Panel takes them for.
fn list() {
    let ports = serial::ports();
    if ports.is_empty() {
        println!("no serial ports");
    }
    for port in ports {
        let ids = match (port.vid, port.pid) {
            (Some(v), Some(p)) => format!("{v:04x}:{p:04x}"),
            _ => "-".to_string(),
        };
        let kind = port
            .vid
            .zip(port.pid)
            .and_then(Kind::of_usb)
            .map(|k| format!("  <- {}", kind_name(k)))
            .unwrap_or_default();
        let serial = port.serial_number.unwrap_or_default();
        println!("{}  {ids}  {serial}{kind}", port.device);
    }
}

struct Asked {
    port: Option<String>,
    kind: Option<Kind>,
    seconds: u64,
}

fn asked(args: &[String]) -> Result<Asked, String> {
    let mut asked = Asked {
        port: None,
        kind: None,
        seconds: 10,
    };
    let mut i = 0;
    while i < args.len() {
        let value = args
            .get(i + 1)
            .ok_or_else(|| format!("{} wants a value\n{USAGE}", args[i]))?;
        match args[i].as_str() {
            "--port" => asked.port = Some(value.clone()),
            "--kind" => {
                asked.kind = Some(match value.as_str() {
                    "majesty" => Kind::Majesty,
                    "hongtai" => Kind::Family,
                    _ => return Err(format!("--kind is majesty or hongtai\n{USAGE}")),
                })
            }
            "--seconds" => {
                asked.seconds = value
                    .parse()
                    .map_err(|_| format!("--seconds is a whole number\n{USAGE}"))?
            }
            other => return Err(format!("{other}?\n{USAGE}")),
        }
        i += 2;
    }
    Ok(asked)
}

/// The panel's port and kind: the one asked, else the first port whose USB ids name one.
fn which(asked: &Asked) -> Result<(String, Kind), String> {
    let ports = serial::ports();
    let kind_of = |device: &str| {
        ports
            .iter()
            .find(|p| p.device == device)
            .and_then(|p| p.vid.zip(p.pid))
            .and_then(Kind::of_usb)
    };
    match &asked.port {
        Some(device) => {
            let kind = asked.kind.or_else(|| kind_of(device)).ok_or(format!(
                "{device} is no panel Libre Panel knows by its USB ids: say which with --kind"
            ))?;
            Ok((device.clone(), kind))
        }
        None => ports
            .iter()
            .find_map(|p| {
                let kind = p.vid.zip(p.pid).and_then(Kind::of_usb)?;
                Some((p.device.clone(), asked.kind.unwrap_or(kind)))
            })
            .ok_or_else(|| "no Hongtai screen or MAJESTY found (lp-panel list)".to_string()),
    }
}

/// A picture of `w` x `h` (RGB), each pixel `colour(x, y)`.
fn painted(w: usize, h: usize, colour: impl Fn(usize, usize) -> [u8; 3]) -> Image {
    let mut data = Vec::with_capacity(w * h * 3);
    for y in 0..h {
        for x in 0..w {
            data.extend_from_slice(&colour(x, y));
        }
    }
    Image::from_bytes(Mode::RGB, w, h, data).expect("(its size)")
}

/// The card to tell the panel's way round by: a corner of each colour (top left red, top right
/// green, bottom left blue, bottom right white), an arrow pointing up in the middle, a ramp of
/// grey from black to white to see the brightness by.
fn card(w: usize, h: usize) -> Image {
    let side = (h / 4).max(1);
    let (cx, top, base, bottom) = (
        w as f64 / 2.0,
        h as f64 * 0.12,
        h as f64 * 0.55,
        h as f64 * 0.88,
    );
    let (half, shaft) = (h as f64 * 0.28, h as f64 * 0.09);
    painted(w, h, |x, y| {
        let (left, upper) = (x < side, y < side);
        let (right, lower) = (x >= w - side, y >= h - side);
        match (left, right, upper, lower) {
            (true, _, true, _) => return [230, 30, 30],
            (_, true, true, _) => return [30, 200, 60],
            (true, _, _, true) => return [40, 80, 230],
            (_, true, _, true) => return [240, 240, 240],
            _ => {}
        }
        let (fx, fy) = (x as f64 + 0.5, y as f64 + 0.5);
        let head = fy >= top && fy < base && (fx - cx).abs() <= half * (fy - top) / (base - top);
        let stem = fy >= base && fy < bottom && (fx - cx).abs() <= shaft;
        if head || stem {
            return [255, 255, 255];
        }
        if y > h / 3 && y < h * 2 / 3 && x > side * 2 && x < w / 2 - side * 2 {
            let v = (255 * (x - side * 2) / (w / 2 - side * 4).max(1)).min(255) as u8;
            return [v, v, v];
        }
        [24, 24, 28]
    })
}

/// A picture of the run: a bar going across, so that each picture differs from the last.
fn moving(w: usize, h: usize, n: usize) -> Image {
    let at = (n * 24) % w.max(1);
    painted(w, h, |x, y| {
        if x >= at && x < at + 24 {
            return [250, 250, 250];
        }
        let v = (40 + 120 * y / h.max(1)) as u8;
        [v / 3, v / 2, v]
    })
}

fn test(args: &[String]) -> Result<(), String> {
    let asked = asked(args)?;
    let (device, kind) = which(&asked)?;
    println!("opening {device} as a {}", kind_name(kind));
    let started = Instant::now();
    let port = SerialPort::open(&device, hongtai::BAUD, false).map_err(|e| e.0)?;
    let native = match kind {
        Kind::Majesty => Some((462, 1920)),
        Kind::Family => None,
    };
    let (mut display, identity) =
        HongtaiDisplay::open(port, kind, native, &device, Timing::PANEL).map_err(|e| e.0)?;
    let (nw, nh) = identity.native;
    println!(
        "opened in {:.1} s: it calls itself {:?}, {nw}x{nh}",
        started.elapsed().as_secs_f64(),
        identity.name
    );
    if kind == Kind::Majesty {
        match identity.set_up {
            Some(command) => println!("set up: it answered 15 with a frame of {command:#04x}"),
            None => println!("set up: no answer to 15 within 3 s (went on)"),
        }
    }
    let (w, h) = (nw.max(nh).max(1) as usize, nw.min(nh).max(1) as usize);
    println!("1/3 the card for 5 s: top left red, top right green, bottom left blue, bottom right");
    println!("    white; the white arrow points up, the grey goes from black on the left to white");
    display.show(&card(w, h)).map_err(|e| e.0)?;
    sleep(Duration::from_secs(5));
    println!("2/3 the brightness at 100, 60, 20 and 100 %, 2 s each");
    for percent in [100, 60, 20, 100] {
        println!("    {percent} %");
        display.set_brightness(percent).map_err(|e| e.0)?;
        sleep(Duration::from_secs(2));
    }
    println!(
        "3/3 pictures as fast as it takes them, for {} s",
        asked.seconds
    );
    let run = Instant::now();
    let before = display.counters().bytes;
    let mut shown = 0usize;
    while run.elapsed() < Duration::from_secs(asked.seconds) {
        display.show(&moving(w, h, shown)).map_err(|e| e.0)?;
        shown += 1;
    }
    let took = run.elapsed().as_secs_f64();
    let sent = display.counters().bytes - before;
    println!(
        "    {shown} pictures in {took:.1} s: {:.1} a second, {:.0} KB each",
        shown as f64 / took,
        sent as f64 / shown.max(1) as f64 / 1024.0
    );
    display.close();
    println!("done: let go (the panel shows its own animation again)");
    Ok(())
}
