//! H.264 from the graphics card's own encoder, through Windows' Media Foundation (AMD's,
//! NVIDIA's, Intel's: the hardware encoder Windows lists first): the processor hands it each
//! frame in NV12 and takes the picture back, as with x264, but the card does the encoding. Its
//! bit rate changes in the running stream, a keyframe comes when asked for, and its pictures
//! go through h264.rs's Headers (each SPS saying BT.601 in the range of the frames, SPS and PPS
//! before every keyframe), as the panels take them.
//!
//! A hardware encoder runs on its own (an asynchronous MFT): it asks for a frame
//! (METransformNeedInput) and says when a picture is there (METransformHaveOutput). A frame
//! goes in when it asks; its picture is waited for, a little while (low latency: AMD's gives
//! it before it takes the next frame). One that takes the next frame before it gives the
//! picture of this one (it keeps a frame or two in hand) gives each picture a frame or two
//! later; it is waited for once, then not again. One picture goes out with each frame, never
//! two at once. Any error is the caller's to take x264 instead (encoder.rs).
//!
//! Media Foundation's own functions come from mfplat.dll as it is found, not linked: a Windows
//! without it (an N edition without its Media Feature Pack, a server without Media
//! Foundation) still starts the engine, and x264 encodes there.

use std::collections::VecDeque;
use std::ffi::c_void;
use std::mem::ManuallyDrop;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use windows::core::{s, w, Interface, GUID, HRESULT, PWSTR};
use windows::Win32::Foundation::VARIANT_TRUE;
use windows::Win32::Media::MediaFoundation::{
    eAVEncCommonRateControlMode_PeakConstrainedVBR, eAVEncH264VProfile_Base,
    eAVEncH264VProfile_High, eAVEncH264VProfile_Main, CODECAPI_AVEncCommonBufferSize,
    CODECAPI_AVEncCommonMaxBitRate, CODECAPI_AVEncCommonMeanBitRate,
    CODECAPI_AVEncCommonQualityVsSpeed, CODECAPI_AVEncCommonRateControlMode,
    CODECAPI_AVEncH264CABACEnable, CODECAPI_AVEncMPVDefaultBPictureCount, CODECAPI_AVEncMPVGOPSize,
    CODECAPI_AVEncVideoForceKeyFrame, CODECAPI_AVLowLatencyMode, ICodecAPI, IMFActivate,
    IMFMediaBuffer, IMFMediaEventGenerator, IMFMediaType, IMFSample, IMFShutdown, IMFTransform,
    MEError, METransformHaveOutput, METransformNeedInput, MFMediaType_Video, MFNominalRange_0_255,
    MFNominalRange_16_235, MFT_FRIENDLY_NAME_Attribute, MFVideoFormat_H264, MFVideoFormat_NV12,
    MFVideoInterlace_Progressive, MFVideoPrimaries_SMPTE170M, MFVideoTransFunc_709,
    MFVideoTransferMatrix_BT601, MFSTARTUP_FULL, MFT_CATEGORY_VIDEO_ENCODER,
    MFT_ENUM_FLAG_HARDWARE, MFT_ENUM_FLAG_SORTANDFILTER, MFT_MESSAGE_COMMAND_FLUSH,
    MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, MFT_MESSAGE_NOTIFY_END_OF_STREAM,
    MFT_MESSAGE_NOTIFY_END_STREAMING, MFT_MESSAGE_NOTIFY_START_OF_STREAM, MFT_OUTPUT_DATA_BUFFER,
    MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES, MFT_OUTPUT_STREAM_PROVIDES_SAMPLES,
    MFT_REGISTER_TYPE_INFO, MF_EVENT_FLAG_NO_WAIT, MF_E_NO_EVENTS_AVAILABLE,
    MF_E_TRANSFORM_NEED_MORE_INPUT, MF_E_TRANSFORM_STREAM_CHANGE, MF_LOW_LATENCY,
    MF_MT_AVG_BITRATE, MF_MT_FRAME_RATE, MF_MT_FRAME_SIZE, MF_MT_INTERLACE_MODE, MF_MT_MAJOR_TYPE,
    MF_MT_MPEG2_PROFILE, MF_MT_MPEG_SEQUENCE_HEADER, MF_MT_PIXEL_ASPECT_RATIO, MF_MT_SUBTYPE,
    MF_MT_TRANSFER_FUNCTION, MF_MT_VIDEO_NOMINAL_RANGE, MF_MT_VIDEO_PRIMARIES, MF_MT_YUV_MATRIX,
    MF_TRANSFORM_ASYNC_UNLOCK, MF_VERSION,
};
use windows::Win32::System::Com::{CoInitializeEx, CoTaskMemFree, COINIT_MULTITHREADED};
use windows::Win32::System::LibraryLoader::{
    GetProcAddress, LoadLibraryExW, LOAD_LIBRARY_SEARCH_SYSTEM32,
};
use windows::Win32::System::Variant::{VARIANT, VT_BOOL, VT_UI4};

