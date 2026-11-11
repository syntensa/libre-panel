//! A picture as a PNG file (the virtual display's frames, devices/virtual.py; the layer a
//! TURZX panel has over its video, and its single pictures): eight bits a channel, not
//! interlaced, each row filtered as Pillow's encoder filters it (ZipEncode.c: as it is, by
//! the row above, by the pixel before or by Paeth's rule, whichever leaves the least).

use std::io::Write;
use std::path::Path;

use flate2::write::ZlibEncoder;
pub use flate2::Compression;
use flate2::Crc;
use lp_pixels::{Image, Mode};

fn chunk(out: &mut Vec<u8>, kind: &[u8; 4], data: &[u8]) {
    out.extend_from_slice(&(data.len() as u32).to_be_bytes());
    out.extend_from_slice(kind);
    out.extend_from_slice(data);
    let mut crc = Crc::new();
    crc.update(kind);
    crc.update(data);
    out.extend_from_slice(&crc.sum().to_be_bytes());
}

/// The PNG file of `image` (L, RGB or RGBA), packed fast.
pub fn encode(image: &Image) -> Result<Vec<u8>, String> {
    encode_with(image, Compression::fast())
}

/// The PNG file of `image`, packed as `level` says.
pub fn encode_with(image: &Image, level: Compression) -> Result<Vec<u8>, String> {
    encode_filtered(image, level, false)
}

/// `image.save(fp, "PNG", optimize=True)`: packed as tightly as zlib packs, each row's
/// filter chosen among the average too (ZipEncode.c's optimize).
pub fn encode_optimized(image: &Image) -> Result<Vec<u8>, String> {
    encode_filtered(image, Compression::best(), true)
}

fn encode_filtered(image: &Image, level: Compression, average: bool) -> Result<Vec<u8>, String> {
    let colour_type = match image.mode() {
        Mode::L => 0u8,
        Mode::RGB => 2,
        Mode::RGBA => 6,
        other => return Err(format!("no PNG of a picture in {other:?}")),
    };
    let (width, height) = image.size();
    let mut out = b"\x89PNG\r\n\x1a\n".to_vec();
    let mut head = Vec::with_capacity(13);
    head.extend_from_slice(&(width as u32).to_be_bytes());
    head.extend_from_slice(&(height as u32).to_be_bytes());
    head.extend_from_slice(&[8, colour_type, 0, 0, 0]);
    chunk(&mut out, b"IHDR", &head);
    let (row, step) = (width * image.bands(), image.bands());
    let mut packed = ZlibEncoder::new(Vec::new(), level);
    let none = vec![0u8; row];
    let mut above: &[u8] = &none;
    let mut filtered = [(); 4].map(|()| vec![0u8; row + 1]);
    for line in image.as_bytes().chunks(row.max(1)).take(height) {
        // (how much a row leaves: its bytes as signed ones, their sizes added)
        let weight = |bytes: &[u8]| -> u64 {
            bytes
                .iter()
                .map(|&v| u64::from(if v < 128 { v } else { 255 - v + 1 }))
                .sum()
        };
        let mut least = weight(line);
        let mut chosen: Option<usize> = None;
        if least > 0 {
            // 2, up: tried first, for a row that is the one above
            let up = &mut filtered[0];
            up[0] = 2;
            for i in 0..row {
                up[i + 1] = line[i].wrapping_sub(above[i]);
            }
            let left = weight(&up[1..]);
            if left < least {
                (least, chosen) = (left, Some(0));
            }
        }
        if least > 0 {
            // 1, sub: by the pixel before
            let sub = &mut filtered[1];
            sub[0] = 1;
            for i in 0..row {
                let before = if i >= step { line[i - step] } else { 0 };
                sub[i + 1] = line[i].wrapping_sub(before);
            }
            let left = weight(&sub[1..]);
            if left < least {
                (least, chosen) = (left, Some(1));
            }
        }
        if average && least > 0 {
            // 3, average: of the pixel before and the one above
            let mean = &mut filtered[3];
            mean[0] = 3;
            for i in 0..row {
                let before = if i >= step {
                    u16::from(line[i - step])
                } else {
                    0
                };
                mean[i + 1] = line[i].wrapping_sub(((before + u16::from(above[i])) / 2) as u8);
            }
            let left = weight(&mean[1..]);
            if left < least {
                (least, chosen) = (left, Some(3));
            }
        }
        if least > 0 {
            // 4, Paeth: by the nearest of the pixel before, the one above and the one
            // above before
            let paeth = &mut filtered[2];
            paeth[0] = 4;
            for i in 0..row {
                let (a, c) = if i >= step {
                    (i32::from(line[i - step]), i32::from(above[i - step]))
                } else {
                    (0, 0)
                };
                let b = i32::from(above[i]);
                let (pa, pb, pc) = ((b - c).abs(), (a - c).abs(), (a + b - 2 * c).abs());
                let nearest = if pa <= pb && pa <= pc {
                    a
                } else if pb <= pc {
                    b
                } else {
                    c
                };
                paeth[i + 1] = line[i].wrapping_sub(nearest as u8);
            }
            if weight(&paeth[1..]) < least {
                chosen = Some(2);
            }
        }
        match chosen {
            Some(n) => packed.write_all(&filtered[n]),
            None => packed.write_all(&[0]).and_then(|()| packed.write_all(line)),
        }
        .map_err(|e| e.to_string())?;
        above = line;
    }
    let data = packed.finish().map_err(|e| e.to_string())?;
    chunk(&mut out, b"IDAT", &data);
    chunk(&mut out, b"IEND", &[]);
    Ok(out)
}

/// The picture written to `path`: to a file beside it first, which then takes its place, so
/// that whoever reads `path` never sees half a picture.
pub fn save(image: &Image, path: &Path) -> Result<(), String> {
    let data = encode(image)?;
    let failed = |e: std::io::Error| format!("cannot write {}: {e}", path.display());
    if let Some(folder) = path.parent().filter(|f| !f.as_os_str().is_empty()) {
        std::fs::create_dir_all(folder).map_err(failed)?;
    }
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(".tmp");
    let part = path.with_file_name(name);
    std::fs::write(&part, data).map_err(failed)?;
    std::fs::rename(&part, path).map_err(failed)
}
