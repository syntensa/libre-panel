//! GIF as Pillow 13.0 reads it (GifImagePlugin.py, GifDecode.c): the first frame as Image.open
//! loads a still, and every frame as the app reads an animation, each laid on the last as the
//! plugin lays it (its LOADING_STRATEGY RGB_AFTER_FIRST: the first frame in P or L, the next
//! ones in RGB or RGBA; the disposal methods; frames that grow the picture), with GifDecode.c's
//! LZW to the bit.
//!
//! The pictures are held as Pillow's C side holds them: one byte a pixel in L and P, four in
//! RGB, RGBA and LA (LA as L, L, L, A; RGB with a fourth byte of its own), a palette of 256
//! entries. So each step does to the bytes what Pillow's does: a paste copies a pixel's bytes
//! whatever the two modes, and the mode Python holds and the C picture's may differ, as they
//! do in Pillow (a first frame in L whose own palette is the grey ramp is shown through the
//! file's palette, for one).

use crate::{error, Error, Kind, Picture, Result, Transparency};
use lp_pixels::{Image, Mode};

/// Pillow's MAX_IMAGE_PIXELS twice: a size beyond it is refused as a decompression bomb.
const BOMB_PIXELS: u64 = 2 * (1024 * 1024 * 1024 / 4 / 3);

/// ImageFile's decodermaxblock: how much more of the file the decoder is given each time.
const MAX_BLOCK: usize = 65536;

/// GifDecode.c's table and history: 4096 entries (codes of 12 bits at most).
const TABLE: usize = 4096;

/// _decompression_bomb_check: each side counted as at least 1.
fn bomb_check(w: i64, h: i64) -> Result<()> {
    let pixels = w.max(1) as u64 * h.max(1) as u64;
    if pixels > BOMB_PIXELS {
        return error(format!(
            "Image size ({pixels} pixels) exceeds limit of {BOMB_PIXELS} pixels, could be \
             decompression bomb DOS attack."
        ));
    }
    Ok(())
}

/// Python's IndexError and struct.error where a block is shorter than read.
fn short<T>() -> Result<T> {
    error("a block shorter than its fields")
}

/// i16 (PIL._binary): two bytes, little end first.
fn i16(b: &[u8], at: usize) -> Result<i64> {
    match b.get(at..at + 2) {
        Some(s) => Ok(i64::from(u16::from_le_bytes([s[0], s[1]]))),
        None => short(),
    }
}

/// The modes of Pillow's C pictures the frames go through.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum M {
    L,
    P,
    Rgb,
    Rgba,
    La,
}

impl M {
    fn pixel_size(self) -> usize {
        match self {
            M::L | M::P => 1,
            _ => 4,
        }
    }

    fn name(self) -> &'static str {
        match self {
            M::L => "L",
            M::P => "P",
            M::Rgb => "RGB",
            M::Rgba => "RGBA",
            M::La => "LA",
        }
    }
}

/// A C palette: 256 entries of RGBA, `size` of them put (getpalette gives those).
#[derive(Clone)]
struct Palette {
    entries: Box<[[u8; 4]; 256]>,
    size: usize,
}

impl Palette {
    /// ImagingPaletteNew: black, opaque, none put.
    fn new() -> Palette {
        Palette {
            entries: Box::new([[0, 0, 0, 255]; 256]),
            size: 0,
        }
    }

    /// putpalette("RGB", "RGB", raw)'s palette: a new one with raw's whole entries put.
    fn from_rgb(raw: &[u8]) -> Result<Palette> {
        let size = raw.len() / 3;
        if size > 256 {
            return error("invalid palette size");
        }
        let mut palette = Palette::new();
        for (entry, rgb) in palette.entries.iter_mut().zip(raw.chunks_exact(3)) {
            *entry = [rgb[0], rgb[1], rgb[2], 255];
        }
        palette.size = size;
        Ok(palette)
    }

    /// getpalette(): the entries put, as RGB.
    fn rgb(&self) -> Vec<u8> {
        self.entries[..self.size]
            .iter()
            .flat_map(|e| [e[0], e[1], e[2]])
            .collect()
    }
}

/// A picture as Pillow's C side holds it.
#[derive(Clone)]
struct Core {
    mode: M,
    w: usize,
    h: usize,
    px: Vec<u8>,
    palette: Option<Palette>,
}

/// BLEND (ImagingUtils.h): in1 and in2 weighed by the mask, DIV255 rounding.
fn blend(mask: u32, in1: u8, in2: u8) -> u8 {
    let tmp = u32::from(in1) * (255 - mask) + u32::from(in2) * mask + 128;
    (((tmp >> 8) + tmp) >> 8) as u8
}

