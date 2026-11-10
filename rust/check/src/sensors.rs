//! lp-check's cases for Python's regular expressions as lp-py has them and for the readings of
//! the sensor sources (tests/test_rust_py.py, tests/test_rust_sensors.py).

use serde_json::{json, Value};

fn spans(found: Option<lp_py::regex::Found>) -> Value {
    match found {
        None => Value::Null,
        Some(found) => json!(found
            .spans
            .iter()
            .map(|span| span.map(|(a, b)| json!([a, b])).unwrap_or(Value::Null))
            .collect::<Vec<_>>()),
    }
}

/// regex: each [pattern, ignore case, text] searched, matched, matched whole, replaced by
/// "<>" and split, or the pattern's error.
fn regex(case: &Value) -> Value {
    let cases = case["cases"].as_array().cloned().unwrap_or_default();
    let mut last: Option<(
        String,
        bool,
        Result<lp_py::regex::Regex, lp_py::regex::Error>,
    )> = None;
    let mut out = Vec::with_capacity(cases.len());
    for one in &cases {
        let (pattern, ignore, text) = (
            one[0].as_str().unwrap_or(""),
            one[1].as_bool().unwrap_or(false),
            one[2].as_str().unwrap_or(""),
        );
        if !matches!(&last, Some((p, i, _)) if p == pattern && *i == ignore) {
            last = Some((
                pattern.to_string(),
                ignore,
                lp_py::regex::Regex::new(pattern, ignore),
            ));
        }
        let Some((_, _, compiled)) = &last else {
            continue;
        };
        out.push(match compiled {
            Err(e) => json!({"error": e.0}),
            Ok(re) => {
                let chars: Vec<char> = text.chars().collect();
                json!({
                    "search": spans(re.search(&chars)),
                    "match": spans(re.matches(&chars)),
                    "full": re.fullmatch(&chars).is_some(),
                    "sub": re.sub("<>", text, 0),
                    "split": re.split(text, 0),
                })
            }
        });
    }
    json!(out)
}

fn float(value: f64) -> Value {
    json!(format!("{:016x}", value.to_bits()))
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .map(|item| item.as_str().unwrap_or("").to_string())
        .collect()
}

/// hwnames: each name tidied as a processor's, a graphics card's and a memory maker's; the
/// memory a computer has of each count of visible bytes; each size as people say it; each
/// memory's name.
fn hwnames(case: &Value) -> Value {
    use lp_sensors::hwnames::{
        installed_gb, memory_name, size_label, tidy_cpu, tidy_gpu, tidy_maker,
    };
    let names = strings(&case["names"]);
    let floats = |key: &str| -> Vec<f64> {
        case[key]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_f64)
            .collect()
    };
    json!({
        "cpu": names.iter().map(|n| tidy_cpu(n)).collect::<Vec<_>>(),
        "gpu": names.iter().map(|n| tidy_gpu(n)).collect::<Vec<_>>(),
        "maker": names.iter().map(|n| tidy_maker(n)).collect::<Vec<_>>(),
        "installed": case["visible"].as_array().into_iter().flatten()
            .filter_map(Value::as_u64).map(installed_gb).collect::<Vec<_>>(),
        "sizes": floats("sizes").into_iter().map(size_label).collect::<Vec<_>>(),
        "memory": case["memory"].as_array().into_iter().flatten().map(|m| memory_name(
            m[0].as_str().unwrap_or(""), m[1].as_str().unwrap_or(""),
            m[2].as_i64().unwrap_or(0), m[3].as_f64().unwrap_or(0.0),
        )).collect::<Vec<_>>(),
    })
}

fn sensor_of(row: &Value) -> lp_sensors::lhm::LhmSensor {
    let text = |n: usize| row[n].as_str().unwrap_or("").to_string();
    lp_sensors::lhm::LhmSensor {
        sensor_id: text(0),
        hardware_id: text(1),
        hardware_name: text(2),
        sensor_type: text(3),
        label: text(4),
        value: row[5].as_f64().unwrap_or(0.0),
        unit: text(6),
    }
}

fn sensor_row(sensor: &lp_sensors::lhm::LhmSensor) -> Value {
    json!([
        sensor.sensor_id,
        sensor.hardware_id,
        sensor.hardware_name,
        sensor.sensor_type,
        sensor.label,
        float(sensor.value),
        sensor.unit,
    ])
}

fn readings_rows(readings: &lp_sensors::lhm::Readings) -> Value {
    json!(readings
        .iter()
        .map(|(key, r)| {
            let value = match &r.value {
                lp_py::Value::Float(f) => float(*f),
                other => json!(["str", other.str()]),
            };
            json!([key, value, r.unit, r.label, r.origin, r.device])
        })
        .collect::<Vec<_>>())
}

