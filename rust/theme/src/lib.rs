//! lp-theme: Libre Panel's themes as theme/ has them (docs/RUST.md, R3): theme.json read as
//! Python reads it, checked and completed as theme/model.py checks and completes it, with its
//! messages and warnings in its words.

pub mod model;
pub mod paths;
pub mod specs;
pub mod tables;

pub use model::{load_theme, normalize_widget, parse_theme, Env, Error, Kind, Theme};