impl Core {
    /// Image.core.fill(mode, size, colour): every pixel the ink's bytes (getink's four, the
    /// first alone in L and P); core.new is the same with an ink of zeros.
    fn fill(mode: M, w: usize, h: usize, ink: [u8; 4]) -> Core {
        let n = mode.pixel_size();
        let px = if n == 1 {
            vec![ink[0]; w * h]
        } else {
            ink.repeat(w * h)
        };
        Core {
            mode,
            w,
            h,
            px,
            palette: (mode == M::P).then(Palette::new),
        }
    }

    /// Python's truth of a C picture: its number of pixels.
    fn len(&self) -> usize {
        self.w * self.h
    }

    /// putpalette("RGB", "RGB", raw): a new palette; an L picture becomes P.
    fn putpalette(&mut self, raw: &[u8]) -> Result<()> {
        if !matches!(self.mode, M::L | M::P) {
            return error("unrecognized image mode");
        }
        self.palette = Some(Palette::from_rgb(raw)?);
        self.mode = M::P;
        Ok(())
    }

    fn getpalette(&self) -> Result<Vec<u8>> {
        match &self.palette {
            Some(p) => Ok(p.rgb()),
            None => error("image has no palette"),
        }
    }

    /// putpalettealpha(index, 0)
    fn putpalettealpha(&mut self, index: u8) -> Result<()> {
        match &mut self.palette {
            Some(p) => {
                p.entries[usize::from(index)][3] = 0;
                Ok(())
            }
            None => error("image has no palette"),
        }
    }

    /// ImagingPaste into the box (x0, y0, x1, y1): the box `src`'s size and the pixels of one
    /// size (whatever the modes), clipped to this picture; with an LA or RGBA mask each byte
    /// blended by the mask's alpha (paste_mask_RGBA).
    fn paste(&mut self, src: &Core, b: (i64, i64, i64, i64), mask: Option<&Core>) -> Result<()> {
        let n = self.mode.pixel_size();
        let (mut xsize, mut ysize) = (b.2 - b.0, b.3 - b.1);
        if xsize != src.w as i64 || ysize != src.h as i64 || n != src.mode.pixel_size() {
            return error("images do not match");
        }
        if let Some(m) = mask {
            if (m.w, m.h) != (src.w, src.h) {
                return error("images do not match");
            }
            if !matches!(m.mode, M::La | M::Rgba) {
                return error("bad transparency mask");
            }
        }
        let (mut dx, mut dy, mut sx, mut sy) = (b.0, b.1, 0i64, 0i64);
        if dx < 0 {
            xsize += dx;
            sx = -dx;
            dx = 0;
        }
        if dx + xsize > self.w as i64 {
            xsize = self.w as i64 - dx;
        }
        if dy < 0 {
            ysize += dy;
            sy = -dy;
            dy = 0;
        }
        if dy + ysize > self.h as i64 {
            ysize = self.h as i64 - dy;
        }
        if xsize <= 0 || ysize <= 0 {
            return Ok(());
        }
        let (dx, dy, sx, sy) = (dx as usize, dy as usize, sx as usize, sy as usize);
        let (xsize, ysize) = (xsize as usize, ysize as usize);
        for y in 0..ysize {
            let out = ((dy + y) * self.w + dx) * n;
            let at = ((sy + y) * src.w + sx) * n;
            let row = &mut self.px[out..out + xsize * n];
            match mask {
                None => row.copy_from_slice(&src.px[at..at + xsize * n]),
                Some(m) => {
                    let alphas = &m.px[((sy + y) * m.w + sx) * 4..];
                    let from = &src.px[at..at + xsize * n];
                    let pixels = row.chunks_exact_mut(n).zip(from.chunks_exact(n));
                    for (x, (out, inp)) in pixels.enumerate() {
                        // (BLEND gives the one or the other at 0 and 255)
                        match alphas[x * 4 + 3] {
                            0 => {}
                            255 => out.copy_from_slice(inp),
                            a => {
                                for (o, &i) in out.iter_mut().zip(inp) {
                                    *o = blend(u32::from(a), *o, i);
                                }
                            }
                        }
                    }
                }
            }
        }
        Ok(())
    }

    /// ImagingCrop: the box's pixels, zeros where it goes beyond the picture, the palette kept.
    fn crop(&self, (x0, y0, x1, y1): (i64, i64, i64, i64)) -> Result<Core> {
        let (w, h) = ((x1 - x0).max(0) as usize, (y1 - y0).max(0) as usize);
        let mut out = Core {
            mode: self.mode,
            w,
            h,
            px: vec![0; w * h * self.mode.pixel_size()],
            palette: self.palette.clone(),
        };
        let (wi, hi) = (self.w as i64, self.h as i64);
        out.paste(self, (-x0, -y0, wi - x0, hi - y0), None)?;
        Ok(out)
    }

