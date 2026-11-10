//! Libre Panel's sensor sources (src/libre_panel/sensors): LibreHardwareMonitor's sensors as
//! readings, read through Libre Panel's sensor service on Windows, and the names of the
//! hardware tidied for a panel.

pub mod appicon;
pub mod audio;
pub mod calendar;
pub mod frames;
pub mod gpus;
pub mod hardware;
pub mod hints;
pub mod homeassistant;
pub mod hwmon;
pub mod hwnames;
pub mod jsonsource;
pub mod lhm;
pub mod linux;
pub mod load;
pub mod mangohud;
pub mod media;
#[cfg(windows)]
pub mod media_windows;
pub mod mqtt;
pub mod netlink;
pub mod nut;
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
pub mod weather;
#[cfg(windows)]
pub mod winlink;
