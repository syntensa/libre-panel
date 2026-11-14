//! Built-in line icons (icons.py), drawn as vectors so they stay sharp at any size: on a grid of
//! 24 by 24, four times larger, then made the icon's size (Lanczos). The weather's is chosen by
//! the renderer from the WMO code (weather_icon_name), the network connection's from its kind
//! and signal (link_icon).

use lp_pixels::chops::subtract;
use lp_pixels::{Draw, Filter, Image, Mode, Result, ALL_CORNERS};
use lp_py::Value;

/// Drawn this much larger, then reduced.
const SS: i64 = 4;

type Ink = [u8; 4];
const FULL: Ink = [255, 0, 0, 0]; // (an L mask's ink)

/// What a mask is painted with: a shape, or what is erased from it.
type Paint<'a> = dyn Fn(&mut Draw<'_, Image>, &Pen) -> Result<()> + 'a;

/// _Pen: the picture an icon is drawn on, four times its size, its colour and stroke.
struct Pen {
    s: f64,
    img: Image,
    color: Ink,
    w: i32,
}

impl Pen {
    fn new(size: i64, color: Ink, stroke: f64) -> Pen {
        let side = (size * SS) as usize;
        Pen {
            s: (size * SS) as f64 / 24.0,
            img: Image::blank(Mode::RGBA, side, side),
            color,
            w: (lp_py::round(stroke * SS as f64 * size as f64 / 24.0) as i32).max(1),
        }
    }

    fn p(&self, x: f64, y: f64) -> (f64, f64) {
        (x * self.s, y * self.s)
    }

    fn bx(&self, x0: f64, y0: f64, x1: f64, y1: f64) -> (f64, f64, f64, f64) {
        let (a, b) = self.p(x0, y0);
        let (c, d) = self.p(x1, y1);
        (a, b, c, d)
    }

    fn cap(&mut self, x: f64, y: f64) -> Result<()> {
        let (cx, cy) = self.p(x, y);
        let r = f64::from(self.w) / 2.0;
        let colour = self.color;
        Draw::new(&mut self.img).ellipse((cx - r, cy - r, cx + r, cy + r), Some(colour), None, 1)
    }

    fn line(&mut self, points: &[(f64, f64)]) -> Result<()> {
        let xy: Vec<(f64, f64)> = points.iter().map(|&(x, y)| self.p(x, y)).collect();
        let (colour, w) = (self.color, self.w);
        Draw::new(&mut self.img).line(&xy, Some(colour), w, true)?;
        self.cap(points[0].0, points[0].1)?;
        let last = points[points.len() - 1];
        self.cap(last.0, last.1)
    }

    fn rect(&mut self, x0: f64, y0: f64, x1: f64, y1: f64, r: f64) -> Result<()> {
        let bounds = self.bx(x0, y0, x1, y1);
        let (colour, w, s) = (self.color, self.w, self.s);
        Draw::new(&mut self.img).rounded_rectangle(
            bounds,
            r * s,
            None,
            Some(colour),
            w,
            ALL_CORNERS,
        )
    }

    fn circle(&mut self, cx: f64, cy: f64, r: f64, fill: bool) -> Result<()> {
        let bounds = self.bx(cx - r, cy - r, cx + r, cy + r);
        let (colour, w) = (self.color, self.w);
        let mut draw = Draw::new(&mut self.img);
        if fill {
            draw.ellipse(bounds, Some(colour), None, 1)
        } else {
            draw.ellipse(bounds, None, Some(colour), w)
        }
    }

    fn arc(&mut self, cx: f64, cy: f64, r: f64, a0: f64, a1: f64) -> Result<()> {
        let bounds = self.bx(cx - r, cy - r, cx + r, cy + r);
        let (colour, w) = (self.color, self.w);
        Draw::new(&mut self.img).arc(bounds, a0, a1, Some(colour), w)?;
        for a in [a0, a1] {
            let (sin, cos) = a.to_radians().sin_cos();
            self.cap(cx + r * cos, cy + r * sin)?;
        }
        Ok(())
    }

    fn polygon(&mut self, points: &[(f64, f64)]) -> Result<()> {
        let mut xy: Vec<(f64, f64)> = points.iter().map(|&(x, y)| self.p(x, y)).collect();
        xy.push(xy[0]);
        let (colour, w) = (self.color, self.w);
        Draw::new(&mut self.img).line(&xy, Some(colour), w, true)
    }

