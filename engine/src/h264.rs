//! H.264 as the panels take it, whatever made it (x264 here, a graphics card's encoder): a
//! stream of NAL units (Annex B), each keyframe with its SPS and PPS before it, and the SPS
//! saying the colours as Libre Panel makes them, BT.601 in limited or full range. A decoder
//! that reads the colours stretches what they say: AMD's encoder said limited values were full
//! range (and BT.709), and the picture came out washed out (devices/h264.py's STAMP has
//! ffmpeg's h264_metadata say them in every SPS; this does that here).

/// The NAL units of an Annex B stream: (where each one's header byte is, where it ends).
pub fn units(data: &[u8]) -> Vec<(usize, usize)> {
    let mut starts = Vec::new();
    let mut i = 0;
    while i + 3 <= data.len() {
        if data[i] == 0 && data[i + 1] == 0 && data[i + 2] == 1 {
            starts.push(i + 3);
            i += 3;
        } else {
            i += 1;
        }
    }
    let mut out = Vec::with_capacity(starts.len());
    for (k, &start) in starts.iter().enumerate() {
        let mut end = starts.get(k + 1).map_or(data.len(), |next| next - 3);
        while end > start && data[end - 1] == 0 && k + 1 < starts.len() {
            end -= 1; // (the zero byte of a four-byte start code belongs to the next)
        }
        if end > start {
            out.push((start, end));
        }
    }
    out
}

/// A NAL unit's type.
pub fn kind(unit: &[u8]) -> u8 {
    unit.first().map_or(0, |b| b & 0x1f)
}

pub const IDR: u8 = 5;
pub const SPS: u8 = 7;
pub const PPS: u8 = 8;
/// An access unit's delimiter and filler: nothing a decoder fed whole pictures needs (x264
/// makes neither; a card's encoder may), so not sent.
pub const AUD: u8 = 9;
pub const FILLER: u8 = 12;

/// The RBSP of a NAL unit's payload (after its header byte): its emulation prevention bytes
/// (the 3 of 00 00 03) taken out.
fn rbsp(payload: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(payload.len());
    let mut zeros = 0;
    for &b in payload {
        if zeros >= 2 && b == 3 {
            zeros = 0;
            continue;
        }
        zeros = if b == 0 { zeros + 1 } else { 0 };
        out.push(b);
    }
    out
}

/// An RBSP as a NAL unit's payload: an emulation prevention byte before each 00, 01, 02 or 03
/// that follows two zero bytes.
fn escaped(rbsp: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(rbsp.len() + rbsp.len() / 64 + 2);
    let mut zeros = 0;
    for &b in rbsp {
        if zeros >= 2 && b <= 3 {
            out.push(3);
            zeros = 0;
        }
        zeros = if b == 0 { zeros + 1 } else { 0 };
        out.push(b);
    }
    out
}

struct Bits<'a> {
    data: &'a [u8],
    at: usize, // (in bits)
}

impl Bits<'_> {
    fn bit(&mut self) -> Result<u32, String> {
        let byte = *self.data.get(self.at / 8).ok_or("an SPS cut short")?;
        self.at += 1;
        Ok(u32::from(byte >> (7 - (self.at - 1) % 8) & 1))
    }

    fn bits(&mut self, n: usize) -> Result<u32, String> {
        let mut v = 0;
        for _ in 0..n {
            v = v << 1 | self.bit()?;
        }
        Ok(v)
    }

    fn ue(&mut self) -> Result<u32, String> {
        let mut zeros = 0;
        while self.bit()? == 0 {
            zeros += 1;
            if zeros > 31 {
                return Err("an SPS with a number too long".into());
            }
        }
        Ok(((1u64 << zeros) - 1 + u64::from(self.bits(zeros)?)) as u32)
    }

    fn se(&mut self) -> Result<i64, String> {
        let k = i64::from(self.ue()?);
        Ok(if k % 2 == 1 { (k + 1) / 2 } else { -(k / 2) })
    }
}

#[derive(Default)]
struct Out {
    bytes: Vec<u8>,
    bits: usize,
}

