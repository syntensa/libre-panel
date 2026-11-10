//! Python's zipfile (3.12) as far as a shared theme needs it: a zip read as ZipFile reads one
//! (its end record, ZIP64's too, its directory, each file's header, the file stored or
//! deflated and its CRC), failing where and as zipfile fails; and a zip written as ZipFile
//! writes one in "w" mode with ZIP_DEFLATED.

use flate2::{Compress, Compression, Crc, Decompress, FlushCompress, FlushDecompress, Status};

/// What reading a zip comes to where zipfile raises: its exception and message.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ZipError {
    /// zipfile.BadZipFile
    BadZip(String),
    /// NotImplementedError
    NotImplemented(String),
    /// UnicodeDecodeError (a ValueError) of a name
    UnicodeDecode(String),
    /// EOFError: a file's data ends early
    Eof,
    /// zlib.error: deflated data that is none
    Zlib(String),
    /// ValueError of another kind (a header before the file's start)
    Value(String),
}

impl ZipError {
    /// The exception as Python names it and its message, as an uncaught one is told.
    pub fn told(&self) -> String {
        match self {
            ZipError::BadZip(m) => format!("BadZipFile: {m}"),
            ZipError::NotImplemented(m) => format!("NotImplementedError: {m}"),
            ZipError::UnicodeDecode(m) => format!("UnicodeDecodeError: {m}"),
            ZipError::Eof => "EOFError".into(),
            ZipError::Zlib(m) => format!("error: {m}"),
            ZipError::Value(m) => format!("ValueError: {m}"),
        }
    }
}

type Result<T> = std::result::Result<T, ZipError>;

fn bad<T>(message: &str) -> Result<T> {
    Err(ZipError::BadZip(message.to_string()))
}

/// The letters of the bytes 0x80 to 0xFF in code page 437, a zip's names' own encoding.
const CP437: [char; 128] = [
    '\u{00c7}', '\u{00fc}', '\u{00e9}', '\u{00e2}', '\u{00e4}', '\u{00e0}', '\u{00e5}', '\u{00e7}',
    '\u{00ea}', '\u{00eb}', '\u{00e8}', '\u{00ef}', '\u{00ee}', '\u{00ec}', '\u{00c4}', '\u{00c5}',
    '\u{00c9}', '\u{00e6}', '\u{00c6}', '\u{00f4}', '\u{00f6}', '\u{00f2}', '\u{00fb}', '\u{00f9}',
    '\u{00ff}', '\u{00d6}', '\u{00dc}', '\u{00a2}', '\u{00a3}', '\u{00a5}', '\u{20a7}', '\u{0192}',
    '\u{00e1}', '\u{00ed}', '\u{00f3}', '\u{00fa}', '\u{00f1}', '\u{00d1}', '\u{00aa}', '\u{00ba}',
    '\u{00bf}', '\u{2310}', '\u{00ac}', '\u{00bd}', '\u{00bc}', '\u{00a1}', '\u{00ab}', '\u{00bb}',
    '\u{2591}', '\u{2592}', '\u{2593}', '\u{2502}', '\u{2524}', '\u{2561}', '\u{2562}', '\u{2556}',
    '\u{2555}', '\u{2563}', '\u{2551}', '\u{2557}', '\u{255d}', '\u{255c}', '\u{255b}', '\u{2510}',
    '\u{2514}', '\u{2534}', '\u{252c}', '\u{251c}', '\u{2500}', '\u{253c}', '\u{255e}', '\u{255f}',
    '\u{255a}', '\u{2554}', '\u{2569}', '\u{2566}', '\u{2560}', '\u{2550}', '\u{256c}', '\u{2567}',
    '\u{2568}', '\u{2564}', '\u{2565}', '\u{2559}', '\u{2558}', '\u{2552}', '\u{2553}', '\u{256b}',
    '\u{256a}', '\u{2518}', '\u{250c}', '\u{2588}', '\u{2584}', '\u{258c}', '\u{2590}', '\u{2580}',
    '\u{03b1}', '\u{00df}', '\u{0393}', '\u{03c0}', '\u{03a3}', '\u{03c3}', '\u{00b5}', '\u{03c4}',
    '\u{03a6}', '\u{0398}', '\u{03a9}', '\u{03b4}', '\u{221e}', '\u{03c6}', '\u{03b5}', '\u{2229}',
    '\u{2261}', '\u{00b1}', '\u{2265}', '\u{2264}', '\u{2320}', '\u{2321}', '\u{00f7}', '\u{2248}',
    '\u{00b0}', '\u{2219}', '\u{00b7}', '\u{221a}', '\u{207f}', '\u{00b2}', '\u{25a0}', '\u{00a0}',
];

