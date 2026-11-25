//! A HID screen through Windows' own HID driver (hidusb), for one that comes with it bound and no
//! WinUSB: its reports written and read through its HID device's path, as a [`UsbIo`] with one
//! HID interface whose interrupt endpoints OUT 01 and IN 81 stand for the reports. Windows
//! wants each report with its id in front: report id 0 goes before each written, and comes off
//! each read. No control transfer goes through it.

use std::ffi::c_void;
use std::ptr::{null, null_mut};

use windows_sys::core::GUID;
use windows_sys::Win32::Devices::DeviceAndDriverInstallation::{
    SetupDiDestroyDeviceInfoList, SetupDiEnumDeviceInterfaces, SetupDiGetClassDevsW,
    SetupDiGetDeviceInterfaceDetailW, DIGCF_DEVICEINTERFACE, DIGCF_PRESENT,
    SP_DEVICE_INTERFACE_DATA, SP_DEVICE_INTERFACE_DETAIL_DATA_W,
};
use windows_sys::Win32::Devices::HumanInterfaceDevice::{
    HidD_FreePreparsedData, HidD_GetAttributes, HidD_GetHidGuid, HidD_GetPreparsedData,
    HidD_GetProductString, HidP_GetCaps, HIDD_ATTRIBUTES, HIDP_CAPS, HIDP_STATUS_SUCCESS,
};
use windows_sys::Win32::Foundation::{
    CloseHandle, GetLastError, ERROR_ACCESS_DENIED, ERROR_IO_PENDING, ERROR_SHARING_VIOLATION,
    GENERIC_READ, GENERIC_WRITE, HANDLE, INVALID_HANDLE_VALUE, WAIT_OBJECT_0,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, ReadFile, WriteFile, FILE_FLAG_OVERLAPPED, FILE_SHARE_READ, FILE_SHARE_WRITE,
    OPEN_EXISTING,
};
use windows_sys::Win32::System::Threading::{CreateEventW, ResetEvent, WaitForSingleObject};
use windows_sys::Win32::System::IO::{CancelIoEx, GetOverlappedResult, OVERLAPPED};

use crate::base::{leave_place, take_place, tf, DeviceError, Result};
use crate::usb_link::{EndpointInfo, InterfaceInfo, UsbError, UsbIo, UsbResult, HID_CLASS};

/// The endpoints the reports stand in for.
pub const OUT: u8 = 0x01;
pub const IN: u8 = 0x81;

/// A HID device opened through Windows' HID driver.
pub struct WinHid {
    handle: HANDLE,
    event: HANDLE,
    /// the reports' lengths as Windows has them, the id's byte included
    out_len: usize,
    in_len: usize,
    product: String,
    place: String,
}

// SAFETY: the handles are this WinHid's alone and used by one thread at a time (a driver's link
// is behind its keeper's lock).
unsafe impl Send for WinHid {}

fn wide_to_string(text: &[u16]) -> String {
    let end = text.iter().position(|&c| c == 0).unwrap_or(text.len());
    String::from_utf16_lossy(&text[..end])
}

/// The paths of the HID devices with these ids that are there: by their paths
/// (`\\?\hid#vid_1d6b&pid_0103#...`), so that no other device is opened to be asked.
fn hid_paths_of(vendor: u16, product: u16) -> Vec<Vec<u16>> {
    let ids = format!("vid_{vendor:04x}&pid_{product:04x}");
    hid_paths()
        .into_iter()
        .filter(|path| wide_to_string(path).to_lowercase().contains(&ids))
        .collect()
}

