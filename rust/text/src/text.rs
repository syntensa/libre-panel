//! ImageDraw's text as Pillow 13.0 has it (ImageText.Text, ImageDraw.text, textbbox,
//! textlength): the lines of a text, where each starts from the anchor and the alignment, the
//! box they take, and each drawn as its mask with the ink through it (stroked first where the
//! text has a stroke).

use lp_pixels::{Draw, Image, Mode};

use crate::{Error, Font, Kind, Result};

/// What ImageDraw asks of a font: FreeTypeFont's getlength, getbbox and getmask2 (and of a
/// face that falls back to others for what it lacks, the same).
pub trait TextFace {
    fn getlength(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
    ) -> Result<f64>;

    #[allow(clippy::too_many_arguments)]
    fn getbbox(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
        stroke_width: f64,
        anchor: Option<&str>,
    ) -> Result<(f64, f64, f64, f64)>;

    #[allow(clippy::too_many_arguments)]
    fn getmask2(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
        stroke_width: f64,
        anchor: Option<&str>,
        ink: i64,
        start: (f64, f64),
        stroke_filled: bool,
    ) -> Result<(Image, (i32, i64))>;
}

impl TextFace for Font {
    fn getlength(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
    ) -> Result<f64> {
        Font::getlength(self, text, mode, direction, features, language)
    }

    fn getbbox(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
        stroke_width: f64,
        anchor: Option<&str>,
    ) -> Result<(f64, f64, f64, f64)> {
        Font::getbbox(
            self,
            text,
            mode,
            direction,
            features,
            language,
            stroke_width,
            anchor,
        )
    }

    fn getmask2(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
        stroke_width: f64,
        anchor: Option<&str>,
        ink: i64,
        start: (f64, f64),
        stroke_filled: bool,
    ) -> Result<(Image, (i32, i64))> {
        Font::getmask2(
            self,
            text,
            mode,
            direction,
            features,
            language,
            stroke_width,
            anchor,
            ink,
            start,
            stroke_filled,
        )
    }
}

/// One line (or with "justify" one word) of a text and where it is drawn from.
#[derive(Clone, Debug, PartialEq)]
pub struct Line<'a> {
    pub x: f64,
    pub y: f64,
    pub anchor: String,
    pub text: &'a str,
}

/// ImageText.Text: a text in a font for an image of a mode.
pub struct Text<'a, F: TextFace + ?Sized> {
    pub text: &'a str,
    pub font: &'a F,
    /// the mode of the image it is for ("L", "RGB", "RGBA", …)
    pub mode: &'a str,
    pub spacing: f64,
    pub direction: Option<&'a str>,
    pub features: Option<&'a [String]>,
    pub language: Option<&'a str>,
    pub embedded_color: bool,
    pub stroke_width: f64,
}

fn value(message: &str) -> Error {
    Error::new(Kind::Value, message)
}

impl<'a, F: TextFace + ?Sized> Text<'a, F> {
    /// Text(text, font, mode): Pillow's spacing of 4, no direction, features or language.
    pub fn new(text: &'a str, font: &'a F, mode: &'a str) -> Self {
        Text {
            text,
            font,
            mode,
            spacing: 4.0,
            direction: None,
            features: None,
            language: None,
            embedded_color: false,
            stroke_width: 0.0,
        }
    }

    /// embed_color: the font's own colours (colour glyphs), for RGB and RGBA images.
    pub fn embed_color(&mut self) -> Result<()> {
        if !matches!(self.mode, "RGB" | "RGBA") {
            return Err(value("Embedded color supported only in RGB and RGBA modes"));
        }
        self.embedded_color = true;
        Ok(())
    }