    /// Image._crop: the same, its size checked first.
    fn crop_checked(&self, b: (i64, i64, i64, i64)) -> Result<Core> {
        bomb_check((b.2 - b.0).abs(), (b.3 - b.1).abs())?;
        self.crop(b)
    }

    /// ImagingConvert to RGB or RGBA, as the frames take it (p2rgb, p2rgba, l2rgb, rgb2rgba).
    fn convert(&self, to: M) -> Result<Core> {
        let mut px = vec![0u8; self.len() * 4];
        let out = px.chunks_exact_mut(4);
        match (self.mode, to) {
            (M::P, M::Rgb | M::Rgba) => {
                let Some(palette) = &self.palette else {
                    return error("image has no palette");
                };
                let mut entries = *palette.entries;
                if to == M::Rgb {
                    for e in &mut entries {
                        e[3] = 255;
                    }
                }
                for (o, &i) in out.zip(&self.px) {
                    o.copy_from_slice(&entries[usize::from(i)]);
                }
            }
            (M::L, M::Rgb | M::Rgba) => {
                for (o, &v) in out.zip(&self.px) {
                    o.copy_from_slice(&[v, v, v, 255]);
                }
            }
            (M::Rgb, M::Rgba) => {
                for (o, p) in out.zip(self.px.chunks_exact(4)) {
                    o.copy_from_slice(&[p[0], p[1], p[2], 255]);
                }
            }
            (from, to) if from == to => px.copy_from_slice(&self.px),
            (from, to) => {
                return error(format!(
                    "conversion from {} to {} not supported",
                    from.name(),
                    to.name()
                ))
            }
        }
        Ok(Core {
            mode: to,
            w: self.w,
            h: self.h,
            px,
            palette: None,
        })
    }

    /// ImagingConvertTransparent from L (l2rgb, then rgbT2a): the grey `t` made clear.
    fn convert_transparent(&self, to: M, t: u8) -> Result<Core> {
        if self.mode != M::L || !matches!(to, M::Rgba | M::La) {
            return error(format!(
                "conversion from {} to {} not supported in convert_transparent",
                self.mode.name(),
                to.name()
            ));
        }
        let mut px = Vec::with_capacity(self.len() * 4);
        for &v in &self.px {
            px.extend_from_slice(&[v, v, v, if v == t { 0 } else { 255 }]);
        }
        Ok(Core {
            mode: to,
            w: self.w,
            h: self.h,
            px,
            palette: None,
        })
    }
}

/// The file read as Python's file object reads it: short at its end, never past it.
struct File<'a> {
    data: &'a [u8],
    at: usize,
}

impl<'a> File<'a> {
    fn read(&mut self, n: usize) -> &'a [u8] {
        let from = self.at.min(self.data.len());
        let to = from.saturating_add(n).min(self.data.len());
        self.at = to;
        &self.data[from..to]
    }

    /// GifImageFile.data: the next sub-block (None where its length is 0 or the file ends; a
    /// short one where the file ends within it).
    fn block(&mut self) -> Option<&'a [u8]> {
        match self.read(1) {
            [n] if *n != 0 => Some(self.read(usize::from(*n))),
            _ => None,
        }
    }

    /// `while self.data(): pass`
    fn skip_blocks(&mut self) {
        while self.block().is_some_and(|b| !b.is_empty()) {}
    }
}

/// _is_palette_needed: false for the grey ramp alone ((0, 0, 0), (1, 1, 1), ...). Python's
/// chained comparison stops at the first value that differs; reading past a short last entry
/// is an IndexError.
fn is_palette_needed(p: &[u8]) -> Result<bool> {
    for i in (0..p.len()).step_by(3) {
        let a = p[i];
        if usize::from(a) != i / 3 {
            return Ok(true);
        }
        let Some(&b) = p.get(i + 1) else {
            return short();
        };
        if a != b {
            return Ok(true);
        }
        let Some(&c) = p.get(i + 2) else {
            return short();
        };
        if b != c {
            return Ok(true);
        }
    }
    Ok(false)
}

/// A frame's own colour table: none, the grey ramp ("not needed"), or a palette.
#[derive(Clone)]
enum Local {
    None,
    NotNeeded,
    Needed(Vec<u8>),
}

/// What _seek reads of a frame: its graphic control (the last of its blocks counts), its place
/// and table, and where its LZW data begins.
#[derive(Clone)]
struct Header {
    transparency: Option<u8>,
    duration: Option<u32>,
    disposal: Option<u8>,
    extent: (i64, i64, i64, i64),
    interlace: bool,
    palette: Local,
    bits: u8,
    offset: usize,
}

