//! What Libre Panel sends the engine on its stdin, record by record (little endian):
//!
//! ```text
//! "LPE1", kind: u8, 3 bytes nothing, a: u32, b: u32
//! kind 1, a frame:   a its number, b how many boxes; then each box: x0, y0, x1, y1 (u16) and
//!                    its pixels, three bytes each, row by row (the parts that may have changed)
//! kind 2, bit rate:  a the cap in kbit/s, in the running stream
//! kind 3, keyframe:  the next picture a keyframe
//! ```

use std::io::{self, Read};

pub const MAGIC: &[u8; 4] = b"LPE1";

pub enum Record {
    Frame {
        number: u32,
        boxes: Vec<(usize, usize, usize, usize, Vec<u8>)>,
    },
    Rate(u32),
    Keyframe,
}

fn exact(input: &mut impl Read, buf: &mut [u8]) -> io::Result<bool> {
    let mut got = 0;
    while got < buf.len() {
        match input.read(&mut buf[got..]) {
            Ok(0) if got == 0 => return Ok(false),
            Ok(0) => {
                return Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "a record cut short",
                ))
            }
            Ok(n) => got += n,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
            Err(e) => return Err(e),
        }
    }
    Ok(true)
}

fn u16_at(b: &[u8], i: usize) -> usize {
    u16::from_le_bytes([b[i], b[i + 1]]) as usize
}

/// The next record, or None at the end of the input.
pub fn read(input: &mut impl Read) -> io::Result<Option<Record>> {
    let mut head = [0u8; 16];
    if !exact(input, &mut head)? {
        return Ok(None);
    }
    if &head[..4] != MAGIC {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "not a record of Libre Panel's",
        ));
    }
    let a = u32::from_le_bytes([head[8], head[9], head[10], head[11]]);
    let b = u32::from_le_bytes([head[12], head[13], head[14], head[15]]);
    match head[4] {
        1 => {
            let mut boxes = Vec::with_capacity(b as usize);
            for _ in 0..b {
                let mut corners = [0u8; 8];
                if !exact(input, &mut corners)? {
                    return Err(io::Error::new(
                        io::ErrorKind::UnexpectedEof,
                        "a frame cut short",
                    ));
                }
                let (x0, y0, x1, y1) = (
                    u16_at(&corners, 0),
                    u16_at(&corners, 2),
                    u16_at(&corners, 4),
                    u16_at(&corners, 6),
                );
                if x1 <= x0 || y1 <= y0 {
                    return Err(io::Error::new(io::ErrorKind::InvalidData, "an empty box"));
                }
                let mut pixels = vec![0u8; (x1 - x0) * (y1 - y0) * 3];
                if !exact(input, &mut pixels)? {
                    return Err(io::Error::new(
                        io::ErrorKind::UnexpectedEof,
                        "a box cut short",
                    ));
                }
                boxes.push((x0, y0, x1, y1, pixels));
            }
            Ok(Some(Record::Frame { number: a, boxes }))
        }
        2 => Ok(Some(Record::Rate(a))),
        3 => Ok(Some(Record::Keyframe)),
        kind => Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("a record of kind {kind}"),
        )),
    }
}
