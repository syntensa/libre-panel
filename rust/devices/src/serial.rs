//! The serial port a panel is on (devices/serial_link.py's SerialLink over pyserial's Serial, as
//! open_serial opens it): 8 bits, no parity, one stop bit, RTS/CTS only where asked, DTR and RTS
//! on as pyserial sets them; a read waits up to a second for what it asks (timeout=1), a write
//! up to ten seconds for room (write_timeout=10). Linux: the tty (termios, poll); Windows: the
//! COM port (its DCB and its timeouts). And the ports the system has, with the USB ids of the
//! device behind each (pyserial's list_ports), so that a panel is found by its ids.

use crate::base::{DeviceError, Result};
use crate::link::Link;

/// A serial port the system has: its name ("COM5", "/dev/ttyACM0") and, for a USB device, its
/// ids and serial number.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PortInfo {
    pub device: String,
    pub vid: Option<u16>,
    pub pid: Option<u16>,
    pub serial_number: Option<String>,
}

/// An open serial port.
pub struct SerialPort {
    device: String,
    port: imp::Port,
}

impl SerialPort {
    /// The port opened at `baud` (RTS/CTS where `rtscts`), what came in before thrown away.
    pub fn open(device: &str, baud: u32, rtscts: bool) -> Result<SerialPort> {
        let port = imp::Port::open(device, baud, rtscts)
            .map_err(|e| DeviceError(format!("cannot open {device}: {e}")))?;
        Ok(SerialPort {
            device: device.to_string(),
            port,
        })
    }

    pub fn device(&self) -> &str {
        &self.device
    }

    /// How long a read waits for what it asks, and a write for room (pyserial's `timeout` and
    /// `write_timeout`).
    pub fn set_waits(
        &mut self,
        read: std::time::Duration,
        write: std::time::Duration,
    ) -> Result<()> {
        self.port
            .set_waits(read, write)
            .map_err(|e| DeviceError(format!("cannot set up {}: {e}", self.device)))
    }

    /// pyserial's `exclusive=True`: no other program that asks for the port gets it while
    /// this one has it (Windows opens a port for one program anyway).
    pub fn lock(&self) -> Result<()> {
        self.port.lock().map_err(|e| {
            DeviceError(format!(
                "Could not exclusively lock port {}: {e}",
                self.device
            ))
        })
    }
}

/// The name a USB device behind a serial port gives itself (pyserial's `product`), where the
/// system tells it (Linux).
pub fn product_of(device: &str) -> Option<String> {
    imp::product(device)
}

impl Link for SerialPort {
    fn write(&mut self, data: &[u8]) -> Result<()> {
        self.port
            .write(data)
            .map_err(|e| DeviceError(format!("serial write failed: {e}")))
    }

    fn read(&mut self, size: usize) -> Result<Vec<u8>> {
        self.port
            .read(size)
            .map_err(|e| DeviceError(format!("serial read failed: {e}")))
    }

    fn drain_input(&mut self) {
        self.port.drain_input();
    }

    fn close(&mut self) {
        self.port.close();
    }
}

/// The serial ports the system has (list_ports.comports()), in its order.
pub fn ports() -> Vec<PortInfo> {
    imp::ports()
}

const READ_WAIT: std::time::Duration = std::time::Duration::from_secs(1);
const WRITE_WAIT: std::time::Duration = std::time::Duration::from_secs(10);

#[cfg(unix)]
mod imp {
    use std::ffi::CString;
    use std::io;
    use std::time::Instant;

    use super::{PortInfo, READ_WAIT, WRITE_WAIT};

    /// What pyserial says when a port it waited on gave nothing (a device pulled out).
    const GONE: &str = "device reports readiness to read but returned no data (device \
                        disconnected or multiple access on port?)";

    pub struct Port {
        fd: libc::c_int,
        read_wait: std::time::Duration,
        write_wait: std::time::Duration,
    }

    fn os_error() -> io::Error {
        io::Error::last_os_error()
    }

    fn retry(e: &io::Error) -> bool {
        matches!(e.raw_os_error(), Some(libc::EINTR) | Some(libc::EAGAIN))
    }