/// One frame's blocks as _seek reads them, from where the file stands: None where it holds no
/// more frames (EOFError), an error where Python's reading fails on a short block.
fn parse(f: &mut File, frame: usize) -> Result<Option<Header>> {
    let mut s = f.read(1);
    if s.is_empty() || s == b";" {
        return Ok(None);
    }
    let mut h = Header {
        transparency: None,
        duration: None,
        disposal: None,
        extent: (0, 0, 0, 0),
        interlace: false,
        palette: Local::None,
        bits: 0,
        offset: 0,
    };
    loop {
        if s.is_empty() {
            s = f.read(1);
        }
        if s.is_empty() || s == b";" {
            return Ok(None); // ("image not found in GIF frame")
        }
        if s == b"!" {
            let label = f.read(1);
            let block = f.block();
            let Some(&label) = label.first() else {
                return short();
            };
            match (label, block) {
                (249, Some(b)) => {
                    // the graphic control extension
                    let Some(&flags) = b.first() else {
                        return short();
                    };
                    if flags & 1 != 0 {
                        let Some(&t) = b.get(3) else {
                            return short();
                        };
                        h.transparency = Some(t);
                    }
                    h.duration = Some((i16(b, 1)? * 10) as u32);
                    let disposal = (flags & 0b0001_1100) >> 2;
                    if disposal != 0 {
                        h.disposal = Some(disposal);
                    }
                }
                (254, mut block) => {
                    // a comment: its blocks, read to their end
                    while block.is_some_and(|b| !b.is_empty()) {
                        block = f.block();
                    }
                    s = &[];
                    continue;
                }
                (255, Some(b)) if frame == 0 && b.starts_with(b"NETSCAPE2.0") => {
                    let _loop = f.block();
                }
                _ => {}
            }
            f.skip_blocks();
        } else if s == b"," {
            // the image descriptor
            let d = f.read(9);
            let (x0, y0) = (i16(d, 0)?, i16(d, 2)?);
            h.extent = (x0, y0, x0 + i16(d, 4)?, y0 + i16(d, 6)?);
            let Some(&flags) = d.get(8) else {
                return short();
            };
            h.interlace = flags & 64 != 0;
            if flags & 128 != 0 {
                let p = f.read(3 << ((flags & 7) + 1));
                h.palette = if is_palette_needed(p)? {
                    Local::Needed(p.to_vec())
                } else {
                    Local::NotNeeded
                };
            }
            let Some(&bits) = f.read(1).first() else {
                return short();
            };
            h.bits = bits;
            h.offset = f.at;
            return Ok(Some(h));
        }
        s = &[];
    }
}

