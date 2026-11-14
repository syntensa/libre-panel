//! libx264 through the C bridge (x264_shim.c).

use std::ffi::{c_char, c_int, CStr, CString};

#[repr(C)]
struct Raw {
    _private: [u8; 0],
}

extern "C" {
    fn lp_x264_open(
        width: c_int,
        height: c_int,
        fps: c_int,
        qp: c_int,
        keyint: c_int,
        preset: *const c_char,
        profile: *const c_char,
        full: c_int,
        extra: *const c_char,
        err: *mut c_char,
        err_len: c_int,
    ) -> *mut Raw;
    fn lp_x264_encode(
        e: *mut Raw,
        y: *mut u8,
        u: *mut u8,
        v: *mut u8,
        stride_y: c_int,
        stride_c: c_int,
        mb_info: *mut u8,
        pts: i64,
        qp: c_int,
        out: *mut *const u8,
        keyframe: *mut c_int,
    ) -> c_int;
    fn lp_x264_keyframe(e: *mut Raw);
    fn lp_x264_close(e: *mut Raw);
    fn lp_x264_build() -> c_int;
}

#[derive(Clone)]
pub struct Settings {
    pub width: usize,
    pub height: usize,
    pub fps: u32,
    pub crf: f32,
    pub kbit: u32,
    pub keyint: u32,
    pub preset: String,
    pub profile: String,
    pub full: bool,
    pub extra: String,
}

pub struct Encoder {
    raw: *mut Raw,
    cap: Cap,
    keyint: u32,
    /// pictures since the last keyframe (None: none made yet)
    since_key: Option<u32>,
    key_asked: bool,
    /// the last picture's quantizer
    used: i32,
}

/// The quantizer x264 is given for `crf`, the quality config.toml names: two below it. x264
/// has no rate control of its own here (x264_shim.c), and a constant quantizer two below the
/// CRF the stream had before took as many bits on Lumen with a cleaner picture: on 1272 of
/// its frames at 25 a second (12.3", the demo's readings) 1472 kbit/s instead of 1501, PSNR
/// 53.96 dB instead of 53.07, the largest error 15 instead of 30 and 21416 pixels off by more
/// than 8 instead of 213274 (CRF's adaptive quantizer took the most from the edges of
/// letters), with a third fewer of x264's instructions a picture.
pub fn quantizer(crf: f32) -> i32 {
    ((crf - 2.0).round() as i32).clamp(1 + KEY_STEP, MAX_QP)
}

const MAX_QP: i32 = 51;
/// How far above the lowest quantizer the engine gives a picture x264 lets it go (its I
/// pictures' quantizer from an ip_factor of 0.01: 6 * log2(100), x264_shim.c).
const RANGE: i32 = 39;
/// A keyframe's quantizer below the pictures': x264's own (ip_factor 2.0).
const KEY_STEP: i32 = 6;
/// What a picture costs besides its changed macroblocks: its slice's header, the macroblocks
/// it skips.
const OVERHEAD_BITS: f64 = 400.0;
/// How far below the bucket's top a picture is fitted: what a picture's cost was foreseen
/// short by.
const FILL: f64 = 0.9;
/// The share of the cap the bit rate is kept at: below it the pictures take the settings'
/// quantizer; above it the quantizer rises (a sixth of an octave a step) until the rate is
/// back at it, settling within SETTLE_S, so that the bucket stays near empty for the big
/// pictures (a keyframe, a new screen) and the pictures stay alike.
const AIM: f64 = 0.85;
const SETTLE_S: f64 = 2.0;
/// Above this share of the bucket the quantizer rises STEEP steps more over the whole bucket,
/// where the rate's rise came too late.
const CALM: f64 = 0.5;
const STEEP: f64 = 20.0;
/// How many steps a picture's quantizer may come back down: where the quantizer falls, x264
/// makes what stayed the same anew (a macroblock is kept as it is only where it was made as
/// finely), and all at once would be a picture as big as a keyframe.
const BACK_STEPS: i32 = 1;
/// A keyframe takes at most this share of the bucket (x264's VBV made them as small at a tight
/// cap): the pictures after it need room too.
const KEY_SHARE: f64 = 0.4;

