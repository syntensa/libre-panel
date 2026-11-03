//! What Libre Panel and the engine say to each other, record by record (little endian).
//!
//! On the engine's stdin:
//!
//! ```text
//! "LPE1", kind: u8, 3 bytes nothing, a: u32, b: u32
//! kind 1, a frame:    a its number, b how many boxes; then each box: x0, y0, x1, y1 (u16) and
//!                     its pixels, three bytes each, row by row (the parts that may have changed)
//! kind 7, the same, planned: after the head the time it was drawn for (f64, seconds on the
//!                     main loop's clock), then the boxes; the panel gets it on its time
//! kind 2, bit rate:   a the cap in kbit/s, in the running stream
//! kind 3, keyframe:   the next picture a keyframe
//! with a panel of the engine's own (`panel`):
//! kind 4, a command:  a its number (the reply's), b what: 1 command, 2 ask, 3 query, 4 drain,
//!                     5 push, + the command's id * 256, + 65536 when an ask's reply must
//!                     echo it; then timeout_ms, reply_ms, the arguments' and the payload's
//!                     lengths (u32 each) and those bytes
//! kind 5, a stream:   a the panel's block size, b the length of the encoder's options (as
//!                     for `encode`, NUL apart), which follow
//! kind 6, its end:    the pictures not yet sent are dropped, the encoder closed
//! kind 8, a picture:  a the panel's block size, b its length: H.264 made elsewhere (ffmpeg's:
//!                     a video from its file, the screen), sent as it is, at once
//! a frame of layers (scene.rs), in a stream:
//! kind 9, its ground: a 0 a colour (b 3: R, G, B) or 1 a picture of the stream's size (b its
//!                     length: three bytes a pixel, row by row)
//! kind 10, a layer:   a its number, b the length of what follows: x, y (i32), w, h and how it
//!                     goes on (u32 each: 0 RGBA through its alpha, 1 RGB as it is, 2 RGB light
//!                     added), then its pixels, row by row
//! kind 11, a frame:   a its number, b how many layers; then the time it was drawn for (f64,
//!                     as kind 7's; NaN for none), how it is turned (u32: 0, or 180 for half a
//!                     turn), then the layers' numbers (u32 each), bottom first
//! ```
//!
//! On its stdout, with a panel of its own (`panel`): the same head, then b bytes:
//!
//! ```text
//! kind 64, ready:     where the panel is, as text
//! kind 65, a reply:   a the command's number; one byte (0 its reply, 1 none in time, 2 it
//!                     failed), then the reply's bytes or why it failed
//! kind 66, counts:    what the video path counted, as JSON
//! kind 67, failed:    the video path stopped: a 1 the link or the panel's replies, 2 a hung
//!                     decoder; why, as text
//! kind 68, waiting:   a 1 while frames wait for the panel to take pictures, 0 when that ends
//! ```

use std::io::{self, Read, Write};

pub const MAGIC: &[u8; 4] = b"LPE1";

pub type Boxes = Vec<(usize, usize, usize, usize, Vec<u8>)>;

pub enum Record {
    Frame {
        number: u32,
        boxes: Boxes,
        planned: Option<f64>,
    },
    Rate(u32),
    Keyframe,
    Command(Request),
    Stream {
        block: u32,
        options: Vec<String>,
    },
    End,
    Picture {
        block: u32,
        data: Vec<u8>,
    },
    Ground {
        colour: Option<[u8; 3]>,
        picture: Vec<u8>,
    },
    Layer {
        id: u32,
        x: i32,
        y: i32,
        w: usize,
        h: usize,
        how: u32,
        pixels: Vec<u8>,
    },
    Scene {
        number: u32,
        planned: Option<f64>,
        half: bool,
        order: Vec<u32>,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Op {
    Command,
    Ask,
    Query,
    Drain,
    Push,
}

#[derive(Debug)]
pub struct Request {
    pub id: u32,
    pub op: Op,
    pub cmd: u8,
    pub echo: bool,
    pub timeout_ms: u32,
    pub reply_ms: u32,
    pub args: Vec<u8>,
    pub payload: Vec<u8>,
}

fn invalid(what: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, what.to_string())
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

fn whole(input: &mut impl Read, buf: &mut [u8], what: &str) -> io::Result<()> {
    if buf.is_empty() || exact(input, buf)? {
        Ok(())
    } else {
        Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            what.to_string(),
        ))
    }
}