/// GifDecode.c on the file from the frame's data on, into the tile of `im` (one byte a pixel):
/// done once its last row is written (the rest unread); an error where Pillow's is (a code
/// beyond the table, a code size beyond 12) and where the file ends first (ImageFile.load's
/// "image file is truncated"). The file is given to it as ImageFile.load gives it, 64 KB more
/// each time it returns for more, and it goes on past an end code where there is more.
fn lzw(
    im: &mut Core,
    extent: (i64, i64, i64, i64),
    data: &[u8],
    bits: u8,
    interlace: bool,
    transparency: i32,
) -> Result<()> {
    let (x0, y0, x1, y1) = extent;
    if x0 < 0 || y0 < 0 || x1 <= x0 || y1 <= y0 || x1 > im.w as i64 || y1 > im.h as i64 {
        return error("tile cannot extend outside image");
    }
    if bits > 12 {
        return error("bad configuration");
    }
    let (xoff, yoff) = (x0 as usize, y0 as usize);
    let (xsize, ysize) = ((x1 - x0) as usize, (y1 - y0) as usize);
    let stride = im.w;
    let broken = || error("decoding error");
    let truncated = || error("image file is truncated");

    let clear = 1usize << bits;
    let end = clear + 1;
    let (mut pass, mut step) = if interlace { (1u8, 8usize) } else { (0, 1) };
    // 1: after a clear code; 2: its first code; 3: the codes after
    let mut state = 1u8;
    let (mut next, mut codesize, mut codemask) = (0usize, 0u32, 0usize);
    let mut buffer = vec![0u8; TABLE];
    let mut bufferindex = TABLE;
    let mut link = vec![0u16; TABLE];
    let mut table = vec![0u8; TABLE];
    let (mut lastcode, mut lastdata) = (0usize, 0u8);
    let (mut bitbuffer, mut bitcount, mut blocksize) = (0u32, 0u32, 0usize);
    let mut ptr = 0usize;
    let mut avail = data.len().min(MAX_BLOCK);
    let (mut x, mut y) = (0usize, 0usize);
    loop {
        if state == 1 {
            next = clear + 2;
            codesize = u32::from(bits) + 1;
            codemask = (1 << codesize) - 1;
            bufferindex = TABLE;
            state = 2;
        }
        // the bytes to write: the rest of the last string, or the first of the next code's
        let (from, one);
        if bufferindex < TABLE {
            from = bufferindex;
            one = false;
            bufferindex = TABLE;
        } else {
            while bitcount < codesize {
                if blocksize > 0 {
                    bitbuffer |= u32::from(data[ptr]) << bitcount;
                    ptr += 1;
                    blocksize -= 1;
                    bitcount += 8;
                } else if ptr >= avail || avail - ptr < usize::from(data[ptr]) + 1 {
                    // (a block is read whole or not at all: the decoder returns for more)
                    if avail == data.len() {
                        return truncated();
                    }
                    avail = (avail + MAX_BLOCK).min(data.len());
                } else {
                    blocksize = usize::from(data[ptr]);
                    ptr += 1;
                }
            }
            let mut c = bitbuffer as usize & codemask;
            bitbuffer >>= codesize;
            bitcount -= codesize;
            if c == clear {
                if state != 2 {
                    state = 1;
                }
                continue;
            }
            if c == end {
                // (the decoder returns, and is given more where the file has more)
                if avail == data.len() {
                    return truncated();
                }
                avail = (avail + MAX_BLOCK).min(data.len());
                continue;
            }
            if state == 2 {
                if c > clear {
                    return broken();
                }
                lastdata = c as u8;
                lastcode = c;
                state = 3;
            } else {
                let thiscode = c;
                if c > next {
                    return broken();
                }
                if c == next {
                    if bufferindex == 0 {
                        return broken();
                    }
                    bufferindex -= 1;
                    buffer[bufferindex] = lastdata;
                    c = lastcode;
                }
                while c >= clear {
                    if bufferindex == 0 || c >= TABLE {
                        return broken();
                    }
                    bufferindex -= 1;
                    buffer[bufferindex] = table[c];
                    c = usize::from(link[c]);
                }
                lastdata = c as u8;
                if next < TABLE {
                    table[next] = c as u8;
                    link[next] = lastcode as u16;
                    if next == codemask && codesize < 12 {
                        codesize += 1;
                        codemask = (1 << codesize) - 1;
                    }
                    next += 1;
                }
                lastcode = thiscode;
            }
            from = TABLE;
            one = true;
        }
        if y >= ysize {
            return error("buffer overrun when reading image file");
        }
        let string = if one {
            std::slice::from_ref(&lastdata)
        } else {
            &buffer[from..]
        };
        if transparency < 0 && x + string.len() < xsize {
            // (a string within the row, as GifDecode.c's memcpy writes it)
            let at = (y + yoff) * stride + xoff + x;
            im.px[at..at + string.len()].copy_from_slice(string);
            x += string.len();
            continue;
        }
        for &v in string {
            if transparency < 0 || i32::from(v) != transparency {
                im.px[(y + yoff) * stride + xoff + x] = v;
            }
            x += 1;
            if x >= xsize {
                // NEWLINE: the next row, of this pass or the next
                x = 0;
                y += step;
                while y >= ysize {
                    match pass {
                        1 => {
                            y = 4;
                            pass = 2;
                        }
                        2 => {
                            step = 4;
                            y = 2;
                            pass = 3;
                        }
                        3 => {
                            step = 2;
                            y = 1;
                            pass = 0;
                        }
                        _ => return Ok(()),
                    }
                }
            }
        }
    }
}

/// GifImageFile's state as the plugin keeps it between its frames.
struct Gif<'a> {
    data: &'a [u8],
    size: (i64, i64),
    background: Option<u8>,
    global: Option<Vec<u8>>,
    /// where the first frame's blocks begin
    rewind: usize,
    /// self._mode, as Python holds it
    mode: M,
    /// self._im
    im: Option<Core>,
    /// self.palette: its entries, and whether it is still to be put to the picture ("dirty")
    palette: Option<(Vec<u8>, bool)>,
    /// info["transparency"]
    transparency: Option<u8>,
    disposal_method: u8,
    dispose: Option<Core>,
    dispose_extent: (i64, i64, i64, i64),
    frame_palette: Option<Vec<u8>>,
    frame_transparency: Option<u8>,
    tile_transparency: i32,
}

