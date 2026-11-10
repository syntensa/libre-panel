//! Libre Panel's services (src/libre_panel/services) and what they may do: the host between
//! them and the main loop (plugins/host.py).

pub mod agents;
pub mod agents_service;
pub mod agents_setup;
pub mod agents_usage;
pub mod autopilot;
pub mod cooling;
pub mod cooling_advice;
pub mod cooling_data;
pub mod cooling_text;
pub mod devices;
pub mod devices_windows;
pub mod downloads;
pub mod fancontrol;
pub mod game;
pub mod host;
pub mod launchers;
pub mod machine;
pub mod manager;
pub mod memory;
pub mod memory_store;
pub mod notices;
pub mod paths;
pub mod power;
pub mod registry;
pub mod round_report;
pub mod steam;
pub mod timer;