use crate::h264::Headers;

/// How long a frame's picture is waited for once it went in (a card takes a few milliseconds).
const PICTURE_WAIT: Duration = Duration::from_millis(100);
/// How long the card may take to ask for the next frame before it counts as stuck.
const ASK_WAIT: Duration = Duration::from_secs(1);
/// The room a picture is given where the card wants it given room (one that provides none).
const PICTURE_ROOM: u32 = 4 << 20;

pub struct Settings {
    pub width: usize,
    pub height: usize,
    pub fps: u32,
    pub kbit: u32,
    pub keyint: u32,
    /// "baseline", "main" or "high", as for x264
    pub profile: String,
    pub full: bool,
    /// only an encoder that is proven with the panels (encoder.rs's `proven`): `--encoder auto`
    pub proven_only: bool,
}

pub struct Encoder {
    api: &'static Mf,
    /// what made the encoder: told to shut it down at the end, as Media Foundation asks
    activate: IMFActivate,
    transform: IMFTransform,
    events: IMFMediaEventGenerator,
    codec: Option<ICodecAPI>,
    /// what the encoder is (its name as Windows lists it), for the log
    pub name: String,
    width: usize,
    height: usize,
    fps: u32,
    /// frames it asked for that have not gone in yet
    wanted: u32,
    keyframe: bool,
    provides: bool,
    /// it takes the next frame before it gives this one's picture: no waiting for it
    pipelined: bool,
    headers: Headers,
    /// its pictures as they came, one to go out with each frame
    pictures: VecDeque<Vec<u8>>,
    started: bool,
    /// (the last field: Media Foundation shut down after everything made with it went)
    _platform: Platform,
}

/// Media Foundation, started: shut down again when this goes.
struct Platform(&'static Mf);

impl Drop for Platform {
    fn drop(&mut self) {
        // SAFETY: once for the MFStartup that made this.
        let _ = unsafe { (self.0.shutdown)() };
    }
}

// The encoder is used from one thread at a time (the stream's, which opened it).
unsafe impl Send for Encoder {}

fn failed(what: &str) -> impl Fn(windows::core::Error) -> String + '_ {
    move |e| format!("the card's encoder: {what}: {e}")
}

/// What GetProcAddress gives, and the functions as Media Foundation's headers have them.
type Found = unsafe extern "system" fn() -> isize;
type Startup = unsafe extern "system" fn(u32, u32) -> HRESULT;
type Shutdown = unsafe extern "system" fn() -> HRESULT;
type EnumEx = unsafe extern "system" fn(
    GUID,
    u32,
    *const MFT_REGISTER_TYPE_INFO,
    *const MFT_REGISTER_TYPE_INFO,
    *mut *mut Option<IMFActivate>,
    *mut u32,
) -> HRESULT;
type Make = unsafe extern "system" fn(*mut *mut c_void) -> HRESULT;
type MakeBuffer = unsafe extern "system" fn(u32, *mut *mut c_void) -> HRESULT;

/// Media Foundation's functions the encoder calls, from mfplat.dll.
struct Mf {
    startup: Startup,
    shutdown: Shutdown,
    enum_ex: EnumEx,
    create_media_type: Make,
    create_memory_buffer: MakeBuffer,
    create_sample: Make,
}

