//! lp-check's sessions of the drivers of USB screens (tests/test_rust_usb_drivers.py): a
//! driver opened on a screen the case scripts (its product string, its interfaces, what it
//! answers each read), frames shown, the brightness set, closed; what went to the screen, in
//! order, as the Python driver does it on the same script.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use lp_devices::driver::Driver;
use lp_devices::models::find_model;
use lp_devices::usb_link::{EndpointInfo, InterfaceInfo, UsbError, UsbIo, UsbResult};
use serde_json::{json, Value};

use crate::image_of;

fn b64(data: &[u8]) -> Value {
    Value::String(STANDARD.encode(data))
}

type Events = Arc<Mutex<Vec<Value>>>;

/// A screen the case scripts: each read takes the next answer (null: none in time; an object
/// {"error": text}: the transfer fails with that text); a write may fail as "fail_writes" says ([n, errno,
/// text]: the n-th write, counted from 1, fails as pyusb's USBError(text, errno=errno) says).
struct ScriptedUsb {
    product: String,
    interfaces: Vec<InterfaceInfo>,
    answers: VecDeque<Value>,
    fail_writes: Vec<(u64, String)>,
    writes: u64,
    events: Events,
}

impl ScriptedUsb {
    fn note(&self, event: Value) {
        self.events.lock().unwrap().push(event);
    }

    fn answer(&mut self) -> UsbResult<Vec<u8>> {
        match self.answers.pop_front() {
            None | Some(Value::Null) => Err(UsbError::Timeout),
            Some(Value::Object(error)) => Err(UsbError::Other(
                error
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
            )),
            Some(text) => STANDARD
                .decode(text.as_str().unwrap_or(""))
                .map_err(|e| UsbError::Other(e.to_string())),
        }
    }
}

impl UsbIo for ScriptedUsb {
    fn product(&mut self) -> String {
        self.product.clone()
    }

    fn interfaces(&self) -> Vec<InterfaceInfo> {
        self.interfaces.clone()
    }

    fn claim(&mut self, interfaces: &[u8]) -> UsbResult<()> {
        self.note(json!(["claim", interfaces]));
        Ok(())
    }

    fn write(&mut self, endpoint: u8, data: &[u8], _timeout_ms: u64) -> UsbResult<()> {
        self.writes += 1;
        self.note(json!(["write", endpoint, b64(data)]));
        if let Some((_, why)) = self.fail_writes.iter().find(|(n, _)| *n == self.writes) {
            return Err(UsbError::Other(why.clone()));
        }
        Ok(())
    }

    fn read(&mut self, endpoint: u8, size: usize, _timeout_ms: u64) -> UsbResult<Vec<u8>> {
        self.note(json!(["read", endpoint, size]));
        let mut data = self.answer()?;
        data.truncate(size);
        Ok(data)
    }

    fn control_out(
        &mut self,
        request_type: u8,
        request: u8,
        value: u16,
        index: u16,
        data: &[u8],
        _timeout_ms: u64,
    ) -> UsbResult<()> {
        self.note(json!([
            "ctrl",
            request_type,
            request,
            value,
            index,
            b64(data)
        ]));
        Ok(())
    }

    fn control_in(
        &mut self,
        request_type: u8,
        request: u8,
        value: u16,
        index: u16,
        length: u16,
        _timeout_ms: u64,
    ) -> UsbResult<Vec<u8>> {
        self.note(json!(["ctrl", request_type, request, value, index, length]));
        let mut data = self.answer()?;
        data.truncate(usize::from(length));
        Ok(data)
    }

    fn release(&mut self) {
        self.note(json!(["release"]));
    }
}

fn interfaces_of(v: &Value) -> Vec<InterfaceInfo> {
    v.as_array()
        .into_iter()
        .flatten()
        .map(|i| InterfaceInfo {
            number: i["number"].as_u64().unwrap_or(0) as u8,
            class: i["class"].as_u64().unwrap_or(0) as u8,
            endpoints: i["endpoints"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|e| EndpointInfo {
                    address: e[0].as_u64().unwrap_or(0) as u8,
                    max_packet: e[1].as_u64().unwrap_or(0) as usize,
                    interrupt: e[2].as_bool().unwrap_or(false),
                })
                .collect(),
        })
        .collect()
}