impl<'a> Gif<'a> {
    /// _open: the screen, its palette where needed, and the first frame (its size grown to the
    /// first frame's as _seek(0) grows it, then checked as Image.open checks it).
    fn open(data: &'a [u8]) -> Result<(Gif<'a>, Header)> {
        let s = &data[..data.len().min(13)];
        if !(s.starts_with(b"GIF87a") || s.starts_with(b"GIF89a")) {
            return error("not a GIF file");
        }
        let size = (i16(s, 6)?, i16(s, 8)?);
        let Some(&flags) = s.get(10) else {
            return short();
        };
        let mut f = File { data, at: s.len() };
        let (mut background, mut global) = (None, None);
        if flags & 128 != 0 {
            let Some(&b) = s.get(11) else {
                return short();
            };
            background = Some(b);
            let p = f.read(3 << ((flags & 7) + 1));
            if is_palette_needed(p)? {
                global = Some(p.to_vec());
            }
        }
        let rewind = f.at;
        let Some(first) = parse(&mut f, 0)? else {
            return error("no more images in GIF file");
        };
        let mut gif = Gif {
            data,
            size,
            background,
            global,
            rewind,
            mode: M::L,
            im: None,
            palette: None,
            transparency: None,
            disposal_method: 0,
            dispose: None,
            dispose_extent: (0, 0, 0, 0),
            frame_palette: None,
            frame_transparency: None,
            tile_transparency: -1,
        };
        gif.seek(0, &first)?;
        bomb_check(gif.size.0, gif.size.1)?;
        Ok((gif, first))
    }

    /// The blocks of every frame after the first, as n_frames reads them (each frame's data
    /// skipped from its start).
    fn rest(&self, first: &Header) -> Result<Vec<Header>> {
        let mut f = File {
            data: self.data,
            at: self.rewind,
        };
        let mut all = vec![first.clone()];
        loop {
            f.at = all[all.len() - 1].offset;
            f.skip_blocks();
            match parse(&mut f, all.len())? {
                Some(h) => all.push(h),
                None => return Ok(all),
            }
        }
    }

    fn im(&mut self) -> Result<&mut Core> {
        self.im
            .as_mut()
            .ok_or_else(|| Error("the image is not loaded".into()))
    }

    /// _rgb: a palette index's colour in the frame's palette (index 0 for one beyond it), or
    /// its grey where the frame has none.
    fn rgb(&self, c: u8) -> Result<[u8; 3]> {
        let Some(p) = &self.frame_palette else {
            return Ok([c, c, c]);
        };
        let c = if usize::from(c) * 3 + 3 > p.len() {
            0
        } else {
            usize::from(c)
        };
        match p.get(c * 3..c * 3 + 3) {
            Some(s) => Ok([s[0], s[1], s[2]]),
            None => error("color must be int, or tuple of one, three or four elements"),
        }
    }

    /// _seek(n) once its blocks are read: the size grown, the last frame's disposal done, the
    /// first frame's mode or the change to RGB(A) after it, this frame's disposal made, its
    /// tile.
    fn seek(&mut self, n: usize, h: &Header) -> Result<()> {
        if n == 0 {
            self.disposal_method = 0;
            self.dispose = None;
        }
        if let Some(d) = h.disposal {
            self.disposal_method = d;
        }
        let (x0, y0, x1, y1) = h.extent;
        if x1 > self.size.0 || y1 > self.size.1 {
            let size = (x1.max(self.size.0), y1.max(self.size.1));
            bomb_check(size.0, size.1)?;
            self.size = size;
        }
        if let Some(d) = self.dispose.take() {
            if d.len() > 0 {
                let at = self.dispose_extent;
                self.im()?.paste(&d, at, None)?;
            }
        }
        self.frame_palette = match &h.palette {
            Local::Needed(p) => Some(p.clone()),
            Local::NotNeeded => None,
            Local::None => self.global.clone(),
        };
        self.frame_transparency = h.transparency;
        if n == 0 {
            self.mode = if self.frame_palette.is_some() {
                M::P
            } else {
                M::L
            };
            self.palette = match &h.palette {
                Local::Needed(p) => Some((p.clone(), true)),
                _ => self.global.clone().map(|g| (g, true)),
            };
        } else if self.mode == M::P {
            if let Some(t) = self.transparency.take() {
                let im = self.im()?;
                im.putpalettealpha(t)?;
                *im = im.convert(M::Rgba)?;
                self.mode = M::Rgba;
            } else {
                self.mode = M::Rgb;
                let im = self.im()?;
                *im = im.convert(M::Rgb)?;
            }
        }
        // this frame's disposal, done before the next
        self.dispose_extent = h.extent;
        if self.disposal_method >= 2 {
            let (w, ht) = (x1 - x0, y1 - y0);
            let rgb = matches!(self.mode, M::Rgb | M::Rgba);
            let (wu, hu) = (w as usize, ht as usize);
            if self.disposal_method == 2 {
                // the background: the transparency first
                bomb_check(w, ht)?;
                self.dispose = Some(match self.transparency.or(h.transparency) {
                    Some(c) if rgb => {
                        let [r, g, b] = self.rgb(c)?;
                        Core::fill(M::Rgba, wu, hu, [r, g, b, 0])
                    }
                    Some(c) => Core::fill(M::P, wu, hu, [c, 0, 0, 0]),
                    None => {
                        let c = self.background.unwrap_or(0);
                        if rgb {
                            let [r, g, b] = self.rgb(c)?;
                            Core::fill(M::Rgb, wu, hu, [r, g, b, 255])
                        } else {
                            Core::fill(M::P, wu, hu, [c, 0, 0, 0])
                        }
                    }
                });
            } else if let Some(im) = &self.im {
                // the picture as it was
                self.dispose = Some(im.crop_checked(h.extent)?);
            } else if let Some(t) = h.transparency {
                bomb_check(w, ht)?;
                self.dispose = Some(if rgb {
                    let [r, g, b] = self.rgb(t)?;
                    Core::fill(M::Rgba, wu, hu, [r, g, b, 0])
                } else {
                    Core::fill(M::P, wu, hu, [t, 0, 0, 0])
                });
            }
        }
        self.tile_transparency = -1;
        if let Some(t) = h.transparency {
            if n == 0 {
                self.transparency = Some(t);
            } else if !matches!(self.mode, M::Rgb | M::Rgba) {
                self.tile_transparency = i32::from(t);
            }
        }
        Ok(())
    }

