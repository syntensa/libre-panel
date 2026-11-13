//! A theme's look at runtime (render/look.py, theme `"look"`): SPUR II's grid. `look.grid`
//! lays thin lines over the background, every `spacing` px and every `major`-th one stronger,
//! in the palette's colours: under every widget, but over the backdrops (full-panel rects at
//! the bottom of the list), as SPUR II's grid lies on its gradient. It is drawn once, like a
//! background picture.

use std::rc::Rc;

use lp_pixels::{Image, Mode};
use lp_py::{Dict, Value};

use crate::piece::{Piece, Result};
use crate::renderer::Renderer;
use crate::shapes::Rgba;
use crate::widget;

/// The id the grid's piece goes by among the widgets' (ids are never empty, nor start so).
pub(crate) const GRID: &str = "\0grid";

/// grid_image: lines every `spacing` px from the top left, every `major`-th one (0: none) in
/// `strong`, the others in `minor`, on a clear picture `w` x `h`; the rows over the columns.
pub(crate) fn grid_image(
    (w, h): (usize, usize),
    spacing: i64,
    major: i64,
    minor: [u8; 4],
    strong: [u8; 4],
) -> Image {
    let spacing = spacing.max(1) as usize;
    let every = spacing.saturating_mul(major.max(0) as usize);
    let colour = |at: usize| match major > 0 && at.is_multiple_of(every) {
        true => strong,
        false => minor,
    };
    let mut out = vec![0u8; w * h * 4];
    for x in (0..w).step_by(spacing) {
        let c = colour(x);
        for y in 0..h {
            let at = (y * w + x) * 4;
            out[at..at + 4].copy_from_slice(&c);
        }
    }
    for y in (0..h).step_by(spacing) {
        let c = colour(y);
        for pixel in out[y * w * 4..(y + 1) * w * 4].chunks_exact_mut(4) {
            pixel.copy_from_slice(&c);
        }
    }
    Image::from_bytes(Mode::RGBA, w, h, out).expect("a picture of its own size")
}

impl Renderer {
    /// Whether the theme has the grid (theme.look.get("grid")).
    pub(crate) fn has_grid(&self) -> bool {
        widget::truthy(&self.theme.look, "grid")
    }

    /// grid_colours: the grid's colours (minor, major): as set, else the palette's `grid` /
    /// `grid_major`, else a breath away from the background (the strong lines twice as far);
    /// their alpha times the grid's.
    fn grid_colours(&self, settings: &Dict) -> Result<([u8; 4], [u8; 4])> {
        let ground =
            (self.color(Some(&self.theme.background_color), 1.0)?).unwrap_or([0, 0, 0, 255]);
        let text = crate::light::role_colour(&self.theme.palette, "text");
        let given = |key: &str| match settings.get(key) {
            Some(Value::Str(colour)) if !colour.is_empty() => self.color(Some(colour), 1.0),
            _ => Ok(None),
        };
        let named = |names: [&str; 2]| match names
            .into_iter()
            .find(|n| self.theme.palette.contains_key(*n))
        {
            Some(name) => self.color(Some(&format!("@{name}")), 1.0),
            None => Ok(None),
        };
        let minor = match given("color")? {
            Some(colour) => Some(colour),
            None => named(["grid", "grid_min"])?,
        };
        let minor: Rgba = minor.unwrap_or_else(|| {
            let m = [0, 1, 2].map(|i| {
                let (g, t) = (ground[i], text[i]);
                lp_py::round(g as f64 + (t - g) as f64 * 0.05) as i64
            });
            [m[0], m[1], m[2], 255]
        });
        let strong = match given("major_color")? {
            Some(colour) => Some(colour),
            None => named(["grid_major", "grid_maj"])?,
        };
        let strong: Rgba = strong.unwrap_or_else(|| {
            let twice = [0, 1, 2].map(|i| (2 * minor[i] - ground[i]).clamp(0, 255));
            [twice[0], twice[1], twice[2], minor[3]]
        });
        let alpha = widget::number(settings, "alpha", 1.0);
        let faded = |c: Rgba| {
            let a = lp_py::round(c[3] as f64 * alpha) as i64;
            [c[0], c[1], c[2], a].map(|v| v.clamp(0, 255) as u8)
        };
        Ok((faded(minor), faded(strong)))
    }

    /// _grid_piece: the grid over the whole panel, made once.
    fn grid_piece(&mut self) -> Result<Rc<Piece>> {
        if let Some(made) = &self.grid {
            return Ok(Rc::clone(made));
        }
        let settings = match self.theme.look.get("grid") {
            Some(Value::Dict(settings)) => settings.clone(),
            _ => Dict::default(),
        };
        let (minor, strong) = self.grid_colours(&settings)?;
        let size = (
            self.theme.width.max(0) as usize,
            self.theme.height.max(0) as usize,
        );
        let image = grid_image(
            size,
            widget::int(&settings, "spacing", 8),
            widget::int(&settings, "major", 5),
            minor,
            strong,
        );
        let made = Rc::new(Piece::new(image, 0, 0));
        self.grid = Some(Rc::clone(&made));
        Ok(made)
    }

    /// Look.compose: the grid laid among `pieces` before they are composed, over the backdrops
    /// (the full-panel rects the widgets start with) and the screen.
    pub(crate) fn lay_grid(&mut self, pieces: &mut Vec<(String, Rc<Piece>)>) -> Result<()> {
        let (tw, th) = (self.theme.width, self.theme.height);
        let backdrops: Vec<String> = (self.widgets.iter())
            .take_while(|w| {
                widget::text(w, "type") == "rect"
                    && widget::int(w, "x", 0) <= 0
                    && widget::int(w, "y", 0) <= 0
                    && widget::int(w, "x", 0) + widget::int(w, "w", 0) >= tw
                    && widget::int(w, "y", 0) + widget::int(w, "h", 0) >= th
            })
            .map(|w| widget::text(w, "id").to_string())
            .collect();
        let at = (pieces.iter())
            .take_while(|(id, _)| id == "\0screen" || backdrops.contains(id))
            .count();
        let grid = self.grid_piece()?;
        pieces.insert(at, (GRID.to_string(), grid));
        Ok(())
    }
}
