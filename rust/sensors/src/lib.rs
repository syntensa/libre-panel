//! Libre Panel's sensor sources (src/libre_panel/sensors): LibreHardwareMonitor's sensors as
//! readings, read through Libre Panel's sensor service on Windows, and the names of the
//! hardware tidied for a panel.

pub mod audio;
pub mod frames;
pub mod hints;
pub mod hwnames;
pub mod lhm;
pub mod load;
pub mod netlink;
pub mod presentmon;
pub mod protocol;
pub mod provider;
pub mod service;
pub mod topology;
#[cfg(windows)]
pub mod winlink;