/// lhm_readings: the readings of each case's sensors (rows of seven) for its `gpu`, and the
/// sensors each key is made from.
fn lhm_readings(case: &Value) -> Value {
    let cases = case["cases"].as_array().cloned().unwrap_or_default();
    json!(cases
        .iter()
        .map(|one| {
            let sensors: Vec<_> = one["sensors"]
                .as_array()
                .into_iter()
                .flatten()
                .map(sensor_of)
                .collect();
            let mut sources = lp_sensors::lhm::Sources::new();
            let readings = lp_sensors::lhm::readings_from_sensors(
                &sensors,
                one["gpu"].as_str(),
                Some(&mut sources),
            );
            json!({
                "readings": readings_rows(&readings),
                "sources": sources.iter().map(|(k, ids)| json!([k, ids])).collect::<Vec<_>>(),
            })
        })
        .collect::<Vec<_>>())
}

/// lhm_tree: the sensors of each web server's tree (a JSON text), and each text as a value
/// with its unit.
fn lhm_tree(case: &Value) -> Value {
    let trees: Vec<Value> = strings(&case["trees"])
        .iter()
        .map(|text| match lp_py::json::loads(text) {
            Ok(lp_py::Value::Dict(tree)) => json!(lp_sensors::lhm::sensors_from_tree(&tree)
                .iter()
                .map(sensor_row)
                .collect::<Vec<_>>()),
            _ => Value::Null,
        })
        .collect();
    let values: Vec<Value> = strings(&case["values"])
        .iter()
        .map(|text| match lp_sensors::lhm::parse_value(text) {
            Some((value, unit)) => json!([float(value), unit]),
            None => Value::Null,
        })
        .collect();
    json!({"trees": trees, "values": values})
}

fn failed(e: lp_sensors::protocol::Error) -> Value {
    match e {
        lp_sensors::protocol::Error::Protocol(why) => json!({"protocol": why}),
        lp_sensors::protocol::Error::Service(why) => json!({"service": why}),
    }
}

/// sensor_protocol: each body (base64) read as the answer to hello and to read; each request
/// for readings as its bytes; each header's length.
fn sensor_protocol(case: &Value) -> Value {
    use base64::engine::general_purpose::STANDARD;
    use base64::Engine;
    use lp_sensors::protocol as p;
    let bodies: Vec<Value> = strings(&case["bodies"])
        .iter()
        .map(|body| {
            let body = STANDARD.decode(body).unwrap_or_default();
            let message = match p::decode(&body) {
                Ok(message) => message,
                Err(e) => return failed(e),
            };
            let hello = match p::parse_hello(&message) {
                Ok(hello) => json!([hello.service, hello.protocol]),
                Err(e) => failed(e),
            };
            let round = |done: &Option<p::RoundTime>| match done {
                None => Value::Null,
                Some(done) => json!({
                    "full": done.full,
                    "parts": done.parts.iter().map(|part| json!([
                        part.hardware_id, part.name, part.kind, float(part.seconds)
                    ])).collect::<Vec<_>>(),
                    "seconds": float(done.seconds()),
                }),
            };
            let readings = match p::parse_readings(&message) {
                Ok(r) => json!({
                    "state": r.state, "error": r.error, "age": r.age.map(float),
                    "ring0": r.ring0, "pawnio": r.pawnio,
                    "sensors": r.sensors.iter().map(sensor_row).collect::<Vec<_>>(),
                    "timing": r.timing.as_ref().map(|t| json!({
                        "last": round(&t.last), "full": round(&t.full)
                    })),
                    "memory": r.memory.as_ref().map(|m| json!([
                        m.private, m.working_set, m.managed
                    ])),
                    "round_line": lp_sensors::service::round_line(r.timing.as_ref()),
                }),
                Err(e) => failed(e),
            };
            json!({"hello": hello, "readings": readings})
        })
        .collect();
    let requests: Vec<Value> = case["requests"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|request| {
            let message =
                p::read_request(&strings(&request[0]), request[1].as_bool() == Some(true));
            match p::encode(&message, p::MAX_REQUEST) {
                Ok(bytes) => json!(STANDARD.encode(bytes)),
                Err(e) => failed(e),
            }
        })
        .collect();
    let hello = p::encode(&p::hello_request(), p::MAX_REQUEST).unwrap_or_default();
    let lengths: Vec<Value> = strings(&case["headers"])
        .iter()
        .map(|header| {
            let header = STANDARD.decode(header).unwrap_or_default();
            match p::body_length(&header, p::MAX_ANSWER) {
                Ok(length) => json!(length),
                Err(e) => failed(e),
            }
        })
        .collect();
    json!({"bodies": bodies, "requests": requests, "hello": STANDARD.encode(hello),
           "lengths": lengths})
}

