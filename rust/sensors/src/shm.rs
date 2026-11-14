//! Memory another program shares under a name (RTSS's, HWiNFO's): read, never made nor
//! written.

/// A shared memory, open: its bytes from `offset`.
pub trait Memory: Send {
    /// `size` bytes from `offset`; an error where they are not all there.
    fn read(&self, offset: usize, size: usize) -> Result<Vec<u8>, String>;
}

/// Bytes as a memory (the tests' own).
pub struct Bytes(pub Vec<u8>);

impl Memory for Bytes {
    fn read(&self, offset: usize, size: usize) -> Result<Vec<u8>, String> {
        let end = offset.checked_add(size).filter(|&end| end <= self.0.len());
        match end {
            Some(end) => Ok(self.0[offset..end].to_vec()),
            None => Err("beyond the memory".into()),
        }
    }
}

/// u32 at `at`, little-endian.
pub fn u32_at(data: &[u8], at: usize) -> Option<u32> {
    let bytes = data.get(at..at.checked_add(4)?)?;
    Some(u32::from_le_bytes(bytes.try_into().ok()?))
}

/// f64 at `at`, little-endian.
pub fn f64_at(data: &[u8], at: usize) -> Option<f64> {
    let bytes = data.get(at..at.checked_add(8)?)?;
    Some(f64::from_le_bytes(bytes.try_into().ok()?))
}

/// The memory shared under `name`, open; none where no program shares one (or this is no
/// Windows).
pub fn open(name: &str) -> Option<Box<dyn Memory>> {
    #[cfg(windows)]
    {
        windows::Mapping::open(name).map(|mapping| Box::new(mapping) as Box<dyn Memory>)
    }
    #[cfg(not(windows))]
    {
        let _ = name;
        None
    }
}

#[cfg(windows)]
mod windows {
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::Memory::{
        MapViewOfFile, OpenFileMappingW, UnmapViewOfFile, VirtualQuery, FILE_MAP_READ,
        MEMORY_BASIC_INFORMATION, MEMORY_MAPPED_VIEW_ADDRESS,
    };

    /// A mapping through Windows' own calls: OpenFileMapping, then MapViewOfFile (one that is
    /// not there is not made: the other program makes it).
    pub struct Mapping {
        handle: HANDLE,
        view: MEMORY_MAPPED_VIEW_ADDRESS,
        size: usize,
    }

    // SAFETY: the view is read-only memory of this process, valid until the mapping is
    // dropped; nothing of it is tied to a thread.
    unsafe impl Send for Mapping {}

    impl Mapping {
        pub fn open(name: &str) -> Option<Mapping> {
            let wide: Vec<u16> = name.encode_utf16().chain([0]).collect();
            // SAFETY: `wide` is a NUL-terminated name that lives through the call.
            let handle = unsafe { OpenFileMappingW(FILE_MAP_READ, 0, wide.as_ptr()) };
            if handle.is_null() {
                return None;
            }
            // SAFETY: `handle` is the mapping just opened; the whole of it is mapped to read.
            let view = unsafe { MapViewOfFile(handle, FILE_MAP_READ, 0, 0, 0) };
            if view.Value.is_null() {
                // SAFETY: `handle` is open and not used again.
                unsafe { CloseHandle(handle) };
                return None;
            }
            // SAFETY: MEMORY_BASIC_INFORMATION is plain data that zeroes are valid for.
            let mut info: MEMORY_BASIC_INFORMATION = unsafe { std::mem::zeroed() };
            // SAFETY: `view.Value` is the view's address and `info` has room for the answer.
            let got = unsafe {
                VirtualQuery(
                    view.Value,
                    &mut info,
                    std::mem::size_of::<MEMORY_BASIC_INFORMATION>(),
                )
            };
            let size = if got == 0 { 20 } else { info.RegionSize };
            Some(Mapping { handle, view, size })
        }
    }

    impl super::Memory for Mapping {
        fn read(&self, offset: usize, size: usize) -> Result<Vec<u8>, String> {
            if offset.checked_add(size).is_none_or(|end| end > self.size) {
                return Err("beyond the shared memory".into());
            }
            let mut out = vec![0u8; size];
            // SAFETY: offset + size lies within the view's region, which stays mapped while
            // `self` lives; `out` has room for `size` bytes and does not overlap the view.
            unsafe {
                let from = self.view.Value.cast::<u8>().add(offset);
                std::ptr::copy_nonoverlapping(from, out.as_mut_ptr(), size);
            }
            Ok(out)
        }
    }

    impl Drop for Mapping {
        fn drop(&mut self) {
            // SAFETY: the view and the handle are this mapping's own, let go once.
            unsafe {
                UnmapViewOfFile(self.view);
                CloseHandle(self.handle);
            }
        }
    }
}