    fn fontmode(&self) -> &'static str {
        if matches!(self.mode, "1" | "P" | "I" | "F") {
            "1"
        } else if self.embedded_color {
            "RGBA"
        } else {
            "L"
        }
    }

    /// get_length: the advance of a one-line text.
    pub fn get_length(&self) -> Result<f64> {
        if self.text.contains('\n') {
            return Err(value("can't measure length of multiline text"));
        }
        self.font.getlength(
            self.text,
            self.fontmode(),
            self.direction,
            self.features,
            self.language,
        )
    }

    /// _split: the lines and where each is drawn from, for the anchor and the alignment.
    pub fn split(
        &self,
        xy: (f64, f64),
        anchor: Option<&str>,
        align: &str,
    ) -> Result<Vec<Line<'a>>> {
        let ttb = self.direction == Some("ttb");
        let anchor = match anchor {
            None => if ttb { "lt" } else { "la" }.to_string(),
            Some(a) if a.chars().count() != 2 => {
                return Err(value("anchor must be a 2 character string"))
            }
            Some(a) => a.to_string(),
        };
        let lines: Vec<&'a str> = self.text.split('\n').collect();
        if lines.len() == 1 {
            return Ok(vec![Line {
                x: xy.0,
                y: xy.1,
                anchor,
                text: lines[0],
            }]);
        }
        let mut letters = anchor.chars();
        let (horizontal, vertical) = (letters.next().unwrap(), letters.next().unwrap());
        if "tb".contains(vertical) && !ttb {
            return Err(value("anchor not supported for multiline text"));
        }
        let fontmode = self.fontmode();
        let bbox = self.font.getbbox(
            "A",
            fontmode,
            None,
            self.features,
            self.language,
            self.stroke_width,
            None,
        )?;
        let line_spacing = bbox.3 + self.stroke_width + self.spacing;
        let mut top = xy.1;
        let mut parts = Vec::with_capacity(lines.len());
        if ttb {
            let mut left = xy.0;
            for line in lines {
                parts.push(Line {
                    x: left,
                    y: top,
                    anchor: anchor.clone(),
                    text: line,
                });
                left += line_spacing;
            }
            return Ok(parts);
        }
        let length = |text: &str| {
            self.font
                .getlength(text, fontmode, self.direction, self.features, self.language)
        };
        let mut widths = Vec::with_capacity(lines.len());
        let mut max_width = 0.0f64;
        for line in &lines {
            let width = length(line)?;
            widths.push(width);
            if width > max_width {
                max_width = width;
            }
        }
        let count = lines.len();
        if vertical == 'm' {
            top -= (count - 1) as f64 * line_spacing / 2.0;
        } else if vertical == 'd' {
            top -= (count - 1) as f64 * line_spacing;
        }
        for (idx, line) in lines.into_iter().enumerate() {
            let mut left = xy.0;
            let mut width_difference = max_width - widths[idx];
            match align {
                "left" | "justify" => {}
                "center" => left += width_difference / 2.0,
                "right" => left += width_difference,
                _ => {
                    return Err(value(
                        r#"align must be "left", "center", "right" or "justify""#,
                    ))
                }
            }
            if align == "justify" && width_difference != 0.0 && idx != count - 1 {
                let words: Vec<&'a str> = line.split(' ').collect();
                if words.len() > 1 {
                    if horizontal == 'm' {
                        left -= max_width / 2.0;
                    } else if horizontal == 'r' {
                        left -= max_width;
                    }
                    let word_widths = words
                        .iter()
                        .map(|word| length(word))
                        .collect::<Result<Vec<f64>>>()?;
                    let word_anchor = format!("l{vertical}");
                    width_difference = max_width - word_widths.iter().fold(0.0, |s, w| s + w);
                    for (i, word) in words.iter().enumerate() {
                        parts.push(Line {
                            x: left,
                            y: top,
                            anchor: word_anchor.clone(),
                            text: word,
                        });
                        left += word_widths[i] + width_difference / (words.len() - 1) as f64;
                    }
                    top += line_spacing;
                    continue;
                }
            }
            if horizontal == 'm' {
                left -= width_difference / 2.0;
            } else if horizontal == 'r' {
                left -= width_difference;
            }
            parts.push(Line {
                x: left,
                y: top,
                anchor: anchor.clone(),
                text: line,
            });
            top += line_spacing;
        }
        Ok(parts)
    }

    /// get_bbox: the box all the lines take (textbbox).
    pub fn get_bbox(
        &self,
        xy: (f64, f64),
        anchor: Option<&str>,
        align: &str,
    ) -> Result<(f64, f64, f64, f64)> {
        let mut found: Option<(f64, f64, f64, f64)> = None;
        for line in self.split(xy, anchor, align)? {
            let b = self.font.getbbox(
                line.text,
                self.fontmode(),
                self.direction,
                self.features,
                self.language,
                self.stroke_width,
                Some(&line.anchor),
            )?;
            let b = (b.0 + line.x, b.1 + line.y, b.2 + line.x, b.3 + line.y);
            found = Some(match found {
                None => b,
                Some(f) => (f.0.min(b.0), f.1.min(b.1), f.2.max(b.2), f.3.max(b.3)),
            });
        }
        Ok(found.expect("a line at least"))
    }
}

