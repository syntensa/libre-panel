//! lp-check's cases for lp-devices (tests/test_rust_devices.py): JPEGs as Pillow saves them, a
//! picture dimmed, and the Hongtai family's frames, pictures, answers and whole sessions on a
//! panel the case scripts.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use lp_devices::base::{self, DeviceError};
use lp_devices::hongtai::{self, HongtaiDisplay, Kind, Timing};
use lp_devices::link::Link;
use serde_json::{json, Value};

use crate::{image_of, value_of};

fn bytes_of(v: &Value) -> Result<Vec<u8>, String> {
    STANDARD
        .decode(v.as_str().unwrap_or_default())
        .map_err(|e| e.to_string())
}

fn b64(data: &[u8]) -> Value {
    Value::String(STANDARD.encode(data))
}

/// A panel the case scripts: what it answers each frame written to it (the whole of a write),
/// what it was sent.
struct Scripted {
    written: Arc<Mutex<Vec<u8>>>,
    pending: Vec<u8>,
    replies: Vec<(Vec<u8>, Vec<u8>)>,
}

impl Link for Scripted {
    fn write(&mut self, data: &[u8]) -> lp_devices::base::Result<()> {
        self.written.lock().unwrap().extend_from_slice(data);
        for (asked, reply) in &self.replies {
            if data == asked.as_slice() {
                self.pending.extend_from_slice(reply);
            }
        }
        Ok(())
    }

    fn read(&mut self, size: usize) -> lp_devices::base::Result<Vec<u8>> {
        let n = size.min(self.pending.len());
        Ok(self.pending.drain(..n).collect())
    }

    fn drain_input(&mut self) {
        self.pending.clear();
    }

    fn close(&mut self) {}
}

fn error_of(e: DeviceError) -> String {
    e.0
}

pub fn run(op: &str, case: &Value) -> Result<Value, String> {
    Ok(match op {
        "jpeg_save" => {
            let quality = case["quality"].as_i64().ok_or("a quality")?;
            match lp_decode::save_jpeg(&image_of(&case["image"])?, quality) {
                Ok(data) => json!({"data": b64(&data)}),
                Err(e) => json!({"error": e.msg}),
            }
        }
        "dimmed" => {
            let percent = case["percent"].as_i64().ok_or("a percent")?;
            let out = base::dimmed(&image_of(&case["image"])?, percent).map_err(error_of)?;
            json!({"image": value_of(&out)})
        }
        "hongtai_frame" => {
            let command = case["command"].as_u64().ok_or("a command")? as u8;
            match hongtai::frame(command, &bytes_of(&case["data"])?) {
                Ok(data) => json!({"data": b64(&data)}),
                Err(e) => json!({"error": e.0}),
            }
        }
        "hongtai_picture" => {
            json!({"data": b64(&hongtai::picture(&bytes_of(&case["jpeg"])?).map_err(error_of)?)})
        }
        "hongtai_jpeg" => {
            json!({"data": b64(&hongtai::jpeg(&image_of(&case["image"])?).map_err(error_of)?)})
        }
        "hongtai_answer" => {
            let mut link = Scripted {
                written: Arc::default(),
                pending: bytes_of(&case["stream"])?,
                replies: Vec::new(),
            };
            match hongtai::answer(&mut link) {
                Ok(info) => json!({"info": lp_py::Value::Dict(info).repr()}),
                Err(e) => json!({"error": e.0}),
            }
        }
        "hongtai_session" => session(case)?,
        other => return Err(format!("no case {other}")),
    })
}

/// A whole session: opened on the scripted panel, its steps (a picture shown, a brightness
/// set), closed; what the panel was sent, and what it said when opened (or the error, and what
/// was sent up to it).
fn session(case: &Value) -> Result<Value, String> {
    let kind = match case["kind"].as_str() {
        Some("majesty") => Kind::Majesty,
        _ => Kind::Family,
    };
    let native = case["native"]
        .as_array()
        .map(|v| (v[0].as_i64().unwrap_or(0), v[1].as_i64().unwrap_or(0)));
    let written = Arc::new(Mutex::new(Vec::new()));
    let mut replies = vec![
        (hongtai::RESET.to_vec(), bytes_of(&case["stale"])?),
        (
            hongtai::frame(hongtai::CMD_INFO, &[]).map_err(error_of)?,
            bytes_of(&case["answer"])?,
        ),
    ];
    if !case["setup_answer"].is_null() {
        replies.push((hongtai::majesty_setup(), bytes_of(&case["setup_answer"])?));
    }
    let link = Scripted {
        written: Arc::clone(&written),
        pending: Vec::new(),
        replies,
    };
    let timing = Timing {
        settle: Duration::ZERO,
        // (no 11 of the keeper's within a case, unless it asks for them)
        live_every: Duration::from_millis(case["live_every_ms"].as_u64().unwrap_or(3_600_000)),
        setup_answer: Duration::from_millis(case["setup_wait_ms"].as_u64().unwrap_or(50)),
    };
    let sent = |written: &Arc<Mutex<Vec<u8>>>| b64(&written.lock().unwrap());
    let port = case["port"].as_str().unwrap_or("COM5");
    let (mut display, identity) = match HongtaiDisplay::open(link, kind, native, port, timing) {
        Ok(opened) => opened,
        Err(e) => return Ok(json!({"error": e.0, "written": sent(&written)})),
    };
    let mut failed = None;
    for step in case["steps"].as_array().cloned().unwrap_or_default() {
        let done = if let Some(percent) = step["brightness"].as_i64() {
            display.set_brightness(percent)
        } else if let Some(wait) = step["wait_ms"].as_u64() {
            std::thread::sleep(Duration::from_millis(wait));
            Ok(())
        } else {
            display.show(&image_of(&step["show"])?)
        };
        if let Err(e) = done {
            failed = Some(e.0);
            break;
        }
    }
    let counters = display.counters();
    display.close();
    Ok(json!({
        "written": sent(&written),
        "native": [identity.native.0, identity.native.1],
        "name": identity.name,
        "named": identity.named,
        "set_up": identity.set_up,
        "error": failed,
        "bytes": counters.bytes,
    }))
}