/// The driver of `case["driver"]` opened on `io`.
fn opened(case: &Value, io: Box<dyn UsbIo>) -> Result<Box<dyn Driver>, String> {
    let model = case["model"].as_str().and_then(find_model);
    let options = &case["options"];
    let text = |key: &str, default: &str| options[key].as_str().unwrap_or(default).to_string();
    let int = |key: &str, default: i64| options[key].as_i64().unwrap_or(default);
    match case["driver"].as_str().unwrap_or("") {
        "udisp" => {
            let mut driver = lp_devices::udisp::Udisp::new(
                lp_devices::udisp::Options {
                    jpeg: text("jpeg", "4:4:4"),
                    jpeg_quality: int("jpeg_quality", 90),
                    fps: int("fps", 30),
                },
                model,
            );
            driver.open_on(io).map_err(|e| e.0)?;
            Ok(Box::new(driver))
        }
        "beada" => {
            let mut driver = lp_devices::beada::Beada::new(model, int("brightness", 100));
            driver.settle_s = 0.0;
            driver.open_on(io).map_err(|e| e.0)?;
            Ok(Box::new(driver))
        }
        "thermaltake" => {
            let mut driver = lp_devices::thermaltake::Thermaltake::new(model);
            driver.clock = || 1_700_000_000.123;
            driver.pause_s = 0.0;
            driver.open_on(io).map_err(|e| e.0)?;
            Ok(Box::new(driver))
        }
        "lianli" => {
            let mut driver = lp_devices::lianli::LianLi::new(model);
            driver.stamp = || 12_345_678;
            driver.open_on(io).map_err(|e| e.0)?;
            Ok(Box::new(driver))
        }
        "thermalright" => {
            let mut driver = lp_devices::thermalright::Trofeo::new(model);
            let product = case["product_id"].as_u64().unwrap_or(0) as u16;
            driver.open_on(io, product).map_err(|e| e.0)?;
            Ok(Box::new(driver))
        }
        other => Err(format!("no driver {other:?} here")),
    }
}

pub fn session(case: &Value) -> Result<Value, String> {
    let events: Events = Arc::default();
    let io = ScriptedUsb {
        product: case["product"].as_str().unwrap_or("").to_string(),
        interfaces: interfaces_of(&case["interfaces"]),
        answers: case["answers"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .into(),
        fail_writes: case["fail_writes"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|f| {
                let errno = f[1].as_i64().unwrap_or(0);
                let text = f[2].as_str().unwrap_or("");
                (
                    f[0].as_u64().unwrap_or(0),
                    format!("[Errno {errno}] {text}"),
                )
            })
            .collect(),
        writes: 0,
        events: Arc::clone(&events),
    };
    let seen = |events: &Events| Value::Array(events.lock().unwrap().clone());
    let mut driver = match opened(case, Box::new(io)) {
        Ok(driver) => driver,
        Err(why) => return Ok(json!({"error": why, "events": seen(&events)})),
    };
    let mut failed = None;
    for step in case["steps"].as_array().cloned().unwrap_or_default() {
        let done = if let Some(percent) = step["brightness"].as_i64() {
            driver.set_brightness(percent)
        } else if let Some(layout) = step["jpeg"].as_str() {
            driver.set_jpeg(layout)
        } else {
            let region = step["region"].as_array().map(|r| {
                let n = |i: usize| r[i].as_i64().unwrap_or(0);
                (n(0), n(1), n(2), n(3))
            });
            driver.show(&image_of(&step["show"])?, region)
        };
        if let Err(e) = done {
            failed = Some(e.0);
            break;
        }
    }
    let counters = driver.counters();
    let told = json!({
        "picture": driver.picture(),
        "jpeg_layout": driver.jpeg_layout(),
        "describe": driver.describe(),
        "streaming": driver.streaming(),
        "slow": driver.slow(),
        "model": driver.model().map(|m| m.id),
    });
    driver.close();
    Ok(json!({
        "events": seen(&events),
        "error": failed,
        "bytes": counters.bytes,
        "usb_errors": counters.usb_errors,
        "told": told,
    }))
}
