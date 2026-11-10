//! A frame as a PNG file (the virtual display's, devices/virtual.py): eight bits a channel,
//! not interlaced, each row unfiltered.

use std::io::Write;
use std::path::Path;

use flate2::write::ZlibEncoder;
use flate2::{Compression, Crc};
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

/// The PNG file of `image` (L, RGB or RGBA).
pub fn encode(image: &Image) -> Result<Vec<u8>, String> {
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
    let row = width * image.bands();
    let mut packed = ZlibEncoder::new(Vec::new(), Compression::fast());
    for line in image.as_bytes().chunks(row.max(1)).take(height) {
        packed.write_all(&[0]).map_err(|e| e.to_string())?;
        packed.write_all(line).map_err(|e| e.to_string())?;
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
