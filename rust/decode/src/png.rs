//! PNG as Pillow 13.0's PngImagePlugin opens it. Its chunks are read here as PngStream reads
//! them: their checksums checked up to the image data (only there), PLTE taken before it,
//! tRNS, eXIf and the texts wherever they are (load_end reads those after it, the later one
//! winning). The pixels come from the `png` crate given only the chunks that make them; they
//! are put in the mode of the bit depth and colour type (_MODES) as Pillow's raw modes unpack
//! them: grey of 1, 2 and 4 bits spread to 0..255, 16 bits cut to their high byte, I;16 kept.

use std::io::{Cursor, Read};

use crate::{error, Error, Kind, Picture, Result, Transparency};

/// Image.MAX_IMAGE_PIXELS twice over: where Pillow refuses a picture as a bomb.
const BOMB: u64 = 2 * (1024 * 1024 * 1024 / 4 / 3);

fn u32_at(b: &[u8], at: usize) -> u32 {
    u32::from_be_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

/// A chunk: its type, its data.
struct Chunk<'a> {
    kind: [u8; 4],
    data: &'a [u8],
}

fn crc32(kind: &[u8], data: &[u8]) -> u32 {
    let mut crc = !0u32;
    for &byte in kind.iter().chain(data) {
        crc ^= u32::from(byte);
        for _ in 0..8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0xEDB8_8320
            } else {
                crc >> 1
            };
        }
    }
    !crc
}

/// The file's chunks up to IEND, the checksums of those before the image data checked.
fn chunks(file: &[u8]) -> Result<Vec<Chunk<'_>>> {
    let mut out = Vec::new();
    let mut at = 8;
    let mut seen_data = false;
    while at + 8 <= file.len() {
        let length = u32_at(file, at) as usize;
        let kind: [u8; 4] = file[at + 4..at + 8].try_into().unwrap();
        if !kind.iter().all(|c| c.is_ascii_alphanumeric() || *c == b'_') {
            return error("broken PNG file (chunk name)");
        }
        let start = at + 8;
        let Some(data) = file.get(start..start + length) else {
            return error("Truncated File Read");
        };
        if &kind == b"IDAT" || &kind == b"fdAT" {
            seen_data = true;
        } else if !seen_data {
            let Some(crc) = file.get(start + length..start + length + 4) else {
                return error("broken PNG file (incomplete checksum)");
            };
            if u32_at(crc, 0) != crc32(&kind, data) {
                return error("broken PNG file (bad header checksum)");
            }
        }
        out.push(Chunk { kind, data });
        if &kind == b"IEND" {
            break;
        }
        at = start + length + 4;
    }
    Ok(out)
}

/// chunk_tRNS: P's single clear index (one 0 among 255s) or its alphas; 1's, L's and I;16's
/// grey; RGB's colour (each the two bytes the chunk gives, as i16 reads them); other modes
/// none.
fn transparency(kind: Kind, t: &[u8]) -> Result<Option<Transparency>> {
    let i16 = |at: usize| u32::from(u16::from_be_bytes([t[at], t[at + 1]]));
    Ok(match kind {
        Kind::P => {
            let zeros = t.iter().filter(|&&v| v == 0).count();
            if zeros == 1 && t.iter().all(|&v| v == 0 || v == 255) {
                Some(Transparency::Index(
                    t.iter().position(|&v| v == 0).unwrap() as u32
                ))
            } else {
                Some(Transparency::Alphas(t.to_vec()))
            }
        }
        Kind::Bilevel | Kind::L | Kind::I16 => {
            if t.len() < 2 {
                return error("Truncated tRNS chunk");
            }
            let v = i16(0);
            Some(Transparency::Grey(match kind {
                Kind::Bilevel if v != 0 => 255,
                Kind::Bilevel => 0,
                _ => v,
            }))
        }
        Kind::RGB => {
            if t.len() < 6 {
                return error("Truncated tRNS chunk");
            }
            Some(Transparency::Colour(i16(0), i16(2), i16(4)))
        }
        _ => None,
    })
}

/// zlib's data inflated (a text chunk's).
fn inflate(data: &[u8]) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    flate2::read::ZlibDecoder::new(data)
        .read_to_end(&mut out)
        .ok()?;
    Some(out)
}