fn cp437(bytes: &[u8]) -> String {
    bytes
        .iter()
        .map(|&b| match b {
            0..=0x7F => b as char,
            _ => CP437[usize::from(b - 0x80)],
        })
        .collect()
}

/// A name as zipfile decodes it: UTF-8 where the flag says so, else code page 437.
fn decoded(bytes: &[u8], utf8: bool) -> Result<String> {
    if !utf8 {
        return Ok(cp437(bytes));
    }
    lp_py::codecs::decode_utf8(bytes)
        .map(str::to_string)
        .map_err(|e| ZipError::UnicodeDecode(e.to_string()))
}

/// repr() of bytes: b'...' with Python's escapes.
fn repr_bytes(bytes: &[u8]) -> String {
    let quote = if bytes.contains(&b'\'') && !bytes.contains(&b'"') {
        b'"'
    } else {
        b'\''
    };
    let mut out = format!("b{}", quote as char);
    for &b in bytes {
        match b {
            _ if b == quote || b == b'\\' => {
                out.push('\\');
                out.push(b as char);
            }
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            0x20..=0x7E => out.push(b as char),
            _ => out.push_str(&format!("\\x{b:02x}")),
        }
    }
    out.push(quote as char);
    out
}

/// _sanitize_filename: the name to its first NUL, the system's separator a slash.
fn sanitized(name: &str) -> String {
    let name = name.split('\0').next().unwrap_or("");
    if cfg!(windows) {
        name.replace('\\', "/")
    } else {
        name.to_string()
    }
}

fn u16_at(d: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([d[at], d[at + 1]])
}

fn u32_at(d: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([d[at], d[at + 1], d[at + 2], d[at + 3]])
}

fn u64_at(d: &[u8], at: usize) -> u64 {
    let mut b = [0u8; 8];
    b.copy_from_slice(&d[at..at + 8]);
    u64::from_le_bytes(b)
}

/// `n` bytes of `data` from `at`, as a file's read gives them: fewer at its end, none past it.
fn read_at(data: &[u8], at: i128, n: i128) -> &[u8] {
    let len = data.len() as i128;
    let start = at.clamp(0, len);
    let end = (at + n.max(0)).clamp(start, len);
    &data[start as usize..end as usize]
}

/// ZipInfo: one file of a zip as its directory has it.
#[derive(Clone, Debug)]
pub struct Info {
    pub filename: String,
    pub orig_filename: String,
    pub flag_bits: u16,
    pub compress_type: u16,
    pub crc: u32,
    pub compress_size: u64,
    pub file_size: u64,
    pub header_offset: i128,
    pub external_attr: u32,
    #[allow(dead_code)] // (the community's zips rewritten keep it)
    pub date_time: [u32; 6],
    end_offset: Option<i128>,
}

impl Info {
    /// is_dir(): a name ending in a slash.
    pub fn is_dir(&self) -> bool {
        self.filename.ends_with('/')
    }
}

/// The end of the central directory: its size, its offset and where the record is.
struct End {
    size: u128,
    offset: u128,
    location: i128,
}

const END: &[u8] = b"PK\x05\x06";
const END64: &[u8] = b"PK\x06\x06";
const END64_LOCATOR: &[u8] = b"PK\x06\x07";
const CENTRAL: &[u8] = b"PK\x01\x02";
const LOCAL: &[u8] = b"PK\x03\x04";