    /// The termios speed of `baud` (the speeds Linux names).
    fn speed_of(baud: u32) -> io::Result<libc::speed_t> {
        Ok(match baud {
            9600 => libc::B9600,
            19200 => libc::B19200,
            38400 => libc::B38400,
            57600 => libc::B57600,
            115200 => libc::B115200,
            230400 => libc::B230400,
            460800 => libc::B460800,
            921600 => libc::B921600,
            #[cfg(target_os = "linux")]
            1_000_000 => libc::B1000000,
            #[cfg(target_os = "linux")]
            2_000_000 => libc::B2000000,
            _ => {
                return Err(io::Error::new(
                    io::ErrorKind::Unsupported,
                    format!("a speed of {baud} baud is not set here"),
                ))
            }
        })
    }

    impl Port {
        pub fn open(device: &str, baud: u32, rtscts: bool) -> io::Result<Port> {
            let path = CString::new(device)
                .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "a NUL in its name"))?;
            let flags = libc::O_RDWR | libc::O_NOCTTY | libc::O_NONBLOCK | libc::O_CLOEXEC;
            // SAFETY: a path of C; the descriptor is this Port's from now on
            let fd = unsafe { libc::open(path.as_ptr(), flags) };
            if fd < 0 {
                return Err(os_error());
            }
            let port = Port {
                fd,
                read_wait: READ_WAIT,
                write_wait: WRITE_WAIT,
            };
            port.configure(baud, rtscts)?;
            Ok(port)
        }

        pub fn set_waits(
            &mut self,
            read: std::time::Duration,
            write: std::time::Duration,
        ) -> io::Result<()> {
            (self.read_wait, self.write_wait) = (read, write);
            Ok(())
        }

        pub fn lock(&self) -> io::Result<()> {
            // SAFETY: the Port's descriptor
            match unsafe { libc::flock(self.fd, libc::LOCK_EX | libc::LOCK_NB) } {
                0 => Ok(()),
                _ => Err(os_error()),
            }
        }

        /// pyserial's _reconfigure_port: raw, 8N1, its speed, no flow control unless asked,
        /// reads that wait for nothing (poll waits); then DTR and RTS on, the input flushed.
        fn configure(&self, baud: u32, rtscts: bool) -> io::Result<()> {
            // SAFETY: termios is plain data, filled by tcgetattr
            let mut t: libc::termios = unsafe { std::mem::zeroed() };
            // SAFETY: the Port's descriptor and a termios of its own
            if unsafe { libc::tcgetattr(self.fd, &mut t) } != 0 {
                return Err(os_error());
            }
            t.c_cflag |= libc::CLOCAL | libc::CREAD;
            t.c_lflag &= !(libc::ICANON
                | libc::ECHO
                | libc::ECHOE
                | libc::ECHOK
                | libc::ECHONL
                | libc::ISIG
                | libc::IEXTEN
                | libc::ECHOCTL
                | libc::ECHOKE);
            t.c_oflag &= !(libc::OPOST | libc::ONLCR | libc::OCRNL);
            t.c_iflag &= !(libc::INLCR
                | libc::IGNCR
                | libc::ICRNL
                | libc::IGNBRK
                | libc::PARMRK
                | libc::INPCK
                | libc::ISTRIP
                | libc::IXON
                | libc::IXOFF
                | libc::IXANY);
            #[cfg(target_os = "linux")]
            {
                t.c_iflag &= !libc::IUCLC;
            }
            t.c_cflag &=
                !(libc::CSIZE | libc::CSTOPB | libc::PARENB | libc::PARODD | libc::CRTSCTS);
            t.c_cflag |= libc::CS8;
            if rtscts {
                t.c_cflag |= libc::CRTSCTS;
            }
            t.c_cc[libc::VMIN] = 0;
            t.c_cc[libc::VTIME] = 0;
            let speed = speed_of(baud)?;
            // SAFETY: as above
            unsafe {
                libc::cfsetispeed(&mut t, speed);
                libc::cfsetospeed(&mut t, speed);
                if libc::tcsetattr(self.fd, libc::TCSANOW, &t) != 0 {
                    return Err(os_error());
                }
            }
            let mut bits: libc::c_int = libc::TIOCM_DTR;
            if !rtscts {
                bits |= libc::TIOCM_RTS;
            }
            // SAFETY: TIOCMBIS takes a pointer to an int of modem bits
            if unsafe { libc::ioctl(self.fd, libc::TIOCMBIS, &bits) } != 0 {
                let e = os_error();
                // (a port without modem lines refuses: pyserial goes on, as here)
                if !matches!(e.raw_os_error(), Some(libc::EINVAL) | Some(libc::ENOTTY)) {
                    return Err(e);
                }
            }
            self.drain_input();
            Ok(())
        }

        fn wait(&self, events: libc::c_short, until: Instant) -> io::Result<bool> {
            loop {
                let left = until.saturating_duration_since(Instant::now());
                if left.is_zero() {
                    return Ok(false);
                }
                let mut fds = libc::pollfd {
                    fd: self.fd,
                    events,
                    revents: 0,
                };
                let ms = left.as_millis().clamp(1, i32::MAX as u128) as libc::c_int;
                // SAFETY: one pollfd of this Port's
                let n = unsafe { libc::poll(&mut fds, 1, ms) };
                if n < 0 {
                    let e = os_error();
                    if retry(&e) {
                        continue;
                    }
                    return Err(e);
                }
                return Ok(n > 0);
            }
        }

        pub fn read(&mut self, size: usize) -> io::Result<Vec<u8>> {
            let until = Instant::now() + self.read_wait;
            let mut out = Vec::with_capacity(size);
            while out.len() < size {
                if !self.wait(libc::POLLIN, until)? {
                    break;
                }
                let mut chunk = vec![0u8; size - out.len()];
                // SAFETY: into a buffer of its length
                let got = unsafe { libc::read(self.fd, chunk.as_mut_ptr().cast(), chunk.len()) };
                if got < 0 {
                    let e = os_error();
                    if retry(&e) {
                        continue;
                    }
                    return Err(e);
                }
                if got == 0 {
                    return Err(io::Error::other(GONE));
                }
                out.extend_from_slice(&chunk[..got as usize]);
            }
            Ok(out)
        }

        pub fn write(&mut self, data: &[u8]) -> io::Result<()> {
            let until = Instant::now() + self.write_wait;
            let mut sent = 0;
            while sent < data.len() {
                // SAFETY: from the data, its length left
                let n = unsafe {
                    libc::write(self.fd, data[sent..].as_ptr().cast(), data.len() - sent)
                };
                if n > 0 {
                    sent += n as usize;
                    continue;
                }
                if n < 0 {
                    let e = os_error();
                    if !retry(&e) {
                        return Err(e);
                    }
                }
                if !self.wait(libc::POLLOUT, until)? {
                    return Err(io::Error::new(io::ErrorKind::TimedOut, "Write timeout"));
                }
            }
            Ok(())
        }

        pub fn drain_input(&self) {
            // SAFETY: the Port's descriptor
            unsafe { libc::tcflush(self.fd, libc::TCIFLUSH) };
        }

        pub fn close(&mut self) {
            if self.fd >= 0 {
                // SAFETY: the Port's descriptor, closed once
                unsafe { libc::close(self.fd) };
                self.fd = -1;
            }
        }
    }

    impl Drop for Port {
        fn drop(&mut self) {
            self.close();
        }
    }

    fn line(path: &std::path::Path) -> Option<String> {
        std::fs::read_to_string(path)
            .ok()
            .map(|s| s.trim_end_matches(['\n', '\r']).to_string())
    }

    /// The USB device behind a tty, in sysfs (pyserial's list_ports_linux).
    fn usb_device(name: &str) -> Option<std::path::PathBuf> {
        let device = std::path::Path::new("/sys/class/tty")
            .join(name)
            .join("device");
        let device_path = std::fs::canonicalize(device).ok()?;
        let subsystem = std::fs::canonicalize(device_path.join("subsystem")).ok()?;
        let interface = match subsystem.file_name()?.to_str()? {
            "usb-serial" => device_path.parent()?.to_path_buf(),
            "usb" => device_path,
            _ => return None,
        };
        Some(interface.parent()?.to_path_buf())
    }

    /// The name the USB device behind `device` gives itself.
    pub fn product(device: &str) -> Option<String> {
        let name = std::path::Path::new(device).file_name()?.to_str()?;
        line(&usb_device(name)?.join("product"))
    }

    /// pyserial's list_ports_linux: the ttys of /dev it looks at, a USB device's ids from sysfs.
    pub fn ports() -> Vec<PortInfo> {
        const KINDS: [&str; 8] = [
            "ttyS", "ttyUSB", "ttyXRUSB", "ttyACM", "ttyAMA", "rfcomm", "ttyAP", "ttyGS",
        ];
        let mut found = Vec::new();
        let Ok(entries) = std::fs::read_dir("/dev") else {
            return found;
        };
        let mut names: Vec<String> = entries
            .filter_map(|e| e.ok().map(|e| e.file_name().to_string_lossy().into_owned()))
            .filter(|n| KINDS.iter().any(|k| n.starts_with(k)))
            .collect();
        names.sort();
        for name in names {
            let device = std::path::Path::new("/sys/class/tty")
                .join(&name)
                .join("device");
            let Ok(device_path) = std::fs::canonicalize(&device) else {
                found.push(PortInfo {
                    device: format!("/dev/{name}"),
                    vid: None,
                    pid: None,
                    serial_number: None,
                });
                continue;
            };
            let subsystem = std::fs::canonicalize(device_path.join("subsystem"))
                .ok()
                .and_then(|p| p.file_name().map(|n| n.to_string_lossy().into_owned()));
            let interface = match subsystem.as_deref() {
                Some("usb-serial") => device_path.parent().map(|p| p.to_path_buf()),
                Some("usb") => Some(device_path.clone()),
                _ => None,
            };
            let usb = interface.as_ref().and_then(|i| i.parent());
            let id = |file: &str| {
                usb.and_then(|u| line(&u.join(file)))
                    .and_then(|v| u16::from_str_radix(&v, 16).ok())
            };
            if subsystem.as_deref() == Some("platform") {
                continue; // (pyserial leaves out the board's own, which may not be there)
            }
            found.push(PortInfo {
                device: format!("/dev/{name}"),
                vid: id("idVendor"),
                pid: id("idProduct"),
                serial_number: usb.and_then(|u| line(&u.join("serial"))),
            });
        }
        found
    }
}

