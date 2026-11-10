//! Libre Panel's services (src/libre_panel/services) and what they may do: the host between
//! them and the main loop (plugins/host.py).

pub mod agents;
pub mod agents_service;
pub mod agents_setup;
pub mod agents_usage;
pub mod autopilot;
pub mod downloads;
pub mod game;
pub mod host;
pub mod launchers;
pub mod machine;
pub mod manager;
pub mod notices;
pub mod paths;
pub mod power;
pub mod registry;
pub mod steam;
pub mod timer;