/// Media Foundation, loaded once; why not where it is not there.
fn mf() -> Result<&'static Mf, String> {
    static MF: OnceLock<Result<Mf, String>> = OnceLock::new();
    MF.get_or_init(|| {
        // SAFETY: the system's own mfplat.dll (System32 only), kept loaded; each function's
        // address taken as the type Media Foundation's headers give it.
        unsafe {
            let module = LoadLibraryExW(w!("mfplat.dll"), None, LOAD_LIBRARY_SEARCH_SYSTEM32)
                .map_err(|e| format!("no Media Foundation on this Windows (mfplat.dll: {e})"))?;
            let find = |name: windows::core::PCSTR| {
                GetProcAddress(module, name).ok_or_else(|| {
                    format!(
                        "a Media Foundation without {}",
                        name.to_string().unwrap_or_default()
                    )
                })
            };
            Ok(Mf {
                startup: std::mem::transmute::<Found, Startup>(find(s!("MFStartup"))?),
                shutdown: std::mem::transmute::<Found, Shutdown>(find(s!("MFShutdown"))?),
                enum_ex: std::mem::transmute::<Found, EnumEx>(find(s!("MFTEnumEx"))?),
                create_media_type: std::mem::transmute::<Found, Make>(find(s!(
                    "MFCreateMediaType"
                ))?),
                create_memory_buffer: std::mem::transmute::<Found, MakeBuffer>(find(s!(
                    "MFCreateMemoryBuffer"
                ))?),
                create_sample: std::mem::transmute::<Found, Make>(find(s!("MFCreateSample"))?),
            })
        }
    })
    .as_ref()
    .map_err(Clone::clone)
}

impl Mf {
    /// A new interface of what `make` makes.
    unsafe fn made<T: Interface>(make: Make) -> windows::core::Result<T> {
        let mut raw = std::ptr::null_mut();
        // SAFETY: `make` gives a new reference to its interface, taken over here.
        unsafe {
            make(&mut raw).ok()?;
            Ok(T::from_raw(raw))
        }
    }

    unsafe fn media_type(&self) -> windows::core::Result<IMFMediaType> {
        // SAFETY: MFCreateMediaType's.
        unsafe { Mf::made(self.create_media_type) }
    }

    unsafe fn sample(&self) -> windows::core::Result<IMFSample> {
        // SAFETY: MFCreateSample's.
        unsafe { Mf::made(self.create_sample) }
    }

    unsafe fn memory_buffer(&self, length: u32) -> windows::core::Result<IMFMediaBuffer> {
        let mut raw = std::ptr::null_mut();
        // SAFETY: as made.
        unsafe {
            (self.create_memory_buffer)(length, &mut raw).ok()?;
            Ok(IMFMediaBuffer::from_raw(raw))
        }
    }
}

fn variant_u32(value: u32) -> VARIANT {
    let mut v = VARIANT::default();
    // SAFETY: a VARIANT's tag and the value of its type, written together.
    unsafe {
        let inner = &mut *v.Anonymous.Anonymous;
        inner.vt = VT_UI4;
        inner.Anonymous.ulVal = value;
    }
    v
}

fn variant_true() -> VARIANT {
    let mut v = VARIANT::default();
    // SAFETY: as variant_u32.
    unsafe {
        let inner = &mut *v.Anonymous.Anonymous;
        inner.vt = VT_BOOL;
        inner.Anonymous.boolVal = VARIANT_TRUE;
    }
    v
}

