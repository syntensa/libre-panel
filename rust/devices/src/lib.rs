//! lp-devices: the panels Libre Panel drives (docs/RUST.md, R6): their catalog, as
//! devices/models.py has it (what the theme format checks a theme's display against, R3), and
//! their drivers, the first the Hongtai family's and the MAJESTY's (hongtai), which comes in
//! Rust alone; the serial port they are on (serial); and lp-panel, to try a panel with.

pub mod base;
pub mod hongtai;
pub mod link;
pub mod models;
pub mod serial;
mod table;
