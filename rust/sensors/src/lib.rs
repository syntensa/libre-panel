//! Libre Panel's sensor sources (src/libre_panel/sensors): LibreHardwareMonitor's sensors as
//! readings, read through Libre Panel's sensor service on Windows, and the names of the
//! hardware tidied for a panel.

pub mod audio;
pub mod frames;
pub mod gpus;
pub mod hardware;
pub mod hints;
pub mod hwmon;
pub mod hwnames;
pub mod lhm;
pub mod linux;
pub mod load;
pub mod mangohud;
pub mod netlink;
pub mod nvml;
pub mod presentmon;
pub mod protocol;
pub mod provider;
pub mod rapl;
pub mod service;
pub mod sky;
pub mod smbios;
pub mod sysfs;
pub mod topology;
#[cfg(windows)]
pub mod winlink;