#[cfg(windows)]
mod imp {
    use std::io;
    use std::ptr::{null, null_mut};

    use windows_sys::core::GUID;
    use windows_sys::Win32::Devices::Communication::{
        GetCommState, PurgeComm, SetCommState, SetCommTimeouts, SetupComm, COMMTIMEOUTS, DCB,
        NOPARITY, ONESTOPBIT, PURGE_RXABORT, PURGE_RXCLEAR, PURGE_TXABORT, PURGE_TXCLEAR,
    };
    use windows_sys::Win32::Devices::DeviceAndDriverInstallation::{
        SetupDiDestroyDeviceInfoList, SetupDiEnumDeviceInfo, SetupDiGetClassDevsW,
        SetupDiGetDeviceInstanceIdW, SetupDiOpenDevRegKey, DICS_FLAG_GLOBAL, DIGCF_PRESENT,
        DIREG_DEV, SP_DEVINFO_DATA,
    };
    use windows_sys::Win32::Foundation::{
        CloseHandle, GENERIC_READ, GENERIC_WRITE, HANDLE, INVALID_HANDLE_VALUE,
    };
    use windows_sys::Win32::Storage::FileSystem::{
        CreateFileW, ReadFile, WriteFile, FILE_ATTRIBUTE_NORMAL, OPEN_EXISTING,
    };
    use windows_sys::Win32::System::Registry::{RegCloseKey, RegQueryValueExW, KEY_READ};