/// lhm_hints: for each case (the keys read, the providers, the status or none, windows,
/// ready, unlock) the steps to the CPU's sensors, the command line's hint and the person's
/// sentence, in the language `locale` names.
fn lhm_hints(case: &Value) -> Result<Value, String> {
    use lp_sensors::hints::{hardware_hint, ring0_steps, user_hint};
    if let Some(code) = case["locale"].as_str() {
        let folder = std::path::PathBuf::from(case["locale_dir"].as_str().unwrap_or(""));
        lp_i18n::set_language(lp_i18n::Language::load(&folder, code)?);
    }
    let cases = case["cases"].as_array().cloned().unwrap_or_default();
    let out: Vec<Value> = cases
        .iter()
        .map(|one| {
            let mut readings = lp_sensors::lhm::Readings::new();
            for key in strings(&one["keys"]) {
                readings.insert(key, lp_render::Reading::of(lp_py::Value::Float(1.0)));
            }
            let said = &one["status"];
            let status = said.is_object().then(|| lp_sensors::service::LhmStatus {
                mode: said["mode"].as_str().unwrap_or("").into(),
                elevated: said["elevated"].as_bool(),
                ring0: said["ring0"].as_bool(),
                pawnio: said["pawnio"].as_str().map(str::to_string),
                error: said["error"].as_str().unwrap_or("").into(),
                service: said["service"].as_str().unwrap_or("").into(),
                ..lp_sensors::service::LhmStatus::default()
            });
            let (windows, unlock) = (
                one["windows"].as_bool() == Some(true),
                one["unlock"].as_bool() == Some(true),
            );
            json!({
                "steps": status.as_ref().map(|status| ring0_steps(status, unlock)),
                "hint": hardware_hint(&readings, &strings(&one["providers"]),
                    one["url"].as_str().unwrap_or(""), windows, status.as_ref(), unlock),
                "user": user_hint(&readings, windows, status.as_ref(),
                    one["ready"].as_bool() == Some(true), unlock),
            })
        })
        .collect();
    lp_i18n::set_language(lp_i18n::Language::english());
    Ok(json!(out))
}

fn named(value: &Value) -> Vec<(String, f64)> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .map(|pair| {
            (
                pair[0].as_str().unwrap_or("").to_string(),
                pair[1].as_f64().unwrap_or(0.0),
            )
        })
        .collect()
}

/// load_math: what the load source's numbers mean: the load between two samples of the
/// system's times, the card picked by its memory in use and its load of its engines, the
/// cores' loads over a run of samples, a core's busy and whole time; and the cores told by
/// Windows' records (base64), by a folder as sysfs has it, and checked.
fn load_math(case: &Value) -> Value {
    use base64::engine::general_purpose::STANDARD;
    use base64::Engine;
    use lp_sensors::{load, topology};
    let list = |key: &str| case[key].as_array().cloned().unwrap_or_default();
    let triple = |v: &Value| {
        v.as_array().map(|t| {
            let n = |i: usize| t[i].as_u64().unwrap_or(0);
            (n(0), n(1), n(2))
        })
    };
    let pairs = |v: &Value| -> Vec<(f64, f64)> {
        v.as_array()
            .into_iter()
            .flatten()
            .map(|p| (p[0].as_f64().unwrap_or(0.0), p[1].as_f64().unwrap_or(0.0)))
            .collect()
    };
    let cores = |found: Option<Vec<Vec<usize>>>| json!(found);
    json!({
        "cpu": list("cpu").iter().map(|c| load::cpu_load(triple(&c[0]), triple(&c[1])).map(float))
            .collect::<Vec<_>>(),
        "adapters": list("memory").iter().map(|m| load::pick_adapter(&named(m)))
            .collect::<Vec<_>>(),
        "loads": list("engines").iter()
            .map(|e| float(load::adapter_load(e[0].as_str().unwrap_or(""), &named(&e[1]))))
            .collect::<Vec<_>>(),
        "cores": list("runs").iter().map(|run| {
            let samples = run.as_array().cloned().unwrap_or_default();
            let mut cores = load::Cores::new(samples[0][0].as_f64().unwrap_or(0.0),
                pairs(&samples[0][1]));
            samples[1..].iter().map(|sample| {
                cores.read(sample[0].as_f64().unwrap_or(0.0), pairs(&sample[1]))
                    .map(|loads| loads.into_iter().map(float).collect::<Vec<_>>())
            }).collect::<Vec<_>>()
        }).collect::<Vec<_>>(),
        "busy": list("times").iter().map(|times| {
            let times: Vec<f64> = times.as_array().into_iter().flatten()
                .filter_map(Value::as_f64).collect();
            let (busy, total) = load::busy_of(&times);
            json!([float(busy), float(total)])
        }).collect::<Vec<_>>(),
        "records": strings(&case["records"]).iter().map(|raw| {
            cores(topology::parse_processor_cores(&STANDARD.decode(raw).unwrap_or_default()))
        }).collect::<Vec<_>>(),
        "sysfs": strings(&case["sysfs"]).iter()
            .map(|root| cores(topology::linux_cores(std::path::Path::new(root))))
            .collect::<Vec<_>>(),
        "checked": list("checked").iter().map(|c| {
            let given = c[0].as_array().map(|all| all.iter().map(|core| {
                core.as_array().into_iter().flatten()
                    .filter_map(|t| t.as_u64().map(|t| t as usize)).collect()
            }).collect());
            cores(topology::checked(given, c[1].as_u64().unwrap_or(0) as usize))
        }).collect::<Vec<_>>(),
    })
}