/// _EndRecData64: ZIP64's end record where its locator stands before the end record.
fn end64(data: &[u8], offset: i128, end: End) -> Result<End> {
    let offset = offset - 20;
    if offset < 0 {
        return Ok(end);
    }
    let locator = read_at(data, offset, 20);
    if &locator[..4] != END64_LOCATOR {
        return Ok(end);
    }
    let (diskno, reloff, disks) = (u32_at(locator, 4), u64_at(locator, 8), u32_at(locator, 16));
    if diskno != 0 || disks > 1 {
        return bad("zipfiles that span multiple disks are not supported");
    }
    let offset = offset - 56;
    let reloff = i128::from(reloff);
    if reloff > offset {
        return bad("Corrupt zip64 end of central directory locator");
    }
    let mut extrasz = offset - reloff;
    // (a record cut short: OSError, which _RealGetContents tells as no zip)
    let mut record = read_at(data, reloff, 56);
    if record.len() != 56 {
        return bad("File is not a zip file");
    }
    if !record.starts_with(END64) && reloff != offset {
        extrasz = 0;
        record = read_at(data, offset, 56);
        if record.len() != 56 {
            return bad("File is not a zip file");
        }
    }
    if !record.starts_with(END64) {
        return bad("Zip64 end of central directory record not found");
    }
    let sz = i128::from(u64_at(record, 4));
    let (dirsize, diroffset) = (
        u128::from(u64_at(record, 40)),
        u128::from(u64_at(record, 48)),
    );
    if diroffset + dirsize != reloff as u128 || sz + 12 != 56 + extrasz {
        return bad("Corrupt zip64 end of central directory record");
    }
    Ok(End {
        size: dirsize,
        offset: diroffset,
        location: offset - extrasz,
    })
}

/// _EndRecData: None where there is no end record.
fn end_record(data: &[u8]) -> Result<Option<End>> {
    let filesize = data.len() as i128;
    if filesize < 22 {
        return Ok(None);
    }
    let last = &data[data.len() - 22..];
    if &last[..4] == END && last[20..] == [0, 0] {
        let end = End {
            size: u128::from(u32_at(last, 12)),
            offset: u128::from(u32_at(last, 16)),
            location: filesize - 22,
        };
        return end64(data, filesize - 22, end).map(Some);
    }
    // (an end record with a comment after it: the last one in the last 64 KiB and more)
    let start_at = (filesize - (1 << 16) - 22).max(0) as usize;
    let tail = &data[start_at..];
    let Some(start) = tail.windows(4).rposition(|w| w == END) else {
        return Ok(None);
    };
    let record = &tail[start..];
    if record.len() < 22 {
        return Ok(None);
    }
    let end = End {
        size: u128::from(u32_at(record, 12)),
        offset: u128::from(u32_at(record, 16)),
        location: (start_at + start) as i128,
    };
    end64(data, (start_at + start) as i128, end).map(Some)
}

/// A zip read: ZipFile(BytesIO(data)).
pub struct Archive<'a> {
    data: &'a [u8],
    pub infolist: Vec<Info>,
}