fn u16_at(b: &[u8], i: usize) -> usize {
    u16::from_le_bytes([b[i], b[i + 1]]) as usize
}

fn u32_at(b: &[u8], i: usize) -> u32 {
    u32::from_le_bytes([b[i], b[i + 1], b[i + 2], b[i + 3]])
}

/// The longest arguments and payload a command takes: a packet's arguments, a PNG of the
/// largest panel (the panel itself takes no more than 1 MiB).
const MOST_ARGS: usize = 512;
const MOST_PAYLOAD: usize = 4 << 20;
/// The largest picture of another encoder's taken (a keyframe of the 12.3" is under 1 MB).
const MOST_PICTURE: usize = 16 << 20;
/// The largest ground and layer taken (a 4K panel's ground is 25 MB, a layer its size 33 MB).
const MOST_LAYER: usize = 64 << 20;
/// The most layers a frame takes (Lumen's on the 12.3" are about a hundred).
const MOST_LAYERS: usize = 1 << 16;

fn boxes(input: &mut impl Read, count: u32) -> io::Result<Boxes> {
    let mut boxes = Vec::with_capacity(count.min(4096) as usize);
    for _ in 0..count {
        let mut corners = [0u8; 8];
        whole(input, &mut corners, "a frame cut short")?;
        let (x0, y0, x1, y1) = (
            u16_at(&corners, 0),
            u16_at(&corners, 2),
            u16_at(&corners, 4),
            u16_at(&corners, 6),
        );
        if x1 <= x0 || y1 <= y0 {
            return Err(invalid("an empty box"));
        }
        let mut pixels = vec![0u8; (x1 - x0) * (y1 - y0) * 3];
        whole(input, &mut pixels, "a box cut short")?;
        boxes.push((x0, y0, x1, y1, pixels));
    }
    Ok(boxes)
}

