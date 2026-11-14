//! Libre Panel's side of the named pipe to the sensor service, and what Windows says of the
//! service (sensorservice/winpipe.py's Connection and connect, winapi.py's service calls,
//! sensors/lhm_service.py's open_link and installed).
//!
//! - The pipe's server must be the service: the process the Service Control Manager names for
//!   LibrePanelSensors. A program that took the pipe's name is not talked to.
//! - A service that is installed but not running (its start comes a while after Windows
//!   starts) is asked to start; it lets everyone signed in at this PC do that.
//! - Every wait has an end: bytes go in and out through overlapped I/O, so that a timeout can
//!   cancel a read or a write that hangs. The server may tell who this is, but not act as
//!   this account (`SECURITY_IDENTIFICATION`).

use std::io;
use std::time::{Duration, Instant};

use lp_py::{Dict, Value};
use windows_sys::Win32::Foundation::{
    CloseHandle, GetLastError, ERROR_IO_PENDING, ERROR_PIPE_BUSY, ERROR_SERVICE_ALREADY_RUNNING,
    ERROR_SERVICE_DOES_NOT_EXIST, HANDLE, INVALID_HANDLE_VALUE, WAIT_OBJECT_0,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, ReadFile, WriteFile, FILE_FLAG_OVERLAPPED, OPEN_EXISTING, SECURITY_IDENTIFICATION,
    SECURITY_SQOS_PRESENT,
};
use windows_sys::Win32::System::Pipes::{GetNamedPipeServerProcessId, WaitNamedPipeW};
use windows_sys::Win32::System::Registry::{
    RegCloseKey, RegGetValueW, RegOpenKeyExW, HKEY, HKEY_LOCAL_MACHINE, KEY_READ, RRF_RT_REG_DWORD,
};
use windows_sys::Win32::System::Services::{
    CloseServiceHandle, OpenSCManagerW, OpenServiceW, QueryServiceStatusEx, StartServiceW,
    SC_HANDLE, SC_MANAGER_CONNECT, SC_STATUS_PROCESS_INFO, SERVICE_QUERY_STATUS, SERVICE_RUNNING,
    SERVICE_START, SERVICE_STATUS_PROCESS, SERVICE_STOPPED,
};
use windows_sys::Win32::System::Threading::{CreateEventW, ResetEvent, WaitForSingleObject};
use windows_sys::Win32::System::IO::{CancelIoEx, GetOverlappedResult, OVERLAPPED};

use crate::protocol::{body_length, decode, encode, HEADER, MAX_ANSWER, MAX_REQUEST};
use crate::service::{Link, Unavailable, PIPE_NAME, SERVICE_NAME};

/// a client waits this long for a free instance of the pipe
const CONNECT_SECONDS: f64 = 2.0;
/// What a client asks for: read and write the pipe's data and read its attributes
/// (FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE).
const CLIENT_ACCESS: u32 = 0x0010_0083;
/// the service's start type "Disabled"
const DISABLED: u32 = 4;

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

fn failed(what: &str, code: u32) -> Unavailable {
    Unavailable(format!(
        "{what}: {}",
        io::Error::from_raw_os_error(code as i32)
    ))
}

fn state_name(state: u32) -> String {
    match state {
        1 => "stopped".into(),
        2 => "starting".into(),
        3 => "stopping".into(),
        4 => "running".into(),
        5 => "continuing".into(),
        6 => "pausing".into(),
        7 => "paused".into(),
        other => other.to_string(),
    }
}

struct Service(SC_HANDLE, SC_HANDLE);

impl Drop for Service {
    fn drop(&mut self) {
        // SAFETY: both are handles this struct opened, closed once here.
        unsafe {
            if !self.1.is_null() {
                CloseServiceHandle(self.1);
            }
            CloseServiceHandle(self.0);
        }
    }
}

/// The service's handle, or none when there is no such service.
fn service(name: &str, access: u32) -> Result<Option<Service>, Unavailable> {
    // SAFETY: plain calls with a NUL-ended name; the handles are closed by Service's drop.
    unsafe {
        let manager = OpenSCManagerW(std::ptr::null(), std::ptr::null(), SC_MANAGER_CONNECT);
        if manager.is_null() {
            return Err(failed("OpenSCManager", GetLastError()));
        }
        let handle = OpenServiceW(manager, wide(name).as_ptr(), access);
        let code = GetLastError();
        let opened = Service(manager, handle);
        if handle.is_null() {
            if code != ERROR_SERVICE_DOES_NOT_EXIST {
                return Err(failed(&format!("OpenService {name}"), code));
            }
            return Ok(None);
        }
        Ok(Some(opened))
    }
}

