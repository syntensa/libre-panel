//! Libre Panel's renderer (render/ of the Python app, docs/RUST.md R4): the frames of a theme,
//! widget by widget, to the bit; so far the texts readings become, the ground and the cards.

pub mod compose;
pub mod cover;
pub mod effects;
pub mod formatting;
pub mod piece;
mod rect;
pub mod renderer;
pub mod shapes;
pub mod snapshot;
mod text;
pub mod widget;

pub use piece::{Failed, Piece};
pub use renderer::{Options, Renderer, Unported};
pub use snapshot::{Moment, Reading, Snapshot};