    /// Image.load's palette: put to the picture where it is new.
    fn realize(&mut self) -> Result<()> {
        if let (Some(im), Some((raw, dirty))) = (&mut self.im, &mut self.palette) {
            if *dirty {
                im.putpalette(raw)?;
                *dirty = false;
            }
        }
        Ok(())
    }

    /// ImageFile.load of frame n: load_prepare, the tile decoded, load_end.
    fn load(&mut self, n: usize, h: &Header) -> Result<()> {
        self.realize()?;
        let laid = matches!(self.mode, M::Rgb | M::Rgba);
        if n != 0 && laid {
            if let Some(prev) = self.im.take_if(|im| im.len() > 0) {
                return self.lay(h, prev);
            }
        }
        // load_prepare, for the first frame and the frames of a picture in L (or P), decoded
        // on the picture itself
        let (w, ht) = (self.size.0 as usize, self.size.1 as usize);
        let temp = if self.frame_palette.is_some() {
            M::P
        } else {
            M::L
        };
        if n == 0 {
            if let Some(t) = self.frame_transparency {
                self.im = Some(Core::fill(temp, w, ht, [t, 0, 0, 0]));
            }
        } else if laid {
            // (a picture of no pixels counts as none)
            self.im = None;
            if let Some(p) = &self.frame_palette {
                let t = self.frame_transparency.unwrap_or(0);
                let mut im = Core::fill(M::P, w, ht, [t, 0, 0, 0]);
                im.putpalette(p)?;
                self.im = Some(im);
            }
        }
        if let Some(im) = &self.im {
            if (im.w, im.h) != (w, ht) {
                let mut expanded = Core::fill(im.mode, w, ht, [0; 4]);
                if let Some(p) = &self.frame_palette {
                    expanded.putpalette(p)?;
                }
                expanded.paste(im, (0, 0, im.w as i64, im.h as i64), None)?;
                self.im = Some(expanded);
            }
        }
        self.mode = temp;
        self.frame_palette = None;
        if self.im.is_none() {
            self.im = Some(Core::fill(self.mode, w, ht, [0; 4]));
        }
        if self.mode == M::P {
            self.realize()?;
        }
        let file = self.data;
        let data = &file[h.offset.min(file.len())..];
        let tile = self.tile_transparency;
        lzw(self.im()?, h.extent, data, h.bits, h.interlace, tile)?;
        // (load_end has nothing to do for these)
        self.realize()
    }