impl<'a> Archive<'a> {
    /// _RealGetContents: the files its directory names.
    pub fn new(data: &'a [u8]) -> Result<Archive<'a>> {
        let Some(end) = end_record(data)? else {
            return bad("File is not a zip file");
        };
        let concat = end.location - end.size as i128 - end.offset as i128;
        let start_dir = end.offset as i128 + concat;
        if start_dir < 0 {
            return bad("Bad offset for central directory");
        }
        let directory = read_at(data, start_dir, end.size as i128);
        let mut infolist: Vec<Info> = Vec::new();
        let (mut total, mut at) = (0u128, 0usize);
        let read = |at: &mut usize, n: usize| -> &[u8] {
            let start = (*at).min(directory.len());
            let stop = (start + n).min(directory.len());
            *at = stop;
            &directory[start..stop]
        };
        while total < end.size {
            let centdir = read(&mut at, 46);
            if centdir.len() != 46 {
                return bad("Truncated central directory");
            }
            if &centdir[..4] != CENTRAL {
                return bad("Bad magic number for central directory");
            }
            let extract_version = centdir[6];
            let flag_bits = u16_at(centdir, 8);
            let (t, d) = (
                u32::from(u16_at(centdir, 12)),
                u32::from(u16_at(centdir, 14)),
            );
            let (name_len, extra_len, comment_len) = (
                usize::from(u16_at(centdir, 28)),
                usize::from(u16_at(centdir, 30)),
                usize::from(u16_at(centdir, 32)),
            );
            let name_bytes = read(&mut at, name_len).to_vec();
            let mut name_crc = Crc::new();
            name_crc.update(&name_bytes);
            let name = decoded(&name_bytes, flag_bits & 0x800 != 0)?;
            let extra = read(&mut at, extra_len).to_vec();
            read(&mut at, comment_len);
            if extract_version > 63 {
                return Err(ZipError::NotImplemented(format!(
                    "zip file version {:.1}",
                    f64::from(extract_version) / 10.0
                )));
            }
            let mut info = Info {
                filename: sanitized(&name),
                orig_filename: name,
                flag_bits,
                compress_type: u16_at(centdir, 10),
                crc: u32_at(centdir, 16),
                compress_size: u64::from(u32_at(centdir, 20)),
                file_size: u64::from(u32_at(centdir, 24)),
                header_offset: i128::from(u32_at(centdir, 42)),
                external_attr: u32_at(centdir, 38),
                date_time: [
                    (d >> 9) + 1980,
                    (d >> 5) & 0xF,
                    d & 0x1F,
                    t >> 11,
                    (t >> 5) & 0x3F,
                    (t & 0x1F) * 2,
                ],
                end_offset: None,
            };
            decode_extra(&mut info, &extra, name_crc.sum())?;
            info.header_offset += concat;
            infolist.push(info);
            total += 46 + (name_len + extra_len + comment_len) as u128;
        }
        // (each file ends where the next begins, the last where the directory does)
        let mut order: Vec<usize> = (0..infolist.len()).collect();
        order.sort_by(|&a, &b| infolist[b].header_offset.cmp(&infolist[a].header_offset));
        let mut end_offset = start_dir;
        for i in order {
            infolist[i].end_offset = Some(end_offset);
            end_offset = infolist[i].header_offset;
        }
        Ok(Archive { data, infolist })
    }

    /// read(info): the file's bytes, unpacked, their CRC checked (open and ZipExtFile).
    pub fn read(&self, info: &Info) -> Result<Vec<u8>> {
        if info.header_offset < 0 {
            let at = info.header_offset;
            return Err(ZipError::Value(format!("negative seek value {at}")));
        }
        let header = read_at(self.data, info.header_offset, 30);
        if header.len() != 30 {
            return bad("Truncated file header");
        }
        if &header[..4] != LOCAL {
            return bad("Bad magic number for file header");
        }
        let name_len = i128::from(u16_at(header, 26));
        let extra_len = i128::from(u16_at(header, 28));
        let name = read_at(self.data, info.header_offset + 30, name_len);
        if info.flag_bits & 0x20 != 0 {
            return Err(ZipError::NotImplemented(
                "compressed patched data (flag bit 5)".into(),
            ));
        }
        if info.flag_bits & 0x40 != 0 {
            return Err(ZipError::NotImplemented(
                "strong encryption (flag bit 6)".into(),
            ));
        }
        if decoded(name, u16_at(header, 6) & 0x800 != 0)? != info.orig_filename {
            return bad(&format!(
                "File name in directory {} and header {} differ.",
                lp_py::repr(&info.orig_filename),
                repr_bytes(name)
            ));
        }
        // (where the data begins: past the name and the extra field, as the seek leaves it)
        let start = info.header_offset + 30 + name.len() as i128 + extra_len;
        if info
            .end_offset
            .is_some_and(|end| start + i128::from(info.compress_size) > end)
        {
            return bad(&format!(
                "Overlapped entries: {} (possible zip bomb)",
                lp_py::repr(&info.orig_filename)
            ));
        }
        if info.flag_bits & 0x1 != 0 {
            let name = lp_py::repr(&info.filename);
            return Err(ZipError::Value(format!(
                "File {name} is encrypted, password required for extraction"
            )));
        }
        match info.compress_type {
            0 | 8 => {}
            12 | 14 => {
                return Err(ZipError::NotImplemented(
                    "bzip2 and lzma: not read by this build".into(),
                ))
            }
            _ => {
                return Err(ZipError::NotImplemented(
                    "That compression method is not supported".into(),
                ))
            }
        }
        let given = read_at(self.data, start, i128::from(info.compress_size));
        if given.is_empty() && info.compress_size > 0 {
            return Err(ZipError::Eof);
        }
        let all_given = given.len() as u64 == info.compress_size;
        let (mut out, mut eof) = match info.compress_type {
            0 => (given.to_vec(), all_given),
            _ => {
                let (out, ended) = inflated(given)?;
                (out, ended || all_given)
            }
        };
        let left = usize::try_from(info.file_size).unwrap_or(usize::MAX);
        if out.len() >= left {
            out.truncate(left);
            eof = true;
        }
        if !eof {
            return Err(ZipError::Eof);
        }
        let mut crc = Crc::new();
        crc.update(&out);
        if crc.sum() != info.crc {
            return bad(&format!(
                "Bad CRC-32 for file {}",
                lp_py::repr(&info.filename)
            ));
        }
        Ok(out)
    }
}

/// zlib.decompressobj(-15) of all of `given`: what it unpacks and whether the stream ended.
fn inflated(given: &[u8]) -> Result<(Vec<u8>, bool)> {
    let mut inflate = Decompress::new(false);
    let mut out = Vec::with_capacity(given.len().saturating_mul(3).min(64 << 20));
    loop {
        out.reserve(64 * 1024);
        let (fed, made) = (inflate.total_in() as usize, out.len());
        let status = inflate
            .decompress_vec(&given[fed..], &mut out, FlushDecompress::None)
            .map_err(|e| ZipError::Zlib(format!("Error -3 while decompressing data: {e}")))?;
        if status == Status::StreamEnd {
            return Ok((out, true));
        }
        let all_fed = inflate.total_in() as usize >= given.len();
        if all_fed && out.len() == made {
            return Ok((out, false));
        }
    }
}

/// _decodeExtra: ZIP64's sizes and offset, and a name in Unicode where the extra field has
/// one for this name.
fn decode_extra(info: &mut Info, extra: &[u8], name_crc: u32) -> Result<()> {
    let mut extra = extra;
    while extra.len() >= 4 {
        let (tp, ln) = (u16_at(extra, 0), usize::from(u16_at(extra, 2)));
        if ln + 4 > extra.len() {
            return bad(&format!("Corrupt extra field {tp:04x} (size={ln})"));
        }
        let mut data = &extra[4..ln + 4];
        if tp == 0x0001 {
            let mut take = |field: &str| -> Result<u64> {
                if data.len() < 8 {
                    return bad(&format!("Corrupt zip64 extra field. {field} not found."));
                }
                let value = u64_at(data, 0);
                data = &data[8..];
                Ok(value)
            };
            if info.file_size == 0xFFFF_FFFF {
                info.file_size = take("File size")?;
            }
            if info.compress_size == 0xFFFF_FFFF {
                info.compress_size = take("Compress size")?;
            }
            if info.header_offset == 0xFFFF_FFFF {
                info.header_offset = i128::from(take("Header offset")?);
            }
        } else if tp == 0x7075 {
            if data.len() < 5 {
                return bad("Corrupt unicode path extra field (0x7075)");
            }
            if data[0] == 1 && u32_at(data, 1) == name_crc {
                let name = lp_py::codecs::decode_utf8(&data[5..]).map_err(|_| {
                    ZipError::BadZip(
                        "Corrupt unicode path extra field (0x7075): invalid utf-8 bytes".into(),
                    )
                })?;
                if !name.is_empty() {
                    info.filename = sanitized(name);
                }
            }
        }
        extra = &extra[ln + 4..];
    }
    Ok(())
}

/// A zip written as ZipFile(BytesIO(), "w", ZIP_DEFLATED) writes one.
#[derive(Default)]
pub struct Writer {
    out: Vec<u8>,
    central: Vec<u8>,
    count: u64,
}

/// A file of a zip to write: its name, its time, the system's attributes, its bytes.
pub struct Entry<'a> {
    pub name: &'a str,
    pub date_time: [u32; 6],
    pub external_attr: u32,
    pub data: &'a [u8],
}

