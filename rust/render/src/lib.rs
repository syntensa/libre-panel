//! Libre Panel's renderer (render/ of the Python app, docs/RUST.md R4): the frames of a theme,
//! widget by widget, to the bit; so far the texts readings become, the ground and the cards.

mod agent;
mod bar;
mod calendar;
pub mod compose;
pub mod countdown;
pub mod cover;
pub mod effects;
pub mod facets;
mod finish;
pub mod formatting;
mod gauge;
pub mod icons;
mod image;
mod lit;
mod metric;
pub mod piece;
mod rect;
pub mod renderer;
pub mod shapes;
pub mod snapshot;
mod sunpath;
mod text;
pub mod widget;

pub use piece::{Failed, Piece};
pub use renderer::{Options, Renderer, Unported};
pub use snapshot::{Moment, Reading, Snapshot};