/// The bit rate's cap, kept as x264's VBV kept it before (its maxrate and buffer the cap,
/// one second's bits): a leaky bucket that each picture fills with its bits and each
/// picture's time drains by the cap's share of it. The pictures' quantizer follows the bit
/// rate of the last second (AIM, SETTLE_S) and how full the bucket is (CALM, STEEP), a
/// keyframe's KEY_STEP below theirs; a picture that would still fill it above FILL is made
/// coarser until it fits, its bits foreseen from how many of its macroblocks changed and what
/// one cost lately (at the quantizer it was made with, a sixth of an octave a step).
#[derive(Clone, Debug)]
pub struct Cap {
    base: i32,
    /// the highest quantizer x264 takes (x264_shim.c)
    top: i32,
    pub kbit: u32,
    fps: u32,
    fullness: f64,
    /// the bits of about the last second, a second's worth
    rate: f64,
    /// the pictures' quantizer above the settings' for the rate
    offset: f64,
    per_block: f64,
    per_block_key: f64,
    last: i32,
}

impl Cap {
    pub fn new(base: i32, kbit: u32, fps: u32) -> Cap {
        let base = base.clamp(1 + KEY_STEP, MAX_QP);
        Cap {
            base,
            top: (base - KEY_STEP + RANGE).min(MAX_QP),
            kbit,
            fps: fps.max(1),
            fullness: 0.0,
            rate: 0.0,
            offset: 0.0,
            // (what a changed macroblock of Lumen's took at the default, until one is seen)
            per_block: 200.0,
            per_block_key: 200.0,
            last: base,
        }
    }

    fn size(&self) -> f64 {
        self.kbit.max(1) as f64 * 1000.0
    }

    fn drain(&self) -> f64 {
        self.size() / self.fps as f64
    }

    /// The bits foreseen for a picture of `blocks` changed macroblocks at quantizer `qp`.
    fn foreseen(&self, qp: i32, blocks: usize, key: bool) -> f64 {
        let (per, at) = if key {
            (self.per_block_key, self.base - KEY_STEP)
        } else {
            (self.per_block, self.base)
        };
        OVERHEAD_BITS + blocks as f64 * per * 2f64.powf((at - qp) as f64 / 6.0)
    }

    /// The quantizer for a picture with `changed` of its `total` macroblocks changed.
    pub fn choose(&self, changed: usize, total: usize, key: bool) -> i32 {
        let filled = (self.fullness - self.drain()).max(0.0);
        let mut room = FILL * self.size() - filled;
        let pictures = self.level(filled);
        let (blocks, floor) = if key {
            room = room.min(KEY_SHARE * self.size());
            (total, pictures - KEY_STEP)
        } else {
            (changed, pictures)
        };
        let floor = floor.clamp(self.base - KEY_STEP, self.top);
        (floor..=self.top)
            .find(|&qp| self.foreseen(qp, blocks, key) <= room)
            .unwrap_or(self.top)
    }

    /// The pictures' quantizer for a bucket with `filled` bits in it, before a picture is
    /// fitted to it: from how full it is, and never more than BACK_STEPS below the last.
    fn level(&self, filled: f64) -> i32 {
        let over = (filled / self.size() - CALM).max(0.0);
        let wanted = self.base + self.offset.round() as i32 + (STEEP * over).ceil() as i32;
        wanted.max(self.last - BACK_STEPS).min(self.top)
    }

    /// A picture made: its bits, its quantizer and what changed in it.
    pub fn took(&mut self, bits: f64, qp: i32, changed: usize, total: usize, key: bool) {
        let filled = (self.fullness - self.drain()).max(0.0);
        if !key {
            // (the level the pictures were at, not a picture fitted coarser than it)
            self.last = self.level(filled);
        }
        self.fullness = filled + bits;
        let fps = self.fps as f64;
        self.rate += (bits * fps - self.rate) / fps;
        let aim = AIM * self.size();
        self.offset += 6.0 / (SETTLE_S * fps) * (self.rate.max(1.0) / aim).log2();
        self.offset = self.offset.clamp(0.0, (self.top - self.base) as f64);
        let blocks = if key { total } else { changed };
        if blocks > 0 {
            let at = if key { self.base - KEY_STEP } else { self.base };
            let seen =
                (bits - OVERHEAD_BITS).max(0.0) / blocks as f64 * 2f64.powf((qp - at) as f64 / 6.0);
            let seen = seen.clamp(4.0, 20000.0);
            if key {
                self.per_block_key += 0.5 * (seen - self.per_block_key);
            } else {
                self.per_block += 0.25 * (seen - self.per_block);
            }
        }
    }

    /// The bits in the bucket now.
    #[cfg(test)]
    pub fn fullness(&self) -> f64 {
        self.fullness
    }
}

// (one thread owns it at a time)
unsafe impl Send for Encoder {}