    /// A frame after the first laid on the picture in RGB or RGBA: load_prepare's picture of
    /// its own for it, the tile decoded, load_end laying it on. Of that picture load_end takes
    /// the frame's extent alone, so only the extent is made (the same bytes: the decoder writes
    /// nothing outside it). The palette is no longer new after the first frame (Image.load).
    fn lay(&mut self, h: &Header, mut prev: Core) -> Result<()> {
        let (w, ht) = (self.size.0 as usize, self.size.1 as usize);
        let (x0, y0, x1, y1) = h.extent;
        if x0 < 0 || y0 < 0 || x1 <= x0 || y1 <= y0 || x1 > self.size.0 || y1 > self.size.1 {
            return error("tile cannot extend outside image");
        }
        let (ew, eh) = ((x1 - x0) as usize, (y1 - y0) as usize);
        let mut im = match &self.frame_palette {
            Some(p) => {
                let t = self.frame_transparency.unwrap_or(0);
                let mut im = Core::fill(M::P, ew, eh, [t, 0, 0, 0]);
                im.putpalette(p)?;
                im
            }
            None => Core::fill(M::L, ew, eh, [0; 4]),
        };
        let mode = im.mode;
        self.mode = mode;
        self.frame_palette = None;
        let file = self.data;
        let data = &file[h.offset.min(file.len())..];
        let whole = (0, 0, ew as i64, eh as i64);
        lzw(
            &mut im,
            whole,
            data,
            h.bits,
            h.interlace,
            self.tile_transparency,
        )?;
        // load_end
        if (prev.w, prev.h) != (w, ht) {
            let mut expanded = if self.frame_transparency.is_some() {
                Core::fill(M::Rgba, w, ht, [0; 4])
            } else {
                let mut e = Core::fill(M::P, w, ht, [0; 4]);
                e.putpalette(&im.getpalette()?)?;
                e.convert(M::Rgb)?
            };
            expanded.paste(&prev, (0, 0, prev.w as i64, prev.h as i64), None)?;
            prev = expanded;
        }
        let frame = match self.frame_transparency {
            Some(t) if mode == M::L => im.convert_transparent(M::La, t)?,
            Some(t) => {
                im.putpalettealpha(t)?;
                im.convert(M::Rgba)?
            }
            None => im.convert(M::Rgb)?,
        };
        bomb_check(ew as i64, eh as i64)?; // (_crop's check, of the extent)
        self.mode = prev.mode;
        let masked = matches!(frame.mode, M::La | M::Rgba);
        prev.paste(&frame, h.extent, masked.then_some(&frame))?;
        self.im = Some(prev);
        Ok(())
    }

    /// Image.convert("RGBA") of the frame as Python holds it (its mode, info["transparency"]).
    fn rgba(&mut self) -> Result<Image> {
        let (mode, transparency) = (self.mode, self.transparency);
        let im = self.im()?;
        let out = match (mode, transparency) {
            (M::Rgba, _) => im.clone(),
            (M::L, Some(t)) => im.convert_transparent(M::Rgba, t)?,
            (M::P, Some(t)) => {
                im.putpalettealpha(t)?;
                im.convert(M::Rgba)?
            }
            _ => im.convert(M::Rgba)?,
        };
        Image::from_bytes(Mode::RGBA, out.w, out.h, out.px).map_err(|e| Error(e.to_string()))
    }

    /// The first frame as a picture of its own (ImageOps.exif_transpose's copy: the mode the C
    /// picture has, info["transparency"] kept).
    fn picture(&self) -> Result<Picture> {
        let im = self
            .im
            .as_ref()
            .ok_or_else(|| Error("the image is not loaded".into()))?;
        let kind = match im.mode {
            M::L => Kind::L,
            M::P => Kind::P,
            M::Rgb => Kind::RGB,
            M::Rgba => Kind::RGBA,
            M::La => Kind::LA,
        };
        let data = match im.mode {
            M::Rgb => im
                .px
                .chunks_exact(4)
                .flat_map(|p| [p[0], p[1], p[2]])
                .collect(),
            M::La => im.px.chunks_exact(4).flat_map(|p| [p[0], p[3]]).collect(),
            _ => im.px.clone(),
        };
        let transparency = self.transparency.map(|t| match kind {
            Kind::P => Transparency::Index(u32::from(t)),
            _ => Transparency::Grey(u32::from(t)),
        });
        Ok(Picture {
            kind,
            width: im.w,
            height: im.h,
            data,
            palette: im.palette.as_ref().map(|p| p.entries.to_vec()),
            transparency,
            exif: None,
            raw_exif: None,
            xmp: None,
            xmp_bytes: None,
        })
    }
}

/// The first frame as Image.open loads a still picture.
pub(crate) fn decode(data: &[u8]) -> Result<Picture> {
    let (mut gif, first) = Gif::open(data)?;
    gif.load(0, &first)?;
    gif.picture()
}

/// A frame of an animation as the app reads it: its info["duration"] (milliseconds, None where
/// the frame has none: a GIF's without a graphic control) and its picture in RGBA.
#[derive(Clone, Debug)]
pub struct Frame {
    pub duration: Option<f64>,
    pub image: Image,
}

/// Every frame of a GIF as the app reads an animation (render/moving.py's read_animation):
/// n_frames, the first frame converted to RGBA, then ImageSequence's frames one after the
/// other, each with its duration and converted to RGBA.
pub fn frames(data: &[u8]) -> Result<Vec<Frame>> {
    let (mut gif, first) = Gif::open(data)?;
    let all = gif.rest(&first)?;
    let mut out = Vec::with_capacity(all.len());
    for (n, h) in all.iter().enumerate() {
        if n > 0 {
            gif.seek(n, h)?;
        }
        gif.load(n, h)?;
        out.push(Frame {
            duration: h.duration.map(f64::from),
            image: gif.rgba()?,
        });
    }
    Ok(out)
}
