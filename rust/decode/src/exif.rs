//! The EXIF orientation as Pillow 13.0 reads it (Image.getexif, Exif.load and
//! TiffImagePlugin's ImageFileDirectory_v2 on the first directory): the first Orientation tag
//! (274) of IFD0, its first value; XMP's tiff:Orientation where EXIF has none.

use crate::{bytes_repr, raise, Raised, Result};

/// TiffImagePlugin.PREFIXES: the headers ImageFileDirectory_v2 takes.
const PREFIXES: [&[u8; 4]; 6] = [
    b"MM\x00\x2a",
    b"II\x2a\x00",
    b"MM\x2a\x00",
    b"II\x00\x2a",
    b"MM\x00\x2b",
    b"II\x2b\x00",
];

/// The orientation in EXIF data (with or without "Exif\0\0" before it, as Exif.load strips one
/// or two of them): None where IFD0 has no Orientation tag (then XMP's counts), Some(0) where
/// it has one that is no whole number (bytes, text, a fraction: no turn, and XMP's does not
/// count); an error where Pillow fails on it (a header that is not TIFF's, too short to hold
/// its directory's place, or BigTIFF's, whose place Exif.load cannot read from 8 bytes).
pub fn orientation(data: &[u8]) -> Result<Option<u32>> {
    let mut d = data;
    for _ in 0..2 {
        match d.strip_prefix(b"Exif\0\0") {
            Some(rest) => d = rest,
            None => break,
        }
    }
    if d.is_empty() {
        return Ok(None);
    }
    let head = &d[..d.len().min(8)];
    if head.len() < 4 || !PREFIXES.iter().any(|p| head[..4] == p[..]) {
        let msg = format!("not a TIFF file (header {} not valid)", bytes_repr(head));
        return raise(Raised::SyntaxError, msg);
    }
    let big = &head[..2] == b"MM";
    // (the next directory's place: BigTIFF's eight bytes after the eight read, none; else the
    // four after the first four)
    if head[2] == 43 {
        return raise(Raised::StructError, "unpack requires a buffer of 8 bytes");
    }
    if head.len() < 8 {
        return raise(Raised::StructError, "unpack requires a buffer of 4 bytes");
    }
    let u16_at = |b: &[u8]| -> u16 {
        if big {
            u16::from_be_bytes([b[0], b[1]])
        } else {
            u16::from_le_bytes([b[0], b[1]])
        }
    };
    let u32_at = |b: &[u8]| -> u32 {
        if big {
            u32::from_be_bytes([b[0], b[1], b[2], b[3]])
        } else {
            u32::from_le_bytes([b[0], b[1], b[2], b[3]])
        }
    };
    let next = u32_at(&head[4..8]) as usize;
    let limit = d.len();
    // ImageFileDirectory_v2.load: the entries until the data ends (a short read stops it, a
    // value beyond the data too)
    let Some(count) = d.get(next..next + 2).map(u16_at) else {
        return Ok(None);
    };
    let mut total = 0usize;
    for i in 0..usize::from(count) {
        let at = next + 2 + i * 12;
        let Some(entry) = d.get(at..at + 12) else {
            return Ok(None);
        };
        let (tag, typ, n) = (
            u16_at(&entry[0..2]),
            u16_at(&entry[2..4]),
            u32_at(&entry[4..8]),
        );
        // (_load_dispatch: each type's size; others are skipped)
        let unit = match typ {
            1 | 2 | 6 | 7 => 1,
            3 | 8 => 2,
            4 | 9 | 11 | 13 => 4,
            5 | 10 | 12 | 16 => 8,
            _ => continue,
        };
        let size = n as usize * unit;
        let value: &[u8] = if size > 4 {
            let offset = u32_at(&entry[8..12]) as usize;
            total += size;
            if total > limit {
                continue;
            }
            match d.get(offset..offset + size) {
                Some(v) => v,
                None => return Ok(None), // (_safe_read's OSError: the directory read no further)
            }
        } else {
            &entry[8..8 + size]
        };
        if value.is_empty() {
            continue;
        }
        if tag != 274 {
            continue;
        }
        // the first Orientation read is the one Pillow keeps; its first value as Python
        // compares it with 2 to 8 (bytes and text never equal them: 0, no turn)
        let whole = |f: f64| {
            Some(if f.fract() == 0.0 && f >= 0.0 {
                f as u32
            } else {
                0
            })
        };
        return Ok(match typ {
            3 => Some(u32::from(u16_at(value))),
            8 => Some((u16_at(value) as i16).max(0) as u32),
            4 | 13 => Some(u32_at(value)),
            9 => Some((u32_at(value) as i32).max(0) as u32),
            6 => Some((value[0] as i8).max(0) as u32),
            16 => {
                let (hi, lo) = (u32_at(&value[..4]), u32_at(&value[4..8]));
                let v = if big {
                    (u64::from(hi) << 32) | u64::from(lo)
                } else {
                    (u64::from(lo) << 32) | u64::from(hi)
                };
                Some(u32::try_from(v).unwrap_or(u32::MAX))
            }
            5 | 10 => {
                let (num, den) = (u32_at(&value[..4]), u32_at(&value[4..8]));
                let (num, den) = if typ == 10 {
                    (f64::from(num as i32), f64::from(den as i32))
                } else {
                    (f64::from(num), f64::from(den))
                };
                if den == 0.0 {
                    Some(0) // (IFDRational's NaN)
                } else {
                    whole(num / den)
                }
            }
            11 => whole(f64::from(f32::from_bits(u32_at(value)))),
            12 => {
                let (hi, lo) = (u32_at(&value[..4]), u32_at(&value[4..8]));
                let bits = if big {
                    (u64::from(hi) << 32) | u64::from(lo)
                } else {
                    (u64::from(lo) << 32) | u64::from(hi)
                };
                whole(f64::from_bits(bits))
            }
            _ => Some(0),
        });
    }
    Ok(None)
}