impl Encoder {
    /// The first hardware H.264 encoder Windows lists, set up for `s`; an error where there is
    /// none, or it does not take what the panels need.
    pub fn open(s: &Settings) -> Result<Encoder, String> {
        if !s.width.is_multiple_of(2) || !s.height.is_multiple_of(2) {
            return Err(format!(
                "the card's encoder: frames of {}x{} (NV12 wants them even)",
                s.width, s.height
            ));
        }
        // SAFETY: COM and Media Foundation calls as documented, each result checked; what
        // MFTEnumEx allocates is released below.
        unsafe {
            let api = mf().map_err(|e| format!("the card's encoder: {e}"))?;
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED); // (already, on this thread: fine)
            (api.startup)(MF_VERSION, MFSTARTUP_FULL)
                .ok()
                .map_err(failed("Media Foundation"))?;
            let platform = Platform(api); // (dropped last, on any error below too)
            let activate = first_hardware_encoder(api)?;
            let name = friendly_name(&activate);
            if s.proven_only && !crate::encoder::proven(&name) {
                return Err(format!(
                    "the graphics card's encoder ({name}) is not proven with the panels yet"
                ));
            }
            let transform: IMFTransform = match activate.ActivateObject() {
                Ok(transform) => transform,
                Err(e) => {
                    let _ = activate.ShutdownObject();
                    return Err(failed("its start")(e));
                }
            };
            let events: Result<IMFMediaEventGenerator, _> = transform.cast();
            let events = match events {
                Ok(events) => events,
                Err(e) => {
                    let _ = activate.ShutdownObject();
                    return Err(failed("its events (not an asynchronous one)")(e));
                }
            };
            // (from here on Drop shuts it down, and Media Foundation after it)
            let mut encoder = Encoder {
                api,
                activate,
                transform,
                events,
                codec: None,
                name,
                width: s.width,
                height: s.height,
                fps: s.fps.max(1),
                wanted: 0,
                keyframe: true,
                provides: false,
                pipelined: false,
                headers: Headers::new(s.full),
                pictures: VecDeque::new(),
                started: false,
                _platform: platform,
            };
            let attributes = encoder
                .transform
                .GetAttributes()
                .map_err(failed("its attributes"))?;
            attributes
                .SetUINT32(&MF_TRANSFORM_ASYNC_UNLOCK, 1)
                .map_err(failed("unlocking it"))?;
            let _ = attributes.SetUINT32(&MF_LOW_LATENCY, 1);
            encoder.codec = encoder.transform.cast().ok();
            // (some of its settings it takes only before its types, some only after: both)
            encoder.tune(s);
            encoder.types(s)?;
            encoder.tune(s);
            encoder.sequence_header();
            let info = encoder
                .transform
                .GetOutputStreamInfo(0)
                .map_err(failed("its output"))?;
            let gives =
                MFT_OUTPUT_STREAM_PROVIDES_SAMPLES.0 | MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES.0;
            encoder.provides = info.dwFlags & gives as u32 != 0;
            for message in [
                MFT_MESSAGE_NOTIFY_BEGIN_STREAMING,
                MFT_MESSAGE_NOTIFY_START_OF_STREAM,
            ] {
                encoder
                    .transform
                    .ProcessMessage(message, 0)
                    .map_err(failed("its start"))?;
            }
            encoder.started = true;
            Ok(encoder)
        }
    }

    /// The output type (H.264 of the frames' size and rate, the profile, the bit rate, the
    /// colours) and then the input type (NV12 alike), as an encoder wants them set.
    unsafe fn types(&mut self, s: &Settings) -> Result<(), String> {
        // SAFETY: Media Foundation calls on types this function made.
        unsafe {
            let size = (s.width as u64) << 32 | s.height as u64;
            let rate = u64::from(self.fps) << 32 | 1;
            let range = if s.full {
                MFNominalRange_0_255
            } else {
                MFNominalRange_16_235
            };
            let out = self.api.media_type().map_err(failed("a type"))?;
            out.SetGUID(&MF_MT_MAJOR_TYPE, &MFMediaType_Video)
                .map_err(failed("its type"))?;
            out.SetGUID(&MF_MT_SUBTYPE, &MFVideoFormat_H264)
                .map_err(failed("its type"))?;
            out.SetUINT32(&MF_MT_AVG_BITRATE, s.kbit.saturating_mul(1000))
                .map_err(failed("its bit rate"))?;
            out.SetUINT64(&MF_MT_FRAME_SIZE, size)
                .map_err(failed("its size"))?;
            out.SetUINT64(&MF_MT_FRAME_RATE, rate)
                .map_err(failed("its rate"))?;
            out.SetUINT64(&MF_MT_PIXEL_ASPECT_RATIO, 1 << 32 | 1)
                .map_err(failed("its pixels"))?;
            out.SetUINT32(&MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive.0 as u32)
                .map_err(failed("its frames"))?;
            let profile = match s.profile.as_str() {
                "baseline" => eAVEncH264VProfile_Base,
                "high" => eAVEncH264VProfile_High,
                _ => eAVEncH264VProfile_Main,
            };
            out.SetUINT32(&MF_MT_MPEG2_PROFILE, profile.0 as u32)
                .map_err(failed("its profile"))?;
            // (said, for one that writes them; the SPS says them anyway: h264.rs)
            let _ = out.SetUINT32(&MF_MT_VIDEO_NOMINAL_RANGE, range.0 as u32);
            let _ = out.SetUINT32(&MF_MT_YUV_MATRIX, MFVideoTransferMatrix_BT601.0 as u32);
            let _ = out.SetUINT32(&MF_MT_VIDEO_PRIMARIES, MFVideoPrimaries_SMPTE170M.0 as u32);
            let _ = out.SetUINT32(&MF_MT_TRANSFER_FUNCTION, MFVideoTransFunc_709.0 as u32);
            self.transform
                .SetOutputType(0, &out, 0)
                .map_err(failed("the H.264 it makes"))?;
            let input = self.api.media_type().map_err(failed("a type"))?;
            input
                .SetGUID(&MF_MT_MAJOR_TYPE, &MFMediaType_Video)
                .map_err(failed("its type"))?;
            input
                .SetGUID(&MF_MT_SUBTYPE, &MFVideoFormat_NV12)
                .map_err(failed("its type"))?;
            input
                .SetUINT64(&MF_MT_FRAME_SIZE, size)
                .map_err(failed("its size"))?;
            input
                .SetUINT64(&MF_MT_FRAME_RATE, rate)
                .map_err(failed("its rate"))?;
            input
                .SetUINT64(&MF_MT_PIXEL_ASPECT_RATIO, 1 << 32 | 1)
                .map_err(failed("its pixels"))?;
            input
                .SetUINT32(&MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive.0 as u32)
                .map_err(failed("its frames"))?;
            let _ = input.SetUINT32(&MF_MT_VIDEO_NOMINAL_RANGE, range.0 as u32);
            self.transform
                .SetInputType(0, &input, 0)
                .map_err(failed("the frames it takes (NV12)"))?;
            Ok(())
        }
    }

    /// The SPS and PPS of an encoder that says them in its output type, for the keyframes that
    /// come without them (one that puts them in its pictures needs none of this).
    fn sequence_header(&mut self) {
        // SAFETY: a blob of the size the type says, read into a buffer of that size.
        unsafe {
            let Ok(kind) = self.transform.GetOutputCurrentType(0) else {
                return;
            };
            let Ok(size) = kind.GetBlobSize(&MF_MT_MPEG_SEQUENCE_HEADER) else {
                return;
            };
            let mut blob = vec![0u8; size as usize];
            if size > 0
                && kind
                    .GetBlob(&MF_MT_MPEG_SEQUENCE_HEADER, &mut blob, None)
                    .is_ok()
            {
                let _ = self.headers.learn(&blob);
            }
        }
    }

    /// What the panels' stream wants of it, where it takes it: low latency, no B-frames, a
    /// keyframe every `keyint` frames, the bit rate as a cap (peak-constrained VBR, AMD's
    /// vbr_peak of before), as fast as it goes.
    fn tune(&self, s: &Settings) {
        let Some(codec) = &self.codec else { return };
        let set = |api: &GUID, value: VARIANT| {
            // SAFETY: a GUID and a VARIANT that live through the call.
            let _ = unsafe { codec.SetValue(api, &value) };
        };
        let bits = s.kbit.saturating_mul(1000);
        set(&CODECAPI_AVLowLatencyMode, variant_true());
        set(&CODECAPI_AVEncMPVDefaultBPictureCount, variant_u32(0));
        set(&CODECAPI_AVEncMPVGOPSize, variant_u32(s.keyint.max(1)));
        set(
            &CODECAPI_AVEncCommonRateControlMode,
            variant_u32(eAVEncCommonRateControlMode_PeakConstrainedVBR.0 as u32),
        );
        set(&CODECAPI_AVEncCommonMeanBitRate, variant_u32(bits));
        set(&CODECAPI_AVEncCommonMaxBitRate, variant_u32(bits));
        set(&CODECAPI_AVEncCommonBufferSize, variant_u32(bits)); // (a second: x264's bufsize)
        set(&CODECAPI_AVEncCommonQualityVsSpeed, variant_u32(0));
        if s.profile != "baseline" {
            set(&CODECAPI_AVEncH264CABACEnable, variant_true());
        }
    }

    /// A new cap on the bit rate, from the next frame on.
    pub fn rate(&mut self, kbit: u32) -> Result<(), String> {
        let Some(codec) = &self.codec else {
            return Err("the card's encoder takes no bit rate in the running stream".into());
        };
        let bits = variant_u32(kbit.saturating_mul(1000));
        // SAFETY: as in tune.
        unsafe {
            codec
                .SetValue(&CODECAPI_AVEncCommonMeanBitRate, &bits)
                .map_err(failed("its bit rate"))?;
            let _ = codec.SetValue(&CODECAPI_AVEncCommonMaxBitRate, &bits);
            let _ = codec.SetValue(&CODECAPI_AVEncCommonBufferSize, &bits);
        }
        Ok(())
    }

    /// The next picture a keyframe.
    pub fn keyframe(&mut self) {
        self.keyframe = true;
    }

    /// The picture of a frame (I420 planes, `stride` wide), Annex B as the panels take it;
    /// empty where the card has none yet (it comes with the next).
    pub fn encode(
        &mut self,
        y: &[u8],
        u: &[u8],
        v: &[u8],
        stride: usize,
        pts: i64,
    ) -> Result<Vec<u8>, String> {
        let asked = Instant::now() + ASK_WAIT;
        while self.wanted == 0 {
            if !self.event(asked)? {
                return Err("the card's encoder asked for no frame in a second".into());
            }
        }
        self.wanted -= 1;
        // SAFETY: the planes are the frame's (checked against its size in give).
        unsafe { self.give(y, u, v, stride, pts)? };
        let wait = if self.pipelined {
            Duration::ZERO // (what came meanwhile, nothing waited for)
        } else {
            PICTURE_WAIT
        };
        let until = Instant::now() + wait;
        while self.pictures.is_empty() {
            if !self.event(until)? {
                if self.wanted > 0 && !self.pipelined {
                    // it took the frame and asks for the next before its picture: it keeps
                    // frames in hand, and gives each picture that much later
                    self.pipelined = true;
                    crate::encoder::note(&format!(
                        "{} gives each picture a frame or two after its frame",
                        self.name
                    ));
                }
                break;
            }
        }
        let Some(picture) = self.pictures.pop_front() else {
            return Ok(Vec::new());
        };
        if !self.headers.knows() {
            self.sequence_header(); // (one that says them only once it has begun)
        }
        self.headers.picture(&picture)
    }

    /// One event of the card's, waited for until `until`: whether one came.
    fn event(&mut self, until: Instant) -> Result<bool, String> {
        loop {
            // SAFETY: a call on the encoder's own event queue, not waiting.
            let got = unsafe { self.events.GetEvent(MF_EVENT_FLAG_NO_WAIT) };
            match got {
                Ok(event) => {
                    // SAFETY: an event the queue gave.
                    let kind = unsafe { event.GetType() }.map_err(failed("an event"))?;
                    if kind == METransformNeedInput.0 as u32 {
                        self.wanted += 1;
                    } else if kind == METransformHaveOutput.0 as u32 {
                        // SAFETY: the picture it said it has.
                        unsafe { self.take()? };
                    } else if kind == MEError.0 as u32 {
                        // SAFETY: as GetType.
                        let status = unsafe { event.GetStatus() }.map_err(failed("an event"))?;
                        return Err(format!(
                            "the card's encoder failed: {}",
                            windows::core::Error::from(status)
                        ));
                    }
                    return Ok(true);
                }
                Err(e) if e.code() == MF_E_NO_EVENTS_AVAILABLE => {
                    if Instant::now() >= until {
                        return Ok(false);
                    }
                    std::thread::sleep(Duration::from_micros(500));
                }
                Err(e) => return Err(failed("its events")(e)),
            }
        }
    }

    /// The frame into the card as NV12 (Y, then U and V side by side), at `pts` (frames), a
    /// keyframe where one was asked for.
    unsafe fn give(
        &mut self,
        y: &[u8],
        u: &[u8],
        v: &[u8],
        stride: usize,
        pts: i64,
    ) -> Result<(), String> {
        let (w, h) = (self.width, self.height);
        let (cw, cs) = (w / 2, stride / 2);
        if stride < w || y.len() < stride * h || u.len() < cs * (h / 2) || v.len() < cs * (h / 2) {
            return Err("the card's encoder: planes smaller than the frame".into());
        }
        let length = w * h * 3 / 2;
        // SAFETY: a buffer of `length` bytes, written within it while locked.
        unsafe {
            let buffer = self
                .api
                .memory_buffer(length as u32)
                .map_err(failed("a buffer"))?;
            let mut data: *mut u8 = std::ptr::null_mut();
            buffer
                .Lock(&mut data, None, None)
                .map_err(failed("a buffer"))?;
            let nv12 = std::slice::from_raw_parts_mut(data, length);
            for row in 0..h {
                nv12[row * w..row * w + w].copy_from_slice(&y[row * stride..row * stride + w]);
            }
            let chroma = &mut nv12[w * h..];
            for row in 0..h / 2 {
                let line = &mut chroma[row * w..row * w + w];
                let (us, vs) = (&u[row * cs..row * cs + cw], &v[row * cs..row * cs + cw]);
                for (pair, (&a, &b)) in line.chunks_exact_mut(2).zip(us.iter().zip(vs)) {
                    pair[0] = a;
                    pair[1] = b;
                }
            }
            buffer.Unlock().map_err(failed("a buffer"))?;
            buffer
                .SetCurrentLength(length as u32)
                .map_err(failed("a buffer"))?;
            let sample = self.api.sample().map_err(failed("a sample"))?;
            sample.AddBuffer(&buffer).map_err(failed("a sample"))?;
            let tick = 10_000_000 / i64::from(self.fps); // (100 ns a unit)
            sample
                .SetSampleTime(pts * tick)
                .map_err(failed("a sample"))?;
            sample.SetSampleDuration(tick).map_err(failed("a sample"))?;
            if std::mem::take(&mut self.keyframe) {
                if let Some(codec) = &self.codec {
                    let _ = codec.SetValue(&CODECAPI_AVEncVideoForceKeyFrame, &variant_u32(1));
                }
            }
            self.transform
                .ProcessInput(0, &sample, 0)
                .map_err(failed("a frame"))?;
            Ok(())
        }
    }

    /// The picture the card has, to `pictures`.
    unsafe fn take(&mut self) -> Result<(), String> {
        // SAFETY: the output buffer as ProcessOutput wants it, its sample and events released
        // after; a picture's buffer read while locked, within its length.
        unsafe {
            let mut out = MFT_OUTPUT_DATA_BUFFER {
                dwStreamID: 0,
                pSample: ManuallyDrop::new(None),
                dwStatus: 0,
                pEvents: ManuallyDrop::new(None),
            };
            if !self.provides {
                let info = self
                    .transform
                    .GetOutputStreamInfo(0)
                    .map_err(failed("its output"))?;
                let buffer = self
                    .api
                    .memory_buffer(info.cbSize.max(PICTURE_ROOM))
                    .map_err(failed("a buffer"))?;
                let sample = self.api.sample().map_err(failed("a sample"))?;
                sample.AddBuffer(&buffer).map_err(failed("a sample"))?;
                out.pSample = ManuallyDrop::new(Some(sample));
            }
            let mut status = 0u32;
            let done = self
                .transform
                .ProcessOutput(0, std::slice::from_mut(&mut out), &mut status);
            let sample = ManuallyDrop::take(&mut out.pSample);
            drop(ManuallyDrop::take(&mut out.pEvents));
            match done {
                Ok(()) => {}
                Err(e) if e.code() == MF_E_TRANSFORM_STREAM_CHANGE => {
                    // (it settled its output: the type it has now, the picture comes after)
                    let kind = self
                        .transform
                        .GetOutputAvailableType(0, 0)
                        .map_err(failed("its output type"))?;
                    self.transform
                        .SetOutputType(0, &kind, 0)
                        .map_err(failed("its output type"))?;
                    self.sequence_header();
                    return Ok(());
                }
                Err(e) if e.code() == MF_E_TRANSFORM_NEED_MORE_INPUT => return Ok(()),
                Err(e) => return Err(failed("a picture")(e)),
            }
            let Some(sample) = sample else {
                return Ok(());
            };
            let buffer = sample
                .ConvertToContiguousBuffer()
                .map_err(failed("a picture"))?;
            let mut data: *mut u8 = std::ptr::null_mut();
            let mut length = 0u32;
            buffer
                .Lock(&mut data, None, Some(&mut length))
                .map_err(failed("a picture"))?;
            let picture = std::slice::from_raw_parts(data, length as usize).to_vec();
            buffer.Unlock().map_err(failed("a picture"))?;
            if !picture.is_empty() {
                self.pictures.push_back(picture);
            }
            Ok(())
        }
    }
}