impl Writer {
    /// writestr/write: the file deflated after its header, and its line in the directory.
    pub fn add(&mut self, entry: &Entry) {
        let name = entry.name.as_bytes();
        // (_encodeFilenameFlags: a name of other than ASCII in UTF-8, flagged)
        let flags: u16 = if entry.name.is_ascii() { 0 } else { 0x800 };
        let mut crc = Crc::new();
        crc.update(entry.data);
        let packed = deflated(entry.data);
        let dt = entry.date_time;
        let dosdate = ((dt[0] - 1980) << 9 | dt[1] << 5 | dt[2]) as u16;
        let dostime = (dt[3] << 11 | dt[4] << 5 | (dt[5] / 2)) as u16;
        let offset = self.out.len() as u32;
        let common = |out: &mut Vec<u8>| {
            out.extend(flags.to_le_bytes());
            out.extend(8u16.to_le_bytes());
            out.extend(dostime.to_le_bytes());
            out.extend(dosdate.to_le_bytes());
            out.extend(crc.sum().to_le_bytes());
            out.extend((packed.len() as u32).to_le_bytes());
            out.extend((entry.data.len() as u32).to_le_bytes());
            out.extend((name.len() as u16).to_le_bytes());
            out.extend(0u16.to_le_bytes()); // (no extra field)
        };
        self.out.extend(LOCAL);
        self.out.extend([20, 0]);
        common(&mut self.out);
        self.out.extend(name);
        self.out.extend(&packed);
        let system: u8 = if cfg!(windows) { 0 } else { 3 };
        self.central.extend(CENTRAL);
        self.central.extend([20, system, 20, 0]);
        common(&mut self.central);
        self.central.extend([0, 0, 0, 0, 0, 0]); // (no comment, disk 0, no internal attributes)
        self.central.extend(entry.external_attr.to_le_bytes());
        self.central.extend(offset.to_le_bytes());
        self.central.extend(name);
        self.count += 1;
    }