    fn mask(&self, paint: &Paint<'_>) -> Result<Image> {
        let mut mask = Image::blank(Mode::L, self.img.width(), self.img.height());
        paint(&mut Draw::new(&mut mask), self)?;
        Ok(mask)
    }

    /// shape: the outline of a filled shape (circles, rects, ... together): the shape less
    /// itself eroded by the stroke (a Gaussian blur thresholded at 2 sigma).
    fn shape(&mut self, paint: &Paint<'_>, erase: Option<&Paint<'_>>) -> Result<()> {
        let mut mask = self.mask(paint)?;
        if let Some(erase) = erase {
            mask = subtract(&mask, &self.mask(erase)?, 1.0, 0)?;
        }
        let radius = self.w as f32 / 2.0;
        let lut: Vec<i64> = (0..256).map(|v| if v >= 249 { 255 } else { 0 }).collect();
        let inner = mask.gaussian_blur(radius, radius)?.point(&lut)?;
        let ring = subtract(&mask, &inner, 1.0, 0)?;
        let (w, h) = (self.img.width() as i64, self.img.height() as i64);
        let colour = self.color;
        self.img.paste_colour(&colour, (0, 0, w, h), Some(&ring))
    }

    /// clear: a shape and a stroke's gap round it erased.
    fn clear(&mut self, paint: &Paint<'_>) -> Result<()> {
        let mask = self.mask(paint)?;
        let radius = self.w as f32 / 2.0;
        let lut: Vec<i64> = (0..256).map(|v| if v >= 6 { 255 } else { 0 }).collect();
        let grown = mask.gaussian_blur(radius, radius)?.point(&lut)?;
        let (w, h) = (self.img.width() as i64, self.img.height() as i64);
        self.img
            .paste_colour(&[0, 0, 0, 0], (0, 0, w, h), Some(&grown))
    }
}

fn cloud(d: &mut Draw<'_, Image>, pen: &Pen, dx: f64, dy: f64) -> Result<()> {
    for (cx, cy, r) in [(8.0, 14.5, 4.0), (13.0, 11.0, 5.5), (17.5, 14.5, 3.8)] {
        d.ellipse(
            pen.bx(cx - r + dx, cy - r + dy, cx + r + dx, cy + r + dy),
            Some(FULL),
            None,
            1,
        )?;
    }
    d.rectangle(
        pen.bx(8.0 + dx, 13.0 + dy, 17.5 + dx, 18.3 + dy),
        Some(FULL),
        None,
        1,
    )
}

fn cloud_top(pen: &mut Pen, dy: f64) -> Result<()> {
    pen.shape(&|d, p| cloud(d, p, 0.0, dy), None)
}

fn sun(pen: &mut Pen, cx: f64, cy: f64, r: f64, ray: f64) -> Result<()> {
    pen.circle(cx, cy, r, false)?;
    for i in 0..8 {
        let (sin, cos) = (f64::from(i) * 45.0).to_radians().sin_cos();
        pen.line(&[
            (cx + (r + 2.0) * cos, cy + (r + 2.0) * sin),
            (cx + (r + 2.0 + ray) * cos, cy + (r + 2.0 + ray) * sin),
        ])?;
    }
    Ok(())
}

fn speaker(pen: &mut Pen) -> Result<()> {
    pen.polygon(&[
        (3.0, 9.0),
        (7.0, 9.0),
        (12.0, 4.5),
        (12.0, 19.5),
        (7.0, 15.0),
        (3.0, 15.0),
    ])
}

fn mic(pen: &mut Pen) -> Result<()> {
    pen.rect(9.0, 2.5, 15.0, 14.5, 3.0)?;
    pen.arc(12.0, 11.0, 7.0, 0.0, 180.0)?;
    pen.line(&[(12.0, 18.0), (12.0, 21.5)])?;
    pen.line(&[(8.5, 21.5), (15.5, 21.5)])
}