/// The paths of the HID devices that are there.
fn hid_paths() -> Vec<Vec<u16>> {
    let mut paths = Vec::new();
    // SAFETY: the set is destroyed before the return; each detail is read from a buffer of the
    // size Windows asked for, aligned for its cbSize, and its path ends with a NUL.
    unsafe {
        let mut guid: GUID = std::mem::zeroed();
        HidD_GetHidGuid(&mut guid);
        let set = SetupDiGetClassDevsW(
            &guid,
            null(),
            null_mut(),
            DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
        );
        if set == INVALID_HANDLE_VALUE as isize {
            return paths;
        }
        for index in 0.. {
            let mut data: SP_DEVICE_INTERFACE_DATA = std::mem::zeroed();
            data.cbSize = std::mem::size_of::<SP_DEVICE_INTERFACE_DATA>() as u32;
            if SetupDiEnumDeviceInterfaces(set, null(), &guid, index, &mut data) == 0 {
                break;
            }
            let mut needed = 0u32;
            SetupDiGetDeviceInterfaceDetailW(set, &data, null_mut(), 0, &mut needed, null_mut());
            if needed < 8 {
                continue;
            }
            let mut buffer = vec![0u32; (needed as usize).div_ceil(4)];
            let detail = buffer
                .as_mut_ptr()
                .cast::<SP_DEVICE_INTERFACE_DETAIL_DATA_W>();
            (*detail).cbSize = std::mem::size_of::<SP_DEVICE_INTERFACE_DETAIL_DATA_W>() as u32;
            if SetupDiGetDeviceInterfaceDetailW(set, &data, detail, needed, null_mut(), null_mut())
                == 0
            {
                continue;
            }
            let start = std::ptr::addr_of!((*detail).DevicePath).cast::<u16>();
            let room = (needed as usize - 4) / 2;
            let path = std::slice::from_raw_parts(start, room);
            let end = path.iter().position(|&c| c == 0).unwrap_or(room);
            let mut path = path[..end].to_vec();
            path.push(0);
            paths.push(path);
        }
        SetupDiDestroyDeviceInfoList(set);
    }
    paths
}

/// What a HID device at a path is: its ids and its reports' lengths.
struct Seen {
    vendor: u16,
    product: u16,
    out_len: usize,
    in_len: usize,
}

/// seen: a HID device looked at without reading or writing it; None where it cannot be.
fn seen(path: &[u16]) -> Option<Seen> {
    // SAFETY: a NUL-ended path; the handle and the preparsed data are let go before the return.
    unsafe {
        let handle = CreateFileW(
            path.as_ptr(),
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            null(),
            OPEN_EXISTING,
            0,
            null_mut(),
        );
        if handle == INVALID_HANDLE_VALUE {
            return None;
        }
        let mut attributes: HIDD_ATTRIBUTES = std::mem::zeroed();
        attributes.Size = std::mem::size_of::<HIDD_ATTRIBUTES>() as u32;
        let mut found = None;
        if HidD_GetAttributes(handle, &mut attributes) {
            let mut data = 0isize;
            if HidD_GetPreparsedData(handle, &mut data) {
                let mut caps: HIDP_CAPS = std::mem::zeroed();
                if HidP_GetCaps(data, &mut caps) == HIDP_STATUS_SUCCESS {
                    found = Some(Seen {
                        vendor: attributes.VendorID,
                        product: attributes.ProductID,
                        out_len: usize::from(caps.OutputReportByteLength),
                        in_len: usize::from(caps.InputReportByteLength),
                    });
                }
                HidD_FreePreparsedData(data);
            }
        }
        CloseHandle(handle);
        found
    }
}

/// products: the product names of the HID devices with these ids that are there (each asked of
/// the device without opening it for reading or writing).
pub fn products(vendor: u16, product: u16) -> Vec<String> {
    let mut names = Vec::new();
    for path in hid_paths_of(vendor, product) {
        let Some(seen) = seen(&path) else {
            continue;
        };
        if seen.vendor != vendor || seen.product != product {
            continue;
        }
        // SAFETY: a NUL-ended path; the handle is closed before the next
        unsafe {
            let handle = CreateFileW(
                path.as_ptr(),
                0,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                null(),
                OPEN_EXISTING,
                0,
                null_mut(),
            );
            if handle == INVALID_HANDLE_VALUE {
                continue;
            }
            let mut name = [0u16; 128];
            if HidD_GetProductString(
                handle,
                name.as_mut_ptr().cast::<c_void>(),
                (name.len() * 2) as u32,
            ) {
                names.push(wide_to_string(&name));
            }
            CloseHandle(handle);
        }
    }
    names
}

