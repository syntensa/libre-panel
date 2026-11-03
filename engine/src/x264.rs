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
        crf: f32,
        kbit: c_int,
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
        out: *mut *const u8,
        keyframe: *mut c_int,
    ) -> c_int;
    fn lp_x264_rate(e: *mut Raw, kbit: c_int) -> c_int;
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
                s.crf,
                s.kbit as c_int,
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
        Ok(Encoder { raw })
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
                &mut out,
                &mut key,
            )
        };
        if size < 0 {
            return Err("x264 could not encode the picture".into());
        }
        if size == 0 || out.is_null() {
            return Ok((&[], false));
        }
        Ok((
            unsafe { std::slice::from_raw_parts(out, size as usize) },
            key != 0,
        ))
    }

    /// The bit rate's cap, changed in the running stream.
    pub fn rate(&mut self, kbit: u32) -> Result<(), String> {
        match unsafe { lp_x264_rate(self.raw, kbit as c_int) } {
            0 => Ok(()),
            _ => Err(format!("x264 did not take the bit rate {kbit} kbit/s")),
        }
    }

    /// The next picture a keyframe.
    pub fn keyframe(&mut self) {
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