    use super::{PortInfo, READ_WAIT, WRITE_WAIT};

    /// The class of ports (COM and LPT): GUID_DEVCLASS_PORTS.
    const PORTS: GUID = GUID {
        data1: 0x4D36_E978,
        data2: 0xE325,
        data3: 0x11CE,
        data4: [0xBF, 0xC1, 0x08, 0x00, 0x2B, 0xE1, 0x03, 0x18],
    };
    const DTR_CONTROL_ENABLE: u32 = 1;
    const RTS_CONTROL_ENABLE: u32 = 1;
    const RTS_CONTROL_HANDSHAKE: u32 = 2;

    pub struct Port {
        handle: HANDLE,
    }

    // SAFETY: a COM port's handle may be used from any thread (the driver's link is behind a
    // Mutex: one at a time)
    unsafe impl Send for Port {}

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    impl Port {
        pub fn open(device: &str, baud: u32, rtscts: bool) -> io::Result<Port> {
            // (pyserial's: COM10 and beyond only by the device namespace)
            let name = if device.starts_with("COM") {
                format!("\\\\.\\{device}")
            } else {
                device.to_string()
            };
            let name = wide(&name);
            // SAFETY: a NUL-ended name; the handle is this Port's from now on
            let handle = unsafe {
                CreateFileW(
                    name.as_ptr(),
                    GENERIC_READ | GENERIC_WRITE,
                    0,
                    null(),
                    OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL,
                    null_mut(),
                )
            };
            if handle == INVALID_HANDLE_VALUE {
                return Err(io::Error::last_os_error());
            }
            let port = Port { handle };
            port.configure(baud, rtscts)?;
            Ok(port)
        }

        pub fn set_waits(
            &mut self,
            read: std::time::Duration,
            write: std::time::Duration,
        ) -> io::Result<()> {
            let timeouts = COMMTIMEOUTS {
                ReadIntervalTimeout: 0,
                ReadTotalTimeoutMultiplier: 0,
                ReadTotalTimeoutConstant: read.as_millis() as u32,
                WriteTotalTimeoutMultiplier: 0,
                WriteTotalTimeoutConstant: write.as_millis() as u32,
            };
            // SAFETY: the Port's handle and a structure of its own
            match unsafe { SetCommTimeouts(self.handle, &timeouts) } {
                0 => Err(io::Error::last_os_error()),
                _ => Ok(()),
            }
        }

        pub fn lock(&self) -> io::Result<()> {
            Ok(()) // (opened for this program alone)
        }

        /// pyserial's _reconfigure_port: its buffers, the timeouts of a read (a second at most
        /// for what it asks) and of a write (ten), 8N1 at its speed, binary, DTR and RTS on (RTS
        /// as the handshake where asked); then everything on the way thrown away.
        fn configure(&self, baud: u32, rtscts: bool) -> io::Result<()> {
            let ok = |done: i32| {
                if done == 0 {
                    Err(io::Error::last_os_error())
                } else {
                    Ok(())
                }
            };
            // SAFETY: the Port's handle and structures of its own
            unsafe {
                ok(SetupComm(self.handle, 4096, 4096))?;
                let timeouts = COMMTIMEOUTS {
                    ReadIntervalTimeout: 0,
                    ReadTotalTimeoutMultiplier: 0,
                    ReadTotalTimeoutConstant: READ_WAIT.as_millis() as u32,
                    WriteTotalTimeoutMultiplier: 0,
                    WriteTotalTimeoutConstant: WRITE_WAIT.as_millis() as u32,
                };
                ok(SetCommTimeouts(self.handle, &timeouts))?;
                let mut dcb: DCB = std::mem::zeroed();
                dcb.DCBlength = std::mem::size_of::<DCB>() as u32;
                ok(GetCommState(self.handle, &mut dcb))?;
                dcb.BaudRate = baud;
                dcb.ByteSize = 8;
                dcb.Parity = NOPARITY;
                dcb.StopBits = ONESTOPBIT;
                // the DCB's bit fields: fBinary (bit 0), fOutxCtsFlow (2), fOutxDsrFlow (3),
                // fDtrControl (4-5), fDsrSensitivity (6), fTXContinueOnXoff (7), fOutX (8),
                // fInX (9), fErrorChar (10), fNull (11), fRtsControl (12-13), fAbortOnError (14)
                let mut bits = dcb._bitfield & !0x7FFF;
                bits |= 1; // fBinary
                if rtscts {
                    bits |= 1 << 2; // fOutxCtsFlow
                }
                bits |= DTR_CONTROL_ENABLE << 4;
                let rts = if rtscts {
                    RTS_CONTROL_HANDSHAKE
                } else {
                    RTS_CONTROL_ENABLE
                };
                bits |= rts << 12;
                dcb._bitfield = bits;
                dcb.XonChar = 0x11;
                dcb.XoffChar = 0x13;
                ok(SetCommState(self.handle, &dcb))?;
                PurgeComm(
                    self.handle,
                    PURGE_TXCLEAR | PURGE_TXABORT | PURGE_RXCLEAR | PURGE_RXABORT,
                );
            }
            Ok(())
        }

        pub fn read(&mut self, size: usize) -> io::Result<Vec<u8>> {
            let mut out = vec![0u8; size];
            let mut got = 0u32;
            // SAFETY: into a buffer of its length; the port's timeouts end the wait
            let done = unsafe {
                ReadFile(
                    self.handle,
                    out.as_mut_ptr(),
                    size as u32,
                    &mut got,
                    null_mut(),
                )
            };
            if done == 0 {
                return Err(io::Error::last_os_error());
            }
            out.truncate(got as usize);
            Ok(out)
        }

        pub fn write(&mut self, data: &[u8]) -> io::Result<()> {
            let mut sent = 0usize;
            while sent < data.len() {
                let mut wrote = 0u32;
                let left = (data.len() - sent).min(u32::MAX as usize) as u32;
                // SAFETY: from the data, its length left
                let done = unsafe {
                    WriteFile(
                        self.handle,
                        data[sent..].as_ptr(),
                        left,
                        &mut wrote,
                        null_mut(),
                    )
                };
                if done == 0 {
                    return Err(io::Error::last_os_error());
                }
                if wrote == 0 {
                    return Err(io::Error::new(io::ErrorKind::TimedOut, "Write timeout"));
                }
                sent += wrote as usize;
            }
            Ok(())
        }

        pub fn drain_input(&self) {
            // SAFETY: the Port's handle
            unsafe { PurgeComm(self.handle, PURGE_RXCLEAR | PURGE_RXABORT) };
        }

        pub fn close(&mut self) {
            if !self.handle.is_null() && self.handle != INVALID_HANDLE_VALUE {
                // SAFETY: the Port's handle, closed once
                unsafe { CloseHandle(self.handle) };
                self.handle = null_mut();
            }
        }
    }

