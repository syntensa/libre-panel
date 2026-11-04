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
//! kind 12, a ring:    a its number, b the length of what follows: its canvas's side and the
//!                     light's core (u32 each), the gauge's width and groove, its start and end
//!                     (degrees) and the halo (f64 each), its colour (RGBA), then the groove and
//!                     the groove with the whole sweep lit (RGBA each, the canvas): ring.rs
//! kind 13, a layer of a ring: a its number, b 24: the ring's number, x, y (u32, i32, i32),
//!                     nothing (u32), the share of its sweep lit (f64)
//! kind 14, a source:  a its number, b the length of what follows: w, h (u32 each), then its
//!                     pixels (RGBA), row by row: what layers of kind 15 are cut from (a
//!                     graph's strip), kept while a layer of the frame before is cut from it
//! kind 15, a layer cut from sources: a its number, b the length of what follows: x, y (i32),
//!                     w, h and how many parts (u32 each), then each part: its source (u32),
//!                     where it begins in the source and in the layer (x, y: i32 each), w, h
//!                     (u32 each); RGBA through its alpha, transparent where no part lies,
//!                     each part pasted over those before it with nothing where it lies off
//!                     its source (as Pillow crops and pastes): scene.rs's `view`
//! kind 16, a globe:   a its number, b the length of what follows: its size, its map's width,
//!                     how many pixels of its disc and of its rim (u32 each), then for each of
//!                     the disc's its place (y * size + x), its map's column at step 0 and its
//!                     row times the map's width, for each of the rim's its place, then their
//!                     alpha (u32 each, the alphas u8): globe.rs
//! kind 17, a layer of a globe: a its number, b the length of what follows: the globe's
//!                     number, its map's source, its air's source (0: none), the step of its
//!                     turn (u32 each), x, y (i32), its mark's w and h (u32, 0 for none) and
//!                     where it lies on the globe (i32 each), then the mark's pixels (RGBA)
//! kind 18, a layer of one colour: a its number, b 20: x, y (i32), w, h (u32 each), its colour
//!                     (RGBA): a card's inside, through its alpha (scene.rs's Layer::filled)
//! kind 19, a layer laid from sources: a its number, b the length of what follows: x, y
//!                     (i32), w, h, how many parts and how many sources it keeps (u32 each),
//!                     then each part: its source (u32) and where its top left lies on the
//!                     layer (x, y: i32 each), then the sources it keeps (u32 each); RGBA
//!                     through its alpha, each part laid over those before it on a clear
//!                     picture as Pillow's alpha_composite lays it, the kept sources staying
//!                     while it does (a fan's ring and rotor, the rotor's other positions):
//!                     scene.rs's `laid`
//! kind 20, a source grown from another: a its number, b the length of what follows: w, h,
//!                     the other's number, the other's column its first columns begin at, and
//!                     how many of them (u32 each), then the pixels of the rest of its columns
//!                     (RGBA), row by row: a source of kind 14 as it would come, its first
//!                     columns the other's (a graph's strip grown from the one before), the
//!                     other kept while a layer of the frame before is cut from it:
//!                     scene.rs's `grown`
//! kind 21, a lit bar: a its number, b the length of what follows: its length, thickness, room
//!                     round it, its light's thickness (u32 each) and top (i32), how it lies
//!                     (u32: 1 upright, + 2 filling from the far end), its segments, its pill's
//!                     first and last columns kept and how many short pills (u32 each), its
//!                     glow's scale (u32, 0: no glow), the bar's x, y and the glow area's x, y
//!                     (i32 each), w, h (u32 each), nothing (u32), the halo, the glow's radius,
//!                     strength, rise, tail (f64 each), its seven steps' shares and whites
//!                     (f64 each); then the groove on its canvas (RGBA, lying), the pill's first
//!                     columns, its middle column and its last columns, then the short pills,
//!                     1, 2, ... long (a level a pixel each, row by row): bar.rs
//! kind 22, a layer of a lit bar: a its number, b the length of what follows: the bar's number,
//!                     x, y (u32, i32, i32), the number of its glow's layer (u32, 0: none),
//!                     its colour and the colour its gradient runs to (RGBA each), whether it
//!                     has one and how many spans are lit (u32 each), then each span: from, to
//!                     along the bar (f64 each)
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
    RingBase {
        id: u32,
        side: usize,
        geometry: crate::ring::Geometry,
        groove: Vec<u8>,
        lit: Vec<u8>,
    },
    Ring {
        id: u32,
        ring: u32,
        x: i32,
        y: i32,
        frac: f64,
    },
    Source {
        id: u32,
        w: usize,
        h: usize,
        pixels: Vec<u8>,
    },
    Grown {
        id: u32,
        w: usize,
        h: usize,
        from: u32,
        shift: usize,
        keep: usize,
        rest: Vec<u8>,
    },
    View {
        id: u32,
        x: i32,
        y: i32,
        w: usize,
        h: usize,
        parts: Vec<crate::scene::Part>,
    },
    Globe {
        id: u32,
        tables: crate::globe::Tables,
    },
    Laid {
        id: u32,
        x: i32,
        y: i32,
        w: usize,
        h: usize,
        parts: Vec<crate::scene::Placed>,
        kept: Vec<u32>,
    },
    Fill {
        id: u32,
        x: i32,
        y: i32,
        w: usize,
        h: usize,
        rgba: [u8; 4],
    },
    BarBase {
        id: u32,
        geometry: Box<crate::bar::Geometry>,
        canvas: Vec<u8>,
        pills: Box<crate::bar::Pills>,
    },
    Bar {
        id: u32,
        bar: u32,
        x: i32,
        y: i32,
        light: u32,
        paint: crate::bar::Paint,
        spans: Vec<(f64, f64)>,
    },
    GlobeLayer {
        id: u32,
        globe: u32,
        map: u32,
        air: u32,
        step: u32,
        x: i32,
        y: i32,
        mark: Option<(Vec<u8>, usize, usize, i32, i32)>,
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

/// `len` bytes of `input` in a vector of their own, read into it as they come (not zeroed
/// first: a layer's pixels are most of what comes, and zeroing them cost as much again).
fn bytes(input: &mut impl Read, len: usize, what: &str) -> io::Result<Vec<u8>> {
    let mut out = Vec::with_capacity(len);
    input.take(len as u64).read_to_end(&mut out)?;
    if out.len() == len {
        Ok(out)
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
/// The most parts a layer is cut from (a graph's window: three).
const MOST_PARTS: usize = 64;
/// The most pictures a layer laid from them keeps unused (a turning rotor's positions).
const MOST_KEPT: usize = 1024;
/// The head of a lit bar (kind 21), and the most spans one lights (a segment each).
const BAR_HEAD: usize = 224;
const MOST_SPANS: usize = 4096;

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
        let pixels = bytes(input, (x1 - x0) * (y1 - y0) * 3, "a box cut short")?;
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
            let args = bytes(input, args_len, "a command cut short")?;
            let payload = bytes(input, payload_len, "a command cut short")?;
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
            let text = bytes(input, b as usize, "a stream cut short")?;
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
            let data = bytes(input, b as usize, "a picture cut short")?;
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
                    let picture = bytes(input, len, "a ground cut short")?;
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
            let pixels = bytes(input, len - 20, "a layer cut short")?;
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
            let ids = bytes(input, count * 4, "a frame cut short")?;
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
        12 => {
            let len = b as usize;
            if !(56..=2 * MOST_LAYER + 56).contains(&len) {
                return Err(invalid("a ring too large"));
            }
            let mut head = [0u8; 56];
            whole(input, &mut head, "a ring cut short")?;
            let f = |i: usize| f64::from_le_bytes(head[i..i + 8].try_into().expect("8 bytes"));
            let side = u32_at(&head, 0) as usize;
            let picture = side * side * 4;
            if len != 56 + 2 * picture {
                return Err(invalid("a ring's pictures are not of its canvas"));
            }
            let groove = bytes(input, picture, "a ring cut short")?;
            let lit = bytes(input, picture, "a ring cut short")?;
            Ok(Some(Record::RingBase {
                id: a,
                side,
                geometry: crate::ring::Geometry {
                    stroke: u32_at(&head, 4),
                    w: f(8),
                    thickness: f(16),
                    start: f(24),
                    end: f(32),
                    halo: f(40),
                    colour: [head[48], head[49], head[50], head[51]],
                },
                groove,
                lit,
            }))
        }
        13 => {
            if b != 24 {
                return Err(invalid("a layer of a ring of another length"));
            }
            let mut more = [0u8; 24];
            whole(input, &mut more, "a layer of a ring cut short")?;
            let frac = f64::from_le_bytes(more[16..24].try_into().expect("8 bytes"));
            if !frac.is_finite() {
                return Err(invalid("a ring lit by no share"));
            }
            Ok(Some(Record::Ring {
                id: a,
                ring: u32_at(&more, 0),
                x: u32_at(&more, 4) as i32,
                y: u32_at(&more, 8) as i32,
                frac,
            }))
        }
        14 => {
            let len = b as usize;
            if !(8..=MOST_LAYER + 8).contains(&len) {
                return Err(invalid("a source too large"));
            }
            let mut size = [0u8; 8];
            whole(input, &mut size, "a source cut short")?;
            let (w, h) = (u32_at(&size, 0) as usize, u32_at(&size, 4) as usize);
            if w.checked_mul(h).and_then(|n| n.checked_mul(4)) != Some(len - 8) {
                return Err(invalid("a source's pixels are not of its size"));
            }
            let pixels = bytes(input, len - 8, "a source cut short")?;
            Ok(Some(Record::Source {
                id: a,
                w,
                h,
                pixels,
            }))
        }
        15 => {
            let len = b as usize;
            if len < 20 || !(len - 20).is_multiple_of(28) || (len - 20) / 28 > MOST_PARTS {
                return Err(invalid("a layer cut from sources of another length"));
            }
            let data = bytes(input, len, "a layer cut from sources cut short")?;
            if u32_at(&data, 16) as usize != (len - 20) / 28 {
                return Err(invalid("a layer cut from sources of another length"));
            }
            let parts = data[20..]
                .chunks_exact(28)
                .map(|p| crate::scene::Part {
                    source: u32_at(p, 0),
                    sx: u32_at(p, 4) as i32,
                    sy: u32_at(p, 8) as i32,
                    dx: u32_at(p, 12) as i32,
                    dy: u32_at(p, 16) as i32,
                    w: u32_at(p, 20) as usize,
                    h: u32_at(p, 24) as usize,
                })
                .collect();
            Ok(Some(Record::View {
                id: a,
                x: u32_at(&data, 0) as i32,
                y: u32_at(&data, 4) as i32,
                w: u32_at(&data, 8) as usize,
                h: u32_at(&data, 12) as usize,
                parts,
            }))
        }
        16 => {
            let len = b as usize;
            if !(16..=4 * MOST_LAYER + 16).contains(&len) {
                return Err(invalid("a globe too large"));
            }
            let mut head = [0u8; 16];
            whole(input, &mut head, "a globe cut short")?;
            let (size, width) = (u32_at(&head, 0) as usize, u32_at(&head, 4) as usize);
            let (disc, rim) = (u32_at(&head, 8) as usize, u32_at(&head, 12) as usize);
            if Some(len - 16) != disc.checked_mul(12).and_then(|n| n.checked_add(rim * 5)) {
                return Err(invalid("a globe's tables are not as long as it says"));
            }
            let data = bytes(input, len - 16, "a globe cut short")?;
            let words = |from: usize, n: usize| -> Vec<u32> {
                data[from..from + 4 * n]
                    .chunks_exact(4)
                    .map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
                    .collect()
            };
            let inside = words(0, disc);
            let col = words(4 * disc, disc);
            let rowbase = words(8 * disc, disc);
            let rim_at = words(12 * disc, rim);
            let rim_alpha = data[12 * disc + 4 * rim..].to_vec();
            let tables =
                crate::globe::Tables::new(size, width, (inside, col, rowbase), (rim_at, rim_alpha))
                    .map_err(|e| invalid(&e))?;
            Ok(Some(Record::Globe { id: a, tables }))
        }
        17 => {
            let len = b as usize;
            if !(40..=MOST_LAYER + 40).contains(&len) {
                return Err(invalid("a layer of a globe of another length"));
            }
            let mut head = [0u8; 40];
            whole(input, &mut head, "a layer of a globe cut short")?;
            let (mw, mh) = (u32_at(&head, 24) as usize, u32_at(&head, 28) as usize);
            if mw.checked_mul(mh).and_then(|n| n.checked_mul(4)) != Some(len - 40) {
                return Err(invalid("a globe's mark is not of its size"));
            }
            let pixels = bytes(input, len - 40, "a layer of a globe cut short")?;
            let mark = (mw > 0 && mh > 0).then(|| {
                let (mx, my) = (u32_at(&head, 32) as i32, u32_at(&head, 36) as i32);
                (pixels, mw, mh, mx, my)
            });
            Ok(Some(Record::GlobeLayer {
                id: a,
                globe: u32_at(&head, 0),
                map: u32_at(&head, 4),
                air: u32_at(&head, 8),
                step: u32_at(&head, 12),
                x: u32_at(&head, 16) as i32,
                y: u32_at(&head, 20) as i32,
                mark,
            }))
        }
        18 => {
            if b != 20 {
                return Err(invalid("a layer of one colour of another length"));
            }
            let mut more = [0u8; 20];
            whole(input, &mut more, "a layer of one colour cut short")?;
            Ok(Some(Record::Fill {
                id: a,
                x: u32_at(&more, 0) as i32,
                y: u32_at(&more, 4) as i32,
                w: u32_at(&more, 8) as usize,
                h: u32_at(&more, 12) as usize,
                rgba: [more[16], more[17], more[18], more[19]],
            }))
        }
        19 => {
            let len = b as usize;
            if !(24..=24 + 12 * MOST_PARTS + 4 * MOST_KEPT).contains(&len) {
                return Err(invalid("a layer laid from sources of another length"));
            }
            let data = bytes(input, len, "a layer laid from sources cut short")?;
            let (n, kept) = (u32_at(&data, 16) as usize, u32_at(&data, 20) as usize);
            if n > MOST_PARTS || kept > MOST_KEPT || len != 24 + 12 * n + 4 * kept {
                return Err(invalid("a layer laid from sources of another length"));
            }
            let parts = data[24..24 + 12 * n]
                .chunks_exact(12)
                .map(|p| crate::scene::Placed {
                    source: u32_at(p, 0),
                    dx: u32_at(p, 4) as i32,
                    dy: u32_at(p, 8) as i32,
                })
                .collect();
            let kept = data[24 + 12 * n..]
                .chunks_exact(4)
                .map(|k| u32_at(k, 0))
                .collect();
            Ok(Some(Record::Laid {
                id: a,
                x: u32_at(&data, 0) as i32,
                y: u32_at(&data, 4) as i32,
                w: u32_at(&data, 8) as usize,
                h: u32_at(&data, 12) as usize,
                parts,
                kept,
            }))
        }
        20 => {
            let len = b as usize;
            if !(20..=MOST_LAYER + 20).contains(&len) {
                return Err(invalid("a grown source too large"));
            }
            let mut head = [0u8; 20];
            whole(input, &mut head, "a grown source cut short")?;
            let (w, h) = (u32_at(&head, 0) as usize, u32_at(&head, 4) as usize);
            let keep = u32_at(&head, 16) as usize;
            let rest = w
                .checked_sub(keep)
                .and_then(|n| n.checked_mul(h)?.checked_mul(4));
            if rest != Some(len - 20) {
                return Err(invalid("a grown source's pixels are not of its size"));
            }
            let rest = bytes(input, len - 20, "a grown source cut short")?;
            Ok(Some(Record::Grown {
                id: a,
                w,
                h,
                from: u32_at(&head, 8),
                shift: u32_at(&head, 12) as usize,
                keep,
                rest,
            }))
        }
        21 => {
            let len = b as usize;
            if !(BAR_HEAD..=2 * MOST_LAYER).contains(&len) {
                return Err(invalid("a bar too large"));
            }
            let mut head = [0u8; BAR_HEAD];
            whole(input, &mut head, "a bar cut short")?;
            let u = |i: usize| u32_at(&head, i) as usize;
            let i = |at: usize| u32_at(&head, at) as i32;
            let f = |i: usize| f64::from_le_bytes(head[i..i + 8].try_into().expect("8 bytes"));
            let (length, thick, pad, core) = (u(0), u(4), u(8), u(12));
            let (lw, rw, short) = (u(28), u(32), u(36));
            let small = |v: usize| v <= 1 << 14;
            if ![length, thick, pad, core, lw, rw, short]
                .into_iter()
                .all(small)
            {
                return Err(invalid("a bar too large"));
            }
            let high = core + 2 * pad;
            let canvas = (length + 2 * pad) * (thick + 2 * pad) * 4;
            let shorts: Vec<usize> = (1..=short).map(|s| high * (s + 2 * pad)).collect();
            let want = BAR_HEAD as u64
                + canvas as u64
                + (high * (lw + 1 + rw)) as u64
                + shorts.iter().map(|&n| n as u64).sum::<u64>();
            if want != len as u64 {
                return Err(invalid("a bar's pictures are not of its sizes"));
            }
            let scale = u(40);
            let steps = |at: usize| std::array::from_fn(|k| f(at + 8 * k));
            let glow = (scale > 0).then(|| crate::bar::Glow {
                bar: (i(44), i(48)),
                area: (i(52), i(56), u(60), u(64)),
                scale,
                radius: f(80),
                strength: f(88),
                head: f(96),
                tail: f(104),
                levels: steps(112),
                whites: steps(168),
            });
            let geometry = crate::bar::Geometry {
                length,
                thick,
                pad,
                core,
                offset: i64::from(i(16)),
                upright: u(20) & 1 != 0,
                reverse: u(20) & 2 != 0,
                segments: u32_at(&head, 24),
                halo: f(72),
                glow,
            };
            let canvas = bytes(input, canvas, "a bar cut short")?;
            let left = bytes(input, high * lw, "a bar cut short")?;
            let middle = bytes(input, high, "a bar cut short")?;
            let right = bytes(input, high * rw, "a bar cut short")?;
            let short = shorts
                .into_iter()
                .map(|n| bytes(input, n, "a bar cut short"))
                .collect::<io::Result<Vec<_>>>()?;
            Ok(Some(Record::BarBase {
                id: a,
                geometry: Box::new(geometry),
                canvas,
                pills: Box::new(crate::bar::Pills {
                    left,
                    lw,
                    middle,
                    right,
                    rw,
                    short,
                }),
            }))
        }
        22 => {
            let len = b as usize;
            if len < 32 || !(len - 32).is_multiple_of(16) || (len - 32) / 16 > MOST_SPANS {
                return Err(invalid("a layer of a bar of another length"));
            }
            let mut more = [0u8; 32];
            whole(input, &mut more, "a layer of a bar cut short")?;
            let n = u32_at(&more, 28) as usize;
            if len != 32 + 16 * n {
                return Err(invalid("a layer of a bar of another length"));
            }
            let data = bytes(input, 16 * n, "a layer of a bar cut short")?;
            let spans: Vec<(f64, f64)> = data
                .chunks_exact(16)
                .map(|s| {
                    let f = |i: usize| f64::from_le_bytes(s[i..i + 8].try_into().expect("8 bytes"));
                    (f(0), f(8))
                })
                .collect();
            if spans
                .iter()
                .any(|&(a, b)| !(a.is_finite() && b.is_finite() && a <= b && a > -1e6 && b < 1e6))
            {
                return Err(invalid("a bar lit by no span known"));
            }
            let rgba = |i: usize| [more[i], more[i + 1], more[i + 2], more[i + 3]];
            Ok(Some(Record::Bar {
                id: a,
                bar: u32_at(&more, 0),
                x: u32_at(&more, 4) as i32,
                y: u32_at(&more, 8) as i32,
                light: u32_at(&more, 12),
                paint: crate::bar::Paint {
                    colour: rgba(16),
                    to: (u32_at(&more, 24) != 0).then(|| rgba(20)),
                },
                spans,
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
    fn a_layer_laid_from_sources_with_those_it_keeps() {
        let mut input = head(19, 3, 24 + 2 * 12 + 4);
        for v in [5u32, (-2i32) as u32, 68, 68, 2, 1] {
            input.extend(v.to_le_bytes());
        }
        for v in [7u32, 0, 0, 8, 14, (-1i32) as u32, 9] {
            input.extend(v.to_le_bytes());
        }
        match read(&mut input.as_slice()).unwrap() {
            Some(Record::Laid {
                id,
                x,
                y,
                w,
                h,
                parts,
                kept,
            }) => {
                assert_eq!((id, x, y, w, h), (3, 5, -2, 68, 68));
                let placed = |source, dx, dy| crate::scene::Placed { source, dx, dy };
                assert_eq!(parts, vec![placed(7, 0, 0), placed(8, 14, -1)]);
                assert_eq!(kept, vec![9]);
            }
            _ => panic!("not the layer"),
        }
        let mut counted = head(19, 3, 24 + 12);
        for v in [0u32, 0, 1, 1, 2, 0, 7, 0, 0] {
            counted.extend(v.to_le_bytes());
        }
        assert!(read(&mut counted.as_slice()).is_err()); // (two parts said, one there)
    }

    #[test]
    fn a_source_grown_from_another() {
        // 3 x 2, its first column the other's from column 4, the rest (2 columns) after
        let mut input = head(20, 6, 20 + 2 * 2 * 4);
        for v in [3u32, 2, 5, 4, 1] {
            input.extend(v.to_le_bytes());
        }
        input.extend(1..=16u8);
        match read(&mut input.as_slice()).unwrap() {
            Some(Record::Grown {
                id,
                w,
                h,
                from,
                shift,
                keep,
                rest,
            }) => {
                assert_eq!((id, w, h, from, shift, keep), (6, 3, 2, 5, 4, 1));
                assert_eq!(rest, (1..=16u8).collect::<Vec<_>>());
            }
            _ => panic!("not the source"),
        }
        for (keep, len) in [(4u32, 0usize), (1, 12), (0, 16)] {
            // (more columns kept than it has; a rest of another length)
            let mut wrong = head(20, 6, 20 + len as u32);
            for v in [3u32, 2, 5, 4, keep] {
                wrong.extend(v.to_le_bytes());
            }
            wrong.extend(std::iter::repeat_n(0u8, len));
            assert!(
                read(&mut wrong.as_slice()).is_err(),
                "keep {keep}, {len} bytes"
            );
        }
    }

    #[test]
    fn a_source_and_a_layer_cut_from_it() {
        let mut input = head(14, 4, 8 + 2 * 4);
        input.extend(2u32.to_le_bytes());
        input.extend(1u32.to_le_bytes());
        input.extend([1, 2, 3, 4, 5, 6, 7, 8]);
        input.extend(head(15, 9, 20 + 28));
        for v in [(-1i32) as u32, 2, 3, 1, 1, 4, 1, 0, (-1i32) as u32, 0, 2, 1] {
            input.extend(v.to_le_bytes());
        }
        let mut input = input.as_slice();
        match read(&mut input).unwrap() {
            Some(Record::Source { id, w, h, pixels }) => {
                assert_eq!((id, w, h), (4, 2, 1));
                assert_eq!(pixels, vec![1, 2, 3, 4, 5, 6, 7, 8]);
            }
            _ => panic!("not the source"),
        }
        match read(&mut input).unwrap() {
            Some(Record::View {
                id,
                x,
                y,
                w,
                h,
                parts,
            }) => {
                assert_eq!((id, x, y, w, h), (9, -1, 2, 3, 1));
                let p = &parts[0];
                assert_eq!(
                    (p.source, p.sx, p.sy, p.dx, p.dy, p.w, p.h),
                    (4, 1, 0, -1, 0, 2, 1)
                );
            }
            _ => panic!("not the layer"),
        }
        let mut wrong = head(14, 4, 8 + 3);
        wrong.extend(1u32.to_le_bytes());
        wrong.extend(1u32.to_le_bytes());
        wrong.extend([0u8; 3]);
        assert!(read(&mut wrong.as_slice()).is_err()); // (not of its size)
        let mut counted = head(15, 9, 20 + 28);
        for v in [0u32, 0, 1, 1, 2, 0, 0, 0, 0, 0, 1, 1] {
            counted.extend(v.to_le_bytes());
        }
        assert!(read(&mut counted.as_slice()).is_err()); // (two parts said, one there)
    }

    #[test]
    fn a_globe_and_a_layer_of_it() {
        let mut body = Vec::new();
        for v in [2u32, 4, 2, 1] {
            body.extend(v.to_le_bytes()); // size, width, disc, rim
        }
        for v in [0u32, 3, 1, 2, 4, 4, 3] {
            body.extend(v.to_le_bytes()); // inside, col, rowbase, rim
        }
        body.push(77); // the rim's alpha
        let mut input = head(16, 5, body.len() as u32);
        input.extend(&body);
        let mut layer = Vec::new();
        for v in [5u32, 8, 0, 3, (-2i32) as u32, 7, 1, 1, 1, (-1i32) as u32] {
            layer.extend(v.to_le_bytes());
        }
        layer.extend([1, 2, 3, 4]);
        input.extend(head(17, 9, layer.len() as u32));
        input.extend(&layer);
        let mut input = input.as_slice();
        match read(&mut input).unwrap() {
            Some(Record::Globe { id, tables }) => assert_eq!((id, tables.size()), (5, 2)),
            _ => panic!("not the globe"),
        }
        match read(&mut input).unwrap() {
            Some(Record::GlobeLayer {
                id,
                globe,
                map,
                air,
                step,
                x,
                y,
                mark,
            }) => {
                assert_eq!((id, globe, map, air, step, x, y), (9, 5, 8, 0, 3, -2, 7));
                assert_eq!(mark, Some((vec![1, 2, 3, 4], 1, 1, 1, -1)));
            }
            _ => panic!("not the layer"),
        }
        let mut short = head(16, 5, body.len() as u32 - 1);
        short.extend(&body[..body.len() - 1]);
        assert!(read(&mut short.as_slice()).is_err()); // (not as long as it says)
    }

    #[test]
    fn a_layer_of_one_colour() {
        let mut input = head(18, 21, 20);
        for n in [-7i32, 30] {
            input.extend(n.to_le_bytes());
        }
        for n in [609u32, 626] {
            input.extend(n.to_le_bytes());
        }
        input.extend([12, 18, 24, 178]);
        match read(&mut input.as_slice()).unwrap() {
            Some(Record::Fill {
                id,
                x,
                y,
                w,
                h,
                rgba,
            }) => {
                assert_eq!(
                    (id, x, y, w, h, rgba),
                    (21, -7, 30, 609, 626, [12, 18, 24, 178])
                );
            }
            _ => panic!("not the layer"),
        }
        assert!(read(&mut head(18, 21, 19).as_slice()).is_err()); // (of another length)
        let mut short = head(18, 21, 20);
        short.extend([0u8; 12]);
        assert!(read(&mut short.as_slice()).is_err());
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
