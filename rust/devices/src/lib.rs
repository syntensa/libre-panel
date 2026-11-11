//! lp-devices: the panels Libre Panel drives (docs/RUST.md, R6): their catalog, as
//! devices/models.py has it (what the theme format checks a theme's display against, R3), and
//! their drivers: the Hongtai family's and the MAJESTY's (hongtai), which comes in Rust
//! alone, on its serial port (serial); the TURZX USB panels (turzx: their video, with its
//! bit rate (rate), and their single pictures), found on the bus (usb) and driven through
//! Libre Panel's engine (engine); PNG files (png); and lp-panel, to try a panel with.

pub mod base;
pub mod beada;
pub mod changes;
pub mod driver;
pub mod engine;
pub mod h264;
pub mod hongtai;
pub mod keep;
pub mod lianli;
pub mod link;
pub mod models;
pub mod png;
pub mod ports;
pub mod rate;
pub mod rights;
pub mod serial;
mod table;
pub mod thermalright;
pub mod thermaltake;
pub mod turzx;
pub mod turzx_packet;
pub mod udisp;
pub mod usb;
pub mod usb_link;
pub mod waveshare;