/// A text chunk's keyword and text as PngStream decodes it (tEXt and zTXt in Latin-1, iTXt in
/// UTF-8); None where Pillow leaves it out.
fn text(chunk: &Chunk) -> Option<(String, String)> {
    let latin = |b: &[u8]| -> String { b.iter().map(|&c| char::from(c)).collect() };
    let split = |b: &[u8]| -> (Vec<u8>, Vec<u8>) {
        match b.iter().position(|&c| c == 0) {
            Some(i) => (b[..i].to_vec(), b[i + 1..].to_vec()),
            None => (b.to_vec(), Vec::new()),
        }
    };
    match &chunk.kind {
        b"tEXt" => {
            let (k, v) = split(chunk.data);
            (!k.is_empty()).then(|| (latin(&k), latin(&v)))
        }
        b"zTXt" => {
            let (k, v) = split(chunk.data);
            if k.is_empty() || v.first().copied().unwrap_or(0) != 0 {
                return None;
            }
            let text = inflate(v.get(1..).unwrap_or(&[]))?;
            Some((latin(&k), latin(&text)))
        }
        b"iTXt" => {
            let i = chunk.data.iter().position(|&c| c == 0)?;
            let (k, r) = (&chunk.data[..i], &chunk.data[i + 1..]);
            if r.len() < 2 {
                return None;
            }
            let (cf, cm, r) = (r[0], r[1], &r[2..]);
            let mut parts = r.splitn(3, |&c| c == 0);
            let (_lang, _tk, v) = (parts.next()?, parts.next()?, parts.next()?);
            let v = if cf != 0 {
                if cm != 0 {
                    return None;
                }
                inflate(v)?
            } else {
                v.to_vec()
            };
            Some((latin(k), String::from_utf8(v).ok()?))
        }
        _ => None,
    }
}

/// The picture's first image, as Image.open and load give it.
pub fn decode(file: &[u8]) -> Result<Picture> {
    let all = chunks(file)?;
    let Some(header) = all.first().filter(|c| &c.kind == b"IHDR") else {
        return error("cannot identify image file");
    };
    if header.data.len() < 13 {
        return error("Truncated IHDR chunk");
    }
    let (width, height) = (
        u32_at(header.data, 0) as usize,
        u32_at(header.data, 4) as usize,
    );
    if width as u64 * height as u64 > BOMB {
        return error("decompression bomb");
    }
    let (depth, colour) = (header.data[8], header.data[9]);
    let (kind, raw) = match (depth, colour) {
        (1, 0) => (Kind::Bilevel, "1"),
        (2, 0) => (Kind::L, "L;2"),
        (4, 0) => (Kind::L, "L;4"),
        (8, 0) => (Kind::L, "L"),
        (16, 0) => (Kind::I16, "I;16B"),
        (8, 2) => (Kind::RGB, "RGB"),
        (16, 2) => (Kind::RGB, "RGB;16B"),
        (1, 3) => (Kind::P, "P;1"),
        (2, 3) => (Kind::P, "P;2"),
        (4, 3) => (Kind::P, "P;4"),
        (8, 3) => (Kind::P, "P"),
        (8, 4) => (Kind::LA, "LA"),
        (16, 4) => (Kind::RGBA, "LA;16B"),
        (8, 6) => (Kind::RGBA, "RGBA"),
        (16, 6) => (Kind::RGBA, "RGBA;16B"),
        _ => return error("cannot identify image file"),
    };
    // what the chunks say, in their order; the image data's first run is what Pillow reads
    let (mut palette, mut transparent, mut exif) = (None, None, None);
    let (mut raw_exif, mut xmp) = (None, None);
    let mut image: Vec<&Chunk> = Vec::new();
    let (mut in_data, mut after_data) = (false, false);
    for chunk in &all[1..] {
        if &chunk.kind == b"IDAT" {
            if !after_data {
                image.push(chunk);
                in_data = true;
            }
            continue;
        }
        if in_data {
            after_data = true;
            in_data = false;
        }
        match &chunk.kind {
            b"PLTE" if !after_data && kind == Kind::P => palette = Some(chunk.data),
            b"tRNS" => {
                if let Some(t) = transparency(kind, chunk.data)? {
                    transparent = Some(t);
                }
            }
            b"eXIf" => {
                let mut v = b"Exif\0\0".to_vec();
                v.extend_from_slice(chunk.data);
                exif = Some(v);
            }
            _ => match text(chunk) {
                Some((k, v)) if k == "Raw profile type exif" => raw_exif = Some(v),
                Some((k, v)) if k == "XML:com.adobe.xmp" => xmp = Some(v),
                _ => {}
            },
        }
    }
    if image.is_empty() {
        return error("image file is truncated");
    }
    // the chunks that make the pixels, for the png crate (their checksums made anew: Pillow
    // reads the image data without checking them)
    let mut stream = b"\x89PNG\r\n\x1a\n".to_vec();
    let mut put = |kind: &[u8], data: &[u8]| {
        stream.extend_from_slice(&(data.len() as u32).to_be_bytes());
        stream.extend_from_slice(kind);
        stream.extend_from_slice(data);
        stream.extend_from_slice(&crc32(kind, data).to_be_bytes());
    };
    put(b"IHDR", &header.data[..13]);
    if let Some(p) = palette {
        put(b"PLTE", p);
    } else if kind == Kind::P {
        put(b"PLTE", &[0, 0, 0]); // (no palette: every index black, as Pillow's is)
    }
    for chunk in &image {
        put(b"IDAT", chunk.data);
    }
    put(b"IEND", b"");
    let fail = |e: png::DecodingError| Error(e.to_string());
    let mut decoder = png::Decoder::new(Cursor::new(&stream[..]));
    decoder.set_transformations(png::Transformations::IDENTITY);
    let mut reader = decoder.read_info().map_err(fail)?;
    let size = reader
        .output_buffer_size()
        .ok_or_else(|| Error("too large".into()))?;
    let mut rows = vec![0u8; size];
    let out = reader.next_frame(&mut rows).map_err(fail)?;
    let mut pixels = Vec::with_capacity(width * height * kind.bytes());
    for y in 0..height {
        let row = &rows[y * out.line_size..(y + 1) * out.line_size];
        unpack(raw, row, width, &mut pixels);
    }
    let palette = (kind == Kind::P).then(|| {
        let mut entries = vec![[0u8, 0, 0, 255]; 256];
        let plte = palette.unwrap_or(&[]);
        for (entry, rgb) in entries.iter_mut().zip(plte.chunks_exact(3)) {
            entry[..3].copy_from_slice(rgb);
        }
        entries
    });
    Ok(Picture {
        kind,
        width,
        height,
        data: pixels,
        palette,
        transparency: transparent,
        exif,
        raw_exif,
        xmp,
    })
}

