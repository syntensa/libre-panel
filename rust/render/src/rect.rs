//! Rects (render/renderer.py's _rect_piece and _card): a card drawn at its size with only its
//! round corners drawn three times larger (the pixels of drawing it all so, at a fraction of
//! the cost), anything else (a pill, a circle, an outline that fills it) drawn three times
//! larger and reduced; a colour or a gradient, an outline, a frosted backdrop.

use lp_pixels::{chops, Draw, Image, Mode, ALL_CORNERS};
use lp_py::Dict;

use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Renderer, MOST_PIXELS};
use crate::shapes::{card_corners, down, gradient, gradient_down, ink, Rgba, SUPERSAMPLE};
use crate::widget;

/// The theme's card colour: a rect filled with it is a card, as see-through as asked.
pub const SURFACE: &str = "@surface";

/// Python's floor division by 2.
fn half(n: i64) -> i64 {
    n.div_euclid(2)
}

impl Renderer {
    /// _rect_piece: a rect as it is drawn.
    pub(crate) fn rect_piece(&self, w: &Dict) -> Result<Piece> {
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (width, height) = (widget::int(w, "w", 100), widget::int(w, "h", 50));
        if let Some(card) = self.card(w, x, y, width, height)? {
            return Ok(card);
        }
        let s = SUPERSAMPLE as i64;
        let (lw, lh) = (
            (width.saturating_mul(s)).max(1),
            (height.saturating_mul(s)).max(1),
        );
        if lw.saturating_mul(lh) > MOST_PIXELS {
            return Err(Failed("a layer too large".into()));
        }
        let mut layer = Image::blank(Mode::RGBA, lw as usize, lh as usize);
        let radius = widget::int(w, "radius", 0)
            .min(half(width))
            .min(half(height))
            * s;
        let bounds = (0.0, 0.0, (width * s - 1) as f64, (height * s - 1) as f64);
        if widget::truthy(w, "color") {
            let a = self.colour_of(w, "color")?.expect("a colour");
            let b = self.colour_of(w, "color2")?;
            let across = widget::text(w, "gradient") == "horizontal";
            self.fill_shape(&mut layer, bounds, radius as f64, a, b, across)?;
        }
        if widget::truthy(w, "outline") {
            let line = self.colour_of(w, "outline")?.expect("a colour");
            let thick = widget::int(w, "outline_width", 1).saturating_mul(s);
            let thick = i32::try_from(thick).map_err(|_| Failed("width too large".into()))?;
            Draw::new(&mut layer).rounded_rectangle(
                bounds,
                radius as f64,
                None,
                Some(ink(line)),
                thick,
                ALL_CORNERS,
            )?;
        }
        let blur = widget::int(w, "backdrop_blur", 0);
        let backdrop = if blur != 0 {
            let mut mask = Image::new(Mode::L, layer.width(), layer.height(), &[0]);
            Draw::new(&mut mask).rounded_rectangle(
                bounds,
                radius as f64,
                Some([255, 0, 0, 0]),
                None,
                1,
                ALL_CORNERS,
            )?;
            Some(down(&mask, width, height, SUPERSAMPLE)?)
        } else {
            None
        };
        let mut piece = Piece::new(down(&layer, width, height, SUPERSAMPLE)?, x, y);
        piece.hit = Some([x, y, width, height]);
        piece.backdrop = backdrop;
        piece.backdrop_radius = blur as f32;
        Ok(piece)
    }

    /// _fill_shape: a rounded rectangle filled with a colour, or a gradient across the layer.
    pub(crate) fn fill_shape(
        &self,
        layer: &mut Image,
        bounds: (f64, f64, f64, f64),
        radius: f64,
        a: Rgba,
        b: Option<Rgba>,
        horizontal: bool,
    ) -> Result<()> {
        let Some(b) = b else {
            Draw::new(layer).rounded_rectangle(
                bounds,
                radius,
                Some(ink(a)),
                None,
                1,
                ALL_CORNERS,
            )?;
            return Ok(());
        };
        let mut mask = Image::new(Mode::L, layer.width(), layer.height(), &[0]);
        Draw::new(&mut mask).rounded_rectangle(
            bounds,
            radius,
            Some([255, 0, 0, 0]),
            None,
            1,
            ALL_CORNERS,
        )?;
        let ramp = gradient(layer.size(), a, b, horizontal, self.dither)?;
        layer.paste(&ramp, 0, 0, Some(&mask))?;
        Ok(())
    }

