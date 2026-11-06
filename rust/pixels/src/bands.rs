//! An image's bands: split, getchannel, merge and putalpha (libImaging/Bands.c, PIL/Image.py).

use crate::{error, Image, Mode, Result};

impl Image {
    /// Image.getchannel(band): band ``band`` (0 the first) as an L image.
    pub fn getchannel(&self, band: usize) -> Result<Image> {
        let bands = self.bands();
        if band >= bands {
            return error("band index out of range");
        }
        let data = self.data.chunks_exact(bands).map(|p| p[band]).collect();
        Ok(Image {
            mode: Mode::L,
            width: self.width,
            height: self.height,
            data,
        })
    }

    /// Image.split(): every band as an L image.
    pub fn split(&self) -> Vec<Image> {
        (0..self.bands())
            .map(|b| self.getchannel(b).expect("a band of the image"))
            .collect()
    }

    /// Image.putalpha(alpha): an L image of this size as the alpha. RGB becomes RGBA in place
    /// (_imaging.c's setmode, between RGB modes, fills the alpha with 255 first); RGBa is no RGB
    /// mode to setmode, so Image.py converts it to RGBA: its colours divided by its old alpha.
    pub fn putalpha(&mut self, alpha: &Image) -> Result<()> {
        if alpha.mode != Mode::L {
            return error("illegal image mode");
        }
        if alpha.size() != self.size() {
            return error("images do not match");
        }
        self.with_alpha_band()?;
        for (p, &a) in self.data.chunks_exact_mut(4).zip(&alpha.data) {
            p[3] = a;
        }
        Ok(())
    }

    /// Image.putalpha(value): the same alpha everywhere (fillband).
    pub fn putalpha_value(&mut self, value: u8) -> Result<()> {
        self.with_alpha_band()?;
        for p in self.data.chunks_exact_mut(4) {
            p[3] = value;
        }
        Ok(())
    }

    fn with_alpha_band(&mut self) -> Result<()> {
        match self.mode {
            Mode::RGBA => {}
            Mode::RGBa => *self = self.convert(Mode::RGBA)?,
            Mode::RGB => {
                let mut data = Vec::with_capacity(self.width * self.height * 4);
                for p in self.data.chunks_exact(3) {
                    data.extend_from_slice(&[p[0], p[1], p[2], 255]);
                }
                self.data = data;
                self.mode = Mode::RGBA;
            }
            Mode::L => return error("alpha channel could not be added"),
        }
        Ok(())
    }
}

/// Image.merge(mode, bands): L images of one size as the bands of a ``mode`` image.
pub fn merge(mode: Mode, bands: &[&Image]) -> Result<Image> {
    if bands.len() != mode.bands() {
        return error("wrong number of bands");
    }
    let (w, h) = bands[0].size();
    if bands
        .iter()
        .any(|b| b.mode != Mode::L || b.size() != (w, h))
    {
        return error("mode mismatch");
    }
    let n = mode.bands();
    let mut data = vec![0u8; w * h * n];
    for (b, band) in bands.iter().enumerate() {
        for (p, &v) in data.chunks_exact_mut(n).zip(&band.data) {
            p[b] = v;
        }
    }
    Ok(Image {
        mode,
        width: w,
        height: h,
        data,
    })
}