impl Encoder {
    pub fn open(s: &Settings) -> Result<Encoder, String> {
        let preset = CString::new(s.preset.as_str()).map_err(|e| e.to_string())?;
        let profile = CString::new(s.profile.as_str()).map_err(|e| e.to_string())?;
        let extra = CString::new(s.extra.as_str()).map_err(|e| e.to_string())?;
        let mut err = [0 as c_char; 256];
        let raw = unsafe {
            lp_x264_open(
                s.width as c_int,
                s.height as c_int,
                s.fps as c_int,
                (quantizer(s.crf) - KEY_STEP) as c_int,
                s.keyint as c_int,
                preset.as_ptr(),
                profile.as_ptr(),
                s.full as c_int,
                extra.as_ptr(),
                err.as_mut_ptr(),
                err.len() as c_int,
            )
        };
        if raw.is_null() {
            let text = unsafe { CStr::from_ptr(err.as_ptr()) }
                .to_string_lossy()
                .into_owned();
            return Err(text);
        }
        Ok(Encoder {
            raw,
            cap: Cap::new(quantizer(s.crf), s.kbit, s.fps),
            keyint: s.keyint.max(1),
            since_key: None,
            key_asked: false,
            used: quantizer(s.crf),
        })
    }

    /// One picture of the planes (I420), told which macroblocks stayed the same: its NAL units
    /// and whether it is a keyframe.
    pub fn encode(
        &mut self,
        y: &mut [u8],
        u: &mut [u8],
        v: &mut [u8],
        stride: usize,
        mb: &mut [u8],
        pts: i64,
    ) -> Result<(&[u8], bool), String> {
        let total = mb.len();
        let changed = mb
            .iter()
            .filter(|&&m| m & crate::yuv::CONSTANT == 0)
            .count();
        // (x264 makes a keyframe every keyint pictures, and where one is asked for)
        let key_due = self.key_asked || self.since_key.is_none_or(|n| n + 1 >= self.keyint);
        let qp = self.cap.choose(changed, total, key_due);
        let mut out: *const u8 = std::ptr::null();
        let mut key: c_int = 0;
        let size = unsafe {
            lp_x264_encode(
                self.raw,
                y.as_mut_ptr(),
                u.as_mut_ptr(),
                v.as_mut_ptr(),
                stride as c_int,
                (stride / 2) as c_int,
                mb.as_mut_ptr(),
                pts,
                qp as c_int,
                &mut out,
                &mut key,
            )
        };
        if size < 0 {
            return Err("x264 could not encode the picture".into());
        }
        self.key_asked = false;
        self.since_key = Some(if key != 0 {
            0
        } else {
            self.since_key.map_or(0, |n| n + 1)
        });
        self.used = qp;
        self.cap
            .took(size.max(0) as f64 * 8.0, qp, changed, total, key != 0);
        if size == 0 || out.is_null() {
            return Ok((&[], false));
        }
        Ok((
            unsafe { std::slice::from_raw_parts(out, size as usize) },
            key != 0,
        ))
    }

    /// The bit rate's cap, changed in the running stream: the next picture is fitted to it.
    pub fn rate(&mut self, kbit: u32) -> Result<(), String> {
        self.cap.kbit = kbit;
        Ok(())
    }

    /// The quantizer the last picture was made with.
    #[cfg(test)]
    pub fn quantizer_used(&self) -> i32 {
        self.used
    }

    /// The next picture a keyframe.
    pub fn keyframe(&mut self) {
        self.key_asked = true;
        unsafe { lp_x264_keyframe(self.raw) }
    }
}

impl Drop for Encoder {
    fn drop(&mut self) {
        unsafe { lp_x264_close(self.raw) }
    }
}