fn horizon_sun(pen: &mut Pen) -> Result<()> {
    pen.arc(12.0, 16.0, 5.0, 180.0, 360.0)?;
    for a in [180.0f64, 225.0, 270.0, 315.0, 360.0] {
        let (sin, cos) = a.to_radians().sin_cos();
        pen.line(&[
            (12.0 + 7.5 * cos, 16.0 + 7.5 * sin),
            (12.0 + 9.5 * cos, 16.0 + 9.5 * sin),
        ])?;
    }
    pen.line(&[(2.0, 16.0), (22.0, 16.0)])
}

/// _wifi_arcs: a dot and three arcs over it, `arcs` of them lit, the others faint.
fn wifi_arcs(pen: &mut Pen, arcs: usize) -> Result<()> {
    let lit = pen.color;
    pen.circle(12.0, 19.0, 1.4, true)?;
    for (i, r) in [5.0, 9.5, 14.0].into_iter().enumerate() {
        pen.color = if i < arcs {
            lit
        } else {
            let faint = lp_py::round(f64::from(lit[3]) * 0.28) as u8;
            [lit[0], lit[1], lit[2], faint]
        };
        pen.arc(12.0, 20.0, r, 225.0, 315.0)?;
    }
    pen.color = lit;
    Ok(())
}