impl Out {
    fn put(&mut self, v: u32, n: usize) {
        for i in (0..n).rev() {
            if self.bits.is_multiple_of(8) {
                self.bytes.push(0);
            }
            if v >> i & 1 == 1 {
                let last = self.bytes.len() - 1;
                self.bytes[last] |= 0x80 >> (self.bits % 8);
            }
            self.bits += 1;
        }
    }

    /// Bits `from..to` of `data`, as they are.
    fn copy(&mut self, data: &[u8], from: usize, to: usize) {
        for at in from..to {
            self.put(u32::from(data[at / 8] >> (7 - at % 8) & 1), 1);
        }
    }

    /// The stop bit and the zeros to the byte's end.
    fn trailing(mut self) -> Vec<u8> {
        self.put(1, 1);
        self.bytes
    }
}

/// Where an RBSP's data ends: before its stop bit (the last 1) and the zeros after it.
fn data_end(rbsp: &[u8]) -> Result<usize, String> {
    let last = rbsp
        .iter()
        .rposition(|&b| b != 0)
        .ok_or("an SPS of nothing")?;
    Ok(last * 8 + 7 - rbsp[last].trailing_zeros() as usize)
}

/// Steps `r` over an SPS's fields before its VUI flag.
fn skip_to_vui(r: &mut Bits) -> Result<(), String> {
    let profile = r.bits(8)?;
    r.bits(16)?; // the constraint flags and the level
    r.ue()?; // seq_parameter_set_id
    if matches!(
        profile,
        100 | 110 | 122 | 244 | 44 | 83 | 86 | 118 | 128 | 138 | 139 | 134 | 135
    ) {
        let chroma = r.ue()?;
        if chroma == 3 {
            r.bit()?; // separate_colour_plane_flag
        }
        r.ue()?; // bit_depth_luma_minus8
        r.ue()?; // bit_depth_chroma_minus8
        r.bit()?; // qpprime_y_zero_transform_bypass_flag
        if r.bit()? == 1 {
            // seq_scaling_matrix_present_flag: the lists, read to be stepped over
            for i in 0..if chroma == 3 { 12 } else { 8 } {
                if r.bit()? == 1 {
                    let size = if i < 6 { 16 } else { 64 };
                    let (mut last, mut next) = (8i64, 8i64);
                    for _ in 0..size {
                        if next != 0 {
                            next = (last + r.se()? + 256) % 256;
                        }
                        last = if next == 0 { last } else { next };
                    }
                }
            }
        }
    }
    r.ue()?; // log2_max_frame_num_minus4
    match r.ue()? {
        0 => {
            r.ue()?; // log2_max_pic_order_cnt_lsb_minus4
        }
        1 => {
            r.bit()?;
            r.se()?;
            r.se()?;
            for _ in 0..r.ue()? {
                r.se()?;
            }
        }
        _ => {}
    }
    r.ue()?; // max_num_ref_frames
    r.bit()?; // gaps_in_frame_num_value_allowed_flag
    r.ue()?; // pic_width_in_mbs_minus1
    r.ue()?; // pic_height_in_map_units_minus1
    if r.bit()? == 0 {
        r.bit()?; // mb_adaptive_frame_field_flag
    }
    r.bit()?; // direct_8x8_inference_flag
    if r.bit()? == 1 {
        for _ in 0..4 {
            r.ue()?; // the frame's crop
        }
    }
    Ok(())
}