/// The EXIF data of ImageMagick's "Raw profile type exif" text (Image.getexif: its lines from
/// the fourth on, joined, as bytes.fromhex reads them: white space skipped between the pairs,
/// not within one; the place of the first character it cannot take in the ValueError).
pub fn from_raw_profile(text: &str) -> Result<Vec<u8>> {
    let hex: String = text.split('\n').skip(3).collect();
    let refused = |at: usize| -> Result<Vec<u8>> {
        let msg = format!("non-hexadecimal number found in fromhex() arg at position {at}");
        raise(Raised::ValueError, msg)
    };
    // (text of more than ASCII: the first character beyond it, counted in characters)
    if let Some(at) = hex.chars().position(|c| !c.is_ascii()) {
        return refused(at);
    }
    let b = hex.as_bytes();
    let space = |c: u8| matches!(c, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c);
    let digit = |c: Option<&u8>| c.and_then(|&c| char::from(c).to_digit(16)).map(|v| v as u8);
    let mut out = Vec::with_capacity(b.len() / 2);
    let mut at = 0;
    while at < b.len() {
        if space(b[at]) {
            while at < b.len() && space(b[at]) {
                at += 1;
            }
            if at >= b.len() {
                break;
            }
        }
        let Some(top) = digit(b.get(at)) else {
            return refused(at);
        };
        let Some(bottom) = digit(b.get(at + 1)) else {
            return refused(at + 1);
        };
        out.push(top << 4 | bottom);
        at += 2;
    }
    Ok(out)
}

/// XMP's orientation (getexif: the first tiff:Orientation=" or tiff:Orientation> and the digit
/// after it).
pub fn xmp_orientation(xmp: &[u8]) -> Option<u32> {
    let bytes = xmp;
    let name = b"tiff:Orientation";
    let mut from = 0;
    while let Some(at) = find(&bytes[from..], name) {
        let rest = &bytes[from + at + name.len()..];
        let digit = match rest {
            [b'=', b'"', d, ..] | [b'>', d, ..] if d.is_ascii_digit() => Some(*d),
            _ => None,
        };
        if let Some(d) = digit {
            return Some(u32::from(d - b'0'));
        }
        from += at + 1;
    }
    None
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_orientation_is_read_from_either_byte_order() {
        let le = b"Exif\0\0II\x2a\x00\x08\x00\x00\x00\x01\x00\x12\x01\x03\x00\x01\x00\x00\x00\x06\x00\x00\x00";
        assert_eq!(orientation(le), Ok(Some(6)));
        let be =
            b"MM\x00\x2a\x00\x00\x00\x08\x00\x01\x01\x12\x00\x03\x00\x00\x00\x01\x00\x03\x00\x00";
        assert_eq!(orientation(be), Ok(Some(3)));
        assert!(orientation(b"Exif\0\0XX\x2a\x00").is_err());
        assert_eq!(xmp_orientation(b"<x tiff:Orientation=\"8\"/>"), Some(8));
        assert_eq!(
            xmp_orientation(b"<tiff:Orientation>5</tiff:Orientation>"),
            Some(5)
        );
    }
}