/// A row of samples as Pillow's unpacker for `raw` puts them in its mode.
fn unpack(raw: &str, row: &[u8], width: usize, out: &mut Vec<u8>) {
    let bits = |n: usize, bits: u32| -> u8 {
        let per = 8 / bits as usize;
        let byte = row[n / per];
        let shift = 8 - bits * (n % per + 1) as u32;
        (byte >> shift) & ((1u8 << bits) - 1)
    };
    match raw {
        "1" => out.extend((0..width).map(|x| if bits(x, 1) != 0 { 255 } else { 0 })),
        "L;2" => out.extend((0..width).map(|x| bits(x, 2) * 0x55)),
        "L;4" => out.extend((0..width).map(|x| bits(x, 4) * 0x11)),
        "P;1" => out.extend((0..width).map(|x| bits(x, 1))),
        "P;2" => out.extend((0..width).map(|x| bits(x, 2))),
        "P;4" => out.extend((0..width).map(|x| bits(x, 4))),
        "L" | "P" => out.extend_from_slice(&row[..width]),
        "I;16B" => {
            for px in row[..width * 2].chunks_exact(2) {
                out.extend_from_slice(&[px[1], px[0]]);
            }
        }
        "RGB" => out.extend_from_slice(&row[..width * 3]),
        "RGB;16B" => {
            for px in row[..width * 6].chunks_exact(6) {
                out.extend_from_slice(&[px[0], px[2], px[4]]);
            }
        }
        "LA" => out.extend_from_slice(&row[..width * 2]),
        "LA;16B" => {
            for px in row[..width * 4].chunks_exact(4) {
                out.extend_from_slice(&[px[0], px[0], px[0], px[2]]);
            }
        }
        "RGBA" => out.extend_from_slice(&row[..width * 4]),
        "RGBA;16B" => {
            for px in row[..width * 8].chunks_exact(8) {
                out.extend_from_slice(&[px[0], px[2], px[4], px[6]]);
            }
        }
        _ => unreachable!("a raw mode of _MODES"),
    }
}