/// netlink: each speed as one says it, the default route's interface of each routing table,
/// a Wi-Fi's quality in each [text, name], and the rate in each of iw's answers.
fn netlink(case: &Value) -> Value {
    use lp_sensors::netlink as n;
    json!({
        "speeds": case["speeds"].as_array().into_iter().flatten()
            .map(|s| n::speed_text(s.as_f64().unwrap_or(0.0))).collect::<Vec<_>>(),
        "routes": strings(&case["routes"]).iter().map(|r| n::default_interface(r))
            .collect::<Vec<_>>(),
        "quality": case["wireless"].as_array().into_iter().flatten().map(|w| {
            match n::wireless_quality(w[0].as_str().unwrap_or(""), w[1].as_str().unwrap_or("")) {
                Ok(found) => json!(found.map(float)),
                Err(why) => json!({"error": why}),
            }
        }).collect::<Vec<_>>(),
        "rates": strings(&case["iw"]).iter().map(|out| n::iw_rate(out).map(float))
            .collect::<Vec<_>>(),
    })
}

/// audio: each text as wpctl's and pactl's answers are read, and the readings of each
/// [level, muted, microphone].
fn audio(case: &Value) -> Value {
    use lp_sensors::audio as a;
    let texts = strings(&case["texts"]);
    json!({
        "wpctl": texts.iter().map(|t| a::parse_wpctl(t).map(|(l, m)| json!([float(l), m])))
            .collect::<Vec<_>>(),
        "volume": texts.iter().map(|t| a::parse_pactl_volume(t).map(float)).collect::<Vec<_>>(),
        "mute": texts.iter().map(|t| a::parse_pactl_mute(t)).collect::<Vec<_>>(),
        "readings": case["states"].as_array().into_iter().flatten().map(|s| {
            let found = a::readings_of(s[0].as_f64().unwrap_or(0.0),
                s[1].as_bool() == Some(true), s[2].as_bool() == Some(true));
            found.iter().map(|(key, r)| {
                let value = match &r.value {
                    lp_py::Value::Float(f) => float(*f),
                    other => json!(["int", other.str()]),
                };
                json!([key, value, r.unit, r.label])
            }).collect::<Vec<_>>()
        }).collect::<Vec<_>>(),
    })
}

/// frames: each run of events played to a FrameCounter (["target", exe or null], ["add", app,
/// ms, at, waits or null], ["read", now]) with what each read gave; each list of frame times
/// as a round (its percentiles and figures); and each table of CSV lines as its columns and
/// frames.
fn frames(case: &Value) -> Value {
    use lp_sensors::frames as f;
    let list = |key: &str| case[key].as_array().cloned().unwrap_or_default();
    let runs: Vec<Value> = list("runs")
        .iter()
        .map(|run| {
            let mut counter = f::FrameCounter::new(run["every_frame"].as_bool() != Some(false));
            let mut reads = Vec::new();
            for event in run["events"].as_array().into_iter().flatten() {
                match event[0].as_str().unwrap_or("") {
                    "target" => counter.set_target(event[1].as_str()),
                    "add" => {
                        let waits = event[4].as_array().map(|w| {
                            let n = |i: usize| w[i].as_f64().unwrap_or(0.0);
                            [n(0), n(1), n(2), n(3)]
                        });
                        counter.add(
                            event[1].as_str().unwrap_or(""),
                            event[2].as_f64().unwrap_or(f64::NAN),
                            event[3].as_f64().unwrap_or(0.0),
                            waits,
                        );
                    }
                    _ => {
                        let found = counter.readings(event[1].as_f64().unwrap_or(0.0));
                        reads.push(json!(found
                            .iter()
                            .map(|(key, r)| {
                                let value = match &r.value {
                                    lp_py::Value::Float(v) => float(*v),
                                    other => json!(["str", other.str()]),
                                };
                                json!([key, value, r.unit, r.label])
                            })
                            .collect::<Vec<_>>()));
                    }
                }
            }
            json!(reads)
        })
        .collect();
    let rounds: Vec<Value> = list("rounds")
        .iter()
        .map(|times| {
            let mut round = f::FrameTimes::default();
            for ms in times
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_f64)
            {
                round.add(ms);
            }
            let percentiles = [1u64, 500, 990, 999, 1000].map(|p| round.percentile(p).map(float));
            json!({
                "percentiles": percentiles,
                "figures": round.figures(None).map(|all| all.map(float)),
            })
        })
        .collect();
    let tables: Vec<Value> = list("tables")
        .iter()
        .map(|lines| {
            let lines = strings(lines);
            let mut columns: Option<f::Columns> = None;
            let mut found = Vec::new();
            for line in &lines {
                let row = f::csv_row(line);
                match &columns {
                    None => {
                        columns = f::Columns::of(&row);
                        found.push(json!(columns
                            .as_ref()
                            .map(|c| json!([c.app, c.frame, c.waits]))));
                    }
                    Some(columns) => {
                        found.push(json!(columns.frame_of(&row).map(|(app, ms, waits)| {
                            json!([app, float(ms), waits.map(|w| w.map(float))])
                        })))
                    }
                }
            }
            json!(found)
        })
        .collect();
    let limits: Vec<Value> = list("limits")
        .iter()
        .map(|limit| {
            let waits: Vec<f::Waits> = limit[0]
                .as_array()
                .into_iter()
                .flatten()
                .map(|w| {
                    let n = |i: usize| w[i].as_f64().unwrap_or(0.0);
                    [n(0), n(1), n(2), n(3)]
                })
                .collect();
            json!(f::limited_by(&waits, limit[1].as_f64().unwrap_or(0.0)))
        })
        .collect();
    json!({"runs": runs, "rounds": rounds, "tables": tables, "limits": limits})
}