/// The icon of `name` drawn with `pen` (the cloud for a name there is none of).
fn icon(name: &str, pen: &mut Pen) -> Result<()> {
    match name {
        "cpu" => {
            pen.rect(5.0, 5.0, 19.0, 19.0, 2.0)?;
            pen.rect(9.0, 9.0, 15.0, 15.0, 1.0)?;
            for v in [9.0, 12.0, 15.0] {
                pen.line(&[(v, 2.0), (v, 5.0)])?;
                pen.line(&[(v, 19.0), (v, 22.0)])?;
                pen.line(&[(2.0, v), (5.0, v)])?;
                pen.line(&[(19.0, v), (22.0, v)])?;
            }
            Ok(())
        }
        "gpu" => {
            pen.rect(2.0, 6.0, 22.0, 17.0, 2.0)?;
            pen.circle(9.0, 11.5, 3.0, false)?;
            pen.line(&[(15.0, 9.5), (19.0, 9.5)])?;
            pen.line(&[(15.0, 13.5), (19.0, 13.5)])?;
            for x in [6.0, 10.0, 14.0] {
                pen.line(&[(x, 17.0), (x, 20.0)])?;
            }
            Ok(())
        }
        "ram" => {
            pen.rect(2.0, 7.0, 22.0, 15.0, 1.5)?;
            for x in [5.0, 10.5, 16.0] {
                pen.rect(x, 9.5, x + 3.0, 12.5, 0.5)?;
            }
            for x in [6.0, 10.0, 14.0, 18.0] {
                pen.line(&[(x, 15.0), (x, 18.0)])?;
            }
            Ok(())
        }
        "disk" => {
            pen.rect(3.0, 4.0, 21.0, 20.0, 2.5)?;
            pen.circle(12.0, 11.0, 4.0, false)?;
            pen.circle(12.0, 11.0, 0.9, true)?;
            pen.line(&[(7.0, 17.0), (9.5, 17.0)])
        }
        "network" => {
            pen.line(&[(8.0, 3.0), (8.0, 19.0)])?;
            pen.line(&[(4.0, 15.0), (8.0, 19.0), (12.0, 15.0)])?;
            pen.line(&[(16.0, 21.0), (16.0, 5.0)])?;
            pen.line(&[(12.0, 9.0), (16.0, 5.0), (20.0, 9.0)])
        }
        "download" => {
            pen.line(&[(12.0, 3.0), (12.0, 15.0)])?;
            pen.line(&[(7.0, 10.0), (12.0, 15.0), (17.0, 10.0)])?;
            pen.line(&[(5.0, 20.0), (19.0, 20.0)])
        }
        "upload" => {
            pen.line(&[(12.0, 15.0), (12.0, 3.0)])?;
            pen.line(&[(7.0, 8.0), (12.0, 3.0), (17.0, 8.0)])?;
            pen.line(&[(5.0, 20.0), (19.0, 20.0)])
        }
        "temperature" => {
            let s = pen.s;
            pen.shape(
                &|d, p| {
                    d.rounded_rectangle(
                        p.bx(8.5, 1.5, 15.5, 15.0),
                        3.5 * s,
                        Some(FULL),
                        None,
                        1,
                        ALL_CORNERS,
                    )?;
                    d.ellipse(p.bx(6.8, 12.3, 17.2, 22.7), Some(FULL), None, 1)
                },
                None,
            )?;
            pen.circle(12.0, 17.5, 2.2, true)?;
            pen.line(&[(12.0, 6.5), (12.0, 17.0)])
        }
        "fan" => {
            pen.circle(12.0, 12.0, 10.0, false)?;
            pen.circle(12.0, 12.0, 1.6, true)?;
            for a in [0.0f64, 120.0, 240.0] {
                let (sin, cos) = (a + 60.0).to_radians().sin_cos();
                pen.arc(
                    12.0 + 4.0 * cos,
                    12.0 + 4.0 * sin,
                    4.0,
                    a + 150.0,
                    a + 290.0,
                )?;
            }
            Ok(())
        }
        "power" => pen.polygon(&[
            (13.0, 2.0),
            (4.0, 14.0),
            (11.0, 14.0),
            (10.0, 22.0),
            (20.0, 9.5),
            (13.0, 9.5),
        ]),
        "clock" => {
            pen.circle(12.0, 12.0, 9.5, false)?;
            pen.line(&[(12.0, 6.5), (12.0, 12.0), (15.5, 14.0)])
        }
        "sun" => sun(pen, 12.0, 12.0, 4.0, 2.5),
        "moon" => pen.shape(
            &|d, p| d.ellipse(p.bx(3.5, 3.5, 20.5, 20.5), Some(FULL), None, 1),
            Some(&|d, p| d.ellipse(p.bx(10.0, 1.0, 24.0, 15.0), Some(FULL), None, 1)),
        ),
        "partly" => {
            sun(pen, 8.5, 8.5, 3.0, 1.8)?;
            pen.clear(&|d, p| cloud(d, p, 1.5, 3.0))?;
            pen.shape(&|d, p| cloud(d, p, 1.5, 3.0), None)
        }
        "rain" => {
            cloud_top(pen, -3.0)?;
            for x in [8.0, 12.0, 16.0] {
                pen.line(&[(x, 17.5), (x - 1.5, 21.5)])?;
            }
            Ok(())
        }
        "snow" => {
            cloud_top(pen, -3.0)?;
            for (x, y) in [(8.0, 18.5), (12.0, 21.0), (16.0, 18.5)] {
                pen.circle(x, y, 0.9, true)?;
            }
            Ok(())
        }
        "storm" => {
            cloud_top(pen, -3.0)?;
            pen.line(&[(12.5, 16.5), (10.0, 20.0), (13.5, 20.0), (11.0, 23.0)])
        }
        "fog" => {
            cloud_top(pen, -4.0)?;
            pen.line(&[(4.0, 17.5), (20.0, 17.5)])?;
            pen.line(&[(6.0, 21.0), (18.0, 21.0)])
        }
        "humidity" => pen.shape(
            &|d, p| {
                let tip = [p.p(12.0, 2.5), p.p(5.8, 13.0), p.p(18.2, 13.0)];
                d.polygon(&tip, Some(FULL), None, 1)?;
                d.ellipse(p.bx(5.5, 8.5, 18.5, 21.5), Some(FULL), None, 1)
            },
            None,
        ),
        "wind" => {
            pen.line(&[(3.0, 8.0), (14.0, 8.0)])?;
            pen.arc(14.0, 5.5, 2.5, -90.0, 90.0)?;
            pen.line(&[(3.0, 12.0), (18.0, 12.0)])?;
            pen.arc(18.0, 14.5, 2.5, -90.0, 90.0)?;
            pen.line(&[(3.0, 16.0), (10.0, 16.0)])
        }
        "battery" => {
            pen.rect(2.0, 7.0, 19.5, 17.0, 2.0)?;
            pen.line(&[(22.0, 10.5), (22.0, 13.5)])?;
            let (bounds, colour, s) = (pen.bx(5.0, 10.0, 12.0, 14.0), pen.color, pen.s);
            Draw::new(&mut pen.img).rounded_rectangle(bounds, s, Some(colour), None, 1, ALL_CORNERS)
        }
        "plug" => {
            pen.line(&[(9.0, 2.5), (9.0, 7.0)])?;
            pen.line(&[(15.0, 2.5), (15.0, 7.0)])?;
            let s = pen.s;
            pen.shape(
                &|d, p| {
                    let body = p.bx(5.5, 7.0, 18.5, 15.5);
                    d.rounded_rectangle(body, 4.0 * s, Some(FULL), None, 1, ALL_CORNERS)
                },
                None,
            )?;
            pen.line(&[(12.0, 15.5), (12.0, 21.5)])
        }
        "list" => {
            for y in [6.0, 12.0, 18.0] {
                pen.circle(4.5, y, 1.4, true)?;
                pen.line(&[(9.0, y), (20.0, y)])?;
            }
            Ok(())
        }
        "grid" => {
            for (x, y) in [(3.0, 3.0), (13.5, 3.0), (3.0, 13.5), (13.5, 13.5)] {
                pen.rect(x, y, x + 7.5, y + 7.5, 1.5)?;
            }
            Ok(())
        }
        "signal" => {
            for (i, x) in [5.0, 10.0, 15.0, 20.0].into_iter().enumerate() {
                pen.line(&[(x, 20.0), (x, 16.0 - i as f64 * 4.0)])?;
            }
            Ok(())
        }
        "ethernet" => {
            pen.line(&[
                (5.0, 9.0),
                (8.0, 9.0),
                (8.0, 6.0),
                (16.0, 6.0),
                (16.0, 9.0),
                (19.0, 9.0),
                (19.0, 19.0),
                (5.0, 19.0),
                (5.0, 9.0),
            ])?;
            for x in [9.5, 12.0, 14.5] {
                pen.line(&[(x, 13.0), (x, 16.0)])?;
            }
            Ok(())
        }
        "wifi" | "link" => wifi_arcs(pen, 3),
        "wifi-1" => wifi_arcs(pen, 1),
        "wifi-2" => wifi_arcs(pen, 2),
        "uptime" => {
            pen.arc(12.0, 13.5, 8.0, 300.0, 600.0)?;
            pen.line(&[(12.0, 3.0), (12.0, 11.5)])
        }
        "sunrise" => {
            horizon_sun(pen)?;
            pen.line(&[(12.0, 23.0), (12.0, 19.5)])?;
            pen.line(&[(10.0, 21.2), (12.0, 19.5), (14.0, 21.2)])
        }
        "sunset" => {
            horizon_sun(pen)?;
            pen.line(&[(12.0, 19.5), (12.0, 23.0)])?;
            pen.line(&[(10.0, 21.3), (12.0, 23.0), (14.0, 21.3)])
        }
        "hourglass" => {
            pen.line(&[(6.0, 2.5), (18.0, 2.5)])?;
            pen.line(&[(6.0, 21.5), (18.0, 21.5)])?;
            pen.polygon(&[(7.5, 2.5), (16.5, 2.5), (12.0, 12.0)])?;
            pen.polygon(&[(12.0, 12.0), (16.5, 21.5), (7.5, 21.5)])
        }
        "image" => {
            pen.rect(2.5, 4.0, 21.5, 20.0, 2.0)?;
            pen.circle(8.0, 9.5, 1.8, false)?;
            pen.line(&[
                (3.5, 18.0),
                (9.5, 12.5),
                (13.0, 16.0),
                (16.0, 13.0),
                (20.5, 17.5),
            ])
        }
        "globe" => {
            pen.circle(12.0, 12.0, 9.5, false)?;
            pen.line(&[(2.5, 12.0), (21.5, 12.0)])?;
            let (bounds, colour, w) = (pen.bx(7.5, 2.5, 16.5, 21.5), pen.color, pen.w);
            Draw::new(&mut pen.img).ellipse(bounds, None, Some(colour), w)?;
            pen.line(&[(4.0, 7.2), (20.0, 7.2)])?;
            pen.line(&[(4.0, 16.8), (20.0, 16.8)])
        }
        "music" => {
            pen.line(&[(9.0, 18.0), (9.0, 4.5), (20.0, 2.5), (20.0, 16.0)])?;
            pen.line(&[(9.0, 8.8), (20.0, 6.8)])?;
            pen.circle(6.3, 18.0, 2.7, true)?;
            pen.circle(17.3, 16.0, 2.7, true)
        }
        "calendar" => {
            pen.rect(3.0, 5.0, 21.0, 21.0, 2.0)?;
            pen.line(&[(3.0, 10.0), (21.0, 10.0)])?;
            pen.line(&[(8.0, 2.5), (8.0, 6.5)])?;
            pen.line(&[(16.0, 2.5), (16.0, 6.5)])?;
            for (x, y) in [
                (8.0, 14.0),
                (12.0, 14.0),
                (16.0, 14.0),
                (8.0, 17.5),
                (12.0, 17.5),
            ] {
                pen.circle(x, y, 0.9, true)?;
            }
            Ok(())
        }
        "home" => {
            pen.polygon(&[
                (3.0, 11.0),
                (12.0, 3.0),
                (21.0, 11.0),
                (19.0, 11.0),
                (19.0, 21.0),
                (5.0, 21.0),
                (5.0, 11.0),
            ])?;
            pen.line(&[(10.0, 21.0), (10.0, 15.0), (14.0, 15.0), (14.0, 21.0)])
        }
        "agent" => {
            pen.circle(12.0, 12.8, 8.0, false)?;
            pen.circle(12.0, 4.8, 2.4, true)
        }
        "gamepad" => {
            let s = pen.s;
            pen.shape(
                &|d, p| {
                    let body = p.bx(3.5, 7.0, 20.5, 16.0);
                    d.rounded_rectangle(body, 4.5 * s, Some(FULL), None, 1, ALL_CORNERS)?;
                    d.ellipse(p.bx(2.0, 9.5, 9.5, 19.5), Some(FULL), None, 1)?;
                    d.ellipse(p.bx(14.5, 9.5, 22.0, 19.5), Some(FULL), None, 1)
                },
                None,
            )?;
            pen.line(&[(8.0, 9.8), (8.0, 13.2)])?;
            pen.line(&[(6.3, 11.5), (9.7, 11.5)])?;
            pen.circle(15.5, 10.5, 0.9, true)?;
            pen.circle(17.5, 12.6, 0.9, true)
        }
        "usb" => {
            pen.line(&[(12.0, 19.0), (12.0, 4.5)])?;
            let arrow = [pen.p(12.0, 1.8), pen.p(9.0, 6.4), pen.p(15.0, 6.4)];
            let colour = pen.color;
            Draw::new(&mut pen.img).polygon(&arrow, Some(colour), None, 1)?;
            pen.line(&[(12.0, 14.5), (6.5, 11.0), (6.5, 8.5)])?;
            pen.circle(6.5, 7.0, 1.9, true)?;
            pen.line(&[(12.0, 11.5), (17.5, 8.5), (17.5, 7.2)])?;
            let square = pen.bx(15.7, 3.9, 19.3, 7.5);
            Draw::new(&mut pen.img).rectangle(square, Some(colour), None, 1)?;
            pen.circle(12.0, 20.2, 2.1, true)
        }
        "stick" => {
            pen.rect(8.0, 2.0, 16.0, 9.0, 1.0)?;
            pen.circle(10.4, 5.7, 0.7, true)?;
            pen.circle(13.6, 5.7, 0.7, true)?;
            pen.rect(6.0, 9.0, 18.0, 22.0, 2.5)?;
            pen.line(&[(10.0, 18.0), (14.0, 18.0)])
        }
        "phone" => {
            pen.rect(6.0, 2.0, 18.0, 22.0, 2.5)?;
            pen.line(&[(10.5, 5.8), (13.5, 5.8)])?;
            pen.line(&[(10.5, 18.2), (13.5, 18.2)])
        }
        "ssd" => {
            pen.rect(2.0, 7.5, 22.0, 16.5, 1.5)?;
            pen.line(&[(2.0, 10.5), (4.5, 10.5), (4.5, 13.5), (2.0, 13.5)])?;
            pen.rect(7.0, 10.0, 11.5, 14.0, 0.6)?;
            pen.rect(13.0, 10.0, 17.5, 14.0, 0.6)?;
            pen.circle(20.0, 12.0, 0.9, true)
        }
        "play" => {
            let corners = [(7.5, 4.5), (7.5, 19.5), (19.0, 12.0)];
            let xy: Vec<(f64, f64)> = corners.iter().map(|&(x, y)| pen.p(x, y)).collect();
            let colour = pen.color;
            Draw::new(&mut pen.img).polygon(&xy, Some(colour), None, 1)?;
            pen.polygon(&corners) // (its stroke rounds the corners)
        }
        "pause" => {
            for left in [6.5, 13.5] {
                let (bounds, colour, s) = (pen.bx(left, 4.5, left + 4.0, 19.5), pen.color, pen.s);
                Draw::new(&mut pen.img).rounded_rectangle(
                    bounds,
                    s * 1.1,
                    Some(colour),
                    None,
                    1,
                    ALL_CORNERS,
                )?;
            }
            Ok(())
        }
        "up" => {
            pen.line(&[(12.0, 20.0), (12.0, 4.5)])?;
            pen.line(&[(6.0, 10.5), (12.0, 4.5), (18.0, 10.5)])
        }
        "down" => {
            pen.line(&[(12.0, 4.0), (12.0, 19.5)])?;
            pen.line(&[(6.0, 13.5), (12.0, 19.5), (18.0, 13.5)])
        }
        "volume" => {
            speaker(pen)?;
            pen.arc(12.5, 12.0, 4.0, -42.0, 42.0)?;
            pen.arc(12.5, 12.0, 8.0, -48.0, 48.0)
        }
        "mute" => {
            speaker(pen)?;
            pen.line(&[(15.5, 9.0), (21.0, 15.0)])?;
            pen.line(&[(21.0, 9.0), (15.5, 15.0)])
        }
        "mic" => mic(pen),
        "mic_off" => {
            mic(pen)?;
            pen.line(&[(4.0, 3.0), (20.0, 21.0)])
        }
        _ => pen.shape(&|d, p| cloud(d, p, 0.0, 0.0), None), // (the cloud: _cloud_icon)
    }
}