    /// close(): the directory and the end record after the files.
    pub fn finish(mut self) -> Vec<u8> {
        let (at, size) = (self.out.len() as u32, self.central.len() as u32);
        self.out.extend(&self.central);
        self.out.extend(END);
        self.out.extend([0, 0, 0, 0]);
        self.out.extend((self.count as u16).to_le_bytes());
        self.out.extend((self.count as u16).to_le_bytes());
        self.out.extend(size.to_le_bytes());
        self.out.extend(at.to_le_bytes());
        self.out.extend(0u16.to_le_bytes());
        self.out
    }
}

/// zlib.compressobj(-1, DEFLATED, -15): raw deflate at zlib's default level.
fn deflated(data: &[u8]) -> Vec<u8> {
    let mut deflate = Compress::new(Compression::new(6), false);
    let mut out = Vec::with_capacity(data.len() / 2 + 64);
    loop {
        out.reserve(64 * 1024);
        let fed = deflate.total_in() as usize;
        match deflate.compress_vec(&data[fed..], &mut out, FlushCompress::Finish) {
            Ok(Status::StreamEnd) | Err(_) => return out,
            Ok(_) => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_zip_written_is_read_back() {
        let mut writer = Writer::default();
        writer.add(&Entry {
            name: "a/th\u{e9}me.json",
            date_time: [2024, 5, 6, 7, 8, 10],
            external_attr: 0o600 << 16,
            data: b"hello hello hello",
        });
        let zip = writer.finish();
        let archive = Archive::new(&zip).expect("read");
        let info = &archive.infolist[0];
        assert_eq!(info.filename, "a/th\u{e9}me.json");
        assert_eq!(info.date_time, [2024, 5, 6, 7, 8, 10]);
        assert_eq!(archive.read(info).expect("data"), b"hello hello hello");
    }
}