/// (state, process id) of a service; none when it is not installed. Any user may ask.
pub fn service_status(name: &str) -> Result<Option<(u32, u32)>, Unavailable> {
    let Some(opened) = service(name, SERVICE_QUERY_STATUS)? else {
        return Ok(None);
    };
    // SAFETY: the buffer is a SERVICE_STATUS_PROCESS of the size given.
    unsafe {
        let mut status: SERVICE_STATUS_PROCESS = std::mem::zeroed();
        let mut needed = 0u32;
        if QueryServiceStatusEx(
            opened.1,
            SC_STATUS_PROCESS_INFO,
            (&raw mut status).cast(),
            size_of::<SERVICE_STATUS_PROCESS>() as u32,
            &mut needed,
        ) == 0
        {
            return Err(failed(
                &format!("QueryServiceStatusEx {name}"),
                GetLastError(),
            ));
        }
        Ok(Some((status.dwCurrentState, status.dwProcessId)))
    }
}

/// Asks the Service Control Manager to start a service; false where this account may not (or
/// it is not installed). True when it starts or runs already.
pub fn start_service(name: &str) -> bool {
    let Ok(Some(opened)) = service(name, SERVICE_START) else {
        return false;
    };
    // SAFETY: the handle is open for starting; no arguments are passed.
    unsafe {
        StartServiceW(opened.1, 0, std::ptr::null()) != 0
            || GetLastError() == ERROR_SERVICE_ALREADY_RUNNING
    }
}

/// Whether the sensor service is installed here and not switched off: what Windows has on
/// record for it, from the registry any user may read.
pub fn installed() -> bool {
    let key = wide(&format!(
        r"SYSTEM\CurrentControlSet\Services\{SERVICE_NAME}"
    ));
    let mut opened: HKEY = std::ptr::null_mut();
    // SAFETY: the key is opened for reading and closed below; the value is read into a u32
    // of the size given.
    unsafe {
        if RegOpenKeyExW(HKEY_LOCAL_MACHINE, key.as_ptr(), 0, KEY_READ, &mut opened) != 0 {
            return false;
        }
        let mut start = 0u32;
        let mut size = size_of::<u32>() as u32;
        let code = RegGetValueW(
            opened,
            std::ptr::null(),
            wide("Start").as_ptr(),
            RRF_RT_REG_DWORD,
            std::ptr::null_mut(),
            (&raw mut start).cast(),
            &mut size,
        );
        RegCloseKey(opened);
        code != 0 || start != DISABLED
    }
}

/// An open pipe handle, read and written with overlapped I/O: each operation waits for its
/// end or the timeout, whichever comes first.
struct Pipe {
    handle: HANDLE,
    event: HANDLE,
}

// SAFETY: the handles are used by one thread at a time (the pipe is owned by its Link).
unsafe impl Send for Pipe {}

impl Drop for Pipe {
    fn drop(&mut self) {
        // SAFETY: both are handles this struct owns, closed once here.
        unsafe {
            CloseHandle(self.event);
            CloseHandle(self.handle);
        }
    }
}

impl Pipe {
    /// One read or write of up to `size` bytes at `data`; how many were done.
    fn io(
        &mut self,
        write: bool,
        data: *mut u8,
        size: usize,
        deadline: Instant,
    ) -> Result<usize, Unavailable> {
        // SAFETY: `data` is valid for `size` bytes until this returns: an operation that is
        // still pending when time is up is cancelled and waited for before the return.
        unsafe {
            let mut overlapped: OVERLAPPED = std::mem::zeroed();
            ResetEvent(self.event);
            overlapped.hEvent = self.event;
            let size = size.min(u32::MAX as usize) as u32;
            let done = if write {
                WriteFile(
                    self.handle,
                    data,
                    size,
                    std::ptr::null_mut(),
                    &mut overlapped,
                )
            } else {
                ReadFile(
                    self.handle,
                    data,
                    size,
                    std::ptr::null_mut(),
                    &mut overlapped,
                )
            };
            if done == 0 {
                let code = GetLastError();
                if code != ERROR_IO_PENDING {
                    return Err(Unavailable(format!("pipe error {code}")));
                }
                let left = deadline.saturating_duration_since(Instant::now());
                let wait = left.as_millis().min(u128::from(u32::MAX - 1)) as u32;
                if WaitForSingleObject(self.event, wait) != WAIT_OBJECT_0 {
                    let mut count = 0u32;
                    CancelIoEx(self.handle, &overlapped);
                    GetOverlappedResult(self.handle, &overlapped, &mut count, 1);
                    return Err(Unavailable("timed out".into()));
                }
            }
            let mut count = 0u32;
            if GetOverlappedResult(self.handle, &overlapped, &mut count, 1) == 0 {
                return Err(Unavailable(format!("pipe error {}", GetLastError())));
            }
            if count == 0 {
                return Err(Unavailable("the other side closed the pipe".into()));
            }
            Ok(count as usize)
        }
    }