/// An SPS (its NAL unit, header byte first) saying BT.601 in its VUI, in limited range or in
/// full: its colour description written anew there, the rest of it as it was (a VUI of the
/// colours alone where it had none). The same for an SPS that says them already.
pub fn stamp_sps(unit: &[u8], full: bool) -> Result<Vec<u8>, String> {
    if kind(unit) != SPS {
        return Err("not an SPS".into());
    }
    let data = rbsp(&unit[1..]);
    let end = data_end(&data)?;
    let mut r = Bits { data: &data, at: 0 };
    skip_to_vui(&mut r)?;
    let vui_at = r.at;
    let mut out = Out::default();
    out.put(u32::from(unit[0]), 8);
    let colours = |out: &mut Out, format: u32| {
        out.put(1, 1); // video_signal_type_present_flag
        out.put(format, 3);
        out.put(u32::from(full), 1);
        out.put(1, 1); // colour_description_present_flag
        for _ in 0..3 {
            out.put(6, 8); // SMPTE 170M (BT.601): primaries, transfer, matrix
        }
    };
    if r.bit()? == 0 {
        // no VUI: one of the colours alone, the rest of it not there
        out.copy(&data, 0, vui_at);
        out.put(1, 1);
        out.put(0, 2); // no aspect ratio, no overscan
        colours(&mut out, 5);
        out.put(0, 5); // no chroma location, timing, HRDs (NAL, VCL), picture structure
        out.put(0, 1); // no bitstream restriction
        out.copy(&data, r.at, end);
        return Ok(with_header(out.trailing()));
    }
    if r.bit()? == 1 && r.bits(8)? == 255 {
        r.bits(32)?; // aspect_ratio_idc 255: the sample's width and height
    }
    if r.bit()? == 1 {
        r.bit()?; // overscan_appropriate_flag
    }
    let signal_at = r.at;
    let mut format = 5;
    if r.bit()? == 1 {
        format = r.bits(3)?;
        r.bit()?; // video_full_range_flag
        if r.bit()? == 1 {
            r.bits(24)?; // the colour description
        }
    }
    out.copy(&data, 0, signal_at);
    colours(&mut out, format);
    out.copy(&data, r.at, end);
    Ok(with_header(out.trailing()))
}

fn with_header(rbsp_with_header: Vec<u8>) -> Vec<u8> {
    let mut unit = vec![rbsp_with_header[0]];
    unit.extend(escaped(&rbsp_with_header[1..]));
    unit
}

/// What a stream's pictures need to be the panels': each SPS stamped (stamp_sps), and the last
/// SPS and PPS before each keyframe that comes without them.
pub struct Headers {
    full: bool,
    sps: Option<Vec<u8>>,
    pps: Option<Vec<u8>>,
}

impl Headers {
    pub fn new(full: bool) -> Headers {
        Headers {
            full,
            sps: None,
            pps: None,
        }
    }

    /// Whether it has an SPS and a PPS, for the keyframes that come without them.
    pub fn knows(&self) -> bool {
        self.sps.is_some() && self.pps.is_some()
    }

    /// The SPS and PPS of an encoder that says them apart from its pictures (Media
    /// Foundation's MF_MT_MPEG_SEQUENCE_HEADER), for the keyframes that come without them.
    pub fn learn(&mut self, data: &[u8]) -> Result<(), String> {
        self.picture(data).map(drop)
    }