/// The next record, or None at the end of the input.
pub fn read(input: &mut impl Read) -> io::Result<Option<Record>> {
    let mut head = [0u8; 16];
    if !exact(input, &mut head)? {
        return Ok(None);
    }
    if &head[..4] != MAGIC {
        return Err(invalid("not a record of Libre Panel's"));
    }
    let a = u32_at(&head, 8);
    let b = u32_at(&head, 12);
    match head[4] {
        1 => Ok(Some(Record::Frame {
            number: a,
            boxes: boxes(input, b)?,
            planned: None,
        })),
        7 => {
            let mut when = [0u8; 8];
            whole(input, &mut when, "a frame cut short")?;
            let planned = f64::from_le_bytes(when);
            Ok(Some(Record::Frame {
                number: a,
                boxes: boxes(input, b)?,
                planned: planned.is_finite().then_some(planned),
            }))
        }
        2 => Ok(Some(Record::Rate(a))),
        3 => Ok(Some(Record::Keyframe)),
        4 => {
            let mut more = [0u8; 16];
            whole(input, &mut more, "a command cut short")?;
            let (args_len, payload_len) = (u32_at(&more, 8) as usize, u32_at(&more, 12) as usize);
            if args_len > MOST_ARGS || payload_len > MOST_PAYLOAD {
                return Err(invalid("a command too long"));
            }
            let op = match b & 0xFF {
                1 => Op::Command,
                2 => Op::Ask,
                3 => Op::Query,
                4 => Op::Drain,
                5 => Op::Push,
                _ => return Err(invalid("a command of no kind known")),
            };
            let mut args = vec![0u8; args_len];
            whole(input, &mut args, "a command cut short")?;
            let mut payload = vec![0u8; payload_len];
            whole(input, &mut payload, "a command cut short")?;
            Ok(Some(Record::Command(Request {
                id: a,
                op,
                cmd: (b >> 8) as u8,
                echo: b & 0x1_0000 != 0,
                timeout_ms: u32_at(&more, 0),
                reply_ms: u32_at(&more, 4),
                args,
                payload,
            })))
        }
        5 => {
            if b as usize > 4096 {
                return Err(invalid("a stream's options too long"));
            }
            let mut text = vec![0u8; b as usize];
            whole(input, &mut text, "a stream cut short")?;
            let text = String::from_utf8(text).map_err(|_| invalid("a stream's options"))?;
            Ok(Some(Record::Stream {
                block: a,
                options: text
                    .split('\0')
                    .filter(|s| !s.is_empty())
                    .map(String::from)
                    .collect(),
            }))
        }
        6 => Ok(Some(Record::End)),
        8 => {
            if b as usize > MOST_PICTURE {
                return Err(invalid("a picture too large"));
            }
            let mut data = vec![0u8; b as usize];
            whole(input, &mut data, "a picture cut short")?;
            Ok(Some(Record::Picture { block: a, data }))
        }
        9 => {
            let len = b as usize;
            match a {
                0 if len == 3 => {
                    let mut rgb = [0u8; 3];
                    whole(input, &mut rgb, "a ground cut short")?;
                    Ok(Some(Record::Ground {
                        colour: Some(rgb),
                        picture: Vec::new(),
                    }))
                }
                1 if len <= MOST_LAYER => {
                    let mut picture = vec![0u8; len];
                    whole(input, &mut picture, "a ground cut short")?;
                    Ok(Some(Record::Ground {
                        colour: None,
                        picture,
                    }))
                }
                _ => Err(invalid("a ground of no kind known")),
            }
        }
        10 => {
            let len = b as usize;
            if !(20..=MOST_LAYER + 20).contains(&len) {
                return Err(invalid("a layer too large"));
            }
            let mut geometry = [0u8; 20];
            whole(input, &mut geometry, "a layer cut short")?;
            let mut pixels = vec![0u8; len - 20];
            whole(input, &mut pixels, "a layer cut short")?;
            Ok(Some(Record::Layer {
                id: a,
                x: u32_at(&geometry, 0) as i32,
                y: u32_at(&geometry, 4) as i32,
                w: u32_at(&geometry, 8) as usize,
                h: u32_at(&geometry, 12) as usize,
                how: u32_at(&geometry, 16),
                pixels,
            }))
        }
        11 => {
            let count = b as usize;
            if count > MOST_LAYERS {
                return Err(invalid("a frame of too many layers"));
            }
            let mut more = [0u8; 12];
            whole(input, &mut more, "a frame cut short")?;
            let planned = f64::from_le_bytes(more[..8].try_into().expect("eight bytes"));
            let half = match u32_at(&more, 8) {
                0 => false,
                180 => true,
                _ => return Err(invalid("a frame turned otherwise than 0 or 180")),
            };
            let mut ids = vec![0u8; count * 4];
            whole(input, &mut ids, "a frame cut short")?;
            Ok(Some(Record::Scene {
                number: a,
                planned: planned.is_finite().then_some(planned),
                half,
                order: ids
                    .chunks_exact(4)
                    .map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
                    .collect(),
            }))
        }
        kind => Err(invalid(&format!("a record of kind {kind}"))),
    }
}

pub const READY: u8 = 64;
pub const REPLY: u8 = 65;
pub const COUNTS: u8 = 66;
pub const FAILED: u8 = 67;
pub const WAITING: u8 = 68;