/// draw_icon: the RGBA icon of `name`, `size` px square (8 to 512), `stroke` in units of the
/// 24 grid.
pub fn draw_icon(name: &str, size: i64, color: Ink, stroke: f64) -> Result<Image> {
    let size = size.clamp(8, 512);
    let mut pen = Pen::new(size, color, stroke);
    icon(name, &mut pen)?;
    pen.img
        .resize(size as usize, size as usize, Filter::Lanczos, None, None)
}

/// weather_icon_name: the icon of a WMO weather code (Open-Meteo's); the cloud for no code or
/// one that is no number.
pub fn weather_icon_name(code: &Value, night: bool) -> &'static str {
    let code = match code {
        Value::Bool(b) => i64::from(*b),
        Value::Int(n) => match i64::try_from(n) {
            Ok(n) => n,
            Err(_) => {
                return if n.sign() == num_bigint::Sign::Minus {
                    "cloud"
                } else {
                    "storm"
                }
            }
        },
        Value::Float(x) if x.is_finite() => {
            let t = x.trunc();
            if t >= 9.2e18 {
                return "storm";
            }
            if t <= -9.2e18 {
                return "cloud";
            }
            t as i64
        }
        Value::Str(s) => match lp_py::int_of_str(s) {
            Some(n) => match i64::try_from(&n) {
                Ok(n) => n,
                Err(_) => {
                    return if n.sign() == num_bigint::Sign::Minus {
                        "cloud"
                    } else {
                        "storm"
                    }
                }
            },
            None => return "cloud",
        },
        _ => return "cloud", // (None, NaN, infinity, a list: no code)
    };
    match code {
        0 | 1 => {
            if night {
                "moon"
            } else {
                "sun"
            }
        }
        2 => "partly",
        3 => "cloud",
        45 | 48 => "fog",
        71 | 73 | 75 | 77 | 85 | 86 => "snow",
        _ if code >= 95 => "storm",
        51..=82 => "rain",
        _ => "cloud",
    }
}

/// link_icon: the network connection's icon: a cable's plug, or Wi-Fi with as many arcs as its
/// signal is strong (a third: one, two thirds: two, more: three).
pub fn link_icon(kind: &Value, signal: &Value) -> &'static str {
    if *kind == Value::from("lan") {
        return "ethernet";
    }
    if *kind != Value::from("wlan") {
        return "network";
    }
    let number = matches!(signal, Value::Bool(_) | Value::Int(_) | Value::Float(_));
    if !number {
        return "wifi";
    }
    let below = |limit: i64| lp_py::num::lt(signal, &Value::int(limit)).unwrap_or(false);
    if below(34) {
        "wifi-1"
    } else if below(67) {
        "wifi-2"
    } else {
        "wifi"
    }
}