    /// _card: a rect drawn at its size with only its round corners supersampled; None where the
    /// corners meet (a pill, a circle) or the outline fills it: those are drawn whole.
    fn card(&self, w: &Dict, x: i64, y: i64, width: i64, height: i64) -> Result<Option<Piece>> {
        let r = widget::int(w, "radius", 0)
            .min(half(width))
            .min(half(height));
        let ring = if widget::truthy(w, "outline") {
            widget::int(w, "outline_width", 1)
        } else {
            0
        };
        let least = width.min(height);
        if least < 2 * (r + 2) + 2 || 2 * ring >= least {
            return Ok(None);
        }
        if width.saturating_mul(height) > MOST_PIXELS {
            return Err(Failed("a layer too large".into()));
        }
        let (shape, corners, c) = card_corners(r, ring)?;
        let small = shape.width() as i64;
        let (uw, uh) = (width as usize, height as usize);
        let mut full = Image::new(Mode::L, uw, uh, &[255]);
        let mut edge = (ring != 0).then(|| Image::new(Mode::L, uw, uh, &[0]));
        if let Some(edge) = edge.as_mut() {
            let mut draw = Draw::new(edge);
            for band in [
                (0, 0, width - 1, ring - 1),
                (0, height - ring, width - 1, height - 1),
                (0, 0, ring - 1, height - 1),
                (width - ring, 0, width - 1, height - 1),
            ] {
                let band = (band.0 as f64, band.1 as f64, band.2 as f64, band.3 as f64);
                draw.rectangle(band, Some([255, 0, 0, 0]), None, 1)?;
            }
        }
        let from = [
            (0, 0),
            (small - c, 0),
            (0, small - c),
            (small - c, small - c),
        ];
        let to = [
            (0, 0),
            (width - c, 0),
            (0, height - c),
            (width - c, height - c),
        ];
        for ((sx, sy), (bx, by)) in from.into_iter().zip(to) {
            full.paste(&shape.crop(sx, sy, sx + c, sy + c), bx, by, None)?;
            if let (Some(edge), Some(corners)) = (edge.as_mut(), corners.as_ref()) {
                edge.paste(&corners.crop(sx, sy, sx + c, sy + c), bx, by, None)?;
            }
        }
        let mut layer = None;
        if widget::truthy(w, "color") {
            let mut a = self.colour_of(w, "color")?.expect("a colour");
            let b = self.colour_of(w, "color2")?;
            if widget::text(w, "color") == SURFACE {
                // (a card, as see-through as asked)
                let alpha = a[3] as f64 * self.card_opacity()?;
                if !alpha.is_finite() {
                    return Err(Failed("cannot convert float to integer".into()));
                }
                a[3] = shapes_round(alpha)?;
            }
            let mut made = match b {
                None => Image::new(Mode::RGBA, uw, uh, &ink(a)),
                Some(b) => {
                    let across = widget::text(w, "gradient") == "horizontal";
                    gradient_down((uw, uh), a, b, across, self.dither)?
                }
            };
            let inside = match &edge {
                None => full.clone(),
                Some(edge) => chops::subtract(&full, edge, 1.0, 0)?,
            };
            let alpha = made.getchannel(3)?;
            let solid = alpha.getextrema() == Some(vec![(255, 255)]);
            let cover = if solid {
                inside
            } else {
                chops::multiply(&alpha, &inside)?
            };
            made.putalpha(&cover)?;
            layer = Some(made);
        }
        if let Some(edge) = &edge {
            let colour = self.colour_of(w, "outline")?.expect("a colour");
            let mut line = Image::new(Mode::RGBA, uw, uh, &ink(colour));
            let cover = if colour[3] == 255 {
                edge.clone()
            } else {
                let level = Image::new(Mode::L, uw, uh, &[ink(colour)[3]]);
                chops::multiply(edge, &level)?
            };
            line.putalpha(&cover)?;
            layer = Some(match layer {
                None => line,
                Some(made) => {
                    let sum = chops::add(
                        &made.convert(Mode::RGBa)?,
                        &line.convert(Mode::RGBa)?,
                        1.0,
                        0,
                    )?;
                    sum.convert(Mode::RGBA)?
                }
            });
        }
        let layer = layer.unwrap_or_else(|| Image::blank(Mode::RGBA, uw, uh));
        let blur = widget::int(w, "backdrop_blur", 0);
        let mut piece = Piece::new(layer, x, y);
        piece.hit = Some([x, y, width, height]);
        if blur != 0 {
            piece.backdrop = Some(full);
            piece.backdrop_radius = blur as f32;
        }
        Ok(Some(piece))
    }
}

/// round() of a card's alpha: Python's round to a whole number, an int that must fit the C int
/// Pillow takes of it.
fn shapes_round(alpha: f64) -> Result<i64> {
    let n = lp_pixels::round_half_even(alpha);
    if !(i64::from(i32::MIN)..=i64::from(i32::MAX)).contains(&n) {
        return Err(Failed("signed integer is greater than maximum".into()));
    }
    Ok(n)
}