    /// One picture (its NAL units, Annex B) as the panels take it.
    pub fn picture(&mut self, data: &[u8]) -> Result<Vec<u8>, String> {
        let found = units(data);
        let has = |k: u8| found.iter().any(|&(a, b)| kind(&data[a..b]) == k);
        let (with_sps, with_pps) = (has(SPS), has(PPS));
        let mut out = Vec::with_capacity(data.len() + 64);
        let mut put = |unit: &[u8]| {
            out.extend([0, 0, 0, 1]);
            out.extend(unit);
        };
        let mut sent = false; // (the headers, before this picture's keyframe slices)
        for (a, b) in found {
            let unit = &data[a..b];
            match kind(unit) {
                SPS => {
                    let stamped = stamp_sps(unit, self.full)?;
                    put(&stamped);
                    self.sps = Some(stamped);
                }
                PPS => {
                    put(unit);
                    self.pps = Some(unit.to_vec());
                }
                IDR if !sent && !(with_sps && with_pps) => {
                    let (Some(sps), Some(pps)) = (&self.sps, &self.pps) else {
                        return Err("a keyframe before any SPS and PPS".into());
                    };
                    if !with_sps {
                        put(sps);
                    }
                    if !with_pps {
                        put(pps);
                    }
                    sent = true;
                    put(unit);
                }
                AUD | FILLER => {}
                _ => put(unit),
            }
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A stream of `n` frames of 64x32 made by x264 here, with its own colours.
    fn stream(full: bool, n: usize) -> Vec<Vec<u8>> {
        stream_of(full, n, "ultrafast")
    }

    /// The same with x264's `preset` (ultrafast: constrained baseline; superfast: high).
    fn stream_of(full: bool, n: usize, preset: &str) -> Vec<Vec<u8>> {
        let settings = crate::x264::Settings {
            width: 64,
            height: 32,
            fps: 25,
            crf: 23.0,
            kbit: 1000,
            keyint: 2,
            preset: preset.into(),
            profile: "high".into(),
            full,
            extra: String::new(),
        };
        let mut encoder = crate::x264::Encoder::open(&settings).unwrap();
        let (mut y, mut u, mut v) = (
            vec![90u8; 64 * 32],
            vec![128u8; 16 * 32],
            vec![128; 16 * 32],
        );
        let mut mb = vec![0u8; 4 * 2];
        (0..n)
            .map(|i| {
                y[i] = 200;
                let (picture, _key) = encoder
                    .encode(&mut y, &mut u, &mut v, 64, &mut mb, i as i64)
                    .unwrap();
                picture.to_vec()
            })
            .collect()
    }

    fn sps_of(picture: &[u8]) -> Vec<u8> {
        let (a, b) = units(picture)
            .into_iter()
            .find(|&(a, b)| kind(&picture[a..b]) == SPS)
            .unwrap();
        picture[a..b].to_vec()
    }

    /// `unit` (an SPS) without its VUI, as an encoder that writes none makes it.
    fn without_vui(unit: &[u8]) -> Vec<u8> {
        let data = rbsp(&unit[1..]);
        let mut r = Bits { data: &data, at: 0 };
        skip_to_vui(&mut r).unwrap();
        let mut out = Out::default();
        out.put(u32::from(unit[0]), 8);
        out.copy(&data, 0, r.at);
        out.put(0, 1); // vui_parameters_present_flag
        with_header(out.trailing())
    }

    #[test]
    #[ignore]
    fn h264_dump() {
        let path = std::env::var("LP_H264_DUMP").expect("LP_H264_DUMP");
        let mut headers = Headers::new(true);
        let mut out = Vec::new();
        for picture in stream(false, 6) {
            let bare: Vec<u8> = units(&picture)
                .into_iter()
                .flat_map(|(a, b)| {
                    let unit = &picture[a..b];
                    let unit = if kind(unit) == SPS {
                        without_vui(unit)
                    } else {
                        unit.to_vec()
                    };
                    [vec![0u8, 0, 0, 1], unit].concat()
                })
                .collect();
            out.extend(headers.picture(&bare).unwrap());
        }
        std::fs::write(path, out).unwrap();
    }

    #[test]
    fn units_of_an_annex_b_stream() {
        let data = [
            0, 0, 0, 1, 0x67, 1, 2, 0, 0, 1, 0x68, 3, 0, 0, 0, 1, 0x65, 9,
        ];
        let found = units(&data);
        assert_eq!(found, vec![(4, 7), (10, 12), (16, 18)]);
        assert_eq!(
            found
                .iter()
                .map(|&(a, b)| kind(&data[a..b]))
                .collect::<Vec<_>>(),
            [7, 8, 5]
        );
    }

    #[test]
    fn the_rbsp_and_its_escapes_go_back_and_forth() {
        let rbsp_ = [0u8, 0, 1, 0, 0, 0, 0, 0, 3, 7, 0, 0];
        let payload = escaped(&rbsp_);
        assert_eq!(payload, [0, 0, 3, 1, 0, 0, 3, 0, 0, 3, 0, 3, 7, 0, 0]);
        assert_eq!(rbsp(&payload), rbsp_);
    }

    #[test]
    fn an_sps_says_bt601_in_the_range_asked_for_and_stays_the_same_otherwise() {
        // x264 says BT.601 itself, in either range: the stamp of one is the SPS of the other
        for preset in ["ultrafast", "superfast"] {
            let limited = sps_of(&stream_of(false, 1, preset)[0]);
            let full = sps_of(&stream_of(true, 1, preset)[0]);
            assert_ne!(limited, full);
            for (sps, other, to_full) in [(&limited, &full, true), (&full, &limited, false)] {
                assert_eq!(&stamp_sps(sps, to_full).unwrap(), other, "{preset}");
                assert_eq!(&stamp_sps(sps, !to_full).unwrap(), sps); // (says it already)
            }
            let bare = without_vui(&limited);
            assert_eq!(
                stamp_sps(&stamp_sps(&bare, true).unwrap(), true).unwrap(),
                stamp_sps(&bare, true).unwrap()
            ); // fmt: skip
        }
        let limited = sps_of(&stream(false, 1)[0]);
        // one without a VUI gets one, of the colours: stamped again, it stays so
        let bare = without_vui(&limited);
        assert!(bare.len() < limited.len());
        let given = stamp_sps(&bare, true).unwrap();
        assert_ne!(given, bare);
        assert_eq!(stamp_sps(&given, true).unwrap(), given);
        assert_ne!(stamp_sps(&given, false).unwrap(), given);
        assert!(stamp_sps(&[0x68, 1, 2], true).is_err()); // (a PPS)
    }

    #[test]
    fn a_keyframe_without_its_headers_gets_the_last_ones_before_it() {
        let pictures = stream(false, 4);
        let mut headers = Headers::new(true);
        let first = headers.picture(&pictures[0]).unwrap();
        let kinds = |p: &[u8]| {
            units(p)
                .iter()
                .map(|&(a, b)| kind(&p[a..b]))
                .collect::<Vec<_>>()
        };
        assert!(kinds(&first).starts_with(&[SPS, PPS]));
        assert_eq!(
            sps_of(&first),
            stamp_sps(&sps_of(&pictures[0]), true).unwrap()
        );
        // a keyframe of a card that sends them only once: they come before it again
        let bare: Vec<u8> = {
            let p = &pictures[2];
            units(p)
                .into_iter()
                .filter(|&(a, b)| !matches!(kind(&p[a..b]), SPS | PPS))
                .flat_map(|(a, b)| [&[0u8, 0, 0, 1][..], &p[a..b]].concat())
                .collect()
        };
        assert!(kinds(&bare).contains(&IDR) && !kinds(&bare).contains(&SPS));
        let again = headers.picture(&bare).unwrap();
        let k = kinds(&again);
        let at = k.iter().position(|&x| x == IDR).unwrap();
        assert_eq!(&k[at - 2..at], &[SPS, PPS]);
        assert_eq!(
            sps_of(&again),
            stamp_sps(&sps_of(&pictures[0]), true).unwrap()
        );
        // a picture that is no keyframe goes as it is
        let other = &pictures[1];
        assert!(!kinds(other).contains(&IDR));
        assert_eq!(kinds(&headers.picture(other).unwrap()), kinds(other));
        assert!(Headers::new(false).picture(&bare).is_err());
    }

    #[test]
    fn headers_said_apart_serve_the_keyframes_and_delimiters_and_filler_go() {
        let pictures = stream(false, 3);
        let only = |p: &[u8], keep: &dyn Fn(u8) -> bool| -> Vec<u8> {
            units(p)
                .into_iter()
                .filter(|&(a, b)| keep(kind(&p[a..b])))
                .flat_map(|(a, b)| [&[0u8, 0, 0, 1][..], &p[a..b]].concat())
                .collect()
        };
        // the SPS and PPS as Media Foundation says them (MF_MT_MPEG_SEQUENCE_HEADER), and a
        // keyframe without them, a delimiter before it and filler after, as a card may make it
        let said = only(&pictures[0], &|k| matches!(k, SPS | PPS));
        let bare = only(&pictures[2], &|k| !matches!(k, SPS | PPS));
        let card = [
            &[0u8, 0, 0, 1, 0x09, 0xf0][..],
            &bare,
            &[0, 0, 0, 1, 0x0c, 0xff, 0xff, 0x80],
        ]
        .concat();
        let mut headers = Headers::new(false);
        headers.learn(&said).unwrap();
        let out = headers.picture(&card).unwrap();
        assert_eq!(out, Headers::new(false).picture(&pictures[2]).unwrap());
        let kinds: Vec<u8> = units(&out).iter().map(|&(a, b)| kind(&out[a..b])).collect();
        assert_eq!(&kinds[..2], &[SPS, PPS]);
        assert!(!kinds.contains(&AUD) && !kinds.contains(&FILLER));
    }
}