    impl Drop for Port {
        fn drop(&mut self) {
            self.close();
        }
    }

    /// "VID_33C3&PID_7792\\<serial>" read from a device's instance id (pyserial's way): the ids,
    /// and the serial number where the device has one of its own (not one Windows made up).
    fn ids_of(instance: &str) -> (Option<u16>, Option<u16>, Option<String>) {
        let upper = instance.to_ascii_uppercase();
        let hex = |key: &str| {
            upper.find(key).and_then(|at| {
                let digits = upper.get(at + key.len()..at + key.len() + 4)?;
                u16::from_str_radix(digits, 16).ok()
            })
        };
        let serial = instance
            .rsplit('\\')
            .next()
            .filter(|s| !s.contains('&') && upper.starts_with("USB\\"))
            .map(str::to_string);
        (hex("VID_"), hex("PID_"), serial)
    }

    /// (Windows does not tell the name a device gives itself with its port.)
    pub fn product(_device: &str) -> Option<String> {
        None
    }

    /// The present ports of the class of ports (COM, not LPT), with their USB ids.
    pub fn ports() -> Vec<PortInfo> {
        let mut found = Vec::new();
        // SAFETY: SetupAPI's list, walked and freed here; the strings read into buffers of
        // their size
        unsafe {
            let set = SetupDiGetClassDevsW(&PORTS, null(), null_mut(), DIGCF_PRESENT);
            if set == INVALID_HANDLE_VALUE as isize || set == 0 {
                return found;
            }
            let mut index = 0;
            loop {
                let mut info: SP_DEVINFO_DATA = std::mem::zeroed();
                info.cbSize = std::mem::size_of::<SP_DEVINFO_DATA>() as u32;
                if SetupDiEnumDeviceInfo(set, index, &mut info) == 0 {
                    break;
                }
                index += 1;
                let key =
                    SetupDiOpenDevRegKey(set, &info, DICS_FLAG_GLOBAL, 0, DIREG_DEV, KEY_READ);
                if key.is_null() || key == INVALID_HANDLE_VALUE {
                    continue;
                }
                let mut name = [0u16; 256];
                let mut bytes = (name.len() * 2) as u32;
                let read = RegQueryValueExW(
                    key,
                    wide("PortName").as_ptr(),
                    null(),
                    null_mut(),
                    name.as_mut_ptr().cast(),
                    &mut bytes,
                );
                RegCloseKey(key);
                if read != 0 {
                    continue;
                }
                let end = name.iter().position(|&c| c == 0).unwrap_or(name.len());
                let device = String::from_utf16_lossy(&name[..end]);
                if device.starts_with("LPT") {
                    continue;
                }
                let mut id = [0u16; 512];
                let mut needed = 0u32;
                let instance = if SetupDiGetDeviceInstanceIdW(
                    set,
                    &info,
                    id.as_mut_ptr(),
                    id.len() as u32,
                    &mut needed,
                ) != 0
                {
                    let end = id.iter().position(|&c| c == 0).unwrap_or(id.len());
                    String::from_utf16_lossy(&id[..end])
                } else {
                    String::new()
                };
                let (vid, pid, serial_number) = ids_of(&instance);
                found.push(PortInfo {
                    device,
                    vid,
                    pid,
                    serial_number,
                });
            }
            SetupDiDestroyDeviceInfoList(set);
        }
        found
    }
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use std::io::{Read, Write};
    use std::os::fd::FromRawFd;
    use std::time::{Duration, Instant};