/// ImageDraw's fontmode for an image of the mode.
pub fn draw_fontmode(mode: Mode) -> &'static str {
    match mode.name() {
        "1" | "P" | "I" | "F" => "1",
        _ => "L",
    }
}

/// The ink's int as ImageDraw's draw_ink gives it (its bytes as Pillow's getink fills them).
pub fn ink_int(ink: [u8; 4]) -> i64 {
    i64::from(i32::from_ne_bytes(ink))
}

/// ImageDraw.text: the text drawn onto the image at `xy` from the anchor, in `ink` (a colour
/// as the image's mode has it), the stroke first in `stroke_ink` (else the ink) where the text
/// has a stroke.
pub fn draw<F: TextFace + ?Sized>(
    image: &mut Image,
    xy: (f64, f64),
    text: &Text<'_, F>,
    ink: [u8; 4],
    stroke_ink: Option<[u8; 4]>,
    anchor: Option<&str>,
    align: &str,
) -> Result<()> {
    let fontmode = draw_fontmode(image.mode());
    draw_in(image, xy, text, ink, stroke_ink, anchor, align, fontmode)
}

/// ImageDraw.text with the draw's fontmode set ("1": the letters hinted and drawn without
/// their grey edges).
#[allow(clippy::too_many_arguments)]
pub fn draw_in<F: TextFace + ?Sized>(
    image: &mut Image,
    xy: (f64, f64),
    text: &Text<'_, F>,
    ink: [u8; 4],
    stroke_ink: Option<[u8; 4]>,
    anchor: Option<&str>,
    align: &str,
    fontmode: &str,
) -> Result<()> {
    let stroke_ink = (text.stroke_width != 0.0).then(|| stroke_ink.unwrap_or(ink));
    for line in text.split(xy, anchor, align)? {
        let mut draw_text = |ink: [u8; 4], stroke_width: f64| -> Result<()> {
            let mode = if stroke_width == 0.0 && text.embedded_color {
                "RGBA"
            } else {
                fontmode
            };
            let (mut x, mut y) = (line.x.trunc() as i64, line.y.trunc() as i64);
            let start = (line.x.fract(), line.y.fract());
            let (mask, offset) = text.font.getmask2(
                line.text,
                mode,
                text.direction,
                text.features,
                text.language,
                stroke_width,
                Some(&line.anchor),
                ink_int(ink),
                start,
                true,
            )?;
            x += i64::from(offset.0);
            y += offset.1;
            if mode == "RGBA" {
                let alpha = mask.getchannel(3)?;
                let mut colour = mask;
                colour.putalpha_value(ink[3])?;
                image.paste(&colour, x, y, Some(&alpha))?;
            } else {
                Draw::new(image).draw_bitmap(x as f64, y as f64, &mask, ink)?;
            }
            Ok(())
        };
        match stroke_ink {
            Some(stroke) => {
                draw_text(stroke, text.stroke_width)?;
                if ink != stroke {
                    draw_text(ink, 0.0)?;
                }
            }
            None => draw_text(ink, 0.0)?,
        }
    }
    Ok(())
}