impl Drop for Encoder {
    fn drop(&mut self) {
        // SAFETY: the encoder told the stream ended and shut down, as an asynchronous one must
        // be (a card's sessions are few: NVIDIA's), and what made it told so; its interfaces
        // are released after this, Media Foundation last (the field _platform).
        unsafe {
            if self.started {
                let _ = self
                    .transform
                    .ProcessMessage(MFT_MESSAGE_NOTIFY_END_OF_STREAM, 0);
                let _ = self.transform.ProcessMessage(MFT_MESSAGE_COMMAND_FLUSH, 0);
                let _ = self
                    .transform
                    .ProcessMessage(MFT_MESSAGE_NOTIFY_END_STREAMING, 0);
            }
            if let Ok(shutdown) = self.transform.cast::<IMFShutdown>() {
                let _ = shutdown.Shutdown();
            }
            let _ = self.activate.ShutdownObject();
        }
    }
}

/// The first hardware encoder from NV12 to H.264 that Windows lists (its best, first).
unsafe fn first_hardware_encoder(api: &Mf) -> Result<IMFActivate, String> {
    let input = MFT_REGISTER_TYPE_INFO {
        guidMajorType: MFMediaType_Video,
        guidSubtype: MFVideoFormat_NV12,
    };
    let output = MFT_REGISTER_TYPE_INFO {
        guidMajorType: MFMediaType_Video,
        guidSubtype: MFVideoFormat_H264,
    };
    let mut found: *mut Option<IMFActivate> = std::ptr::null_mut();
    let mut count = 0u32;
    // SAFETY: MFTEnumEx fills the array and its count; each element is taken (released when
    // dropped) and the array freed with CoTaskMemFree, as documented.
    unsafe {
        (api.enum_ex)(
            MFT_CATEGORY_VIDEO_ENCODER,
            (MFT_ENUM_FLAG_HARDWARE | MFT_ENUM_FLAG_SORTANDFILTER).0 as u32,
            &input,
            &output,
            &mut found,
            &mut count,
        )
        .ok()
        .map_err(failed("its list"))?;
        if found.is_null() {
            return Err("no graphics card's H.264 encoder here".into());
        }
        let list = std::slice::from_raw_parts_mut(found, count as usize);
        let first = list.first_mut().and_then(Option::take);
        for item in list.iter_mut() {
            drop(item.take());
        }
        CoTaskMemFree(Some(found as *const _));
        first.ok_or_else(|| "no graphics card's H.264 encoder here".into())
    }
}

/// An encoder's name as Windows lists it ("AMDh264Encoder", "NVIDIA H.264 Encoder MFT").
fn friendly_name(activate: &IMFActivate) -> String {
    // SAFETY: a string the call allocates, read and then freed.
    unsafe {
        let mut text = PWSTR::null();
        let mut length = 0u32;
        if activate
            .GetAllocatedString(&MFT_FRIENDLY_NAME_Attribute, &mut text, &mut length)
            .is_err()
            || text.is_null()
        {
            return "a graphics card's H.264 encoder".into();
        }
        let name = text.to_string().unwrap_or_default();
        CoTaskMemFree(Some(text.0 as *const _));
        name
    }
}