    use super::SerialPort;
    use crate::hongtai::{self, HongtaiDisplay, Kind, Timing};
    use crate::link::Link;

    /// A pseudo-terminal: the panel's end (a file) and the port's name.
    fn pty() -> (std::fs::File, String) {
        // SAFETY: openpty fills two descriptors; the name is read from the one it gives
        unsafe {
            let (mut panel, mut port) = (0, 0);
            let mut name = [0 as libc::c_char; 128];
            let done = libc::openpty(
                &mut panel,
                &mut port,
                name.as_mut_ptr(),
                std::ptr::null(),
                std::ptr::null(),
            );
            assert_eq!(done, 0);
            let name = std::ffi::CStr::from_ptr(name.as_ptr())
                .to_string_lossy()
                .into_owned();
            libc::close(port); // (the port is opened again by its name, as a panel's is)
            (std::fs::File::from_raw_fd(panel), name)
        }
    }

    #[test]
    fn every_byte_goes_through_as_it_is() {
        let (mut panel, name) = pty();
        let mut port = SerialPort::open(&name, hongtai::BAUD, false).expect("the pty opens");
        let all: Vec<u8> = (0..=255).collect(); // (0x11 and 0x13, XON and XOFF, among them)
        port.write(&all).unwrap();
        let mut got = vec![0u8; all.len()];
        panel.read_exact(&mut got).unwrap();
        assert_eq!(got, all);
        panel.write_all(&all).unwrap();
        let mut back = Vec::new();
        while back.len() < all.len() {
            let chunk = port.read(all.len() - back.len()).unwrap();
            assert!(!chunk.is_empty(), "the bytes came");
            back.extend(chunk);
        }
        assert_eq!(back, all);
    }