    fn read(&mut self, size: usize, deadline: Instant) -> Result<Vec<u8>, Unavailable> {
        let mut out = vec![0u8; size];
        let mut got = 0;
        while got < size {
            got += self.io(false, out[got..].as_mut_ptr(), size - got, deadline)?;
        }
        Ok(out)
    }

    fn write(&mut self, data: &[u8], deadline: Instant) -> Result<(), Unavailable> {
        let mut sent = 0;
        while sent < data.len() {
            let rest = &data[sent..];
            sent += self.io(true, rest.as_ptr().cast_mut(), rest.len(), deadline)?;
        }
        Ok(())
    }

    /// The process id of the pipe's server (0 where Windows does not say).
    fn server_pid(&self) -> u32 {
        let mut pid = 0u32;
        // SAFETY: the handle is an open pipe; the id is written into `pid`.
        if unsafe { GetNamedPipeServerProcessId(self.handle, &mut pid) } == 0 {
            return 0;
        }
        pid
    }
}

/// Libre Panel's connection to the service: a request, its answer.
struct Connection(Pipe);

impl Link for Connection {
    fn request(&mut self, message: &Value, timeout: f64) -> Result<Dict, Unavailable> {
        let deadline = Instant::now() + Duration::from_secs_f64(timeout);
        self.0.write(&encode(message, MAX_REQUEST)?, deadline)?;
        let length = body_length(&self.0.read(HEADER, deadline)?, MAX_ANSWER)?;
        Ok(decode(&self.0.read(length, deadline)?)?)
    }
}

/// Opens the pipe as a client.
fn connect(name: &str, timeout: f64) -> Result<Pipe, Unavailable> {
    let wide_name = wide(name);
    let flags = FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION;
    let deadline = Instant::now() + Duration::from_secs_f64(timeout);
    loop {
        // SAFETY: plain calls with a NUL-ended name; the handles go to a Pipe, which closes
        // them.
        unsafe {
            let handle = CreateFileW(
                wide_name.as_ptr(),
                CLIENT_ACCESS,
                0,
                std::ptr::null(),
                OPEN_EXISTING,
                flags,
                std::ptr::null_mut(),
            );
            if !handle.is_null() && handle != INVALID_HANDLE_VALUE {
                let event = CreateEventW(std::ptr::null(), 1, 0, std::ptr::null());
                if event.is_null() {
                    let code = GetLastError();
                    CloseHandle(handle);
                    return Err(failed("CreateEvent", code));
                }
                return Ok(Pipe { handle, event });
            }
            let code = GetLastError();
            let left = deadline.saturating_duration_since(Instant::now());
            if code != ERROR_PIPE_BUSY || left.is_zero() {
                return Err(failed(&format!("opening {name}"), code));
            }
            // (all instances busy: wait for one)
            WaitNamedPipeW(wide_name.as_ptr(), (left.as_millis() as u32).max(1));
        }
    }
}

/// open_link: a connection to the running service, after making sure that the pipe's server
/// is it. A service that is installed but stopped is asked to start (and this try fails).
pub fn open_link() -> Result<Box<dyn Link>, Unavailable> {
    let Some((state, pid)) = service_status(SERVICE_NAME)? else {
        return Err(Unavailable("the sensor service is not installed".into()));
    };
    if state != SERVICE_RUNNING {
        if state == SERVICE_STOPPED && start_service(SERVICE_NAME) {
            return Err(Unavailable("the sensor service is starting".into()));
        }
        return Err(Unavailable(format!(
            "the sensor service is {}",
            state_name(state)
        )));
    }
    let pipe = connect(PIPE_NAME, CONNECT_SECONDS)?;
    if pid == 0 || pipe.server_pid() != pid {
        return Err(Unavailable(
            "the pipe is held by another program than the sensor service".into(),
        ));
    }
    Ok(Box::new(Connection(pipe)))
}