/// A record for Libre Panel: the head, then `data`.
pub fn write(out: &mut impl Write, kind: u8, a: u32, data: &[&[u8]]) -> io::Result<()> {
    let len: usize = data.iter().map(|part| part.len()).sum();
    let mut head = [0u8; 16];
    head[..4].copy_from_slice(MAGIC);
    head[4] = kind;
    head[8..12].copy_from_slice(&a.to_le_bytes());
    head[12..16].copy_from_slice(&(len as u32).to_le_bytes());
    out.write_all(&head)?;
    for part in data {
        out.write_all(part)?;
    }
    out.flush()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn head(kind: u8, a: u32, b: u32) -> Vec<u8> {
        let mut h = MAGIC.to_vec();
        h.extend([kind, 0, 0, 0]);
        h.extend(a.to_le_bytes());
        h.extend(b.to_le_bytes());
        h
    }

    #[test]
    fn records_in_turn() {
        let mut data = head(7, 3, 1);
        data.extend(12.5f64.to_le_bytes());
        data.extend([0, 0, 0, 0, 1, 0, 1, 0]); // a box of one pixel
        data.extend([1, 2, 3]);
        data.extend(head(4, 9, 3 | (122 << 8)));
        for n in [400u32, 0, 0, 0] {
            data.extend(n.to_le_bytes());
        }
        let options = b"--size\x0016x16";
        data.extend(head(5, 202_752, options.len() as u32));
        data.extend(options);
        data.extend(head(6, 0, 0));
        let mut input = data.as_slice();
        match read(&mut input).unwrap() {
            Some(Record::Frame {
                number,
                boxes,
                planned,
            }) => {
                assert_eq!((number, planned), (3, Some(12.5)));
                assert_eq!(boxes, vec![(0, 0, 1, 1, vec![1, 2, 3])]);
            }
            _ => panic!("not the frame"),
        }
        match read(&mut input).unwrap() {
            Some(Record::Command(r)) => {
                assert_eq!((r.id, r.op, r.cmd, r.echo), (9, Op::Query, 122, false));
                assert_eq!(r.timeout_ms, 400);
            }
            _ => panic!("not the command"),
        }
        match read(&mut input).unwrap() {
            Some(Record::Stream { block, options }) => {
                assert_eq!(block, 202_752);
                assert_eq!(options, vec!["--size", "16x16"]);
            }
            _ => panic!("not the stream"),
        }
        assert!(matches!(read(&mut input).unwrap(), Some(Record::End)));
        assert!(read(&mut input).unwrap().is_none());
    }

    #[test]
    fn a_scene_its_ground_layers_and_frame() {
        let mut input = head(9, 0, 3);
        input.extend([1, 2, 3]);
        let mut layer = head(10, 7, 20 + 4);
        for v in [(-3i32) as u32, 5, 1, 1, 0] {
            layer.extend(v.to_le_bytes());
        }
        layer.extend([9, 9, 9, 255]);
        input.extend(layer);
        input.extend(head(11, 12, 2));
        input.extend(2.5f64.to_le_bytes());
        input.extend(180u32.to_le_bytes());
        input.extend(7u32.to_le_bytes());
        input.extend(3u32.to_le_bytes());
        let mut input = input.as_slice();
        assert!(matches!(
            read(&mut input).unwrap(),
            Some(Record::Ground {
                colour: Some([1, 2, 3]),
                ..
            })
        ));
        match read(&mut input).unwrap() {
            Some(Record::Layer {
                id,
                x,
                y,
                w,
                h,
                how,
                pixels,
            }) => {
                assert_eq!((id, x, y, w, h, how), (7, -3, 5, 1, 1, 0));
                assert_eq!(pixels, vec![9, 9, 9, 255]);
            }
            _ => panic!("not the layer"),
        }
        match read(&mut input).unwrap() {
            Some(Record::Scene {
                number,
                planned,
                half,
                order,
            }) => {
                assert_eq!((number, planned, half), (12, Some(2.5), true));
                assert_eq!(order, vec![7, 3]);
            }
            _ => panic!("not the frame"),
        }
    }

    #[test]
    fn a_record_cut_short_or_of_another_kind_is_an_error() {
        let mut cut = head(4, 1, 1);
        cut.extend([0u8; 5]);
        assert!(read(&mut cut.as_slice()).is_err());
        assert!(read(&mut head(99, 0, 0).as_slice()).is_err());
        assert!(read(&mut head(9, 2, 3).as_slice()).is_err()); // (a ground of no kind known)
        assert!(read(&mut head(11, 0, 1 << 20).as_slice()).is_err());
        let mut long = head(4, 1, 1);
        for n in [0u32, 0, 10_000, 0] {
            long.extend(n.to_le_bytes());
        }
        assert!(read(&mut long.as_slice()).is_err());
    }
}