pub fn build() -> i32 {
    unsafe { lp_x264_build() }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_quantizer_is_two_below_the_crf() {
        assert_eq!(quantizer(20.0), 18);
        assert_eq!(quantizer(23.0), 21);
        // (never so low that a keyframe's would be 0: x264 is lossless there)
        assert_eq!(quantizer(1.0), 1 + KEY_STEP);
        assert_eq!(quantizer(60.0), MAX_QP);
    }

    #[test]
    fn with_room_in_the_cap_a_picture_takes_the_settings_quantizer() {
        let cap = Cap::new(18, 32000, 25);
        assert_eq!(cap.choose(300, 5400, false), 18);
        assert_eq!(cap.choose(5400, 5400, false), 18);
        assert_eq!(cap.choose(5400, 5400, true), 18 - KEY_STEP);
    }

    #[test]
    fn a_big_picture_raises_the_quantizer_and_it_comes_back_a_step_a_picture() {
        let mut cap = Cap::new(18, 2000, 25);
        // (a second's bits in one picture: the bucket full, the rate far over the cap)
        cap.took(2_000_000.0, 18, 5400, 5400, false);
        let raised = cap.choose(300, 5400, false);
        assert!(raised > 18, "{raised}");
        let mut level = cap.level((cap.fullness() - cap.drain()).max(0.0));
        for _ in 0..100 {
            let qp = cap.choose(0, 5400, false);
            cap.took(500.0, qp, 0, 5400, false);
            let next = cap.level((cap.fullness() - cap.drain()).max(0.0));
            assert!(next >= level - BACK_STEPS, "{level} -> {next}");
            level = next;
        }
        assert_eq!(cap.choose(300, 5400, false), 18);
        assert!(cap.fullness() < 1000.0);
    }

    #[test]
    fn a_bigger_picture_foreseen_takes_a_coarser_quantizer() {
        let cap = Cap::new(18, 1000, 25);
        let small = cap.choose(100, 5400, false);
        let big = cap.choose(5400, 5400, false);
        assert_eq!(small, 18);
        assert!(big > small, "{big}");
        // (each step a sixth of an octave fewer bits, as foreseen)
        let ratio = cap.foreseen(24, 5400, false) / cap.foreseen(18, 5400, false);
        assert!((ratio - 0.5).abs() < 0.02, "{ratio}");
    }

    /// Frames of 320x176 with a square of cells (2x2 pixels, a new grey each frame) that moves
    /// 8 pixels a frame, the blocks it left and reached marked as changed: x264 at a cap of
    /// `kbit`, the pictures' bits and quantizers.
    fn cells_stream(kbit: u32, frames: usize) -> Vec<(f64, i32)> {
        let (w, h) = (320usize, 176usize);
        let settings = Settings {
            width: w,
            height: h,
            fps: 25,
            crf: 20.0,
            kbit,
            keyint: 50,
            preset: "superfast".into(),
            profile: "high".into(),
            full: false,
            extra: "no-deblock=1:subme=0".into(),
        };
        let mut encoder = Encoder::open(&settings).unwrap();
        let (mut y, mut u, mut v) = (
            vec![30u8; w * h],
            vec![128u8; w * h / 4],
            vec![128u8; w * h / 4],
        );
        let (cols, rows) = (w / 16, h / 16);
        let mut seed = 0x2545_f491_u32;
        let mut grey = || {
            seed ^= seed << 13;
            seed ^= seed >> 17;
            seed ^= seed << 5;
            40 + (seed >> 24) as u8 % 180
        };
        let mut was = None;
        (0..frames)
            .map(|i| {
                let x0 = (i * 8) % (w - 64);
                let y0 = 48;
                y.iter_mut().for_each(|p| *p = 30);
                for r in (y0..y0 + 64).step_by(2) {
                    for c in (x0..x0 + 64).step_by(2) {
                        let g = grey();
                        for (dr, dc) in [(0, 0), (0, 1), (1, 0), (1, 1)] {
                            y[(r + dr) * w + c + dc] = g;
                        }
                    }
                }
                let mut mb = vec![crate::yuv::CONSTANT; cols * rows];
                for (from, _) in [(x0, 0), (was.unwrap_or(x0), 0)] {
                    for mr in y0 / 16..(y0 + 64).div_ceil(16) {
                        for mc in from / 16..(from + 64).div_ceil(16) {
                            mb[mr * cols + mc] = 0;
                        }
                    }
                }
                was = Some(x0);
                let (picture, _key) = encoder
                    .encode(&mut y, &mut u, &mut v, w, &mut mb, i as i64)
                    .unwrap();
                (picture.len() as f64 * 8.0, encoder.quantizer_used())
            })
            .collect()
    }

    #[test]
    fn the_stream_keeps_to_its_cap_as_a_bucket_of_a_second() {
        let kbit = 250;
        let made = cells_stream(kbit, 150);
        let bits: Vec<f64> = made.iter().map(|m| m.0).collect();
        let (size, drain) = (kbit as f64 * 1000.0, kbit as f64 * 1000.0 / 25.0);
        let mut fullness = 0f64;
        for (i, b) in bits.iter().enumerate() {
            fullness = (fullness - drain).max(0.0) + b;
            assert!(
                fullness <= size,
                "picture {i}: the bucket at {fullness:.0} of {size:.0}"
            );
        }
        // (over the whole stream no more than the cap's share of its time and a bucket)
        let total: f64 = bits.iter().sum();
        assert!(total <= drain * bits.len() as f64 + size, "{total:.0}");
        // (at the settings' quantizer while the bucket is less than half full)
        assert!(made[1..10].iter().all(|m| m.1 == 18), "{:?}", &made[..10]);
        // (the same frames with room enough take many more bits: the cap was what held them)
        let free: f64 = cells_stream(32000, 150).iter().map(|m| m.0).sum();
        assert!(free > 1.5 * total, "{free:.0} {total:.0}");
    }
}