/// take: the first HID device with one of these ids whose reports have the length `report`
/// (the id's byte not counted) and that no panel of this process has open, opened for this
/// one: the device and its product id.
pub fn take(vendor: u16, products: &[u16], report: usize, label: &str) -> Result<(WinHid, u16)> {
    let mut taken_already = false;
    let mut refused = None;
    for &product in products {
        for path in hid_paths_of(vendor, product) {
            let Some(seen) = seen(&path) else {
                continue;
            };
            if seen.vendor != vendor || seen.product != product || seen.out_len != report + 1 {
                continue;
            }
            let place = format!("hid:{}", wide_to_string(&path).to_lowercase());
            if !take_place(&place) {
                taken_already = true;
                continue;
            }
            match open(&path, &seen, place.clone()) {
                Ok(device) => return Ok((device, product)),
                Err(e) => {
                    leave_place(&place);
                    refused = Some(e);
                }
            }
        }
    }
    if let Some(e) = refused {
        return Err(DeviceError(tf(
            "the {panel} did not start: {error}",
            &[("panel", label), ("error", &e.to_string())],
        )));
    }
    if taken_already {
        return Err(DeviceError(tf(
            "another panel of Libre Panel has the {panel} open",
            &[("panel", label)],
        )));
    }
    Err(DeviceError(tf(
        "no {panel} on the USB bus",
        &[("panel", label)],
    )))
}

fn open(path: &[u16], seen: &Seen, place: String) -> UsbResult<WinHid> {
    // SAFETY: a NUL-ended path; the handles are the WinHid's from here, closed by its release.
    unsafe {
        let handle = CreateFileW(
            path.as_ptr(),
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            null(),
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            null_mut(),
        );
        if handle == INVALID_HANDLE_VALUE {
            return Err(error_of(GetLastError()));
        }
        let event = CreateEventW(null(), 1, 0, null());
        if event.is_null() {
            let code = GetLastError();
            CloseHandle(handle);
            return Err(error_of(code));
        }
        let mut name = [0u16; 128];
        let product = if HidD_GetProductString(
            handle,
            name.as_mut_ptr().cast::<c_void>(),
            (name.len() * 2) as u32,
        ) {
            wide_to_string(&name)
        } else {
            String::new()
        };
        Ok(WinHid {
            handle,
            event,
            out_len: seen.out_len,
            in_len: seen.in_len,
            product,
            place,
        })
    }
}

fn error_of(code: u32) -> UsbError {
    match code {
        ERROR_ACCESS_DENIED => UsbError::Denied,
        ERROR_SHARING_VIOLATION => UsbError::Busy,
        code => UsbError::Other(format!("Windows error {code}")),
    }
}

impl WinHid {
    /// One report written or read, `timeout_ms` at most: the bytes it moved.
    fn io(&mut self, write: bool, data: *mut u8, size: usize, timeout_ms: u64) -> UsbResult<usize> {
        if self.handle.is_null() {
            return Err(UsbError::Other("the device is let go".into()));
        }
        // SAFETY: `data` is valid for `size` bytes until this returns: an operation still
        // pending when the time is up is cancelled and waited for before the return.
        unsafe {
            let mut overlapped: OVERLAPPED = std::mem::zeroed();
            ResetEvent(self.event);
            overlapped.hEvent = self.event;
            let size = size.min(u32::MAX as usize) as u32;
            let done = if write {
                WriteFile(self.handle, data, size, null_mut(), &mut overlapped)
            } else {
                ReadFile(self.handle, data, size, null_mut(), &mut overlapped)
            };
            if done == 0 {
                let code = GetLastError();
                if code != ERROR_IO_PENDING {
                    return Err(error_of(code));
                }
                let wait = timeout_ms.min(u64::from(u32::MAX - 1)) as u32;
                if WaitForSingleObject(self.event, wait) != WAIT_OBJECT_0 {
                    CancelIoEx(self.handle, &overlapped);
                    // (done all the same just before it was cancelled: what it moved counts)
                    let mut count = 0u32;
                    if GetOverlappedResult(self.handle, &overlapped, &mut count, 1) != 0 {
                        return Ok(count as usize);
                    }
                    return Err(UsbError::Timeout);
                }
            }
            let mut count = 0u32;
            if GetOverlappedResult(self.handle, &overlapped, &mut count, 1) == 0 {
                return Err(error_of(GetLastError()));
            }
            Ok(count as usize)
        }
    }
}