    #[test]
    fn a_read_waits_a_second_at_most() {
        let (_panel, name) = pty();
        let mut port = SerialPort::open(&name, hongtai::BAUD, false).unwrap();
        let started = Instant::now();
        assert!(port.read(10).unwrap().is_empty());
        let waited = started.elapsed();
        assert!(waited >= Duration::from_millis(900) && waited < Duration::from_secs(3));
    }

    #[test]
    fn the_majesty_is_opened_and_shown_over_the_port() {
        // (the panel's end answers 06 and 15, then takes what comes until 21)
        let (mut panel, name) = pty();
        let answer = {
            let data = br#"{"status": 200, "model": "MAJESTY", "width": 462, "height": 1920}"#;
            let mut head = vec![0x55, 0xAA];
            head.extend_from_slice(&((data.len() + 7) as u16).to_le_bytes());
            head.push(0x06);
            head.extend_from_slice(data);
            let sum = head.iter().map(|&b| u32::from(b)).sum::<u32>() as u16;
            head.extend_from_slice(&sum.to_le_bytes());
            head
        };
        let close = hongtai::frame(hongtai::CMD_CLOSE, &[]).unwrap();
        // (the port open before the panel's end is read: with no end of it open, Linux answers a
        // read of the other end with EIO)
        let port = SerialPort::open(&name, hongtai::BAUD, false).unwrap();
        let reader = std::thread::spawn(move || {
            let mut seen = Vec::new();
            let mut buf = [0u8; 4096];
            let (mut answered, mut set_up) = (false, false);
            while !seen.ends_with(&close) {
                let n = panel.read(&mut buf).unwrap();
                seen.extend_from_slice(&buf[..n]);
                if !answered && seen.ends_with(&hongtai::frame(hongtai::CMD_INFO, &[]).unwrap()) {
                    // (in two parts, a while apart: a read waits for what it asks)
                    panel.write_all(&answer[..10]).unwrap();
                    std::thread::sleep(Duration::from_millis(150));
                    panel.write_all(&answer[10..]).unwrap();
                    answered = true;
                }
                if !set_up && seen.ends_with(&hongtai::majesty_setup()) {
                    panel
                        .write_all(&[0x55, 0xAA, 0x07, 0x00, 0x15, 0x1B, 0x01])
                        .unwrap();
                    set_up = true;
                }
            }
            seen
        });
        let timing = Timing {
            settle: Duration::from_millis(20),
            ..Timing::PANEL
        };
        let (mut display, identity) =
            HongtaiDisplay::open(port, Kind::Majesty, Some((462, 1920)), &name, timing).unwrap();
        assert_eq!(identity.native, (462, 1920));
        assert_eq!(identity.set_up, Some(0x15));
        let frame = lp_pixels::Image::new(lp_pixels::Mode::RGB, 1920, 462, &[200, 40, 40]);
        display.set_brightness(50).unwrap();
        // (a JPEG wants libjpeg, loaded where it runs: LP_LIBJPEG, as the comparisons have it;
        // where there is none the pictures are left to tests/test_rust_devices.py)
        let jpegs = lp_decode::save_jpeg(&frame, 90).is_ok();
        if jpegs {
            display.show(&frame).unwrap();
        }
        display.close();
        let seen = reader.join().unwrap();
        assert!(seen.starts_with(&hongtai::RESET));
        let picture_at = seen
            .windows(hongtai::END.len())
            .rposition(|w| w == hongtai::END)
            .unwrap();
        assert!(!jpegs || picture_at > 1000, "a picture went out");
        assert!(seen.ends_with(&hongtai::frame(hongtai::CMD_CLOSE, &[]).unwrap()));
    }
}

#[cfg(test)]
mod everywhere {
    use super::{ports, SerialPort};

    #[test]
    fn the_ports_are_listed_and_one_that_is_not_there_is_refused() {
        let _ = ports(); // (whatever the machine has: listed without a fault)
        let missing = if cfg!(windows) {
            "COM250"
        } else {
            "/dev/ttyLP-none"
        };
        let refused = SerialPort::open(missing, 2_000_000, false).err();
        let message = refused.expect("no such port").0;
        assert!(
            message.starts_with(&format!("cannot open {missing}: ")),
            "{message}"
        );
    }
}