/// hardware: the processor's name of each cpuinfo file, the cards of each tree of folders,
/// the memory modules of each table (base64) and folder of entries, each made-up computer
/// described, and int() of texts.
fn hardware(case: &Value) -> Value {
    use base64::engine::general_purpose::STANDARD;
    use base64::Engine;
    use lp_sensors::hardware as hw;
    use lp_sensors::smbios;
    use std::path::{Path, PathBuf};
    let list = |key: &str| case[key].as_array().cloned().unwrap_or_default();
    let path = |value: &Value| PathBuf::from(value.as_str().unwrap_or(""));
    let module = |m: &smbios::Module| json!([m.megabytes, m.kind, m.speed, m.maker]);
    let cpus: Vec<Value> = list("cpuinfo")
        .iter()
        .map(|file| json!(hw::linux_cpu_name(&path(file))))
        .collect();
    let trees: Vec<Value> = list("trees")
        .iter()
        .map(|tree| {
            let ids: Vec<PathBuf> = tree["pci_ids"]
                .as_array()
                .into_iter()
                .flatten()
                .map(path)
                .collect();
            let found = hw::linux_adapters(
                &path(&tree["drm"]),
                &path(&tree["nvidia"]),
                &ids,
                &path(&tree["amdgpu_ids"]),
            );
            json!(found
                .iter()
                .map(|a| json!([a.name, a.memory, a.discrete, a.slot]))
                .collect::<Vec<_>>())
        })
        .collect();
    let tables: Vec<Value> = list("tables")
        .iter()
        .map(|table| {
            let data = STANDARD
                .decode(table.as_str().unwrap_or(""))
                .unwrap_or_default();
            json!(smbios::memory_modules(&data)
                .iter()
                .map(module)
                .collect::<Vec<_>>())
        })
        .collect();
    let entries: Vec<Value> = list("entries")
        .iter()
        .map(|folder| {
            let data = smbios::linux_table(Path::new(folder.as_str().unwrap_or("")));
            json!(smbios::memory_modules(&data)
                .iter()
                .map(module)
                .collect::<Vec<_>>())
        })
        .collect();
    let founds: Vec<Value> = list("founds")
        .iter()
        .map(|one| {
            let text = |value: &Value| value.as_str().unwrap_or("").to_string();
            let found = hw::Found {
                cpu: text(&one["cpu"]),
                adapters: one["adapters"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(|a| hw::Adapter {
                        name: text(&a[0]),
                        memory: a[1].as_u64().unwrap_or(0),
                        discrete: a[2].as_bool().unwrap_or(false),
                        slot: String::new(),
                    })
                    .collect(),
                modules: one["modules"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(|m| smbios::Module {
                        megabytes: m[0].as_u64().unwrap_or(0) as u32,
                        kind: text(&m[1]),
                        speed: m[2].as_u64().unwrap_or(0) as u16,
                        maker: text(&m[3]),
                    })
                    .collect(),
                memory: one["memory"].as_u64().unwrap_or(0),
            };
            let prefer = text(&one["prefer"]);
            let custom = [
                text(&one["custom"][0]),
                text(&one["custom"][1]),
                text(&one["custom"][2]),
            ];
            let picked = hw::pick_adapter(&found.adapters, &prefer)
                .and_then(|best| found.adapters.iter().position(|a| std::ptr::eq(a, best)));
            let source = hw::HardwareSource::of(
                &found,
                [custom[0].as_str(), custom[1].as_str(), custom[2].as_str()],
                &prefer,
            );
            let readings: Vec<Value> = source
                .read()
                .iter()
                .map(|(key, r)| {
                    let value = match &r.value {
                        lp_py::Value::Float(f) => json!(f),
                        other => json!(other.str()),
                    };
                    json!([key, value, r.unit, r.label])
                })
                .collect();
            json!({"names": hw::describe(&found, &prefer), "speed": hw::memory_speed(&found),
                   "picked": picked, "readings": readings})
        })
        .collect();
    let ints: Vec<Value> = list("ints")
        .iter()
        .map(|one| {
            let n = lp_sensors::sysfs::int_of(
                one[0].as_str().unwrap_or(""),
                one[1].as_u64().unwrap_or(10) as u32,
            );
            json!(n.map(|n| n.to_string()))
        })
        .collect();
    json!({"cpus": cpus, "trees": trees, "tables": tables, "entries": entries,
           "founds": founds, "ints": ints})
}

/// A stand-in for NVIDIA's library: the cards a case describes, each with the values it
/// gives (those it has not are refused, as a card without a fan refuses).
struct MadeUpNvml(Vec<Value>);

impl MadeUpNvml {
    fn number(&self, card: usize, key: &str) -> Option<u32> {
        self.0.get(card.wrapping_sub(1))?[key]
            .as_u64()
            .map(|n| n as u32)
    }
}

impl lp_sensors::nvml::Api for MadeUpNvml {
    fn init(&self) -> Option<i32> {
        Some(0)
    }
    fn shutdown(&self) {}
    fn count(&self) -> u32 {
        self.0.len() as u32
    }
    fn handle_by_slot(&self, slot: &str) -> Option<usize> {
        let found = self
            .0
            .iter()
            .position(|card| card["slot"].as_str() == Some(slot));
        found.map(|n| n + 1)
    }
    fn handle_by_index(&self, index: u32) -> Option<usize> {
        ((index as usize) < self.0.len()).then_some(index as usize + 1)
    }
    fn temperature(&self, card: usize) -> Option<u32> {
        self.number(card, "temp")
    }
    fn fan_speed(&self, card: usize) -> Option<u32> {
        self.number(card, "fan")
    }
    fn fan_rpm(&self, card: usize) -> Option<u32> {
        self.number(card, "rpm")
    }
    fn power(&self, card: usize) -> Option<u32> {
        self.number(card, "power")
    }
    fn utilization(&self, card: usize) -> Option<(u32, u32)> {
        Some((self.number(card, "busy")?, 0))
    }
    fn memory(&self, card: usize) -> Option<(u64, u64, u64)> {
        let memory = &self.0.get(card.wrapping_sub(1))?["memory"];
        Some((
            memory[0].as_u64()?,
            memory[1].as_u64()?,
            memory[2].as_u64()?,
        ))
    }
    fn clock(&self, card: usize, kind: i32) -> Option<u32> {
        self.number(card, if kind == 0 { "clock" } else { "memory_clock" })
    }
}

/// linux_source: each machine laid out in a folder read by the Linux source at each step's
/// time, after the step's files were written: the readings in their order, and at the end
/// what the source says of itself.
fn linux_source(case: &Value) -> Result<Value, String> {
    use lp_sensors::linux::LinuxSource;
    use lp_sensors::service::Wanted;
    let text = |value: &Value| value.as_str().unwrap_or("").to_string();
    let mut out = Vec::new();
    for machine in case["machines"].as_array().into_iter().flatten() {
        let wanted = Wanted::default();
        wanted.set(machine["wanted"].as_array().into_iter().flatten().map(text));
        let adapters: Vec<lp_sensors::hardware::Adapter> = machine["adapters"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|a| lp_sensors::hardware::Adapter {
                name: text(&a[0]),
                memory: a[1].as_u64().unwrap_or(0),
                discrete: a[2].as_bool().unwrap_or(false),
                slot: text(&a[3]),
            })
            .collect();
        let cards = machine["nvml"].as_array().cloned();
        let library = cards.map(|cards| lp_sensors::nvml::Nvml::of(Box::new(MadeUpNvml(cards))));
        let mut source = LinuxSource::at(
            std::path::Path::new(machine["root"].as_str().unwrap_or("")),
            machine["prefer"].as_str().unwrap_or(""),
            machine["nvidia"].as_str().unwrap_or("auto"),
            wanted,
            Box::new(move || library.clone()),
            Box::new(move || adapters.clone()),
        );
        let mut steps = Vec::new();
        for step in machine["steps"].as_array().into_iter().flatten() {
            for write in step["writes"].as_array().into_iter().flatten() {
                let file = std::path::PathBuf::from(text(&write[0]));
                if let Some(folder) = file.parent() {
                    std::fs::create_dir_all(folder).map_err(|e| e.to_string())?;
                }
                std::fs::write(&file, text(&write[1])).map_err(|e| e.to_string())?;
            }
            let readings = source.read_at(step["now"].as_f64().unwrap_or(0.0));
            steps.push(json!(readings
                .iter()
                .map(|(key, r)| {
                    let value = match &r.value {
                        lp_py::Value::Float(f) => json!(f),
                        other => json!(other.str()),
                    };
                    json!([key, value, r.unit, r.label, r.origin])
                })
                .collect::<Vec<_>>()));
        }
        let status = source.status();
        out.push(
            json!({"steps": steps, "status": [status.chips, status.temperatures,
            status.fans, status.graphics, status.nvml, status.cpu_power]}),
        );
    }
    Ok(json!({"machines": out}))
}

/// sky: the sun's and the moon's readings at each [time, latitude, longitude] (the place
/// null for none) in the computer's zone; null where the clock has no such time.
fn sky(case: &Value) -> Result<Value, String> {
    let zone = lp_py::zones::local().ok_or("no zone of the computer")?;
    let moments: Vec<Value> = case["moments"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|moment| {
            let place = match (moment[1].as_f64(), moment[2].as_f64()) {
                (Some(latitude), Some(longitude)) => Some((latitude, longitude)),
                _ => None,
            };
            let found =
                lp_sensors::sky::sky_readings(zone, moment[0].as_f64().unwrap_or(0.0), place);
            json!(found.map(|readings| readings
                .iter()
                .map(|(key, r)| {
                    let value = match &r.value {
                        lp_py::Value::Float(f) => json!(f),
                        lp_py::Value::Int(n) => json!(n.to_string().parse::<i64>().ok()),
                        lp_py::Value::None => Value::Null,
                        other => json!(other.str()),
                    };
                    json!([key, value, r.unit, r.label])
                })
                .collect::<Vec<_>>()))
        })
        .collect();
    Ok(json!({"moments": moments}))
}

/// mangohud: the folder each MangoHud.conf names (a home and an environment), the newest log
/// of each folder at a time, the program of each log's name, and the frame times read from
/// each log as its chunks (base64) are written.
fn mangohud(case: &Value) -> Result<Value, String> {
    use base64::engine::general_purpose::STANDARD;
    use base64::Engine;
    use lp_sensors::mangohud as mh;
    use std::io::Write;
    use std::path::Path;
    let list = |key: &str| case[key].as_array().cloned().unwrap_or_default();
    let text = |value: &Value| value.as_str().unwrap_or("").to_string();
    let configs: Vec<Value> = list("configs")
        .iter()
        .map(|one| {
            let env = |name: &str| one["env"][name].as_str().map(str::to_string);
            let folder = mh::config_folder(Path::new(&text(&one["home"])), &env);
            json!(folder.to_string_lossy())
        })
        .collect();
    let folders: Vec<Value> = list("folders")
        .iter()
        .map(|one| {
            let found = mh::newest_log(Path::new(&text(&one[0])), one[1].as_f64().unwrap_or(0.0));
            json!(found.map(|path| path.to_string_lossy().into_owned()))
        })
        .collect();
    let names: Vec<Value> = list("names")
        .iter()
        .map(|name| json!(mh::app_of(Path::new(&text(name)))))
        .collect();
    let mut logs = Vec::new();
    for one in list("logs") {
        let path = text(&one["path"]);
        std::fs::write(&path, b"").map_err(|e| e.to_string())?;
        let mut reader = mh::LogReader::new(Path::new(&path));
        let mut rounds = Vec::new();
        for chunk in one["chunks"].as_array().into_iter().flatten() {
            let data = STANDARD
                .decode(chunk.as_str().unwrap_or(""))
                .unwrap_or_default();
            let mut file = std::fs::OpenOptions::new()
                .append(true)
                .open(&path)
                .map_err(|e| e.to_string())?;
            file.write_all(&data).map_err(|e| e.to_string())?;
            // (a number JSON has not, as Python's str names it)
            let frames = reader.frames().into_iter().map(|ms| match ms.is_finite() {
                true => json!(ms),
                false => json!(lp_py::float_repr(ms)),
            });
            rounds.push(json!(frames.collect::<Vec<_>>()));
        }
        logs.push(json!({"app": reader.app, "rounds": rounds}));
    }
    Ok(json!({"configs": configs, "folders": folders, "names": names, "logs": logs}))
}

fn track_json(track: Option<lp_sensors::media::Track>) -> Value {
    use lp_sensors::media::Cover;
    match track {
        None => Value::Null,
        Some(t) => json!([
            t.state,
            t.title,
            t.artist,
            t.album,
            t.position,
            t.duration,
            match t.cover {
                Some(Cover::Place(place)) => json!(place),
                _ => Value::Null,
            },
            t.source,
            t.at,
            t.app_id
        ]),
    }
}

/// media: each track's readings at a time, each player's name, what playerctl's and busctl's
/// answers say plays, and each cover (a file or web address, or base64 of the picture) as
/// the picture the source makes of it: its size and its bytes' SHA-256, null for none.
fn media(case: &Value) -> Value {
    use base64::engine::general_purpose::STANDARD;
    use base64::Engine;
    use lp_sensors::media as m;
    use sha2::{Digest, Sha256};
    let list = |key: &str| case[key].as_array().cloned().unwrap_or_default();
    let text = |value: &Value| value.as_str().unwrap_or("").to_string();
    let readings: Vec<Value> = list("tracks")
        .iter()
        .map(|one| {
            let t = &one[0];
            let track = m::Track {
                state: text(&t[0]),
                title: text(&t[1]),
                artist: text(&t[2]),
                album: text(&t[3]),
                position: t[4].as_f64(),
                duration: t[5].as_f64(),
                cover: None,
                source: text(&t[6]),
                at: t[7].as_f64().unwrap_or(0.0),
                app_id: String::new(),
            };
            let found = track.readings(one[1].as_f64().unwrap_or(0.0));
            json!(found
                .iter()
                .map(|(key, r)| {
                    let value = match &r.value {
                        lp_py::Value::Float(f) => json!(f),
                        lp_py::Value::Int(n) => json!(n.to_string().parse::<i64>().ok()),
                        other => json!(other.str()),
                    };
                    json!([key, value, r.unit, r.label])
                })
                .collect::<Vec<_>>())
        })
        .collect();
    let names: Vec<Value> = list("names")
        .iter()
        .map(|n| json!(m::app_name(&text(n))))
        .collect();
    let playerctl: Vec<Value> = list("playerctl")
        .iter()
        .map(|one| track_json(m::parse_playerctl(&text(one), 12.5)))
        .collect();
    let mpris: Vec<Value> = list("mpris")
        .iter()
        .map(|one| {
            let reply = lp_py::json::loads(&text(&one[0])).ok();
            track_json(reply.and_then(|reply| m::parse_mpris(&reply, &text(&one[1]), 12.5)))
        })
        .collect();
    let covers: Vec<Value> = list("covers")
        .iter()
        .map(|one| {
            let cover = match one["bytes"].as_str() {
                Some(data) => m::Cover::Picture(std::sync::Arc::new(
                    STANDARD.decode(data).unwrap_or_default(),
                )),
                None => m::Cover::Place(text(&one["place"])),
            };
            match m::load_cover(&cover) {
                None => Value::Null,
                Some(picture) => {
                    let hash = Sha256::digest(picture.as_bytes());
                    let hex: String = hash.iter().map(|byte| format!("{byte:02x}")).collect();
                    json!([picture.width(), picture.height(), hex])
                }
            }
        })
        .collect();
    json!({"readings": readings, "names": names, "playerctl": playerctl, "mpris": mpris,
           "covers": covers})
}

/// A picture as its size and its bytes' SHA-256, null for none.
fn picture_json(picture: Option<lp_pixels::Image>) -> Value {
    use sha2::{Digest, Sha256};
    match picture {
        None => Value::Null,
        Some(picture) => {
            let hash = Sha256::digest(picture.as_bytes());
            let hex: String = hash.iter().map(|byte| format!("{byte:02x}")).collect();
            json!([picture.width(), picture.height(), hex])
        }
    }
}

/// appicon: the program names of each id, each logo's rank, the logo each manifest names,
/// the file behind each logo, each packaged app's logo, each file as a badge, and on Linux
/// the folders, each player's desktop entry, its icon's name, the icon's file and the icon.
fn appicon(case: &Value) -> Value {
    use lp_sensors::appicon as a;
    use std::path::{Path, PathBuf};
    let list = |key: &str| case[key].as_array().cloned().unwrap_or_default();
    let text = |value: &Value| value.as_str().unwrap_or("").to_string();
    let paths = |value: &Value| -> Vec<PathBuf> {
        value
            .as_array()
            .into_iter()
            .flatten()
            .map(|p| PathBuf::from(text(p)))
            .collect()
    };
    let shown = |path: Option<PathBuf>| json!(path.map(|p| p.to_string_lossy().into_owned()));
    let names: Vec<Value> = list("ids")
        .iter()
        .map(|id| json!(a::program_names(&text(id))))
        .collect();
    let ranks: Vec<Value> = list("ranks")
        .iter()
        .map(|one| {
            let (plain, near) = a::logo_rank(&text(&one[0]), one[1].as_i64().unwrap_or(44) as i128);
            json!([i32::from(plain), near.to_string()])
        })
        .collect();
    let manifests: Vec<Value> = list("manifests")
        .iter()
        .map(|one| json!(a::manifest_logo(Path::new(&text(&one[0])), &text(&one[1]))))
        .collect();
    let logos: Vec<Value> = list("logos")
        .iter()
        .map(|one| shown(a::logo_file(Path::new(&text(one)))))
        .collect();
    let packages: Vec<Value> = list("packages")
        .iter()
        .map(|one| picture_json(a::package_logo(&text(&one[0]), paths(&one[1]))))
        .collect();
    let files: Vec<Value> = list("files")
        .iter()
        .map(|one| picture_json(a::load_file(Path::new(&text(one)))))
        .collect();
    let dirs: Vec<Value> = list("dirs")
        .iter()
        .map(|one| {
            let env = |name: &str| one["env"][name].as_str().map(str::to_string);
            let found = a::data_dirs(&env, Path::new(&text(&one["home"])));
            json!(found
                .iter()
                .map(|p| p.to_string_lossy().into_owned())
                .collect::<Vec<_>>())
        })
        .collect();
    let players: Vec<Value> = list("players")
        .iter()
        .map(|one| {
            let dirs = paths(&one[1]);
            let entry = a::desktop_entry(&text(&one[0]), &dirs);
            let name = entry.as_ref().and_then(|entry| a::desktop_icon_name(entry));
            let file = name.as_ref().and_then(|name| a::icon_file(name, &dirs));
            json!([
                shown(entry),
                name,
                shown(file),
                picture_json(a::linux_icon(&text(&one[0]), &dirs))
            ])
        })
        .collect();
    json!({"names": names, "ranks": ranks, "manifests": manifests, "logos": logos,
           "packages": packages, "files": files, "dirs": dirs, "players": players})
}

pub fn run(op: &str, case: &Value) -> Result<Value, String> {
    Ok(match op {
        "regex" => regex(case),
        "hwnames" => hwnames(case),
        "lhm_readings" => lhm_readings(case),
        "lhm_tree" => lhm_tree(case),
        "sensor_protocol" => sensor_protocol(case),
        "lhm_hints" => lhm_hints(case)?,
        "load_math" => load_math(case),
        "netlink" => netlink(case),
        "audio" => audio(case),
        "game_frames" => frames(case),
        "hardware" => hardware(case),
        "sky" => sky(case)?,
        "mangohud" => mangohud(case)?,
        "media" => media(case),
        "appicon" => appicon(case),
        "linux_source" => linux_source(case)?,
        _ => return Err(format!("no such op: {op}")),
    })
}