impl UsbIo for WinHid {
    fn product(&mut self) -> String {
        self.product.clone()
    }

    fn interfaces(&self) -> Vec<InterfaceInfo> {
        let endpoint = |address, length: usize| EndpointInfo {
            address,
            max_packet: length.saturating_sub(1),
            interrupt: true,
        };
        vec![InterfaceInfo {
            number: 0,
            class: HID_CLASS,
            endpoints: vec![endpoint(OUT, self.out_len), endpoint(IN, self.in_len)],
        }]
    }

    fn claim(&mut self, _interfaces: &[u8]) -> UsbResult<()> {
        Ok(())
    }

    fn write(&mut self, _endpoint: u8, data: &[u8], timeout_ms: u64) -> UsbResult<()> {
        // (report id 0, then the report, filled up to the length Windows wants)
        let mut report = vec![0u8; self.out_len.max(data.len() + 1)];
        report[1..=data.len()].copy_from_slice(data);
        let size = report.len();
        self.io(true, report.as_mut_ptr(), size, timeout_ms)?;
        Ok(())
    }

    fn read(&mut self, _endpoint: u8, size: usize, timeout_ms: u64) -> UsbResult<Vec<u8>> {
        let mut report = vec![0u8; self.in_len.max(2)];
        let length = report.len();
        let count = self.io(false, report.as_mut_ptr(), length, timeout_ms)?;
        let mut data = report[1..count.max(1)].to_vec();
        data.truncate(size);
        Ok(data)
    }

    fn control_out(&mut self, _: u8, _: u8, _: u16, _: u16, _: &[u8], _: u64) -> UsbResult<()> {
        Err(UsbError::Other(
            "no control transfer goes through Windows' HID driver".into(),
        ))
    }

    fn control_in(&mut self, _: u8, _: u8, _: u16, _: u16, _: u16, _: u64) -> UsbResult<Vec<u8>> {
        Err(UsbError::Other(
            "no control transfer goes through Windows' HID driver".into(),
        ))
    }

    fn release(&mut self) {
        // SAFETY: each handle is closed once, then forgotten
        unsafe {
            if !self.handle.is_null() {
                CloseHandle(self.handle);
                self.handle = null_mut();
            }
            if !self.event.is_null() {
                CloseHandle(self.event);
                self.event = null_mut();
            }
        }
        if !self.place.is_empty() {
            leave_place(&std::mem::take(&mut self.place));
        }
    }
}

impl Drop for WinHid {
    fn drop(&mut self) {
        self.release();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_wide_text_ends_at_its_nul() {
        let path: Vec<u16> = r"\\?\HID#VID_1D6B&PID_0103#7&1&0000#{4d1e55b2}"
            .encode_utf16()
            .collect();
        assert!(wide_to_string(&path)
            .to_lowercase()
            .contains("vid_1d6b&pid_0103"));
        let text: Vec<u16> = "Aigo USB Device\0junk".encode_utf16().collect();
        assert_eq!(wide_to_string(&text), "Aigo USB Device");
        assert_eq!(wide_to_string(&[]), "");
    }
}
